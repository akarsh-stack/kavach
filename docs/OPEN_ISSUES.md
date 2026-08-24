# Open issues

Live list of known weaknesses. Kept in the repo deliberately — a reviewer
finding a problem we have already written down is a different conversation from
a reviewer finding one we hid.

## Blocking submission

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
