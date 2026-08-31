# Kavach — where this is, and what to do next

Written to be picked up cold.

## Run it

```bash
pip install -r requirements.txt
python scripts/run_eval.py --engine ollama --limit 150 --no-ablation   # 0 live calls, all replayed
python scripts/run_eval.py --engine replay --limit 150                 # + the guardrail ablation
python scripts/live_probe.py --replay                                  # real Razorpay payload
python -m pytest tests/ -q                                             # 64 tests
cd web && npm run install:all && npm run dev                           # :5173
```

**No API key needed for any of it.** When no backend is reachable the client
*replays* the committed decision cache — 1,988 recorded decisions from
`gpt-oss:120b`, temperature 0. Verified on a fresh clone with no `.env`:
0 live calls, agent ₹40,849, and the 36-point sensitivity sweep regenerates
byte-identical apart from its timestamp.

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

**Say the unflattering half too.** The ablation — same agent, policy layer off —
nets ₹40,880 with 1 violation. So on this batch the guardrails **cost ₹31 and
prevented exactly one thing**, and vetoed nothing at all; all 28 interventions
were quiet-hours deferrals. The argument for them is the exposure axis above and
`naive_llm`, not this run's P&L. Do not let anyone hear it the other way round.

## Live Razorpay proof

`python scripts/live_probe.py --replay`

```
payment       pay_TUORtBOxc2nlNA   (test mode)
error_reason  international_transaction_not_allowed
error_source  business

in our transcribed taxonomy : YES
agent diagnosis             : hard_stop, confidence 0.98
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
| Gemini | yes, generous per day | `GEMINI_API_KEY` — aistudio.google.com/apikey. Default `gemini-3.5-flash`; Google retires these fast, and the model *listing* advertises ones the API then refuses with "no longer available to new users". Verify by calling, not by listing. |
| Groq | yes, but 8,000 tokens/min | `GROQ_API_KEY` — console.groq.com/keys. ~5 decisions/min with a 1,575-token system prompt, so a 150-payment run is ~3.4 hours. Fine for recording batches, useless for a full evaluation. Needs a real User-Agent or Cloudflare answers 403. |
| Ollama Cloud | ~1,500 decisions per window | produced the committed run |
| Anthropic | paid | path built, never executed |

A quota wall is an inconvenience, not a blocker: whatever a run completes is
banked in the cache, and `LLMQuotaExhausted` aborts loudly rather than silently
degrading every decision to `stop`.

## What is left

**Blocking:**

- [x] ~~**Push to a public GitHub repo.**~~ Done —
      **https://github.com/akarsh-stack/kavach**, public. Verified by cloning
      from GitHub with every credential stripped: 0 live calls, agent ₹40,849,
      64 tests.
- [ ] **Deploy the dashboard.** The repo is Vercel-ready (`vercel.json`, static
      build stages the artefacts). A judge clicks a link; a judge does not clone.
- [ ] **Record the video.** Script and harness ready — `docs/VIDEO.md`,
      `python scripts/demo.py`. No beat needs a model or a key:
      beats 5 and 6 replay the committed cache.

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
- **The API port is 5185, and something else may already have it.** It was 5174
  until an unrelated service on the dev machine took that port: Express never
  bound, `concurrently` kept the UI alive, and the dashboard silently fell into
  its backend-less mode and hid the run controls. It looked exactly like a
  deleted feature. `app.listen` now reports EADDRINUSE and exits; the Vite proxy
  follows `API_PORT` so there is only one copy of the number.
- **Provider model names rot, and the model LISTING lies.** Google's
  `models?key=` advertises models the API then refuses with "no longer available
  to new users" — that killed `gemini-2.0-flash` and `gemini-2.5-flash` in turn.
  Verify a default by calling it, never by listing.
- **urllib's default User-Agent is banned by Groq's edge** (Cloudflare 1010),
  surfacing as an HTTP 403 indistinguishable from a bad key.
- **"Upgrade" appears in transient rate-limit messages.** Groq's per-minute
  ceiling says "try again in 660ms" and then advertises its paid tier; matching
  the upsell classified a sub-second wait as a permanently exhausted quota and
  killed a live run. Transient hints are now checked *before* quota hints.
- **`make clean` used to delete the published evidence.** `rm -rf
  data/runs/*.json` takes out `reference.json` and `sensitivity.json`, both of
  which are committed and both of which the dashboard reads by default. Only
  `latest.json`, `demo.json` and `sensitivity_baselines.json` are scratch.
- **`run_sensitivity.py` picks its save name from `--engine`.** The
  credential-free sweep does not contain the agent at all, so letting it write
  to `sensitivity.json` replaced the headline 33/36 grid with a baselines-only
  one. `make sens`, and therefore `make all`, used to do exactly that.
- **Vite reports a dead API proxy as HTTP 500, not 502.** Any "is the backend
  up?" check keyed on 502 silently never fires. Distinguish by body shape: every
  error this API raises is JSON with an `error` key; a proxy failure is not.
- **`requestAnimationFrame` timestamps can predate a `performance.now()` taken
  in the same frame.** Clamp animation progress at *both* ends — an upper-only
  clamp let eased values go negative and rendered negative rupees for a frame.
- **A `.metric` is the only child of its own wrapper**, so `:first-child`
  matches every one of them. Style the grid children, not the cells.
- **Stale HMR errors survive a reload.** Vite can leave `Failed to reload` and
  `X is not defined` in the console after an edit sequence, long after the code
  is correct and the production build is clean. Open a **fresh tab** — it gets
  its own console buffer — before believing any of it.
- **Numbers typed into narration go stale silently.** `scripts/demo.py` printed
  one result and then read out a different one. Derive every figure in the demo
  from the run that just printed; nothing in CI can catch a stale sentence.
