# Phase 1 Worker Task — Task F: Benchmark Factor Construction

## Common Phase 1 background (read this first)

You are implementing one task of Phase 1 of the `smart_beta` project. This
is a from-2020 China A-share factor-investing research codebase,
originally four standalone, buggy Jupyter notebooks (`Beta.ipynb`,
`BetaEffect.ipynb`, `CAPM.ipynb`, `Factor_Effect.ipynb` — still present at
the repo root; read them for context on what the old, broken version did,
but do not modify or delete them). Phase 0 replaced the notebooks'
position-indexed numpy arrays with a schema-driven, long-format pandas
package to eliminate an entire class of alignment bugs found in review
(off-by-one rolling windows, NaN silently coerced to 0, weighted means
normalized by the wrong denominator, etc.).

**Your working directory for this task is:**

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-f-benchmarks
```

`cd` there before running any command. It is a git worktree already
checked out on branch `phase1/task-f-benchmarks`, branched from `master`
at commit `34058fa` ("Add Phase 0 package skeleton..."). A venv already
exists at `.venv` in that worktree with the project installed
(`pip install -e ".[dev]"` — pandas 3.0.5, numpy, scipy, statsmodels,
scikit-learn, pytest all present and `.venv/bin/pytest` passing 17/17
before you start). If anything looks stale, re-run
`.venv/bin/pip install -e ".[dev]"` from that directory. Run tests with
`.venv/bin/pytest`.

Fixed foundation, already merged to `master`, **DO NOT MODIFY**:

- `smart_beta/data/schema.py` (long-format panel schema, `validate_panel()`)
- `smart_beta/data/sources/base.py` (`DataSource` abstract interface)
- `smart_beta/data/sources/synthetic.py` (`SyntheticDataSource`,
  `make_default_fixture()`)
- `smart_beta/config/settings.py` (`Settings` dataclass, `DEFAULT_SETTINGS`)
- `tests/conftest.py` (pytest fixture `synthetic_source`)

If you find you need a new named constant in `Settings` or a new schema in
`schema.py`, do **not** edit those files — five other workers are
implementing other Phase 1 tasks in parallel worktrees against the same
base, and editing shared files risks merge conflicts across tasks.
Instead hardcode locally with a `# TODO(settings): ...` comment and flag
it explicitly in your final report.

You are working in isolation from five parallel tasks (universe
construction — **already complete and approved**, on branch
`phase1/task-a-universe` — plus beta estimation, portfolio-sort engine,
Fama-MacBeth engine, and inference utilities, all still in progress). Do
not assume any of their deliverables exist in your worktree, and do not
import from files owned by another task. If your logic conceptually
overlaps with another task's, duplication is fine for Phase 1 — it gets
reconciled during integration. Do not attempt cross-task integration
yourself.

Your code must not mutate input DataFrames in place. Always `.copy()`
before modifying.

**When done:** run the full test suite (not just your new files) from
your worktree to confirm you haven't broken anything, commit your changes
on your branch (`phase1/task-f-benchmarks`), and report back:
(a) the exact function signatures you settled on,
(b) any settings constants you'd want added to the shared `Settings`
dataclass,
(c) test results,
(d) anything you scoped down or left as a stretch goal.

Do not merge your branch, do not touch `master`, and do not modify files
outside the ones listed as yours below.

---

## Task F specifics: Benchmark factor construction

**File ownership** (create these; touch nothing else):

- `smart_beta/benchmarks/capm.py` (new)
- `smart_beta/benchmarks/ff3.py` (new)
- `smart_beta/benchmarks/ff5.py` (new)
- `smart_beta/benchmarks/ch3.py` (new)
- `smart_beta/benchmarks/ch4.py` (new)
- `tests/test_benchmarks.py` (new)

`smart_beta/benchmarks/__init__.py` already exists (empty, from Phase 0)
— do not modify it; just add your new module files inside `benchmarks/`.

### Context: bug to fix

The original notebooks used the US Fama-French `F-F_Research_Data_Factors.CSV`
risk-free rate to compute excess returns for China A-share data — a
market mismatch bug. Your code must source the risk-free rate exclusively
via `DataSource.get_risk_free()` (see `smart_beta/data/sources/base.py`
and `synthetic.py`), never a hardcoded foreign CSV.

Because Task C's `engines/portfolio_sort.py` may not be merged yet, this
task must be self-contained: implement whatever minimal internal sort/
portfolio-construction helper you need locally (as a private function in
this module), understanding it will likely be refactored to reuse
`engines.portfolio_sort` once Phase 2 integrates everything. Duplication
here is expected and fine for Phase 1.

### Deliverable

One function per file, each returning a date-indexed DataFrame/Series of
factor returns:

- `capm.py`: market excess return factor (MKT)
- `ff3.py`: MKT, SMB, HML (Fama-French 3-factor)
- `ff5.py`: ff3 + RMW, CMA (profitability, investment)
- `ch3.py`: Liu-Stambaugh-Yuan (2019, *JFE*) 3-factor model: market, size
  (**excluding** the bottom 30% by market cap when forming size
  portfolios — this is CH-3's defining fix for the "shell value" problem
  in small China A-shares), and an E/P-based value factor
- `ch4.py`: ch3 + a turnover-based sentiment factor

The `SyntheticDataSource` doesn't have a real E/P or turnover field — use
`book_value`/`mcap` as an explicit, clearly-documented placeholder proxy
for value (never claim it equals a real E/P), and note in your report
what a real implementation would need from the `DataSource` interface
(e.g. should `get_financials` support a "turnover" field going forward?).
For `ch4`'s sentiment factor, you may similarly document a placeholder
proxy built from available synthetic fields (e.g. trading-status flags)
rather than blocking on a real turnover field.

### Tests

- Each constructed factor series has one row per date in the requested
  range.
- No look-ahead: a factor value at date *t* must only use data available
  at or before *t*.
- A smoke check that your CAPM market factor correlates strongly with
  `synthetic_source.ground_truth.market_return` (since the synthetic
  return-generating process is literally built from that series — see
  `smart_beta/data/sources/synthetic.py`'s docstring).
- A test confirming the CH-3 size factor's construction actually excludes
  the bottom 30% by market cap (e.g. verify the smallest-cap stocks in a
  given cross-section don't appear in the "small" leg of the size sort).
