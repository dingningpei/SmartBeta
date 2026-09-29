# Discovery Dataset v1.1 — D7b Development Delisting-Sensitivity Clarification (NORMATIVE)

- **Status:** normative clarification of the D7b HOLD items, decided by the
  user on 2026-09-28 across four resolutions: B1–B4; sign orientation;
  grouping versus return admissibility; final holdout.
- **Documentation only:** it changes no completed stage (D1–D7a, D-SC) and no
  sealed phase. Phase 7 (P7-A/B/C/D/E/F/G), Phase 8 and Phase 10 are **not**
  modified.
- **It clarifies, without modifying:**
  - `docs/discovery_dataset_v1_contract.md` §§9.2, 9.4, 9.5, 9.6, 10, 11.2 —
    `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`;
  - `docs/discovery_dataset_v1_1_d7_clarification.md` (8efbd96) —
    `bc8e6920d31dd429beb62030123a721a7293558ba57fe8e3d4d6cd6368a96946`;
  - `worker_tasks/phase7/phase7-plan.md` §§5, 6.1, 8.1 —
    `8b2009533ee00e8a04e0c65b6853711f01387592e42f60c0b97170d23183a13e`;
  - `worker_tasks/phase8/phase8-plan.md` §§7.3, 11 (policy and holdout
    governance) —
    `755f57182310d40d5ba3f095bac7e92a9297e62ddc5bf38bb34617cb40f9e32f`.

## 1. Scope: development-only diagnostic

- D7b applies the frozen T0/TS delisting-sensitivity framework (§§9.4–9.6)
  as a **development diagnostic**, completed before any candidate may advance
  to final-holdout use or confirmation.
- D7b is **not** a complete Phase-7 experiment. It does not call
  `engine.evaluate`, build an `EvaluationRecord`, or use a `HoldoutRegistry`.
  It consumes, reads and evaluates **no** final-holdout observation, and no
  Dataset-v1 reserve observation.
- **Final-holdout consumption by D7b is exactly zero.**
- Phase-8 single-use holdout governance is unchanged. There is no scenario
  companion, no paired consumption and no fresh-registry bypass.
- Two boundaries apply independently:
  - the Dataset-v1 reserve (≥ 2023-07-03; D7a's `crosses_reserve` cells are
    `PURGED_RESERVED_OUTCOME`);
  - the Phase-7/Campaign final holdout.
- D7b passing removes one development-stage blocker. It never authorizes
  holdout consumption.

## 2. Campaign evaluation declaration (B1)

D7b defines no primary metric, screen, threshold or accept/reject policy.
Those belong to the Campaign and to Phase-8 governance.

D7b requires a caller-supplied, immutable, hash-bound
`CampaignEvaluationDeclaration`, frozen before any T0/TS outcome is
inspected. It fixes:
- the development fold: exactly one fold of a hash-bound P7-A `Partition`;
- the primary `MetricKey` and the gross/net basis;
- the primary development-screen identity and its policy/threshold
  provenance;
- the FactorSpec identity and the factor-panel hash;
- the universe (top-200/500/1000);
- the horizon h, `n_groups`, winsorization, cost and annualization;
- a precommitment reference.

Consequences:
- A fold that overlaps the final holdout, a holdout-role fold, a missing
  declaration or a domain that is not development-only gives `NOT_ASSESSED`,
  and the candidate cannot advance.
- D7b does not invent Campaign-1 internal splits (§11.2). If none is frozen,
  D7b is not runnable.
- D7b computes **only** the declared metric. It never offers a menu of
  alternatives.
- §9.6(b) screen verdicts are produced by the owning Campaign/Phase-8 screen,
  applied under the **same** screen to the T0 and the TS development
  evidence, and passed back hash-bound. D7b only compares them.

## 3. Membership versus return admissibility (B2)

These are two distinct concepts.
- **Formation membership** comes entirely from the selected D6/D7a universe.
  A member never disappears because of a missing return, a terminal event, an
  incomplete path or later vendor disappearance.
- **The return-admissible sample** holds the members whose frozen return
  construction is valid for the horizon.

A `terminal_affected` and `observed_path_complete = false` member is typed
`TERMINAL_OBSERVED_PATH_INCOMPLETE`:
- it is return-inadmissible under **both** T0 and TS;
- there is no imputation, no gap bridging and no partial compounding;
- the TS long-leg algebra (−100% regardless of R_obs) never makes it
  admissible;
- it stays in membership, affected accounting, B, and E_S/E_L.

A non-terminal incomplete member is typed `OBSERVED_PATH_INCOMPLETE`.

Membership preservation does not imply that every member has a statistically
admissible realized return.

## 4. One grouping; typed NaN representation

- For each formation, universe, FactorSpec and `n_groups` there is exactly
  one factor-value group assignment: `portfolio_sort.assign_groups` on the
  winsorized factor values of the complete membership.
