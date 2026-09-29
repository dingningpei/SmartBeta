# D7b — Development delisting-sensitivity bridge (Dataset v1.1) — FROZEN BEFORE IMPLEMENTATION

- **Status:** FROZEN BEFORE IMPLEMENTATION (user decisions of 2026-09-28).
  Implementation requires a separate launch authorization.
- **Authority, in precedence order** (on any conflict, stop and report):
  1. `docs/discovery_dataset_v1_contract.md` §§9.2, 9.4, 9.5, 9.6, 10, 11.2 —
     `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`;
  2. `docs/discovery_dataset_v1_1_d7b_clarification.md` (0a72a02) —
     `7cf2f702982bf24179010b6adcdc37f867d0c70803afe4fc7809898f22324bad`;
  3. `docs/discovery_dataset_v1_1_d7_clarification.md` (8efbd96) —
     `bc8e6920d31dd429beb62030123a721a7293558ba57fe8e3d4d6cd6368a96946`;
  4. `worker_tasks/dataset_v1/task-d7-forward-returns.md` (d6fa30b; the D7a
     schema) — `f3f3c99ad1a03833be49737c6a591a7337ee25b92b5e6029801c54d1da4cfb44`;
  5. `worker_tasks/phase7/phase7-plan.md` §§5, 6.1, 8.1 —
     `8b2009533ee00e8a04e0c65b6853711f01387592e42f60c0b97170d23183a13e`;
  6. `worker_tasks/phase8/phase8-plan.md` §§7.3, 11 —
     `755f57182310d40d5ba3f095bac7e92a9297e62ddc5bf38bb34617cb40f9e32f`.
- **Owned files (new):** `smart_beta/dataset_v1/delisting_bridge.py`,
  `tests/test_dataset_v1_delisting_bridge.py`,
  `tests/fixtures/dataset_v1/d7b/**`.
- **Must not modify** any existing file (Phase 7, Phase 8, D7a and every
  other file) or any artifact. There is no CLI, and nothing is ever written
  under `data_store`.
- **Provider or network calls: 0.**

## 1. Scope

- D7b is a **development-only diagnostic bridge**. It turns bound D7a
  evidence, a hash-bound Campaign declaration, a FactorSpec and a factor panel
  into T0/TS development diagnostics, then into a §9.6 classification.
- D7b uses existing Phase-7 lower-level primitives only (§3).
- **D7b is not:**
  - `engine.evaluate` or its orchestration;
  - an `EvaluationRecord`, a `DecisionRecord`, a Phase-8/9 lifecycle step, a
    final-holdout result or confirmation evidence;
  - a screen or ACCEPT/REJECT judge.
- **Hard invariant:** final-holdout consumption by D7b is 0. D7b reads no
  final-holdout observation and no Dataset-v1 reserve observation.

## 2. Bindings (exact; fail closed; never "latest")

**D7a artifact** `r-34565f074d64ccda`,
`returns_hash 4c487e47fd613f3f272109d14b43a27e061f9cc31e5b6d6bcefd4abc97994861`,
manifest `derived/returns/r-34565f074d64ccda/returns.json`
(`6093bec9730e5bd0a6e9e1410db165fca148e3be1b01ef2534db12e34370886e`).

These are the only files D7b may open, relative to `<root>`, through a guarded
resolver that raises `ReserveFirewallError` for a `reserve_sealed`, `raw` or
`dsc_evidence` component and for any non-allowlisted path:

| Relative path | SHA-256 |
|---|---|
| `derived/returns/r-34565f074d64ccda/members.parquet` | `fcb2c9df2918a7bd43399b220f9595fc94d8a5dc49542219698ecafb7c6b4488` |
| `derived/returns/r-34565f074d64ccda/horizons.parquet` | `61a2ac310021f153676445de7f0122ef74f67910e6506a6e5ef1e286f3d1ccbb` |
| `derived/returns/r-34565f074d64ccda/labels.parquet` | `262b8b1d445dfb329d33359966a9deba67bd5999ccdc01dd8183eda6f4f2645e` |
| `derived/returns/r-34565f074d64ccda/daily_returns.parquet` | `d4286d0e48ca5fa86d3631b5c2d5f1bb04fb18accbffd95a61fe6694276b7911` |

**Phase-7 code authority.** The SHA-256 of each file is recorded in every
output, and a mismatch against these values fails closed:

