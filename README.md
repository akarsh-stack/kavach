# Kavach · कवच

**Bounded revenue recovery** — an LLM agent for failed payments, held inside a
deterministic policy layer.

*Kavach* is Sanskrit for armour. The policy layer is the armour: the model
proposes, and eight ordered rules allow, defer or veto every proposal before it
touches a customer or a card network.

Built for the **Razorpay Buildathon** — Track 03, AI Revenue Recovery.

An agent that works a queue of failed Razorpay payments: diagnoses each root
cause, chooses a bounded recovery action, escalates what needs a human, and
stops when stopping is the right answer.

Then it proves the whole thing against four competing policies — **net of the
cost of trying**.

---

## The brief, clause by clause

> *"Build an agent that detects revenue at risk, determines the right
> intervention, and executes a bounded recovery workflow."*
>
> *"Don't just identify the problem. Show measured money recovered across a
> batch, with compliant escalation, stopping rules, and an audit trail."*

| The bar asks for | Where you see it |
|---|---|
| detects revenue at risk | Recovery console — ₹1,61,600 unresolved, ranked by what wants attention |
| determines the right intervention | per-payment diagnosis + the model's own rationale |
| executes a bounded recovery workflow | the step timeline on every payment |
| compliant escalation | **18** payments routed to a human, each with a stated cause |
| stopping rules | 8 ordered rules in [`agent/policy.py`](agent/policy.py) that **veto, defer or substitute** the model |
| an audit trail | [`agent/audit.py`](agent/audit.py) — records what was **prevented**, not just what ran |
| measured money recovered | 5 policies, same batch, same seed, same luck |

---

## Two surfaces

**Recovery console** — the product. A work queue: what's at risk, what the agent
diagnosed, what it's doing, what it recovered, what needs you. Expand a payment
to see the bounded workflow it executed, and every action the policy layer
allowed, **deferred** or vetoed along the way.

**Evidence** — the five-policy comparison, cost breakdown, diagnosis accuracy and
sensitivity sweep. *Why you should believe the console.*

```bash
cd web && npm run install:all && npm run dev     # :5173
```

---

## Quickstart

```bash
pip install -r requirements.txt
python scripts/inspect_batch.py                              # generate + verify calibration
python scripts/run_eval.py --engine ollama --limit 150 --no-ablation
python scripts/run_sensitivity.py --engine ollama --subject agent
python scripts/live_probe.py --replay                        # real Razorpay payload
python -m pytest tests/ -q                                   # 64 tests
```

**No API key needed.** When no backend is reachable the client *replays* the
committed decision cache:

```
[llm] Ollama unavailable: model 'qwen2.5:7b' unavailable
      replaying 1688 recorded decisions from gpt-oss:120b
running agent      net Rs 40,849   (0 live calls, disk cache 100%)
running naive_llm  net Rs -34,119  (0 live calls, disk cache 100%)
```

To run live instead, copy `.env.example` to `.env` and fill in **any one** of
`GEMINI_API_KEY`, `GROQ_API_KEY`, `OLLAMA_API_KEY` or `ANTHROPIC_API_KEY`.

---

## The result

150 failed payments, ₹2,05,462 at risk, ₹1,76,982 theoretically recoverable.
Decisions by `gpt-oss:120b` via Ollama Cloud.

```
policy                 engine     net Rs   direct Rs   gross Rs   expo Rs   viol   wasted
agent                  ollama     40,849      40,849     43,862         0      0        0
rules_engine           rules      37,437      37,437     40,193         0      0        0
fixed_retry *          none       33,605      37,655     38,627     4,050     48       48
no_retry               none            0           0          0         0      0        0
naive_llm *            ollama    -34,119     -25,719     46,738     8,400    603       56

* runs without guardrails, by design
```

**The agent beats the rules engine — Razorpay's own published guidance,
competently implemented — by 9.1% net, with zero policy violations and zero
wasted attempts.**

Three results matter more than that one.

### 1. The obvious approach is worse than doing nothing

`naive_llm` is an LLM, the error string, and "recover the revenue" — the typical
hackathon submission. **Same model as our agent.** It recovered ₹46,738 gross,
*more than anyone else*.

Then it destroyed **₹19,066 in goodwill and ₹51,300 in churn**, committed **603
policy violations**, and hit the event cap on most of the batch because it never
stops.

Net: **−₹34,119.** The difference between that and ₹40,849 is entirely the
economics, the stopping rules and the guardrails — not the model.

### 2. The guardrails made an entire axis of risk inapplicable

Not the argument we expected, and the honest version is unflattering.

Run the ablation — the same agent with the policy layer switched off:

```
agent                  40,849   0 violations
agent_no_guardrails    40,880   1 violation
```

