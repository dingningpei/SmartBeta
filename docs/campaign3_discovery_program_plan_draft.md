# Campaign-3 — first real discovery run: program plan (DRAFT for review; nothing frozen or authorized)

- **Status:** planner draft, 2026-10-09. The user chose option (b): a small, budgeted,
  generator-driven discovery run with a positive control, instead of testing known
  anomalies only.
- **Purpose:** run the **whole discovery chain once, for real**:

  > generator proposes → every candidate registered → P-Q-certified evaluation on
  > development data → multiplicity-honest screen → ≤ m candidates confirmed once on the
  > sealed reserve.

- **What a result means:**
  - a confirmed candidate is the project's first piece of information about alpha that
    is not data-mined;
  - a null result is informative too, given the power analysis in §5.

## 1. Prerequisites (each has its own freeze or authorization)

| # | Item | What it delivers | Depends on |
|---|---|---|---|
| P1 | **M1b freeze** (event sensitivity semantics; the draft exists) | typed causes for unmapped / contradicted identity, reserve, and the K0/K1 timing scenario | ADM-13 facts (done: 15,759 boundary-flag events) |
| P2 | **P-Q v2.3 amendment:** an event grammar `EVENT_G` | certified event expressions (§2) | P1 |
| P3 | **Track P** (PA-0 → P10-P → PA): admit the first InferenceProcedure | the ability to make a confirmatory claim on the reserve | — (parallel with P1/P2) |
| P4 | **M4 generator integration:** the Phase-9 loop on v1.2 + v1.3 with the v2.2/v2.3 grammars | budgeted, fully registered candidate generation | P2 |
| P5 | **Campaign-3 precommitment + registration** | the frozen budget, screen, forwarding rule, positive control and power analysis | P1–P4 |
| P7 | **Reserve-fold data path** (identified while drafting PA-0): reserve-period D7a/D7c returns and the P-Q treatment of the reserve, admitted before any confirmation. P-Q v2.x currently certifies the development period only | the confirmation series | — (design needed) |
| P6 | **Implementation + execution** | the development screen, then a one-shot reserve confirmation | P5, P7 |

## 2. P-Q v2.3 event grammar (sketch, for design)

**Event primitives** (from v1.3, development only; `EA_8K202` with K(e) present):

| Primitive | Definition |
|---|---|
| `ear(f)` | the latest event's announcement-window return. TRI ratio over sessions `[K(e)−1 → K(e)+1]`, for the latest event with K(e)+1 ≤ date(f−1) |
| `days_since_ea(f)` | sessions since that event's K(e) |
| `has_recent_ea(f, w)` | 1 if an event's K(e) lies within the last w sessions |

**Composition:** event primitives combine with SIMPLE_G price/volume terms under the
same denominator, bound and own-cell rules. There is no cross-sectional event node.

**Certification idea** (to be proven in the amendment):
- the event dates are **data-independent of prices** (SEC acceptance times), so the TRI
  window they select is a fixed algebraic window per (key, f);
- Props. 2 and 4 then apply unchanged;
- the M1b causes cover identity, timing and reserve.

**Open design point:** whether `FALLBACK` events (10-Q/10-K without an 8-K 2.02) are
admitted. Proposed: not in v2.3 (`EA_8K202` only).

## 3. Discovery design (for the Campaign-3 precommitment)

1. **Universe:** **TOP_1000** (mid caps, where anomalies are larger). Requires a
   cost-model review; 10 bps is likely too low for the smaller names.
2. **Generator:** the Phase-9 loop.
   - It proposes FactorSpecs inside SIMPLE_G ∪ STD_X_RANK_G ∪ EVENT_G.
   - **Budget N** candidates (decision D1).
   - Every proposal is `PROPOSAL_RECORDED` in K-PIT (GENERATOR channel) **before**
     evaluation.
   - Non-novel and invalid candidates are rejected only by non-empirical checks.
3. **Positive control:** the classic EAR drift (`ear` held 1 month, sign +1) is
   registered via the HUMAN channel.
   - If the control fails the development screen, the run reports a **pipeline /
     power warning**, not "no alpha exists".
   - The control is **not** eligible for the reserve slot unless it qualifies like any
     other candidate.
4. **Development screen:**
   - every candidate is evaluated with P-Q v2.2/v2.3 (T0 and TS both);
   - the multiplicity-adjusted threshold across **all N candidates plus the control**
     is fixed before generation (Holm or Bonferroni over N+1; decision in P5);
   - NOT_ASSESSED candidates count toward N, but cannot advance.
5. **Forwarding:** at most **m** survivors, ranked by a precommitted rule (for example
   the largest lower-envelope t), go to the reserve.
6. **Reserve confirmation:**
   - a single use of 2023-07-03 … C (about 3.1 years) with the admitted
     InferenceProcedure (P3);
   - Holm over m;
   - the reserve is consumed for these m hypotheses permanently.

## 4. Data exposure and honesty constraints

- **Development:** the generator sees development **results** of prior candidates. That
  is allowed and recorded, which is the whole point of history-aware generation; every
  look is counted in N.
- **Reserve:** never visible to the generator or the screen (firewall).
- **Disclosures carried:**
  - the C2 re-use disclosure, if C1 expressions reappear;
  - the v1.3 EV-T exposure disclosure.

## 5. Power (pure arithmetic, recorded before any data is used)

Reserve confirmation, about 3.1 years of monthly net long-short, one-sided α = 0.05
Holm-adjusted:

| m forwarded | Annual Sharpe needed for 50% power | for 80% power |
|---|---|---|
| 1 | **0.93** | 1.41 |
| 3 | 1.21 | 1.69 |

- **What this means:** the reserve can only confirm **strong** effects. A genuine factor
  with a net Sharpe of about 0.5 would most likely **not** be confirmed. This is a
  property of the data length, not of the method.
- **Hence the recommendation of m = 1:** forward only the single best survivor. The
  report must state this power limit verbatim, whatever the outcome.

## 6. Decisions needed from you (before the P5 freeze; these can wait until P1–P4 progress)

| ID | Decision | Recommendation |
|---|---|---|
| D1 | discovery budget N | **100** (enough to explore; the Holm threshold for 101 tests is about z ≈ 3.3 on development) |
| D2 | universe | **TOP_1000**, with a cost-model review |
| D3 | m forwarded to the reserve | **1** (power, §5) |
| D4 | generator model (cost/billing) | DeepSeek `deepseek-v4-flash` (the current Pi pair) or Claude; your call |
| D5 | `FALLBACK` events in EVENT_G | exclude in v2.3 |

## 7. Immediate next steps (proposed for authorization now)

- **(i)** Freeze M1b, with its boundary window confirmed against the ADM-13 structural
  count (15,759 flagged events; no returns used).
- **(ii)** Draft the P-Q v2.3 EVENT_G amendment (document-only).
- **(iii)** Draft the Track P PA-0 (InferenceProcedure admission plan), in parallel.

Each stops at its own freeze for your review.
