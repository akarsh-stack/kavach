"""The world. Holds all hidden state and resolves what an intervention achieves.

**Nothing under `agent/` may import this module.** That rule is enforced by
`tests/test_observability_boundary.py`, which walks the AST of every file under
`agent/` and fails the build on any import from `sim`. It is the load-bearing
claim of the whole project: the agent is solving the real inference problem
rather than reading an answer key, and we would rather prove that mechanically
than assert it in a README.

The split is physical, not conventional:

  * `FailureEvent` contains only fields a real merchant receives on a failed
    payment webhook. It is safe to hand to the agent verbatim.
  * `Truth` contains the answer -- the real recovery class, the real recovery
    probability -- and lives in a private dict on `World`, keyed by payment id.
    There is no reference to it from any object the agent can reach.

Because of that split there is no way for the agent to accidentally read hidden
state through an innocuous-looking attribute, which is the usual way simulated
benchmarks leak.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from core.actions import Action, Channel
from sim.customers import Customer, Population
from sim.issuers import IssuerHealth
from sim.taxonomy import BY_REASON, ErrorClass, Method, RecoveryClass, Source


# Reach x salience, relative to WhatsApp. Assumed. WhatsApp dominates in India
# for transactional messaging; email is near-dead for payment recovery.
CHANNEL_EFFICACY: dict[Channel, float] = {
    Channel.WHATSAPP: 1.00,
    Channel.SMS: 0.74,
    Channel.EMAIL: 0.43,
}


@dataclass(frozen=True)
class FailureEvent:
    """A failed payment, exactly as the merchant sees it. Safe for the agent.

    Every field here maps to something present on a real Razorpay payment
    object or failed-payment webhook. If a field could not be justified that
    way, it does not belong in this class -- it belongs in `Truth`.
    """

    payment_id: str
    customer_id: str
    amount_paise: int
    method: Method
    issuer: str
    psp: str | None
    reason: str
    description: str
    source: Source
    error_class: ErrorClass
    failed_at: datetime
    is_subscription: bool
    attempt_no: int

    @property
    def amount_rupees(self) -> float:
        return self.amount_paise / 100.0


@dataclass
class Truth:
    """The answer key. Never leaves sim/.

    `true_class` differs from `BY_REASON[reason].recovery_class` only for the
    ambiguous reasons -- chiefly `payment_failed`, which Razorpay documents as
    carrying no specific gateway error code. For those the world samples a real
    underlying cause that the reason string cannot reveal, so the agent has to
    infer it from method, issuer health, amount and customer history. That is
    the single hardest slice of the batch and eval/report.py scores it apart
    from the rest.
    """

    payment_id: str
    true_class: RecoveryClass
    base_prob: float
    is_ambiguous: bool
    first_failed_at: datetime
    prior_attempts: int = 0
    resolved: bool = False


@dataclass
class Outcome:
    payment_id: str
    action: Action
    executed_at: datetime
    succeeded: bool
    amount_recovered_paise: int
    new_reason: str | None
    """If a retry/switch failed, the reason string the gateway returned this
    time. Feeds the agent's next decision, exactly as in production."""
    customer_churned: bool
    p_success: float
    """The probability the world actually rolled against. Recorded for
    diagnostics and never shown to the agent -- eval/report.py uses it to
    separate 'made a bad decision' from 'made a good decision and got unlucky',
    which is a distinction a single-run recovery number completely hides."""
    note: str = ""


# How well each action serves each true underlying cause.
#
# The 0.06 in SWITCH_RAIL/RETRY is the most consequential number in the file:
# it is the cost of re-presenting a dead instrument, and it is what the
# fixed-retry baseline burns its budget on. A card that has expired does not
# un-expire because you asked three times.
MATCH: dict[RecoveryClass, dict[Action, float]] = {
    RecoveryClass.RETRY_SAME: {
        Action.RETRY: 1.00,
        Action.SWITCH_RAIL: 0.58,
        Action.NUDGE: 0.40,
    },
    RecoveryClass.RETRY_LATER_FUNDS: {
        Action.RETRY: 1.00,
        Action.SWITCH_RAIL: 0.50,
        Action.NUDGE: 0.44,
    },
    RecoveryClass.SWITCH_RAIL: {
        Action.RETRY: 0.06,
        Action.SWITCH_RAIL: 1.00,
        Action.NUDGE: 0.54,
    },
    RecoveryClass.NUDGE_CUSTOMER: {
        Action.RETRY: 0.22,
        Action.SWITCH_RAIL: 0.47,
        Action.NUDGE: 1.00,
    },
    # Nothing recovers these. Retrying a risk decline is worse than useless.
    RecoveryClass.HARD_STOP: {Action.RETRY: 0.0, Action.SWITCH_RAIL: 0.0, Action.NUDGE: 0.0},
    RecoveryClass.MERCHANT_FIX: {Action.RETRY: 0.0, Action.SWITCH_RAIL: 0.0, Action.NUDGE: 0.0},
}

QUIET_START, QUIET_END = 21, 9
"""No-contact window, local time. Messaging outside it is both less effective
and, under TRAI's commercial-communication rules, not something a payments
merchant should be doing casually. agent/policy.py forbids it outright; the
world independently penalises it so that a policy which ignores the rule is
punished on the numbers as well as flagged."""


