# Discovery Dataset v1.2 — P-Q Quarantine Rules (NORMATIVE; ADOPTED)

- **Status:** ADOPTED, as part of Dataset v1.2. Adoption was authorized by the
  user on 2026-09-30 ("P-Q ADOPTION — FREEZE DATASET v1.2 CONTRACT").
  **Implementation is NOT authorized** by this document.
- **`quarantine_rule_hash`:** the SHA-256 of this file's exact committed bytes. It
  is recorded outside this file, in the adoption commit message and in
  `docs/discovery_dataset_v1_2_pq_amendment.md`.
- **Scope:** development research consumption of Dataset v1.2 only. No reserve,
  confirmation, final-holdout or production use is governed or authorized here.
- **Derivation:** consolidated from the P-Q draft (`786c9f14…e543`) and the
  open-question resolution (`a6abdc05…1667`), with the user's adoption decisions
  (U1–U8, R1–R3). Where those documents differ from this file, **this file
  governs**.

## Q1. Suspect source set

**Q1.1** For each development CORP_ACTION_SUSPECT flag on trusted session s of key
K (D5 `q-5d1028655f8c82e1`, `class_or_flag = CORP_ACTION_SUSPECT`), the quarantined
source set is

    Q(s) = { close_K(prev(s)), close_K(s), CA-state_K(prev(s) → s) }

where prev and next are trusted NYSE sessions inside K's D4 interval. Every
matching flag is applied mechanically, whether or not it affects any current
factor.

**Q1.2** No factual value is deleted, repaired, relabelled or replaced. States are
carried in separate typed panels or columns.

## Q2. States

**Q2.1** Cell states:

| State | Meaning |
|---|---|
| `CLEAN` | a value exists and consumes no element of any Q(s) |
| `QUARANTINED_CA_SUSPECT` | a primitive whose own computation consumes an element of some Q(s) |
| `QUARANTINE_PROPAGATED` | a derived value consuming at least one quarantined primitive under Q3–Q4 |
| `NOT_APPLICABLE` | no value exists for an independent, already-typed reason. That type is kept and never overwritten |

**Q2.2** Undeterminable dependency resolves to `QUARANTINE_PROPAGATED`. A
quarantine state is never downgraded.

**Q2.3** Member-level flags: `CA_QUARANTINED_INPUT` (the factor observation at f is
quarantined) and `CA_QUARANTINED_LABEL` (the (f, h) label is quarantined). Both are
independent of, and recorded alongside, every D7b type, terminal affectedness and
reserve purge. They never become missingness, terminal evidence or purge.

## Q3. Input propagation

**Q3.1** Primitives, dated by their own session u (before the bridge lag):

| Primitive | `QUARANTINED_CA_SUSPECT` iff |
|---|---|
| `daily_total_return`(u) | u ∈ {prev(s), s, next(s)} for some flag s |
| `dollar_volume`(u) | u ∈ {prev(s), s} for some flag s |

`total_return_index`(u) is `QUARANTINE_PROPAGATED` iff its admitted segment, from
the anchor through u, includes a quarantined `daily_total_return`. This is
structural and conservative; no algebraic cancellation is recognized.

**Q3.2** State panels pass through the identical contract §11.1 one-session bridge
lag as the values. Row d carries the state of the primitive dated prev(d).

**Q3.3** FactorSpec propagation, evaluated alongside the expression on the same
grid:

| Operation | Propagation |
|---|---|
| input | the lagged state |
| `lag(x, N)` | shift by N rows |
| rolling op over N rows | any quarantined cell in the window |
| element-wise arithmetic and functions | any quarantined operand |
| cross-sectional `rank` / `winsorize` / `standardize` | the cell's own state only |
| nesting | composition of the above |

**Q3.4** Factor values are identical in both estimands. Forbidden: shortening a
lookback, skipping the suspect day, substituting an observation, forward-filling,
back-filling, recomputing with another window, or masking a value to NaN.

**Q3.5** Universe membership (D6) is unchanged. Liquidity-window exposure (members
whose 63-session window contains a quarantined `dollar_volume`) is report-only.