- That one assignment owns:
  - the P7-E spread under T0 and TS;
  - TS leg identity;
  - economic long/short;
  - E_S and E_L;
  - affected-leg accounting.
- There is no regrouping after return availability is known.
- Return-inadmissible members stay in the P7-E input with the existing
  P7-B/P7-E missing-return representation (`forward_return = NaN`).
  - This is permitted **only** because D7b predeclares, types and
    provenance-binds each such NaN, then reconciles its downstream effect.
  - Exclusion through **unaccounted** NaN, implicit sample selection or
    unverified downstream missingness is forbidden and fails closed.
- The inadmissibility (NaN) mask, the admissible set and the group assignment
  are identical under T0 and TS. A scenario changes return **values** only.

## 5. Economic orientation (B3)

`FactorSpec.sign` sets the economic direction. Factor values, ranks and
groups are never changed, and the FactorSpec is never rewritten.

| sign | economic long (predicted-high) | economic short (predicted-low) |
|---|---|---|
| +1 | highest factor-value group (`n_groups`) | lowest group (1) |
| −1 | lowest group (1) | highest group (`n_groups`) |

- Middle groups are middle.
- **For sign = +1,** Phase-7 metrics pass through unchanged.
- **For sign = −1,** only this **closed** list of orientation-equivariant
  statistics is admitted, with `economic = −recorded`:
  - `IC`;
  - `RANK_IC`;
  - `LONG_SHORT` (the gross long-short mean and its t-stat);
  - `SHARPE` (gross).
- Everything else under sign = −1 gives
  `NOT_ASSESSED_UNSUPPORTED_ORIENTATION` and cannot advance. That includes:
  - the net basis (`TURNOVER_COST_ADJUSTED`, since −net = −gross + cost ≠
    −gross − cost);
  - `MAX_DRAWDOWN`;
  - `BENCHMARK_RELATIVE`;
  - any other key.
- D7b never builds a second net series.
- Adding to the list requires a mathematical justification, review, tests and
  a frozen amendment.

## 6. Scenario returns

The §9.2 formula is unchanged:

    R = (1 + R_obs) · (1 + τ) − 1

- R_obs is the P7-B compounding of the D7a VALID daily returns over the
  observed segment. When `listed_end == f`, it is the empty identity 0.
- **T0:** τ = 0 for every affected admissible member.
- **TS:** τ = −100% in the economic long, +100% in the economic short
  (a stress value, not a bound), and 0 in the middle.
- Non-affected admissible members take the P7-B horizon-h return unchanged in
  both scenarios.
- Scenario panels exist only in memory at evaluation time. They are never
  stored as data (§9.4).

## 7. B, E_S, E_L (B4) and §9.6

These are computed for the declared horizon h, universe N and development
fold.
- The evaluated formations F are the fold's formations whose (f, h) cell is
  neither reserve-purged nor fold-boundary-purged.
- `U_f` is every member of the selected universe at f. It does not depend on
  returns.
- `A_f` is the members with `terminal_affected(f, h)`.
- `S_f` and `L_f` are the members assigned to the economic short and long
  groups. They include return-inadmissible members; members with a missing
  factor value have no group.

**Formulas:**

    B   = mean over f ∈ F with |U_f| > 0 of |A_f ∩ U_f| / |U_f|
    E_S = mean over f ∈ F with |S_f| > 0 of |A_f ∩ S_f| / |S_f|
    E_L = mean over f ∈ F with |L_f| > 0 of |A_f ∩ L_f| / |L_f|

- Weights are equal (count-based) only. A value-weight request fails closed.
- E_L is reported and not used for classification.

**DELISTING-SENSITIVE** holds if any of these is true:
- (a) sgn(economic_T0) ≠ sgn(economic_TS) for the declared primary statistic,
  with sgn ∈ {−1, 0, +1} and computed on the same sample;
- (b) the declared screen passes under T0 but fails under TS;
- (c) E_S > 2·B strictly, or E_S > 0 when B = 0. Equality is not sensitive.

A sensitive or `NOT_ASSESSED` result cannot advance. A not-sensitive result
does not authorize holdout use.

## 8. Nonclaims kept

- **D7b is not:** a full Phase-7 evaluation, a final-holdout result,
  confirmation evidence or an ACCEPT/REJECT decision.
- **D7b does not claim:** alpha, predictive validity, investability or
  production readiness.
- **Delisting returns and terminal proceeds:** NOT CERTIFIED
  (`DELISTING_RETURN_NOT_CERTIFIED`). T0 is a reference and TS a stress; the
  +100% short value is not a bound.
- **Sign orientation:** general sign support is not certified. Only the closed
  list in §5 is.
- **Carried forward:** every D7a, D6 and D4 nonclaim, including
  issuer-identity timing and continuity, delisting-knowledge timing and
  survivorship completeness.
