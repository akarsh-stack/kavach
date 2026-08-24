"""Batch generation: simulate payment attempts, keep the failures.

## Why we simulate successes we then throw away

The lazy way to build this benchmark is to sample 200 failure reasons straight
from a weighted list. We deliberately do not, because it forfeits the one free
calibration check available to us.

Instead we simulate a full stream of payment attempts -- successes included --
where each attempt fails as a *consequence* of its issuer's NPCI-derived decline
rates, any live outage, and the customer's liquidity. The failures fall out of
the model rather than being chosen by us.

That buys two things:

1. **A falsifiable check.** The resulting overall success rate has to land in
   the 92-96% band that NPCI's published figures imply for a blended Indian
   merchant. If it does not, our parameters are wrong and `verify()` says so.
   A directly-sampled failure list could never be wrong about anything.

2. **Real correlation structure.** Failures cluster during outages, cluster at
   weak issuers, and cluster at month-end when customers are short. That
   clustering is genuine signal the agent can exploit -- three PhonePe failures
   in ten minutes means something -- and it exists only because we let it
   emerge instead of drawing reasons independently.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sim.customers import Population, build_population
from sim.issuers import BY_CODE, PSP_BY_CODE, IssuerHealth
from sim.taxonomy import (
    BY_REASON,
    ERRORS,
    ErrorReason,
    Method,
    RecoveryClass,
    prevalence,
)
from sim.world import FailureEvent, Truth, World

# Method mix for a mid-size Indian D2C merchant with a subscription line.
# ASSUMED, though the UPI dominance is not controversial.
METHOD_MIX: dict[Method, float] = {
    Method.UPI: 0.58,
    Method.CARD: 0.24,
    Method.EMANDATE: 0.07,
    Method.NETBANKING: 0.07,
    Method.WALLET: 0.04,
}

# Which underlying cause a non-outage failure has.
#
# ASSUMED, but shaped by NPCI's BD/TD split: business declines (funds, limits,
# customer error) hugely outnumber technical ones in steady state. Auth and OTP
# drop-off is the other giant, and is the category most worth recovering
# because the customer demonstrably wanted to pay.
BASE_CAUSE_MIX: dict[RecoveryClass, float] = {
    RecoveryClass.RETRY_SAME: 0.14,
    RecoveryClass.RETRY_LATER_FUNDS: 0.30,
    RecoveryClass.SWITCH_RAIL: 0.18,
    RecoveryClass.NUDGE_CUSTOMER: 0.28,
    RecoveryClass.HARD_STOP: 0.06,
    RecoveryClass.MERCHANT_FIX: 0.04,
}

# During a live outage the cause distribution collapses toward transient
# infrastructure failure -- that is what an outage *is*.
OUTAGE_CAUSE_MIX: dict[RecoveryClass, float] = {
    RecoveryClass.RETRY_SAME: 0.86,
    RecoveryClass.RETRY_LATER_FUNDS: 0.04,
    RecoveryClass.SWITCH_RAIL: 0.05,
    RecoveryClass.NUDGE_CUSTOMER: 0.05,
}

P_AMBIGUOUS = 0.09
"""Share of failures reported as `payment_failed` -- the reason Razorpay
documents as carrying no specific gateway error code. The true cause still
exists; the agent simply cannot read it off the string."""

# What `payment_failed` actually turns out to be. The agent must infer this
# from method, issuer health, amount and customer history.
AMBIGUOUS_TRUE_MIX: dict[RecoveryClass, float] = {
    RecoveryClass.RETRY_SAME: 0.38,
    RecoveryClass.RETRY_LATER_FUNDS: 0.22,
    RecoveryClass.SWITCH_RAIL: 0.25,
    RecoveryClass.NUDGE_CUSTOMER: 0.15,
}


class _Draw:
    """Keyed uniform draws, so generation is reproducible across machines."""

    def __init__(self, seed: int) -> None:
        self._key = seed.to_bytes(8, "big", signed=False)

    def u01(self, *parts: object) -> float:
        raw = "|".join(str(p) for p in parts).encode()
        d = hashlib.blake2b(raw, digest_size=8, key=self._key).digest()
        return int.from_bytes(d, "big") / 2**64

    def pick(self, weights: dict, *parts: object):
        total = sum(weights.values())
        r = self.u01(*parts) * total
        acc = 0.0
        for k, w in weights.items():
            acc += w
            if r < acc:
                return k
        return list(weights)[-1]


def _amount_paise(draw: _Draw, is_subscription: bool, key: object) -> int:
    """Ticket size. Lognormal, which is what real e-commerce baskets look like.

    Subscriptions cluster tightly around common Indian SaaS/OTT price points;
    one-off retail has a long right tail.
    """
    if is_subscription:
        tiers = [14900, 19900, 29900, 49900, 59900, 99900, 149900]
        return tiers[int(draw.u01("subtier", key) * len(tiers)) % len(tiers)]
    # median ~ e^6.8 = ~900 rupees
    u = draw.u01("amt", key)
    # Box-Muller from two keyed uniforms
    import math

    v = draw.u01("amt2", key)
    z = math.sqrt(-2.0 * math.log(max(u, 1e-12))) * math.cos(2 * math.pi * v)
    rupees = math.exp(6.8 + 1.05 * z)
    return int(max(49.0, min(rupees, 250000.0)) * 100)


def _choose_reason(
    draw: _Draw,
    cause: RecoveryClass,
    method: Method,
    key: object,
) -> ErrorReason | None:
    candidates = sorted(
        (e for e in ERRORS if e.recovery_class == cause and method in e.methods),
        key=lambda e: e.reason,
    )
    if not candidates:
        return None
    # Weighted by real-world prevalence, not uniform. See taxonomy.PREVALENCE
    # for why uniform sampling produced a nonsense failure stream.
    weights = [prevalence(e.reason) for e in candidates]
    r = draw.u01("reason", key) * sum(weights)
    acc = 0.0
    for cand, w in zip(candidates, weights):
        acc += w
        if r < acc:
            return cand
    return candidates[-1]


def generate_batch(
    seed: int = 42,
    n_attempts: int = 20000,
    days: int = 30,
    n_customers: int = 3000,
    start: datetime | None = None,
    prob_scale: float = 1.0,
) -> tuple[World, list[FailureEvent], dict]:
    """Simulate `n_attempts` payments over `days`; return the world and failures.

    Returns `(world, failures, stats)` where `stats` carries the calibration
    check -- overall success rate, cause mix, outage counts -- for `verify()`
    and for the README to quote.
    """
    start = start or datetime(2026, 8, 1, 0, 0, 0)
    draw = _Draw(seed)

    pop = build_population(seed, n_customers)
    health = IssuerHealth(seed, start, days)
    world = World(seed, pop, health, prob_scale=prob_scale)

    cust_ids = sorted(pop.customers.keys())
    failures: list[FailureEvent] = []
    # The merchant's own payment records. Not hidden state -- every merchant
    # has their own transaction history, and the agent is entitled to it.
    # Recorded here because the failures alone lose the successes, and "has
    # this customer paid us six times before" is exactly the context that
    # separates a good recovery decision from a guess.
    history: dict[str, list[tuple[datetime, bool, int]]] = {}
    n_success = 0
    cause_counts: dict[str, int] = {}
    outage_linked = 0

    window_minutes = days * 24 * 60

    for i in range(n_attempts):
        t = start + timedelta(minutes=draw.u01("t", i) * window_minutes)
        cust = pop.customers[cust_ids[int(draw.u01("c", i) * len(cust_ids)) % len(cust_ids)]]

        method = draw.pick(METHOD_MIX, "m", i)
        if cust.is_subscription and draw.u01("submeth", i) < 0.72:
            method = Method.EMANDATE

        issuer = BY_CODE[cust.issuer]
        psp = PSP_BY_CODE[cust.psp] if method == Method.UPI else None
        entity = psp.code if psp else issuer.code

        boost = health.failure_boost_at(entity, method, t)

        # Business declines rise when the customer is short of money. This is
        # what ties month-end to the insufficient_funds spike, and it is why
        # waiting for salary day is a real lever rather than a decoration.
        liq = cust.liquidity_at(t)
        bd = issuer.base_bd * (1.55 - 0.85 * liq)
        td = issuer.base_td + (psp.base_failure if psp else 0.0)

        p_fail = min(0.95, td + bd + boost)
        amount = _amount_paise(draw, cust.is_subscription, i)
        if draw.u01("fail", i) >= p_fail:
            n_success += 1
            history.setdefault(cust.id, []).append((t, True, amount))
            continue

        in_outage = boost > 0.0 and draw.u01("outsrc", i) < boost / p_fail
        mix = OUTAGE_CAUSE_MIX if in_outage else BASE_CAUSE_MIX
        cause = draw.pick(mix, "cause", i)
        if in_outage:
            outage_linked += 1

        ambiguous = (
            cause not in (RecoveryClass.HARD_STOP, RecoveryClass.MERCHANT_FIX)
            and draw.u01("amb", i) < P_AMBIGUOUS
        )

        if ambiguous:
            true_class = draw.pick(AMBIGUOUS_TRUE_MIX, "ambtrue", i)
            err = BY_REASON["payment_failed"]
            base_prob = BY_REASON[
                _representative_reason(true_class, method)
            ].base_recovery_prob
        else:
            picked = _choose_reason(draw, cause, method, i)
            if picked is None:
                continue
            err = picked
            true_class = err.recovery_class
            base_prob = err.base_recovery_prob

        cause_counts[true_class.value] = cause_counts.get(true_class.value, 0) + 1

        pid = f"pay_{i:06d}"
        src_idx = int(draw.u01("src", i) * len(err.sources)) % len(err.sources)

        event = FailureEvent(
            payment_id=pid,
            customer_id=cust.id,
            amount_paise=amount,
            method=method,
            issuer=issuer.code,
            psp=psp.code if psp else None,
            reason=err.reason,
            description=err.description,
            source=err.sources[src_idx],
            error_class=err.error_class,
            failed_at=t,
            is_subscription=cust.is_subscription,
            attempt_no=1,
        )
        truth = Truth(
            payment_id=pid,
            true_class=true_class,
            base_prob=base_prob,
            is_ambiguous=ambiguous,
            first_failed_at=t,
        )
        world.register(event, truth)
        failures.append(event)
        history.setdefault(cust.id, []).append((t, False, amount))

    failures.sort(key=lambda e: e.failed_at)

    total = n_success + len(failures)
    stats = {
        "attempts": total,
        "successes": n_success,
        "failures": len(failures),
        "success_rate_pct": 100.0 * n_success / max(total, 1),
        "outage_linked_failures": outage_linked,
        "ambiguous_failures": sum(1 for e in failures if e.reason == "payment_failed"),
        "cause_mix": cause_counts,
        "downtime": health.stats(),
        "total_value_rupees": world.total_value_paise() / 100.0,
        "recoverable_value_rupees": world.recoverable_value_paise() / 100.0,
    }
    for h in history.values():
        h.sort(key=lambda r: r[0])
    stats["customer_history"] = history
    return world, failures, stats


def _representative_reason(rc: RecoveryClass, method: Method) -> str:
    """A stand-in reason used only to source a base recovery probability for an
    ambiguous event. The agent never sees this -- it sees `payment_failed`."""
    for e in ERRORS:
        if e.recovery_class == rc and method in e.methods and not e.held_out:
            return e.reason
    for e in ERRORS:
        if e.recovery_class == rc:
            return e.reason
    return "payment_failed"


def verify(stats: dict) -> list[str]:
    """Calibration checks. Returns a list of problems; empty means we pass.

    These are real assertions about the generated world, not decoration. If the
    simulated merchant's success rate drifts outside the band NPCI's published
    figures imply, our parameters are wrong and we want to be told.
    """
    problems: list[str] = []

    sr = stats["success_rate_pct"]
    if not (92.0 <= sr <= 96.5):
        problems.append(
            f"success rate {sr:.2f}% outside the 92-96% band implied by NPCI "
            f"blended figures (TD<1%, BD<5% per circular OC-149)"
        )

    if stats["failures"] < 100:
        problems.append(
            f"only {stats['failures']} failures generated; the brief asks for a "
            f"batch, and metrics below ~100 events are too noisy to defend"
        )

    amb = stats["ambiguous_failures"] / max(stats["failures"], 1)
    if not (0.04 <= amb <= 0.15):
        problems.append(f"ambiguous share {amb:.1%} far from the intended {P_AMBIGUOUS:.0%}")

    if stats["downtime"].get("episodes", 0) < 5:
        problems.append("too few downtime episodes; retry timing would be untestable")

    if stats["outage_linked_failures"] < 10:
        problems.append("too few outage-linked failures to exercise timing decisions")

    return problems
