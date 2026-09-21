# Phase 7 — Wave 2 Integration Note and Wave-3 (P7-F) Contract Freeze

**Status: integration record + frozen contract sheet.** This document records
the integration of the approved Phase 7 Wave 2 task commits and freezes the
contracts that Wave 3 (**P7-F** robustness/sensitivity) may rely on. It does
**not** amend the canonical plan (`phase7-plan.md`), does not cross Barrier 2,
and does not authorize Wave 3 execution.

## 1. Integrated Wave 2 commits

Master was advanced from `9569aeaab121b2d53e696b033e564c8bd60cd460` by two
`--no-ff` merge commits (task commits preserved, never squashed or rewritten).
Ownership was disjoint, so both merges were clean — **no integration fix was
required**.

| merge commit | preserved task commit | task |
|---|---|---|
| `64b437da91e6ef78485aba7963a46145801dad27` | `38bddda996e4098cd74352ee34a1bfae36e4e3e5` | P7-D — bounded evaluation metrics |
| `d4363a87e8c71a454d97907eea76fb552885cf96` | `c6b721ac0de17aa681c33802fe8c1ee87ced263d` | P7-E — portfolio evaluation + turnover + cost |

## 2. Frozen metric contracts (`evaluation/metrics.py`, P7-D)

Public primitives (each returns a frozen result carrying `value`/`n_obs` plus
any per-date diagnostic series; `value` is `NaN` for undefined/insufficient):

- `information_coefficient(panel, *, value_col, return_col)` — cross-sectional
  Pearson per date, then time-series mean + Newey-West t-stat. Paired
  missingness only; a date with < 2 valid pairs or a constant side → `NaN`
  date (counted missing).
- `rank_information_coefficient(...)` — identical structure, Spearman with
  tie method `"average"` passed explicitly.
- `long_short_mean_tstat(returns)` — mean + Newey-West t-stat, reusing the
  trusted `engines.inference.newey_west_ols` (constant regressor,
  `settings.newey_west_lags`). < 2 obs → `NaN`.
- `sharpe_ratio(returns, *, periods_per_year)` — `mean/std(ddof=1) *
  sqrt(periods_per_year)`; `periods_per_year` REQUIRED (no frequency inferred);
  zero/non-finite std or < 2 obs → `NaN` (never `inf`).
- `max_drawdown(returns)` — wealth = `cumprod(1 + r)`; drawdown =
  `(wealth - running_peak)/running_peak`; result = `min(drawdown)` (non-positive
  fraction); < 2 obs → `NaN`.
- `benchmark_relative_excess(long_short, benchmark)` — date-intersection
  excess, mean + Newey-West t-stat; single-series dates excluded and counted
  (`n_excluded`).

**Uniform fail-safe:** insufficient information → `NaN` + correct `n_obs`.
Never `inf`, never silent zero, never observation substitution. `metrics.py`
performs **no** alignment, horizon choice, partitioning, PIT selection,
FactorSpec execution, or provider access.

## 3. Frozen portfolio contracts (`evaluation/portfolio.py`, P7-E)

- **Formation authority:** `engines/portfolio_sort.py` remains the sole owner
  of sorting/weighting/long-short mechanics. `evaluation/portfolio.py` delegates
  via `sort_portfolios`, `long_short_return`, and `assign_groups`; it contains
  **no** independent ranking/grouping/weighting. An unsupported `formation_rule`
  raises `UnsupportedFormationRuleError` (fail closed; only the rank single-sort
  is supported).

### 3.1 Frozen turnover convention (membership-based — NOT weight turnover)

For consecutive rebalances with group-membership sets `U_{t-1}`, `U_t`:

    turnover_t = changed / |U_{t-1} ∪ U_t|

where `changed` counts, over the union: names that **entered**, names that
**exited**, and names present in both whose assigned group changed. The first
rebalance (or an empty previous membership) has `turnover = 0.0`.
Consequences (hand-verified): unchanged → `0`; complete replacement → `1.0`;
partial swap → exact fraction; entry/exit each count; `0 <= turnover <= 1`.

This is the plan's *"fraction of names that change groups"* option. It is
**not** weight turnover and is not asserted equal to
`0.5 * sum_i |w_{i,t} - w_{i,t-1}|` (that equality is not established).

### 3.2 Frozen transaction-cost rule (single application)

    cost_rate = transaction_cost_bps / 10000.0
    cost_t = turnover_t * cost_rate
    net_t = gross_t - cost_t

Hand-verified: turnover `0.5`, gross `0.02`, 100 bps → cost `0.005`, net
`0.015`. `transaction_cost_bps = 0` → `net == gross`. Cost is applied exactly
once, here; the delegated engine produces gross returns only.

### 3.3 Gross / net identities (unambiguous)

`PortfolioEvaluation` exposes `gross_returns`, `turnover`, `costs`,
`net_returns`, and `accounting` (per-rebalance `n_obs`, `n_members`, gross,
turnover, cost, net). Downstream consumers must reference these fields by name;
there is no implicit selection of gross vs net. `net_returns == gross_returns -
costs` exactly.

### 3.4 Temporal authority

`evaluate_portfolio` consumes P7-B's aligned output; it never calls
`lag_panel`/`shift`, never alters §8.1 eligibility, and cannot restore purged
observations (purged rows are already absent from the aligned panel).
Membership and weights are functions of the `t`-observable factor value only;
the forward return is consumed only as the measured realization.

## 4. Frozen Wave-3 (P7-F) authority

- **P7-F may vary only** evaluation parameters permitted by the canonical plan:
  evaluation-parameter values (`n_groups`, horizon `h`, cost bps, winsorization
  bounds), frozen calendar-aligned subperiods, and caller-supplied universe
  variants. It re-runs the frozen P7-D/P7-E primitives over those variations.
- **P7-F MUST NOT mutate:** `FactorSpec`, the factor expression AST, factor
  provenance, PIT selection, publication/vintage identity, forward-return
  orientation, §8.1 purge semantics, holdout history, or portfolio formation
  mechanics outside the permitted evaluation parameters.
- **Robustness ≠ new hypothesis:** a robustness variant is an evaluation of the
  *same* frozen `FactorSpec` under a permitted `EvaluationSpec` variation. A
  variation that changes the factor expression/economic hypothesis is a new
  hypothesis and is **outside P7-F authority**.
- **Redundancy:** P7-F may *measure* redundancy against caller-supplied
  accepted-factor observations. It may **not** decide "too redundant → reject"
  (Phase 8 skeptical-judge authority); no registry access.
- Wave-1/Wave-2 contracts remain authoritative and unchanged by P7-F.
