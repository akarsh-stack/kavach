# Architecture

## The shape of the problem

A failed payment is not one decision, it is a sequence of them under
uncertainty. You see an error string, you do not see why it really failed, and
the right response depends on facts you have to infer: is this instrument dead
or is the bank having a bad ten minutes? Does this customer still want the thing?
Will they have money on Tuesday?

Every design decision below follows from wanting to *measure* whether a system
makes those calls well — which is harder than building the system.

---

## Two halves that cannot see each other

```
┌───────────────────────────────┐        ┌──────────────────────────────┐
│  sim/                         │        │  agent/                      │
│                               │        │                              │
│  Truth  ── true recovery      │        │  Observation                 │
│           class, real         │   ✗    │    reason, description,      │
│           probability,        │ ────►  │    amount, method, issuer,   │
│           customer intent,    │  never │    own customer history,     │
│           unreported outages  │        │    delayed downtime signal   │
└───────────────────────────────┘        └──────────────────────────────┘
                │                                       ▲
                │        ┌──────────────────────┐       │
                └───────►│ evaluation/adapter.py│───────┘
                         │  the only meeting    │
                         │  point; imports both │
                         └──────────────────────┘
```

`Truth` lives in a private dict on `World`, keyed by payment ID. No object the
agent can reach holds a reference to it, so there is no attribute to
accidentally read and no duck-typed object to reach through.

`evaluation/adapter.py` is the single place both halves meet. It lives outside
`agent/` on purpose: it imports both sides so neither has to import the other.

**Enforced, not conventional.** `tests/test_observability_boundary.py` parses
every file under `agent/` and fails on any `sim` import, any attribute read of a
private name, or a `getattr` used to evade both. It is AST-based rather than a
text scan so agent modules stay free to *discuss* the boundary in their
docstrings — a naive `token in line` check would forbid exactly the
documentation the design most needs written.

### What the agent is allowed to know

The rule applied field by field: *could an engineer at a mid-size D2C company
read this off their own database or their Razorpay dashboard on a Tuesday
afternoon?*

| Category | Examples | Why it qualifies |
|---|---|---|
| The failed payment | `reason`, `description`, `source`, amount, method | On Razorpay's payment object and failed-payment webhook |
| Their own CRM | prior payments, prior failures, contacts this week | The merchant's own data about their own customer |
| Public downtime | two booleans, delayed and lossy | Razorpay exposes downtime to merchants |
| Their own stream | failures at the same bank in the last hour | Anyone can count their own webhooks |

Deliberately **not** exposed: outage severity or expected duration. A real
downtime feed tells you something is wrong, not how wrong or for how long.
Handing over a severity float would leak simulator state through a
plausible-looking API — which is how this class of benchmark usually cheats.

That omission creates the most interesting inference in the project: **a third
of outages here are never reported at all**, and reports lag onset. A good agent
notices nine failures at one PSP in ten minutes and acts before the feed admits
anything is wrong.

---

## The simulator

### Failures emerge; they are not sampled

`sim/generate.py` simulates all 20,000 attempts and keeps the ~1,500 that fail.
Each fails as a consequence of its issuer's decline rate, any live outage, and
the customer's liquidity.

Throwing away 18,500 successful payments buys two things a sampled failure list
cannot have:

1. **A falsifiable check.** The success rate must land in the 92–96% band NPCI's
   published figures imply. It lands at 92.54%. A hand-built list could never be
   wrong about anything.
2. **Real correlation structure.** Failures cluster during outages, at weak
   issuers, and at month-end when customers are short. That clustering is the
   signal a good agent exploits, and it exists only because we let it emerge.

### The tension that makes timing a real decision

Two forces point in opposite directions, and the optimum is between them:

- **Waiting costs money.** Purchase intent decays exponentially — fast for
  impulse retail, slowly for a subscription renewal where the customer already
  decided and a mandate is in place.
- **Waiting earns money.** An `insufficient_funds` failure on the 28th has poor
  odds. The same customer on the 2nd, post-salary, is a different proposition.

Neither a fixed backoff nor a lookup table built from Razorpay's documented next
steps can find that optimum, because it depends on *this* customer, on *this*
date, at *this* amount. That is the gap a model has to earn its place in.

### Common random numbers

Every draw is keyed by `(payment_id, purpose, attempt_no)` and hashed with the
run seed, rather than pulled from a stream.

The naive version silently breaks the comparison: policies take different
actions, consume draws at different rates, and within ten payments are being
scored against different luck. A 5% lift measured that way might be nothing but
variance. Keyed draws mean the roll deciding *"does attempt 2 on pay_00042
succeed"* is identical under every policy — so a single run per policy is enough,
instead of hundreds of replications to average the noise out.

`blake2b`, not the builtin `hash()`, which is salted per process and would make
runs irreproducible across machines — the exact failure a reviewer would hit
trying to replicate our numbers.

---

## The agent

### One structured call, two scored outputs

`agent/decide.py` merges diagnosis and planning into a single call. Splitting
them doubles cost and latency for no measured gain — the plan is largely
determined by the diagnosis plus the observation, and the model has both.

But the diagnosis is still returned as its own field, so
`evaluation/report.py` scores classification accuracy independently of plan
quality. An agent that diagnoses correctly and then plans badly needs a
different fix from one that misreads the failure entirely.

Output is constrained by a JSON schema (`output_config.format` on Anthropic,
`format` on Ollama), so the response is guaranteed to parse and the loop
contains no text-wrangling at all.

### The rulebook is generated, not hand-written