| File | SHA-256 |
|---|---|
| `smart_beta/evaluation/forward_returns.py` | `e7d261a4096ac84f9ea3a522d82e3bf48fd84c3111894c28bb94bf5beebd45de` |
| `smart_beta/evaluation/portfolio.py` | `5dea8adc581ae1bfe2bc7b771db37f36a71c2331cf702d9c8b7174039c3ed2dc` |
| `smart_beta/evaluation/metrics.py` | `747282a2ee7cc7b63d0511d29ab3bdf8c3c9aceea9fb965dd64de543d76831a8` |
| `smart_beta/evaluation/engine.py` | `4ef0caa7d453a27a35c23fc3c7d7ece4b17c132cff651efa6dd79cb2a39d31c8` |
| `smart_beta/evaluation/robustness.py` | `86f8be60303d0fe9618d0c153036732315e557f5ec648406e306752876090abc` |
| `smart_beta/evaluation/partition.py` | `36bb418b6c5662c671c6a869ec9b015df75e47a994395a530addd0b53783be72` |
| `smart_beta/evaluation/spec.py` | `a0e151356a3435c2a3ce98bb4d4cfce25a78111c561419aefb2c1cb77f2ae4de` |
| `smart_beta/engines/portfolio_sort.py` | `d9776fdca3186795b87b3825f2c43b77e1ed79c66bf4ec4e05cb2a00f2178b57` |
| `smart_beta/spec/factor_spec.py` | `ab6c65d312d6fe1e421c489e6ad8045a50e265c8f73dbe6a07738e1bf5458c56` |

**Other bindings:**
- The six authority documents are hash-bound.
- Bindings are a frozen value. Production constants are the default; tests
  inject synthetic bindings.

## 3. Allowed imports (read-only; no reimplementation of their mathematics)

- `forward_returns.align_forward_returns` (P7-B), with the fold predicate as
  `partition_boundary`;
- `portfolio.evaluate_portfolio` (P7-E), with `weight_col=None` always;
- `portfolio_sort.assign_groups`, `sort_portfolios` and `long_short_return`,
  used only for the frozen grouping and the §6 reconciliation replay;
- `robustness.winsorize_panel`;
- `metrics.information_coefficient` and `rank_information_coefficient`, for
  the IC replay;
- `engine._metric_observations`, the frozen MetricKey→primitive glue, called
  with `benchmark_series=None` and a single declared key. **No other `engine`
  symbol may be referenced.** In particular, `evaluate` must not be.
- `partition.Partition` and `Fold` (`contains_interval`, `holdout_fold`,
  `partition_id`), `spec.MetricKey`, and `factor_spec.FactorSpec`;
- D7a's status and state constants from `smart_beta.dataset_v1.returns`.

**Forbidden:**
- `HoldoutRegistry`, or any holdout consume or mark;
- `engine.evaluate`;
- any Phase-8/9/10 module;
- any vendor module.

## 4. Public API

```python
CampaignEvaluationDeclaration   # frozen dataclass; declaration_hash = SHA-256(canonical_json(fields))
ScreenOutcome                   # frozen dataclass
compute_factor_independent_accounting(root, declaration, partition, *, bindings=None) -> FactorIndependentAccounting
build_development_diagnostics(root, declaration, factor_spec, factor_panel, partition, *, bindings=None) -> DevelopmentDiagnostics
classify_delisting_sensitivity(diagnostics, screen_t0, screen_ts) -> D7bDevelopmentSensitivityRecord
```

Every output is an immutable dataclass with canonical JSON and a content
hash. None is an `EvaluationRecord` or a `DecisionRecord`.

### 4.1 `CampaignEvaluationDeclaration` fields

| Field | Rule |
|---|---|
| `precommitment_ref` | non-empty string (the Campaign/Phase-8 precommitment record id) |
| `factor_spec_version` | must equal `factor_spec.version` |
| `factor_panel_sha256` | must equal §5.3's canonical hash of the supplied factor panel |
| `partition_id`, `fold_key` | must equal `partition.partition_id`; the fold must exist |
| `universe` | `TOP_200`, `TOP_500` or `TOP_1000` |
| `horizon` | int, 1 ≤ h ≤ 63 |
| `n_groups` | int ≥ 2 |
| `winsorization` | float, as `winsorize_panel` accepts |
| `transaction_cost_bps` | finite, ≥ 0 |
| `periods_per_year` | int ≥ 1 |
| `weighting` | `EQUAL` only; `VALUE` gives `NOT_ASSESSED_VALUE_WEIGHT_UNSUPPORTED` |
| `primary_metric` | a `MetricKey` |
| `basis` | `NONE` for IC/RANK_IC; `GROSS` for LONG_SHORT/SHARPE/MAX_DRAWDOWN; `NET` for TURNOVER_COST_ADJUSTED; any mismatch gives `NOT_ASSESSED_DECLARATION_INCONSISTENT` |
| `screen_id`, `screen_policy_hash` | the owning Campaign/Phase-8 screen and its policy/threshold provenance; non-empty |

