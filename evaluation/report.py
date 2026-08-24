"""Turns run results into the comparison table, with the caveats attached.

Two rules this module enforces rather than trusts:

  * **A stub run is never labelled a model run.** If a policy claims to use an
    LLM but its engine is `stub`, every line about it is marked and the headline
    lift is suppressed. The stub exists to test plumbing, and a number produced
    by it must not be able to escape into a slide.

  * **The headline is a lift over the best baseline, not an absolute.** If our
    recovery probabilities are globally too generous, every policy benefits
    equally and the ranking survives; the rupee figure would not. So the rupee
    figure is reported, but the *claim* is the lift.
"""

from __future__ import annotations

from evaluation.harness import RunResult

R = 1 / 100.0


def _rs(paise: int) -> str:
    return f"{paise * R:,.0f}"


def comparison_table(results: list[RunResult], recoverable_paise: int) -> str:
    rows = []
    header = (
        f"{'policy':<22} {'engine':<9} {'net Rs':>10} {'direct Rs':>10} "
        f"{'gross Rs':>9} {'expo Rs':>8} {'rec%':>6} {'viol':>5} {'waste':>6}"
    )
    rows.append(header)
    rows.append("-" * len(header))

    for r in sorted(results, key=lambda x: -x.ledger.net_paise()):
        led = r.ledger
        rate = 100.0 * led.gross_recovered_paise / recoverable_paise if recoverable_paise else 0.0
        guard = "" if r.enforce_guardrails else " *"
        name = f"{r.policy_name}{guard}"
        engine = r.engine if not (r.uses_llm and r.engine == "stub") else "STUB!"
        rows.append(
            f"{name:<22} {engine:<9} {_rs(led.net_paise()):>10} "
            f"{_rs(led.net_direct_paise()):>10} {_rs(led.gross_recovered_paise):>9} "
            f"{_rs(led.compliance_exposure_paise):>8} "
            f"{rate:>5.1f}% {led.policy_violations:>5} {led.wasted_attempts:>6}"
        )

    rows.append("")
    rows.append("* = guardrails not enforced for this policy (by design; see baselines.py)")
    return "\n".join(rows)


def cost_breakdown(results: list[RunResult]) -> str:
    rows = []
    header = (
        f"{'policy':<22} {'MDR':>9} {'attempts':>9} {'messages':>9} "
        f"{'human':>8} {'annoy':>8} {'churn':>9}"
    )
    rows.append(header)
    rows.append("-" * len(header))
    for r in sorted(results, key=lambda x: -x.ledger.net_paise()):
        c = r.ledger
        rows.append(
            f"{r.policy_name:<22} {_rs(c.mdr_paise):>9} {_rs(c.attempt_cost_paise):>9} "
            f"{_rs(c.message_cost_paise):>9} {_rs(c.escalation_cost_paise):>8} "
            f"{_rs(c.annoyance_cost_paise):>8} {_rs(c.churn_cost_paise):>9}"
        )
    return "\n".join(rows)


def diagnosis_table(results: list[RunResult]) -> str:
    """Classification accuracy, split by how hard the case was.

    The held-out and ambiguous columns are the ones that matter. Overall
    accuracy is dominated by documented reasons, where a lookup table scores
    100% by construction and tells you nothing.
    """
    rows = []
    header = (
        f"{'policy':<22} {'overall':>9} {'n':>6} {'held-out':>10} {'n':>5} "
        f"{'ambiguous':>11} {'n':>5}"
    )
    rows.append(header)
    rows.append("-" * len(header))
    for r in sorted(results, key=lambda x: -x.class_accuracy()):
        if not r.classified:
            continue
        rows.append(
            f"{r.policy_name:<22} {r.class_accuracy():>8.1%} {r.classified:>6} "
            f"{r.heldout_accuracy():>9.1%} {r.classified_heldout:>5} "
            f"{r.ambiguous_accuracy():>10.1%} {r.classified_ambiguous:>5}"
        )
    return "\n".join(rows)


