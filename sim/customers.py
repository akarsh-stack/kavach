"""The customer population: hidden intent, liquidity cycles, and patience.

This is the least grounded module in the project and docs/CALIBRATION.md says so
plainly. It is entirely Tier 3 -- assumed, not measured. What justifies it is
not realism in the absolute, but that it creates the *tension* a recovery agent
actually faces in production:

  * **Waiting costs money.** Purchase intent decays. A payment recovered on day
    four is worth less than one recovered in the first hour, because by day four
    a good share of those customers have bought elsewhere or cooled off.

  * **Waiting also earns money.** An `insufficient_funds` failure on the 28th
    has poor odds; the same customer on the 2nd, post-salary, is a very
    different proposition.

Those two forces point in opposite directions, and the optimum sits somewhere
between them. That is the decision the agent exists to make, and neither a
fixed-retry schedule nor a lookup table built from Razorpay's documented next
steps can find it -- because the right answer depends on *this* customer, on
*this* date, at *this* amount. It is the clearest place where a model that can
weigh context should beat a rulebook, which is precisely why we built the world
so the question has a right answer we can score against.

Every constant here is an assumption. `eval/sensitivity.py` re-runs the whole
comparison with these scaled up and down, and we report the range.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sim.issuers import ISSUERS, PSPS, Issuer, PSP
from sim.taxonomy import Method


@dataclass
class Customer:
    """A customer of the merchant. All fields below are HIDDEN from the agent.

    The agent sees only this customer's observable history with the merchant --
    prior payment attempts, successes, failures, and how many times we have
    already contacted them. See agent/observe.py. It must infer everything else.
    """

    id: str
    issuer: str
    psp: str

    base_intent: float
    """P(this customer still wants the purchase) at the moment of first failure.
    Subscription renewals sit high; impulse retail sits lower."""

    intent_halflife_hours: float
    """Hours for remaining intent to halve. The cost of waiting."""

    salary_day: int
    """Day of month wages land. Drives the liquidity cycle."""

    liquidity: float
    """0-1 baseline financial headroom. Modulated by the salary cycle."""

    responsiveness: float
    """P(acts on a nudge), before fatigue."""

    patience: int
    """Contacts tolerated before annoyance turns into churn risk."""

    has_alt_method: bool
    """Whether they have a second workable instrument. Gates SWITCH_RAIL."""

    is_subscription: bool
    """Recurring mandate rather than a one-off. Higher intent, slower decay,
    and a saved alternate instrument is far more likely to exist."""

    # -- mutable state, advanced by the world as actions are taken --------
    contacts_made: int = 0
    annoyance: float = 0.0
    churned: bool = False

    # ------------------------------------------------------------------

    def intent_at(self, first_failure: datetime, now: datetime) -> float:
        """Remaining purchase intent after exponential decay.

        Subscriptions decay far more slowly: the customer already decided, and
        the merchant has a standing mandate. A failed Netflix renewal is still
        worth chasing on day five in a way an abandoned cart is not.
        """
        if self.churned:
            return 0.0
        hours = max(0.0, (now - first_failure).total_seconds() / 3600.0)
        return self.base_intent * math.pow(0.5, hours / self.intent_halflife_hours)

    def liquidity_at(self, when: datetime) -> float:
        """Financial headroom on a given date, following the salary cycle.

        Peaks the day wages land and decays through the month, with a floor --
        nobody is at exactly zero, and some customers are never short. The shape
        is a triangular ramp rather than anything fancier; we have no data that
        would justify more precision, and pretending otherwise would be exactly
        the kind of false rigour this project is arguing against.
        """
        days_since_salary = (when.day - self.salary_day) % 30
        # Full headroom at salary day, tapering to ~35% by the end of the cycle.
        cycle = 1.0 - 0.65 * (days_since_salary / 30.0)
        return min(1.0, max(0.05, self.liquidity * cycle))

    def days_until_salary(self, when: datetime) -> int:
        return (self.salary_day - when.day) % 30

    def nudge_success_odds(self) -> float:
        """P(responds to one more contact), degraded by fatigue.

        Each prior contact costs responsiveness. Past `patience`, extra contacts
        stop helping and start doing damage -- economics/costs.py prices that
        damage, and agent/policy.py caps contacts before we get there. A recovery
        system that ignores this recovers more payments this week and fewer
        customers this year.
        """
        if self.churned:
            return 0.0
        decay = math.pow(0.62, self.contacts_made)
        return self.responsiveness * decay

    def register_contact(self) -> float:
        """Record one outbound contact. Returns the resulting churn hazard.

        The caller (sim/world.py) owns the RNG and rolls against this, so that a
        run stays reproducible from its seed. Below `patience` the hazard is
        zero; past it, each further contact carries a real chance of losing the
        customer outright.
        """
        self.contacts_made += 1
        overage = max(0, self.contacts_made - self.patience)
        self.annoyance += 1.0 + 1.8 * overage
        return 0.0 if overage == 0 else min(0.45, 0.11 * overage)


@dataclass
class Population:
    customers: dict[str, Customer] = field(default_factory=dict)

    def get(self, cid: str) -> Customer:
        return self.customers[cid]

    def __len__(self) -> int:
        return len(self.customers)


# Merchant archetype mix. A payments company sees all of these, and they behave
# differently enough under recovery that lumping them together would hide the
# effect we are trying to measure.
_ARCHETYPES = (
    # (label, weight, base_intent, halflife_h, responsiveness, patience, alt_p, sub_p)
    ("subscription_renewal", 0.30, 0.92, 168.0, 0.55, 4, 0.72, 1.00),
    ("considered_purchase", 0.24, 0.78, 52.0, 0.48, 3, 0.55, 0.00),
    ("impulse_retail", 0.28, 0.52, 14.0, 0.34, 2, 0.44, 0.00),
    ("high_value_b2b", 0.10, 0.88, 120.0, 0.62, 5, 0.80, 0.15),
    ("price_sensitive", 0.08, 0.41, 9.0, 0.26, 2, 0.30, 0.00),
)


def build_population(seed: int, n: int) -> Population:
    rng = random.Random(seed ^ 0xC0FFEE)
    pop = Population()

    labels = [a[0] for a in _ARCHETYPES]
    weights = [a[1] for a in _ARCHETYPES]
    spec = {a[0]: a for a in _ARCHETYPES}

    issuer_codes = [i.code for i in ISSUERS]
    issuer_w = [i.volume_share for i in ISSUERS]
    psp_codes = [p.code for p in PSPS]
    psp_w = [p.volume_share for p in PSPS]

    for k in range(n):
        label = rng.choices(labels, weights=weights, k=1)[0]
        _, _, intent, halflife, resp, patience, alt_p, sub_p = spec[label]

        # Salary days cluster hard at month start in India, with a secondary
        # bump around the 7th for public sector and a long tail for the rest.
        r = rng.random()
        if r < 0.55:
            salary_day = rng.choice([1, 1, 1, 2, 2, 3])
        elif r < 0.78:
            salary_day = rng.choice([5, 6, 7, 7, 8])
        else:
            salary_day = rng.randint(9, 28)

        cust = Customer(
            id=f"cust_{k:05d}",
            issuer=rng.choices(issuer_codes, weights=issuer_w, k=1)[0],
            psp=rng.choices(psp_codes, weights=psp_w, k=1)[0],
            base_intent=min(0.99, max(0.05, rng.gauss(intent, 0.09))),
            intent_halflife_hours=max(3.0, rng.gauss(halflife, halflife * 0.28)),
            salary_day=salary_day,
            liquidity=min(1.0, max(0.08, rng.betavariate(2.6, 2.2))),
            responsiveness=min(0.95, max(0.05, rng.gauss(resp, 0.11))),
            patience=patience,
            has_alt_method=rng.random() < alt_p,
            is_subscription=rng.random() < sub_p,
        )
        cust.archetype = label  # type: ignore[attr-defined]
        pop.customers[cust.id] = cust

    return pop


def archetype_mix(pop: Population) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in pop.customers.values():
        label = getattr(c, "archetype", "unknown")
        out[label] = out.get(label, 0) + 1
    return out
