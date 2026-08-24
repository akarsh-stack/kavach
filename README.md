# Bounded Revenue Recovery

**Razorpay Buildathon — Track 03, AI Revenue Recovery**

An agent that takes a batch of failed Razorpay payments, diagnoses each root
cause, chooses a bounded recovery action, and proves it recovered more money
than four competing policies — **net of the cost of trying**.

The interesting part is not the agent. It is the measurement around it.

---

## The brief, clause by clause

> *"Don't just identify the problem. Show measured money recovered across a
> batch, with compliant escalation, stopping rules, and an audit trail."*

| The bar asks for | Where it lives | What it does |
|---|---|---|
| measured money recovered across a batch | [`evaluation/harness.py`](evaluation/harness.py) | 6 policies, same batch, same seed, same luck |
| compliant escalation | [`agent/policy.py`](agent/policy.py) | risk → risk review, integration bugs → engineering, once per bug |
| stopping rules | [`agent/policy.py`](agent/policy.py) | 8 ordered rules that **veto, defer or substitute** the model's proposal |
| an audit trail | [`agent/audit.py`](agent/audit.py) | records what was **prevented**, not just what ran |

---

## 60-second quickstart

Everything below runs with **no API key and no model**. Three of the five
policies need neither.

```bash
pip install pydantic pytest
python scripts/inspect_batch.py        # generate a batch, verify its calibration
python scripts/run_eval.py --no-llm    # compare the credential-free policies
python scripts/run_sensitivity.py      # does the conclusion survive our assumptions?
python -m pytest tests/ -q             # 32 tests, including the boundary proof
```

The dashboard:

```bash
cd web && npm run install:all && npm run dev
```

React on `:5173`, Express on `:5174`. The **run evaluation** button executes the
real Python pipeline and streams its output live.

---

## The result

150 failed payments, ₹192,658 at risk, ₹165,146 of it theoretically recoverable.
Decisions by `gpt-oss:120b` via Ollama Cloud, served from the committed cache.

```
policy                 engine     net Rs   direct Rs   gross Rs   expo Rs   viol   wasted
agent                  ollama     39,420      39,420     42,341         0      0        0
agent_no_guardrails *  ollama     39,407      39,407     42,341         0      0        0
rules_engine           rules      35,970      35,970     38,673         0      0        0
fixed_retry *          none       32,007      36,057     36,995     4,050     48       48
no_retry               none            0           0          0         0      0        0
naive_llm *            ollama    -39,640     -31,240     38,752     8,400    607       57

* runs without guardrails, by design
```

**The agent beats the rules engine — Razorpay's own published guidance,
competently implemented — by 9.6% net, with zero policy violations and zero
wasted attempts.**

Three results matter more than that one.

### The obvious approach is worse than doing nothing

`naive_llm` is an LLM, the error string, and "recover the revenue" — the typical
hackathon submission. It recovered ₹38,752 gross, right alongside everyone else.
Then it destroyed **₹18,564 in goodwill and ₹49,500 in churn**, committed **607
policy violations**, and hit the event cap on 100 of 150 payments because it
never stops.

Net: **−₹39,640.** Same model as our agent. The difference is entirely the
economics, the stopping rules and the guardrails.

### The guardrails gained ₹12 and prevented nothing

Not the result we expected, and we are not dressing it up. On this batch the
model never proposed a blocked action, so the policy layer never fired.

The honest reading: the case for the guardrails is that they **bound the worst
case**, not that they improve the average. `naive_llm` is what the worst case
looks like with the same model behind it.

### Where the model actually earns its place

```
                  overall    n     held-out   n    ambiguous   n
rules_engine       95.9%    147       0.0%    0       14.3%    7
agent              95.3%    150      66.7%    3       14.3%    7
```

On **held-out** reasons — absent from the rulebook entirely — the agent scores
66.7% where the rules engine scores nothing, because it has no mapping and
structurally cannot. That is the gap the design predicted. **But n = 3.**

On **ambiguous** `payment_failed`, both manage 1 of 7. Both bad. The contextual
inference we claimed is not yet demonstrated, and a larger batch is the next
thing this project needs.

### Does the lift survive our assumptions?

