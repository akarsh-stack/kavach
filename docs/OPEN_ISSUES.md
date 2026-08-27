# Open issues

Live list of known weaknesses. Kept in the repo deliberately — a reviewer
finding a problem we have already written down is a different conversation from
a reviewer finding one we hid.

## Blocking submission

- [x] ~~**No model has ever run a full evaluation.**~~ Resolved: gpt-oss:120b
  via Ollama Cloud produced the committed results, and the decision cache makes
  them reproducible without credentials.

- [x] ~~**The Anthropic API path has never executed.**~~ Partly resolved. It has
  now been exercised end to end against the live endpoint and returns
  `credit balance too low` — so auth, request shape and the *error* path are
  verified, and the dashboard degrades correctly (log header reads `aborted`,
  banner carries the reason, previous results are left untouched). What remains
  unverified is a **successful** Anthropic completion, which needs a funded key.

- [ ] **Held-out and ambiguous sample sizes are too small to conclude from.**
  n=3 held-out and n=10 ambiguous on a 150-payment batch. The agent's 66.7% on
  held-out reasons is 2 of 3. Extending to 300+ is the single highest-value next
  run, and the cache makes the first 150 free.

- [x] ~~**NPCI figures are second-hand.**~~ Resolved by removing them. NPCI
  blocks automated access (403 on fetch, 404 redirect in a browser), so the
  primary file could not be obtained. Rather than ship secondary reporting
  dressed as sourced, per-bank precision is gone: rates now derive from the
  OC-149 ceilings (TD<1%, BD<5%) plus a stated tier position marked Tier 3.
  See `docs/CALIBRATION.md` §2.4.

## Known limitations, will ship with them

- [ ] `base_recovery_prob` values are assumptions. Mitigated by sensitivity
  analysis, not resolved. No public dataset exists to fix this.
- [ ] Customer behaviour model is the least grounded component in the project.
- [ ] No festival/seasonal load modelling, despite it being a real driver of
  Indian payment failure rates.
- [ ] `bank_technical_error` is mapped to `RETRY_SAME` against Razorpay's
  documented next step. Judgement call, argued in CALIBRATION §3.
- [ ] `payment_risk_check_failed` is mapped to `HARD_STOP`, overriding
  Razorpay's documented next step on risk-management grounds. Deliberate.
- [ ] **The guardrails lose money on this batch.** The ablation nets ₹40,880
  against the agent's ₹40,849: the policy layer cost ₹31 and prevented one
  violation, and vetoed nothing. The argument for it is the compliance-exposure
  axis and `naive_llm`, not this run's P&L. Stated plainly in the README rather
  than buried.

## Decided against

- ~~Real Razorpay test-mode API calls.~~ **Reversed — this was built.**
  `scripts/live_probe.py` fetches a genuine test-mode failure
  (`pay_TUORtBOxc2nlNA`, `international_transaction_not_allowed`) and pushes it
  through the same `Observation` the evaluation uses. The demo-day failure mode
  was avoided by committing the payload and adding `--replay`, so the beat runs
  offline. It sits outside the evaluation path, so a network failure cannot
  affect the reproducible numbers.
- Voice/Hinglish recovery channel. Demo-fragile, and splits focus away from the
  measurement story that the brief actually asks for.
