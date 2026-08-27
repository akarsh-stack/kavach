"""Verify the Anthropic path end to end. Run this the moment a real key exists.

    python scripts/smoke_llm.py

Checks, in order: credentials resolve, the structured-output schema is accepted,
the response validates against the Pydantic model, prompt caching actually
engages on the second call, and the reported cost is sane.

The cache check is the one worth watching. If `cache_read_tokens` is zero on the
second call, something in the system prompt is varying between requests and the
full batch will cost roughly ten times what it should.
"""

from __future__ import annotations

import pathlib
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agent.decide import SYSTEM_PROMPT, Decision, build_user_message  # noqa: E402
from agent.llm import AnthropicClient, LLMUnavailable  # noqa: E402
from agent.observe import Observation  # noqa: E402

NOW = datetime(2026, 8, 28, 14, 30)


def sample(reason: str, description: str, **kw) -> Observation:
    base = dict(
        payment_id="pay_smoke",
        customer_id="cust_smoke",
        amount_paise=249_900,
        method="card",
        issuer="HDFC",
        psp=None,
        reason=reason,
        description=description,
        source="issuer_bank",
        error_class="GATEWAY_ERROR",
        failed_at=NOW,
        is_subscription=False,
        attempt_no=1,
        customer_prior_payments=6,
        customer_prior_failures=1,
        customer_lifetime_paise=1_800_000,
        customer_first_seen=datetime(2025, 3, 1),
        customer_contacts_this_week=0,
        issuer_downtime_reported=False,
        psp_downtime_reported=False,
        recent_failures_same_entity=0,
        recent_failures_same_reason=0,
        now=NOW,
        attempts_made=0,
        contacts_made=0,
        budget_remaining_paise=5_000_000,
    )
    base.update(kw)
    return Observation(**base)


CASES = [
    (
        "documented reason",
        sample("card_expired", "The card has expired."),
        "expect switch_rail -- retrying a dead card is the classic money burner",
    ),
    (
        "risk decline",
        sample(
            "payment_risk_check_failed",
            "Payment declined due to risk checks.",
            source="razorpay",
        ),
        "expect hard_stop + escalate. If it proposes a retry, the policy layer catches it.",
    ),
    (
        "HELD OUT -- not in the rulebook",
        sample(
            "funds_blocked_by_mandate",
            "Funds are blocked by an existing mandate.",
            method="emandate",
            is_subscription=True,
        ),
        "expect retry_later_funds, reasoned from the description alone",
    ),
    (
        "AMBIGUOUS + outage signal",
        sample(
            "payment_failed",
            "Payment processing failed due to error at bank or wallet gateway. "
            "No specific error code received from gateway in this case.",
            method="upi",
            psp="PHONEPE",
            recent_failures_same_entity=9,
            recent_failures_same_reason=7,
        ),
        "9 failures at the same PSP in an hour with no downtime flag -> "
        "expect retry_same with a delay, inferred from the cluster",
    ),
]


def _bad_key_note() -> str:
    key = __import__("os").environ.get("ANTHROPIC_API_KEY", "")
    shown = f"{key[:7]}... ({len(key)} chars)" if key else "(unset)"
    return (
        "    FAIL: 401 invalid x-api-key\n\n"
        f"    ANTHROPIC_API_KEY is {shown}\n"
        "    Anthropic keys start with 'sk-ant-'. If this one does not, it\n"
        "    belongs to a different service. Everything downstream would run\n"
        "    on StubClient until a real key is set -- and stub output is NOT\n"
        "    a model result. See docs/OPEN_ISSUES.md."
    )


def main() -> int:
    import anthropic

    try:
        client = AnthropicClient()
    except LLMUnavailable as exc:
        print(f"FAIL: {exc}")
        print("\nAn Anthropic key starts with 'sk-ant-'. Set ANTHROPIC_API_KEY and re-run.")
        return 1

    print(f"model={client.model}  effort={client.effort}\n")

    failed = 0
    for label, obs, expectation in CASES:
        print(f"--- {label}")
        print(f"    {obs.reason}")
        try:
            d = client.complete(SYSTEM_PROMPT, build_user_message(obs), Decision)
        except anthropic.APIStatusError as exc:
            # Rarely reached: the client wraps almost everything below.
            print(f"    FAIL: HTTP {exc.status_code} — {str(exc)[:160]}\n")
            return 1
        except LLMUnavailable as exc:
            # The client converts every non-5xx status into LLMUnavailable
            # (agent/llm.py), so a 401 arrives here, not as an
            # anthropic.AuthenticationError. Reading the status back out of the
            # message is ugly but it is where the information actually is.
            if "401" in str(exc):
                print(_bad_key_note())
                return 1
            print(f"    FAIL: {exc}\n")
            failed += 1
            continue
        print(f"    -> {d.recovery_class} / {d.action} / +{d.delay_hours}h / {d.channel}")
        print(f"       conf {d.confidence:.2f}: {d.rationale}")
        print(f"       ({expectation})\n")

    u = client.usage
    print("=" * 60)
    print(f"calls          {u.calls}")
    print(f"input tokens   {u.input_tokens:,}")
    print(f"cache reads    {u.cache_read_tokens:,}")
    print(f"cache hit rate {u.cache_hit_rate():.1%}   <- should be high after call 1")
    print(f"output tokens  {u.output_tokens:,}")
    print(f"cost           ${u.cost_usd():.4f}")
    print(f"failures       {u.failures}   retries {u.retries}")

    # A smoke test that makes zero successful calls has verified nothing. It
    # used to print this summary full of zeroes and exit 0, which reads as a
    # pass. Every case failing is the loudest possible failure, not a pass.
    if u.calls == 0:
        print("\nFAIL: no call succeeded, so nothing was verified.")
        return 1
    if failed:
        print(f"\nFAIL: {failed} of {len(CASES)} cases did not complete.")
        return 1
    if u.calls > 1 and u.cache_read_tokens == 0:
        print("\nWARNING: no cache hits. Something in the system prompt varies")
        print("between calls; a full batch will cost ~10x what it should.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
