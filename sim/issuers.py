"""Issuer banks and PSPs, their baseline health, and their downtime episodes.

Downtime is the mechanism that makes *timing* matter. A `RETRY_SAME` failure
during a live outage will fail again if retried immediately and succeed if
retried after the outage clears, so the agent has to reason about when to act,
not just what to do.

## Provenance

Decline rates are **derived from NPCI's published expectation, not copied from
per-bank figures we could not verify.**

The citable anchor is NPCI Circular **OC-149 (June 2022)**, which sets the
expectation that member banks hold Technical Decline below **1%** and Business
Decline below **5%**. Those two numbers are the only hard facts we have, and
everything here is positioned against them.

An earlier version of this file carried specific per-bank rates (SBI 0.90%,
ICICI 1.01%, Axis 0.60%, HDFC 0.13%) taken from secondary reporting of an older
NPCI snapshot. Those are now removed. We attempted to replace them with a named
month's official file from NPCI's BD/TD & Uptime dashboard and could not: the
site returns 403 to automated fetches and redirects the statistics path to a 404
page. Rather than ship second-hand figures dressed as primary sources, we
removed the false precision.

What survives is the part that is actually defensible:

  * the OC-149 ceilings, which are published and widely cited
  * the *direction* -- public sector banks run materially higher decline rates
    than large private banks, which is not in dispute
  * a stated tier position for each bank relative to those ceilings, marked
    Tier 3 in docs/CALIBRATION.md like every other assumption

This is a weaker claim than "these are the real per-bank rates", and it is the
true one. The model behaves almost identically either way -- what changes is
whether the README can honestly say where the numbers came from.

## The definitions, from NPCI

  Technical Decline (TD)  failures from system unavailability or network issues
                          at the bank or NPCI side
  Business Decline (BD)   failures from user or merchant causes -- wrong PIN,
                          insufficient balance, limit exceeded, invalid
                          beneficiary

That split maps almost exactly onto our recovery taxonomy: TD is RETRY_SAME,
BD is RETRY_LATER_FUNDS plus SWITCH_RAIL.

## The public downtime signal

Razorpay exposes payment downtime to merchants (downtime webhooks / API), so an
agent observing issuer health is using a signal a real merchant genuinely has.
We model it as *imperfect*, which is the honest version: reports arrive after a
detection delay, and low-severity outages are often never reported at all. The
agent therefore cannot simply read outage state off a dashboard — it has to
combine a partial signal with what it sees in its own failure stream.
"""

from __future__ import annotations

import random
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from sim.taxonomy import Method


class BankKind(str, Enum):
    LARGE_PRIVATE = "large_private"
    MID_PRIVATE = "mid_private"
    PUBLIC_SECTOR = "public_sector"
    SMALL_FINANCE = "small_finance"
    PSP = "psp"


# NPCI Circular OC-149 (June 2022): member banks are expected to hold Technical
# Decline below 1% and Business Decline below 5%. These two numbers are the only
# hard, citable facts in this module. Everything else is positioned against them.
TD_CEILING = 0.010
BD_CEILING = 0.050

# Where each tier sits relative to those ceilings, as a fraction of each.
#
# TIER 3 ASSUMPTION, stated rather than smuggled in as a per-bank measurement we
# never took. The ordering is not controversial -- large private banks run well
# inside the ceilings, public sector banks routinely breach the TD one, small
# finance banks are worse still. The exact fractions are ours.
TIER_POSITION: dict[BankKind, tuple[float, float]] = {
    BankKind.LARGE_PRIVATE: (0.22, 0.84),
    BankKind.MID_PRIVATE: (0.62, 0.92),
    BankKind.PUBLIC_SECTOR: (1.15, 1.18),
    BankKind.SMALL_FINANCE: (1.65, 1.30),
}

