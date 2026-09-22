# Phase 7 — Wave 3 Integration Note and Wave-4 (P7-G) Contract Freeze

**Status: integration record + frozen contract sheet.** This document records
the integration of the approved Phase 7 Wave 3 task (P7-F) and the bounded
P7-D numerical repair, and freezes the contracts that Wave 4 (**P7-G**
orchestration/evidence assembly) may rely on. It does **not** amend the
canonical plan (`phase7-plan.md`), does not cross Barrier 3, and does not
authorize Wave 4 execution.

## 1. Integrated commits

Master was advanced from `ec05961fe5dad2a8a4f25cd44466ac7dcd4e7644` by:

| merge/commit | preserved task commit(s) | change |
|---|---|---|
| `79993489a6a3b43b5a64bc1b4a3bb029d11f3a31` (merge) | `7c5739242fe0c6b2f026fd86dda5f6116b407137`, then `21ea4b7589cb98b07364ba9752be89cee5eb3134` | P7-F — robustness/sensitivity/redundancy (implementation + constant-series repair) |
| `fc35c961abb58d0e5a95bae510b659b2709710a3` (repair) | — | P7-D bounded numerical repair (constant-input detection in `metrics.py`) |

## 2. P7-D repair (bounded, frozen)

`smart_beta/evaluation/metrics.py` had a numerically fragile constant-series
guard (`std(ddof=0) == 0.0`) in two places — `_pearson` (IC/rank-IC) and
`_newey_west_aggregate` (Newey-West mean/t-stat) — which emitted a spurious
finite value (a near-zero correlation, or a huge finite t-stat) for a
mathematically constant series of non-exactly-representable floats (e.g.
three identical `0.05` values). Both guards are now exact
(`np.ptp(values) == 0.0`, i.e. zero range), matching P7-F's already-repaired
`_series_correlation`. No metric definition, public API, paired-missingness,
Newey-West estimator (`engines.inference.newey_west_ols`, reused unchanged),
or alignment authority changed. Regression tests added in
`tests/test_evaluation_metrics.py` (constant `0.05` factor/return side →
`NaN`; constant `0.05` series → `NaN` t-stat; tiny genuinely-nonconstant
input NOT misclassified; normal correlation unchanged).

**Frozen constant-input contract (all three modules agree):** a mathematically
constant series → correlation/t-stat undefined (`NaN`, or `None` at the
`MetricValue`/`RedundancyMeasurement` record boundary). Never a spurious
finite epsilon or huge t-stat.

## 3. Frozen P7-G authority (Wave 4)

P7-G is **orchestration / evidence assembly**. It may:

- consume the frozen `EvaluationSpec` and Phase-6 `FactorSpec`/admission
  provenance;
- invoke/coordinate the already-certified Phase-7 components through their
  frozen APIs (`partition`, `forward_returns`, `metrics`, `portfolio`,
  `robustness`, `spec`);
- assemble metrics, portfolio/turnover/cost evidence, robustness/sensitivity
  evidence, purge/sample accounting, partition identities, and provenance into
  the frozen `EvaluationRecord`;
- produce deterministic evidence artifacts as specified by the plan.

P7-G MUST NOT: redefine metrics; recompute correlation differently; redefine
portfolio formation/turnover; reapply transaction costs; perform its own
return alignment; weaken §8.1; mutate `FactorSpec`; select robustness winners;
create adaptive robustness grids; issue accept/reject; implement
multiple-testing governance; access any experiment registry; claim
cross-experiment holdout freshness; or access providers/PIT selection.

**P7-G assembles evidence; it does not judge evidence.**

## 4. EvaluationRecord completeness (verified at Barrier 3)

The existing `EvaluationRecord` (P7-C) can represent all required Wave-4
evidence — no alternate record schema is permitted or needed:

- `ParameterSensitivityResult.table` / `SubperiodStabilityResult.table` /
  `UniverseSensitivityResult.table` → the record's
  `parameter_sensitivity_table` / `subperiod_table` /
  `universe_sensitivity_table` (all `EvidenceTable`);
- `RedundancyReport.measurements` → `redundancy_measurements`
  (`tuple[RedundancyMeasurement]`);
- P7-D metric results → `metric_tables`; P7-E cost-adjusted series →
  `cost_adjusted_series`; §8.1 purge accounting → `purge_counts`; partition
  identity → `partition`/`holdout_key`; `holdout_consumed` remains
  evaluation-local only.

P7-G must populate every required field and must not silently omit any of the
canonical provenance (FactorSpec identity, EvaluationSpec identity,
partition/fold identity, horizon identity, purge/sample accounting, gross/net
identity, turnover/cost evidence, variant/universe identities).
