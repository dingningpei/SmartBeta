# Phase 2 Worker Task — P2-F: Pipeline Layer + End-to-End Tests (Wave 3, final)

## Background (read this first)

`smart_beta` is a China A-share factor-investing research package. Phase 1
built six independent modules in parallel git worktrees (universe
construction, beta estimation, a portfolio-sort engine, a Fama-MacBeth
engine, statistical inference utilities, and benchmark factor
construction), each reviewed and merged. Phase 2 then landed, in two
waves: a shared per-stock lag utility, four reconciled `Settings` fields,
and newly-public portfolio-sort primitives (Wave 1); and a cleanup of
`engines/fama_macbeth.py` and `benchmarks/*.py` to consume those shared
pieces instead of duplicating them (Wave 2). Master is green at 134/134
tests as of commit `818f20f`.

**This is the final Phase 2 task.** Every component module (`data.universe`,
`factors.beta`, `data.align`, `engines.portfolio_sort`,
`engines.fama_macbeth`, `engines.inference`, `benchmarks.*`) now exists,
is individually tested, and is behaviorally stable. Nothing has ever
composed them together end-to-end. Your job is to build that composition
layer in `smart_beta/pipelines/` and prove, with tests that exercise the
real modules (not mocks), that the composition is correct — especially
the one alignment rule every earlier task has had to reason about but
nothing has enforced in a full pipeline context: a beta estimate is
inclusive of its own date and must be lagged one period before it is
paired with a return for sorting or regression.

