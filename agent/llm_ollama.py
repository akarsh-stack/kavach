"""Local model backend via Ollama. Free, offline, no credentials.

Implements the same `LLMClient` interface as the Anthropic backend, so nothing
else in the project changes: the prompt, the schema, the policy layer, the audit
trail and the harness are all engine-agnostic by construction. Swapping backends
is a flag.

## Structured output

Ollama accepts a JSON schema in the `format` field (v0.5+) and constrains
decoding to match it, which is the same guarantee `output_config.format` gives
us on the Anthropic side. That matters more here than there: a 7B model asked
politely for JSON will sometimes produce prose, and the whole loop depends on
the response parsing. We still validate and retry, because constrained decoding
does not guarantee *semantic* validity -- a model can emit a well-formed object
with a nonsense field.

## What to expect

A 7B local model is meaningfully weaker than Claude at exactly the parts of this
problem that matter most: generalising to an unmapped reason, and inferring a
cause from a failure cluster when the reason string says nothing. Those are the
two slices `evaluation/report.py` breaks out separately, so the weakness will be
visible in the results rather than hidden in an average.

That is a fine outcome to report. "A local 7B model recovers X% and here is
precisely where it falls down against a rules engine" is a real finding. What
would not be fine is presenting it as though a frontier model produced it, so
`Usage.model` records exactly which model ran and the report prints it.

## Concurrency

Ollama serialises requests unless `OLLAMA_NUM_PARALLEL` is set, so a large wave
buys nothing and just queues. Default the wave low and let the user raise it if
they have configured parallelism and have the VRAM for it.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agent.jsonio import contract as _contract
from agent.jsonio import extract_json as _extract_json
from agent.llm import LLMClient, LLMQuotaExhausted, LLMUnavailable, _json_schema

T = TypeVar("T", bound=BaseModel)

LOCAL_HOST = "http://localhost:11434"
CLOUD_HOST = "https://ollama.com"

DEFAULT_MODEL = "qwen2.5:7b"
"""Local default. Fits on a laptop (~4.7GB) with decent JSON adherence."""

CLOUD_MODEL = "gpt-oss:120b"
"""Cloud default.

Chosen over the larger options on the roster for throughput, not capability:
this makes several hundred calls per evaluation, and the 400B-plus models are
slow enough per request to turn a five-minute run into an hour. `qwen3.5:397b`
is the upgrade if a single headline run is worth the wait.

