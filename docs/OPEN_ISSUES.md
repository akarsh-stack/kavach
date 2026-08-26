# Open issues

Live list of known weaknesses. Kept in the repo deliberately — a reviewer
finding a problem we have already written down is a different conversation from
a reviewer finding one we hid.

## Blocking submission

- [x] ~~**No model has ever run a full evaluation.**~~ Resolved: gpt-oss:120b
  via Ollama Cloud produced the committed results, and the decision cache makes
  them reproducible without credentials.

- [ ] **The Anthropic API path has still never executed.** Not blocking -- the
  headline numbers come from Ollama -- but `agent/llm.py` remains unverified
  against a live Anthropic endpoint. `scripts/smoke_llm.py` checks it in ~90s
  if a key ever appears.

- [ ] **Held-out and ambiguous sample sizes are too small to conclude from.**
  n=3 and n=7 on a 150-payment batch. The agent's 66.7% on held-out reasons is
  2 of 3. Extending to 300+ is the single highest-value next run, and the cache
  makes the first 150 free.

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

## Decided against

- Real Razorpay test-mode API calls. Adds a demo-day failure mode for no gain
  in the thing being measured — decision quality. Noted as a stretch goal in
  PLAN.md, not a commitment.
- Voice/Hinglish recovery channel. Demo-fragile, and splits focus away from the
  measurement story that the brief actually asks for.