**Your working directory for this task is:**

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-f-pipeline-integration
```

`cd` there before running any command. It is a git worktree on branch
`phase2/task-f-pipeline-integration`, branched from `master` after Wave 2
merged. A venv already exists at `.venv` with the project installed
(`pip install -e ".[dev]"`) and `.venv/bin/pytest` passing (134/134) before
you start. If anything looks stale, re-run
`.venv/bin/pip install -e ".[dev]"` from that directory. Run tests with
`.venv/bin/pytest`.

This is Wave 3 and the sole task in it — there is no parallel task to
coordinate with, but every dependency (P2-A through P2-E2) is already
merged and stable.

## File ownership

**You may create or modify exactly these files, and no others:**

- `smart_beta/pipelines/__init__.py` (rewrite; currently an empty stub)
- `smart_beta/pipelines/_common.py` (new)
- `smart_beta/pipelines/beta_portfolio.py` (new)
- `smart_beta/pipelines/fama_macbeth_premium.py` (new)
- `tests/test_pipelines.py` (new)

**You must not modify anything else**, including but not limited to:
`smart_beta/data/*` (other than reading/importing from it),
`smart_beta/factors/*`, `smart_beta/engines/*`, `smart_beta/benchmarks/*`,
`smart_beta/config/*`, and every existing test file owned by an earlier
task (`tests/test_schema.py`, `tests/test_align.py`,
`tests/test_beta.py`, `tests/test_portfolio_sort.py`,
`tests/test_fama_macbeth.py`, `tests/test_inference.py`,
`tests/test_benchmarks.py`, `tests/test_universe.py`,
`tests/test_settings.py`, `tests/test_synthetic_source.py`,
`tests/conftest.py`).

**If you find, while implementing, that a public primitive you need does
not actually exist** (despite the reference below being accurate as of
`818f20f`), **stop and report exactly what is missing** — do not add it
yourself to another module's file, and do not work around it with a
private duplicate inside `pipelines/`. Report it as a blocking finding
instead.

**You must use only public APIs of every module you compose.** Do not
import any name beginning with `_` from `smart_beta.data.universe`,
`smart_beta.factors.beta`, `smart_beta.data.align`,
`smart_beta.engines.portfolio_sort`, `smart_beta.engines.fama_macbeth`,
`smart_beta.engines.inference`, or `smart_beta.benchmarks.*`. In
particular, never import `smart_beta.benchmarks.capm._load_panel`,
`_assign_groups`, `_value_weighted_returns`, or `_value_weighted_by` — it
does not expose a raw contemporaneous market-cap column anyway (Wave 2
renamed it away entirely; only `<col>_lag` columns exist there), and
depending on it would couple `pipelines/` to benchmark-internal
implementation details that owners of `benchmarks/*` are free to change.
(The one exception: your own `smart_beta/pipelines/_common.py` is private
to *this* package and *your own* `tests/test_pipelines.py` may import from
it — that is testing your own package's internals, not reaching into
another module.)

## Reference: the exact public APIs you will compose (as of master `818f20f`)

```python
# smart_beta.data.sources.base.DataSource (abstract; SyntheticDataSource implements it)
def get_returns(self, start, end) -> pd.DataFrame               # date, stock_id, ret
def get_market_cap(self, start, end) -> pd.DataFrame            # date, stock_id, mcap
def get_financials(self, start, end, fields) -> pd.DataFrame    # date, stock_id, <fields>
def get_risk_free(self, start, end) -> pd.DataFrame             # date, rf
def get_trading_status(self, start, end) -> pd.DataFrame        # date, stock_id, is_suspended, is_limit_up, is_limit_down, is_st
def get_listing_info(self) -> pd.DataFrame                      # stock_id, list_date, delist_date

# smart_beta.data.universe
TRADABLE_COL = "is_tradable"
def build_tradable_universe(returns, market_cap, trading_status, listing_info, settings=DEFAULT_SETTINGS) -> pd.DataFrame
    # -> date, stock_id, is_tradable (bool); keyed on `returns`'s own rows

# smart_beta.factors.beta
def rolling_ols_beta(returns, market_return, risk_free, settings=DEFAULT_SETTINGS) -> pd.DataFrame
    # -> date, stock_id, value (VALUE_COL); inclusive of the return at date t

# smart_beta.data.align
def lag_panel(panel, columns, periods=1, date_col=DATE_COL, stock_col=STOCK_COL) -> pd.DataFrame

# smart_beta.engines.portfolio_sort
GROUP_COL = "group"; VW_RETURN_COL = "vw_return"  # (also EW_RETURN_COL, N_STOCKS_COL, N_RETURNS_COL, WEIGHT_SUM_COL)
def assign_groups(values: pd.Series, n_groups: int) -> pd.Series
def group_return_stats(members, ret_col, weight_col) -> tuple[float, float, int, int, float]  # (ew, vw, n_stocks, n_returns, weight_sum)
def sort_portfolios(panel, char_col, ret_col, weight_col, date_col="date", n_groups=DEFAULT_SETTINGS.n_portfolio_groups, group_col=GROUP_COL) -> pd.DataFrame
def long_short_return(sorted_returns, low_group=1, high_group=None, measure=VW_RETURN_COL, date_col="date", group_col=GROUP_COL) -> pd.Series

# smart_beta.engines.fama_macbeth
class FamaMacBethResult: ...  # coefficients, std_errors, r_squared, n_obs, mean_coefficients, mean_std_errors, t_stats, p_values, n_periods, mean_r_squared, characteristic_cols, settings
def fama_macbeth(panel, characteristic_cols, ret_col, date_col="date", industry_col=None, settings=DEFAULT_SETTINGS) -> FamaMacBethResult

# smart_beta.engines.inference
def newey_west_ols(y, X, lags=DEFAULT_SETTINGS.newey_west_lags) -> RegressionResultsWrapper
def grs_test(alphas, residual_cov, factor_means, factor_cov, n_obs) -> tuple[float, float]

# smart_beta.benchmarks.capm
def compute_market_excess_return(source, start, end, *, settings=DEFAULT_SETTINGS) -> pd.DataFrame  # date-indexed, column "MKT"
# (ff3.compute_ff3_factors / ff5.compute_ff5_factors / ch3.compute_ch3_factors / ch4.compute_ch4_factors have the analogous signature)

# smart_beta.data.schema
DATE_COL = "date"; STOCK_COL = "stock_id"; RETURN_COL = "ret"; MARKET_CAP_COL = "mcap"; VALUE_COL = "value"

# smart_beta.config.settings
class Settings:
    beta_rolling_window_months: int = 24
    beta_min_valid_obs: int = 20
    momentum_lookback_months: int = 11
    momentum_skip_months: int = 1
    min_listing_age_months: int = 12
    bottom_mcap_exclude_pct: float = 0.30
    winsorize_lower_pct: float = 0.01
    winsorize_upper_pct: float = 0.99
    n_portfolio_groups: int = 5
    benchmark_size_legs: int = 2
    benchmark_char_legs: int = 3
    turnover_abnormal_window_months: int = 6
    newey_west_lags: int = 6
    fama_macbeth_min_obs: int = 3
    transaction_cost_bps: float = 30.0
DEFAULT_SETTINGS = Settings()
```

## Deliverable 1: `smart_beta/pipelines/_common.py` (private, shared by the two public pipelines below)

Implement exactly:

```python
"""Private helpers shared by smart_beta.pipelines submodules.

Not part of the public API. Only smart_beta.pipelines.* modules (and this
package's own tests/test_pipelines.py) should import from here.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from smart_beta.config.settings import Settings
from smart_beta.data.schema import DATE_COL, STOCK_COL
from smart_beta.data.sources.base import DataSource
from smart_beta.data.universe import TRADABLE_COL, build_tradable_universe
from smart_beta.engines.portfolio_sort import group_return_stats


def build_universe_and_tradable_returns(
    source: DataSource,
    start: date | str,
    end: date | str,
    settings: Settings,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch returns/market cap/trading status/listing info from ``source``,
    build the tradable-universe mask, and return
    ``(universe, tradable_returns)`` where ``tradable_returns`` is the
    ``returns`` panel restricted to rows with ``is_tradable`` True.
    """
    returns = source.get_returns(start, end)
    market_cap = source.get_market_cap(start, end)
    trading_status = source.get_trading_status(start, end)
    listing_info = source.get_listing_info()
    universe = build_tradable_universe(
        returns, market_cap, trading_status, listing_info, settings=settings
    )
    tradable_keys = universe.loc[universe[TRADABLE_COL], [DATE_COL, STOCK_COL]]
    tradable_returns = returns.merge(
        tradable_keys, on=[DATE_COL, STOCK_COL], how="inner"
    )
    return universe, tradable_returns


def value_weighted_market_return(
    panel: pd.DataFrame,
    ret_col: str,
    weight_col: str,
    date_col: str = DATE_COL,
) -> pd.Series:
    """Value-weighted mean return of the whole cross-section, by date.

    Treats every row on a given date as one group and delegates to
    ``engines.portfolio_sort.group_return_stats`` -- the public primitive
    -- rather than duplicating benchmarks.capm's private aggregation
    logic. Used as the ``market_return`` input to
    ``factors.beta.rolling_ols_beta``.
    """
    dates = []
    values = []
    for d, members in panel.groupby(date_col, sort=True):
        _, vw, *_ = group_return_stats(members, ret_col, weight_col)
        dates.append(d)
        values.append(vw)
    return pd.Series(
        values, index=pd.Index(dates, name=date_col), name="market_return"
    )
```

## Deliverable 2: `smart_beta/pipelines/beta_portfolio.py`

Implement exactly (adjust only if you find a genuine bug in this sketch;
the intent is not to leave design choices open, so deviate only to fix
something actually broken, and say so in your report):

```python
"""Universe -> beta -> explicit lag -> portfolio sort -> long-short -> inference.

Enforces, as a correctness invariant rather than a caller convention, the
alignment rule documented in smart_beta.data.align: beta from
rolling_ols_beta is inclusive of the return at date t, so it is always
passed through lag_panel before being paired with a same-date return for
sorting. No function in this module ever merges the raw (unlagged) beta
panel onto a return panel for sorting.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import statsmodels.api as sm
from statsmodels.regression.linear_model import RegressionResultsWrapper

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import DATE_COL, MARKET_CAP_COL, RETURN_COL, STOCK_COL, VALUE_COL
from smart_beta.data.sources.base import DataSource
from smart_beta.engines.inference import newey_west_ols
from smart_beta.engines.portfolio_sort import (
    GROUP_COL,
    VW_RETURN_COL,
    long_short_return,
    sort_portfolios,
)
from smart_beta.factors.beta import rolling_ols_beta
from smart_beta.pipelines._common import (
    build_universe_and_tradable_returns,
    value_weighted_market_return,
)

__all__ = ["BetaPortfolioResult", "build_beta_sorted_portfolios", "spanning_test"]

_BETA_LAG_COL = "beta_lag"


@dataclass(frozen=True)
class BetaPortfolioResult:
    """Output of build_beta_sorted_portfolios.

    universe: (date, stock_id, is_tradable).
    beta: raw rolling_ols_beta output -- as-of date t, inclusive of the
          return at t. Exposed for inspection/testing; never merged
          directly onto a return panel.
    beta_lagged: beta passed through lag_panel(periods=1) -- this is the
          panel actually used as the sort characteristic.
    sorted_returns: output of sort_portfolios, sorted on beta_lagged.
    long_short: output of long_short_return (value-weighted, high-minus-low).
    settings: the Settings used.
    """

    universe: pd.DataFrame
    beta: pd.DataFrame
    beta_lagged: pd.DataFrame
    sorted_returns: pd.DataFrame
    long_short: pd.Series
    settings: Settings


def build_beta_sorted_portfolios(
    source: DataSource,
    start: date | str,
    end: date | str,
    *,
    n_groups: int = DEFAULT_SETTINGS.n_portfolio_groups,
    settings: Settings = DEFAULT_SETTINGS,
) -> BetaPortfolioResult:
    universe, tradable_returns = build_universe_and_tradable_returns(
        source, start, end, settings
    )
    market_cap = source.get_market_cap(start, end)
    risk_free = source.get_risk_free(start, end)

    weighting_panel = tradable_returns.merge(
        market_cap, on=[DATE_COL, STOCK_COL], how="inner"
    )
    market_return = value_weighted_market_return(
        weighting_panel, RETURN_COL, MARKET_CAP_COL
    )

    beta = rolling_ols_beta(
        tradable_returns, market_return, risk_free, settings=settings
    )
    beta_lagged = lag_panel(
        beta, [VALUE_COL], periods=1, date_col=DATE_COL, stock_col=STOCK_COL
    )

    sort_panel = tradable_returns.merge(
        beta_lagged.rename(columns={VALUE_COL: _BETA_LAG_COL}),
        on=[DATE_COL, STOCK_COL],
        how="left",
    )
    sort_panel = sort_panel.merge(market_cap, on=[DATE_COL, STOCK_COL], how="left")

    sorted_returns = sort_portfolios(
        sort_panel,
        char_col=_BETA_LAG_COL,
        ret_col=RETURN_COL,
        weight_col=MARKET_CAP_COL,
        date_col=DATE_COL,
        n_groups=n_groups,
    )
    long_short = long_short_return(
        sorted_returns,
        low_group=1,
        high_group=n_groups,
        measure=VW_RETURN_COL,
        date_col=DATE_COL,
        group_col=GROUP_COL,
    )

    return BetaPortfolioResult(
        universe=universe,
        beta=beta,
        beta_lagged=beta_lagged,
        sorted_returns=sorted_returns,
        long_short=long_short,
        settings=settings,
    )


def spanning_test(
    candidate_returns: pd.Series,
    benchmark_factors: pd.DataFrame,
    *,
    settings: Settings = DEFAULT_SETTINGS,
) -> RegressionResultsWrapper:
    """Newey-West spanning regression of candidate_returns on
    benchmark_factors with an intercept (the spanning alpha).

    Aligns both inputs on their shared date index (inner join), drops any
    row with a NaN in either, and fits via
    engines.inference.newey_west_ols using settings.newey_west_lags.
    candidate_returns is e.g. BetaPortfolioResult.long_short; benchmark_factors
    is e.g. benchmarks.capm.compute_market_excess_return's output, or any
    other compute_*_factors output. Neither input is mutated.
    """
    combined = pd.concat(
        [candidate_returns.rename("_y"), benchmark_factors], axis=1, join="inner"
    ).dropna()
    y = combined["_y"].to_numpy(dtype=float)
    x = sm.add_constant(combined.drop(columns=["_y"]).to_numpy(dtype=float))
    return newey_west_ols(y, x, lags=settings.newey_west_lags)
```

## Deliverable 3: `smart_beta/pipelines/fama_macbeth_premium.py`

Implement exactly:

```python
"""Universe -> characteristic alignment -> Fama-MacBeth -> Newey-West."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import DATE_COL, RETURN_COL, STOCK_COL
from smart_beta.data.sources.base import DataSource
from smart_beta.engines.fama_macbeth import FamaMacBethResult, fama_macbeth
from smart_beta.pipelines._common import build_universe_and_tradable_returns

__all__ = ["FamaMacBethPipelineResult", "build_fama_macbeth_premium"]


@dataclass(frozen=True)
class FamaMacBethPipelineResult:
    """Output of build_fama_macbeth_premium.

    universe: (date, stock_id, is_tradable).
    aligned_panel: the tradable panel with each characteristic field
        lagged by one period -- this is what is actually passed to
        fama_macbeth.
    result: the FamaMacBethResult.
    settings: the Settings used.
    """

    universe: pd.DataFrame
    aligned_panel: pd.DataFrame
    result: FamaMacBethResult
    settings: Settings


def build_fama_macbeth_premium(
    source: DataSource,
    characteristic_fields: Sequence[str],
    start: date | str,
    end: date | str,
    *,
    industry_col: str | None = None,
    settings: Settings = DEFAULT_SETTINGS,
) -> FamaMacBethPipelineResult:
    characteristic_fields = list(characteristic_fields)
    universe, tradable_returns = build_universe_and_tradable_returns(
        source, start, end, settings
    )
    financials = source.get_financials(start, end, fields=characteristic_fields)
    panel = tradable_returns.merge(financials, on=[DATE_COL, STOCK_COL], how="inner")
    aligned_panel = lag_panel(
        panel,
        characteristic_fields,
        periods=1,
        date_col=DATE_COL,
        stock_col=STOCK_COL,
    )
    aligned_panel = aligned_panel.dropna(
        subset=[*characteristic_fields, RETURN_COL]
    ).reset_index(drop=True)

    result = fama_macbeth(
        aligned_panel,
        characteristic_fields,
        RETURN_COL,
        date_col=DATE_COL,
        industry_col=industry_col,
        settings=settings,
    )
    return FamaMacBethPipelineResult(
        universe=universe,
        aligned_panel=aligned_panel,
        result=result,
        settings=settings,
    )
```

## Deliverable 4: `smart_beta/pipelines/__init__.py`

Replace the current empty stub with:

```python
"""Orchestration layer: composes DataSource, universe, factors, engines and
benchmarks into public pipeline entry points.

Every pipeline threads a single ``settings: Settings = DEFAULT_SETTINGS``
parameter rather than introducing a second configuration mechanism, and
enforces the beta/characteristic lag-before-pairing-with-a-return
invariant documented in :mod:`smart_beta.data.align` as part of its own
contract, not as a caller convention.
"""
from smart_beta.pipelines.beta_portfolio import (
    BetaPortfolioResult,
    build_beta_sorted_portfolios,
    spanning_test,
)
from smart_beta.pipelines.fama_macbeth_premium import (
    FamaMacBethPipelineResult,
    build_fama_macbeth_premium,
)

__all__ = [
    "BetaPortfolioResult",
    "build_beta_sorted_portfolios",
    "spanning_test",
    "FamaMacBethPipelineResult",
    "build_fama_macbeth_premium",
]
```

## Deliverable 5: `tests/test_pipelines.py`

Use `START = "2015-01-31"`, `END = "2030-12-31"` (the same constants every
other test file in this repo uses for the full synthetic fixture range)
and the existing `synthetic_source` fixture from `tests/conftest.py`.

### Required test 1 (the critical one): the beta-lag regression test

This is not optional and must follow this design, not a weaker
substitute: it must exercise the actual `build_beta_sorted_portfolios`
output, reconstruct the *incorrect* (unlagged) variant using the same
public `sort_portfolios`/`long_short_return` functions, and show they
differ. Implement (adjust variable plumbing as needed, but keep this
exact logical structure):

```python
def test_pipeline_uses_lagged_beta_not_contemporaneous(synthetic_source):
    """Distinguishes the pipeline's correct beta(t-1) -> return(t) wiring
    from the incorrect beta(t) -> return(t) wiring it must never produce.

    Reconstructs the "naive" (incorrect) sort using the SAME public
    sort_portfolios/long_short_return functions the pipeline itself calls,
    merging the UNLAGGED beta panel (result.beta) as the sort
    characteristic instead of result.beta_lagged. rolling_ols_beta's
    24-month rolling window shifts by one month between t-1 and t, so
    beta(t) and beta(t-1) are numerically distinct for essentially every
    stock-month in the fixture -- the two long-short spreads must differ.
    If a future edit accidentally wired the pipeline to sort on unlagged
    beta, this test would then find the two spreads identical (computed
    from the same panel) and fail.
    """
    result = build_beta_sorted_portfolios(synthetic_source, START, END)

    _, tradable_returns = build_universe_and_tradable_returns(
        synthetic_source, START, END, DEFAULT_SETTINGS
    )
    market_cap = synthetic_source.get_market_cap(START, END)
    naive_panel = tradable_returns.merge(
        result.beta.rename(columns={VALUE_COL: "beta_lag"}),
        on=[DATE_COL, STOCK_COL],
        how="left",
    ).merge(market_cap, on=[DATE_COL, STOCK_COL], how="left")
    naive_sorted = sort_portfolios(
        naive_panel,
        char_col="beta_lag",
        ret_col=RETURN_COL,
        weight_col=MARKET_CAP_COL,
        date_col=DATE_COL,
    )
    naive_long_short = long_short_return(
        naive_sorted, low_group=1, high_group=DEFAULT_SETTINGS.n_portfolio_groups,
        measure=VW_RETURN_COL, date_col=DATE_COL,
    )

    common = result.long_short.index.intersection(naive_long_short.index)
    assert len(common) > 10
    # If these were equal, the pipeline would be pairing beta(t) with
    # return(t) -- the exact bug this pipeline exists to prevent.
    assert not result.long_short.loc[common].equals(naive_long_short.loc[common])
    diffs = (result.long_short.loc[common] - naive_long_short.loc[common]).abs()
    assert diffs.max() > 1e-6
```

### Required test 2: beta_lagged is genuinely `beta` shifted one period, in the pipeline's own output

Using `result.beta` and `result.beta_lagged` from an actual
`build_beta_sorted_portfolios` call, verify — for every stock and every
date with a prior observation — that `beta_lagged[stock, t] == beta[stock, t-1]`.
This checks the *pipeline's* wiring (that it exposes and uses the lagged
panel correctly), not `lag_panel`'s own contract (already covered by
`tests/test_align.py` — do not duplicate that coverage here).

### Required test 3: group monotonicity sanity check

Across `result.sorted_returns`, the mean of `beta_lagged`'s value within
each group, averaged over all dates, must be non-decreasing from group 1
to group `n_groups` (low-beta to high-beta, by construction of
`assign_groups`). This is a real, non-tautological sanity check that the
sort characteristic is actually beta and actually monotonic after
lagging.

### Required test 4: benchmark integration via `spanning_test`

Call `compute_market_excess_return(synthetic_source, START, END)` (a
public benchmark function) to get the `MKT` column, and regress
`result.long_short` on it via `spanning_test`. Assert the regression runs,
returns a fitted result with a `params` array of length 2 (intercept +
MKT), and that `fit.rsquared` is between 0 and 1. Do not assert a specific
alpha value — the synthetic fixture's beta anomaly is not part of its
designed ground truth, so only assert the composition works and produces
a sane result.

### Required test 5: end-to-end Fama-MacBeth via `build_fama_macbeth_premium`

Mirror `tests/test_fama_macbeth.py`'s known-answer pattern, but through
the pipeline: call `build_fama_macbeth_premium(synthetic_source, ["signal"], START, END)`
and assert `result.result.mean_coefficients["signal"]` is close to
`synthetic_source.ground_truth.true_signal_coef` (start with `abs=0.01`,
the same tolerance Task D's own test uses; widen it only if the added
universe filter measurably shifts the estimate, and say so in your
report) and that `abs(result.result.t_stats["signal"]) > 3.0`.

### Required test 6: no mutation in `spanning_test`

Deep-copy `candidate_returns`/`benchmark_factors` before calling
`spanning_test`, and assert both are unchanged afterward.

### Required test 7: determinism

Call `build_beta_sorted_portfolios` (and separately
`build_fama_macbeth_premium`) twice with identical arguments and assert
the results are identical (`pd.testing.assert_frame_equal`/
`assert_series_equal` on every DataFrame/Series field), the same pattern
`tests/test_benchmarks.py`'s `test_factor_construction_is_deterministic`
already uses.

## Non-goals

- Do not add a third pipeline beyond the two specified here (no separate
  benchmark-spanning "pipeline" module — `spanning_test` living alongside
  `build_beta_sorted_portfolios` in `beta_portfolio.py` is the complete
  benchmark-integration surface for this task).
- Do not add CLI entry points, notebooks, or reporting/plotting code.
- Do not change `n_groups`'s default or add new `Settings` fields — reuse
  `settings.n_portfolio_groups` and the existing fields exactly as listed
  in the reference section above.

## Acceptance criteria

- All 134 baseline tests continue to pass unchanged.
- All 7 new tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly the five files listed under File ownership.
- `git grep -n "capm\._load_panel\|_assign_groups\|_value_weighted_returns\|_value_weighted_by" smart_beta/pipelines/ tests/test_pipelines.py`
  returns nothing (confirms no benchmark-private helper is used).
- No pipeline function mutates any DataFrame/Series passed to it.
- Output is deterministic for the synthetic fixture (required test 7).

## When done

Run the full test suite (`.venv/bin/pytest`) from your worktree, commit
your changes on branch `phase2/task-f-pipeline-integration`, and report
back:

(a) confirmation all five deliverables were implemented as specified (or,
if you deviated, exactly what you changed and why — this file's code
sketches are meant to be implemented close to verbatim, not treated as
loose inspiration);
(b) test results (should be 134 baseline + 7 new, all passing);
(c) the `git diff --stat` output;
(d) anything you found missing that blocked you (per the "stop and
report" instruction above), if applicable.

Do not merge your branch, do not touch `master`, and do not modify files
outside the five listed above.
