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

from agent.llm_http import _QUOTA_HINTS, _TRANSIENT_HINTS, _normalise, _retry_delay

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


def _is_transient(detail: str, headers=None) -> bool:
    return _retry_delay(detail, headers) is not None or any(
        h in _normalise(detail) for h in _TRANSIENT_HINTS
    )


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
