"""Diagnosis and planning in one structured call.

## Why one call and not two

The obvious design splits this: a diagnosis call that names the root cause, then
a planning call that picks an action. We merged them because the split doubles
cost and latency for no measured gain -- the plan is almost entirely determined
by the diagnosis plus the observation, and the model already has both in
context. The merged call still *reports* its diagnosis as a separate field, so
`evaluation/report.py` can score classification accuracy independently of
whether the resulting action was any good. That distinction matters: an agent
that diagnoses correctly and then plans badly needs a different fix from one
that misreads the failure entirely.

## What the model is actually being asked to do

Not "recover this payment". The three things a lookup table cannot do:

1. **Classify a reason the rulebook does not cover.** Roughly one failure in
   forty carries a reason string our rulebook has no entry for. The model gets
   Razorpay's description text and has to generalise.

2. **Infer a cause that the reason string cannot express.** `payment_failed` is
   documented by Razorpay as carrying no specific gateway error code. The
   underlying cause is real but has to come from context: which rail, whether
   other payments at the same bank are failing right now, how this customer has
   behaved before.

3. **Choose *when*.** Nothing in Razorpay's documentation says whether to retry
   in ten minutes or on the 2nd of next month, and that choice is where most of
   the recoverable money actually sits.

The prompt is written to push on those three and to stay quiet about the rest.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent.knowledge import RULEBOOK
from agent.observe import Observation

RecoveryClassName = Literal[
    "retry_same",
    "retry_later_funds",
    "switch_rail",
    "nudge_customer",
    "hard_stop",
    "merchant_fix",
]

ActionName = Literal["retry", "switch_rail", "nudge", "escalate", "stop"]
ChannelName = Literal["whatsapp", "sms", "email"]


class Decision(BaseModel):
    """The structured plan for one failed payment."""

    recovery_class: RecoveryClassName = Field(
        description="Root cause category of this failure."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="How confident you are in the classification. Be honest: "
        "low confidence on an ambiguous reason is correct, not a weakness.",
    )
    action: ActionName = Field(description="What to do about it.")
    delay_hours: float = Field(
        ge=0.0,
        le=720.0,
        description="Hours from now to execute. 0 means immediately.",
    )
    channel: ChannelName | None = Field(
        default=None,
        description="Messaging channel. Required for nudge and switch_rail, null otherwise.",
    )
    rationale: str = Field(
        max_length=400,
        description="One or two sentences. State the reasoning, not a restatement of the input.",
    )


def _rulebook_block() -> str:
    """Compact rendering of the public rulebook for the system prompt.

    Sorted so the string is byte-identical on every call. A dict-order change
    here would silently destroy the prompt cache across the whole batch.
    """
    lines = []
    for reason in sorted(RULEBOOK):
        entry = RULEBOOK[reason]
        lines.append(f"  {reason} -> {entry.recovery_class}")
    return "\n".join(lines)


SYSTEM_PROMPT = f"""\
You triage failed payments for an Indian merchant on Razorpay and decide how to \
recover them. You are one step in an automated pipeline: your output is executed.

# Action space

- retry        re-present the same instrument. Silent, cheap, no customer burden.
- switch_rail  ask the customer to pay by another method. Needs them to act.
- nudge        message the customer with a fresh payment link. Costs money and goodwill.
- escalate     hand to a human (risk review, engineering, or collections).
- stop         deliberately give up.

`stop` is a real answer and often the best one. A dead instrument does not \
revive because you asked three times, and every wasted attempt is money spent \
against a payment that was never going to complete.

# Root cause classes

- retry_same         transient fault at bank, gateway or PSP. Same instrument will work once it clears.
- retry_later_funds  no money or a rolling limit hit. Needs calendar time, not seconds.
- switch_rail        the instrument itself is dead. Retrying it is pure waste.
- nudge_customer     the customer dropped out mid-flow. Intent existed; they need a prompt.
- hard_stop          risk, fraud or compliance block. Never retry.
- merchant_fix       our own integration is broken. No customer action can fix it.

# What Razorpay's documentation says

Below is the mapping our rulebook covers. It is Razorpay's published guidance, \
not ground truth, and it is incomplete.

{_rulebook_block()}

If a reason is NOT in that list, you have to work it out from the description \
text. Say so in your rationale and set confidence accordingly.

If the reason is `payment_failed`, Razorpay documents it as carrying no specific \
error code from the gateway. The string tells you nothing. Infer the cause from \
context: the rail, whether other payments at this bank or PSP are failing right \
now, the amount, and this customer's history.

# Timing is the decision most people get wrong