- A missing declaration, or any missing or invalid field, gives
  `NOT_ASSESSED_DECLARATION_MISSING` or `_INCONSISTENT`.
- D7b defines no threshold or screen.

## 5. Procedure (order is part of the contract)

### 5.1 Domain and firewalls, before any return or label load
1. **Verify** every binding, the code-authority hash, the document hashes and
   the declaration.
2. **Fold:** `fold = partition fold fold_key`.
   - If `fold.role == holdout`, `partition.holdout_fold` is `None`, or
     `[fold.start, fold.end)` intersects the holdout fold's interval, the
     result is `NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP`.
   - If the fold is not development-only (any date ≥ 2023-07-03), the result
     is `NOT_ASSESSED_NOT_DEVELOPMENT`.
3. **Formations:** read `members.parquet` (formation dates only) and
   `horizons.parquet` rows with `h == horizon`. The candidate formations are
   `fold.start ≤ f < fold.end`, which is the engine's half-open slice.
4. **Cell purge,** per formation, in this order:
   - `crosses_reserve` gives `PURGED_RESERVED_OUTCOME`;
   - otherwise, if `fold.contains_interval(f, endpoint)` is false, the cell is
     `PURGED_FOLD_BOUNDARY`.
   - Purged cells are recorded with counts and never reach P7-B, P7-E, B, E_S
     or E_L. They are never NaN members.
   - F is the set of remaining formations.
5. **Filtered reads:**
   - `members` and `labels` for f ∈ F and `h == horizon`;
   - `daily_returns` with `date ∈ (min F, fold.end)` and
     `security_key ∈ members`.
   - All reads use pyarrow row filters, so no out-of-domain row is
     materialized.
6. **Post-load assertions:**
   - every loaded daily or label date lies within `[fold.start, fold.end)`,
     otherwise `FinalHoldoutFirewallError`;
   - every date is < 2023-07-03, otherwise `ReserveFirewallError`.
   - The two errors are distinct.

### 5.2 Membership and admissibility (no factor needed)

- `U_f` is the members with the declared universe flag at f ∈ F, carried
  verbatim from D7a with no reranking.
- The D7a label row for (f, K, h) gives the typed admissibility:

| D7a label | D7b `admissibility` | Return |
|---|---|---|
| `COMPLETE`, not affected | `ADMISSIBLE` | P7-B horizon-h return |
| `COMPLETE`, affected | `ADMISSIBLE_TERMINAL` | R_obs from P7-B at horizon `n_observed_sessions` |
| `EMPTY_OBSERVED_PATH_IDENTITY` | `ADMISSIBLE_TERMINAL_EMPTY` | R_obs = 0 (empty identity; not an observed return) |
| `INCOMPLETE`, affected | `TERMINAL_OBSERVED_PATH_INCOMPLETE` | inadmissible (NaN) |
| `INCOMPLETE`, not affected | `OBSERVED_PATH_INCOMPLETE` | inadmissible (NaN) |

- `A_f` is the affected members (`terminal_affected`).
- `compute_factor_independent_accounting` returns the result of §5.1–5.2 plus
  B (§5.7) and per-reason counts. It computes no return value.

### 5.3 Factor panel and grouping

**Factor panel:**
- It must satisfy `FACTOR_PANEL_SCHEMA` (`date`, `stock_id` = security_key,
  `value`).
- Every date must be in F. Any other date raises `FinalHoldoutFirewallError`,
  checked from the dates alone.
- Every `stock_id` must be in `U_f` at its date. Duplicates raise an error.
- **Canonical hash:** sort by (date, stock_id). The canonical JSON rows are
  `[date ISO, stock_id, float.hex(value) or null]`, and the hash is the
  SHA-256 of the result. It must equal `factor_panel_sha256`.
- The supplied panel is never mutated.
- A member without a row, or with a NaN value, is `FACTOR_VALUE_MISSING`: it
  has no group, counts in B, and does not count in E_S/E_L or any statistic.

**Base panel:**
- One row per `U_f` member for f ∈ F, sorted by (date, stock_id). This is the
  canonical row order for every panel and replay.
- The value is the factor value or NaN.
- The panel is winsorized once with `winsorize_panel(winsorization)`.

**Frozen grouping G:** for each f, `assign_groups(winsorized values at f,
n_groups)` on the canonical order. One assignment only, reused for everything.

**Economic legs:**
- sign = +1: long = group `n_groups`, short = group 1;
- sign = −1: long = group 1, short = group `n_groups`;
- everything else is middle.

### 5.4 Observed compounding (P7-B, reused)

