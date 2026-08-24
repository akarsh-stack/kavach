"""What the agent is allowed to know. This module defines the boundary.

Every field on `Observation` has to be defensible as something a real Razorpay
merchant genuinely has access to. The rule we applied, field by field, was:
*could an engineer at a mid-size D2C company read this off their own database or
their Razorpay dashboard on a Tuesday afternoon?* If not, it does not go here.

Three categories pass that test:

1. **The failed payment itself** -- the fields Razorpay puts on a payment object
   and its failed-payment webhook: `reason`, `description`, `source`, amount,
   method, timestamps.

2. **The merchant's own records about their own customer** -- prior payment
   count, prior failures, when we first saw them, what we have already spent
   contacting them this week. Every merchant has this; it is their data.

3. **Razorpay's public downtime signal** -- delayed and lossy, exposed as two
   booleans and nothing more. Deliberately *not* severity, and deliberately not
   an expected end time. A real downtime feed tells you something is wrong, not
   how wrong or for how long. Handing over a severity float would be leaking
   simulator state through a plausible-looking API, which is the usual way this
   kind of benchmark quietly cheats.

What is conspicuously absent: the true recovery class, the recovery
probability, the customer's actual intent or bank balance, and whether an
unreported outage is currently running. Those exist in the simulator and the
agent has no path to them. `tests/test_observability_boundary.py` enforces it
by walking this package's AST on every run.

This module imports nothing from the simulator, and nothing here knows the
simulator exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Observation:
    """One failed payment, as the merchant sees it, at the moment of deciding.

    Field names are checked against an allow-list in the boundary test. Adding
    one means justifying it there against a real Razorpay field.
    """

    # -- the failed payment ------------------------------------------------
    payment_id: str
    customer_id: str
    amount_paise: int
    method: str
    issuer: str
    psp: str | None
    reason: str
    """Razorpay's machine-readable failure reason, e.g. `insufficient_funds`."""
    description: str
    """Razorpay's human-readable description of the failure."""
    source: str
    """Where Razorpay says the error originated: customer, issuer_bank, gateway…"""
    error_class: str
    failed_at: datetime
    is_subscription: bool
    attempt_no: int

    # -- the merchant's own CRM --------------------------------------------
    customer_prior_payments: int
    customer_prior_failures: int
    customer_contacts_this_week: int
    customer_first_seen: datetime
    customer_lifetime_paise: int

    # -- Razorpay's public downtime signal (delayed, lossy) ----------------
    issuer_downtime_reported: bool
    psp_downtime_reported: bool

    # -- what the agent has inferred from its own failure stream -----------
    # Not privileged information: any merchant watching their own webhooks can
    # count how many failures just hit the same bank. This is the signal that
    # lets a good agent spot an outage the downtime feed has not reported yet,
    # which is the single most valuable inference available in this domain --
    # two thirds of outages here are visible in the stream before the feed
    # admits to them, and a third are never reported at all.
    recent_failures_same_entity: int
    recent_failures_same_reason: int

    # -- the agent's own bookkeeping ---------------------------------------
    now: datetime
    attempts_made: int
    """Recovery attempts already spent on THIS payment."""
    contacts_made: int
    """Outbound messages already sent to THIS customer, all payments."""
    budget_remaining_paise: int

    @property
    def amount_rupees(self) -> float:
        return self.amount_paise / 100.0

    @property
    def hours_since_failure(self) -> float:
        return max(0.0, (self.now - self.failed_at).total_seconds() / 3600.0)

    @property
    def any_downtime_reported(self) -> bool:
        return self.issuer_downtime_reported or self.psp_downtime_reported

    @property
    def entity(self) -> str:
        """The component that actually failed: the PSP app for UPI, else the bank.

        Razorpay's UPI `source` enum separates `customer_psp` from `issuer_bank`
        precisely because these are different things, and the right recovery
        differs -- "try another UPI app" is not "try another bank".
        """
        return self.psp if (self.method == "upi" and self.psp) else self.issuer

    def brief(self) -> str:
        """One line for the audit trail and the demo."""
        rs = self.amount_rupees
        sub = " sub" if self.is_subscription else ""
        return (
            f"{self.payment_id} Rs{rs:,.0f}{sub} {self.method}/{self.entity} "
            f"{self.reason} (+{self.hours_since_failure:.1f}h)"
        )


@dataclass
class CustomerLedger:
    """The merchant's running record of one customer.

    Kept by the agent as it works through a batch, exactly as a real recovery
    service would keep state between webhooks. This is the agent's own memory,
    not a window into the simulator.
    """

    customer_id: str
    first_seen: datetime
    prior_payments: int = 0
    prior_failures: int = 0
    lifetime_paise: int = 0
    contact_times: list[datetime] = field(default_factory=list)

    def contacts_in_week_before(self, now: datetime) -> int:
        cutoff = now - timedelta(days=7)
        return sum(1 for t in self.contact_times if t >= cutoff)

    def record_contact(self, when: datetime) -> None:
        self.contact_times.append(when)