## Q4. Label propagation

**Q4.1** For formation f, horizon h, member K, D7a endpoint e and observed end
e′ = min(e, `listed_end`): the label is `QUARANTINE_PROPAGATED` iff there is a flag
s with s ∈ (f, e′] or prev(s) ∈ [f, e′].

**Q4.2** No removal of the suspect day, compounding around it, path shortening,
value change, or reinterpretation as terminal, delisting or missingness. Labels
already `NOT_APPLICABLE` keep their type.

## Q5. Estimands

**Q5.1** Every Dataset-v1.2 Campaign evaluation precommits, in its hash-bound
declaration, both:
- **A. QUARANTINED**, under Q5–Q9;
- **B. ORIGINAL**, the unmodified Dataset-v1.1 / D7b treatment.

Both use the same FactorSpec, sign, universe variant, formation schedule, horizons,
grouping, `n_groups`, winsorization, cost assumptions, EvaluationSpec, partitions
and fold, screen and its threshold, and holdout policy.
- Neither is tuned separately or chosen after outcomes.
- A declaration missing either estimand gives `NOT_ASSESSED`.

**Q5.2** Estimand A conditions on post-formation information (label quarantine). It
is a **data-contamination sensitivity estimand, not an investable return
estimand**, and is never reported or used alone.

## Q6. Portfolio-period accounting (A)

**Q6.1** One group assignment per formation, universe, FactorSpec and `n_groups`,
from the complete membership and the common factor values (D7b §4). It is shared by
A and B, by T0 and TS, and by every quantity below.

**Q6.2** **The unit of quarantine is the portfolio-period.** Group g at period t is
unavailable in A (`QUARANTINE_PROPAGATED`) iff any member of its frozen composition
carries `CA_QUARANTINED_INPUT` or `CA_QUARANTINED_LABEL`.

**Forbidden:** dropping members, renormalizing weights, implicit cash,
zero-return substitution, filling, regrouping, or changing quantile membership.

**Q6.3** Availability in A:
- **long leg:** iff the economic-long group is available;
- **short leg:** iff the economic-short group is available;
- **gross long-short:** iff both legs are available;
- **benchmark-relative** (where admitted): iff the portfolio value and the benchmark
  value are both available. A member-built benchmark is available iff none of its
  constituents is flagged.

**Q6.4** On every A-available period, the A value equals the B value exactly. A is B
restricted to quarantine-free periods.

**Q6.5** Existing D7b typed NaNs keep their unchanged treatment in both estimands.
P-Q introduces no member-level NaN.

## Q7. Turnover, cost and net

**Q7.1** turnover_t and cost_t (= cost_rate × turnover_t) are factual, computed
identically in A and B from the common membership. In A each carries a composition
flag:
- `COMPOSITION_CLEAN` iff no member of U_t or U_{t−1} carries `CA_QUARANTINED_INPUT`;
- otherwise `COMPOSITION_QUARANTINED`.

**Q7.2** Net long-short in A is available iff the gross long-short is available
**and** cost_t is `COMPOSITION_CLEAN`. Return and cost always refer to the same
effective portfolio.

## Q8. IC / rank-IC (A)

**Q8.1** IC_t and rank-IC_t are unavailable in A iff any member of U_t that has a
factor value carries `CA_QUARANTINED_INPUT` or `CA_QUARANTINED_LABEL`. Otherwise
they equal the B values. Dropping pairs and recomputing the cross-sectional
correlation is forbidden.

## Q9. Statistics over A (gap semantics)

**Q9.1** The original formation-time index is preserved. Unavailable periods stay
explicit gaps.

**Q9.2** **Arithmetic statistics** may use the A-available periods, subject to the
normal minimum-support rules:
- mean;
- Sharpe as mean/std × √periods_per_year;
- per-period benchmark excess means;
- subperiod means;
- dated-intersection time-series correlations used for redundancy.

**Q9.3** **HAC / Newey-West inference.** Lag relationships use the **original
formation-index distance**:
- a pair contributes to lag-k covariance only when its original index distance is k
  under the frozen evaluation schedule;