At 120B this is roughly two orders of magnitude larger than the local 7B
fallback, which matters most on exactly the two slices we score separately:
generalising to an unmapped reason, and inferring a cause from a failure
cluster when the reason string says nothing.
"""

SUGGESTED_LOCAL = ("qwen2.5:7b", "llama3.1:8b", "qwen2.5:14b", "mistral:7b")
SUGGESTED_CLOUD = ("gpt-oss:120b", "qwen3.5:397b", "deepseek-v4-flash:preview", "glm-5.2")


class OllamaClient(LLMClient):
    engine = "ollama"

    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        api_key: str | None = None,
        max_attempts: int = 5,
        timeout_s: float = 180.0,
        backoff_base: float = 1.5,
        temperature: float = 0.0,
        num_ctx: int = 8192,
    ) -> None:
        super().__init__()
        # An API key means Ollama Cloud unless a host is named explicitly. The
        # same /api/chat contract serves both, so everything downstream is
        # identical -- only the base URL and one header change.
        self.api_key = api_key if api_key is not None else os.environ.get("OLLAMA_API_KEY", "")
        self.cloud = bool(self.api_key) and (host is None or "ollama.com" in (host or ""))
        self.host = (host or (CLOUD_HOST if self.cloud else LOCAL_HOST)).rstrip("/")
        self.model = model or (CLOUD_MODEL if self.cloud else DEFAULT_MODEL)

        self.max_attempts = max_attempts
        self.timeout_s = timeout_s
        self.backoff_base = backoff_base
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.usage.model = f"ollama/{self.model}"
        self._check()

    # -- setup -------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _check(self) -> None:
        """Fail loudly at construction rather than 300 calls into a run."""
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", headers=self._headers())
            with urllib.request.urlopen(req, timeout=20) as r:
                tags = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise LLMUnavailable(
                    f"Ollama rejected the API key ({exc.code}). Check OLLAMA_API_KEY."
                ) from exc
            raise LLMUnavailable(f"Ollama at {self.host} returned {exc.code}") from exc
        except urllib.error.URLError as exc:
            where = "Ollama Cloud" if self.cloud else f"local Ollama at {self.host}"
            hint = (
                "check your network and OLLAMA_API_KEY"
                if self.cloud
                else "install from https://ollama.com/download, then `ollama serve`"
            )
            raise LLMUnavailable(f"cannot reach {where} ({exc.reason}). {hint}.") from exc
        except Exception as exc:
            raise LLMUnavailable(f"could not reach Ollama at {self.host}: {exc}") from exc

        available = [m.get("name", "") for m in tags.get("models", [])]
        # Ollama reports "qwen2.5:7b"; accept a bare "qwen2.5" as a match too.
        if not any(
            a == self.model or a.split(":")[0] == self.model.split(":")[0] for a in available
        ):
            pull = (
                f"pick one of the cloud models above"
                if self.cloud
                else f"run:  ollama pull {self.model}"
            )
            raise LLMUnavailable(
                f"model '{self.model}' unavailable; {pull}.\n"
                f"    available: {', '.join(sorted(available)[:12]) or '(none)'}"
            )

    # -- the call ----------------------------------------------------------

    def complete(self, system: str, user: str, schema_cls: type[T]) -> T:
        schema = _json_schema(schema_cls)

        # Ollama Cloud accepts the `format` schema and then ignores it. Probed
        # directly: asked for our Decision schema it returned well-formed JSON
        # with entirely invented keys -- `classification`, `resolution_plan`,
        # `confidence: "high"` as a string. Local Ollama constrains decoding
        # properly; the cloud proxy does not.
        #
        # So the contract goes in the prompt instead, and every response is
        # validated with a repair turn on failure. Sent in both modes: it is
        # harmless where decoding is already constrained, and one code path is
        # easier to trust than two.
        system = f"{system}\n\n{_contract(schema_cls, schema)}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Constrained decoding against our schema. Without this a 7B model
            # will periodically answer in prose and break the loop.
            "format": schema,
            "stream": False,
            "options": {
                # Deterministic, so a rerun of the same batch reproduces. The
                # whole evaluation rests on policies being comparable across
                # runs, and a sampling temperature would quietly break that.
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
            },
            # Hold the model in memory between calls; reloading per request
            # would dominate runtime over a few hundred decisions.
            "keep_alive": "10m",
        }
        last: Exception | None = None

        for attempt in range(self.max_attempts):
            body = json.dumps(payload).encode()
            try:
                req = urllib.request.Request(
                    f"{self.host}/api/chat", data=body, headers=self._headers()
                )
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    data = json.loads(r.read())
            except urllib.error.HTTPError as exc:
                # Rate limits and 5xx are transient and must be waited out.
                # Retrying instantly -- which is what a bare `continue` does --
                # burns all three attempts inside a few milliseconds and reports
                # a model failure for what was actually back-pressure. That bug
                # cost us 35 dropped decisions in a 100-payment run, and a
                # dropped decision defaults to `stop`, so it silently
                # handicapped the agent it was measuring.
                last = exc
                self._record(retries=1)
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass

                # A 429 means two completely different things, and treating
                # them the same wasted an entire free-tier allowance here.
                #
                #   - transient back-pressure: wait and it clears
                #   - an exhausted usage quota: waiting never clears it
                #
                # Ollama Cloud reports the second as "session usage limit" in
                # the body with no rate-limit headers at all. Retrying that with
                # backoff just sleeps through the batch and then reports a model
                # failure -- and a failed decision defaults to `stop`, so it
                # silently handicaps the policy being measured. Fail fast and
                # say so instead.
                if exc.code == 429 and ("usage limit" in detail or "upgrade" in detail):
                    raise LLMQuotaExhausted(
                        f"Ollama Cloud quota exhausted, not rate-limited -- waiting will "
                        f"not help. {detail}"
                    ) from exc

                if exc.code in (408, 429, 500, 502, 503, 504):
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    delay = (
                        float(retry_after)
                        if retry_after and str(retry_after).isdigit()
                        else self.backoff_base * (2**attempt)
                    )
                    time.sleep(min(delay, 30.0))
                    continue
                raise LLMUnavailable(
                    f"ollama HTTP {exc.code}: {exc.reason} {detail}"
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                self._record(retries=1)
                time.sleep(min(self.backoff_base * (2**attempt), 30.0))
                continue
            except Exception as exc:
                last = exc
                self._record(retries=1)
                continue

            # Ollama reports prompt/completion token counts under these keys.
            self._record(
                calls=1,
                input_tokens=int(data.get("prompt_eval_count", 0) or 0),
                output_tokens=int(data.get("eval_count", 0) or 0),
            )

            text = (data.get("message") or {}).get("content", "")
            if not text:
                last = LLMUnavailable("empty response")
                self._record(retries=1)
                continue

            try:
                return schema_cls.model_validate_json(_extract_json(text))
            except ValidationError as exc:
                # Repair turn: hand the model its own output and the precise
                # validation errors. Far more reliable than resending the same
                # prompt and hoping -- the model can see exactly which key it
                # invented or which type it got wrong.
                last = exc
                self._record(retries=1)
                payload["messages"] = payload["messages"][:2] + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            "That did not match the required shape:\n"
                            f"{_errors(exc)}\n\n"
                            "Return ONLY the corrected JSON object with exactly the "
                            "required keys. No prose, no extra keys."
                        ),
                    },
                ]

        self._record(failures=1)
        raise LLMUnavailable(f"ollama failed after {self.max_attempts} attempts: {last}")


def build_ollama(model: str | None = None, host: str | None = None) -> OllamaClient:
    """Cloud when OLLAMA_API_KEY is set, local otherwise. Both, same interface."""
    return OllamaClient(model=model, host=host)
