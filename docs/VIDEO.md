# The 5-minute video

Everything below is checked against the committed artefacts. No number in this
script is aspirational.

---

## Before you record

```bash
# 1. Confirm the state (30 seconds)
python -m pytest tests/ -q                     # 64 passed
python scripts/inspect_batch.py                # CALIBRATION: PASS

# 2. Start the dashboard, leave it running
cd web && npm run dev                          # :5173

# 3. Have a second terminal ready, sized ~110 columns, dark, 16pt
```

Browser at **1440×900, zoom 110%**. Close every other tab. Notifications off.

Record screen and voice **separately** — narrating live over a demo is much
harder to get right, and one fluffed line costs you the whole take.

---

## The argument

One line, and every beat serves it:

> There is real money in failed payments. An LLM alone makes it worse. A bounded
> agent makes it better, and here is the proof — including where the proof
> breaks.

If a beat doesn't advance that, cut it.

**Tone: flat.** No "revolutionary", no "game-changing". The material is strong
enough that overselling actively hurts — an engineer trusts the person who says
"here is what I cannot prove" far more than the one who says "this changes
everything."

---

## Beat 1 · 0:00–0:35 · The problem, on screen

**Show:** the Recovery console, top of page.

**Say:**

> This is a month of payments for a mid-size Indian merchant on Razorpay.
> Twenty thousand attempts, a 92.5% success rate — which sounds fine until you
> price the other 7.5%.
>
> Fifteen hundred failed payments. In this batch of a hundred and fifty, one
> lakh sixty-one thousand rupees still unresolved.
>
> Some of that is genuinely unrecoverable. Most isn't. The question is which —
> and what it costs to find out.

**On screen:** `REVENUE AT RISK ₹1,61,600` · `RECOVERED ₹43,862` ·
`NEEDS A HUMAN 18`

---

## Beat 2 · 0:35–1:20 · The agent working

**Show:** the queue. Click **`pay_011504`** to expand it.

**Say:**

> This is the queue the agent works. Not a report — a work list, ordered by what
> wants attention first.
>
> This payment failed with `payment_risk_check_failed`. The agent diagnosed it
> as a hard stop, at 0.99 confidence, and here's its own reasoning:
>
> *"risk declines must not be retried or nudged, so escalation is required."*
>
> So it didn't retry. It routed it to a human. That's one of eighteen sitting in
> the escalation queue, each with a stated cause — which is a very different
> conversation with a risk team than "we retried four hundred payments."

**Then click a `Recovered` one** to show a multi-step timeline — diagnosis,
scheduled action, outcome.

---

## Beat 3 · 1:20–1:55 · The guardrails

**Show:** second terminal.

```bash
python scripts/demo.py --beat 4
```

**Say:**

> The agent behaved. But I don't design for that.
>
> Here's the policy layer directly. A model proposes retrying a risk-declined
> payment at 0.91 confidence — plausible reasoning, high-value customer, good
> history. Eight deterministic rules sit between it and the payment rail.

**On screen:**
```
MODEL     proposes retry, confidence 0.91
POLICY    VETO  [R1_RISK_BLOCK]  ->  escalate
```

> The model proposes; the policy disposes. And note we're overriding Razorpay
> here — their docs say the *customer* should try another method, which is fine
> for a human at a checkout. It's a different thing for an automated system to
> re-present a declined transaction at volume. That override is deliberate and
> it's written down.

**Say plainly** (this is a credibility moment, do not skip it):

> In this run the agent never actually proposed a blocked action, so the
> guardrails never fired. They gained twelve rupees. I'll come back to why they
> still matter.

---

## Beat 4 · 1:55–2:25 · The data is real

**Show:** split screen — `sim/taxonomy.py` and the live Razorpay docs page
`razorpay.com/docs/errors/payments/list`. Scroll both.

**Say:**

> Every failure here is a real Razorpay error reason. Sixty-six of them, copied
> from their public error documentation — the reason string, their description,
> and their own recommended next step.
>
> That last column matters more than it looks. Razorpay publishes what *they*
> think you should do about each failure. So the recovery logic isn't my opinion
> about payments — it's a compression of theirs, and you can check it line by
> line.

---

## Beat 4b · 2:25–2:45 · It has touched real Razorpay

**Show:** terminal.

```bash
python scripts/live_probe.py --replay
```

**Say:**

> And it is not a closed loop. This is a real failed payment from Razorpay test
> mode — a real error payload, going through the same code the evaluation uses.
>
> `international_transaction_not_allowed`. Already in my taxonomy, because I
> copied that taxonomy from their docs. The agent called it a compliance block
> and escalated instead of retrying.
>
> Test mode can't give me sixty-six failure reasons with realistic clustering —
> that's what the simulator is for. But it can prove the schema is real.

**Keep this to 20 seconds.** It is a rebuttal, not a feature.

---

## Beat 5 · 2:45–3:10 · The test that can fail

**Show:** terminal.

