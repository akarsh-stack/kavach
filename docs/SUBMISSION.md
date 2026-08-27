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

**Kavach** — कवच

*(if the field allows a subtitle)*

> **Kavach** — bounded revenue recovery

*Kavach* is Sanskrit for armour. The policy layer is the armour: the model
proposes a recovery action, and eight ordered rules allow, defer or veto it
before it reaches a customer or a card network. The name is the argument.

---

## 9 · What it solves

*(short version — use this)*

> A merchant on Razorpay loses ~7.5% of payment attempts. Some of that is
> genuinely unrecoverable and some is a bank having a bad ten minutes, and the
> difference is worth lakhs — but chasing it wrong costs more than it recovers.
>
> Kavach is an agent that works a queue of failed payments: diagnoses each root
> cause from Razorpay's real error taxonomy, picks a bounded recovery action,
> escalates what needs a human, and stops when stopping is right. Then it proves
> it against four competing policies, net of the cost of trying.
>
> The result that matters: the same model with no guardrails and no economics —
> the obvious build — recovers *more gross* (₹46,738 against the agent's
> ₹43,862) and finishes **net negative at −₹34,119**, because it burns goodwill
> and churn chasing money it should have left alone. The agent nets ₹40,849 with
> zero policy violations.

*(one-liner, if the field is short)*

> An agent that recovers revenue from failed Razorpay payments — and an
> evaluation proving it beats Razorpay's own published guidance by 9.1% net,
> while an unguarded LLM on the same model goes net negative.

---

## 10 · GitHub repo URL, public

`https://github.com/<you>/kavach`

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
> Three others worth naming, because they are all the same failure in different
> clothes — **the thing I believed was never the thing I had checked.**
>
> **My README claimed a reviewer could reproduce my numbers without an API key,
> and that was false.** Without credentials the client fell back to a stub whose
> model name can't match the cache, so it silently recomputed every decision
> with a heuristic and printed ₹35,919 against my published ₹39,420 — with
> nothing on screen indicating a problem. I found it by cloning my own repo to
> /tmp and reading the engine column. There's now a replay client that serves
> recorded decisions or fails loudly, and a replay *miss* is fatal.
>
> **My guardrail was working and my dashboard showed no sign of it.** The
> console decided an intervention had happened by comparing the proposed action
> to the final one — but a deferral changes an action's *timing*, not its
> identity, so all 28 quiet-hours holds rendered as ordinary allows. The agent
> vetoes nothing, so those deferrals were the only visible evidence that the
> policy layer does anything at all, and the single most demonstrable compliance
> behaviour in the project was sitting in the artifact, unrenderable.
>
> **My demo contradicted itself out loud.** The narrated walkthrough printed
> "rules_engine wins 24/36 … loses at 12" and then, two lines below, said "wins
> 27 and loses 9". Both numbers had been typed in when they were true and never
> touched again. Nothing in the repo could have caught it — it only surfaces
> when somebody reads a whole beat aloud, which is exactly what recording the
> video means. Every figure in the demo is now derived from the run that just
> printed.
>
> One more, on method rather than code: **my first fix for a startup race did
> nothing, because I guessed a status code.** The dashboard was reporting "no
> results — go run this Python script" when the real cause was that the API
> hadn't finished booting. I wrote a retry that triggered on HTTP 502. Vite
> reports a dead proxy target as a plain **500**. I only found out because I
> pointed the proxy at a dead port to watch the fix work and saw the old banner
> appear anyway.
>
> These and eight more — twelve in total — are written up in the README under
> "What we got wrong".
> The pattern is the lesson: **64 tests did not catch a single one of them.**
> Every one came from running the thing and reading the output — cloning to a
> scratch directory, pointing a proxy at nothing, sampling a component mid-mount
> instead of after it settled, reading a script out loud.

*(short version if the field is tight)*

