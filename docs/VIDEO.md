# The 5-minute video

## What this video has to do

Judges are watching a lot of these. Most will be *"I built an agent that retries
payments, look, it retried a payment."* Our advantage is not the agent — it is
that we can **prove** things about it. So the video is structured to make one
argument, in this order:

> The problem is real → our data is real → the system works → here is the money
> → **and here is where our own result breaks.**

The last beat is the one nobody else will do, and it is the one that gets a call
back. Do not cut it for time. Cut something else.

**Tone: flat and factual.** No hype adjectives, no "revolutionary". The material
is strong enough that overselling it actively hurts — an engineer watching will
trust a person who says "this is what we cannot prove" far more than one who
says "this changes everything."

---

## Before you record

```bash
# 1. Fresh, verified data
python scripts/inspect_batch.py
python scripts/run_eval.py --engine ollama --limit 300     # or --no-llm
python scripts/run_sensitivity.py --limit 300

# 2. Tests green on camera
python -m pytest tests/ -q

# 3. Dashboard up
cd web && npm run dev
```

Terminal: dark theme, font size ~16pt, window ~110 columns. Browser at 1440×900,
zoom 110%. Close every other tab. Turn off notifications.

Record at 1080p minimum. Screen capture with a voiceover recorded separately is
easier to get right than live narration.

---

## Shot list

### 0:00 – 0:25 · The problem, in one number

**Screen:** terminal, run `python scripts/inspect_batch.py`

**Say:**
> A mid-size Indian merchant on Razorpay. Twenty thousand payment attempts in a
> month, and a 92% success rate — which sounds fine until you count what the
> other 8% is worth. Fifteen hundred failed payments. Four and a half lakh
> rupees sitting there.
>
> Some of that is genuinely unrecoverable. Most of it isn't. The question is
> which, and what it costs to find out.

**Show:** the calibration block — success rate, value at risk, recoverable.

---

### 0:25 – 1:00 · The data is real

**Screen:** split — `sim/taxonomy.py` on the left, the actual Razorpay docs page
(`razorpay.com/docs/errors/payments/list/`) on the right. Scroll both together.

**Say:**
> Every failure in this simulation is a real Razorpay error reason. Sixty-six of
> them, copied from their public error documentation — the reason string, their
> description, and their own recommended next step.
>
> That last column matters more than it looks. Razorpay publishes what *they*
> think you should do about each failure. So the recovery logic here isn't my
> opinion about payments. It's a compression of theirs, and you can check it
> against their docs line by line.

**Show:** hover one row — e.g. `card_expired` → "The customer must retry with a
valid card" — in both windows at once.

---

### 1:00 – 1:35 · The agent cannot see the answer

**Screen:** `tests/test_observability_boundary.py`, then run the test.

**Say:**
> The obvious objection to any simulation is that I wrote both the world and the
> agent, so of course the agent wins. So the agent physically cannot read the
> answer key. The true failure cause lives in a private dictionary the agent has
> no reference to.
>
> And that's not a promise, it's a test. This walks the syntax tree of every
> agent file and fails the build on any import from the simulator.

**Do this on camera** — it is 15 seconds and it is worth it. Add
`from sim.world import Truth` to `agent/observe.py`, save, re-run:

```
E  AssertionError: The agent must not import the simulator.
E      agent/observe.py:38 imports from sim.world
FAILED tests/test_observability_boundary.py::test_agent_never_imports_sim
```

Undo it, re-run, 20 passed.

> A test that can't fail proves nothing. That one can.

---

### 1:35 – 2:10 · One payment, end to end

**Screen:** dashboard → Audit trail card. Pick one entry and zoom.

**Say:**
> Here's a single decision. The payment failed with `bank_technical_error`. The
> agent diagnosed a transient fault, and rather than retrying immediately —
> which fails, because the bank is still down — it scheduled a retry for thirty
> minutes out.
>
> Diagnosis, proposed action, ruling, result. Every decision in the batch has
> this record.

---

### 2:10 – 2:45 · The veto — *the moment*

**Screen:** Audit trail, filtered to overruled decisions. Zoom on an
`R1_RISK_BLOCK` row.

**Say:**
> This is the part I care most about.
>
> The model looked at this payment and proposed a retry, with high confidence.
> The payment had been declined by Razorpay's risk checks. Re-presenting a risk
> decline at volume is what gets a merchant's account reviewed.
>
> So the policy layer vetoed it. The model proposes; the policy disposes.
>
> Every one of these is logged. And that changes what you can say to a risk
> team. Not "we retried four hundred payments" — but "we declined to retry these
> sixty-two, and here's the rule that stopped each one."