The observed panel is every D7a daily row loaded:
`(date, stock_id, adj_ret = daily_total_return)`, with NaN unless VALID.

- **`ADMISSIBLE` members:** call `align_forward_returns` on the factor rows of
  those members, the observed panel, `horizon` and the fold predicate.
  - Each row must come back `aligned`, with `realization_end` equal to the
    D7a `endpoint`.
- **`ADMISSIBLE_TERMINAL` members:** group them by `n_observed_sessions` = m.
  For each m, call `align_forward_returns(..., m)`.
  - Each row must come back `aligned`, with `realization_end` equal to the
    D7a `observed_segment_end`.
  - The result is R_obs.
- **Consistency:** any status or date disagreement raises
  `D7bConsistencyError`. POST_TERMINAL sessions are never compounded.

### 5.5 Scenario panels (in memory only; never persisted)

- The scenario panel for s ∈ {T0, TS} is the base panel (same rows, same
  order, same winsorized values) plus a `forward_return` column.
- **Non-affected admissible:** the P7-B horizon-h value.
- **Admissible terminal (including empty):**
  `R = (1 + R_obs)(1 + τ_s) − 1`, where:
  - `τ_T0 = 0`;
  - `τ_TS = −1.0` for the economic long, `+1.0` for the economic short, and
    `0.0` for the middle;
  - a member with no group (`FACTOR_VALUE_MISSING`) gets τ = 0 and has no
    statistic.
- **Inadmissible and `FACTOR_VALUE_MISSING`:** NaN, each with an accounting
  record.
- Every admissible value must be finite, with R ≥ −1. Otherwise raise
  `D7bConsistencyError`.
- The accounting record is keyed by (f, K, h, scenario) and holds:
  - `group`, `economic_leg` and `admissibility`;
  - `terminal_affected` and the D7a `label_state`;
  - `expected_admissible`.
  - It holds no return value.

### 5.6 Development diagnostic (primary statistic)

**Admission, before any computation:**
- Only the declared `primary_metric` is ever computed.
- `BENCHMARK_RELATIVE` gives `NOT_ASSESSED_NO_BENCHMARK_AUTHORITY`, because
  Dataset-v1 D7b binds no benchmark series.
- A key that is not one of the engine's primary keys gives `NOT_ASSESSED`.
- sign = +1 admits `IC`, `RANK_IC`, `LONG_SHORT`, `SHARPE`, `MAX_DRAWDOWN` and
  `TURNOVER_COST_ADJUSTED`.
- sign = −1 admits only the closed list `IC`, `RANK_IC`, `LONG_SHORT` (gross)
  and `SHARPE` (gross). Anything else gives
  `NOT_ASSESSED_UNSUPPORTED_ORIENTATION`.

**Per scenario s:**
- `portfolio_s = evaluate_portfolio(panel_s, n_groups, transaction_cost_bps)`;
- `(value_s, t_s, n_obs_s) = engine._metric_observations(panel_s, portfolio_s, None, periods_per_year, (primary_metric,))`.

**Economic orientation:**
- sign = +1: `economic_s = value_s`;
- sign = −1: `economic_s = −value_s` and `t` is negated.
- The same rule applies to both scenarios.
- D7b never builds a net series of its own.

### 5.7 Reconciliation (fail closed on any mismatch; both scenarios)
1. Per f, the replay `assign_groups` on `panel_s` equals G, and G is identical
   under T0 and TS.
2. `sort_portfolios(panel_s, n_groups)` per (f, group):
   - `n_stocks` equals the count of members with that group;
   - `n_returns` equals the count of those members that are admissible.
3. `long_short_return(...)` from that replay equals `portfolio_s.gross_returns`
   exactly (bitwise, with NaN positions equal).
4. `portfolio_s.accounting.n_members == Σ n_stocks` and `n_obs` equals the
   rows at f.
5. Every NaN `forward_return` has an inadmissible or missing-factor accounting
   record. Every such record is NaN. Every admissible row is finite.
6. The NaN mask and the admissible (f, K) set are identical under T0 and TS.
7. **IC pairs:**
   - `pairs_s` = {(f, K): finite winsorized value and finite forward_return};
   - it must equal the expected set {group assigned and admissible};
   - `pairs_T0 == pairs_TS`.
   - For IC/RANK_IC, replaying the same metric function on `panel_s` limited
     explicitly to `pairs_s` must reproduce `value_s`, `t_s`, `n_obs_s` and
     the per-date series bit for bit.
8. `portfolio_T0.turnover` equals `portfolio_TS.turnover`, and
   `costs_T0 == costs_TS`.

