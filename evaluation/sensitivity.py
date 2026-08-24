"""Does the conclusion survive our assumptions being wrong?

Everything in `sim/` marked Tier 3 in docs/CALIBRATION.md is a guess: recovery
probabilities, the price of customer annoyance, the exposure from re-presenting
a risk decline. Any of them could be off by a lot. A result that only holds at
one convenient setting of those guesses is not a result, it is a coincidence we
found and kept.

So this module re-runs the whole comparison across a grid of them and asks one
question: **does the winner change?**

Three axes, chosen because they are the three softest numbers in the project:

  * `prob_scale` -- every assumed recovery probability, scaled. This is the
    global "are we too optimistic" knob. It should NOT change the ranking, since
    it lifts every policy at once, and confirming that is what earns us the
    right to quote a lift instead of a rupee total.

  * `annoyance` -- the cost of over-contacting a customer, swept across two
    orders of magnitude. Set it to zero and a recovery system concludes that
    harassing people is free, which is precisely the conclusion a naive bot
    reaches and a payments company must not ship. Set it high and messaging
    stops being worth it at all. Somewhere between those the honest answer sits,
    and we cannot source it.

  * `exposure` -- the compliance cost of re-presenting a risk decline, including
    **zero**. Zero is the important one: it is the setting under which ignoring
    the risk layer is free, and it is where the naive policies look best. If the
    case for guardrails only survives at our default, we would rather publish
    that than hide it.

The output is deliberately blunt: a table of who wins at each grid point, and a
single verdict line saying whether the ranking ever flips.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from economics.costs import DEFAULT, CostModel
from evaluation.baselines import Policy
from evaluation.harness import run_policy
from sim.generate import generate_batch

# Deliberately wide. A +/-10% sweep would prove nothing.
PROB_SCALES = (0.7, 1.0, 1.3)
ANNOYANCE_MULTIPLIERS = (0.0, 0.1, 1.0, 10.0)
EXPOSURE_MULTIPLIERS = (0.0, 1.0, 5.0)


@dataclass
class GridPoint:
    prob_scale: float
    annoyance_mult: float
    exposure_mult: float
    nets: dict[str, int]

    @property
    def winner(self) -> str:
        return max(self.nets, key=lambda k: self.nets[k])

    def label(self) -> str:
        return (
            f"prob x{self.prob_scale:<4} annoy x{self.annoyance_mult:<5} "
            f"expo x{self.exposure_mult:<4}"
        )


def costs_for(annoyance_mult: float, exposure_mult: float) -> CostModel:
    return replace(
        DEFAULT,
        annoyance_paise_per_unit=int(DEFAULT.annoyance_paise_per_unit * annoyance_mult),
        compliance_exposure_paise=int(DEFAULT.compliance_exposure_paise * exposure_mult),
    )


def sweep(
    make_policies,
    seed: int = 42,
    limit: int = 300,
    n_attempts: int = 20000,
    prob_scales: tuple[float, ...] = PROB_SCALES,
    annoyance_mults: tuple[float, ...] = ANNOYANCE_MULTIPLIERS,
    exposure_mults: tuple[float, ...] = EXPOSURE_MULTIPLIERS,
    progress: bool = True,
) -> list[GridPoint]:
    """Run every policy at every grid point.

    `make_policies` is a zero-argument factory rather than a list, because the
    LLM-backed policies carry mutable usage state and reusing one across 36 grid
    points would pool their token counts into nonsense.
    """
    points: list[GridPoint] = []
    total = len(prob_scales) * len(annoyance_mults) * len(exposure_mults)
    n = 0

    for ps in prob_scales:
        # Regenerated per prob_scale because the scale lives on the World. The
        # batch itself is seed-determined and therefore identical each time --
        # same payments, same amounts, same order -- so only the outcome
        # probabilities move.
        world, failures, stats = generate_batch(
            seed=seed, n_attempts=n_attempts, prob_scale=ps
        )
        for am in annoyance_mults:
            for em in exposure_mults:
                n += 1
                costs = costs_for(am, em)
                nets: dict[str, int] = {}
                for policy in make_policies():
                    r = run_policy(
                        policy,
                        world,
                        failures,
                        stats["customer_history"],
                        limit=limit,
                        costs=costs,
                    )
                    nets[policy.name] = r.ledger.net_paise()
                points.append(GridPoint(ps, am, em, nets))
                if progress:
                    p = points[-1]
                    print(f"  [{n:>2}/{total}] {p.label()}  winner: {p.winner}")
    return points


def verdict(points: list[GridPoint], subject: str = "agent") -> str:
    """The blunt answer: does `subject` win everywhere, sometimes, or never?"""
    if not points:
        return "no grid points"
    if subject not in points[0].nets:
        return f"'{subject}' not present in this sweep"

    wins = sum(1 for p in points if p.winner == subject)
    total = len(points)

    lifts = []
    for p in points:
        others = {k: v for k, v in p.nets.items() if k != subject and not k.startswith(subject)}
        if not others:
            continue
        best = max(others.values())
        if best > 0:
            lifts.append(100.0 * (p.nets[subject] - best) / best)

    lines = []
    lines.append(f"{subject} wins {wins}/{total} grid points")
    if lifts:
        lo, hi = min(lifts), max(lifts)
        lines.append(f"lift over best rival ranges {lo:+.1f}% to {hi:+.1f}%")
        if lo < 0 < hi:
            lines.append(
                "SIGN FLIPS. The conclusion is assumption-dependent and must be "
                "reported as such -- see the losing region below."
            )
        elif hi < 0:
            lines.append("NEGATIVE THROUGHOUT. The subject loses across the whole grid.")
        else:
            lines.append("Sign holds across the whole grid.")

    losers = [p for p in points if p.winner != subject]
    if losers:
        lines.append("")
        lines.append(f"loses at {len(losers)} point(s):")
        for p in losers[:8]:
            lines.append(f"    {p.label()} -> {p.winner}")
        if len(losers) > 8:
            lines.append(f"    ... and {len(losers) - 8} more")
    return "\n".join(lines)


def one_at_a_time(points: list[GridPoint], policies: list[str]) -> str:
    """Readable per-axis view: hold two axes at default, vary the third."""
    rows = []

    def block(title: str, matches) -> None:
        rows.append(f"  {title}")
        sel = [p for p in points if matches(p)]
        if not sel:
            return
        head = f"    {'setting':<16}" + "".join(f"{n:>16}" for n in policies)
        rows.append(head)
        rows.append("    " + "-" * (len(head) - 4))
        for p in sel:
            if title.startswith("recovery"):
                setting = f"x{p.prob_scale}"
            elif title.startswith("annoyance"):
                setting = f"x{p.annoyance_mult}"
            else:
                setting = f"x{p.exposure_mult}"
            cells = "".join(f"{p.nets.get(n, 0) / 100:>16,.0f}" for n in policies)
            rows.append(f"    {setting:<16}{cells}")
        rows.append("")

    block(
        "recovery probability scaled (annoyance x1, exposure x1)",
        lambda p: p.annoyance_mult == 1.0 and p.exposure_mult == 1.0,
    )
    block(
        "annoyance cost scaled (prob x1, exposure x1)",
        lambda p: p.prob_scale == 1.0 and p.exposure_mult == 1.0,
    )
    block(
        "compliance exposure scaled (prob x1, annoyance x1)",
        lambda p: p.prob_scale == 1.0 and p.annoyance_mult == 1.0,
    )
    return "\n".join(rows)


def report(points: list[GridPoint], subject: str = "agent") -> str:
    names = list(points[0].nets) if points else []
    out = ["=" * 78, "  SENSITIVITY -- does the conclusion survive our assumptions?", "=" * 78, ""]
    out.append(one_at_a_time(points, names))
    out.append("=" * 78)
    out.append(verdict(points, subject))
    out.append("=" * 78)
    return "\n".join(out)
