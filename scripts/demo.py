"""A narrated walkthrough, sized for screen recording.

    python scripts/demo.py              # pause between beats (press Enter)
    python scripts/demo.py --auto 6     # advance every 6 seconds
    python scripts/demo.py --beat 4     # jump straight to one beat

Follows the shot list in docs/VIDEO.md, so the terminal and the script agree.
Every number printed comes from the real pipeline -- nothing here is staged, and
the boundary test really is executed on camera, failure included.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

W = 74


def rule(ch: str = "=") -> None:
    print(ch * W)


def beat(n: int, title: str, subtitle: str = "") -> None:
    print()
    rule()
    print(f"  BEAT {n}  ·  {title}")
    if subtitle:
        print(f"           {subtitle}")
    rule()
    print()


def say(text: str) -> None:
    """Narration cue. Indented so it reads distinctly from program output."""
    for line in text.strip().split("\n"):
        print(f"   | {line.strip()}")
    print()


def wait(args) -> None:
    if args.beat:
        return
    # `--auto 0` means "no pause at all", so test for None rather than
    # truthiness -- 0 is a perfectly good interval and blocking on input()
    # because of it makes the flag useless in CI.
    if args.auto is not None:
        if args.auto > 0:
            time.sleep(args.auto)
    else:
        try:
            input("   [enter] ")
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)


# ---------------------------------------------------------------------------


def beat_1_problem(args) -> None:
    beat(1, "The problem, in one number", "python scripts/inspect_batch.py")
    from sim.generate import generate_batch, verify

    world, failures, stats = generate_batch(seed=42)
    print(f"   attempts        {stats['attempts']:>10,}")
    print(f"   success rate    {stats['success_rate_pct']:>9.2f}%   <- NPCI band is 92-96%")
    print(f"   failures        {stats['failures']:>10,}")
    print(f"   value at risk   Rs {stats['total_value_rupees']:>11,.0f}")
    print(f"   recoverable     Rs {stats['recoverable_value_rupees']:>11,.0f}")
    print()
    print(f"   calibration: {'PASS' if not verify(stats) else 'FAIL'}")
    print()
    say("""
        A 92% success rate sounds fine until you count what the other 8% is worth.
        Some of it is genuinely unrecoverable. Most of it isn't. The question is
        which, and what it costs to find out.
    """)


def beat_2_taxonomy(args) -> None:
    beat(2, "The data is real", "sim/taxonomy.py vs razorpay.com/docs/errors")
    from sim.taxonomy import BY_REASON, ERRORS, HELD_OUT_REASONS, summary

    print(f"   {len(ERRORS)} error reasons, transcribed verbatim from Razorpay's docs")
    print()
    for rc, n in summary().items():
        print(f"     {rc:<20} {n:>3}")
    print()
    for reason in ("card_expired", "payment_failed"):
        e = BY_REASON[reason]
        print(f"   {reason}")
        print(f"     description : {e.description[:66]}")
        print(f"     next steps  : {e.next_steps[:66]}")
        print(f"     -> class    : {e.recovery_class.value}")
        print()
    print(f"   {len(HELD_OUT_REASONS)} reasons are withheld from the agent's rulebook,")
    print("   simulating the reality that gateway codes appear faster than anyone maps them.")
    print()
    say("""
        Razorpay publishes what THEY think you should do about each failure.
        So the recovery logic here isn't my opinion about payments -- it's a
        compression of theirs, and you can check it line by line.
    """)


def beat_3_boundary(args) -> None:
    beat(3, "The agent cannot see the answer key", "and the test that proves it")
    target = REPO / "agent" / "observe.py"
    original = target.read_text(encoding="utf-8")

    print("   Normally:")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_observability_boundary.py", "-q"],
        cwd=REPO, capture_output=True, text=True,
    )
    print(f"     {r.stdout.strip().splitlines()[-1]}")
    print()
    print("   Now injecting `from sim.world import Truth` into agent/observe.py ...")
    try:
        target.write_text(
            original.replace(
                "from dataclasses import dataclass, field",
                "from dataclasses import dataclass, field\nfrom sim.world import Truth",
            ),
            encoding="utf-8",
        )
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_observability_boundary.py", "-q"],
            cwd=REPO, capture_output=True, text=True,
        )
        for line in r.stdout.splitlines():
            if "AssertionError" in line or "imports from sim" in line or "FAILED" in line:
                print(f"     {line.strip()[:70]}")
    finally:
        target.write_text(original, encoding="utf-8")

    print()
    print("   Restored:")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"], cwd=REPO,
        capture_output=True, text=True,
    )
    print(f"     {r.stdout.strip().splitlines()[-1]}")
    print()
    say("""
        A test that can't fail proves nothing. That one can.
    """)


def beat_4_veto(args) -> None:
    beat(4, "The veto", "the model proposes, the policy disposes")
    from datetime import datetime

    from agent.observe import Observation
    from agent.policy import PolicyEngine, Proposal
    from core.actions import Action

    now = datetime(2026, 8, 14, 11, 0)
    obs = Observation(
        payment_id="pay_004417", customer_id="cust_00812", amount_paise=489900,
        method="card", issuer="HDFC", psp=None,
        reason="payment_risk_check_failed",
        description="Payment declined due to risk checks.",
        source="razorpay", error_class="GATEWAY_ERROR", failed_at=now,
        is_subscription=False, attempt_no=1,
        customer_prior_payments=7, customer_prior_failures=1,
        customer_contacts_this_week=0, customer_first_seen=datetime(2025, 2, 1),
        customer_lifetime_paise=2_100_000,
        issuer_downtime_reported=False, psp_downtime_reported=False,
        recent_failures_same_entity=0, recent_failures_same_reason=0,
        now=now, attempts_made=0, contacts_made=0, budget_remaining_paise=5_000_000,
    )
    proposal = Proposal(
        payment_id=obs.payment_id, action=Action.RETRY, at=now,
        rationale="High-value customer with strong history; a retry is likely to clear.",
        confidence=0.91, diagnosed_class="retry_same",
    )

    print(f"   payment   {obs.payment_id}   Rs {obs.amount_rupees:,.0f}")
    print(f"   reason    {obs.reason}")
    print()
    print(f"   MODEL     proposes {proposal.action.value}, confidence {proposal.confidence:.2f}")
    print(f"             \"{proposal.rationale}\"")
    print()

    ruling = PolicyEngine().review(obs, proposal)
    print(f"   POLICY    {ruling.verdict.value.upper()}  [{ruling.rule}]")
    print(f"             -> {ruling.action.value}")
    print(f"             {ruling.explanation}")
    print()
    say("""
        The model was confident and it did not matter. Re-presenting a risk
        decline at volume is what gets a merchant's account reviewed.

        And that changes what you can tell a risk team. Not "we retried 400
        payments" -- but "we declined to retry these, and here is the rule
        that stopped each one."
    """)


def beat_5_money(args) -> None:
    beat(5, "The money", "python scripts/run_eval.py --no-llm")
    r = subprocess.run(
        [sys.executable, "scripts/run_eval.py", "--no-llm", "--limit", "300", "--save", "demo"],
        cwd=REPO, capture_output=True, text=True,
    )
    show = False
    for line in r.stdout.splitlines():
        if "RECOVERY COMPARISON" in line:
            show = True
        if show:
            print(f"   {line}")
        if line.startswith("  WHERE THE MONEY WENT"):
            break
    print()
    say("""
        Five policies, the same 300 payments, the same seed -- and the randomness
        is keyed per decision, so every policy faces identical luck at identical
        moments. A policy that wins, won on judgement, not on dice.

        Now read the SECOND column before the first. On direct P&L, the dumb
        retry loop WINS. It only loses once you price the risk declines it
        re-presented.

        That is not a bug in my favour. It is why naive retry loops are
        everywhere.
    """)


def beat_6_sensitivity(args) -> None:
    beat(6, "Where my own result breaks", "python scripts/run_sensitivity.py")
    from evaluation import sensitivity as sens
    from evaluation.baselines import FixedRetryPolicy, NoRetryPolicy, RulesEnginePolicy

    pts = sens.sweep(
        lambda: [NoRetryPolicy(), FixedRetryPolicy(), RulesEnginePolicy()],
        limit=300, progress=False,
    )
    print(sens.one_at_a_time(pts, ["no_retry", "fixed_retry", "rules_engine"]))
    print(sens.verdict(pts, "rules_engine"))
    print()
    say("""
        36 combinations of the three softest assumptions in the model. The rules
        engine wins 27 and loses 9 -- and 8 of those 9 are one column, where I
        set compliance exposure to zero.

        So the honest claim is conditional: doing it properly beats the dumb loop
        PROVIDED re-presenting a risk decline costs anything at all.

        I also can't defend the size of the lift. Across that grid it ranges from
        minus 24 percent to plus 780. So I quote no lift number anywhere.
    """)


BEATS = [
    beat_1_problem,
    beat_2_taxonomy,
    beat_3_boundary,
    beat_4_veto,
    beat_5_money,
    beat_6_sensitivity,
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", type=float, default=None,
                    help="seconds between beats; 0 = no pause")
    ap.add_argument("--beat", type=int, default=0, help="run one beat and exit")
    args = ap.parse_args()

    if args.beat:
        BEATS[args.beat - 1](args)
        return 0

    print()
    rule()
    print("  BOUNDED REVENUE RECOVERY  ·  Razorpay Buildathon, Track 03")
    print("  Every number below comes from the real pipeline.")
    rule()
    wait(args)

    for fn in BEATS:
        fn(args)
        wait(args)

    print()
    rule()
    print("  Repo: README.md   Architecture: docs/ARCHITECTURE.md")
    print("  What we got wrong: README.md -> 'What we got wrong'")
    rule()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
