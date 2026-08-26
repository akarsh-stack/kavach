# Calibration

> **Why this document exists.** The obvious attack on this project is: *"You wrote
> the simulator and you wrote the agent, so of course your agent wins."* That
> attack is correct against most simulated benchmarks, and we would rather state
> it ourselves than have it discovered. This document is the answer: which
> numbers come from published sources, which are our assumptions, and what we do
> to stop the assumptions from deciding the result.

## 1. The three-tier honesty rule

Every number in `sim/` falls into exactly one tier, and is labelled as such in
the code:

| Tier | Meaning | Where |
|---|---|---|
| **Tier 1 — Transcribed** | Copied verbatim from a public source. Zero authorial freedom. | `reason`, `description`, `next_steps`, `source` enums |
| **Tier 2 — Derived** | A mechanical, stated rule applied to Tier 1 data. Reproducible by a third party from the same source. | `recovery_class` |
| **Tier 3 — Assumed** | Our own estimate. Not measured. | `base_recovery_prob`, customer behaviour, cost model |

**The headline result must survive Tier 3 being wrong.** Section 5 describes the
sensitivity analysis that establishes this. If our conclusion only holds for one
particular setting of the assumed numbers, we report that as a negative result.

---

## 2. Tier 1 — Transcribed sources

### 2.1 Error taxonomy

**Source:** <https://razorpay.com/docs/errors/payments/list/> (retrieved 2026-08-24)

All 66 error reasons in `sim/taxonomy.py` are real, documented Razorpay error
reasons. The `reason` string, the "Detailed Error Description" and the "Next
Steps" text are copied verbatim. We did not invent a single failure mode.

This matters more than it might appear. It means the agent is being asked to
reason about the *actual* strings a Razorpay merchant sees in production — not a
sanitised toy vocabulary — including genuinely awkward ones like
`payment_failed`, which Razorpay itself documents as *"No specific error code
received from gateway in this case."*

### 2.2 The `source` enum

**Source:** <https://razorpay.com/docs/errors/payment-error-parameters>

Razorpay documents a different `source` enum per payment method. We reproduce
this exactly, including the fact that UPI carries values (`customer_psp`,
`network`, `beneficiary_bank`) that cards do not. Razorpay's own docs describe
`source` as the field that "tells you where the error originated and who needs
to act" — which makes it a legitimate agent input, not a leak of hidden state.

### 2.3 Razorpay's documented next steps as ground truth

The single strongest calibration fact available to us: **Razorpay publishes its
own recommended recovery action for every error reason.** The "Next Steps"
column is, in effect, a published recovery policy written by the payments
company we are building for.

This means our `recovery_class` mapping is not our opinion about what should be
done. It is a compression of Razorpay's stated opinion. See section 3.

### 2.4 UPI decline rates

**Citable anchor:** NPCI Circular **OC-149 (June 2022)** — member banks are
expected to hold Technical Decline below **1%** and Business Decline below **5%**.

NPCI's definitions, which map almost exactly onto our taxonomy:

- **Technical Decline (TD)** — failures from bank/NPCI system unavailability.
  Our `RETRY_SAME`.
- **Business Decline (BD)** — failures from user/merchant causes: wrong PIN,
  insufficient balance, limit exceeded, invalid beneficiary. Our
  `RETRY_LATER_FUNDS` + `SWITCH_RAIL`.

**What we do NOT have, and no longer pretend to.** An earlier version of
`sim/issuers.py` carried specific per-bank rates (SBI 0.90%, ICICI 1.01%, Axis
0.60%, HDFC 0.13%) taken from secondary reporting of an older NPCI snapshot. We
attempted to replace them with a named month's official file from NPCI's BD/TD &
Uptime dashboard and could not: the site returns **403** to automated fetches and
redirects the statistics path to a **404** page.

So the figures are gone. Per-bank rates are now *derived* — each bank declares
only a **tier** and a volume share, and TD/BD fall out of the OC-149 ceilings
times a stated tier position:

```
large_private   TD x0.22 of ceiling   BD x0.84
mid_private     TD x0.62              BD x0.92
public_sector   TD x1.15              BD x1.18   (above the TD ceiling)
small_finance   TD x1.65              BD x1.30
```

The tier positions are **Tier 3 assumptions**, marked as such in the code. What
is defensible is the anchor (published), the direction (public sector banks run
materially worse than large private ones — not in dispute), and the fact that
there is no longer a single per-bank number in that file we would have to defend
as a measurement.

This is a weaker claim than the one we started with. It is the true one, and the
model behaves almost identically either way — what changed is whether the README
can honestly say where the numbers came from.

**Cross-check:** the resulting simulated merchant runs a **92.54%** success rate,
inside the 92–96% band the OC-149 ceilings imply for a blended Indian merchant.
`sim/generate.py:verify()` fails the run if it drifts outside.

---

## 3. Tier 2 — The derivation rule for `recovery_class`