def headline(results: list[RunResult]) -> str:
    """The claim, with the honesty checks applied."""
    by_name = {r.policy_name: r for r in results}
    agent = by_name.get("agent")
    if agent is None:
        return "no agent policy in this run"

    competitors = [
        r for r in results if r.policy_name not in ("agent", "agent_no_guardrails")
    ]
    if not competitors:
        return "no baselines to compare against"

    best = max(competitors, key=lambda r: r.ledger.net_paise())
    a_net = agent.ledger.net_paise()
    b_net = best.ledger.net_paise()

    lines = []
    if agent.uses_llm and agent.engine == "stub":
        lines.append("!! STUB RUN -- no model was called. These numbers measure plumbing,")
        lines.append("!! not decision quality, and must not be reported as a result.")
        lines.append("")

    if b_net <= 0:
        lines.append(
            f"agent net Rs {_rs(a_net)} vs best baseline ({best.policy_name}) "
            f"Rs {_rs(b_net)} -- baseline is not net positive, so no lift is quoted."
        )
        return "\n".join(lines)

    lift = 100.0 * (a_net - b_net) / b_net
    lines.append(
        f"agent net Rs {_rs(a_net)}  vs  best baseline "
        f"({best.policy_name}) Rs {_rs(b_net)}"
    )
    lines.append(f"lift: {lift:+.1f}%   (net of every cost of chasing)")

    abl = by_name.get("agent_no_guardrails")
    if abl:
        d = a_net - abl.ledger.net_paise()
        verdict = "cost us" if d < 0 else "gained us"
        lines.append(
            f"guardrails {verdict} Rs {_rs(abs(d))} in net, and prevented "
            f"{abl.ledger.policy_violations} policy violations."
        )
    return "\n".join(lines)


def integrity_checks(results: list[RunResult]) -> list[str]:
    """Things that must be true. Any output here is a bug, not a trade-off."""
    problems = []
    for r in results:
        if r.enforce_guardrails and r.ledger.policy_violations:
            problems.append(
                f"{r.policy_name}: {r.ledger.policy_violations} violations despite "
                f"enforcing guardrails -- the policy layer has a hole"
            )
        if r.runaway_payments:
            problems.append(
                f"{r.policy_name}: {r.runaway_payments} payments hit the event cap "
                f"(policy may never stop)"
            )
        if r.decision_failures:
            problems.append(
                f"{r.policy_name}: {r.decision_failures} decisions failed and "
                f"defaulted to stop"
            )
        if r.uses_llm and r.engine == "stub":
            problems.append(f"{r.policy_name}: ran on the STUB, not a model")
    return problems


def full_report(results: list[RunResult], stats: dict, batch_size: int) -> str:
    recoverable = int(stats["recoverable_value_rupees"] * 100)
    out = []
    out.append("=" * 78)
    out.append(f"  RECOVERY COMPARISON  --  {batch_size} failed payments")
    out.append("=" * 78)
    out.append(f"  value at risk    Rs {stats['total_value_rupees']:>12,.0f}  (full batch)")
    out.append(f"  recoverable      Rs {stats['recoverable_value_rupees']:>12,.0f}")
    out.append("")
    out.append(comparison_table(results, recoverable))
    out.append("")
    out.append("  WHERE THE MONEY WENT")
    out.append(cost_breakdown(results))
    out.append("")
    out.append("  ROOT CAUSE DIAGNOSIS")
    out.append(diagnosis_table(results))
    out.append("")
    out.append("=" * 78)
    out.append(headline(results))
    out.append("=" * 78)

    problems = integrity_checks(results)
    if problems:
        out.append("")
        out.append("  INTEGRITY CHECKS")
        for p in problems:
            out.append(f"    ! {p}")

    usage = [(r.policy_name, r.llm_usage) for r in results if r.llm_usage]
    if usage:
        out.append("")
        out.append("  MODEL USAGE")
        for name, u in usage:
            out.append(
                f"    {name:<22} {u['calls']:>5} calls  "
                f"cache {u['cache_hit_rate']:.0%}  ${u['cost_usd']:.3f}"
            )
    return "\n".join(out)
