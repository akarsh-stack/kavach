"""Generate a batch and print its calibration report.

    python scripts/inspect_batch.py [--seed 42] [--attempts 20000]

Run this first. If the calibration checks fail, nothing downstream is worth
measuring.
"""

from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from collections import Counter

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from sim.generate import generate_batch, verify  # noqa: E402
from sim.taxonomy import BY_REASON  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--attempts", type=int, default=20000)
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    world, failures, stats = generate_batch(
        seed=args.seed, n_attempts=args.attempts, days=args.days
    )

    print("=" * 66)
    print(f"  BATCH  seed={args.seed}  window={args.days}d")
    print("=" * 66)
    print(f"  attempts          {stats['attempts']:>10,}")
    print(f"  successes         {stats['successes']:>10,}")
    print(f"  failures          {stats['failures']:>10,}")
    print(f"  success rate      {stats['success_rate_pct']:>9.2f}%   (NPCI band: 92-96%)")
    print()
    print(f"  value at risk     Rs {stats['total_value_rupees']:>12,.0f}")
    print(f"  recoverable       Rs {stats['recoverable_value_rupees']:>12,.0f}")
    print(
        f"  unwinnable        Rs "
        f"{stats['total_value_rupees'] - stats['recoverable_value_rupees']:>12,.0f}"
        "   (risk blocks + our own bugs)"
    )
    print()

    print("  TRUE CAUSE MIX")
    for cause, n in sorted(stats["cause_mix"].items(), key=lambda x: -x[1]):
        bar = "#" * int(38 * n / stats["failures"])
        print(f"    {cause:<20} {n:>5}  {100 * n / stats['failures']:>5.1f}%  {bar}")
    print()

    print("  HARDEST SLICES")
    print(f"    ambiguous (payment_failed)   {stats['ambiguous_failures']:>5}")
    print(f"    outage-linked                {stats['outage_linked_failures']:>5}")
    held = sum(1 for e in failures if BY_REASON[e.reason].held_out)
    print(f"    held-out reasons             {held:>5}   (rules engine has no mapping)")
    print()

    d = stats["downtime"]
    print("  ISSUER DOWNTIME")
    print(f"    episodes                     {d['episodes']:>5}")
    print(f"    publicly reported            {d['reported_pct']:>5.0f}%   (rest are invisible)")
    print(f"    median duration              {d['median_minutes']:>5.0f} min")
    print(f"    longest                      {d['max_minutes']:>5.0f} min")
    print()

    top = Counter(e.reason for e in failures).most_common(8)
    print("  TOP REASONS")
    for reason, n in top:
        print(f"    {reason:<38} {n:>5}")
    print()

    problems = verify(stats)
    print("=" * 66)
    if problems:
        print("  CALIBRATION: FAIL")
        for p in problems:
            print(f"    ! {p}")
        return 1
    print("  CALIBRATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
