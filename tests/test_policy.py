"""The guardrails must hold even when the model is confidently wrong.

Each test here poses the question as an adversarial one: the model has proposed
something plausible-sounding and high-confidence, and we check the policy layer
blocks it anyway. A guardrail that only holds when the model is already behaving
is not a guardrail.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agent.observe import Observation
from agent.policy import Limits, PolicyEngine, Proposal, Verdict
from core.actions import Action, Channel

NOON = datetime(2026, 8, 12, 12, 0)


def obs(**over) -> Observation:
    base = dict(
        payment_id="pay_000001",
        customer_id="cust_00001",
        amount_paise=250_000,
        method="card",
        issuer="HDFC",
        psp=None,
        reason="insufficient_funds",
        description="The customer does not have sufficient funds.",
        source="issuer_bank",
        error_class="BAD_REQUEST_ERROR",
        failed_at=NOON,
        is_subscription=False,
        attempt_no=1,
        customer_prior_payments=4,
        customer_prior_failures=1,
        customer_contacts_this_week=0,
        customer_first_seen=datetime(2026, 1, 1),
        customer_lifetime_paise=1_000_000,
        issuer_downtime_reported=False,
        psp_downtime_reported=False,
        recent_failures_same_entity=0,
        recent_failures_same_reason=0,
        now=NOON,
        attempts_made=0,
        contacts_made=0,
        budget_remaining_paise=10_000_000,
    )
    base.update(over)
    return Observation(**base)


def prop(action: Action, at: datetime = NOON, channel: Channel | None = None) -> Proposal:
    return Proposal(
        payment_id="pay_000001",
        action=action,
        at=at,
        channel=channel,
        rationale="model is very confident about this",
        confidence=0.97,
    )


# -- the one that matters most ------------------------------------------------


@pytest.mark.parametrize("action", [Action.RETRY, Action.SWITCH_RAIL, Action.NUDGE])
def test_risk_decline_can_never_be_retried(action: Action) -> None:
    """No active recovery action survives a risk decline, whatever the model says.

    This is the demo moment and the compliance story in one test. The model is
    at 0.97 confidence and it does not matter.
    """
    engine = PolicyEngine()
    ruling = engine.review(obs(reason="payment_risk_check_failed"), prop(action))

    assert ruling.verdict is Verdict.VETO
    assert ruling.action is Action.ESCALATE
    assert ruling.rule == "R1_RISK_BLOCK"
    assert ruling.proposed_action is action


def test_merchant_bug_goes_to_engineering_not_the_customer() -> None:
    """Retrying our own integration bug burns money on a defect we should report."""
    engine = PolicyEngine()
    ruling = engine.review(obs(reason="invalid_order_id"), prop(Action.RETRY))

    assert ruling.verdict is Verdict.VETO
    assert ruling.action is Action.ESCALATE
    assert ruling.rule == "R2_MERCHANT_BUG"


# -- limits -------------------------------------------------------------------


def test_attempt_cap_stops_further_tries() -> None:
    engine = PolicyEngine(Limits(max_attempts_per_payment=3))
    ruling = engine.review(obs(attempts_made=3), prop(Action.RETRY))

    assert ruling.verdict is Verdict.VETO
    assert ruling.action is Action.STOP
    assert ruling.rule == "R3_ATTEMPT_CAP"


def test_customer_contact_cap_spans_the_whole_batch() -> None:
    """A customer with three failed payments must not receive six messages.

    The per-payment cap alone would allow exactly that, which is why the
    per-customer weekly cap exists.
    """
    engine = PolicyEngine(Limits(max_contacts_per_customer_per_week=4))
    ruling = engine.review(
        obs(contacts_made=0, customer_contacts_this_week=4),
        prop(Action.NUDGE, channel=Channel.WHATSAPP),
    )

    assert ruling.verdict is Verdict.VETO
    assert ruling.rule == "R4_CONTACT_CAP_CUSTOMER"


def test_uneconomic_action_blocked_on_small_payments() -> None:
    """Rs 60 of messaging to recover a Rs 99 subscription is a loss."""
    engine = PolicyEngine(Limits(max_spend_ratio=0.15))
    ruling = engine.review(
        obs(amount_paise=9_900),  # Rs 99
        prop(Action.NUDGE, channel=Channel.WHATSAPP),  # Rs 0.85 -> above 15% of Rs 99? no
    )
    assert ruling.verdict is Verdict.ALLOW

    ruling = engine.review(obs(amount_paise=200), prop(Action.NUDGE, channel=Channel.WHATSAPP))
    assert ruling.verdict is Verdict.VETO
    assert ruling.rule == "R6_UNECONOMIC"


def test_escalation_capacity_is_finite() -> None:
    """A policy that escalates everything has forwarded the problem, not solved it."""
    engine = PolicyEngine(Limits(max_escalations=2))
    for _ in range(2):
        r = engine.review(obs(), prop(Action.ESCALATE))
        assert r.verdict is Verdict.ALLOW
        engine.commit(r)

    ruling = engine.review(obs(), prop(Action.ESCALATE))
    assert ruling.verdict is Verdict.VETO
    assert ruling.rule == "R7_ESCALATION_CAPACITY"


def test_budget_ceiling_halts_spending() -> None:
    engine = PolicyEngine(batch_budget_paise=100)
    r = engine.review(obs(), prop(Action.RETRY))  # costs 50
    assert r.verdict is Verdict.ALLOW
    engine.commit(r)
    r = engine.review(obs(), prop(Action.RETRY))
    assert r.verdict is Verdict.ALLOW
    engine.commit(r)

    r = engine.review(obs(), prop(Action.RETRY))
    assert r.verdict is Verdict.VETO
    assert r.rule == "R5_BUDGET"


# -- quiet hours defer rather than cancel -------------------------------------


def test_overnight_nudge_is_deferred_not_cancelled() -> None:
    """The contact is legitimate; only the timing is wrong.

    Vetoing outright would throw away recoverable revenue for no reason, so the
    policy reschedules to the next permitted moment instead.
    """
    engine = PolicyEngine()
    night = datetime(2026, 8, 12, 23, 30)
    ruling = engine.review(obs(now=night), prop(Action.NUDGE, at=night, channel=Channel.SMS))

    assert ruling.verdict is Verdict.DEFER
    assert ruling.action is Action.NUDGE
    assert ruling.at == datetime(2026, 8, 13, 9, 0)
    assert ruling.rule == "R8_QUIET_HOURS"


def test_early_morning_nudge_defers_to_same_day() -> None:
    engine = PolicyEngine()
    dawn = datetime(2026, 8, 12, 4, 15)
    ruling = engine.review(obs(now=dawn), prop(Action.NUDGE, at=dawn, channel=Channel.SMS))

    assert ruling.verdict is Verdict.DEFER
    assert ruling.at == datetime(2026, 8, 12, 9, 0)


def test_retry_is_not_subject_to_quiet_hours() -> None:
    """A silent re-presentment disturbs nobody, so the rule must not apply.

    Blocking overnight retries would forfeit the whole night for outage
    recovery, which is when banks most often come back up.
    """
    engine = PolicyEngine()
    night = datetime(2026, 8, 12, 3, 0)
    ruling = engine.review(obs(now=night), prop(Action.RETRY, at=night))

    assert ruling.verdict is Verdict.ALLOW


# -- normal operation still works --------------------------------------------


def test_reasonable_proposal_passes_untouched() -> None:
    engine = PolicyEngine()
    ruling = engine.review(obs(), prop(Action.RETRY))

    assert ruling.verdict is Verdict.ALLOW
    assert ruling.action is Action.RETRY
    assert ruling.rule == ""


def test_stop_is_always_permitted() -> None:
    """Giving up must never be blocked -- it is the safe default."""
    engine = PolicyEngine(Limits(max_attempts_per_payment=0))
    ruling = engine.review(obs(reason="payment_risk_check_failed"), prop(Action.STOP))

    assert ruling.verdict is Verdict.ALLOW


def test_veto_counts_are_recorded_for_the_audit_trail() -> None:
    engine = PolicyEngine()
    for _ in range(3):
        engine.review(obs(reason="payment_risk_check_failed"), prop(Action.RETRY))

    report = engine.report()
    assert report["total_vetoes"] == 3
    assert report["vetoes_by_rule"]["R1_RISK_BLOCK"] == 3
