# Where this is, and what to do next

Written to be picked up cold.

## Run it

```bash
pip install -r requirements.txt
python scripts/run_eval.py --engine ollama --limit 150 --no-ablation   # 0 live calls, all cached
python -m pytest tests/ -q                                             # 64 tests
cd web && npm run install:all && npm run dev                           # :5173
```

The decision cache is committed and *replayed* when no backend is reachable, so
that evaluation reproduces the published numbers **with no API key and no GPU**.
Verified on a fresh clone: 1,500 decisions replayed, agent Rs 39,420, 0 live
calls.

This was broken until late: without credentials the client fell back to the
stub, whose model name cannot match the recorded entries, so it silently
recomputed everything with a heuristic and printed Rs 35,919 as though nothing
were wrong. Caught only by cloning to /tmp and reading the engine column. If you
change the prompt, the cache misses and the run **aborts** rather than
publishing different numbers -- re-record with a live backend.

## The two surfaces

The project has one product and one body of evidence, and the dashboard is
split accordingly.

**Recovery console** (default tab) — the work queue. Revenue at risk, what the
agent diagnosed, the intervention it chose, what it recovered, and an
escalation queue of things needing a human. This is the brief's four clauses
made visible: *detects revenue at risk → determines the intervention → executes
a bounded workflow → compliant escalation, stopping rules, audit trail.*

**Evidence** (second tab) — the five-policy comparison, cost breakdown,
diagnosis accuracy and sensitivity sweep. Why you should believe the console.

That split was a late correction. The first build was an evaluation harness
with an agent inside it; the brief asks for an agent with evidence behind it.
Same data, correct framing.

## The numbers

150 failed payments, `gpt-oss:120b` via Ollama Cloud.

```
agent          39,420   0 violations   0 wasted attempts
rules_engine   35,970
fixed_retry    32,007  48 violations
no_retry            0
naive_llm     -39,640 607 violations
```

**+9.6%** over Razorpay's own published guidance, holding at **34 of 36**
sensitivity grid points. The two losses are both at `annoyance ×10`, where
messaging stops paying and a policy that never contacts anyone wins.

`naive_llm` — same model, no guardrails, no economics — is **net negative**.
That contrast is the strongest single result in the project.

## Model providers

Any one of these is enough. Copy `.env.example` to `.env` and fill in one.

| Provider | Free? | Notes |
|---|---|---|
| Gemini | yes, generous | `GEMINI_API_KEY` from aistudio.google.com/apikey |
| Groq | yes, per-minute limits | `GROQ_API_KEY` from console.groq.com/keys |
| Ollama Cloud | ~1,500 decisions per window | currently in use |
| Anthropic | paid | path built, never executed |

A quota wall is an inconvenience, not a blocker: whatever a run completes is
banked in the cache permanently, and `LLMQuotaExhausted` aborts loudly rather
than silently degrading every decision to `stop`.

## What is left

**Blocking:**

- [ ] **Record the video.** Script and demo harness are ready —
      `docs/VIDEO.md`, `make demo`. Beats 1–4 need no model and are final.
- [ ] **NPCI figures are second-hand.** `sim/issuers.py` per-bank decline rates
      come from secondary reporting. Replace with one named month's official
      file from NPCI's BD/TD dashboard and cite it. Doable offline, no
      dependency on anything else.

**Known limitations, documented and shippable:**

- Held-out n=3 and ambiguous n=7 are too thin to conclude from. A 300-payment
  run fixes it and the first 150 are already cached — it only pays for the new
  ones. Needs one quota window.
- The agent scored 14.3% on ambiguous `payment_failed`, same as the rules
  engine. The contextual inference the design predicted is **not yet
  demonstrated**.
- Only payment failures. The brief also mentions checkout abandonment and
  overdue receivables; abandonment is partly covered via the
  `nudge_customer` class, receivables not at all.

## Traps already hit — do not re-learn these

- **The console reads `reference.json`, never `latest.json`.** `latest` is
  scratch, gitignored, and written by any run -- including ones nobody started.
  It only appears on screen after *you* press Run. Earlier the console defaulted
  to `latest` and stray spawns silently changed what it displayed.
- **Starting a run is POST `/api/evaluate`; watching one is GET `/api/stream`.**
  Never merge them. EventSource speaks only GET and reconnects on every blip, so
  a GET with a spawn behind it re-runs the job. This bit twice: the first fix
  guarded against *concurrent* runs, which did not help, because a reconnect
  after completion simply started a fresh one. Five evaluations spawned that
  nobody asked for.
- **A `--no-llm` run will overwrite `reference.json`** if you pass
  `--save reference`. It happened. The cache made recovery free, but check what
  you are overwriting.
- **Heredoc patching mangles backslashes.** Two scripts shipped broken
  multi-line strings this way. Use the editor, not `python - <<EOF` string
  replacement, for anything containing `\\n`.
- **`tests/test_imports.py` exists because a dataclass field-ordering bug
  survived a 32-test run** — nothing imported `agent/audit.py`. Keep it.
