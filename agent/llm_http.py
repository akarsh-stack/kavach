"""Gemini and Groq backends. Both have usable free tiers and need no GPU.

Same `LLMClient` interface as every other backend, so nothing downstream
changes: prompt, schema, policy layer, audit trail and harness are all
engine-agnostic. Swapping providers is a flag.

Added because Ollama Cloud's free allowance runs out at roughly 1,500 decisions
and an evaluation needs more than that. Having three interchangeable free
providers means a quota wall is an inconvenience rather than a blocker -- and
the committed decision cache means whatever a run does complete is banked
permanently.

Provider notes, measured rather than assumed -- see agent/jsonio.py.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agent.jsonio import contract, extract_json, gemini_schema, repair_turn
from agent.llm import LLMClient, LLMQuotaExhausted, LLMUnavailable, _json_schema

T = TypeVar("T", bound=BaseModel)

# Sent on every request. urllib's default "Python-urllib/3.x" is refused by
# Groq's edge with Cloudflare error 1010 -- surfaced as a bare HTTP 403 that is
# indistinguishable from a bad key until you diff the headers.
_USER_AGENT = "kavach/1.0 (+https://github.com/akarsh-stack/kavach)"

# Phrases that mean "waiting WILL help", checked before the quota hints below.
#
# Groq's per-minute token limit is a textbook case: the body says "Rate limit
# reached ... tokens per minute (TPM) ... Please try again in 659ms" and then
# appends an upsell. That upsell contains "upgrade", which matched _QUOTA_HINTS
# and killed a live run dead over a limit that clears in under a second.
#
# A provider telling you when to come back is the strongest possible signal
# that the failure is temporary, so it outranks every other hint.
_TRANSIENT_HINTS = (
    "try again in",
    "please retry",
    "retry after",
    "per minute",
    "tokens per minute",
    "requests per minute",
    "rate limit reached",
    "temporarily",
)

# Quota-style refusals that waiting will never clear. Distinguishing these from
# transient back-pressure matters: a failed decision defaults to `stop`, so
# retrying through an exhausted quota silently produces results in which the
# agent did nothing.
_QUOTA_HINTS = (
    "quota",
    "usage limit",
    "billing",
    "insufficient_quota",
    "exceeded your current",
    "upgrade",
)


class _HttpClient(LLMClient):
    """Shared retry, repair and quota handling for JSON-over-HTTP providers."""

    def __init__(self, model: str, max_attempts: int = 4, timeout_s: float = 120.0) -> None:
        super().__init__()
        self.model = model
        self.max_attempts = max_attempts
        self.timeout_s = timeout_s
        self.backoff_base = 1.5

    # -- provider hooks ----------------------------------------------------

    def _endpoint(self) -> str: ...
    def _headers(self) -> dict[str, str]: ...
    def _body(self, system: str, user: str, schema: dict, extra: list[dict]) -> dict: ...
    def _text(self, data: dict) -> str: ...
    def _tokens(self, data: dict) -> tuple[int, int]:
        return (0, 0)

    # -- the loop ----------------------------------------------------------

    def complete(self, system: str, user: str, schema_cls: type[T]) -> T:
        schema = _json_schema(schema_cls)
        system = f"{system}\n\n{contract(schema_cls, schema)}"
        extra: list[dict] = []
        last: Exception | None = None

        for attempt in range(self.max_attempts):
            body = json.dumps(self._body(system, user, schema, extra)).encode()
            try:
                req = urllib.request.Request(
                    self._endpoint(), data=body, headers=self._headers()
                )
                # Identify ourselves. urllib defaults to "Python-urllib/3.x",
                # which Groq's edge rejects outright with Cloudflare error 1010
                # ("browser signature banned") -- an HTTP 403 that looks exactly
                # like an auth failure and sends you hunting for a bad key. A
                # plain product User-Agent is enough.
                req.add_header("User-Agent", _USER_AGENT)
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    data = json.loads(r.read())
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                low = detail.lower()
                self._record(retries=1)
                transient = any(h in low for h in _TRANSIENT_HINTS)
                if (
                    exc.code in (429, 402, 403)
                    and not transient
                    and any(h in low for h in _QUOTA_HINTS)
                ):
                    raise LLMQuotaExhausted(
                        f"{self.engine} quota exhausted -- waiting will not help. {detail}"
                    ) from exc
                if exc.code in (408, 429, 500, 502, 503, 504):
                    # Prefer the provider's own Retry-After over our backoff
                    # curve: it knows when the window rolls and we are guessing.
                    wait = self.backoff_base * (2**attempt)
                    hdr = exc.headers.get("retry-after") if exc.headers else None
                    if hdr:
                        try:
                            wait = max(wait, float(hdr))
                        except ValueError:
                            pass
                    time.sleep(min(wait, 30.0))
                    last = exc
                    continue
                raise LLMUnavailable(f"{self.engine} HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                self._record(retries=1)
                time.sleep(min(self.backoff_base * (2**attempt), 30.0))
                continue

            tin, tout = self._tokens(data)
            self._record(calls=1, input_tokens=tin, output_tokens=tout)

            text = self._text(data)
            if not text:
                last = LLMUnavailable("empty response")
                self._record(retries=1)
                continue

            try:
                return schema_cls.model_validate_json(extract_json(text))
            except ValidationError as exc:
                last = exc
                self._record(retries=1)
                extra = repair_turn(text, exc)

        self._record(failures=1)
        raise LLMUnavailable(f"{self.engine} failed after {self.max_attempts} attempts: {last}")


class GeminiClient(_HttpClient):
    """Google AI Studio. Generous free tier, no card required."""

    engine = "gemini"
    # Verified against a live key by calling each candidate, not by reading the
    # model list. `models?key=` happily advertises models it then refuses with
    # "no longer available to new users" -- 2.0-flash and 2.5-flash both 404ed
    # that way. Google retires these faster than a README gets updated, so if
    # this 404s, run scripts/smoke_llm.py or list the models and pick a live one.
    DEFAULT_MODEL = "gemini-3.5-flash"

    def __init__(self, model: str | None = None, api_key: str | None = None, **kw) -> None:
        super().__init__(model or self.DEFAULT_MODEL, **kw)
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self.api_key:
            raise LLMUnavailable(
                "GEMINI_API_KEY is not set. Get one free at https://aistudio.google.com/apikey"
            )
        self.usage.model = f"gemini/{self.model}"

    def _endpoint(self) -> str:
        return (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "x-goog-api-key": self.api_key}

    def _body(self, system, user, schema, extra) -> dict:
        contents = [{"role": "user", "parts": [{"text": user}]}]
        for m in extra:
            contents.append(
                {
                    "role": "model" if m["role"] == "assistant" else "user",
                    "parts": [{"text": m["content"]}],
                }
            )
        return {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": gemini_schema(schema),
            },
        }

    def _text(self, data) -> str:
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            return ""

    def _tokens(self, data):
        u = data.get("usageMetadata", {})
        return (u.get("promptTokenCount", 0) or 0, u.get("candidatesTokenCount", 0) or 0)


class GroqClient(_HttpClient):
    """Groq. OpenAI-compatible, very fast, free tier with per-minute limits."""

    engine = "groq"
    DEFAULT_MODEL = "openai/gpt-oss-120b"

    def __init__(self, model: str | None = None, api_key: str | None = None, **kw) -> None:
        super().__init__(model or self.DEFAULT_MODEL, **kw)
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        if not self.api_key:
            raise LLMUnavailable(
                "GROQ_API_KEY is not set. Get one free at https://console.groq.com/keys"
            )
        self.usage.model = f"groq/{self.model}"

    def _endpoint(self) -> str:
        return "https://api.groq.com/openai/v1/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _body(self, system, user, schema, extra) -> dict:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
                *extra,
            ],
            "temperature": 0,
            # json_object guarantees valid JSON, not the right shape -- the
            # prompt contract and repair turn cover the rest.
            "response_format": {"type": "json_object"},
        }

    def _text(self, data) -> str:
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            return ""

    def _tokens(self, data):
        u = data.get("usage", {})
        return (u.get("prompt_tokens", 0) or 0, u.get("completion_tokens", 0) or 0)
