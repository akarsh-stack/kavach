"""The decision cache is now load-bearing, so its guarantees need proving.

Two claims rest on it:

  * **Reproducibility.** The committed cache lets a reviewer with no API key
    reproduce our model-driven numbers exactly. That only holds if a cache hit
    is genuinely the same call -- if a changed prompt could silently serve a
    stale decision, every number in the repo would be suspect.

  * **Attribution.** A cached *stub* decision must never be able to present
    itself as a model decision, and one model's decisions must never be served
    under another's name.

These tests exist because both failures would be silent. A cache that serves
the wrong answer does not raise; it just quietly makes the results a lie.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from agent.cache import CachedClient, _key
from agent.llm import LLMClient


class Answer(BaseModel):
    label: str
    score: float


class CountingClient(LLMClient):
    """Records every call that reaches it, so hits can be distinguished from misses."""

    engine = "fake"
    model = "fake-model-v1"

    def __init__(self, label: str = "alpha") -> None:
        super().__init__()
        self.label = label
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user, schema_cls):
        self.calls.append((system, user))
        return schema_cls.model_validate({"label": self.label, "score": 0.5})


@pytest.fixture
def cache_path(tmp_path):
    return tmp_path / "cache.json"


def test_identical_input_does_not_reach_the_model(cache_path):
    inner = CountingClient()
    c = CachedClient(inner, path=cache_path)

    first = c.complete("SYS", "USER", Answer)
    second = c.complete("SYS", "USER", Answer)

    assert len(inner.calls) == 1, "second identical call should not reach the model"
    assert first.model_dump() == second.model_dump()
    assert c.hits == 1 and c.misses == 1


@pytest.mark.parametrize(
    "system,user",
    [
        ("SYS CHANGED", "USER"),
        ("SYS", "USER CHANGED"),
        ("SYS ", "USER"),  # even a trailing space is a different prompt
    ],
)
def test_any_prompt_change_is_a_real_call(cache_path, system, user):
    """The safety property. A stale decision for a changed input would make
    every result in the repo untrustworthy, so nothing may be served loosely."""
    inner = CountingClient()
    c = CachedClient(inner, path=cache_path)

    c.complete("SYS", "USER", Answer)
    c.complete(system, user, Answer)

    assert len(inner.calls) == 2


def test_one_model_cannot_be_served_under_anothers_name(cache_path):
    """Cross-model laundering is the failure that would most damage the claim."""
    a = CountingClient(label="from-model-a")
    a.model = "model-a"
    ca = CachedClient(a, path=cache_path)
    ca.complete("SYS", "USER", Answer)
    ca.save()

    b = CountingClient(label="from-model-b")
    b.model = "model-b"
    cb = CachedClient(b, path=cache_path)
    got = cb.complete("SYS", "USER", Answer)

    assert len(b.calls) == 1, "model-b must not read model-a's cached decision"
    assert got.label == "from-model-b"


def test_entries_record_which_model_produced_them(cache_path):
    inner = CountingClient()
    c = CachedClient(inner, path=cache_path)
    c.complete("SYS", "USER", Answer)
    c.save()

    entries = json.loads(cache_path.read_text(encoding="utf-8"))["entries"]
    assert all(e["model"] == "fake-model-v1" for e in entries.values())


def test_cache_reports_the_wrapped_engine_not_itself(cache_path):
    """A run served entirely from disk is still a run of whatever produced it.

    If this reported "cache", the report's stub-detection would break and a
    stub run could print under a model policy label.
    """
    inner = CountingClient()
    c = CachedClient(inner, path=cache_path)
    assert c.engine == "fake"
    assert c.model == "fake-model-v1"


def test_stub_entries_are_attributable(cache_path):
    from agent.llm import StubClient

    c = CachedClient(StubClient(), path=cache_path)
    assert c.engine == "stub"
    assert c.model == "stub-heuristic-v1"


def test_survives_a_corrupt_cache_file(cache_path):
    """Worst case is paying for the calls again, never failing the run."""
    cache_path.write_text("{ not json at all", encoding="utf-8")

    inner = CountingClient()
    c = CachedClient(inner, path=cache_path)
    got = c.complete("SYS", "USER", Answer)

    assert got.label == "alpha"
    assert len(inner.calls) == 1


def test_persists_across_client_instances(cache_path):
    """The reproducibility claim: a fresh process reads the committed file."""
    first = CountingClient()
    c1 = CachedClient(first, path=cache_path)
    c1.complete("SYS", "USER", Answer)
    c1.save()

    second = CountingClient(label="should-not-be-used")
    c2 = CachedClient(second, path=cache_path)
    got = c2.complete("SYS", "USER", Answer)

    assert len(second.calls) == 0, "should have been served from disk"
    assert got.label == "alpha"


def test_concurrent_writes_do_not_corrupt_the_file(cache_path):
    """The harness decides concurrently; flushes raced on Windows and died."""
    from concurrent.futures import ThreadPoolExecutor

    inner = CountingClient()
    c = CachedClient(inner, path=cache_path, flush_every=2)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: c.complete("SYS", f"USER {i}", Answer), range(60)))
    c.save()

    entries = json.loads(cache_path.read_text(encoding="utf-8"))["entries"]
    assert len(entries) == 60
    assert len(inner.calls) == 60


def test_key_is_stable_across_processes():
    """blake2b, not the salted builtin hash -- otherwise a committed cache would
    be useless to anyone else, which is the entire point of committing it."""
    k1 = _key("m", "Answer", "sys", "user")
    k2 = _key("m", "Answer", "sys", "user")
    assert k1 == k2
    assert k1 != _key("m", "Answer", "sys", "user2")
    assert len(k1) == 32
