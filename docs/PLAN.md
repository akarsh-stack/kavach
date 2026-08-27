# Build plan — 12 days to 5 September

> **Historical.** This is the plan as written at the start, kept so the
> difference between what was intended and what was built stays visible. It is
> not the current state of the project — for that, see
> [`STATE.md`](STATE.md), and for what is still wrong,
> [`OPEN_ISSUES.md`](OPEN_ISSUES.md). Notably, this document decided *against*
> real Razorpay API calls; that was reversed and the live probe was built.

Track 03, AI Revenue Recovery. Deliverables per the brief: **public repo,
5-minute pitch video, architecture.**

## The one-sentence pitch

An agent that takes a batch of failed Razorpay payments, diagnoses each root
cause from the real error taxonomy, chooses a bounded recovery action, and
proves it recovered more money than four competing policies — *net of the cost
of trying*.

## The bar we are being judged against

> "Don't just identify the problem. Show measured money recovered across a
> batch, with compliant escalation, stopping rules, and an audit trail."

Four requirements. Each maps to a component:

| Brief requirement | Component |
|---|---|
| measured money recovered across a batch | `eval/harness.py` — 5 policies, same seed, net of costs |
| compliant escalation | `agent/policy.py` — escalation ladder, human handoff |
| stopping rules | `agent/policy.py` — hard vetoes, caps, quiet hours |
| audit trail | `agent/audit.py` — every decision, input, veto and cost |

## Architecture

```
sim/                      the world. agent/ may never import this.
  taxonomy.py    [DONE]   66 real Razorpay error reasons
  issuers.py              per-bank health, calibrated to NPCI TD/BD
  customers.py            population w/ intent, liquidity cycle, patience
  world.py                hidden state; resolves outcomes of actions
  generate.py             deterministic batch generation from a seed

agent/
  observe.py              THE BOUNDARY. Only merchant-visible fields.
  diagnose.py             LLM root-cause inference -> RecoveryClass + confidence
  decide.py               intervention, timing, channel
  policy.py               guardrails. Can veto the model. Vetoes are logged.
  tools.py                retry / switch_rail / nudge / escalate / stop
  audit.py                append-only decision log
  loop.py                 the batch loop

economics/
  costs.py                retry fees, comms cost, human time, annoyance

eval/
  baselines.py            no_retry | fixed_retry | rules_engine | naive_llm
  harness.py              run all policies over one batch
  sensitivity.py          grid over Tier-3 assumptions
  report.py               metrics, lift, honest failure list

dashboard/                the 5 minutes of video
tests/
  test_observability_boundary.py   fails build if agent/ imports sim/
```

## The five policies

1. **`no_retry`** — floor. Do nothing, lose everything.
2. **`fixed_retry`** — what most merchants actually run: retry everything 3×
   with backoff. Burns money on `card_expired` and, worse, on
   `payment_risk_check_failed`.
3. **`rules_engine`** — Razorpay's own documented "Next Steps", competently
   implemented. **The real opponent.** No mapping for the 10 held-out reasons.
4. **`naive_llm`** — an LLM with the error string and "recover this payment",
   no guardrails, no cost awareness. The typical hackathon submission. Expected
   to look good on gross recovery and bad on net, and to violate policy.
5. **`agent`** — ours. Diagnosis + cost-aware decision + hard guardrails.

Reporting `naive_llm` beating us on gross recovery while losing on net is a
*feature* of the story, not an embarrassment.

## Headline metrics

- **Net recovered (₹)** ← the headline. Gross minus all costs.
- Lift over best baseline (%) — the claim that survives bad assumptions
- Recovery rate on recoverable value
- Wasted attempts (retries on `HARD_STOP` / `MERCHANT_FIX` / dead instruments)
- **Policy violations — must be 0**
- Escalations to human
- Accuracy on held-out reasons vs. seen reasons (does the LLM generalise?)
- Accuracy on `payment_failed` (does context inference work?)

## Day plan

| Days | Work |
|---|---|
| 1–3 | `sim/` complete + calibration verified against a primary NPCI file |
| 4–6 | `agent/` — diagnose, decide, policy, tools, audit |
| 7–8 | `eval/` — baselines, harness, sensitivity |
| 9–10 | dashboard |
| 11 | run experiments, write the honest failure analysis |
| 12 | video + README + architecture doc |

## Video structure (5:00)

| Time | Beat |
|---|---|
| 0:00–0:30 | The problem: a batch of 200 failed payments, ₹X at risk |
| 0:30–1:15 | The taxonomy is real. Show the Razorpay docs page side by side. |
| 1:15–2:15 | Watch one payment: diagnose → decide → act → audit line |
| 2:15–2:45 | **The veto.** Model proposes retrying a risk-declined payment; policy layer blocks it. |
| 2:45–3:45 | The five-policy comparison. Net recovered. The lift. |
| 3:45–4:20 | Sensitivity: the lift holds across the assumption grid |
| 4:20–5:00 | **What it gets wrong** — the 7 failures, and why. What we would not ship. |

That last beat is the one nobody else will do.

## Non-goals

Explicitly out of scope, stated so scope creep has a wall to hit:

- No real Razorpay API calls. Test-mode integration is a demo-day dependency we
  refuse to take. (If time remains on day 11, a thin adapter proving the tool
  layer *could* call the real API is a stretch goal — not a commitment.)
- No live customer messaging. Nudges are simulated.
- No fine-tuning. Prompted models only.
- No multi-merchant tenancy, auth, or deployment.