`scripts/gen_knowledge.py` produces `agent/knowledge.py` from `sim/taxonomy.py`,
doing the separation mechanically rather than trusting anyone to remember it:

| | |
|---|---|
| **Carried over** | reason, description, next steps, derived class — all public |
| **Stripped** | `base_recovery_prob` — our hidden assumption, in no Razorpay doc |
| **Omitted** | all 10 `held_out` reasons — a real rulebook always lags |

The generated file is committed, so `agent/` never imports `sim/` at runtime and
the boundary test stays satisfied.

### The policy layer

8 deterministic rules, priority-ordered, first match wins. Each returns one of
**allow / veto / defer / substitute**, and every outcome is recorded with the
rule that produced it.

The rules are not advice. A model at 0.97 confidence proposing a retry on a risk
decline gets an audit line, not a debate. `tests/test_policy.py` poses each rule
adversarially for exactly that reason — a guardrail that only holds when the
model is already behaving is not a guardrail.

Policies that run *without* guardrails (`fixed_retry`, `naive_llm`) still have
their proposals **evaluated** by the engine, so every action that would have been
blocked is counted. That is where the `policy_violations` column comes from, and
it is what shows the real cost of those policies: not lower recovery, but
retried risk declines and over-contacted customers.

---

## The evaluation

### Discrete-event, one shared clock

The obvious implementation walks failures one at a time and runs each payment's
recovery to completion. That is simpler and it is wrong, because two constraints
are *shared*:

- a customer's weekly contact cap spans every payment they have with us
- the batch budget and ops escalation capacity are global

Running payment A's four-day recovery before touching payment B — which failed an
hour later — makes that bookkeeping time-inconsistent and the caps stop meaning
anything. So all payments share one priority queue and interventions interleave
the way they would in production.

`wave > 1` decides concurrently, which is what makes a model run finish in
minutes. The approximation is documented in the code: payments inside one wave
observe pre-wave state, which can only ever make a policy look *worse* on contact
caps, never better — so it cannot flatter our own agent.

### The cost model

Gross recovery is a vanity metric any policy can win by trying everything. Four
things get priced:

1. **Direct attempt cost** — messaging fees and the real cost of re-presenting
2. **MDR** — recovering ₹1,000 does not put ₹1,000 in the merchant's account
3. **Human time** on escalations, deliberately expensive so the optimal policy
   is not "escalate everything"
4. **Goodwill and compliance exposure** — the two softest numbers, both swept

`net` and `net_direct` are reported separately. The gap between them is the
argument: a policy where they diverge is buying revenue with regulatory risk.

### Sensitivity as a first-class output

`evaluation/sensitivity.py` re-runs the whole comparison across 36 grid points
and asks one question: **does the winner change?**

It does, in a specific and interpretable place — 8 of 9 losses are the
`exposure = 0` column. That result is in the README and the dashboard rather
than a footnote, because a conclusion that only holds at one convenient setting
of an unsourceable number is not a conclusion, and finding that out is the
sensitivity analysis doing its job.

---

## The web layer

```
Python  ──writes──►  data/runs/*.json  ──reads──►  Express  ──►  React
                                                      │
                                                      └─ spawn ─► scripts/run_eval.py
```

The server owns **no analysis**. It serves recorded JSON and shells out to the
Python entrypoints, so what the dashboard shows is exactly what the CLI printed
and there is no second implementation of the metrics to drift out of sync with
the first. If a number on screen is wrong, it is wrong in the artifact, and the
artifact is in git.

Every artifact records the engine that produced it. A stub run is
self-identifying wherever it travels.

Charts are hand-rolled SVG against a palette validated by script rather than by
eye — all six checks pass on this surface (worst adjacent colour-blind
separation ΔE 8.4, normal-vision 19.3, all ≥3:1 contrast). Cell colour in the
sensitivity grid encodes *identity* (which policy won), so it is categorical,
not a sequential ramp — a ramp there would imply an ordering between policies
that does not exist.

---

## The live Razorpay integration

`integrations/razorpay_live.py` talks to the real test-mode API. It exists to
answer one question a simulator cannot: *has any of this touched real Razorpay?*

```
Razorpay test API  ->  LiveFailure  ->  to_observation()  ->  Observation
                                                                  |
                                              the SAME dataclass  |
                                              the evaluation uses <-
```

That arrow is the whole point. If our `Observation` schema did not match
Razorpay's actual payment object, or the reason strings in `sim/taxonomy.py`
were invented, `to_observation()` could not be written and the probe could not
run. It runs, and the live reason came back already present in our taxonomy.

**It is deliberately outside the evaluation path.** Nothing under `evaluation/`
imports it. The reproducible numbers cannot break because a network call failed,
and a demo cannot die because an API was slow — which was the original argument
for skipping live integration entirely. The concern is contained rather than
dismissed.

**Test mode is enforced, not assumed.** `_require_test_mode()` refuses any key
without an `rzp_test_` prefix, and the module has no capture, refund or payout
path — there is no code there that could move money even against a live key.

Scale is the thing it cannot provide. Test mode cannot produce sixty-six failure
reasons with realistic clustering, issuer outages on demand, or a month of
correlated failures. That is why the simulator exists, and why this proves reach
rather than replacing it.

## Non-goals

Stated so scope creep has a wall to hit.

- **No real Razorpay API calls in the evaluation.** Live integration exists
  (see above) but stays outside the measured path: a network failure must not be
  able to change a published number.
- **No live customer messaging.** Nudges are simulated.
- **No fine-tuning.** Prompted models only.
- **No multi-tenancy, auth, or deployment.**
