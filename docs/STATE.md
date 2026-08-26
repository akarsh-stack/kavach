# Where this is, and what to do next

Written to be picked up cold.

## Run it

```bash
pip install -r requirements.txt
python scripts/run_eval.py --engine ollama --limit 150 --no-ablation   # 0 live calls, all replayed
python scripts/live_probe.py --replay                                  # real Razorpay payload
python -m pytest tests/ -q                                             # 64 tests
cd web && npm run install:all && npm run dev                           # :5173
```

**No API key needed for any of it.** When no backend is reachable the client
*replays* the committed decision cache. Verified on a fresh clone: 1,655
decisions replayed, agent ₹40,849, 0 live calls.

This was broken until late. Without credentials the client fell back to the
stub, whose model name cannot match the recorded entries, so it silently
recomputed everything with a heuristic and printed ₹35,919 as though nothing
were wrong. Caught only by cloning to `/tmp` and reading the engine column.

**If you change the prompt, the cache misses and the run aborts** rather than
publishing different numbers. Re-record with a live backend.

## The two surfaces

**Recovery console** (default tab) — the work queue. Revenue at risk, what the
agent diagnosed, the intervention it chose, what it recovered, and an escalation
queue of things needing a human. The brief's four clauses made visible.

**Evidence** (second tab) — the five-policy comparison, cost breakdown,
diagnosis accuracy and sensitivity sweep. Why you should believe the console.

That split was a late correction. The first build was an evaluation harness with
an agent inside it; the brief asks for an agent with evidence behind it.

## The numbers

150 failed payments, `gpt-oss:120b` via Ollama Cloud.

```
agent          40,849   0 violations   0 wasted attempts
rules_engine   37,437
fixed_retry    33,605  48 violations
no_retry            0
naive_llm     -34,119 603 violations
```

**+9.1%** over Razorpay's own published guidance, holding at **33 of 36**
sensitivity grid points. All three losses are at `annoyance ×10`, where
messaging stops paying and a policy that never contacts anyone wins.

Two results matter more than the headline:

- `naive_llm` — same model, no guardrails, no economics — is **net negative**.
- The agent's net is **identical across the whole compliance-exposure axis**
  (₹40,849 at ×0, ×1 and ×5) because it commits zero violations. Every other
  policy swings with the price of a breach. That is what the guardrails bought.

## Live Razorpay proof

`python scripts/live_probe.py --replay`

```
payment       pay_TUORtBOxc2nlNA   (test mode)
error_reason  international_transaction_not_allowed
error_source  business

in our transcribed taxonomy : YES
agent diagnosis             : hard_stop, confidence 1.00
policy ruling               : ALLOW -> escalate
```

A real Razorpay error payload through the **same `Observation` dataclass** the
evaluation uses. Outside the evaluation path entirely, so a network failure
cannot break the reproducible numbers.

To capture a fresh one: `python scripts/live_probe.py` prints a checkout URL —
open it **in a real browser** (the in-app preview pane renders the Razorpay
iframe at 0×0), pay with any test card, choose **Failure** on the simulator.
If you complete it against a different order than the one being polled, capture
it anyway with `--payment pay_xxx`.

## Model providers

Any one is enough. Copy `.env.example` to `.env`.

| Provider | Free? | Notes |
|---|---|---|
| Gemini | yes, generous | `GEMINI_API_KEY` — aistudio.google.com/apikey |
| Groq | yes, per-minute limits | `GROQ_API_KEY` — console.groq.com/keys |
| Ollama Cloud | ~1,500 decisions per window | produced the committed run |
| Anthropic | paid | path built, never executed |

A quota wall is an inconvenience, not a blocker: whatever a run completes is
banked in the cache, and `LLMQuotaExhausted` aborts loudly rather than silently
degrading every decision to `stop`.

## What is left

**Blocking:**

- [ ] **Push to a public GitHub repo.** Form item 10 asks for the URL. 36
      commits, currently local only — no remote configured.
- [ ] **Record the video.** Script and harness ready — `docs/VIDEO.md`,
      `make demo`. Beats 1–4 need no model and are final.

**Known limitations, documented and shippable:**

- Held-out n=3 and ambiguous n=10 are too thin to conclude from. A 300-payment
  run fixes it and the first 150 are cached, so it only pays for the new ones.
- Ambiguous accuracy is **30.0% for both the agent and the rules engine**. The
  contextual inference the design predicted is *not demonstrated*.
- Scope: payment failures yes; checkout abandonment partly (12 reasons in
  `nudge_customer`); overdue receivables not at all.
- `base_recovery_prob` and the customer model are assumptions. Ordering is
  defensible, values are not.

## Traps already hit — do not re-learn these

- **The console reads `reference.json`, never `latest.json`.** `latest` is
  scratch, gitignored, written by any run including ones nobody started. Earlier
  the console defaulted to it and stray spawns changed what it displayed.
- **Starting a run is POST `/api/evaluate`; watching is GET `/api/stream`.**
  Never merge them. EventSource speaks only GET and reconnects on every blip, so
  a GET with a spawn behind it re-runs the job. This bit twice — the first fix
  guarded against *concurrent* runs, which did not help, because a reconnect
  after completion simply started a fresh one. Five evaluations spawned that
  nobody asked for.
- **`datetime.now()` in anything that becomes a prompt** makes every cache key
  unique. It silently turned `--replay` into a live call returning different
  answers between runs.
- **Anything committed from a real API may carry PII.** The captured Razorpay
  payload had the payer's email, phone and card identifiers. Redacted before the
  repo went public.
- **A `--no-llm` run overwrites `reference.json`** if you pass `--save
  reference`. Check what you are overwriting.
- **Heredoc patching mangles backslashes.** Several scripts shipped broken
  multi-line strings this way. Use the editor for anything containing escapes.
- **`tests/test_imports.py` exists** because a dataclass field-ordering bug
  survived a full test run — nothing imported `agent/audit.py`. Keep it.