We compress Razorpay's free-text "Next Steps" into six classes using a stated
rule applied to the *documented text*, not to our intuition:

| If the documented next step says… | → class |
|---|---|
| "must retry" / "retry after some time", with no instruction to change instrument | `RETRY_SAME` |
| "wait 24 hours", "wait and retry", or names a funds/limit cause | `RETRY_LATER_FUNDS` |
| explicitly names a *different* card, account, bank, VPA, PSP or method | `SWITCH_RAIL` |
| requires the customer to supply something (OTP, CVV, correct details, a new session) | `NUDGE_CUSTOMER` |
| names risk, compliance, eligibility or tampering | `HARD_STOP` |
| addresses the *merchant* ("reach out to Razorpay", "fix your request") | `MERCHANT_FIX` |

A third party with the same Razorpay page should reproduce our mapping. Where
the documented text is genuinely ambiguous we record it and explain the call:

- **`bank_technical_error`** → `RETRY_SAME`, despite the documented next step
  saying "try using another bank account or another method". The description
  clearly describes a *transient* CBS fault, and the recommendation to switch
  banks reads as advice to the end customer standing at a checkout, not as
  guidance to an asynchronous recovery system that can simply wait. We flag this
  as a judgement call; it is the one place we knowingly depart from the
  documented text.
- **`payment_risk_check_failed`** → `HARD_STOP`, despite the documented next step
  saying "retry with a different card or method". We deliberately override
  Razorpay here on risk-management grounds: programmatically retrying a
  risk-declined payment is exactly the behaviour that gets a merchant's MID
  reviewed. A human at a checkout choosing another card is a different act from
  an automated system re-presenting a declined transaction. This override is
  enforced in `agent/policy.py` and is, we think, the correct call — but it *is*
  an override, and we say so.

---

## 4. Tier 3 — Assumptions, stated plainly

### 4.1 `base_recovery_prob`

`P(payment recovered | correct intervention chosen)`. **These are not measured.**
No public dataset gives per-error-reason recovery rates; the payment processors
that have this data do not publish it.

We set them by band, with reasoning:

| Band | Rationale | Example |
|---|---|---|
| 0.85–0.95 | Pure infrastructure faults that self-clear | `server_error` 0.93 |
| 0.70–0.85 | Transient but issuer-dependent | `bank_technical_error` 0.74 |
| 0.60–0.75 | Customer must act, intent demonstrated | `incorrect_otp` 0.74 |
| 0.40–0.60 | Dead instrument; needs the customer to produce a new one | `card_expired` 0.55 |
| 0.0 | Risk, compliance, or our own bug | `payment_risk_check_failed` 0.0 |

The ordering between bands is defensible from first principles. The precise
values inside a band are not, and we do not claim they are.

### 4.2 Customer behaviour

Modelled in `sim/customers.py`. Assumed, with the reasoning recorded in that
file: salary-cycle liquidity (recovery odds for `insufficient_funds` rise near
the 1st and 7th of the month), declining responsiveness to repeated contact, and
per-customer intent that persists across attempts.

### 4.3 Cost model

Modelled in `economics/costs.py`. Retry fees, SMS/WhatsApp costs and human
escalation time are set from public list prices where they exist and are marked
as assumed where they do not. The *annoyance* cost — the long-term value
destroyed by over-contacting a customer — is the softest number in the project
and is the primary target of sensitivity analysis.

---

## 5. What stops the assumptions from deciding the result

Four defences. This is the part that matters.

**5.1 The agent cannot see any of it.** `sim/world.py` holds all hidden state —
true recovery class, true recovery probability, customer intent, issuer status.
`agent/observe.py` exposes only what a real merchant's webhook carries: the
`reason` and `description` strings, amount, method, timestamp, and the
customer's own prior history with this merchant. This boundary is enforced by an
automated test (`tests/test_observability_boundary.py`) that fails the build if
anything under `agent/` imports anything under `sim/`. The agent is solving the
real inference problem, not reading the answer key.

**5.2 We report a lift, not an absolute.** Every policy — no-retry, fixed-retry,
rules-engine, naive-LLM, and our agent — runs against the *same* generated batch
with the *same* seed. If `base_recovery_prob` is globally too generous, every
policy benefits equally and the *ranking* is unchanged. The headline claim is
"agent beats the best baseline by X%", never "the agent recovers ₹Y."

**5.3 The rules engine is a real opponent, not a strawman.** The `rules_engine`
baseline is built directly from Razorpay's documented "Next Steps" — the best
policy anyone could write without machine learning. Our agent has to beat
*Razorpay's own published advice, competently implemented*. Two mechanisms are
designed to make this hard and fair:

- **Held-out reasons.** 10 of the 66 reasons are marked `held_out=True` and the
  rules engine is given no mapping for them, simulating the operational reality
  that new gateway reason strings appear faster than rulebooks get updated. This
  is where an LLM should generalise — and if it does not, that is the finding.