- the existing lag specification (Bartlett kernel, L =
  `DEFAULT_SETTINGS.newey_west_lags`) is kept;
- no quarantine gap is bridged because filtered rows become adjacent;
- compressed-time Newey-West is **forbidden**, and no other standard-error estimator
  may be substituted.

Until a gap-aware implementation exists and is frozen, **every A inference
statistic that requires HAC is `NOT_ASSESSED`.** That covers t-statistics for IC,
rank-IC, long-short, net, benchmark-relative and subperiod.

**Q9.4** **Path-dependent statistics** fail closed across gaps:
- maximum drawdown, and any compounded or cumulative-wealth statistic, is
  `NOT_ASSESSED` if any gap lies within its required span;
- segments are never stitched into one synthetic path;
- any other path-dependent metric must be classified explicitly before use, and is
  `NOT_ASSESSED` until it is.

**Q9.5** **Sensitivity metrics** (parameter sensitivity, universe sensitivity): each
variant run carries both estimands under these rules.

## Q10. Comparison support (CS-3)

**Q10.1** **Decision basis, CS-1:** A on its valid support, B on its original
support.

**Q10.2** **Diagnostic only:** statistics on the quarantined-period complement, and
support fractions. They never enter an advancement decision.

**Q10.3** Common-support comparison (CS-2) is never the decision basis.

## Q11. QUARANTINE_SENSITIVE and advancement

**Q11.1** Definitions:
- **Q_B:** the mean over evaluated formations of the fraction of U_f carrying any
  `CA_QUARANTINED_*` flag;
- **Q_S, Q_L:** the same fraction within the economic short and long legs
  (return-inadmissible members included).

**Q11.2** In any evaluation cell (A/B × T0/TS comparison within each scenario), the
result is QUARANTINE_SENSITIVE if:
- (a) the declared primary economic statistic has a different sign in A and B
  (sgn ∈ {−1, 0, +1}); or
- (b) the declared development-screen verdict differs between A and B, in either
  direction; or
- (c) Q_S > 2·Q_B or Q_L > 2·Q_B, or Q_S > 0 or Q_L > 0 when Q_B = 0 (κ = 2,
  strict).

No effect-size-change criterion applies.

**Q11.3** A result **cannot advance** under Dataset v1.2 if it is
QUARANTINE_SENSITIVE in any cell, or if any required A statistic or A screen verdict
is `NOT_ASSESSED`. DELISTING-SENSITIVE (contract §9.6; D7b §7) remains
independently binding in both estimands. A non-sensitive result authorizes neither
holdout use nor confirmation.

## Q12. Reporting (mandatory, side by side)

Every result reports, under A and B and under T0 and TS, every precommitted metric
plus:
- the suspect flags in the evaluated domain, and the affected securities;
- quarantined primitives by type, and quarantined factor observations and labels;
- flagged members by group and leg; Q_B, Q_S and Q_L;
- A-available and unavailable periods (portfolio and IC), and the support fractions;
- the CS-3 complement diagnostics;
- formations with residual cross-sectional influence (a quarantined factor cell
  anywhere in U_f);
- liquidity-window exposure;
- every NOT_ASSESSED statistic, with its reason;
- the QUARANTINE_SENSITIVE and DELISTING-SENSITIVE flags, with their triggering
  clauses.

Reporting only one estimand, or only the better one, is forbidden.

## Q13. Safeguards

1. Gate-6 v1 stays permanently INCONCLUSIVE, and Gate-6 data quality NOT_ASSESSED.
2. Suspect observations stay stored, counted, provenance-bearing, queryable and
   visible.
3. No observation or specimen is reclassified.
4. Quarantine never accepts suspect data as valid.
5. Both estimands are preregistered and always reported.
6. Sensitivity and NOT_ASSESSED block advancement.
7. Mechanical application to every flag.
8. No P-Q parameter is tuned from Campaign performance.
9. No FactorSpec or Campaign may opt out.
10. Development only.
