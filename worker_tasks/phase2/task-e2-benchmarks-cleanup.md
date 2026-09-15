# Phase 2 Worker Task — P2-E2: Benchmarks Cleanup (Wave 2)

## Background (read this first)

`smart_beta` is a China A-share factor-investing research package. Phase 1
built six independent modules in parallel git worktrees, each reviewed and
merged. Phase 2 Wave 1 then landed three foundational pieces, all now on
`master`:

- `smart_beta.data.align.lag_panel` — the package's single canonical
  per-stock lag utility.
- `smart_beta.engines.portfolio_sort.assign_groups` and
  `.group_return_stats` — previously private helpers inside
  `smart_beta/engines/portfolio_sort.py`
  (`_assign_groups`/`_group_return_stats`), now promoted to a public,
  independently-tested API specifically so other modules can reuse them.
- Four new `Settings` fields, including `benchmark_size_legs: int = 2`,
  `benchmark_char_legs: int = 3`, and `turnover_abnormal_window_months: int = 6`
  (replacing local module constants that used to live in
  `smart_beta/benchmarks/capm.py` and `smart_beta/benchmarks/ch4.py`).

`smart_beta/benchmarks/capm.py` currently contains its **own**, independently
written, private implementations of rank-based bucket assignment
(`_assign_groups`) and within-group value-weighted return computation
(`_value_weighted_returns`, `_value_weighted_by`) — duplicating what now
exists properly in `engines.portfolio_sort`. It also has its own inline
per-stock lag logic (`_load_panel`'s `.groupby(STOCK_COL)[col].shift(1)`,
`_add_extra_lag`) and `smart_beta/benchmarks/ch4.py` has its own inline lag
in `_add_turnover_proxy` — duplicating `data.align.lag_panel`.

**Your job is to replace all of this duplication with calls to the shared
primitives, and wire in the three new `Settings` fields — without changing
any benchmark's public output at all.** This is a pure internal refactor.
The acceptance bar is that every existing benchmark test still passes with
**unchanged numerical results**, especially the strong-form no-look-ahead
test and the CH-3 bottom-30%-exclusion tests.

**Read this entire specification before writing any code — there are two
subtle behavior-preservation traps described below (a label-mapping
translation and a positive-weight filter) that will not be caught by the
existing test suite if you get them wrong, precisely because the synthetic
fixture never exercises the edge case they guard against.**

