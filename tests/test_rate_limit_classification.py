"""Transient back-pressure must not be mistaken for an exhausted quota.

This has now been got wrong three times, in both directions:

  1. an exhausted quota treated as transient -- the backoff slept through a
     hard wall and reported model failures for a limit that would never clear
  2. Groq's per-minute token ceiling treated as exhausted, because its 429
     body appends "Upgrade to Dev Tier" and that matched a quota hint. A
     660ms wait killed a live run three policies in
  3. Gemini's per-minute limit treated as exhausted, because the phrase
     matching looked for "per minute" with a space and Gemini writes
     "GenerateRequestsPerMinutePerProjectPerModel"

The cost is asymmetric and that is why this is tested. A failed decision
defaults to `stop`, so misreading a wall as transient silently produces a full
set of results in which the agent did nothing -- while misreading
back-pressure as a wall throws away a run that was seconds from continuing.

The bodies below are copied from real responses, not invented.
"""

from __future__ import annotations

from agent.llm_http import (
    _DAILY_HINTS,
    _QUOTA_HINTS,
    _TRANSIENT_HINTS,
    _normalise,
    _retry_delay,
)

GEMINI_PER_MINUTE = (
    '{"error":{"code":429,"message":"You exceeded your current quota, please check your '
    'plan and billing details","status":"RESOURCE_EXHAUSTED","details":[{"@type":"type.'
    'googleapis.com/google.rpc.QuotaFailure","violations":[{"quotaId":"GenerateRequests'
    'PerMinutePerProjectPerModel-FreeTier"}]},{"@type":"type.googleapis.com/google.rpc.'
    'RetryInfo","retryDelay":"25s"}]}}'
)

GROQ_TPM = (
    '{"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` in '
    "organization `org_x` service tier `on_demand` on tokens per minute (TPM): Limit "
    "8000, Used 7202, Requested 886. Please try again in 659.999999ms. Need more "
    'tokens? Upgrade to Dev Tier today"}}'
)

REAL_EXHAUSTION = (
    '{"error":{"message":"You have exceeded your current quota for the day. Please '
    'upgrade your plan or check your billing details."}}'
)


# Gemini answers a per-DAY exhaustion with a ~30s retryDelay. Taken at face
# value that is a lie: you may retry in 30s and be refused until midnight. The
# body is otherwise near-identical to the per-minute one, and the only thing
# separating them is the quota metric name.
GEMINI_PER_DAY = (
    '{"error":{"code":429,"message":"You exceeded your current quota, please check your '
    'plan and billing details. For more information on this error, head to: '
    'https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, '
    'head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: '
    'generate_requests_per_model_per_day","status":"RESOURCE_EXHAUSTED","details":'
    '[{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"31s"}]}}'
)


def _is_daily(detail: str) -> bool:
    return any(h in _normalise(detail) for h in _DAILY_HINTS)


def _is_transient(detail: str, headers=None) -> bool:
    if _is_daily(detail):
        return False
    return _retry_delay(detail, headers) is not None or any(
        h in _normalise(detail) for h in _TRANSIENT_HINTS
    )


def test_gemini_per_day_is_fatal_despite_a_stated_delay() -> None:
    """The retryDelay is present and small; the metric name is what matters."""
    assert _retry_delay(GEMINI_PER_DAY, None) == 31.0
    assert _is_daily(GEMINI_PER_DAY)
    assert not _is_transient(GEMINI_PER_DAY)


def test_per_day_and_per_minute_are_told_apart() -> None:
    assert _is_transient(GEMINI_PER_MINUTE)
    assert not _is_transient(GEMINI_PER_DAY)


def test_retry_info_survives_display_truncation() -> None:
    """Classification reads the whole body; only the message shown is clipped.

    retryDelay sits at roughly character 450 of Gemini's response, behind the
    prose and two documentation links. Parsing a 300-character copy -- which is
    what the error message displays -- loses the one field that decides this.
    """
    assert GEMINI_PER_DAY.find("retryDelay") > 300
    assert _retry_delay(GEMINI_PER_DAY[:300], None) is None
    assert _retry_delay(GEMINI_PER_DAY, None) == 31.0


def test_gemini_per_minute_is_transient() -> None:
    assert _retry_delay(GEMINI_PER_MINUTE, None) == 25.0
    assert _is_transient(GEMINI_PER_MINUTE)


def test_groq_tpm_is_transient_and_sub_second() -> None:
    delay = _retry_delay(GROQ_TPM, None)
    assert delay is not None and delay < 1.0, delay
    assert _is_transient(GROQ_TPM)


def test_genuine_exhaustion_is_not_transient() -> None:
    assert _retry_delay(REAL_EXHAUSTION, None) is None
    assert not _is_transient(REAL_EXHAUSTION)
    # ...and still trips the quota hints, so it aborts rather than looping.
    assert any(h in REAL_EXHAUSTION.lower() for h in _QUOTA_HINTS)


def test_retry_after_header_wins() -> None:
    assert _retry_delay("no delay in this body", {"retry-after": "30"}) == 30.0


def test_upsell_alone_does_not_make_it_fatal() -> None:
    """Both providers advertise their paid tier inside a temporary refusal."""
    for body in (GEMINI_PER_MINUTE, GROQ_TPM):
        assert any(h in body.lower() for h in _QUOTA_HINTS), "should look like a quota"
        assert _is_transient(body), "but the stated delay must override that"