```bash
python scripts/demo.py --beat 3
```

**Say:**

> The obvious objection to any simulation is that I wrote both the world and the
> agent, so of course the agent wins.
>
> So the agent physically cannot read the answer key — and that's not a promise,
> it's a test that walks the syntax tree of every agent file.
>
> Watch. It injects a forbidden import, runs the suite, and shows the failure.

**On screen:**
```
Normally:                                    5 passed
Now injecting `from sim.world import Truth` ...
  E   AssertionError: The agent must not import the simulator.
  E       agent/observe.py:38 imports from sim.world
  FAILED test_observability_boundary.py::test_agent_never_imports_sim
Restored:                                   64 passed
```

> A test that can't fail proves nothing. That one can.

---

## Beat 6 · 3:10–3:50 · The money

**Show:** Evidence tab → *Net recovery by policy*, then *Where the money went*.

**Say:**

> Five policies, the same hundred and fifty payments, the same seed. The
> randomness is keyed per decision, so every policy faces identical luck at
> identical moments — a policy that wins, won on judgement, not on dice.
>
> The opponent that matters is the rules engine. That's Razorpay's own published
> guidance, competently implemented, reading the same rulebook the agent gets.
> Beating a strawman would prove nothing.
>
> The agent nets forty thousand eight hundred. The rules engine, thirty-seven
> four. About nine percent, net of every cost of chasing.

**Then point at `naive_llm`:**

> Now the one I care about. This is an LLM, the error string, and "recover the
> revenue" — the typical build. **Same model as my agent.**
>
> It recovered *more* gross than anyone. Forty-six thousand.
>
> Then it burned eighteen thousand in goodwill and forty-nine thousand in churn,
> committed six hundred and three policy violations, and never stopped.
>
> Net: **minus thirty-four thousand.** Measured properly, the obvious approach
> is worse than doing nothing at all. The difference isn't the model — it's the
> economics, the stopping rules and the guardrails.

---

## Beat 7 · 3:50–4:25 · Where the result breaks

**Show:** Evidence tab → sensitivity grid. Point at the exposure block.

**Say:**

> I swept thirty-six combinations of the three softest assumptions in the model.
> The agent wins thirty-three. It loses three, all at one setting — where
> annoying a customer costs ten times my estimate. At that price messaging stops
> paying, and a policy that never contacts anyone wins. That's a real condition
> under which my approach would be wrong.
>
> But look at the bottom block. Compliance exposure at zero, one times, five
> times — and the agent's number doesn't move. Forty thousand eight hundred and
> forty-nine, all three.
>
> Because it commits zero violations, exposure is a cost it never incurs. Every
> other policy swings with the price of a compliance breach. Ours has none to
> price.
>
> That's what the guardrails bought. Not a better average — an entire axis of
> regulatory risk made inapplicable.
>
> I also can't defend the *size* of the lift. Across that grid it ranges from
> minus nineteen percent to plus eleven. So I quote no lift number anywhere in
> this project.

---

## Beat 8 · 4:25–5:00 · What broke

**Show:** README → *What we got wrong*.

**Say:**

> Eight bugs worth mentioning. All found by a number looking wrong, not by a
> test.
>
> The worst: my cost model concluded that ignoring fraud was profitable —
> because I'd priced what escalation costs without pricing what it avoids.
> Retrying a fraud block three times cost one rupee fifty. Routing it to a human
> cost forty-five.
>
> The one that flipped my results: silent retries were recovering customers who
> had abandoned a 3DS checkout. There's no stored credential and nobody at the
> auth screen — which is the entire reason nudges exist.
>
> And one I shipped twice. I made quota exhaustion abort loudly instead of
> silently degrading — then added a second fatal error that inherited from the
> recoverable base class, so the policy layer swallowed it again. Both now sit
> under one type.
>
> What I wouldn't ship: held-out reasons are three samples. Ambiguous cases,
> ten — and there the agent ties the lookup table, so the contextual inference I
> predicted isn't demonstrated. The customer model is the least grounded thing
> here. All of that is in the repo.
>
> This is a decision-quality benchmark, not a revenue forecast. Thanks for
> watching.

---

## Pre-flight checklist

- [ ] Runs from a clean clone with the README commands
- [ ] Boundary test shown **failing**, then passing
- [ ] `naive_llm` net **−₹34,119** stated out loud
- [ ] The exposure block called out — the agent's number not moving is the point
- [ ] At least one concrete bug described with its *cause*
- [ ] The guardrails-gained-₹12 admission included
- [ ] The n=3 / n=10 limitation stated
- [ ] **No lift percentage quoted**
- [ ] The live Razorpay payload shown (beat 4b)
- [ ] Under 5:00

## If you have to cut

Drop **Beat 4** (the taxonomy split-screen) first — it's the most replaceable,
and the README covers it.

**Never cut Beat 8.** "What broke, and how you got out" is the last question on
their form and the first thing they read. It is also the beat nobody else will
have.