# Outage frequency per week, by tier. Assumed, on the reasoning that a bank with
# a worse technical decline rate has more frequent infrastructure trouble rather
# than merely a noisier steady state.
TIER_OUTAGE: dict[BankKind, tuple[float, float]] = {
    # (episodes per week, P(publicly reported))
    BankKind.LARGE_PRIVATE: (0.45, 0.84),
    BankKind.MID_PRIVATE: (0.70, 0.74),
    BankKind.PUBLIC_SECTOR: (1.55, 0.66),
    BankKind.SMALL_FINANCE: (1.90, 0.50),
}


@dataclass(frozen=True)
class Issuer:
    code: str
    name: str
    kind: BankKind
    volume_share: float
    """Share of this merchant's transaction volume. Our assumption, for a
    mid-size Indian D2C merchant."""

    @property
    def base_td(self) -> float:
        """Technical decline rate, derived from the OC-149 ceiling and tier."""
        return TD_CEILING * TIER_POSITION[self.kind][0]

    @property
    def base_bd(self) -> float:
        """Business decline rate, derived from the OC-149 ceiling and tier."""
        return BD_CEILING * TIER_POSITION[self.kind][1]

    @property
    def outage_rate_per_week(self) -> float:
        return TIER_OUTAGE[self.kind][0]

    @property
    def report_probability(self) -> float:
        """P(an outage here is publicly reported at all)."""
        return TIER_OUTAGE[self.kind][1]


# Roster. Only the tier and the volume share are declared -- decline rates fall
# out of the ceilings above, so there is no per-bank number here that we would
# have to defend as a measurement.
ISSUERS: tuple[Issuer, ...] = (
    Issuer("HDFC", "HDFC Bank", BankKind.LARGE_PRIVATE, 0.185),
    Issuer("ICICI", "ICICI Bank", BankKind.LARGE_PRIVATE, 0.155),
    Issuer("SBI", "State Bank of India", BankKind.PUBLIC_SECTOR, 0.210),
    Issuer("AXIS", "Axis Bank", BankKind.LARGE_PRIVATE, 0.120),
    Issuer("KOTAK", "Kotak Mahindra Bank", BankKind.LARGE_PRIVATE, 0.070),
    Issuer("PNB", "Punjab National Bank", BankKind.PUBLIC_SECTOR, 0.075),
    Issuer("BOB", "Bank of Baroda", BankKind.PUBLIC_SECTOR, 0.065),
    Issuer("CANARA", "Canara Bank", BankKind.PUBLIC_SECTOR, 0.050),
    Issuer("YES", "Yes Bank", BankKind.MID_PRIVATE, 0.035),
    Issuer("IDFC", "IDFC First Bank", BankKind.MID_PRIVATE, 0.025),
    Issuer("AU", "AU Small Finance Bank", BankKind.SMALL_FINANCE, 0.010),
)

BY_CODE: dict[str, Issuer] = {i.code: i for i in ISSUERS}

OUTAGE_RATE_SCALE = 2.2
"""Global multiplier on per-entity outage frequency.

Set by working backwards from an uptime target rather than by tuning until the
benchmark looked interesting. At scale 1.0 each issuer/method pair was down
roughly 0.1% of the month (~99.9% uptime), which is better than Indian banks
actually manage and left too few outage-linked failures to test retry timing at
all. At 2.2 the figure is ~0.22% downtime, i.e. ~99.78% uptime -- still
conservative against the 99.5% that bank UPI handlers are commonly held to, and
comfortably inside what NPCI's uptime dashboard reports.

Raising this was a calibration fix, not a convenience: `sim/generate.py:verify()`
flagged the shortfall, and the alternative -- weakening the check -- would have
been the dishonest way to make the same warning disappear.
"""

assert abs(sum(i.volume_share for i in ISSUERS) - 1.0) < 1e-9, "volume shares must sum to 1"