**Your working directory for this task is:**

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-e2-benchmarks-cleanup
```

`cd` there before running any command. It is a git worktree on branch
`phase2/task-e2-benchmarks-cleanup`, branched from `master` after Wave 1
merged. A venv already exists at `.venv` with the project installed
(`pip install -e ".[dev]"`) and `.venv/bin/pytest` passing (133/133) before
you start. If anything looks stale, re-run
`.venv/bin/pip install -e ".[dev]"` from that directory. Run tests with
`.venv/bin/pytest`.

This is Phase 2, Wave 2 — one of two tasks (P2-D, P2-E2) starting in
parallel now that Wave 1 (P2-A, P2-B, P2-E1) is merged. The other, P2-D,
refactors `smart_beta/engines/fama_macbeth.py`. You do not depend on P2-D
and it does not depend on you; you touch completely disjoint files.

## File ownership

**You may modify exactly these six files, and no others:**

- `smart_beta/benchmarks/capm.py`
- `smart_beta/benchmarks/ff3.py`
- `smart_beta/benchmarks/ff5.py`
- `smart_beta/benchmarks/ch3.py`
- `smart_beta/benchmarks/ch4.py`
- `tests/test_benchmarks.py`

**You must not modify anything else**, including but not limited to:
`smart_beta/engines/portfolio_sort.py`, `smart_beta/data/align.py`,
`smart_beta/engines/fama_macbeth.py`, `smart_beta/engines/inference.py`,
`smart_beta/factors/*`, `smart_beta/config/settings.py`,
`smart_beta/data/schema.py`.

## The APIs you will consume (final, as merged — read the actual files in
your worktree before writing code, this is a summary for orientation)

### `smart_beta.data.align.lag_panel`

```python
def lag_panel(
    panel: pd.DataFrame,
    columns: Sequence[str],
    periods: int = 1,
    date_col: str = DATE_COL,
    stock_col: str = STOCK_COL,
) -> pd.DataFrame
```

Per-stock shift; new sorted frame; never mutates input. See its module
docstring in `smart_beta/data/align.py` for the full alignment contract.

### `smart_beta.engines.portfolio_sort.assign_groups`

```python
def assign_groups(values: pd.Series, n_groups: int) -> pd.Series
```

Rank-based bucketing (ties broken by `method="first"` so no boundary
observation is dropped). Returns a **float64 Series of bucket numbers
`1..n_groups`** (or `NaN` for input `NaN`s) — **not** string labels.

### `smart_beta.engines.portfolio_sort.group_return_stats`

```python
def group_return_stats(
    members: pd.DataFrame, ret_col: str, weight_col: str
) -> tuple[float, float, int, int, float]
```

Returns `(ew, vw, n_stocks, n_returns, weight_sum)` for one already-formed
group's member rows: equal-weighted mean of non-NaN returns, and
value-weighted mean of returns using `weight_col`, both restricted to rows
where the return is non-NaN and the weight is non-NaN. **It does not
filter out zero or negative weights** — see Trap 2 below.

### `Settings.benchmark_size_legs` / `benchmark_char_legs` / `turnover_abnormal_window_months`

Already on `master` with defaults `2`, `3`, `6` respectively.

## Trap 1: label translation, not a drop-in replacement

`smart_beta/benchmarks/capm.py`'s current private helper:

```python
def _assign_groups(values: pd.Series, labels: Sequence[str]) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    ranks = numeric.rank(method="first", na_option="keep")
    n = int(ranks.notna().sum())
    if n == 0:
        return pd.Series(np.nan, index=values.index, dtype=object)
    n_groups = len(labels)
    buckets = np.floor((ranks - 1) * n_groups / n).clip(upper=n_groups - 1)
    mapping = {float(i): label for i, label in enumerate(labels)}
    return buckets.map(mapping)
```

returns **string labels** directly (e.g. `"small"`/`"big"`), taking a
`labels` sequence as its second argument. `engines.portfolio_sort.assign_groups`
returns **integer-valued buckets 1..n_groups** given an `n_groups` count.
The underlying rank/bucket algorithm is equivalent (both use
`rank(method="first")` to prevent boundary ties from being dropped, and
both clip defensively at the top bucket) — but you must add a small
translation layer where `capm.py` currently calls its own
`_assign_groups(values, labels)`: call
`smart_beta.engines.portfolio_sort.assign_groups(values, n_groups=len(labels))`
to get bucket numbers `1..len(labels)`, then map bucket `i` to `labels[i - 1]`
(e.g. via `.map({i + 1: label for i, label in enumerate(labels)})`) if the
rest of `capm.py`'s code expects string group labels (check
`_add_cross_sectional_groups`, `_two_by_three`, `_spread` — they currently
key on the string labels you pass in `_SIZE_LABELS`/`_VALUE_LABELS`/etc.,
e.g. `vw.xs(label, level=level, axis=1)`). Do not skip this translation and
do not change every downstream consumer to work with integers instead —
keep the string-label contract these already-approved, already-tested
functions rely on; only the group-assignment step's internal implementation
changes.

## Trap 2: the positive-weight filter is not in `group_return_stats`

`smart_beta/benchmarks/capm.py`'s current private helpers:

```python
def _value_weighted_returns(panel: pd.DataFrame) -> pd.Series:
    valid = (
        panel[RETURN_COL].notna()
        & panel[_LAG_COL].notna()
        & (panel[_LAG_COL] > 0)   # <-- excludes zero/negative weight
    )
    ...

def _value_weighted_by(panel: pd.DataFrame, group_cols: Sequence[str]) -> pd.Series:
    ...
    valid = (
        frame[RETURN_COL].notna()
        & frame[_LAG_COL].notna()
        & (frame[_LAG_COL] > 0)   # <-- same
    )
    ...
```

both explicitly exclude rows where the lagged market cap is **zero or
negative**, not just `NaN`. `engines.portfolio_sort.group_return_stats`
only excludes `NaN` weights — it does **not** check `weight > 0`. The
`SyntheticDataSource` fixture always produces strictly positive market
cap, so **the existing benchmark test suite passing is not, by itself,
proof that this protection survived the refactor** — you must preserve it
explicitly and prove it with a new test (see "Required tests" below).
When you replace `_value_weighted_returns`/`_value_weighted_by` with calls
to `group_return_stats`, **pre-filter the `members`/panel to
`weight_col > 0` before calling it** (or, equivalently, before forming the
groups), matching the current behavior exactly.

## What to do

1. In `smart_beta/benchmarks/capm.py`: replace the private `_assign_groups`
   with calls to `smart_beta.engines.portfolio_sort.assign_groups` plus the
   label-translation layer described in Trap 1. Replace
   `_value_weighted_returns` and `_value_weighted_by` with logic built on
   `smart_beta.engines.portfolio_sort.group_return_stats`, applying the
   positive-weight pre-filter described in Trap 2 — `_market_factor` needs
   the whole-cross-section case (treat "all stocks on a date" as a single
   group and call `group_return_stats` once per date), `_two_by_three`
   needs the per-group case (call it once per date x group-cell, mirroring
   how `sort_portfolios` in `portfolio_sort.py` itself loops over date x
   group). A vectorized `pandas.groupby(...).apply(...)` calling
   `group_return_stats` per sub-frame is an acceptable and likely cleaner
   way to do this instead of a nested Python loop — your choice, as long
   as the arithmetic is provably delegated to `group_return_stats`, not
   reimplemented alongside it.
2. In `smart_beta/benchmarks/capm.py`'s `_load_panel` and `_add_extra_lag`:
   replace the inline `panel.groupby(STOCK_COL)[col].shift(...)` calls with
   `smart_beta.data.align.lag_panel(panel, [col], periods=..., date_col=DATE_COL, stock_col=STOCK_COL)`,
   preserving the exact same lag amounts (1 period in `_load_panel`, an
   additional 1-period lag-of-the-lag in `_add_extra_lag`, i.e. 2 periods
   total from the raw column) and the exact same "re-sort back to
   `[date_col, stock_col]` order" behavior these functions currently have
   after the shift.
3. In `smart_beta/benchmarks/ch4.py`'s `_add_turnover_proxy`: replace its
   inline `.groupby(STOCK_COL)["_activity"].shift(1)` (and the
   `.rolling(...).mean().shift(2)` step, if it's cleanly expressible via
   `lag_panel` — the rolling-mean step itself is not a plain lag and may
   stay as-is; only the plain `shift(1)`/`shift(2)`-style steps need to
   move to `lag_panel`) with calls to `smart_beta.data.align.lag_panel`
   where applicable.
4. Replace the module-level constants that duplicate the new `Settings`
   fields:
   - `capm.py`'s `_N_SIZE_LEGS = 2` / `_N_CHAR_LEGS = 3` are currently only
     used in `assert len(_SIZE_LABELS) == _N_SIZE_LEGS`-style sanity
     assertions in each benchmark module (`ff3.py`, `ff5.py`, `ch3.py`,
     `ch4.py`) — point those assertions at
     `settings.benchmark_size_legs`/`settings.benchmark_char_legs` instead
     (the `settings` parameter already flows into every
     `compute_*_factors` function). You do not need to make the sort
     arity dynamically driven by `Settings` — the label tuples
     (`_SIZE_LABELS`, `_VALUE_LABELS`, `_OP_LABELS`, `_INV_LABELS`,
     `_TURNOVER_LABELS`) stay fixed at their current lengths; only the
     assertion's source of truth changes.
   - `ch4.py`'s `_TURNOVER_WINDOW = 6` is used in
     `.rolling(_TURNOVER_WINDOW, min_periods=3).mean()` — replace with
     `settings.turnover_abnormal_window_months`.
5. `_add_mcap_scope` (the CH-3 bottom-30%-by-market-cap shell-stock screen)
   is a distinct algorithm with no equivalent in `engines.portfolio_sort`
   — leave it alone entirely. It is not part of this task's duplication
   cleanup.

## Non-goals

- Do not touch `engines/portfolio_sort.py`, `data/align.py`,
  `engines/inference.py`, or `engines/fama_macbeth.py`.
- Do not change any `compute_*_factors` function's public signature or
  output shape.
- Do not make the 2x3/2x3x3 sort arity dynamically configurable — see
  point 4 above.
- Do not touch `_add_mcap_scope` — see point 5 above.

## Required tests / acceptance criteria

- All 41 pre-existing `tests/test_benchmarks.py` tests pass with
  **unchanged numerical results** — in particular
  `test_factor_has_no_lookahead` (the strong-form row-for-row equality
  check) and `test_ch3_size_sort_excludes_bottom_30pct_by_market_cap`/
  `test_ch3_size_sort_uses_remaining_median_breakpoint`.
- **New test for Trap 2**: construct a small hand-built panel (following
  the existing hand-built-panel test style already in this file) with at
  least one stock whose lagged market cap is zero or negative in some
  cross-section, and confirm it is excluded from the value-weighted return
  for that date — matching the current `_value_weighted_returns`/
  `_value_weighted_by` behavior. This edge case is not exercised by the
  synthetic-fixture-based tests, so it needs its own explicit test.
- `git grep -n "_N_SIZE_LEGS\|_N_CHAR_LEGS\|_TURNOVER_WINDOW"
  smart_beta/benchmarks/` returns nothing (confirms the settings wiring is
  complete, not just additive).
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly the six files listed under File ownership.

## When done

Run the full test suite (`.venv/bin/pytest`) from your worktree, commit
your changes on branch `phase2/task-e2-benchmarks-cleanup`, and report
back:

(a) how you implemented the label translation (Trap 1) and where;
(b) how you preserved the positive-weight filter (Trap 2) and the new test
proving it;
(c) confirmation the three `Settings` fields are wired in and the old
module constants are gone;
(d) test results;
(e) the `git diff --stat` output.

Do not merge your branch, do not touch `master`, and do not modify files
outside the six listed above.
