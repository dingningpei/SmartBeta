# Campaign-4 forward tier F — design draft (DRAFT for review; nothing frozen; K3, user 2026-10-14)

- **Purpose:** a genuinely out-of-sample tier at zero data cost beyond the existing
  vendor. Hypotheses are registered **now** and tested on formations **after**
  C = 2026-08-31. No current data has seen those sessions.
- **Status:** design only.
  - The K-PIT registration append and every Tiingo acquisition need their own
    authorization.
  - Nothing here touches the reserve (2023-07-03 … C) as an *outcome* tier (§3).

## 1. Timeline (arithmetic)

| Item | Value |
|---|---|
| First forward formation | 2026-09-30 (last NYSE session of September 2026) |
| T ≥ 24 (`EWC_T23_ONE_SIDED` minimum) | formations through **2028-08-31**; the last label needs 21 more sessions → evaluable about **early October 2028** |
| T = 36 | about October 2029 |
| Power (m = 1, one-sided α = 0.05, T = 24) | about 0.40 at a true annual SR of 1.0, by the P5 §2 method. The power statement must be carried verbatim |

## 2. Data path

1. **Acquisition.** Each forward batch is a new Tiingo acquisition under the Dataset v1
   contract's batch and end rule (C′ = the last NYSE session of the last complete month
   before the batch).
   - Proposed cadence: **one batch per year** (about 2027-09 and 2028-10).
   - **Budget:** the v1 acquisition fit inside one Tiingo Power month, which needs
     verifying against the current price. One month per year should fit the ≤ ¥300/yr
     budget, **but the price must be confirmed before each batch.**
2. **Derivation.**
   - The forward fold reuses the fold-generic `reserve_chain` modules (D4/D6/D7a/D7c
     re-expressions, proven byte-equivalent to development).
   - It adds a third source, `forward`, beside `development` and `reserve`.
   - That is a new task (spec later) and needs its own equivalence and gate tests.
3. **Continuity.** The forward batch must reproduce the *existing* batch's rows for
   overlapping dates within a declared tolerance, or the difference is reported. Vendor
   revisions are a known Tiingo behaviour. Rule: the forward tier uses **the new batch
   only**, and overlap differences are a diagnostic, not a correction.

## 3. The reserve-lookback problem (open design question F-Q1)

- **The problem:** factor inputs need up to 504 sessions of lookback. For forward
  formations in 2026–2028, that lookback lies **inside the reserve window** (2024–2026).
- **What is allowed and what is not:**
  - computing a forward-formation factor value *reads* reserve-period prices as inputs;
  - it does **not** compute any reserve-period label, return or statistic;
  - but the P7 gate treats any reserve-package read as reserve access.
- **Options:**
  - (a) A new `ACCESS` scope `FORWARD_LOOKBACK_ONLY`, mechanically enforced: the
    forward pipeline may read reserve-dated **input** rows, and a test proves that no
    label, return, group or statistic with formation ≤ C is ever computed or persisted.
  - (b) Use the forward batch's own copy of 2024–2026 history for lookback, which a new
    acquisition re-downloads anyway. This is the same firewall question in another form:
    reserve-period prices are still read.
  - (c) Restrict F to hypotheses with lookback ≤ (forward formation − C). That is
    impractical; most specs use 252–504 sessions.
- **Recommendation:** (a). The reserve's protected property is **outcome-blindness**,
  and input-only reads under a test-enforced rule preserve it. **This is a methodology
  decision that must be frozen by you.**

## 4. Registration (open design question F-Q2)

- **Content:**
  - a forward precommitment document: hypothesis ids, test procedure (`EWC_T23_ONE_SIDED`,
    B and L under all T0/TS × K0/K1, as P7 decision 4), T_min = 24, m, and the power
    statement;
  - a K-PIT record binding that document's hash, appended **before** 2026-09-30 data is
    acquired. It has no effect before the first batch.
- **Candidates:**
  - `ed358470` (the Campaign-3 forwarded hypothesis; IS minimum L t 0.19). Its IS
    evidence is weak, so a forward test is cheap information, not a promising bet.
  - Hypotheses from M0/M1 can be **added** by a later precommitment, but each must be
    registered before its own first forward formation is acquired.
  - **m and the multiplicity rule** across forward additions need a decision (F-Q3).
    Proposed: Holm over all forward-registered hypotheses at each test date.
- **K-PIT schema:** whether an existing record schema (CAMPAIGN/HYPOTHESIS_FREEZE) can
  carry this without changing a frozen consumer, or a new schema is needed. The planner
  re-greps the frozen consumers before proposing.

## 5. Decisions needed (before any freeze)

| ID | Question | Recommendation |
|---|---|---|
| F-Q1 | reserve lookback | (a) `FORWARD_LOOKBACK_ONLY` access scope, test-enforced |
| F-Q2 | registration vehicle | the planner checks the K-PIT schemas, then proposes |
| F-Q3 | multiplicity across forward hypotheses | Holm over all registered at each test date |
| F-Q4 | batch cadence and cost | yearly; confirm the Tiingo price first |

## Decisions (user, 2026-10-14: "按照你的建议")

| ID | Decision |
|---|---|
| F-Q1 | **(a)** A `FORWARD_LOOKBACK_ONLY` access scope. Reserve-dated rows may be read as **inputs** only. A test enforces that no label, return, group or statistic with formation ≤ C is computed or persisted |
| F-Q2 | The planner checks the K-PIT schemas and frozen consumers, then proposes a registration vehicle for approval |
| F-Q3 | Holm over all forward-registered hypotheses at each test date |
| F-Q4 | One batch per year; the Tiingo price is confirmed before each batch |

**Still open before a design freeze:** the F-Q2 proposal. **Not authorized:** the K-PIT
append and any acquisition.
