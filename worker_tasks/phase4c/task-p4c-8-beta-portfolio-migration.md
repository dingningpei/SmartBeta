# Phase 4C Worker Task — P4C-8: Migrate pipelines/beta_portfolio.py

## Background (read this first)

Read `worker_tasks/phase4c/phase4c-plan.md` in full first. This task
starts only after P4C-7 is merged. **Full suite green is not a
precondition for starting** — P4C-7's barrier explicitly leaves
`pipelines/beta_portfolio.py` and its tests broken by design; you are the
task that fixes that, for this file only. Confirm your worktree's
`master` merge-base contains the amended `smart_beta/pipelines/
_common.py` and the file `tests/test_beta_portfolio_pipeline.py` (created
by P4C-7, currently failing) before starting.

This is Wave 4b — one of three parallel tasks (P4C-8, P4C-9, P4C-10),
each owning completely disjoint files. Assume P4C-9's and P4C-10's
changes do not exist in your worktree; do not import from them, and do
not touch `pipelines/fama_macbeth_premium.py` or any `benchmarks/*` file.

**Read the actual current `smart_beta/pipelines/beta_portfolio.py`,
`smart_beta/factors/beta.py`, and `smart_beta/data/align.py` before
writing anything.** The Phase-2 temporal invariant this file must
continue to enforce is documented in both files already — do not
re-derive it from memory, read the real comments.

## File ownership

**You may modify exactly these:**

- `smart_beta/pipelines/beta_portfolio.py` (amend)
- `tests/test_beta_portfolio_pipeline.py` (amend/finish — P4C-7 created
  this file with mechanically-moved, currently-broken test bodies and a
  `# TODO(P4C-8)` marker; you fix their calls to match the new signature
  and add the required new tests below)
- `tests/fixtures/phase4c/beta_portfolio/` — any fixture files you need
  (this exact path; do not choose your own name — P4C-9 and P4C-10 are
  running in parallel and each has their own equally exact, reserved
  path, listed in their own specs, to avoid any collision)

