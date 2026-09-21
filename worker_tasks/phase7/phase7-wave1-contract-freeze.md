# Phase 7 — Wave 1 Integration Note and Wave-2 Contract Freeze

**Status: integration record + frozen contract sheet.** This document records
the integration of the approved Phase 7 Wave 1 task commits and freezes the
contracts that the Wave 2 tasks (**P7-D** metrics, **P7-E** portfolio) may
rely on. It does **not** amend the canonical plan (`phase7-plan.md`), does not
cross Barrier 1, and does not authorize Wave 2 execution.

## 1. Integrated Wave 1 commits

Master was advanced from `707eb0ea11915ba045c23ed4104e521166329046` by three
`--no-ff` merge commits, following the established repository convention
(task commits preserved, never squashed or rewritten). Ownership was disjoint,
so all three merges were clean — **no integration fix was required**.

| merge commit | preserved task commit(s) | task |
|---|---|---|
| `4fd111f95a618e0a68fe6b86d5136ca20eab84cc` | `a7283b1a0e8e2f5768ea1755b96df066a7cb3226` | P7-A — temporal partition authority |
| `35f4085938ffb073a76636d9f0e21e7a145f130a` | `fa000cdb0eabbba04de901e8b2240cea4a1f733e` | P7-B — future-return alignment |
| `07f809a048350ab5b25a1f3ca1cd69585bf013cd` | `2fd9c7115c09f688e32b26654251b0d68e388d99`, then `4233377255ebd8004a574525bad63a409031908c` | P7-C — evaluation contracts (implementation + test repair) |

## 2. Frozen Wave-2 contract surface

Wave 2 tasks may **consume** these interfaces; they may **not** redefine
partition semantics, forward-return horizon, §8.1 purge behavior, `FactorSpec`,
or PIT selection. All Wave-1 modules are deterministic and fail closed.

### 2.1 `smart_beta.evaluation.partition` (P7-A)

- `Fold(role, start, end, fold_index=None)` — frozen dataclass; each fold is
  the half-open range `[start, end)` (start inclusive, end exclusive).
- `FoldRole` enum: `IS`, `OOS`, `WALK_FORWARD`, `HOLDOUT`.
- `Partition(folds, split_rule, calendar=None)` — frozen; structural rules:
  at most one `IS`/`OOS`/`HOLDOUT`; IS before OOS; walk-forward `fold_index`
  contiguous `0..n-1`; holdout chronologically last; no overlap. Malformed,
  overlapping, empty, non-midnight, timezone-aware, or inverted ranges fail
  closed (`PartitionValidationError`).
- `Partition.partition_id` / `.content_hash` — deterministic SHA-256 of the
  split rule + explicit fold date ranges.
- `Partition.holdout_key`, `Partition.holdout` — the final-holdout token.
- `Partition.fold_containing(timestamp) -> Fold | None`; a date exactly on a
  shared boundary belongs to the **later** fold.
- `Partition.classify_interval(formation, realization_end) ->
  IntervalMembership` (status `INSIDE` / `CROSSES_BOUNDARY` / `OUTSIDE`) and
  `Partition.contains_interval(formation, realization_end) -> bool` — the
  §8.1 boundary predicate. These reason only about dates; they never inspect
  return data.
- `Holdout` / `HoldoutRegistry` / `HoldoutAlreadyConsumedError` —
  **evaluation-local** single-use only (no cross-experiment claim).
- `Partition.to_dict()` / `Partition.from_dict()` — round-trip with tampered
  `partition_id`/`holdout_key` rejection.
- `canonical_json(partition)`, `partition_id(partition)`, `holdout_key(partition)`.

### 2.2 `smart_beta.evaluation.forward_returns` (P7-B)

- `align_forward_returns(factor_panel, realized_returns, horizon, *,
  return_col="adj_ret", partition_boundary=None) -> ForwardReturnAlignment`.
  `horizon >= 1`; the realized-return window is strictly **after** the
  formation date (the contemporaneous row is never used). Sole future-return
  alignment authority **within the new Phase-7 evaluation stack**; legacy
  pipelines are outside this claim.
- `ForwardReturnAlignment` carries `panel`, `purged`, `horizon`,
  `n_observations`, `n_retained`, `n_aligned`, `n_missing_returns`,
  `n_insufficient_forward_window`, `n_nan_returns`, `n_purged`.
- Output columns: `forward_return`, `realization_start`, `realization_end`,
  `status` (values `aligned`, `missing_return`,
  `insufficient_forward_window`, `purged_crosses_partition_boundary`).
- `partition_boundary` is a callable `(start, end) -> bool` (True = the
  closed interval is not wholly inside one fold) or an object exposing
  `crosses_boundary(start, end)`. The canonical glue with P7-A is
  `lambda f, e: not partition.contains_interval(f, e)`.
- §8.1 purge: a crossing label is **purged** (excluded) deterministically —
  never truncated, shortened, reassigned, or allowed to borrow next-partition
  returns. Purge accounting (`purged` frame + `n_purged`) is preserved for the
  `EvaluationRecord`.

### 2.3 `smart_beta.evaluation.spec` (P7-C)

- `EvaluationSpec(metrics, horizons, split_rule, subperiod_rule,
  parameter_grid, universe_variants, cost_model, benchmark,
  factor_provenance_hash)` — frozen declarative policy. `factor_provenance_hash`
  (the Phase 6 `EngineResult.content_hash`) is **required**, fail-closed, and
  validated as a 64-char lowercase-hex SHA-256.
- `EvaluationRecord(spec_hash, factor_provenance_hash, partition,
  fold_results, metric_tables, cost_adjusted_series, subperiod_table,
  parameter_sensitivity_table, universe_sensitivity_table,
  redundancy_measurements, purge_counts, holdout_consumed, holdout_key)` —
  frozen evidence record; **no verdict field exists** and verdict tokens
  (`accept`/`reject`/`decision`/…) are rejected.
- Enums: `MetricKey` (the ten §7 metric identifiers), `CostMode`,
  `BenchmarkKind`, `FoldRole`.
- Parts: `SplitRule`, `SubperiodRule`, `ParameterPoint`, `CostModel`,
  `BenchmarkRef`, `PartitionRef`, `FoldBoundary`, `FoldResult`, `MetricValue`,
  `EvidenceTable`, `Series`, `RedundancyMeasurement`, `PurgeCount`.
- `canonical_json(obj)`, `content_hash(obj)`, `to_dict(obj)` — canonical
  `sort_keys` JSON + SHA-256, independent of mapping insertion/declaration
  order; `NaN`/`inf` record cells fail closed.
- Errors: `EvaluationContractError`, `EvaluationSpecError`,
  `EvaluationRecordError`.
- `evaluation/__init__.py` re-exports **only** the `spec` surface (25 names);
  `partition` / `forward_returns` are imported directly and are **not**
  re-exported.

## 3. Wave-2 boundaries (binding)

- **P7-D** (`metrics.py`) may consume the P7-B `ForwardReturnAlignment`
  (aligned panel + `forward_return` column) and the P7-C metric vocabulary;
  it must **not** recompute alignment, horizon, or purge.
- **P7-E** (`portfolio.py`) may consume P7-B aligned output and must
  **delegate** sorting/weighting to `engines/portfolio_sort.py`; it must not
  be a second formation implementation and must fail closed if the existing
  engine cannot express a requested rule.
- Neither may redefine partition semantics, forward-return horizon, §8.1
  purge behavior, `FactorSpec`, or PIT selection. `evaluation/` must not
  import `smart_beta/vendors/*`.
