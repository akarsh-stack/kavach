# Open issues

Live list of known weaknesses. Kept in the repo deliberately — a reviewer
finding a problem we have already written down is a different conversation from
a reviewer finding one we hid.

## Blocking submission

- [ ] **The Anthropic API path has never executed.** `ANTHROPIC_API_KEY` in the
  dev environment is a 54-char key with an `fe_oa_e` prefix, which is not an
  Anthropic key (those start with `sk-ant-`), and it returns
  `401 invalid x-api-key`. No `ant` CLI profile is present either. Everything
  downstream therefore runs on `StubClient`, whose output is **not a model** and
  must never be reported as one. `agent/llm.py` is written against the current
  documented SDK surface but is unverified against a live endpoint. First thing
  to do once a real key exists: run `scripts/smoke_llm.py`. — *owner: blocked on
  credentials*

- [ ] **NPCI figures are second-hand.** `sim/issuers.py` per-bank decline rates
  come from secondary reporting of an older NPCI snapshot. Must be replaced with
  a specific month's official file from the NPCI BD/TD & Uptime dashboard, cited
  by filename and month. Until then `docs/CALIBRATION.md` §2.4 carries a
  warning. — *owner: day 3*

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
