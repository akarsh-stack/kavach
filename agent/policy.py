"""Guardrails. The layer that is allowed to overrule the model.

## Why this is not a prompt instruction

The obvious way to build this is to tell the model "never retry a risk-declined
payment" and trust it. We do tell it that. We also assume it will sometimes do
it anyway, because models under pressure to find recoverable revenue will find
reasons, and because a system whose only safety mechanism is a paragraph of
English has no safety mechanism at all.

So every proposal the model makes passes through `review()` before anything
touches a payment. The rules here are deterministic, ordered, and cannot be
argued with. A model that proposes re-presenting a `payment_risk_check_failed`
gets a veto and an audit line, not a debate.

That distinction is the difference between a demo and something a payments
company could plausibly run: **the model proposes, the policy disposes.**

## What each rule protects

Rules fire in priority order, first match wins, and every outcome -- allow,
veto, defer, substitute -- is recorded with the rule that produced it. The audit
trail therefore explains not just what was done but what was *prevented*, which
is the half most recovery systems cannot show.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum

from agent.knowledge import NEVER_RETRY, documented_class
from agent.observe import Observation
from core.actions import ACTIVE_ACTIONS, Action, Channel
from economics.costs import DEFAULT, CostModel


class Verdict(str, Enum):
    ALLOW = "allow"
    VETO = "veto"
    """Blocked outright. The action is replaced by a safe alternative."""
    DEFER = "defer"
    """Permitted, but not now. Rescheduled to a compliant time."""
    SUBSTITUTE = "substitute"
    """Permitted in spirit, but a different action is correct."""


@dataclass(frozen=True)
class Proposal:
    """What the decision layer wants to do."""

    payment_id: str
    action: Action
    at: datetime
    channel: Channel | None = None
    rationale: str = ""
    confidence: float = 0.5
    diagnosed_class: str = ""
    """Root cause the decision layer settled on. Carried through untouched so
    the report can score diagnosis separately from plan quality."""


@dataclass(frozen=True)
class Ruling:
    """What will actually happen, and why."""

    verdict: Verdict
    action: Action
    at: datetime
    channel: Channel | None
    rule: str
    explanation: str
    proposed_action: Action
    """Kept even when unchanged, so the audit trail can show the model's
    intent alongside the outcome."""

    # Passed through from the proposal untouched. The policy layer rules on
    # actions, not diagnoses -- but the audit trail needs both to explain why a
    # decision was made and why it was or was not allowed to stand.
    diagnosed_class: str = ""
    confidence: float = 0.0
    rationale: str = ""

    @property
    def blocked(self) -> bool:
        return self.verdict in (Verdict.VETO, Verdict.SUBSTITUTE)


@dataclass(frozen=True)
class Limits:
    """The bounds. Every one of these is a deliberate business decision.

    Defaults are set to what a cautious payments team would actually run, not
    to what maximises our benchmark score. Where the two diverge, we take the
    cautious number and accept the lower result -- and `evaluation/report.py`
    reports what the cap cost us, so the trade-off is visible rather than
    hidden.
    """

    max_attempts_per_payment: int = 3
    """Card network rules penalise merchants with excessive retry ratios, and
    the marginal attempt is worth very little by the fourth try."""

    max_contacts_per_payment: int = 2
    max_contacts_per_customer_per_week: int = 4
    """Protects the customer relationship across the whole batch, not just
    within one payment. A customer with three failed payments must not receive
    six messages."""

    quiet_start_hour: int = 21
    quiet_end_hour: int = 9
    """No outbound messaging overnight. Both a courtesy and, under India's
    commercial-communication rules, the safe default for a payments merchant."""

    max_spend_ratio: float = 0.15
    """Never spend more than this share of a payment's value chasing it.
    Stops the agent spending Rs 60 of messages recovering a Rs 99 subscription."""

    escalate_above_paise: int = 2_000_000
    """Rs 20,000. Above this, a human should look before we give up."""

    max_escalations: int = 40
    """Ops capacity for the batch. Escalation is a scarce resource, and a policy
    that escalates everything has not solved the problem, it has forwarded it."""


def _next_allowed_time(t: datetime, limits: Limits) -> datetime:
    """Push a timestamp forward to the next moment messaging is permitted."""
    if limits.quiet_end_hour <= t.hour < limits.quiet_start_hour:
        return t
    candidate = t.replace(hour=limits.quiet_end_hour, minute=0, second=0, microsecond=0)
    if candidate <= t:
        candidate += timedelta(days=1)
    return candidate


def in_quiet_hours(t: datetime, limits: Limits) -> bool:
    return not (limits.quiet_end_hour <= t.hour < limits.quiet_start_hour)


class PolicyEngine:
    def __init__(
        self,
        limits: Limits | None = None,
        costs: CostModel | None = None,
        batch_budget_paise: int | None = None,
    ) -> None:
        self.limits = limits or Limits()
        self.costs = costs or DEFAULT
        self.batch_budget_paise = batch_budget_paise
        self.spent_paise = 0
        self.escalations_used = 0
        self.veto_counts: dict[str, int] = {}
        self.reported_bugs: set[str] = set()
        """Integration faults already raised with engineering this batch.
        A single bad deploy produces the same `invalid_order_id` on hundreds of
        payments; filing hundreds of tickets for it is not diligence, it is
        noise. The first one is escalated and the rest reference it."""

    # -- the gate ---------------------------------------------------------

    def review(self, obs: Observation, proposal: Proposal) -> Ruling:
        """Run every rule in priority order. First match wins."""
        for rule in (
            self._r1_risk_block,
            self._r2_merchant_bug,
            self._r3_attempt_cap,
            self._r4_contact_caps,
            self._r5_budget,
            self._r6_uneconomic,
            self._r7_escalation_capacity,
            self._r8_quiet_hours,
        ):
            ruling = rule(obs, proposal)
            if ruling is not None:
                if ruling.blocked:
                    self.veto_counts[ruling.rule] = self.veto_counts.get(ruling.rule, 0) + 1
                return ruling

        return Ruling(
            verdict=Verdict.ALLOW,
            action=proposal.action,
            at=proposal.at,
            channel=proposal.channel,
            rule="",
            explanation="within all limits",
            proposed_action=proposal.action,
            diagnosed_class=proposal.diagnosed_class,
            confidence=proposal.confidence,
            rationale=proposal.rationale,
        )

    # -- rules, highest priority first -------------------------------------

    def _r1_risk_block(self, obs: Observation, p: Proposal) -> Ruling | None:
        """Never re-present a payment the risk or compliance layer declined.

        The single most important rule here. Razorpay's own docs suggest the
        *customer* try another method after a risk decline -- and for a human
        standing at a checkout that is fine. It is a different act entirely for
        an automated system to re-present a declined transaction, and doing it
        at volume is what gets a merchant's MID reviewed.

        We therefore override the documented next step deliberately, and
        docs/CALIBRATION.md says so rather than burying it.
        """
        if p.action not in ACTIVE_ACTIONS:
            return None
        if documented_class(obs.reason) != "hard_stop":
            return None
        return Ruling(
            verdict=Verdict.VETO,
            action=Action.ESCALATE,
            at=p.at,
            channel=None,
            rule="R1_RISK_BLOCK",
            explanation=(
                f"'{obs.reason}' is a risk or compliance decline. Re-presenting it "
                f"is prohibited regardless of expected value; routed to risk review."
            ),
            proposed_action=p.action,
            diagnosed_class=p.diagnosed_class,
            confidence=p.confidence,
            rationale=p.rationale,
        )

    def _r2_merchant_bug(self, obs: Observation, p: Proposal) -> Ruling | None:
        """Our own integration is broken. Retrying cannot fix our bug.

        A recovery agent that cheerfully retries `invalid_order_id` three times
        is burning money on a defect it should be reporting. Escalating to
        engineering is both cheaper and the only thing that actually helps.
        """
        if p.action not in ACTIVE_ACTIONS:
            return None
        if documented_class(obs.reason) != "merchant_fix":
            return None
        if obs.reason in self.reported_bugs:
            return Ruling(
                verdict=Verdict.VETO,
                action=Action.STOP,
                at=p.at,
                channel=None,
                rule="R2_BUG_ALREADY_REPORTED",
                explanation=(
                    f"'{obs.reason}' is already open with engineering this batch. "
                    f"Stopping rather than filing a duplicate."
                ),
                proposed_action=p.action,
                diagnosed_class=p.diagnosed_class,
                confidence=p.confidence,
                rationale=p.rationale,
            )
        return Ruling(
            verdict=Verdict.VETO,
            action=Action.ESCALATE,
            at=p.at,
            channel=None,
            rule="R2_MERCHANT_BUG",
            explanation=(
                f"'{obs.reason}' is an integration fault on our side. No customer-facing "
                f"action can resolve it; routed to engineering."
            ),
            proposed_action=p.action,
            diagnosed_class=p.diagnosed_class,
            confidence=p.confidence,
            rationale=p.rationale,
        )

    def _r3_attempt_cap(self, obs: Observation, p: Proposal) -> Ruling | None:
        if p.action not in ACTIVE_ACTIONS:
            return None
        if obs.attempts_made < self.limits.max_attempts_per_payment:
            return None
        return Ruling(
            verdict=Verdict.VETO,
            action=Action.STOP,
            at=p.at,
            channel=None,
            rule="R3_ATTEMPT_CAP",
            explanation=(
                f"{obs.attempts_made} attempts already made "
                f"(cap {self.limits.max_attempts_per_payment}); stopping."
            ),
            proposed_action=p.action,
            diagnosed_class=p.diagnosed_class,
            confidence=p.confidence,
            rationale=p.rationale,
        )

    def _r4_contact_caps(self, obs: Observation, p: Proposal) -> Ruling | None:
        """Protect the customer relationship, across the batch and not just
        within one payment."""
        if p.action not in (Action.NUDGE, Action.SWITCH_RAIL):
            return None

        if obs.contacts_made >= self.limits.max_contacts_per_payment:
            return Ruling(
                verdict=Verdict.VETO,
                action=Action.STOP,
                at=p.at,
                channel=None,
                rule="R4_CONTACT_CAP_PAYMENT",
                explanation=(
                    f"already contacted {obs.contacts_made} times about this payment "
                    f"(cap {self.limits.max_contacts_per_payment})."
                ),
                proposed_action=p.action,
            )

        if obs.customer_contacts_this_week >= self.limits.max_contacts_per_customer_per_week:
            return Ruling(
                verdict=Verdict.VETO,
                action=Action.STOP,
                at=p.at,
                channel=None,
                rule="R4_CONTACT_CAP_CUSTOMER",
                explanation=(
                    f"customer already contacted {obs.customer_contacts_this_week} times "
                    f"this week across all payments "
                    f"(cap {self.limits.max_contacts_per_customer_per_week}). "
                    f"The relationship is worth more than this payment."
                ),
                proposed_action=p.action,
            )
        return None

    def _r5_budget(self, obs: Observation, p: Proposal) -> Ruling | None:
        if self.batch_budget_paise is None:
            return None
        cost = self.costs.action_cost(p.action, p.channel)
        if self.spent_paise + cost <= self.batch_budget_paise:
            return None
        return Ruling(
            verdict=Verdict.VETO,
            action=Action.STOP,
            at=p.at,
            channel=None,
            rule="R5_BUDGET",
            explanation=(
                f"batch recovery budget exhausted "
                f"({self.spent_paise / 100:,.0f} of {self.batch_budget_paise / 100:,.0f} Rs spent)."
            ),
            proposed_action=p.action,
            diagnosed_class=p.diagnosed_class,
            confidence=p.confidence,
            rationale=p.rationale,
        )

    def _r6_uneconomic(self, obs: Observation, p: Proposal) -> Ruling | None:
        """Do not spend more chasing a payment than the payment is worth.

        Catches the failure mode where an agent spends Rs 60 of WhatsApp
        messages and analyst time recovering a Rs 99 subscription renewal.
        """
        if p.action not in ACTIVE_ACTIONS:
            return None
        cost = self.costs.action_cost(p.action, p.channel)
        ceiling = obs.amount_paise * self.limits.max_spend_ratio
        if cost <= ceiling:
            return None
        return Ruling(
            verdict=Verdict.VETO,
            action=Action.STOP,
            at=p.at,
            channel=None,
            rule="R6_UNECONOMIC",
            explanation=(
                f"action costs Rs {cost / 100:.2f} against a Rs {obs.amount_rupees:,.0f} "
                f"payment, above the {self.limits.max_spend_ratio:.0%} ceiling."
            ),
            proposed_action=p.action,
            diagnosed_class=p.diagnosed_class,
            confidence=p.confidence,
            rationale=p.rationale,
        )

    def _r7_escalation_capacity(self, obs: Observation, p: Proposal) -> Ruling | None:
        """Escalation is scarce. Spend it on the payments that justify a human."""
        if p.action != Action.ESCALATE:
            return None
        if self.escalations_used < self.limits.max_escalations:
            return None
        return Ruling(
            verdict=Verdict.VETO,
            action=Action.STOP,
            at=p.at,
            channel=None,
            rule="R7_ESCALATION_CAPACITY",
            explanation=(
                f"ops escalation capacity exhausted "
                f"({self.escalations_used}/{self.limits.max_escalations})."
            ),
            proposed_action=p.action,
            diagnosed_class=p.diagnosed_class,
            confidence=p.confidence,
            rationale=p.rationale,
        )

    def _r8_quiet_hours(self, obs: Observation, p: Proposal) -> Ruling | None:
        """Never message overnight. Reschedule rather than cancel.

        A defer rather than a veto: the contact is legitimate, the timing is
        not. Cancelling would throw away recoverable revenue for no reason.
        """
        if p.action not in (Action.NUDGE, Action.SWITCH_RAIL):
            return None
        if not in_quiet_hours(p.at, self.limits):
            return None
        moved = _next_allowed_time(p.at, self.limits)
        return Ruling(
            verdict=Verdict.DEFER,
            action=p.action,
            at=moved,
            channel=p.channel,
            rule="R8_QUIET_HOURS",
            explanation=(
                f"{p.at:%H:%M} falls in the quiet window "
                f"({self.limits.quiet_start_hour:02d}:00-{self.limits.quiet_end_hour:02d}:00); "
                f"deferred to {moved:%d %b %H:%M}."
            ),
            proposed_action=p.action,
            diagnosed_class=p.diagnosed_class,
            confidence=p.confidence,
            rationale=p.rationale,
        )

    # -- bookkeeping -------------------------------------------------------

    def commit(self, ruling: Ruling) -> None:
        """Record an executed action against the batch's shared budgets."""
        self.spent_paise += self.costs.action_cost(ruling.action, ruling.channel)
        if ruling.action == Action.ESCALATE:
            self.escalations_used += 1

    def report(self) -> dict[str, object]:
        return {
            "spent_rs": self.spent_paise / 100.0,
            "escalations_used": self.escalations_used,
            "vetoes_by_rule": dict(sorted(self.veto_counts.items())),
            "total_vetoes": sum(self.veto_counts.values()),
        }