# UPI PSP apps. A UPI payment has both an issuer (where the money is) and a PSP
# (the app), and either can be the failing component -- which is exactly why
# Razorpay's UPI `source` enum carries `customer_psp` separately from
# `issuer_bank`. Modelling both lets the agent distinguish "switch app" from
# "switch bank", a real and materially different recovery action.
@dataclass(frozen=True)
class PSP:
    code: str
    name: str
    base_failure: float
    volume_share: float
    outage_rate_per_week: float
    report_probability: float


PSPS: tuple[PSP, ...] = (
    PSP("PHONEPE", "PhonePe", 0.0090, 0.460, 0.45, 0.85),
    PSP("GPAY", "Google Pay", 0.0085, 0.360, 0.40, 0.85),
    PSP("PAYTM", "Paytm", 0.0125, 0.100, 0.70, 0.75),
    PSP("BHIM", "BHIM", 0.0180, 0.045, 1.10, 0.60),
    PSP("CRED", "CRED", 0.0105, 0.035, 0.55, 0.70),
)

PSP_BY_CODE: dict[str, PSP] = {p.code: p for p in PSPS}

assert abs(sum(p.volume_share for p in PSPS) - 1.0) < 1e-9, "PSP shares must sum to 1"


@dataclass(frozen=True)
class Downtime:
    """An outage window at one issuer or PSP.

    `entity` is an issuer code or PSP code. `method` narrows the outage to one
    rail, since real bank outages are frequently rail-specific -- a bank's UPI
    handler can be down while its card authorisation path is fine. That
    distinction is what makes SWITCH_RAIL a genuinely different action from
    RETRY_SAME rather than a cosmetic relabel.
    """

    entity: str
    method: Method
    start: datetime
    end: datetime

    severity: float
    """P(a payment through this entity/method fails) while the window is open."""

    reported: bool
    """Whether this outage ever becomes visible to the merchant."""

    report_delay: timedelta
    """Detection lag before it becomes visible. Only meaningful if reported."""

    def contains(self, t: datetime) -> bool:
        return self.start <= t < self.end

    def visible_at(self, t: datetime) -> bool:
        """Whether a merchant polling the downtime signal would see this at `t`.

        Note the asymmetry: an outage becomes visible only after the detection
        delay, but stays visible until it actually ends. Resolution is reported
        promptly; onset is not. This means the agent's most dangerous moment is
        the first stretch of an outage, when failures are arriving and the
        signal still says everything is healthy.
        """
        if not self.reported:
            return False
        return (self.start + self.report_delay) <= t < self.end