Mostly. `python scripts/run_sensitivity.py --engine ollama --subject agent`
sweeps 36 combinations of the three softest numbers in the model.

**The agent wins 34 of 36.** The two losses are both at `annoyance ×10`, where
over-contacting costs ten times our estimate — at that price messaging stops
paying and `fixed_retry`, which never contacts anyone, pulls ahead of every
policy that does.

```
everywhere except annoyance x10 : 27 / 27
at annoyance x10                :  7 /  9
```

Notably the agent is *completely insensitive* to the compliance-exposure axis,
because it commits zero violations — exposure is a cost it never incurs. That
same axis swings `fixed_retry` from ₹36,057 down to ₹15,807.

**Magnitude is not defensible, only direction.** Across the grid the lift ranges
−17.6% to +13.1%, so we quote no single lift percentage anywhere.

Full grid: [`docs/CALIBRATION.md` §5.4.1](docs/CALIBRATION.md).

---

## Why you can believe the numbers

Every simulated benchmark faces the same objection: *you wrote the simulator and
the agent, so of course the agent wins*. Usually that objection is correct. Five
things are in place so that it is not.

### 1. The failure taxonomy is real, not invented

All **66 error reasons** in [`sim/taxonomy.py`](sim/taxonomy.py) are transcribed
verbatim from
[razorpay.com/docs/errors/payments/list](https://razorpay.com/docs/errors/payments/list/)
— the `reason` string, Razorpay's description, and Razorpay's own **"Next
Steps"**. That last column is, in effect, a published recovery policy written by
the payments company we are building for, so our recovery classes are a
compression of *their* stated opinion rather than ours.

A reviewer can check it against their docs, line by line.

### 2. The agent provably cannot see the answer key

Hidden state lives in a private dict on `World`, keyed by payment ID. Nothing the
agent can reach holds a reference to it.

That is not a convention — it is enforced. `tests/test_observability_boundary.py`
walks the AST of every file under `agent/` and fails the build on any import
from `sim/`, any attribute read of a private name, and any `getattr` used to
sneak past both.

We checked the test can fail. Injecting `from sim.world import Truth` into
`agent/observe.py`:

```
E  AssertionError: The agent must not import the simulator.
E      agent/observe.py:38 imports from sim.world
FAILED tests/test_observability_boundary.py::test_agent_never_imports_sim
```

### 3. Every policy meets identical luck

Random draws are keyed by *what they are a draw about* —
`(payment_id, purpose, attempt_no)` — hashed with the run seed, rather than
pulled from a shared stream.

This matters more than it sounds. With one stream, policies consume draws at
different rates, so by the tenth payment two policies are being scored against
completely different luck and a 5% "lift" could be pure variance. With common
random numbers, the roll deciding *"does attempt 2 on pay_00042 succeed"* is the
same under every policy. A policy that wins, won on judgement.

### 4. You can reproduce the model-driven numbers without a model

Every backend runs at `temperature 0`, which makes the model a deterministic
function of its inputs — so [`agent/cache.py`](agent/cache.py) memoises decisions
to disk, keyed on `(model, schema, system prompt, user message)`. Any change to
the prompt or the observation is a different key and a real call; there is no
way to silently serve a stale decision.

**That cache is committed.** A reviewer with no API key and no GPU can clone this
repo, run the evaluation, and get our exact numbers *including the LLM policies*.

It also makes the sweep affordable. Measured on a 27-point grid over the agent:

```
without the cache: 4,329 model calls
with it:             164        (96.2% hit rate)
```

Entries record which model produced them, so a cached stub decision can never
launder itself into a run labelled as a model's, and one model's decisions can
never be served under another's name. Both properties are tested.

### 5. The failures emerge from the model, so calibration is falsifiable

We simulate all 20,000 payment attempts — successes included — and let each fail
as a *consequence* of its issuer's NPCI-derived decline rate, any live outage,
and the customer's liquidity. That compute is thrown away, and it buys the one
free check available: the resulting success rate must land in the **92–96%** band
NPCI's published figures imply.

```
attempts 19,965 · success rate 92.28% · failures 1,543
```

A hand-picked list of failures could never be *wrong* about anything. This one
can, and `verify()` fails the run if it drifts.

---

## Architecture

```
sim/           the world. agent/ may never import this — enforced by a test.
  taxonomy.py    66 real Razorpay error reasons -> 6 recovery classes
  issuers.py     11 banks + 5 UPI apps, NPCI-informed decline rates,
                 outage episodes with a DELAYED and LOSSY public signal
  customers.py   intent decay vs salary-cycle liquidity
  world.py       hidden truth, common random numbers, outcome resolution
  generate.py    failures emerge from the model; calibration is checkable

agent/         cannot import sim/. At all.
  observe.py     THE BOUNDARY. Only merchant-visible fields.
  knowledge.py   GENERATED from Razorpay's public docs. 56 reasons,
                 0 recovery probabilities, and no entry for 10 held-out ones.
  decide.py      one structured call: diagnose + plan
  policy.py      8 ordered guardrails that can overrule the model
  audit.py       append-only decision log
  llm.py         Claude via structured output
  llm_ollama.py  local Ollama or Ollama Cloud, same interface
  cache.py       deterministic decision cache -- committed, so results
                 reproduce without credentials

economics/     MDR, messaging, human time, goodwill, compliance exposure
evaluation/    adapter (the only place both halves meet), baselines,
               harness, sensitivity, report, artifacts
web/           Express API + React dashboard
```

**~7,900 lines of Python, ~1,100 of JS/JSX, 32 tests.**

Full design rationale: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## The five policies

The comparison only means something if the opponents are real.

1. **`no_retry`** — the floor. What the failures cost if left alone.
2. **`fixed_retry`** — retry everything 3× on a backoff. Not a strawman: it is
   what a great many merchants actually run, because it is the thing you build
   in an afternoon.
3. **`rules_engine`** — **the real opponent.** Razorpay's own published "Next
   Steps", competently implemented, with the same guardrails our agent gets.
4. **`naive_llm`** — an LLM, the error string, and "recover the revenue". The
   typical hackathon submission. No guardrails.
5. **`agent`** — ours. Rulebook, economics, timing, and a policy layer that can
   overrule it.

Plus `agent_no_guardrails` as an ablation, so the guardrails' price is measured
rather than assumed.

### The agent and the rules engine read the same rulebook

This is deliberate, and it makes the test much harder. Both get Razorpay's
public documentation. The model cannot win by knowing what `insufficient_funds`
means — the lookup table knows that too. It has to win on the four things docs
cannot give you:

- **Held-out reasons.** 10 of the 66 are absent from the rulebook, simulating
  the reality that gateway reason strings appear faster than anyone maps them.
- **Ambiguous reasons.** `payment_failed` is documented by Razorpay as carrying
  *no specific error code from the gateway*. The right action must be inferred
  from context — rail, amount, customer history, and whether other payments at
  the same bank are failing right now.
- **Timing.** Nothing in the docs says whether to retry in ten minutes or on the
  2nd of next month. Most of the recoverable money is in that choice.
- **Knowing when to stop.**

---

## The model proposes, the policy disposes

The obvious build is to tell the model *"never retry a risk-declined payment"*
and trust it. We do tell it that. We also assume it will sometimes do it anyway,
because a system whose only safety mechanism is a paragraph of English has no
safety mechanism.

So 8 deterministic rules sit between the model and any real action:

| Rule | What it stops |
|---|---|
| `R1_RISK_BLOCK` | Re-presenting a risk decline — the MID-review risk |
| `R2_MERCHANT_BUG` | Retrying our own `invalid_order_id` instead of paging engineering |
| `R2_BUG_ALREADY_REPORTED` | Filing 300 tickets for one bad deploy |
| `R3_ATTEMPT_CAP` | Excessive retry ratios |
| `R4_CONTACT_CAP` | A customer with 3 failed payments getting 6 messages |
| `R5_BUDGET` | Spending past the batch ceiling |
| `R6_UNECONOMIC` | ₹60 of WhatsApp to recover a ₹99 renewal |
| `R7_ESCALATION_CAPACITY` | Forwarding the problem instead of solving it |
| `R8_QUIET_HOURS` | Overnight messaging — **deferred, not cancelled** |

Two of those deserve a note.

**Quiet hours defer rather than veto.** The contact is legitimate; only the
timing is wrong. Cancelling would throw away real revenue. And retries are
explicitly *exempt* — a silent re-presentment disturbs nobody, and blocking it
would forfeit the whole night, which is exactly when banks come back up.

**We override Razorpay on risk declines.** Their docs suggest the *customer* try
another method, which is fine for a human at a checkout. It is a different act
for an automated system to re-present a declined transaction at volume. That
override is deliberate and argued in `docs/CALIBRATION.md` §3 rather than buried.

---

## What we got wrong

Four bugs worth reporting, all found by numbers looking wrong rather than by a
test, and all fixed at the cause rather than by tuning until we liked the answer.

**1. The cost model said ignoring fraud was profitable.** We priced what
escalation *costs* without pricing what it *avoids*. Direct costs alone said
retrying a fraud block three times costs ₹1.50 while routing it to a human costs
₹45 — so ignoring the risk layer was thirty times cheaper. Added compliance
exposure, tracked separately, with net reported both ways.

**2. Silent retries were recovering abandoned checkouts.** The model had a silent
re-presentment recovering customers who walked away from a 3DS screen. There is
no stored credential and nobody at the auth page — which is the entire reason
nudges exist. Now gated on `is_subscription`, where a standing mandate makes
re-presentment legitimate. This one flipped the ranking.

**3. Sampling uniformly within a recovery class** made `funds_blocked_by_mandate`
— an exotic error — the #2 failure in the batch, and inflated held-out reasons to
16%, quietly making the benchmark far easier for an LLM than reality would.

**4. `ALL_METHODS` excluded `EMANDATE`**, so subscription debits could only fail
with that same exotic mandate error. `insufficient_funds` and `card_expired` are
*the* classic involuntary-churn causes and could not reach that rail at all.

And one in the web layer, at the boring end of the stack but the most
production-shaped of the four: **`EventSource` auto-reconnects, and every
reconnect re-issued a GET that spawned a Python process.** Restarting the server
mid-stream silently launched a fresh evaluation that overwrote the results file.
Any SSE endpoint with a side effect has this bug. Fixed: one job at a time, a
second caller attaches to the running job, and a client hanging up no longer
kills the run.

---

## What this cannot prove

Stated here so nobody has to catch us on it:

- It does not prove the agent recovers real money from real consumers. It proves
  it makes better decisions than four alternatives **in a world calibrated from
  Razorpay's and NPCI's published documentation**.
- `base_recovery_prob` values are assumptions. Their *ordering* is defensible;
  their exact values are not.
- The customer behaviour model is the least grounded component in the project.
- **Lift magnitude is not defensible, only direction.** Across the sensitivity
  grid the lift ranges −23.8% to +781.6%, the top end being denominator collapse
  when the rival is barely net-positive. We therefore quote no single lift
  percentage anywhere.

**This is a decision-quality benchmark, not a revenue forecast.** We would rather
present it accurately than dress it up.

Live list of known weaknesses: [`docs/OPEN_ISSUES.md`](docs/OPEN_ISSUES.md).

---

## Running the model-driven policies

Any of three backends, selected by flag. Everything else is unchanged — the
prompt, schema, policy layer, audit trail and harness are engine-agnostic.

```bash
# Local and free
ollama pull qwen2.5:7b
python scripts/run_eval.py --engine ollama --limit 150

# Anthropic (Haiku ~$1.40/run for iteration, Opus for the headline)
export ANTHROPIC_API_KEY=sk-ant-...
python scripts/smoke_llm.py                       # verify before spending
python scripts/run_eval.py --engine anthropic --model claude-opus-5
```

A run with no working backend falls back to `StubClient` — a deterministic
heuristic that makes no model call. **Stub output is not a model result**, and
that is enforced rather than trusted: every artifact carries `engine: "stub"`,
the report refuses to print a stub run under an LLM policy label, and the
dashboard shows a warning banner.

---

## Documents

| | |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How it fits together and why |
| [`docs/CALIBRATION.md`](docs/CALIBRATION.md) | Which numbers are transcribed, derived, and **assumed** |
| [`docs/OPEN_ISSUES.md`](docs/OPEN_ISSUES.md) | Known weaknesses, kept in the repo on purpose |
| [`docs/PLAN.md`](docs/PLAN.md) | Build plan and non-goals |
