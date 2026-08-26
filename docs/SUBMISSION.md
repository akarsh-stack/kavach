# Application form answers

The form asks for 12 things. Six are about you; six are about the build. These
are the six about the build, written to be pasted.

> *"12 answers. About 15 minutes. We still take the resume. We just don't screen
> on it. **The last one is the one we read first.**"*

---

## 7 · Your track

**Track 03 — AI Revenue Recovery**

---

## 8 · Project name

**Bounded Revenue Recovery**

---

## 9 · What it solves

*(short version — use this)*

> A merchant on Razorpay loses ~7.5% of payment attempts. Some of that is
> genuinely unrecoverable and some is a bank having a bad ten minutes, and the
> difference is worth lakhs — but chasing it wrong costs more than it recovers.
>
> This is an agent that works a queue of failed payments: diagnoses each root
> cause from Razorpay's real error taxonomy, picks a bounded recovery action,
> escalates what needs a human, and stops when stopping is right. Then it proves
> it against four competing policies, net of the cost of trying.
>
> The result that matters: the same model with no guardrails and no economics —
> the obvious build — recovers *more gross* and finishes **net negative**, because
> it burns goodwill and churn chasing money it should have left alone.

*(one-liner, if the field is short)*

> An agent that recovers revenue from failed Razorpay payments — and an
> evaluation proving it beats Razorpay's own published guidance by 9.1% net,
> while an unguarded LLM on the same model goes net negative.

---

## 10 · GitHub repo URL, public

`https://github.com/<you>/bounded-revenue-recovery`

**Before you paste this, check a fresh clone runs:**

```bash
git clone <url> /tmp/check && cd /tmp/check
pip install -r requirements.txt
python scripts/run_eval.py --engine ollama --limit 150 --no-ablation
python -m pytest tests/ -q
```

Expect: `agent net Rs 40,849`, `0 live calls`, `64 passed`. No API key needed —
the committed decision cache replays.

---

## 11 · 5-min pitch video

Unlisted YouTube link. Script and shot list: [`docs/VIDEO.md`](VIDEO.md).

---

## 12 · What broke, and how you got out

*(the one they read first — this is the long-form answer; trim to the field)*

> **My cost model concluded that ignoring fraud was profitable.**
>
> I priced what escalation *costs* — fifteen minutes of an analyst, ₹45 — without
> pricing what it *avoids*. So retrying a fraud-declined payment three times cost
> ₹1.50, routing it to a human cost ₹45, and the model cheerfully concluded that
> ignoring the risk layer was thirty times cheaper. The rules engine lost to a
> dumb retry loop and I nearly reported that as a finding.
>
> I only caught it because the *ranking* looked wrong, not because a test failed.
> The fix was to price the compliance exposure of re-presenting a risk decline —
> tracked separately from direct spend, with net reported both ways, because on
> direct P&L the dumb loop genuinely does win. That's why merchants run them.
>
> Two others worth naming:
>
> **Silent retries were recovering abandoned checkouts.** My simulator let a
> silent re-presentment recover a customer who walked away from a 3DS screen.
> There's no stored credential and nobody at the auth page — which is the entire
> reason nudges exist. Fixing it flipped my results.
>
> **I shipped the same bug twice.** I made quota exhaustion abort loudly instead
> of silently degrading every decision to "stop" — then added a second fatal
> error that inherited from the *recoverable* base class, so the policy layer
> swallowed it again. Both now sit under one type, so the policy re-raises a
> class rather than a list somebody has to remember to extend.
>
> And the one that would have been worst: **my README claimed a reviewer could
> reproduce my numbers without an API key, and that was false.** Without
> credentials the client fell back to a stub whose model name can't match the
> cache, so it silently recomputed every decision with a heuristic and printed
> ₹35,919 against my published ₹39,420 — with nothing on screen indicating a
> problem. I found it by cloning my own repo to /tmp and reading the engine
> column. There's now a replay client that serves recorded decisions or fails
> loudly, and a replay *miss* is fatal.
>
> Eight of these are written up in the README under "What we got wrong". Every
> one was found by a number looking wrong, not by a test — which is the actual
> lesson: 64 tests didn't catch a single one of them.

*(short version if the field is tight)*

> My cost model concluded that ignoring fraud was profitable — I'd priced what
> escalation costs without pricing what it avoids, so retrying a fraud block
> (₹1.50) beat routing it to a human (₹45) by thirty times. Caught it because the
> ranking looked wrong, not because a test failed.
>
> Worst one: my README claimed anyone could reproduce my numbers without an API
> key. False. Without credentials it fell back to a stub and printed different
> numbers with no warning. Found it by cloning my own repo and reading the engine
> column. Eight bugs are written up in the README — all found by numbers looking
> wrong, none by the 64 tests.

---

## If they ask in the panel

**"Why simulated data?"**

> Razorpay test mode can't produce the thing the project is about — sixty-six
> failure reasons with realistic clustering, issuer outages on demand, a month of
> correlated failures. So the evaluation runs on a simulator calibrated from
> their published error docs and NPCI's decline ceilings, and the success rate is
> a falsifiable check: it has to land in the 92–96% band or the run fails.
>
> But it does touch real Razorpay — `scripts/live_probe.py` puts a real
> test-mode failure through the same `Observation` the evaluation uses. The live
> reason came back already present in my taxonomy, because I copied the taxonomy
> from their docs.

**"Is the agent actually better?"**

> On this batch, by 9.1% net, holding at 33 of 36 sensitivity grid points. But
> held-out reasons are n=3 and ambiguous cases are n=10 — where it *ties* the
> lookup table. The contextual inference I predicted isn't demonstrated. A larger
> batch is the next thing it needs, and that's in OPEN_ISSUES.

**"What would you do with another week?"**

> Extend to 300+ payments so the held-out and ambiguous slices mean something,
> and model overdue receivables — the brief mentions them and I only cover
> payment failures and part of checkout abandonment.