- **Ambiguous reasons.** `payment_failed` carries no usable signal in its reason
  string by Razorpay's own admission. Its true recovery class is resolved
  per-event by the world from context the agent must *infer*. Performance on
  these is reported separately.

**5.4 Sensitivity analysis.** `evaluation/sensitivity.py` re-runs the full
comparison across a 36-point grid of Tier 3 assumptions — recovery probabilities
scaled ±30%, annoyance cost across two orders of magnitude, and compliance
exposure including zero. We report the range of the lift, not a single number.

### 5.4.1 Result for the agent: wins 33 of 36, loses only where messaging stops paying

`gpt-oss:120b` via Ollama Cloud, 150 failed payments, decisions replayed from
the committed cache so this reproduces without credentials.

```
                          no_retry   fixed_retry   rules_engine     agent
recovery prob  x0.7              0        18,603         31,560    33,829
               x1.0              0        33,605         37,437    40,849
               x1.3              0        36,688         44,861    47,831

annoyance cost x0.0              0        33,605         38,541    41,989
               x0.1              0        33,605         38,431    41,875
               x1.0              0        33,605         37,437    40,849
               x10.0             0        33,605         27,501    30,589

compliance     x0.0              0        37,655         37,437    40,849
exposure       x1.0              0        33,605         37,437    40,849
               x5.0              0        17,405         37,437    40,849
```

**The agent wins 33 of 36 points.** The three losses are not scattered:

```
everywhere except annoyance x10 : 27 / 27
at annoyance x10                :  6 /  9
```

All three sit at `annoyance x10` — where over-contacting a customer costs ten
times our estimate. At that price messaging stops paying, and `fixed_retry`,
which never contacts anyone, pulls ahead of every policy that does. A coherent
economic result rather than noise, and it names the condition under which our
approach would be the wrong one.

Note what is *not* sensitive. The agent's net is **identical at every point on
the compliance-exposure axis** — 40,849 at x0, x1 and x5 — because it commits
zero violations, so exposure is a cost it never incurs. The same axis swings
`fixed_retry` from 37,655 down to 17,405.

That is the strongest argument for the guardrails in the project, and it is not
the one we expected. They did not improve the average; they made an entire axis
of regulatory risk **inapplicable**.

**Magnitude remains undefensible.** Across the grid the lift over the best rival
ranges **-18.8% to +11.2%**, so the direction is reportable and the size is not.
We quote no single lift percentage anywhere.

### 5.4.2 Earlier result, non-LLM policies only: the sign flips on exposure

Run on the non-LLM policies (300-failure slice, seed 42):

```
                                no_retry   fixed_retry   rules_engine
recovery prob  x0.7                    0        50,812         96,520
               x1.0                    0       105,696        108,571
               x1.3                    0       113,304        121,011

annoyance cost x0.0                    0       105,696        110,853
               x1.0                    0       105,696        108,571
               x10.0                   0       105,696         88,029

compliance     x0.0                    0       115,596        108,571
exposure       x1.0                    0       105,696        108,571
               x5.0                    0        66,096        108,571
```

`rules_engine` wins 27 of 36 points. **It loses 9, and they are not scattered —
8 of the 9 are the entire `exposure x0` column.**

That is a clean, interpretable finding rather than noise, and it says something
specific: *the case for following Razorpay's documented guidance over a dumb
retry loop rests almost entirely on re-presenting risk declines not being free.*
Set that cost to zero and the naive loop wins on the merchant's own P&L, every
time. The remaining loss is at `annoyance x10`, where messaging customers stops
being worth it and a policy that never messages anyone pulls ahead.

We think a non-zero exposure is obviously correct — card networks levy
excessive-retry fees and acquirers review MIDs over exactly this behaviour — but
we cannot source a point estimate, so the honest statement is conditional:

> Following the documented guidance beats a naive retry loop **provided**
> re-presenting a risk decline carries any real cost at all. If it genuinely
> costs nothing, run the naive loop.

**Magnitude is not defensible, only direction.** Across the grid the lift ranges
from −23.8% to +781.6%. The upper end is an artefact of the denominator
collapsing (at `prob x0.7, exposure x5` the naive loop is barely net-positive,
so any comparison against it explodes). Quoting a single lift percentage from
this project would be misleading, and we do not.

---

## 6. What this still cannot prove

Stated so that nobody has to catch us on it:

- It does not prove the agent recovers real money from real Indian consumers.
  It proves the agent makes better decisions than four alternatives *given a
  world calibrated from Razorpay's and NPCI's published documentation*.
- `base_recovery_prob` values are assumptions. Their *ordering* is defensible;
  their exact values are not.
- The customer behaviour model is the least grounded component. It is informed
  by no data we could verify.
- A real deployment would face adversarial and seasonal effects (festival load,
  bank migrations, regulatory changes) that we do not model at all.

The honest summary: **this is a decision-quality benchmark, not a revenue
forecast.** We think that is still the right thing to build in twelve days, and
we would rather present it accurately than dress it up.