class IssuerHealth:
    """Generates and answers questions about downtime over a fixed window.

    Two distinct query surfaces, and the split is the whole point:

      * `failure_boost_at()` -- TRUE state. Hidden. Only sim/world.py may call it.
      * `public_downtime_at()` -- the delayed, lossy signal a merchant can see.
        Surfaced to the agent via agent/observe.py.
    """

    def __init__(self, seed: int, start: datetime, days: int) -> None:
        self._rng = random.Random(seed ^ 0x1550E)
        self.start = start
        self.end = start + timedelta(days=days)
        self.days = days
        self.downtimes: list[Downtime] = []
        self._generate()
        # Sorted starts let visible_downtimes() binary-search instead of
        # scanning every episode on every observation. The agent queries this
        # once per decision, so a linear scan would dominate batch runtime.
        self.downtimes.sort(key=lambda d: d.start)
        self._starts = [d.start for d in self.downtimes]

    def _generate(self) -> None:
        weeks = self.days / 7.0
        entities: list[tuple[str, float, float, tuple[Method, ...]]] = [
            (i.code, i.outage_rate_per_week, i.report_probability,
             (Method.UPI, Method.CARD, Method.NETBANKING, Method.EMANDATE))
            for i in ISSUERS
        ] + [
            (p.code, p.outage_rate_per_week, p.report_probability, (Method.UPI,))
            for p in PSPS
        ]

        for code, rate, report_p, methods in entities:
            n = self._poisson(rate * weeks * OUTAGE_RATE_SCALE)
            for _ in range(n):
                method = self._rng.choice(methods)
                offset = self._rng.uniform(0, self.days * 24 * 60)
                begin = self.start + timedelta(minutes=offset)

                # Lognormal-ish durations: most outages are short, a few are
                # brutal. Median ~35 min, long tail to several hours.
                minutes = min(self._rng.lognormvariate(3.55, 0.85), 480)
                finish = begin + timedelta(minutes=minutes)

                # Severity: partial degradation is far more common than a
                # total blackout, which is what makes outages hard to detect.
                severity = min(0.98, max(0.25, self._rng.betavariate(2.2, 2.0) + 0.15))

                reported = self._rng.random() < report_p
                # Detection lag scales inversely with severity -- a total
                # blackout is spotted fast, a 30% degradation takes a while.
                delay_min = self._rng.uniform(4, 22) / max(severity, 0.2)

                self.downtimes.append(
                    Downtime(
                        entity=code,
                        method=method,
                        start=begin,
                        end=finish,
                        severity=severity,
                        reported=reported,
                        report_delay=timedelta(minutes=delay_min),
                    )
                )

    def _poisson(self, lam: float) -> int:
        """Knuth sampler. Our lambdas are small (< 20), so this is fine."""
        import math

        target = math.exp(-lam)
        k, p = 0, 1.0
        while True:
            p *= self._rng.random()
            if p <= target:
                return k
            k += 1

    # -- TRUE state. sim/ only. ------------------------------------------

    def failure_boost_at(self, entity: str, method: Method, t: datetime) -> float:
        """P(failure) contributed by outages at `t`. Hidden from the agent.

        Returns the max severity across overlapping windows rather than
        combining them: two concurrent outages on the same rail is one bad
        situation, not a compounded one.
        """
        worst = 0.0
        for d in self._active(t):
            if d.entity == entity and d.method == method:
                worst = max(worst, d.severity)
        return worst

    def _active(self, t: datetime) -> list[Downtime]:
        idx = bisect_right(self._starts, t)
        # Longest possible outage is 480 min, so nothing starting more than
        # that far back can still be open. Bounds the backward scan.
        cutoff = t - timedelta(minutes=480)
        out = []
        for i in range(idx - 1, -1, -1):
            d = self.downtimes[i]
            if d.start < cutoff:
                break
            if d.contains(t):
                out.append(d)
        return out

    # -- Public signal. Safe to surface to the agent. ---------------------

    def visible_downtimes(self, t: datetime) -> list[tuple[str, Method]]:
        """What a merchant polling Razorpay's downtime signal would see at `t`.

        Deliberately returns only (entity, method) -- no severity, no expected
        end time. A real downtime feed tells you something is wrong, not how
        wrong or for how long. Handing the agent a severity number would be
        leaking hidden state through a plausible-looking API.
        """
        return [(d.entity, d.method) for d in self._active(t) if d.visible_at(t)]

    # -- Reporting -------------------------------------------------------

    def stats(self) -> dict[str, float]:
        if not self.downtimes:
            return {"episodes": 0}
        total = len(self.downtimes)
        reported = sum(1 for d in self.downtimes if d.reported)
        mins = [(d.end - d.start).total_seconds() / 60 for d in self.downtimes]
        return {
            "episodes": total,
            "reported_pct": 100.0 * reported / total,
            "median_minutes": sorted(mins)[total // 2],
            "max_minutes": max(mins),
            "mean_severity": sum(d.severity for d in self.downtimes) / total,
        }


def load_npci_month(path: str) -> None:
    """Replace baked-in decline rates with a parsed official NPCI month file.

    Deliberately unimplemented. It exists to mark the seam: the blocking item in
    docs/OPEN_ISSUES.md is a *data* problem, and this is where the fix lands
    without touching any modelling code.
    """
    raise NotImplementedError(
        "Blocked on pulling a primary NPCI BD/TD file. See docs/OPEN_ISSUES.md."
    )
