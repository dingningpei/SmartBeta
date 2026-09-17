# Phase 4C Worker Task — P4C-9: Migrate pipelines/fama_macbeth_premium.py

## Background (read this first)

Read `worker_tasks/phase4c/phase4c-plan.md` in full first. This task
starts only after P4C-7 is merged. Full-suite-green is not a
precondition for starting, for the same documented reason as P4C-8 (see
its spec's Background section, and the plan document's Wave 4a barrier
note). Confirm your worktree's `master` merge-base contains the amended
`smart_beta/pipelines/_common.py` and the file `tests/
test_fama_macbeth_pipeline.py` (created by P4C-7, currently failing)
before starting.

This is Wave 4b — one of three parallel tasks (P4C-8, P4C-9, P4C-10).
Assume P4C-8's and P4C-10's changes do not exist in your worktree; do not
touch `pipelines/beta_portfolio.py` or any `benchmarks/*` file.

**This is the task where the Fundamentals Ranged-Query Semantics finding
matters most.** `build_fama_macbeth_premium` calls `get_financials`
(soon: fundamentals retrieval) over a caller-chosen `[start, end]` — a
real multi-year Fama-MacBeth backtest is exactly the wide-range case
P4B-9 proved unreliable for Tiingo. Read
`smart_beta/research_inputs/fundamentals_coverage.py` (P4C-3, already
merged) in full before writing anything: this task's whole job is to use
it correctly, by default, in its strict mode.

## File ownership

**You may modify exactly these:**

- `smart_beta/pipelines/fama_macbeth_premium.py` (amend)
- `tests/test_fama_macbeth_pipeline.py` (amend/finish — same pattern as
  P4C-8's spec: P4C-7 left mechanically-moved, currently-broken bodies
  and a `# TODO(P4C-9)` marker)
- `tests/fixtures/phase4c/fama_macbeth/` — any fixture files you need
  (this exact path, reserved for this task only)

**You must not modify anything else**, including
`smart_beta/pipelines/_common.py`, `smart_beta/pipelines/
beta_portfolio.py`, `tests/test_beta_portfolio_pipeline.py`,
`smart_beta/research_inputs/*` (you consume
`fundamentals_coverage.py`/`inputs.py`, you do not change them),
`smart_beta/engines/fama_macbeth.py`, `smart_beta/data/align.py`,
`smart_beta/benchmarks/*`, or `pyproject.toml`.

## The API you consume (already merged — read the actual files)

```python
# smart_beta.pipelines._common (P4C-7, frozen)
def build_universe_and_tradable_returns(view, start, end, settings, *, policy) -> tuple[pd.DataFrame, pd.DataFrame]: ...

# smart_beta.research_inputs.fundamentals_coverage (P4C-3, frozen)
@dataclass(frozen=True)
class FundamentalsCoverageReport: ...
class FundamentalsCoverageError(Exception): ...
@dataclass(frozen=True)
class FundamentalsRetrievalResult:
    data: pd.DataFrame
    coverage: FundamentalsCoverageReport
def retrieve_fundamentals(source, start, end, fields, *, interval_width_days=92, allow_partial=False) -> FundamentalsRetrievalResult: ...
```

(Exact signatures may have shifted during real implementation — read the
real merged files.)

## What to build

```python
def build_fama_macbeth_premium(
    view: "PointInTimeView",
    source: "PITDataSource",   # needed for retrieve_fundamentals -- see
                                # P4C-6's own resolution of the view-vs-
                                # source question; use whatever it
                                # actually settled on, do not re-litigate it
    characteristic_fields: Sequence[str],
    start: date | str,
    end: date | str,
    *,
    policy: "TradabilityPolicy",
    industry_col: str | None = None,
    settings: Settings = DEFAULT_SETTINGS,
    allow_partial_fundamentals: bool = False,
) -> FamaMacBethPipelineResult:
```

**Frozen requirement — the whole point of this task:** call
`retrieve_fundamentals(..., allow_partial=allow_partial_fundamentals)`
with `allow_partial_fundamentals` **defaulting to `False`**, propagated
straight from this function's own parameter of the same default. **Do
not silently set `allow_partial=True` internally "to make the pipeline
more robust."** If the underlying retrieval raises
`FundamentalsCoverageError`, let it propagate out of
`build_fama_macbeth_premium` uncaught — a missing/unreconcilable required
interval must not silently alter the Fama-MacBeth sample. Any
partial-fundamentals mode is available **only** because the caller
explicitly passed `allow_partial_fundamentals=True`, and in that case the
returned `FamaMacBethPipelineResult` must carry the
`FundamentalsCoverageReport` (add a field for it — see below) so the
coverage evidence is reachable from the pipeline's own result object, not
just from an intermediate variable that gets thrown away.

