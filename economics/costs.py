"""What recovery costs. Without this, "money recovered" is a vanity metric.

Any policy can win on gross recovery by trying everything on everything. The
question a merchant actually cares about is whether the recovery was worth
making, and that requires pricing four different things:

1. **Direct cost of the attempt** -- messaging fees, and the small but real cost
   of re-presenting a payment.
2. **MDR on what we recover** -- recovering Rs 1,000 does not put Rs 1,000 in
   the merchant's account. Razorpay's standard domestic rate takes a cut, so a
   recovery that costs more than its own margin is a loss dressed as a win.
3. **Human time** on escalations.
4. **Customer goodwill** burned by over-contacting.

The fourth is the softest number in the project and we say so loudly. It is the
primary target of the sensitivity analysis, because a cost model that quietly
sets goodwill to zero will always conclude that harassing customers is optimal
-- which is exactly the conclusion a naive recovery bot reaches, and exactly the
one a payments company must not ship.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.actions import Action, Channel


@dataclass(frozen=True)
class CostModel:
    """All figures in paise. Provenance noted per field."""

    # -- messaging. Roughly current Indian business-messaging rates. --------
    whatsapp_paise: int = 85
    """WhatsApp Business utility message. ASSUMED, order-of-magnitude from
    published Indian utility-template pricing."""

    sms_paise: int = 20
    """Transactional SMS. ASSUMED, in line with common Indian gateway rates."""

    email_paise: int = 2
    """Effectively free at volume."""

    # -- the payment attempt itself ----------------------------------------
    retry_paise: int = 50
    """ASSUMED. Razorpay does not charge for failed attempts, so this is not a
    gateway fee -- it prices the operational reality that re-presenting
    payments is not free. Card networks levy penalties on merchants with
    excessive retry ratios, issuers deprioritise noisy merchants, and someone
    has to run the infrastructure. Setting this to zero would let a policy
    retry infinitely at no cost, which is neither realistic nor interesting."""

    switch_rail_paise: int = 85
    """Offering an alternative rail means reaching the customer, so it carries
    a message cost on top of the attempt."""

    # -- human time ---------------------------------------------------------
    escalation_paise: int = 4500
    """~15 minutes of a fully-loaded ops analyst. ASSUMED.

    Deliberately expensive. If escalation were cheap the optimal policy would
    escalate everything, and we would have built a very elaborate way of
    recommending that a human do the work."""

    # -- what Razorpay keeps ------------------------------------------------
    mdr_rate: float = 0.02
    """Razorpay's standard domestic rate. Applied to recovered value, because
    the merchant nets the payment minus MDR, not the payment."""

    # -- goodwill -----------------------------------------------------------
    annoyance_paise_per_unit: int = 1200
    """Value destroyed per unit of customer annoyance. THE SOFTEST NUMBER HERE.

    Annoyance accrues only for contacts beyond a customer's tolerance, and it
    stands in for reduced future purchase rate and unsubscribes. There is no
    public figure to anchor this to, so we do not pretend there is -- instead
    evaluation/sensitivity.py sweeps it across two orders of magnitude and we
    report whether the conclusion survives. If it does not, that is the finding."""

    churn_paise: int = 90000
    """Cost of losing a customer outright. ASSUMED, roughly one year of margin
    for a mid-size D2C merchant. Charged when over-contacting causes churn."""

    def action_cost(self, action: Action, channel: Channel | None = None) -> int:
        """Direct cost of taking one action, before outcomes."""
        if action == Action.RETRY:
            return self.retry_paise
        if action == Action.SWITCH_RAIL:
            return self.switch_rail_paise
        if action == Action.NUDGE:
            return {
                Channel.WHATSAPP: self.whatsapp_paise,
                Channel.SMS: self.sms_paise,
                Channel.EMAIL: self.email_paise,
            }[channel or Channel.SMS]
        if action == Action.ESCALATE:
            return self.escalation_paise
        return 0  # STOP is free, and is often the best-value move available.

    def net_recovery(self, gross_paise: int) -> int:
        """What actually lands in the merchant's account."""
        return int(round(gross_paise * (1.0 - self.mdr_rate)))

    def annoyance_cost(self, annoyance_units: float) -> int:
        return int(round(annoyance_units * self.annoyance_paise_per_unit))


DEFAULT = CostModel()


@dataclass
class Ledger:
    """Running tally for one policy's run over one batch.

    Kept deliberately granular: a single "net recovered" figure hides whether a
    policy won by recovering more or simply by spending less, and those are very
    different products.
    """

    gross_recovered_paise: int = 0
    mdr_paise: int = 0
    attempt_cost_paise: int = 0
    message_cost_paise: int = 0
    escalation_cost_paise: int = 0
    annoyance_cost_paise: int = 0
    churn_cost_paise: int = 0

    retries: int = 0
    switches: int = 0
    nudges: int = 0
    escalations: int = 0
    stops: int = 0

    recovered_count: int = 0
    wasted_attempts: int = 0
    """Active attempts spent on payments that were never recoverable. The
    clearest single measure of a policy's judgement."""

    policy_violations: int = 0
    """Must be zero. Any non-zero value is a bug, not a trade-off."""

    def spend_paise(self) -> int:
        return (
            self.attempt_cost_paise
            + self.message_cost_paise
            + self.escalation_cost_paise
            + self.annoyance_cost_paise
            + self.churn_cost_paise
        )

    def net_paise(self) -> int:
        """The headline. Gross, minus MDR, minus every cost of chasing it."""
        return self.gross_recovered_paise - self.mdr_paise - self.spend_paise()

    def record_action(
        self,
        action: Action,
        channel: Channel | None,
        costs: CostModel,
    ) -> None:
        c = costs.action_cost(action, channel)
        if action == Action.RETRY:
            self.retries += 1
            self.attempt_cost_paise += c
        elif action == Action.SWITCH_RAIL:
            self.switches += 1
            self.message_cost_paise += c
        elif action == Action.NUDGE:
            self.nudges += 1
            self.message_cost_paise += c
        elif action == Action.ESCALATE:
            self.escalations += 1
            self.escalation_cost_paise += c
        elif action == Action.STOP:
            self.stops += 1

    def record_recovery(self, gross_paise: int, costs: CostModel) -> None:
        self.gross_recovered_paise += gross_paise
        self.mdr_paise += gross_paise - costs.net_recovery(gross_paise)
        self.recovered_count += 1

    def summary(self) -> dict[str, float]:
        r = 1 / 100.0
        return {
            "gross_recovered_rs": self.gross_recovered_paise * r,
            "mdr_rs": self.mdr_paise * r,
            "attempt_cost_rs": self.attempt_cost_paise * r,
            "message_cost_rs": self.message_cost_paise * r,
            "escalation_cost_rs": self.escalation_cost_paise * r,
            "annoyance_cost_rs": self.annoyance_cost_paise * r,
            "churn_cost_rs": self.churn_cost_paise * r,
            "total_spend_rs": self.spend_paise() * r,
            "net_recovered_rs": self.net_paise() * r,
            "recovered_count": self.recovered_count,
            "retries": self.retries,
            "switches": self.switches,
            "nudges": self.nudges,
            "escalations": self.escalations,
            "stops": self.stops,
            "wasted_attempts": self.wasted_attempts,
            "policy_violations": self.policy_violations,
        }