> Note that we're overriding Razorpay here. Their docs say the *customer* should
> try another method, which is fine for a human at a checkout. It's a different
> thing for an automated system to re-present a declined transaction. That
> override is deliberate, and it's written down.

---

### 2:45 – 3:25 · The money

**Screen:** dashboard → Net recovery by policy, then Where the money went.

**Say:**
> Five policies, the same three hundred payments, the same random seed. And the
> randomness is keyed per decision, so every policy faces identical luck at
> identical moments — a policy that wins, won on judgement, not on dice.
>
> The opponent that matters is the rules engine. That's Razorpay's own published
> guidance, competently implemented, reading the same rulebook the agent gets.
> Beating a strawman would prove nothing.

**Show:** the cost breakdown — point at the compliance-exposure segment on
`fixed_retry`.

---

### 3:25 – 4:00 · The uncomfortable finding

**Screen:** stay on the bar chart. Point at the dashed outlines.

**Say:**
> Now look at the second column before the first.
>
> `direct` is what a merchant sees on their own P&L. And on that number, the
> dumb retry loop *wins*. It recovers more than the rules engine does.
>
> It only loses once you price the ninety-six risk declines it re-presented.
>
> That's not a bug in my favour. That is why naive retry loops are everywhere —
> they genuinely look better on the number your CFO reads. The case for doing it
> properly is a compliance case, not a revenue case, and I'd rather say that
> than pretend otherwise.

---

### 4:00 – 4:35 · Where my own result breaks

**Screen:** dashboard → sensitivity grid. Point at the `exposure ×0` column.

**Say:**
> So I swept it. Thirty-six combinations of the three softest assumptions in the
> model — recovery probabilities, the cost of annoying a customer, and that
> compliance exposure.
>
> The rules engine wins twenty-seven of thirty-six. It loses nine. And eight of
> those nine are this one column — where I set compliance exposure to zero.
>
> So the honest claim is conditional: doing it properly beats the dumb loop
> **provided** re-presenting a risk decline costs anything at all. If it's
> genuinely free, run the dumb loop.
>
> I also can't defend the size of the lift. Across that grid it ranges from
> minus twenty-four percent to plus seven hundred. So I don't quote a lift
> number anywhere in this project.

---

### 4:35 – 5:00 · What broke, and what I'd not ship

**Screen:** README → "What we got wrong".

**Say:**
> Four bugs worth mentioning, all found by numbers looking wrong rather than by
> a test.
>
> The worst one: my cost model concluded that ignoring fraud declines was
> profitable — because I'd priced what escalation costs without pricing what it
> avoids. Retrying a fraud block three times cost one rupee fifty. Routing it to
> a human cost forty-five.
>
> The one that actually flipped my results: silent retries were recovering
> customers who'd abandoned a 3DS checkout. There's no stored credential and
> nobody at the auth screen — which is the entire reason nudges exist.
>
> What I wouldn't ship: the customer behaviour model is the least grounded thing
> here, and the recovery probabilities are assumptions. Their ordering is
> defensible. Their exact values aren't, and the repo says so.
>
> This is a decision-quality benchmark, not a revenue forecast. Thanks for
> watching.

---

## If the model backend is running

Swap the 1:35–2:10 beat for a live decision. Run:

```bash
python scripts/run_eval.py --engine ollama --limit 150 --audit agent --audit-blocked
```

and narrate a real `payment_failed` case — the reason Razorpay documents as
carrying no gateway error code — where the agent infers the cause from a failure
cluster the downtime feed hasn't reported yet. That is the single most
impressive thing the system does, because a lookup table structurally cannot do
it.

Also add, at 2:45:

> Held-out reasons and ambiguous ones are scored separately, because overall
> accuracy is meaningless here — it's dominated by documented reasons where a
> lookup table scores a hundred percent by construction.

---

## Checklist

- [ ] Runs from a clean clone with the README's commands
- [ ] Tests pass on camera, and the boundary test is shown *failing* then passing
- [ ] At least one `R1_RISK_BLOCK` veto visible and readable
- [ ] Net vs direct explained — the whole argument is in that gap
- [ ] Sensitivity grid shown with the losing column called out **by name**
- [ ] A concrete bug described, with why it was wrong
- [ ] No lift percentage quoted anywhere
- [ ] If a stub run is on screen, the warning banner is visible and mentioned
- [ ] Under 5:00