Amend `FamaMacBethPipelineResult` to add a field:

```python
@dataclass(frozen=True)
class FamaMacBethPipelineResult:
    universe: pd.DataFrame
    aligned_panel: pd.DataFrame
    result: FamaMacBethResult
    settings: Settings
    fundamentals_coverage: "FundamentalsCoverageReport"   # NEW -- always
        # present, even in the default strict-mode path (where it will
        # simply show is_complete=True, since the call would otherwise
        # have raised before this object was ever constructed).
```

Returns used in the regression must be `adj_ret` (from
`build_universe_and_tradable_returns`'s new output), never `raw_ret`.

## Required tests

1. **Finish P4C-7's moved tests**, adapted to the new signature and
   fixtures.
2. **Strict-by-default regression, the central test of this task.**
   Using a fixture constructed so the required fundamentals range is
   genuinely unreconcilable (reuse the real evidence in `tests/fixtures/
   tiingo/certification/aapl_statements_*_2026-01-01_2026-12-31.json`,
   copied into your own subdirectory), call `build_fama_macbeth_premium`
   with `allow_partial_fundamentals` omitted (default) and assert it
   raises `FundamentalsCoverageError` — the Fama-MacBeth regression must
   never run at all on an incomplete fundamentals sample by default.
3. **Opt-in partial mode surfaces coverage evidence.** Same fixture,
   `allow_partial_fundamentals=True`: assert the call succeeds, and
   `result.fundamentals_coverage.is_complete` is `False` with the exact
   unresolved intervals visible on the returned object — not merely
   logged or discarded.
4. **A genuinely reconcilable range works end to end** (reuse the real
   `2026-03-01..2026-07-31` evidence) with the default strict mode,
   producing `fundamentals_coverage.is_complete == True`.
5. **Realized returns are `adj_ret`.** Same style of regression as
   P4C-8's required test 4, adapted to this pipeline.
6. **Real `TiingoPITSource` end-to-end proof**, scoped to what the
   current plan tier genuinely supports.
7. **China behavior-preservation**, analogous to P4C-8's required test 7.
8. No `DataSource` import remains in `fama_macbeth_premium.py`.

## Non-goals

- Do not weaken, wrap-and-suppress, or reimplement any part of
  `retrieve_fundamentals`'s fail-closed behavior.
- Do not default `allow_partial_fundamentals` to `True`.
- Do not touch `engines/fama_macbeth.py` — it remains schema-light and
  unchanged; only the column names this pipeline passes into it change
  (`adj_ret` instead of `ret`).

## Acceptance criteria

- `tests/test_fama_macbeth_pipeline.py` passes in full.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/pipelines/fama_macbeth_premium.py`,
  `tests/test_fama_macbeth_pipeline.py`, and files under
  `tests/fixtures/phase4c/fama_macbeth/`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-9-fama-macbeth-migration
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/test_fama_macbeth_pipeline.py -v
git diff --stat master...HEAD
```

Full-suite-green is not required at this task's own gate, for the same
reason as P4C-8 (see its spec). Barrier 4b is the real gate.

## Expected commit scope

One commit (or a small number) on branch
`phase4c/task-p4c-9-fama-macbeth-migration`, touching only the files
listed above.

## If you discover a contract contradiction

Stop and report it rather than improvising — in particular if the
`view`-vs-`source` parameter question (needed for `retrieve_fundamentals`)
contradicts what P4C-6 actually settled on.

## When done

Report: (a) the exact new `build_fama_macbeth_premium` signature and the
amended `FamaMacBethPipelineResult`; (b) confirmation of strict-by-default
behavior with the exact test result; (c) confirmation the partial-mode
opt-in surfaces real coverage evidence on the result object; (d)
confirmation of the `adj_ret` regression; (e) confirmation of zero
`DataSource` imports; (f) the real `TiingoPITSource` end-to-end result;
(g) test results; (h) `git diff --stat`. Do not merge, do not touch
`master`, do not modify files outside the list above.
