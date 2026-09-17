# Phase 4C Worker Task — P4C-7: Migrate pipelines/_common.py; Split Shared Test Infrastructure

## Background (read this first)

Read `worker_tasks/phase4c/phase4c-plan.md` in full first, **especially
its "Ownership collisions found and resolved during planning" section**
— this task exists partly to execute that resolution. This task starts
only after P4C-6 is merged and Barrier 3 is green. Confirm your
worktree's `master` merge-base contains `smart_beta/research_inputs/
inputs.py` before starting.

**Read `smart_beta/pipelines/_common.py` in full — it currently has a
docstring saying only `pipelines.*` modules and `tests/test_pipelines.py`
may import its private helpers. You are changing that rule as part of
this task**, because you are also splitting `tests/test_pipelines.py`.

**Why the test-file split is your job, not P4C-8's or P4C-9's.**
`tests/test_pipelines.py` currently mixes tests of
`build_beta_sorted_portfolios` (P4C-8's file) and
`build_fama_macbeth_premium` (P4C-9's file) in one shared file — some
functions test both in one test body. Both of those tasks need to change
their function's signature, so both would otherwise need to edit this one
file — a real collision inside Wave 4b's parallel wave. You resolve this
now, mechanically, before Wave 4b starts, so P4C-8 and P4C-9 each get a
clean, disjoint, pre-existing file.

**A documented, deliberate exception to "full suite green": after this
task merges, `pipelines/beta_portfolio.py` and `pipelines/
fama_macbeth_premium.py` are EXPECTED to be broken** (they still call
`build_universe_and_tradable_returns` with the OLD `DataSource`-based
signature, which you are replacing) **until P4C-8 and P4C-9 land.** This
is stated explicitly in the plan document's Wave 4a barrier description.
Report this expected breakage plainly; it is not something to hide or
work around by leaving the old signature in place alongside the new one.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-7-common-pipeline-migration`
on branch `phase4c/task-p4c-7-common-pipeline-migration`, branched from
`master` after Barrier 3. Run tests with `.venv/bin/pytest`.

## File ownership

**You may modify/create/delete exactly these:**

- `smart_beta/pipelines/_common.py` (amend)
- `tests/test_pipelines_common.py` (new — dedicated tests for
  `_common.py`'s own functions directly; `_common.py` is not directly
  imported by the old `test_pipelines.py` today, so this is a genuinely
  new file, not a rename)
- `tests/test_beta_portfolio_pipeline.py` (new — created by mechanically
  moving `test_pipelines.py`'s beta-portfolio-relevant test bodies here,
  **as-is**; their calls will be stale/broken against the new
  `_common.py` until P4C-8 fixes them — that is expected and fine, this
  task does not fix their content)
- `tests/test_fama_macbeth_pipeline.py` (new — same mechanical move, for
  the Fama-MacBeth-relevant test bodies)
- `tests/test_pipelines.py` — **delete this file** once its contents are
  fully moved into the three files above.

**You must not modify anything else**, including
`smart_beta/pipelines/beta_portfolio.py`,
`smart_beta/pipelines/fama_macbeth_premium.py` (their own internal
migration is P4C-8/P4C-9's job — you touch only the one call site each
makes into `_common.py`'s functions if, and only if, leaving them
unmodified would prevent the package from importing at all; if a change
there is truly unavoidable, make it the absolute minimum needed to keep
the module importable, and report exactly what and why — do not perform
their migration), `smart_beta/research_inputs/*`, `smart_beta/data/*`,
`smart_beta/benchmarks/*`, or `pyproject.toml`.

## The API you consume (already merged — read the actual files)

```python
# smart_beta.research_inputs.inputs (P4C-6, frozen)
def get_realized_returns(view, start, end) -> pd.DataFrame: ...       # adj_ret
def get_capitalization_weights(view, start, end) -> pd.DataFrame: ... # total_mcap
def get_tradability(view, start, end, keys, policy, settings) -> pd.DataFrame: ...
```

(Exact signatures may have shifted during P4C-6's real implementation —
read the real merged file.)

## What to build

Amend `build_universe_and_tradable_returns` (and, if it still makes
sense given the new inputs, `lag_market_cap`/`value_weighted_market_return`
— read your own current file and decide what changes, but do not rename
these three functions without a concrete reason) to accept a
`PointInTimeView` (or whatever `get_tradability`/`get_realized_returns`/
`get_capitalization_weights` actually require) and an explicit
`TradabilityPolicy`, instead of a `DataSource`:

```python
def build_universe_and_tradable_returns(
    view: "PointInTimeView",
    start: date | str,
    end: date | str,
    settings: Settings,
    *,
    policy: "TradabilityPolicy",   # no default -- caller must choose
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """As before, but sourced from research_inputs instead of DataSource.
    tradable_returns now carries `adj_ret` (not `ret`) as its return
    column -- update every internal reference accordingly. `policy` has
    no default: the caller (a later pipeline task) must decide which
    market's tradability rule applies.
    """
```

