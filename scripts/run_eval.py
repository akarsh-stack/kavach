"""Run every policy over one batch and print the comparison.

    python scripts/run_eval.py                    # all policies (stub if no key)
    python scripts/run_eval.py --no-llm           # the three credential-free policies
    python scripts/run_eval.py --limit 300        # smaller slice
    python scripts/run_eval.py --audit agent      # dump one policy's audit trail

The batch is a contiguous slice of the failure stream, not a random sample, so
the outage clusters and month-end funds spikes stay intact -- those correlations
are the signal a good agent exploits, and sampling would destroy them.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agent.llm import build_client  # noqa: E402
from evaluation import report as rep  # noqa: E402
from evaluation.baselines import build_all, build_non_llm  # noqa: E402
from evaluation.harness import run_policy  # noqa: E402
from sim.generate import generate_batch, verify  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=300, help="failures in the eval batch")
    ap.add_argument("--attempts", type=int, default=20000)
    ap.add_argument("--no-llm", action="store_true", help="skip policies needing a model")
    ap.add_argument("--stub", action="store_true", help="force the stub even if a key exists")
    ap.add_argument("--engine", default="ollama",
                    choices=["ollama", "anthropic", "stub"])
    ap.add_argument("--model", default="",
                    help="default: qwen2.5:7b for ollama, claude-haiku-4-5 for anthropic")
    ap.add_argument("--effort", default="low", help="anthropic only")
    ap.add_argument("--wave", type=int, default=24, help="concurrent decisions per wave")
    ap.add_argument("--audit", type=str, default="", help="dump this policy's audit trail")
    ap.add_argument("--audit-limit", type=int, default=12)
    ap.add_argument("--audit-compact", action="store_true")
    ap.add_argument("--audit-blocked", action="store_true",
                    help="only decisions the policy layer overruled")
    ap.add_argument("--budget", type=int, default=0, help="batch budget in rupees, 0 = none")
    ap.add_argument("--save", default="latest", help="artifact name under data/runs/")
    args = ap.parse_args()

    print("generating batch...")
    world, failures, stats = generate_batch(
        seed=args.seed, n_attempts=args.attempts
    )
    problems = verify(stats)
    if problems:
        print("CALIBRATION FAILED -- nothing downstream is worth measuring:")
        for p in problems:
            print(f"  ! {p}")
        return 1
    print(
        f"  {stats['failures']:,} failures, "
        f"{stats['success_rate_pct']:.2f}% success rate, "
        f"using first {args.limit}"
    )

    if args.no_llm:
        policies = build_non_llm()
        wave = 1
    else:
        if args.engine == "ollama":
            from agent.llm_ollama import DEFAULT_MODEL as OLLAMA_DEFAULT

            model = args.model or OLLAMA_DEFAULT
            print(f"  engine: ollama/{model}  (free, local)")
            client = build_client(engine="ollama", prefer_stub=args.stub, model=model)
            # Ollama serialises unless OLLAMA_NUM_PARALLEL is set, so a big
            # wave just queues. Raise --wave if parallelism is configured.
            wave = min(args.wave, 4)
        else:
            from agent.llm import estimate_cost_usd

            model = args.model or "claude-haiku-4-5"
            est = estimate_cost_usd(args.limit, model=model)
            print(f"  engine: {model}   estimated cost ${est:.2f}")
            client = build_client(
                engine="anthropic", prefer_stub=args.stub, model=model, effort=args.effort
            )
            wave = args.wave
        policies = build_all(client)

    batch = failures[: args.limit]
    budget = args.budget * 100 if args.budget else None

    results = []
    for policy in policies:
        t0 = time.time()
        print(f"  running {policy.name:<22}", end="", flush=True)
        r = run_policy(
            policy,
            world,
            failures,
            stats["customer_history"],
            limit=args.limit,
            batch_budget_paise=budget,
            wave=wave if policy.uses_llm else 1,
        )
        results.append(r)
        print(
            f" net Rs {r.ledger.net_paise() / 100:>10,.0f}  "
            f"({r.decisions} decisions, {time.time() - t0:.1f}s)"
        )

    print()
    # Report against the slice, not the full month, or the recovery rate
    # denominator would be five times too large.
    slice_stats = dict(stats)
    slice_stats["total_value_rupees"] = sum(e.amount_paise for e in batch) / 100.0
    from sim.taxonomy import RecoveryClass

    slice_stats["recoverable_value_rupees"] = (
        sum(
            e.amount_paise
            for e in batch
            if world.truth_of(e.payment_id).true_class
            not in (RecoveryClass.HARD_STOP, RecoveryClass.MERCHANT_FIX)
        )
        / 100.0
    )

    print(rep.full_report(results, slice_stats, len(batch)))

    if args.save:
        from evaluation.artifacts import save_run

        path = save_run(results, slice_stats, len(batch), args.seed, name=args.save)
        print()
        print(f"  saved -> {path}")

    if args.audit:
        match = next((r for r in results if r.policy_name == args.audit), None)
        if match is None:
            print(f"\nno policy named '{args.audit}'")
        else:
            print(f"\n{'=' * 78}\n  AUDIT TRAIL -- {args.audit}\n{'=' * 78}")
            print(
                match.audit.render(
                    limit=args.audit_limit,
                    only_overruled=args.audit_blocked,
                    compact=args.audit_compact,
                )
            )
            print(f"\n  ...{len(match.audit.entries)} decisions total")
            print(f"\n  summary: {match.audit.summary()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
