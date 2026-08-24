"""The model layer: structured JSON decisions, cost tracking, and a stub.

Two implementations behind one interface:

  * `AnthropicClient` -- Claude Opus 5, structured output via a JSON schema, with
    the stable part of the prompt cached. This is what produces the headline
    numbers.

  * `StubClient` -- a deterministic stand-in that makes no network calls, so the
    harness, the tests and the whole pipeline run without credentials.

**The stub is not a model and its results must never be reported as one.**
`evaluation/report.py` refuses to label a stub run as an LLM policy, and every
artefact it writes carries `engine: "stub"`. The point of the stub is to keep
the plumbing testable and to let the non-LLM baselines run on a laptop with no
API key -- not to stand in for the thing being measured.

## Why structured output rather than tool use

The decision is a single classification-plus-plan with a fixed shape. Tool use
would add a round trip and a failure mode (a model that narrates instead of
calling) for no benefit. `output_config.format` with a JSON schema guarantees
the response parses, so the loop has no text-wrangling in it at all.

## Why `effort: "low"`

A per-payment triage is exactly the kind of bounded, well-specified call the
low effort setting exists for, and this runs hundreds of times per evaluation.
Deliberately a cost decision, recorded here rather than buried: raising it is a
one-line change and `evaluation/report.py` prints the effort level alongside the
results, so any comparison states what it was run at.
"""

from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-5"

# Claude Opus 5 list pricing, USD per million tokens.
PRICE_INPUT_PER_MTOK = 5.00
PRICE_OUTPUT_PER_MTOK = 25.00
PRICE_CACHE_READ_PER_MTOK = 0.50
PRICE_CACHE_WRITE_PER_MTOK = 6.25