**You must not modify anything else**, including
`smart_beta/pipelines/_common.py` (P4C-7's merged, frozen deliverable),
`smart_beta/pipelines/fama_macbeth_premium.py`,
`tests/test_fama_macbeth_pipeline.py`, `smart_beta/research_inputs/*`,
`smart_beta/factors/beta.py`, `smart_beta/engines/*`,
`smart_beta/data/align.py`, `smart_beta/benchmarks/*`, or
`pyproject.toml`.

## The API you consume (already merged — read the actual files)

```python
# smart_beta.pipelines._common (P4C-7, frozen)
def build_universe_and_tradable_returns(view, start, end, settings, *, policy) -> tuple[pd.DataFrame, pd.DataFrame]: ...
def lag_market_cap(market_cap: pd.DataFrame) -> pd.DataFrame: ...
def value_weighted_market_return(panel, ret_col, weight_col, date_col=...) -> pd.Series: ...

# smart_beta.research_inputs.inputs (P4C-6, frozen)
def get_capitalization_weights(view, start, end) -> pd.DataFrame: ...  # total_mcap

# smart_beta.research_inputs.risk_free (P4C-1, frozen)
class RiskFreeProvider(abc.ABC):
    def get_risk_free(self, start, end) -> pd.DataFrame: ...  # (date, rf)
```

(Exact signatures may have shifted during real implementation — read the
real merged files.)

## What to build

Amend `build_beta_sorted_portfolios` to accept a `PointInTimeView`, an
explicit `TradabilityPolicy`, and an explicit `RiskFreeProvider`, instead
of a `DataSource`:

```python
def build_beta_sorted_portfolios(
    view: "PointInTimeView",
    start: date | str,
    end: date | str,
    *,
    policy: "TradabilityPolicy",
    risk_free: "RiskFreeProvider",
    n_groups: int = DEFAULT_SETTINGS.n_portfolio_groups,
    settings: Settings = DEFAULT_SETTINGS,
) -> BetaPortfolioResult:
```

**Frozen temporal invariants — preserve exactly, do not move ownership:**

- `beta(t-1) -> return(t)`: `rolling_ols_beta`'s output remains inclusive
  of the return at `t` (unchanged, `factors/beta.py` is not modified),
  and this pipeline function is still the one place that calls
  `lag_panel` on the beta output before it is ever merged onto a
  same-date return for sorting. This logic **stays exactly where it is
  today, in this file** — it does not move into `research_inputs`, and
  `research_inputs` must not perform any lag/alignment itself (confirm
  by reading `research_inputs/inputs.py`'s real merged code: it must not
  call `lag_panel` anywhere).
- `market_cap(t-1) -> weighting return(t)`: unchanged, still via
  `lag_market_cap`, still applied here, before `sort_portfolios`.
- Returns used for realized-return measurement (the `ret_col` passed to
  `sort_portfolios`, and the return series `rolling_ols_beta` regresses)
  must be `adj_ret`, obtained from `build_universe_and_tradable_returns`
  (P4C-7's amended output) — never `raw_ret`.
- Weighting/screening must use `total_mcap`, from
  `get_capitalization_weights`/`build_universe_and_tradable_returns` —
  never `float_mcap`.
- Risk-free comes from the injected `RiskFreeProvider.get_risk_free(...)`
  — this file must not call `source.get_risk_free` or any `DataSource`
  method at all; `DataSource` should not be imported by this file after
  this task.

## Required tests

1. **Finish P4C-7's moved tests.** Every test body P4C-7 mechanically
   moved into `tests/test_beta_portfolio_pipeline.py` now calls the new
   signature correctly and passes (with a real `PointInTimeView`,
   `TradabilityPolicy`, `RiskFreeProvider` in place of the old
   `synthetic_source: DataSource` fixture — decide the cleanest way to
   adapt each fixture; if a test's original intent no longer makes sense
   under the new contract, say so in your report rather than silently
   deleting it).
2. **`beta(t-1)->return(t)` regression, explicit.** A test proving beta
   is lagged before being used as the sort characteristic — reproduce
   (do not just trust) the existing
   `test_pipeline_uses_lagged_beta_not_contemporaneous`-style assertion
   under the new signature.
3. **`market_cap(t-1)->weighting`, explicit**, mirroring the existing
   `test_pipeline_weights_use_lagged_market_cap`-style assertion.
4. **Realized returns are `adj_ret`, not `raw_ret`.** Construct a
   scenario with a real corporate action (reuse the real AAPL 2020-08-31
   split fixture, copied into your own subdirectory) where raw and
   adjusted returns genuinely differ, and confirm the pipeline's sorted
   portfolio returns match the adjusted figure, not the raw one — this
   is the direct regression test for the exact hazard this migration
   exists to avoid.
5. **Weighting uses `total_mcap`.** Confirm the sort/weighting panel
   never references `float_mcap` anywhere, including when the
   underlying source has both columns.
6. **Real `TiingoPITSource` end-to-end proof.** At least one full run of
   `build_beta_sorted_portfolios` against a real, fixture-fed
   `TiingoPITSource`-backed view, `USZeroVolumeTradabilityPolicy`, and a
   `ConstantRiskFreeProvider` (from P4C-1 — explicitly not a real US rate
   source; document that this is a deterministic stand-in, not a
   production choice) — schema-conformant output, no exception, scoped
   to whatever the current plan tier genuinely supports (do not claim
   more universe coverage than P4B-9 certified).
7. **China behavior-preservation.** The same function, with
   `ChinaAShareTradabilityPolicy` and `SyntheticFixtureRiskFreeProvider`,
   against a synthetic-fixture-backed view, produces numerically
   equivalent results to the pre-Phase-4C `DataSource`-based path for an
   equivalent scenario (adjust for the raw-vs-adjusted-return distinction
   not existing in the old synthetic path — document exactly how you
   handled that comparison).
8. No `smart_beta.data.sources.base.DataSource` import remains in
   `beta_portfolio.py` — confirm directly.

## Non-goals

- Do not modify `factors/beta.py`, `data/align.py`, or `engines/
  portfolio_sort.py` — all unchanged, all still schema-light.
- Do not add a default `policy` or `risk_free` value — both must be
  explicit, caller-supplied.
- Do not choose a real production US risk-free source — use P4C-1's
  `ConstantRiskFreeProvider` for the Tiingo path, explicitly labeled as
  a deterministic stand-in.

## Acceptance criteria

- `tests/test_beta_portfolio_pipeline.py` passes in full.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/pipelines/beta_portfolio.py`,
  `tests/test_beta_portfolio_pipeline.py`, and files under
  `tests/fixtures/phase4c/beta_portfolio/`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-8-beta-portfolio-migration
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/test_beta_portfolio_pipeline.py -v
git diff --stat master...HEAD
```

Full-suite-green is **not** required at this task's own gate (P4C-9 and
P4C-10 are running in parallel and may still be red in your worktree if
their branches aren't merged into yours) — Barrier 4b, after all three
Wave-4b tasks merge into `master`, is where full-suite-green is actually
required and will be independently verified.

## Expected commit scope

One commit (or a small number) on branch
`phase4c/task-p4c-8-beta-portfolio-migration`, touching only the files
listed above.

## If you discover a contract contradiction

Stop and report it rather than improvising.

## When done

Report: (a) the exact new `build_beta_sorted_portfolios` signature; (b)
confirmation of both frozen temporal invariants with the specific test
results; (c) the adj_ret-vs-raw_ret regression test's numeric outcome;
(d) confirmation of zero `DataSource` imports; (e) the real
`TiingoPITSource` end-to-end result; (f) test results; (g)
`git diff --stat`. Do not merge, do not touch `master`, do not modify
files outside the list above.
