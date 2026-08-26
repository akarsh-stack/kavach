"""Disk-backed decision cache. Makes model runs cheap, repeatable, and portable.

## Why this is not just an optimisation

Three problems solved by one mechanism:

1. **Cost.** A full evaluation is ~1,000 model calls, and re-running it after a
   one-line change costs that again. We burned an entire Ollama Cloud free tier
   allowance on two runs before this existed.

2. **Sensitivity analysis.** The 36-point sweep re-runs every policy 36 times.
   Without caching that is ~39,000 calls and is simply not affordable; with it,
   the model decisions are computed once and the sweep varies only the cost
   model around them. That is also *correct*: the sweep varies costs and
   probabilities, neither of which the agent can observe, so its decisions for a
   given observation genuinely should not change.

3. **Reproducibility by someone else.** This is the one that matters most for a
   public repo. The cache is committed, so a reviewer with no API key and no
   GPU can clone, run the evaluation, and get *our exact numbers* — including
   the model-driven policies. A result nobody else can reproduce is a claim, not
   a result.

## Why caching is sound here

Every backend runs at `temperature 0`. The model is a deterministic function of
its inputs, so memoising it changes nothing about the outcome — it is the same
call, not an approximation of one.

The key is a hash of `(model, schema, system prompt, user message)`. Any change
to the prompt, the taxonomy rendered inside it, or the observation produces a
different key and a real call. There is no way to silently serve a stale
decision for a changed input.

Entries record which model produced them, so a cache file can never launder one
model's decisions into a run labelled with another's.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import threading
from typing import TypeVar

from pydantic import BaseModel

from agent.llm import LLMClient, LLMUnavailable, Usage

T = TypeVar("T", bound=BaseModel)

DEFAULT_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "llm_cache.json"


def _key(model: str, schema_name: str, system: str, user: str) -> str:
    h = hashlib.blake2b(digest_size=16)
    for part in (model, schema_name, system, user):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class CachedClient(LLMClient):
    """Wraps any LLMClient. Serves repeats from disk, calls through on a miss."""

    def __init__(
        self,
        inner: LLMClient,
        path: pathlib.Path | str = DEFAULT_PATH,
        read_only: bool = False,
        flush_every: int = 25,
    ) -> None:
        super().__init__()
        self.inner = inner
        # Report the wrapped engine, not "cache". A run served entirely from
        # disk is still a run of whatever model produced those entries, and the
        # report must say which -- especially so a cached *stub* run can never
        # be mistaken for a model run.
        self.engine = inner.engine
        self.path = pathlib.Path(path)
        self.read_only = read_only
        self.flush_every = flush_every

        self._store: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._dirty = 0
        self.hits = 0
        self.misses = 0
        self._load()

    @property
    def model(self) -> str:
        return getattr(self.inner, "model", "unknown")

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._store = raw.get("entries", {})
        except (json.JSONDecodeError, OSError):
            # A corrupt cache must never take down a run. Worst case we pay for
            # the calls again.
            self._store = {}

    def save(self) -> None:
        """Atomic write, serialised across threads.

        Both halves matter on Windows. The harness decides concurrently, so
        several threads hit the flush threshold at once; with a shared temp
        filename they raced and `replace()` died with WinError 32 (file in use
        by another process). A unique temp name fixes the collision and the lock
        keeps the snapshot of `_store` consistent with what gets written.
        """
        if self.read_only:
            return
        with self._write_lock:
            with self._lock:
                snapshot = dict(self._store)
                self._dirty = 0
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "note": "Deterministic model decisions (temperature 0). "
                "Committed so results reproduce without credentials.",
                "entries": snapshot,
            }
            tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                tmp.write_text(
                    json.dumps(payload, indent=0, sort_keys=True), encoding="utf-8"
                )
                os.replace(tmp, self.path)
            except OSError:
                # Losing a flush is survivable -- the entries are still in
                # memory and the next flush writes them. Failing the run over a
                # cache write would not be.
                tmp.unlink(missing_ok=True)

    def complete(self, system: str, user: str, schema_cls: type[T]) -> T:
        k = _key(self.model, schema_cls.__name__, system, user)

        with self._lock:
            hit = self._store.get(k)
        if hit is not None:
            self.hits += 1
            try:
                return schema_cls.model_validate(hit["value"])
            except Exception:
                # A stale entry that no longer fits the schema (the model
                # changed shape between runs) falls through to a live call
                # rather than failing the decision.
                pass

        self.misses += 1
        result = self.inner.complete(system, user, schema_cls)

        with self._lock:
            self._store[k] = {"model": self.model, "value": result.model_dump(mode="json")}
            self._dirty += 1
            should_flush = self._dirty >= self.flush_every
        if should_flush:
            self.save()
        return result

    # -- reporting ---------------------------------------------------------

    @property
    def usage(self) -> Usage:  # type: ignore[override]
        return self.inner.usage

    @usage.setter
    def usage(self, value: Usage) -> None:
        # LLMClient.__init__ assigns this before `inner` exists.
        self.__dict__.setdefault("_own_usage", value)

    def stats(self) -> dict[str, object]:
        total = self.hits + self.misses
        return {
            "entries": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
            "live_calls": self.inner.usage.calls,
        }


class ReplayMiss(LLMUnavailable):
    """A replay run asked for a decision the cache does not hold.

    Fatal by design. A partial replay silently produces different numbers from
    the ones published, which is the precise failure this whole mechanism
    exists to prevent. A miss means the prompt, the taxonomy inside it, or the
    batch has changed since the cache was recorded -- in which case the honest
    move is to re-record, not to paper over the gap.
    """


class ReplayClient(LLMClient):
    """Serves recorded decisions and nothing else. No network, no invention.

    This is what makes the reproducibility claim true rather than aspirational.
    Without it, a clone with no credentials falls back to `StubClient`, whose
    model name differs -- so it cannot hit the recorded entries at all, quietly
    recomputes every decision with a heuristic, and reports numbers that are not
    the published ones.

    We shipped exactly that bug and only caught it by cloning the repo and
    reading the engine column.
    """

    engine = "replay"

    def __init__(self, path: pathlib.Path | str | None = None, model: str | None = None):
        super().__init__()
        # Resolved at call time, not bound as a default argument: a default
        # binds at def time, which made the path unpatchable in tests and the
        # class impossible to point at a second cache.
        self.path = pathlib.Path(path if path is not None else DEFAULT_PATH)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self._store = raw.get("entries", {})

        models = {}
        for v in self._store.values():
            models[v["model"]] = models.get(v["model"], 0) + 1
        real = {m: n for m, n in models.items() if not m.startswith("stub")}
        if model is None:
            if not real:
                raise LLMUnavailable("cache holds no real model decisions to replay")
            model = max(real, key=lambda m: real[m])
        self.model = model
        self.usage.model = model
        self.available = models.get(model, 0)
        self.hits = 0

    def complete(self, system: str, user: str, schema_cls: type[T]) -> T:
        k = _key(self.model, schema_cls.__name__, system, user)
        hit = self._store.get(k)
        if hit is None:
            raise ReplayMiss(
                f"no recorded decision for this input under '{self.model}'. The prompt "
                f"or batch has changed since the cache was recorded; re-record with a "
                f"live backend rather than replaying a stale cache."
            )
        self.hits += 1
        return schema_cls.model_validate(hit["value"])

    def stats(self) -> dict[str, object]:
        return {
            "entries": self.available,
            "hits": self.hits,
            "misses": 0,
            "hit_rate": 1.0,
            "live_calls": 0,
        }