def in_quiet_hours(t: datetime) -> bool:
    return t.hour >= QUIET_START or t.hour < QUIET_END


class World:
    """Resolves interventions against hidden state, using common random numbers.

    ## Why the randomness is keyed rather than streamed

    The naive implementation draws from one RNG stream. That silently breaks the
    comparison: policies take different actions, so they consume draws at
    different rates, and by the tenth payment two policies are being scored
    against completely different luck. A 5% "lift" measured that way might be
    nothing but variance.

    So every random draw is instead keyed by *what it is a draw about* --
    `(payment_id, purpose, attempt_no)` -- and derived by hashing that key with
    the run seed. The draw governing "does attempt 2 on pay_00042 succeed" is
    the same number under every policy. A policy that beats another does so
    because it made better decisions, not because it got a friendlier dice roll.

    This is the standard common-random-numbers variance reduction, and it is
    what lets us report a lift from a single run per policy rather than needing
    hundreds of replications to average the noise out.
    """

    def __init__(
        self,
        seed: int,
        population: Population,
        health: IssuerHealth,
        prob_scale: float = 1.0,
    ) -> None:
        self.prob_scale = prob_scale
        """Multiplier on every assumed recovery probability.

        The single knob evaluation/sensitivity.py turns to answer the question
        that decides whether any of this is trustworthy: if our Tier 3 guesses
        are globally too generous or too stingy, does the *ranking* of policies
        change? It should not -- a scale factor lifts every policy at once --
        and demonstrating that is what lets us quote a lift rather than a
        rupee figure we cannot defend.
        """
        self._seed = seed
        self._key = seed.to_bytes(8, "big", signed=False) if seed >= 0 else b"neg"
        self.pop = population
        self.health = health
        self._truth: dict[str, Truth] = {}
        self._events: dict[str, FailureEvent] = {}

    def _u01(self, *parts: object) -> float:
        """A stable uniform [0,1) draw keyed by `parts`.

        blake2b rather than the builtin `hash()`, which is salted per process
        and would make runs irreproducible across machines -- the exact failure
        a reviewer would hit trying to replicate our numbers.
        """
        raw = "|".join(str(p) for p in parts).encode()
        digest = hashlib.blake2b(raw, digest_size=8, key=self._key).digest()
        return int.from_bytes(digest, "big") / 2**64

    # -- wiring, called by sim/generate.py only --------------------------

    def register(self, event: FailureEvent, truth: Truth) -> None:
        self._events[event.payment_id] = event
        self._truth[event.payment_id] = truth

    def event(self, payment_id: str) -> FailureEvent:
        return self._events[payment_id]

    def all_events(self) -> list[FailureEvent]:
        return list(self._events.values())

    # -- execution -------------------------------------------------------

    def execute(
        self,
        payment_id: str,
        action: Action,
        at: datetime,
        channel: Channel | None = None,
    ) -> Outcome:
        truth = self._truth[payment_id]
        event = self._events[payment_id]
        cust = self.pop.get(event.customer_id)

        if truth.resolved:
            return Outcome(
                payment_id, action, at, False, 0, None, cust.churned, 0.0,
                note="already resolved",
            )

        # ESCALATE and STOP recover nothing directly. Their value is in what
        # they avoid, which the cost model in economics/costs.py accounts for.
        # Scoring them as zero here is correct, not a modelling gap: a stop is
        # worth exactly the money it stops us wasting.
        if action in (Action.ESCALATE, Action.STOP):
            truth.resolved = action == Action.STOP
            return Outcome(
                payment_id, action, at, False, 0, None, cust.churned, 0.0,
                note=f"{action.value}: no direct recovery by construction",
            )

        churned = False
        if action == Action.NUDGE:
            hazard = cust.register_contact()
            if hazard > 0 and self._u01(cust.id, "churn", cust.contacts_made) < hazard:
                cust.churned = True
                churned = True

        p = self._p_success(truth, event, cust, action, at, channel)
        hit = self._u01(payment_id, "success", truth.prior_attempts) < p

        truth.prior_attempts += 1

        if hit:
            truth.resolved = True
            return Outcome(
                payment_id, action, at, True, event.amount_paise, None,
                churned, p, note="recovered",
            )

        return Outcome(
            payment_id, action, at, False, 0,
            self._failure_reason(truth, event, at), churned, p,
            note="attempt failed",
        )

    # -- probability model ------------------------------------------------

    def _p_success(
        self,
        truth: Truth,
        event: FailureEvent,
        cust: Customer,
        action: Action,
        at: datetime,
        channel: Channel | None,
    ) -> float:
        if cust.churned:
            return 0.0

        base = MATCH[truth.true_class].get(action, 0.0)
        if base <= 0.0:
            return 0.0

        p = truth.base_prob * self.prob_scale * base

        # Repeated attempts against an unchanged underlying condition get
        # progressively less likely. Without this, a policy could brute-force
        # its way to a good number by simply retrying more, which would make
        # the whole comparison meaningless.
        p *= 0.82 ** truth.prior_attempts

        p *= self._intent_factor(truth, event, cust, action, at)

        # A silent re-presentment cannot recover a customer who walked away
        # from a checkout. There is no stored credential to charge and nobody
        # at the 3DS screen to approve it -- the retry simply fails at the same
        # step. A standing mandate is the exception: that is precisely what a
        # subscription authorises, so the merchant CAN re-present it.
        #
        # Without this, the model concluded that silently retrying an
        # `authentication_failed` was competitive with messaging the customer,
        # which inverts the entire reason nudges exist.
        if (
            truth.true_class == RecoveryClass.NUDGE_CUSTOMER
            and action == Action.RETRY
            and not event.is_subscription
        ):
            p *= 0.18

        entity = event.psp if (event.method == Method.UPI and event.psp) else event.issuer

        if action == Action.RETRY:
            # Retrying into a live outage mostly fails. This is the timing
            # lever: wait for the window to clear and the same action works.
            p *= 1.0 - self.health.failure_boost_at(entity, event.method, at)
            if truth.true_class == RecoveryClass.RETRY_LATER_FUNDS:
                p *= cust.liquidity_at(at)

        elif action == Action.SWITCH_RAIL:
            # Needs the customer to actually have, and use, something else.
            p *= 0.88 if cust.has_alt_method else 0.12
            p *= 0.45 + 0.55 * cust.responsiveness
            if truth.true_class == RecoveryClass.RETRY_LATER_FUNDS:
                # A different rail does not conjure money into the account,
                # though a credit line sometimes helps where a debit failed.
                p *= 0.35 + 0.65 * cust.liquidity_at(at)

        elif action == Action.NUDGE:
            p *= cust.nudge_success_odds()
            p *= CHANNEL_EFFICACY[channel or Channel.SMS]
            if in_quiet_hours(at):
                p *= 0.25
            # A nudge only helps if the underlying blocker has cleared by the
            # time they act on it.
            if truth.true_class == RecoveryClass.RETRY_SAME:
                p *= 1.0 - 0.6 * self.health.failure_boost_at(entity, event.method, at)
            elif truth.true_class == RecoveryClass.RETRY_LATER_FUNDS:
                p *= 0.30 + 0.70 * cust.liquidity_at(at)

        return max(0.0, min(0.97, p))

    def _intent_factor(
        self,
        truth: Truth,
        event: FailureEvent,
        cust: Customer,
        action: Action,
        at: datetime,
    ) -> float:
        """How much the customer's remaining interest gates this action.

        A silent retry against a standing mandate barely needs the customer to
        still care -- they authorised it in advance, and that authorisation is
        what a subscription *is*. Every other action requires them to show up
        and do something, so decayed intent bites in full.
        """
        intent = cust.intent_at(truth.first_failed_at, at)
        if action == Action.RETRY and event.is_subscription:
            return 0.85 + 0.15 * intent
        return intent

    def _failure_reason(self, truth: Truth, event: FailureEvent, at: datetime) -> str:
        """What the gateway returns when a recovery attempt fails.

        Usually the same reason as before -- the underlying condition has not
        changed. Occasionally a different one, because real gateways are
        inconsistent about which of several true statements they report. This
        is a small thing that makes the agent's second decision genuinely
        harder than its first, in the way production is harder than a demo.
        """
        if self._u01(event.payment_id, "reason_same", truth.prior_attempts) < 0.78:
            return event.reason
        siblings = sorted(
            r
            for r, e in BY_REASON.items()
            if e.recovery_class == truth.true_class and event.method in e.methods
        )
        if not siblings:
            return event.reason
        pick = self._u01(event.payment_id, "reason_pick", truth.prior_attempts)
        return siblings[int(pick * len(siblings)) % len(siblings)]

    # -- reporting; eval/ only, never the agent ---------------------------

    def truth_of(self, payment_id: str) -> Truth:
        return self._truth[payment_id]

    def recoverable_value_paise(self) -> int:
        """Total value attached to failures that *any* policy could have won.

        The denominator for recovery rate. Excludes HARD_STOP and MERCHANT_FIX,
        because counting money that was never winnable would flatter every
        policy equally and make the headline number meaningless.
        """
        return sum(
            self._events[pid].amount_paise
            for pid, t in self._truth.items()
            if t.true_class not in (RecoveryClass.HARD_STOP, RecoveryClass.MERCHANT_FIX)
        )

    def total_value_paise(self) -> int:
        return sum(e.amount_paise for e in self._events.values())

    def reset(self) -> None:
        """Rewind so the next policy faces an identical world.

        Every policy in eval/harness.py must meet the same batch in the same
        state, or the comparison is worthless. Resolution flags, attempt counts
        and all customer mutation are undone here.

        Note there is no RNG state to rewind: draws are keyed by
        `(payment_id, purpose, attempt_no)` rather than streamed, so they are
        already identical across policies. See the class docstring.
        """
        for t in self._truth.values():
            t.resolved = False
            t.prior_attempts = 0
        for c in self.pop.customers.values():
            c.contacts_made = 0
            c.annoyance = 0.0
            c.churned = False