**The guardrails cost ₹31 and prevented exactly one violation on this batch.**
They vetoed nothing at all; every intervention was one of 28 quiet-hours
deferrals. Measured on this run alone, they lose money.

Reproduce it with `python scripts/run_eval.py --engine replay --limit 150`
(without `--no-ablation`) — the decisions are cached, so it costs nothing.

So why keep them? Look at the compliance-exposure axis of the sensitivity sweep:

```
compliance     x0.0     agent 40,849    fixed_retry 37,655
exposure       x1.0     agent 40,849    fixed_retry 33,605
               x5.0     agent 40,849    fixed_retry 17,405
```

**The agent's net does not move.** It commits zero violations, so exposure is a
cost it never incurs. Every other policy's number swings with the price of a
compliance breach; ours has none to price.

The guardrails didn't improve the average. They **bounded the worst case** —
and `naive_llm`, the same model without them, is what that worst case looks like.

### 3. Where the model actually earns its place

```
                  overall    n     held-out   n    ambiguous   n
rules_engine       95.2%    147       0.0%    0       30.0%   10
agent              94.7%    150      66.7%    3       30.0%   10
```

On **held-out** reasons — absent from the rulebook entirely — the agent scores
66.7% where the rules engine scores nothing, because it has no mapping and
structurally cannot. **But n = 3.**

On **ambiguous** `payment_failed`, both manage 30.0%. The contextual inference we
predicted the model would show is **not demonstrated** — it ties the lookup
table. A larger batch is the next thing this project needs.

### Does the lift survive our assumptions?

`python scripts/run_sensitivity.py --engine ollama --subject agent` sweeps 36
combinations of the three softest numbers in the model.

**The agent wins 33 of 36.** All three losses are at `annoyance ×10`, where
over-contacting costs ten times our estimate — at that price messaging stops
paying and `fixed_retry`, which never contacts anyone, pulls ahead of every
policy that does.

```
everywhere except annoyance x10 : 27 / 27
at annoyance x10                :  6 /  9
```

**Magnitude is not defensible, only direction.** Across the grid the lift ranges
−18.8% to +11.2%, so we quote no single lift percentage anywhere.

Full grid: [`docs/CALIBRATION.md` §5.4.1](docs/CALIBRATION.md).

---

## Why you can believe the numbers

Every simulated benchmark faces the same objection: *you wrote the simulator and
the agent, so of course the agent wins*. Usually that objection is correct.

### 1. The failure taxonomy is real, not invented

