"""Capture one REAL Razorpay failure and run the agent on it.

    python scripts/live_probe.py            # create order, wait for a failure
    python scripts/live_probe.py --replay   # re-run from the captured payload

## What this proves

The evaluation runs on a simulator, and it has to -- Razorpay test mode cannot
produce sixty-six failure reasons with realistic clustering. But that leaves a
fair objection: *has any of this touched real Razorpay?*

This closes it. One real order, one real failed payment, the real error payload,
fed through the same `Observation` the agent consumes in the evaluation. If the
schema were wrong, or the reason strings were invented, this would not run.

The captured payload is written to `data/live/` and committed, so the proof
reproduces without anyone repeating the checkout -- and `--replay` runs the
whole thing offline.

## Why a human clicks once

Server-to-server payment creation is disabled on a fresh test account (403), so
a real failure has to come through Checkout, which runs in a cross-origin
iframe. One click, once. After that the payload is on disk forever.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import os
from datetime import datetime

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent.decide import Decision, build_user_message  # noqa: E402
from agent.knowledge import documented_class  # noqa: E402
from agent.policy import PolicyEngine, Proposal  # noqa: E402
from core.actions import Action  # noqa: E402
from integrations.razorpay_live import (  # noqa: E402
    RazorpayLive,
    RazorpayUnavailable,
    to_observation,
)

CAPTURE = REPO / "data" / "live" / "failure.json"
W = 74


def rule(ch: str = "=") -> None:
    print(ch * W)


def show(f, obs) -> None:
    print()
    rule()
    print("  REAL RAZORPAY FAILURE  (test mode)")
    rule()
    print(f"  payment      {f.payment_id}")
    print(f"  order        {f.order_id}")
    print(f"  amount       Rs {f.amount_paise / 100:,.2f}")
    print(f"  method       {f.method}")
    print(f"  status       {f.status}")
    print()
    print(f"  error_code        {f.error_code}")
    print(f"  error_reason      {f.error_reason}")
    print(f"  error_source      {f.error_source}")
    print(f"  error_step        {f.error_step}")
    print(f"  error_description {f.error_description[:60]}")
    print()

    known = f.in_our_taxonomy
    mapped = documented_class(f.error_reason)
    print(f"  in our transcribed taxonomy : {'YES' if known else 'NO'}")
    print(f"  mapped by the agent rulebook: {mapped or 'no entry (held out / unmapped)'}")
    if not known:
        print()
        print("  ^ a live reason we do not carry. Our taxonomy is incomplete, and")
        print("    finding that from the API beats finding it from a judge.")


def decide(obs, engine: str) -> None:
    from agent.llm import build_client

    print()
    rule("-")
    print("  THE AGENT, ON A REAL PAYLOAD")
    rule("-")

    client = build_client(engine=engine)
    from agent.decide import SYSTEM_PROMPT

    try:
        d: Decision = client.complete(SYSTEM_PROMPT, build_user_message(obs), Decision)
    except Exception as exc:
        print(f"  model unavailable: {str(exc)[:150]}")
        print("  (the payload capture above is the part that matters)")
        return

    print(f"  engine       {client.engine} / {getattr(client, 'model', '?')}")
    print(f"  diagnosis    {d.recovery_class}  (confidence {d.confidence:.2f})")
    print(f"  proposes     {d.action}  +{d.delay_hours}h" + (f" via {d.channel}" if d.channel else ""))
    print(f"  rationale    {d.rationale[:180]}")

    ruling = PolicyEngine().review(
        obs,
        Proposal(
            payment_id=obs.payment_id,
            action=Action(d.action),
            at=obs.now,
            channel=None,
            rationale=d.rationale,
            confidence=d.confidence,
            diagnosed_class=d.recovery_class,
        ),
    )
    print()
    print(f"  POLICY       {ruling.verdict.value.upper()}"
          + (f"  [{ruling.rule}]" if ruling.rule else ""))
    print(f"  final action {ruling.action.value}")
    if ruling.explanation:
        print(f"               {ruling.explanation}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true", help="use the captured payload")
    ap.add_argument("--amount", type=int, default=49900, help="paise")
    ap.add_argument("--engine", default="ollama")
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--payment", default="", help="capture an existing failed payment by id")
    args = ap.parse_args()

    if args.payment:
        # Capture a failure that already happened. Needed because the checkout
        # is driven by a human in a separate browser -- if they complete it on a
        # different order than the one being polled, the payment still exists
        # and is still exactly as good a proof.
        rz = RazorpayLive()
        raw = rz._call("GET", f"/payments/{args.payment}")
        if raw.get("status") != "failed":
            print(f"{args.payment} has status '{raw.get('status')}', not 'failed'.")
            return 1
        f = RazorpayLive.to_failure(raw)
        CAPTURE.parent.mkdir(parents=True, exist_ok=True)
        CAPTURE.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        print(f"captured {args.payment} -> {CAPTURE.relative_to(REPO)}")
    elif args.replay:
        if not CAPTURE.exists():
            print(f"no capture at {CAPTURE}. Run without --replay first.")
            return 1
        raw = json.loads(CAPTURE.read_text(encoding="utf-8"))
        f = RazorpayLive.to_failure(raw)
        print(f"replaying captured payload from {CAPTURE.relative_to(REPO)}")
    else:
        try:
            rz = RazorpayLive()
        except RazorpayUnavailable as exc:
            print(f"FAIL: {exc}")
            return 1

        order = rz.create_order(args.amount, f"live-probe-{int(datetime.now().timestamp())}")
        url = (
            f"http://localhost:5173/checkout.html?order={order['id']}"
            f"&key={os.environ['RAZORPAY_KEY_ID']}&amount={order['amount']}"
        )
        rule()
        print("  OPEN THIS, THEN CHOOSE **FAILURE** ON THE SIMULATOR")
        rule()
        print(f"  {url}")
        print()
        print("  Any test card works -- the simulator screen appears after you")
        print("  submit it, with Success / Failure buttons. Pick Failure.")
        print()
        print(f"  waiting up to {args.timeout:.0f}s ...")

        try:
            f = rz.wait_for_failure(order["id"], timeout_s=args.timeout)
        except RazorpayUnavailable as exc:
            print(f"\n  {exc}")
            return 1

        CAPTURE.parent.mkdir(parents=True, exist_ok=True)
        CAPTURE.write_text(json.dumps(f.raw, indent=2), encoding="utf-8")
        print(f"\n  captured -> {CAPTURE.relative_to(REPO)}")

    obs = to_observation(f)
    show(f, obs)
    decide(obs, args.engine)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