@dataclass
class Usage:
    """Running cost, so the report can state what the numbers cost to produce."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    failures: int = 0
    retries: int = 0

    def add(self, other: Usage) -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.failures += other.failures
        self.retries += other.retries

    def cost_usd(self) -> float:
        m = 1_000_000
        return (
            self.input_tokens * PRICE_INPUT_PER_MTOK / m
            + self.output_tokens * PRICE_OUTPUT_PER_MTOK / m
            + self.cache_read_tokens * PRICE_CACHE_READ_PER_MTOK / m
            + self.cache_write_tokens * PRICE_CACHE_WRITE_PER_MTOK / m
        )

    def cache_hit_rate(self) -> float:
        total = self.input_tokens + self.cache_read_tokens
        return self.cache_read_tokens / total if total else 0.0

    def summary(self) -> dict[str, object]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_hit_rate": round(self.cache_hit_rate(), 3),
            "cost_usd": round(self.cost_usd(), 4),
            "failures": self.failures,
            "retries": self.retries,
        }


class LLMUnavailable(RuntimeError):
    pass


class LLMClient(ABC):
    """Returns a validated instance of `schema_cls`, or raises."""

    engine: str = "abstract"

    def __init__(self) -> None:
        self.usage = Usage()
        self._lock = threading.Lock()

    @abstractmethod
    def complete(self, system: str, user: str, schema_cls: type[T]) -> T: ...

    def _record(self, **kw: int) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self.usage, k, getattr(self.usage, k) + v)


def _json_schema(schema_cls: type[BaseModel]) -> dict:
    """Pydantic schema, tightened for the API's structured-output format.

    `additionalProperties: false` matters: without it a model can bolt extra
    keys onto the object and still validate, which quietly defeats the point of
    constraining the output at all.
    """
    schema = schema_cls.model_json_schema()
    schema["additionalProperties"] = False

    def tighten(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node.setdefault("additionalProperties", False)
            for v in node.values():
                tighten(v)
        elif isinstance(node, list):
            for v in node:
                tighten(v)

    tighten(schema.get("$defs", {}))
    return schema


class AnthropicClient(LLMClient):
    engine = "anthropic"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: str = "low",
        max_tokens: int = 1500,
        max_attempts: int = 3,
        enable_refusal_fallback: bool = True,
    ) -> None:
        super().__init__()
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailable("pip install anthropic") from exc

        self._anthropic = anthropic
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.max_attempts = max_attempts
        # Claude Opus 5 can decline on safety grounds; a server-side fallback
        # keeps a batch from developing a hole in it. Vanishingly unlikely for
        # payment triage, but a run that silently drops events would corrupt
        # the comparison rather than fail loudly, which is worse.
        self.enable_refusal_fallback = enable_refusal_fallback
        self._fallback_supported = enable_refusal_fallback

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMUnavailable(
                "ANTHROPIC_API_KEY is not set. Use StubClient for a credential-free run."
            )
        self._client = anthropic.Anthropic()

    def complete(self, system: str, user: str, schema_cls: type[T]) -> T:
        schema = _json_schema(schema_cls)
        last: Exception | None = None

        for attempt in range(self.max_attempts):
            try:
                resp = self._call(system, user, schema)
            except self._anthropic.APIStatusError as exc:
                # A rejected beta flag should degrade, not kill the run.
                if self._fallback_supported and "beta" in str(exc).lower():
                    self._fallback_supported = False
                    continue
                if exc.status_code < 500 and exc.status_code != 429:
                    self._record(failures=1)
                    raise
                last = exc
                self._record(retries=1)
                continue
            except self._anthropic.APIConnectionError as exc:
                last = exc
                self._record(retries=1)
                continue

            u = resp.usage
            self._record(
                calls=1,
                input_tokens=getattr(u, "input_tokens", 0) or 0,
                output_tokens=getattr(u, "output_tokens", 0) or 0,
                cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            )

            if resp.stop_reason == "refusal":
                self._record(failures=1)
                raise LLMUnavailable(
                    f"model declined this request: {getattr(resp, 'stop_details', None)}"
                )

            text = next((b.text for b in resp.content if b.type == "text"), None)
            if text is None:
                last = LLMUnavailable("no text block in response")
                self._record(retries=1)
                continue

            try:
                return schema_cls.model_validate_json(text)
            except ValidationError as exc:
                # Schema-constrained output should make this unreachable. If it
                # fires, something is wrong with the schema rather than the
                # model, so surface it rather than silently retrying forever.
                last = exc
                self._record(retries=1)

        self._record(failures=1)
        raise LLMUnavailable(f"failed after {self.max_attempts} attempts: {last}")

    def _call(self, system: str, user: str, schema: dict):
        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            # The system prompt is large, identical on every call, and carries
            # the whole rulebook -- exactly the shape prompt caching exists for.
            # Volatile per-payment content goes in the user turn, after the
            # breakpoint, so the prefix stays byte-stable across the batch.
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
            output_config={
                "format": {"type": "json_schema", "schema": schema},
                "effort": self.effort,
            },
        )
        if self._fallback_supported:
            return self._client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **kwargs,
            )
        return self._client.messages.create(**kwargs)


class StubClient(LLMClient):
    """Deterministic stand-in. NOT a model. Never report its output as one.

    Exists so the harness, the baselines and the tests run without credentials.
    It applies a crude heuristic over the reason string and returns the same
    structure the real client would.

    Deliberately mediocre. It is not tuned to look good, because a strong stub
    would make the comparison against the rules engine meaningless and would
    tempt exactly the kind of quiet substitution this project is arguing
    against. `evaluation/report.py` refuses to print a stub run under an LLM
    policy label.
    """

    engine = "stub"

    def __init__(self, latency_s: float = 0.0) -> None:
        super().__init__()
        self.latency_s = latency_s

    def complete(self, system: str, user: str, schema_cls: type[T]) -> T:
        if self.latency_s:
            import time

            time.sleep(self.latency_s)
        self._record(calls=1)
        payload = self._heuristic(user)
        try:
            return schema_cls.model_validate(payload)
        except ValidationError:
            return schema_cls.model_validate(self._minimal(schema_cls))

    @staticmethod
    def _heuristic(user: str) -> dict:
        blob = user.lower()

        def has(*needles: str) -> bool:
            return any(n in blob for n in needles)

        if has("risk_check", "compliance", "tampered", "not_allowed", "not_eligible"):
            cls, action = "hard_stop", "escalate"
        elif has("invalid_order", "validation_failed", "order_amount", "not_enabled"):
            cls, action = "merchant_fix", "escalate"
        elif has("insufficient_funds", "limit_exceeded", "attempts_exceeded"):
            cls, action = "retry_later_funds", "retry"
        elif has("expired", "blocked", "declined", "invalid_vpa", "inactive"):
            cls, action = "switch_rail", "switch_rail"
        elif has("otp", "cancelled", "timed_out", "session", "cvv", "authentication"):
            cls, action = "nudge_customer", "nudge"
        else:
            cls, action = "retry_same", "retry"

        return {
            "recovery_class": cls,
            "confidence": 0.5,
            "action": action,
            "delay_hours": 24.0 if cls == "retry_later_funds" else 1.0,
            "channel": "sms" if action in ("nudge", "switch_rail") else None,
            "rationale": "stub heuristic; not a model decision",
        }

    @staticmethod
    def _minimal(schema_cls: type[BaseModel]) -> dict:
        return {
            "recovery_class": "retry_same",
            "confidence": 0.3,
            "action": "stop",
            "delay_hours": 0.0,
            "channel": None,
            "rationale": "stub fallback",
        }


def build_client(prefer_stub: bool = False, **kw: object) -> LLMClient:
    """Real client when credentials work, stub otherwise, and say which.

    Never silently downgrades: an unavailable API is reported to stdout and
    recorded on the returned client's `engine`, so a run can be traced back to
    what actually produced it.
    """
    if prefer_stub:
        return StubClient()
    try:
        return AnthropicClient(**kw)  # type: ignore[arg-type]
    except LLMUnavailable as exc:
        print(f"  [llm] falling back to StubClient: {exc}")
        return StubClient()