All **66 error reasons** in [`sim/taxonomy.py`](sim/taxonomy.py) are transcribed
verbatim from
[razorpay.com/docs/errors/payments/list](https://razorpay.com/docs/errors/payments/list/)
— the `reason` string, Razorpay's description, and Razorpay's own **"Next
Steps"**. That last column is a published recovery policy written by the payments
company we are building for, so our recovery classes compress *their* stated
opinion, not ours. A reviewer can check it line by line.

### 2. The agent provably cannot see the answer key

Hidden state lives in a private dict on `World`. Nothing the agent can reach
holds a reference to it, and `tests/test_observability_boundary.py` walks the AST
of every file under `agent/` and fails the build on any import from `sim/`, any
attribute read of a private name, or a `getattr` used to evade both.

We checked the test can fail — injecting `from sim.world import Truth` into
`agent/observe.py`:

```
E  AssertionError: The agent must not import the simulator.
E      agent/observe.py:38 imports from sim.world
FAILED tests/test_observability_boundary.py::test_agent_never_imports_sim
```

### 3. Every policy meets identical luck

Random draws are keyed by *what they are a draw about* —
`(payment_id, purpose, attempt_no)` — hashed with the run seed, rather than
pulled from a shared stream. With one stream, policies consume draws at different
rates and by the tenth payment are being scored against different luck; a 5%
"lift" could be pure variance. Keyed draws mean a policy that wins, won on
judgement.

### 4. You can reproduce it without a model

Every backend runs at `temperature 0`, so the model is a deterministic function
of its inputs and [`agent/cache.py`](agent/cache.py) memoises decisions keyed on
`(model, schema, system prompt, user message)`. **The cache is committed and
replayed when no backend is reachable** — verified on a fresh clone: 1,688
decisions replayed, the published numbers exactly, 0 live calls.

A replay *miss* is fatal, not silent. A partial replay would publish different
numbers from the recorded ones, which is precisely what this exists to prevent.

### 5. It has touched real Razorpay

The evaluation runs on a simulator, and it has to — test mode cannot produce
sixty-six failure reasons with realistic clustering, issuer outages on demand, or
a month of correlated failures. Anyone who says "just use the real API" has not
thought about where the failure distribution comes from.

But that leaves a fair objection: *has any of this touched real Razorpay?*

```
$ python scripts/live_probe.py --replay

REAL RAZORPAY FAILURE  (test mode)
  payment       pay_TUORtBOxc2nlNA
  error_reason  international_transaction_not_allowed
  error_source  business
  error_step    payment_initiation

  in our transcribed taxonomy : YES
  mapped by the agent rulebook: hard_stop

THE AGENT, ON A REAL PAYLOAD
  diagnosis    hard_stop  (confidence 0.98)
  rationale    "international transactions are disallowed, a compliance
                block, so no retry or customer contact."
  POLICY       ALLOW -> escalate
```

A live Razorpay error payload, through the **same `Observation` dataclass the
evaluation uses**. If the schema were wrong or the reason strings invented, this
could not run. The agent correctly identified a compliance block and chose to
escalate rather than burn attempts on something unrecoverable.

**Deliberately outside the evaluation path** — nothing in `evaluation/` imports
it, so the reproducible numbers cannot break because a network call failed. Test
mode is enforced in code: `_require_test_mode()` refuses any key without an
`rzp_test_` prefix, and the module has no capture, refund or payout path.

The payload is committed (with payer PII redacted), so `--replay` reproduces it
offline forever.

### 6. Failures emerge from the model, so calibration is falsifiable

We simulate all 20,000 payment attempts — successes included — and let each fail
as a *consequence* of its issuer's decline rate, any live outage, and the
customer's liquidity. That buys the one free check available: the success rate
must land in the **92–96%** band NPCI's published ceilings imply.

```
attempts 19,965 · success rate 92.54% · failures 1,492
```

A hand-picked failure list could never be *wrong* about anything. This one can,
and `verify()` fails the run if it drifts.

---

## Architecture

```
sim/           the world. agent/ may never import this — enforced by a test.
  taxonomy.py    66 real Razorpay error reasons -> 6 recovery classes
  issuers.py     decline rates DERIVED from NPCI circular OC-149 ceilings
  customers.py   intent decay vs salary-cycle liquidity
  world.py       hidden truth, common random numbers, outcome resolution
  generate.py    failures emerge; calibration is checkable

agent/         cannot import sim/. At all.
  observe.py     THE BOUNDARY. Only merchant-visible fields.
  knowledge.py   GENERATED from Razorpay's public docs. 56 reasons,
                 0 recovery probabilities, no entry for 10 held-out ones.
  decide.py      one structured call: diagnose + plan
  policy.py      8 ordered guardrails that can overrule the model
  audit.py       append-only decision log
  cache.py       decision cache + replay — committed, reproducible
  llm.py / llm_ollama.py / llm_http.py / jsonio.py
                 Anthropic · Ollama (local+cloud) · Gemini · Groq · stub

economics/     MDR, messaging, human time, goodwill, compliance exposure
evaluation/    adapter, baselines, harness, sensitivity, report, artifacts
integrations/  razorpay_live.py -- real test-mode API. Outside the eval path.
web/           Express API + React console
```

**~9,150 lines of Python, ~3,700 of web, 64 tests.**

Design rationale: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## The five policies

The comparison only means something if the opponents are real.

1. **`no_retry`** — the floor.
2. **`fixed_retry`** — retry everything 3× on a backoff. What a great many
   merchants actually run, because it is the thing you build in an afternoon.
3. **`rules_engine`** — **the real opponent.** Razorpay's own published "Next
   Steps", competently implemented, with the same guardrails our agent gets.
4. **`naive_llm`** — an LLM, the error string, "recover the revenue". No guardrails.
5. **`agent`** — ours.

**The agent and the rules engine read the same rulebook.** That is deliberate and
it makes the test much harder: the model cannot win by knowing what
`insufficient_funds` means, because the lookup table knows too. It has to win on
held-out reasons, ambiguous ones, timing, and knowing when to stop.

---

## What we got wrong

Every one of these was found by a number looking wrong, not by a test.

**The cost model said ignoring fraud was profitable.** We priced what escalation
*costs* without pricing what it *avoids* — so retrying a fraud block three times
(₹1.50) beat routing it to a human (₹45) by thirty times. Added compliance
exposure, tracked separately, net reported both ways.

**Silent retries were recovering abandoned checkouts.** There is no stored
credential and nobody at the 3DS screen — which is the entire reason nudges
exist. This one flipped the ranking.

**`ALL_METHODS` excluded `EMANDATE`**, so subscription debits could only fail
with an exotic mandate error. `insufficient_funds` and `card_expired` are *the*
classic involuntary-churn causes and couldn't reach that rail.

**A 429 means two different things.** Transient back-pressure clears; an
exhausted quota never does. Treating them alike made the backoff sleep through a
batch and report model failures for a hard limit — and a failed decision defaults
to `stop`, so it silently handicapped the policy being measured.

**The reproducibility claim was false.** Without credentials the client fell back
to the stub, whose model name cannot match the cache, so it recomputed every
decision with a heuristic and printed ₹35,919 against the published ₹39,420 with
nothing indicating a problem. Caught by cloning to `/tmp` and reading the engine
column.

**Then we reintroduced the same bug.** `ReplayMiss` was declared fatal but
subclassed `LLMUnavailable`, which the policy layer catches and turns into
`stop`. Both now sit under one `FatalLLMError` base, so the policy re-raises a
*type* rather than a list someone has to remember to extend.

**The cache never flushed on completion** — every run lost its trailing
decisions, so a clone replayed two policies perfectly then missed part-way
through the third.

And one in the web layer: **`EventSource` auto-reconnects, and every reconnect
re-issued a GET that spawned a Python process.** Restarting the server mid-stream
silently launched a fresh evaluation that overwrote the results file. Any SSE
endpoint with a side effect has this bug.

**The guardrail was working and the console showed no sign of it.** A step
rendered as an intervention only when the final action differed from the
proposed one — but a deferral changes an action's *timing*, not its identity, so
all 28 quiet-hours holds drew as ordinary allows. The agent vetoes nothing, so
those deferrals were the only visible evidence the policy layer does anything,
and the most demonstrable compliance behaviour in the run was sitting in the
artifact unrendered. The console and the evidence tab were also quoting
different headline numbers — ₹43,862 gross against ₹40,849 net — with no bridge
between them, so anyone doing the arithmetic got a third figure.

**The demo contradicted its own output.** Beat 6 printed `rules_engine wins
24/36 … loses at 12` and then narrated "wins 27 and loses 9" two lines below it.
Beat 5 said "Five policies" over a table with three rows, and ran `--no-llm`, so
the demo of an AI agent did not contain the agent. Every figure was typed in
when it was true and never revisited. Nothing in the repo could catch this; it
surfaces only when someone reads a beat aloud, which is what recording means.
All of them are now derived from the run that just printed.

**A fix that changed nothing, because we guessed a status code.** The dashboard
answered a slow API boot with "no results yet — generate one with
`python scripts/run_eval.py --no-llm`": wrong cause, wrong remedy, and only F5
cleared it. The retry we added triggered on HTTP 502. Vite reports a dead proxy
target as a plain **500**, so the first version did nothing at all — found by
pointing the proxy at a dead port and watching the old banner appear anyway.
Status alone cannot separate "nothing is listening" from a real server fault;
the body can, since every error this API raises is JSON carrying an `error` key.

**Headline figures could render as negative money.** The count-up clamped its
progress fraction at the top but not the bottom. `requestAnimationFrame` hands
back the timestamp of the frame it belongs to, and that can predate a
`performance.now()` taken inside the same frame — so the eased fraction went
negative and *Value at risk* displayed **−₹4,414** for a frame or two before
settling. Each wrong figure was exactly −2.15% of its target, which is what gave
the easing away. It survived this long because everything that had ever checked
it looked *after* the animation finished.

---

## What this cannot prove

- It does not prove the agent recovers real money from real consumers. It proves
  it makes better decisions than four alternatives **in a world calibrated from
  Razorpay's and NPCI's published documentation**.
- `base_recovery_prob` values are assumptions. Their *ordering* is defensible;
  their exact values are not.
- Per-bank decline rates are **derived from NPCI's OC-149 ceilings plus a stated
  tier assumption**, not measured. We tried to obtain the primary monthly file
  and could not — NPCI returns 403 to automated fetches — so the per-bank
  precision was removed rather than sourced second-hand.
- The customer behaviour model is the least grounded component here.
- **Held-out n=3 and ambiguous n=10 are too thin to conclude from.**
- Only payment failures. The brief also mentions checkout abandonment (partly
  covered via `nudge_customer` — 12 reasons) and overdue receivables (not at
  all).
- The live Razorpay proof is **one payment**. It establishes that the schema and
  the reason strings are real; it is not evidence about recovery rates.

**This is a decision-quality benchmark, not a revenue forecast.**

Live weaknesses: [`docs/OPEN_ISSUES.md`](docs/OPEN_ISSUES.md).

---

## Documents

| | |
|---|---|
| [`docs/SUBMISSION.md`](docs/SUBMISSION.md) | Form answers, and what to say in the panel |
| [`docs/STATE.md`](docs/STATE.md) | Cold-start handoff: run it, what's left, traps already hit |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How it fits together and why |
| [`docs/CALIBRATION.md`](docs/CALIBRATION.md) | Which numbers are transcribed, derived, and **assumed** |
| [`docs/OPEN_ISSUES.md`](docs/OPEN_ISSUES.md) | Known weaknesses, kept in the repo on purpose |
| [`docs/VIDEO.md`](docs/VIDEO.md) | Shot-by-shot script for the 5-minute submission |
