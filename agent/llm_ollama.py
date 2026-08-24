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
import urllib.error
import urllib.request
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agent.llm import LLMClient, LLMUnavailable, _json_schema

T = TypeVar("T", bound=BaseModel)

DEFAULT_HOST = "http://localhost:11434"

DEFAULT_MODEL = "qwen2.5:7b"
"""Good instruction-following and reliable JSON at a size that fits on a
laptop (~4.7GB). `llama3.1:8b` and `mistral:7b` also work; `qwen2.5:14b` is
noticeably better if there is RAM for it."""

SUGGESTED_MODELS = ("qwen2.5:7b", "llama3.1:8b", "qwen2.5:14b", "mistral:7b")


class OllamaClient(LLMClient):
    engine = "ollama"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        max_attempts: int = 3,
        timeout_s: float = 120.0,
        temperature: float = 0.0,
        num_ctx: int = 8192,
    ) -> None:
        super().__init__()
        self.model = model
        self.host = host.rstrip("/")
        self.max_attempts = max_attempts
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.usage.model = f"ollama/{model}"
        self._check()

    # -- setup -------------------------------------------------------------

    def _check(self) -> None:
        """Fail loudly at construction rather than 300 calls into a run."""
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=5) as r:
                tags = json.loads(r.read())
        except urllib.error.URLError as exc:
            raise LLMUnavailable(
                f"no Ollama server at {self.host} ({exc.reason}). "
                f"Install from https://ollama.com/download, then `ollama serve`."
            ) from exc
        except Exception as exc:
            raise LLMUnavailable(f"could not reach Ollama at {self.host}: {exc}") from exc

        available = [m.get("name", "") for m in tags.get("models", [])]
        # Ollama reports "qwen2.5:7b"; accept a bare "qwen2.5" as a match too.
        if not any(a == self.model or a.split(":")[0] == self.model.split(":")[0]
                   for a in available):
            raise LLMUnavailable(
                f"model '{self.model}' not pulled. Run:  ollama pull {self.model}\n"
                f"    available: {', '.join(available) or '(none)'}"
            )

    # -- the call ----------------------------------------------------------

    def complete(self, system: str, user: str, schema_cls: type[T]) -> T:
        schema = _json_schema(schema_cls)
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
        body = json.dumps(payload).encode()
        last: Exception | None = None

        for _ in range(self.max_attempts):
            try:
                req = urllib.request.Request(
                    f"{self.host}/api/chat",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    data = json.loads(r.read())
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
                return schema_cls.model_validate_json(text)
            except ValidationError as exc:
                last = exc
                self._record(retries=1)

        self._record(failures=1)
        raise LLMUnavailable(f"ollama failed after {self.max_attempts} attempts: {last}")


def build_ollama(model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST) -> OllamaClient:
    return OllamaClient(model=model, host=host)