### 5.8 B, E_S, E_L (equal weight; formations F)

    B   = mean_{f∈F, |U_f|>0} |A_f ∩ U_f| / |U_f|
    E_S = mean_{f∈F, |S_f|>0} |A_f ∩ S_f| / |S_f|      S_f = economic-short members of G
    E_L = mean_{f∈F, |L_f|>0} |A_f ∩ L_f| / |L_f|      L_f = economic-long members of G

- The denominators include return-inadmissible members.
- The number of formations entering each mean is recorded.
- The means use `math.fsum` over the Python-float ratios in formation order.
- The §9.5 per-formation, per-group affected counts and fractions are recorded.

### 5.9 Classification

- `classify_delisting_sensitivity` takes `screen_t0` and `screen_ts`.
- Each `ScreenOutcome` has `screen_id`, `screen_policy_hash`,
  `declaration_hash`, `diagnostics_hash`, `scenario` and `passed: bool`. These
  are produced by the owning Campaign/Phase-8 screen, never by D7b.
- The outcomes must match the declaration's screen and policy, the same
  declaration and diagnostics hashes, and the scenarios T0 and TS.
  Otherwise the result is `NOT_ASSESSED_SCREEN_MISMATCH`, or `_MISSING` if an
  outcome is absent.

**Clauses:**
- (a) `sgn(economic_T0) ≠ sgn(economic_TS)`, with sgn ∈ {−1, 0, +1}. A NaN
  statistic gives `NOT_ASSESSED`.
- (b) `screen_t0.passed and not screen_ts.passed`.
- (c) `E_S > 2·B` (strict), or `B == 0 and E_S > 0`. If E_S is undefined
  (every |S_f| = 0), the result is `NOT_ASSESSED`.

**Status:**
- `NOT_ASSESSED` if any input is missing or unsupported;
- else `DELISTING_SENSITIVE` if any clause holds;
- else `NOT_SENSITIVE`.

**Constant fields:**
- `advancement_blocked = status != NOT_SENSITIVE`;
- `holdout_authorized = false`;
- `final_holdout_consumed = false`;
- `confirmation_evidence = false`;
- `scope = DEVELOPMENT_ONLY`.

## 6. Output content (diagnostics and record)

The diagnostics and the record bind:
- the D7a id, hash and file hashes;
- the Phase-7 code hashes and the document hashes;
- `declaration_hash`, `factor_spec_version`, `factor_panel_sha256`,
  `partition_id` and the fold (key, role, start, end);
- the universe, h, `n_groups`, winsorization, cost, `periods_per_year`,
  `primary_metric`, basis, sign and orientation rule;
- F and the purged cells with their reasons;
- the per-formation counts (|U_f|, |A_f|, |S_f|, |A∩S|, |L_f|, |A∩L|,
  admissibility by reason) and the per-group `n_stocks` / `n_returns` /
  affected;
- B, E_S and E_L with their formation counts;
- for T0 and TS: the recorded value, t, `n_obs` and the economic value;
- the reconciliation results;
- the clause results, the status and the constant fields.

The accounting table is carried in memory with its hash. **No scenario return
value and no scenario panel is persisted or serialized.**

## 7. Required tests (all synthetic; socket guard and credential sentinel)

**Bindings and inputs**
1. D7a file hash mismatch, for each file: fail closed.
2. `returns.json` or `returns_hash` mismatch: fail closed.
3. A decoy newer `r-*` directory: ignored.
4. A Phase-7 code-authority or document hash mismatch: fail closed.
5. `declaration_hash` is deterministic and independent of field order.
6. `factor_panel_sha256` mismatch: error.
7. `factor_spec_version` mismatch: error.
8. `partition_id` mismatch, or an unknown `fold_key`: error.

**Scope and final holdout**

9. `engine.evaluate`, monkeypatched to raise, is never called.
10. `HoldoutRegistry` is never constructed and `consume` is never called
    (spy).
11. Outputs carry `scope = DEVELOPMENT_ONLY`, `final_holdout_consumed = false`
    and `confirmation_evidence = false`.
12. Holdout-period D7a rows, poisoned to raise on materialization, are never
    read.
13. A fold with `role == holdout`: `NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP`.
14. A fold overlapping the holdout interval: `NOT_ASSESSED`.
15. The T0 diagnostic consumes no holdout (registry spy count 0).
16. The TS diagnostic consumes no holdout.
17. T0 and TS together consume 0 holdouts.
18. No fresh-registry bypass: the module source references no
    `HoldoutRegistry`.
19. No scenario-companion concept, and no Phase-8/9/10 import.
20. The outputs are not `EvaluationRecord` or `DecisionRecord` instances.
21. Confirmation evidence cannot be produced (constant false; no Phase-10
    import).