- Transient outages clear in roughly half an hour, sometimes several hours. \
Retrying into a live outage mostly fails and burns an attempt. Waiting a little \
converts the same action from a loss into a win.
- Salaries in India land on the 1st, and again around the 7th for many public \
sector employees. An `insufficient_funds` failure on the 28th has poor odds; the \
same customer on the 2nd is a different proposition.
- But waiting costs you. Purchase intent decays -- fast for impulse retail, far \
more slowly for a subscription renewal where the customer already decided and a \
mandate is in place. Weigh the liquidity you gain against the intent you lose.
- Several failures at the same bank or PSP in a short window means an outage, \
even when no downtime has been reported. Roughly a third of real outages are \
never reported at all, and reports lag the onset. Trust the pattern in your own \
failure stream over the absence of a downtime flag.

# Economics

Recovering the money is not the goal; recovering it profitably is. A retry costs \
about Rs 0.50. A WhatsApp message costs about Rs 0.85, an SMS Rs 0.20. A human \
escalation costs about Rs 45, so reserve it for large amounts, risk blocks and \
our own bugs. Razorpay's fee takes about 2% of whatever you recover. Do not spend \
Rs 60 chasing a Rs 99 renewal.

WhatsApp works far better than SMS in India, and email barely works at all for \
payment recovery. Use the cheaper channel when the amount is small.

# Hard limits

These are enforced downstream and you cannot override them. Proposing a blocked \
action wastes a decision and is recorded against you.

- Never retry, switch or nudge a risk, fraud or compliance decline. Escalate.
- Never retry our own integration bugs. Escalate to engineering.
- At most 3 recovery attempts per payment.
- At most 2 messages per payment, and 4 per customer per week across all payments.
- No customer messaging between 21:00 and 09:00. Silent retries are fine overnight.

# Output

Return the structured object. Keep the rationale to one or two sentences, and \
make it the actual reasoning -- not a restatement of the inputs.\
"""


def build_user_message(obs: Observation) -> str:
    """Per-payment context. Everything volatile lives here, after the cache
    breakpoint, so the system prefix stays byte-stable across the batch."""
    lines = [
        "# Failed payment",
        f"reason:        {obs.reason}",
        f"description:   {obs.description}",
        f"source:        {obs.source}",
        f"error class:   {obs.error_class}",
        f"method:        {obs.method}",
        f"issuer:        {obs.issuer}",
    ]
    if obs.psp:
        lines.append(f"UPI app:       {obs.psp}")
    lines += [
        f"amount:        Rs {obs.amount_rupees:,.2f}",
        f"subscription:  {'yes (standing mandate in place)' if obs.is_subscription else 'no (one-off)'}",
        f"failed at:     {obs.failed_at:%a %d %b %Y, %H:%M} IST",
        f"now:           {obs.now:%a %d %b %Y, %H:%M} IST  ({obs.hours_since_failure:.1f}h later)",
        "",
        "# This customer, per our records",
        f"prior successful payments: {obs.customer_prior_payments}",
        f"prior failures:            {obs.customer_prior_failures}",
        f"lifetime value:            Rs {obs.customer_lifetime_paise / 100:,.0f}",
        f"first seen:                {obs.customer_first_seen:%b %Y}",
        f"messages sent this week:   {obs.customer_contacts_this_week}",
        "",
        "# Recovery so far on this payment",
        f"attempts made:  {obs.attempts_made}",
        f"messages sent:  {obs.contacts_made}",
        "",
        "# Signals",
        f"Razorpay downtime reported for {obs.issuer}: "
        f"{'YES' if obs.issuer_downtime_reported else 'no'}",
    ]
    if obs.psp:
        lines.append(
            f"Razorpay downtime reported for {obs.psp}: "
            f"{'YES' if obs.psp_downtime_reported else 'no'}"
        )
    lines += [
        f"other failures at {obs.entity} in the last hour: {obs.recent_failures_same_entity}",
        f"other failures with reason '{obs.reason}' in the last hour: "
        f"{obs.recent_failures_same_reason}",
        "",
        "Decide.",
    ]
    return "\n".join(lines)


NAIVE_SYSTEM_PROMPT = """\
You are a payment recovery assistant for an Indian merchant on Razorpay. A \
payment has failed. Your job is to recover the revenue.

Decide what to do: retry, switch_rail, nudge, escalate, or stop. Pick a delay in \
hours and a channel if you are messaging the customer. Also state which root \
cause class it is: retry_same, retry_later_funds, switch_rail, nudge_customer, \
hard_stop, or merchant_fix.

Recover as much of the money as you can.\
"""
"""The naive baseline's prompt.

This is the honest version of what most people build: an LLM, the error string,
and an instruction to go get the money. No rulebook, no economics, no limits, no
sense that stopping might be correct.

It is not a strawman -- it is a competent statement of the obvious approach, and
we expect it to look *good* on gross recovery. The interesting result is what it
does to net recovery, to the contact caps, and to the payments it should never
have touched. See evaluation/baselines.py.
"""