> My cost model concluded that ignoring fraud was profitable — I'd priced what
> escalation costs without pricing what it avoids, so retrying a fraud block
> (₹1.50) beat routing it to a human (₹45) by thirty times. Caught it because the
> ranking looked wrong, not because a test failed.
>
> Worst one: my README claimed anyone could reproduce my numbers without an API
> key. False. Without credentials it fell back to a stub and printed different
> numbers with no warning. Found it by cloning my own repo and reading the engine
> column.
>
> The one that stung most: my guardrail was working and my dashboard showed no
> sign of it — 28 quiet-hours deferrals rendered as ordinary allows, because the
> code tested whether the *action* changed and a deferral only changes its
> *timing*. Twelve bugs are written up in the README. 64 tests caught none of
> them; every one came from running the thing and reading the output.

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

**"Is any of this real, or is it all mock data?"**

> Four different answers, and the distinction matters.
>
> The **payment stream is synthetic** — 20,000 attempts, 1,492 failures,
> generated by `sim/`. It is not real Razorpay traffic and I do not claim it is.
>
> The **failure taxonomy is real**: 66 error reasons, descriptions and
> next-steps transcribed verbatim from Razorpay's published error docs. The
> **decline rates are derived** from NPCI Circular OC-149's ceilings, not
> invented — and the calibration is falsifiable, because the simulated success
> rate has to land inside NPCI's 92–96% band or `verify()` fails and the run
> aborts. It currently lands at 92.54%.
>
> The **model decisions are real**: 1,688 of them, produced by `gpt-oss:120b`
> through a live Ollama Cloud key. They are recorded at temperature 0 and
> committed, so a reviewer with no key replays *recorded real output* rather
> than a mock. Replay is not mock — a mock is invented; this was generated and
> pinned so the published numbers reproduce exactly.
>
> The **cost model is assumption**: ₹45 per escalation, the annoyance cost, the
> recovery probabilities. That is the weakest part of the project, which is why
> there is a 36-point sensitivity sweep and why it reports that the agent loses
> at 3 of them.

**"Where is the AI, exactly?"**

> `agent/decide.py`. The model receives an `Observation` — error reason and
> description, method, issuer, the customer's payment history, downtime signals,
> recent failure clusters — and returns structured JSON: recovery class, action,
> delay, channel, confidence, rationale.
>
> Everything around it is deliberately deterministic: the policy layer, the
> simulator and the ledger contain no model calls. `agent/` is forbidden from
> importing `sim/`, and a test walks the AST to prove it — inject that import and
> the suite fails. Five providers are wired (Ollama, Anthropic, Gemini, Groq,
> plus stub and replay); the committed run used Ollama Cloud.

**"Is the agent actually better?"**

> On this batch, by 9.1% net, holding at 33 of 36 sensitivity grid points. But
> held-out reasons are n=3 and ambiguous cases are n=10 — where it *ties* the
> lookup table. The contextual inference I predicted isn't demonstrated. A larger
> batch is the next thing it needs, and that's in OPEN_ISSUES.

**"So what did the guardrails actually buy you?"** *(ask this of yourself before
they do — the ablation is in the repo and they can run it)*

> On this batch, nothing good. The ablation nets **₹40,880 against the agent's
> ₹40,849** — the policy layer cost ₹31 and prevented exactly one violation. It
> vetoed nothing at all; all 28 interventions were quiet-hours deferrals.
>
> The case for keeping it is not this run's P&L. It's the compliance-exposure
> axis, where the agent's net is the same at ×0, ×1 and ×5 because it has no
> violations to price, while every other policy swings. And it's `naive_llm` —
> the same model, same prompt, guardrails off — at **−₹34,119** with 603
> violations. The guardrails don't raise the average. They bound the worst case,
> and `naive_llm` is what that worst case looks like.

**"What would you do with another week?"**

> Extend to 300+ payments so the held-out and ambiguous slices mean something,
> and model overdue receivables — the brief mentions them and I only cover
> payment failures and part of checkout abandonment.