22. `DELISTING_SENSITIVE` gives `advancement_blocked`.
23. `NOT_ASSESSED` gives `advancement_blocked`.
24. `NOT_SENSITIVE` gives `advancement_blocked = false` but
    `holdout_authorized = false`.
25. After D7b runs, a separate `HoldoutRegistry` consumes the partition once
    successfully, and a second consumption is refused (governance unchanged).
26. T0 and TS use the identical fold and F.
27. T0 and TS use identical membership, admissibility and grouping sets.
28. The reserve firewall and the final-holdout firewall each trigger alone,
    with distinct errors.
29. The diagnostic equals the engine glue on the same panel (replay parity).
30. A fold-boundary-crossing cell is `PURGED_FOLD_BOUNDARY` before P7-B and is
    never `insufficient_forward_window`.
31. A factor-panel date outside F: error, raised from the dates alone.
32. A cell whose endpoint lies inside the fold is kept.

**Reserve**

33. A `crosses_reserve` cell is `PURGED_RESERVED_OUTCOME` and P7-B never
    receives it.
34. A reserve-purged cell is not sent to P7-E as NaN.
35. A reserve-purged cell is in no B, E_S, E_L or statistic denominator.
36. An injected reserve-dated D7a row raises `ReserveFirewallError`, and a
    non-allowlisted path is refused.

**Returns and T0**

37. Ordinary complete non-terminal member: the P7-B horizon-h value,
    identical under T0 and TS.
38. Complete terminal member: R_obs from P7-B at m, and T0 R = R_obs.
39. `listed_end == f`: T0 = 0 by identity; TS long −1, short +1, middle 0; the
    accounting type is `ADMISSIBLE_TERMINAL_EMPTY`, never an observed zero.
40. Terminal exactly at the endpoint: m = h, and τ applies under TS.
41. Terminal after the endpoint: not affected.
42. POST_TERMINAL sessions are never compounded (poisoned values unused).
43. The exact T0 and TS formulas on hand-computed values.
44. A P7-B `realization_end` mismatch: `D7bConsistencyError`.
45. A P7-B status that disagrees with the D7a label: error.
46. Missing non-terminal: typed `OBSERVED_PATH_INCOMPLETE` NaN.
47. Terminal and incomplete: typed NaN under both scenarios.
48. τ does not repair missing data: the TS-long incomplete member stays NaN.
49. Scenario panels are not persisted, and the outputs contain no return
    value.
50. h = 1 and h = 63 both work; h = 21 is not special.

**Universes**

51. Top-200/500/1000 are selected by flag, with ranks verbatim and no
    reranking.
52. A factor row for a non-member: error.
53. A missing factor value: `FACTOR_VALUE_MISSING`, no group, in B, not in
    E_S/E_L.

**Grouping, NaN and reconciliation**

54. An inadmissible member is in the P7-E input.
55. It receives a frozen group.
56. Removing its return changes no group assignment.
57. `expected_n_stocks` includes it.
58. `expected_n_returns` excludes it.
59. `n_stocks` matches.
60. `n_returns` matches.
61. An injected `n_stocks` mismatch fails closed.
62. An injected `n_returns` mismatch fails closed.
63. An unaccounted NaN fails closed.
64. An accounted, typed NaN is permitted.
65. The NaN masks are identical under T0 and TS.
66. The admissible identities are identical under T0 and TS.
67. The group assignments are identical under T0 and TS.
68. A terminal-incomplete member stays NaN under the TS long leg.
69. The E_S denominator includes an inadmissible short member.
70. The E_L denominator includes an inadmissible long member.
71. The B denominator includes inadmissible members.
72. The IC expected pairs exclude a typed inadmissible member.
73. The rank-IC expected pairs do the same.
74. The IC pair identities are identical under T0 and TS.
75. Admissibility cannot trigger regrouping.
76. A separate full-set grouping plus an admissible-set grouping is rejected
    by the replay check.
77. The replayed gross spread equals P7-E's gross spread exactly.
78. `n_members == Σ n_stocks`.
79. A shuffled factor panel with ties gives identical groups and an identical
    record.

**Sign and orientation**

80. sign = +1: IC unchanged.
81. sign = −1: IC exactly negated.
82. sign = −1: RANK_IC exactly negated.
83. sign = −1: LONG_SHORT mean exactly negated.
84. sign = −1: LONG_SHORT t-stat exactly negated.
85. sign = −1: SHARPE exactly negated.
86. sign = −1 with net (TURNOVER_COST_ADJUSTED):
    `NOT_ASSESSED_UNSUPPORTED_ORIENTATION`.
