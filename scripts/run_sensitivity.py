"""Sweep the assumed costs and probabilities; report whether the ranking holds.

    python scripts/run_sensitivity.py                    # non-LLM policies, fast
    python scripts/run_sensitivity.py --engine ollama    # includes the agent

The non-LLM sweep answers the question that matters most for credibility --
whether a rules engine beats a naive retry loop for real, or only at our chosen
numbers -- and it runs in seconds with no model at all.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from evaluation import sensitivity as sens  # noqa: E402
from evaluation.baselines import (  # noqa: E402
    FixedRetryPolicy,
    NoRetryPolicy,
    RecoveryAgentPolicy,
    RulesEnginePolicy,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--engine", default="none", choices=["none", "ollama", "anthropic"])
    ap.add_argument("--model", default="")
    ap.add_argument("--subject", default="", help="policy to judge; default = best available")
    args = ap.parse_args()

    if args.engine == "none":
        subject = args.subject or "rules_engine"

        def make():
            return [NoRetryPolicy(), FixedRetryPolicy(), RulesEnginePolicy()]

    else:
        from agent.llm import build_client

        model = args.model or (
            "qwen2.5:7b" if args.engine == "ollama" else "claude-haiku-4-5"
        )
        client = build_client(engine=args.engine, model=model)
        if client.engine == "stub":
            print("\nRefusing to run a sensitivity sweep on the stub.")
            print("A stub sweep measures nothing and its output could be mistaken")
            print("for a real result. Start Ollama or set credentials, or drop")
            print("--engine to sweep the non-LLM policies.")
            return 1

        subject = args.subject or "agent"

        def make():
            return [
                NoRetryPolicy(),
                FixedRetryPolicy(),
                RulesEnginePolicy(),
                RecoveryAgentPolicy(client),
            ]

    grid = (
        len(sens.PROB_SCALES)
        * len(sens.ANNOYANCE_MULTIPLIERS)
        * len(sens.EXPOSURE_MULTIPLIERS)
    )
    print(f"sweeping {grid} grid points, subject = {subject}\n")

    t0 = time.time()
    points = sens.sweep(make, seed=args.seed, limit=args.limit)
    print(f"\n{grid} points in {time.time() - t0:.1f}s\n")
    print(sens.report(points, subject=subject))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