Update `_common.py`'s module docstring to name
`tests/test_pipelines_common.py`, `tests/test_beta_portfolio_pipeline.py`,
and `tests/test_fama_macbeth_pipeline.py` as the permitted importers of
its private helpers, replacing the old reference to
`tests/test_pipelines.py`.

## The mechanical test-file split (do this precisely)

1. Read the current `tests/test_pipelines.py` in full.
2. Create `tests/test_beta_portfolio_pipeline.py`: move every test
   function whose body calls `build_beta_sorted_portfolios` (only) into
   it, verbatim, including its imports and fixtures. Do not fix or adapt
   the calls to the new signature — that is explicitly out of scope for
   you; leave a short `# TODO(P4C-8): update for the new
   build_beta_sorted_portfolios signature` comment at the top of the
   file instead.
3. Create `tests/test_fama_macbeth_pipeline.py`: same mechanical move for
   every test function whose body calls `build_fama_macbeth_premium`
   (only), with an equivalent `# TODO(P4C-9): ...` comment.
4. For any test function that calls **both** (e.g. a shared determinism
   test) — split its body into two separate test functions, one per
   pipeline function, placed in the corresponding new file. Do not leave
   a single test that spans both files.
5. Create `tests/test_pipelines_common.py` with genuinely new,
   `_common.py`-focused tests (see Required Tests below) — this is not a
   move, it's new coverage this task adds.
6. Delete `tests/test_pipelines.py`.

## Required tests

1. **`tests/test_pipelines_common.py` (new coverage):**
   `build_universe_and_tradable_returns` returns a `tradable_returns`
   panel keyed on `adj_ret` (not `ret`), fetched via `research_inputs`,
   end-to-end against a real, fixture-fed source (synthetic for
   behavior-preservation reference; real `TiingoPITSource` for at least
   one real-adapter proof). `lag_market_cap`/`value_weighted_market_return`
   (if they still exist under those names — confirm against your own
   amended file) work against `total_mcap`-shaped input.
2. **The mechanical split is complete and lossless.** Every test
   function that existed in the old `tests/test_pipelines.py` (get its
   exact list before you start, e.g. via `grep '^def test_'`) has a
   corresponding test function in one of the two new pipeline-specific
   files (or two, if you split a shared one per step 4 above) — confirm
   by listing test function names before and after and comparing (you
   don't need every one to *pass* right now, per the documented Wave 4a
   exception, but every one must *exist* somewhere and be collectible by
   pytest, not silently dropped).
3. Confirm `_common.py`'s own new tests pass in full (`pytest tests/
   test_pipelines_common.py` alone is green).
4. Confirm (and report, do not hide) that `pytest tests/
   test_beta_portfolio_pipeline.py` and `pytest tests/
   test_fama_macbeth_pipeline.py` currently **fail** — this is the
   documented, expected Wave 4a state. Include the actual failure output
   in your report so the Planner can independently confirm the failures
   are the expected "stale call signature" kind, not something else.

## Non-goals

- Do not fix `beta_portfolio.py` or `fama_macbeth_premium.py`'s internal
  logic — only their one call site into `_common.py`, and only if
  strictly necessary to keep the package importable (see File ownership
  above).
- Do not make the full suite green — that is explicitly not this task's
  bar; Barrier 4a only requires your own new file green plus the
  documented, reported failures elsewhere.
- Do not add a default `policy` value to
  `build_universe_and_tradable_returns`.

## Acceptance criteria

- `tests/test_pipelines_common.py` passes in full.
- The mechanical split is complete (every old test function name is
  accounted for in the new files) and lossless.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/pipelines/_common.py`,
  `tests/test_pipelines_common.py` (new),
  `tests/test_beta_portfolio_pipeline.py` (new),
  `tests/test_fama_macbeth_pipeline.py` (new), and the deletion of
  `tests/test_pipelines.py` — plus, only if you determined it was
  strictly unavoidable, a minimal, explicitly-reported one-line change
  to `beta_portfolio.py` and/or `fama_macbeth_premium.py`'s call site.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-7-common-pipeline-migration
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/test_pipelines_common.py -v
.venv/bin/pytest tests/test_beta_portfolio_pipeline.py tests/test_fama_macbeth_pipeline.py -v   # expected to fail; capture the output
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4c/task-p4c-7-common-pipeline-migration`, touching only the files
listed above.

## If you discover a contract contradiction

Stop and report it rather than improvising — in particular, if keeping
`beta_portfolio.py`/`fama_macbeth_premium.py` importable turns out to
require more than a one-line change, stop and report exactly what's
needed rather than performing their full migration yourself.

## When done

Report: (a) the exact new `build_universe_and_tradable_returns` (and any
other amended function) signature; (b) the complete before/after test
function name mapping for the mechanical split; (c) the real failure
output from `test_beta_portfolio_pipeline.py`/
`test_fama_macbeth_pipeline.py`, confirming it is the expected
stale-signature kind; (d) whether any minimal edit to
`beta_portfolio.py`/`fama_macbeth_premium.py` was needed, and exactly
what; (e) `tests/test_pipelines_common.py`'s test results; (f)
`git diff --stat`. Do not merge, do not touch `master`, do not modify
files outside the list above.