87. −(recorded net) is never produced as an economic net.
88. sign = −1 with MAX_DRAWDOWN: `NOT_ASSESSED_UNSUPPORTED_ORIENTATION`.
89. sign = −1 with BENCHMARK_RELATIVE: `NOT_ASSESSED`.
90. sign = −1 with an unknown or non-primary key: `NOT_ASSESSED`.
91. sign = −1 swaps the economic legs with groups unchanged.
92. sign = −1 TS τ: raw bottom terminal −1, raw top terminal +1.
93. sign = +1 TS τ: raw top −1, raw bottom +1, middle 0.
94. sign = −1 swaps which raw extremes feed E_S and E_L.
95. T0 and TS use the identical orientation rule.
96. §9.6(a) compares economic T0 against economic TS.
97. The FactorSpec identity (`version`) is unchanged.
98. The input factor values are unchanged.
99. No reranking occurs.
100. No second net series is constructed.
101. An unsupported orientation cannot advance.
102. sign = +1 net equals `long_short_mean_tstat(P7-E net)` through the glue.
103. sign = +1 BENCHMARK_RELATIVE: `NOT_ASSESSED_NO_BENCHMARK_AUTHORITY`.
104. A basis/key inconsistency: `NOT_ASSESSED_DECLARATION_INCONSISTENT`.
105. A value-weight request: `NOT_ASSESSED_VALUE_WEIGHT_UNSUPPORTED`.

**B, E and §9.6**

106. B hand-computed.
107. E_S hand-computed.
108. E_L hand-computed; a large E_L alone is not sensitive.
109. The B denominator does not depend on return availability.
110. A terminal-incomplete member counts in B.
111. A terminal-incomplete member counts in E_S or E_L by its leg.
112. E_S == 2B exactly (rational fixture): not sensitive.
113. E_S just above 2B: sensitive.
114. B = 0 and E_S = 0: not sensitive.
115. B = 0 and E_S > 0: sensitive.
116. T0 > 0 and TS < 0: sensitive by (a).
117. sgn zero cases: T0 = 0 with TS > 0 is sensitive; both 0 is not.
118. A NaN primary statistic: `NOT_ASSESSED`.
119. Screen passes under T0 and fails under TS: sensitive. Fails under T0 and
     passes under TS: not sensitive by (b).
120. A screen with a mismatched id, policy, declaration or diagnostics hash:
     `NOT_ASSESSED_SCREEN_MISMATCH`.
121. A missing declaration or screen: `NOT_ASSESSED`, cannot advance.
122. Only the declared metric is computed (no menu).
123. A changed declaration after the diagnostics: classification refuses.
124. The §9.5 per-formation, per-group affected counts and fractions are
     present.
125. The affected-fraction denominators are identical under T0 and TS.
126. Turnover and costs are identical under T0 and TS.

**Determinism and hygiene**

127. Two runs give an identical diagnostics and record hash.
128. Changing `TZ` and `LC_ALL` gives identical output.
129. Permuting the D7a and factor-panel input rows gives identical output.
130. `compute_factor_independent_accounting` needs no factor panel and
     computes no metric or return.
131. No network (socket guard) and no credential read (sentinel).
132. Outputs describe the TS short +100% only as a declared stress
     parameter, never as a bound.
133. E_S undefined (every |S_f| = 0): `NOT_ASSESSED`.

## 8. Required mutations (each must fail the suite)

M1 call `engine.evaluate` · M2 consume holdout in T0 · M3 consume holdout in TS · M4 fresh-registry second consumption · M5 scenario-companion reuse · M6 include holdout formations in T0 · M7 include holdout in TS · M8 read holdout-period rows before filtering · M9 `confirmation_evidence = true` · M10 emit an `EvaluationRecord` · M11 `DELISTING_SENSITIVE` advances · M12 `NOT_ASSESSED` advances · M13 `NOT_SENSITIVE` sets `holdout_authorized` · M14 different folds T0/TS · M15 a single firewall for reserve and holdout · M16 reserve-crossing cell into P7-B · M17 reserve purge sent to P7-E as NaN · M18 fold-crossing cell passed to P7-B · M19 hard-code h = 21 · M20 rerank the universe · M21 τ from a rule other than the frozen legs · M22 different legs T0/TS · M23 persist scenario returns · M24 fill an incomplete pre-terminal path with 0 · M25 admit TS-long incomplete · M26 drop a member from membership accounting · M27 reserve-purged member in a denominator · M28 affected-fraction denominator on admissible members only · M29 B conditioned on valid returns · M30 swap E_S/E_L · M31 flip TS long/short τ · M32 describe +100% as a bound · M33 ignore terminal-at-formation · M34 empty path recorded as an observed zero · M35 TS IC computed on T0 returns · M36 different cost T0/TS · M37 miss the sign change · M38 miss the screen change · M39 threshold ≥ or 3B · M40 mishandle B = 0 · M41 allow a reserve path · M42 resolve the latest D7a · M43 compute every metric and pick one · M44 accept different screens T0/TS · M45 internal screen threshold · M46 different admissible samples T0/TS · M47 remove terminal-incomplete from membership · M48 … from B · M49 … from E_S/E_L · M50 E_S denominator on valid returns · M51 E_L denominator on valid returns · M52 B denominator on valid returns · M53 ignore `sign` · M54 top group always long · M55 negate values before grouping · M56 value-weight E_S/E_L · M57 E_S == 2B sensitive · M58 negate factor values in D7b · M59 rewrite FactorSpec identity · M60 sign applied to TS only · M61 sign applied to T0 only · M62 accept −(recorded net) · M63 build −gross − cost · M64 admit MDD with sign −1 · M65 admit benchmark with sign −1 · M66 admit an unknown key with sign −1 · M67 no E_S/E_L swap with sign −1 · M68 wrong τ with sign −1 · M69 group change between scenarios · M70 drop inadmissible rows before grouping · M71 regroup after removing NaN · M72 τ from a grouping other than P7-E's · M73 E_S/E_L from a separate grouping · M74 exclude inadmissible from expected `n_stocks` · M75 include inadmissible in expected `n_returns` · M76 accept an untyped NaN · M77 skip the `n_stocks` reconciliation · M78 skip the `n_returns` reconciliation · M79 different NaN masks T0/TS · M80 compare pair counts, not identities · M81 skip the P7-B `realization_end` check · M82 non-canonical row order · M83 accept value weighting · M84 persist the scenario panel · M85 skip the factor-panel hash check

**85 mutations.** Any surviving non-equivalent mutation means STOP BEFORE
MERGE.

## 9. Barrier (planner)

- **Software:**
  - review;
  - 133 tests;
  - the 85 mutations, plus a planner-independent harness including
    conditional forms;
  - a credential-scrubbed full suite.
- **Real-data structural dry run:**
  - `compute_factor_independent_accounting` only;
  - a planner fold explicitly labelled `PLANNER-DRY-RUN`, which is not a
    Campaign decision;
  - h ∈ {1, 21, 63}; N = 500.
  - The planner reconciles membership, admissibility and purge counts and B
    against D7a labels independently.
  - **No factor panel, metric, portfolio or E_S/E_L is computed on real
    data** until a Campaign declaration is authorized.
- **Determinism:** two runs give identical hashes.

## 10. Certification boundary and nonclaims

**Proposed certification:**
> D7b deterministically applies the frozen Dataset-v1 T0/TS
> delisting-sensitivity framework (§§9.4–9.6) as a development-only
> diagnostic, using bound D7a factual evidence and existing Phase-7
> lower-level primitives.
>
> **What it guarantees:**
> - it consumes and inspects no final holdout and no confirmation reserve;
> - one frozen full-member grouping owns the spread, τ, E_S and E_L;
> - typed return-inadmissible members are represented through the existing
>   Phase-7 missing-return representation. Their arithmetic exclusion is
>   predeclared, provenance-bound and reconciled against downstream group and
>   metric sample counts, and unaccounted missingness fails closed;
> - T0 and TS share identical membership, grouping, admissibility, cost and
>   orientation;
> - sign = −1 is supported only for the closed orientation-equivariant list;
> - DELISTING-SENSITIVE and NOT_ASSESSED block advancement, and NOT_SENSITIVE
>   authorizes nothing.

**Not claimed:**
- full Phase-7 evaluation, a final-holdout evaluation, confirmation evidence
  or an ACCEPT/REJECT decision;
- alpha, predictive validity, investability or production readiness;
- certified delisting proceeds or delisting returns
  (`DELISTING_RETURN_NOT_CERTIFIED`). T0 is a reference and TS a stress; the
  +100% short value is not a bound;
- general sign-orientation support (sign = −1 net, max drawdown, benchmark
  and future metrics are unsupported);
- benchmark-relative evaluation in D7b v1;
- multi-fold aggregation (exactly one declared development fold);
- the Campaign-1 internal split, which D7b does not define;
- formation membership implying an admissible return for every member;
- every D7a, D6 and D4 nonclaim, including issuer-identity timing and
  continuity, delisting-knowledge timing and survivorship completeness.

## Report

- files changed;
- test and mutation counts, with test-to-item and mutation-to-test maps;
- the commit SHA;
- the line `sealed files modified: NONE`.
