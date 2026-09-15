# Phase 2 Worker Task — P2-A: Contract Reconciliation (Settings/Schema)

## Background (read this first)

`smart_beta` is a China A-share factor-investing research package. Phase 0
built a schema-driven, long-format pandas foundation
(`smart_beta/data/schema.py`, `smart_beta/data/sources/base.py`, a
synthetic fixture generator, `smart_beta/config/settings.py`). Phase 1 then
built six independent modules in parallel git worktrees (universe
construction, beta estimation, a portfolio-sort engine, a Fama-MacBeth
engine, statistical inference utilities, and benchmark factor
construction), each reviewed independently and merged into `master`. Phase
1 finished at commit `37f64c9` with all six tasks merged and 115/115 tests
passing.

During Phase 1 review, several workers found they needed a new shared
constant or schema entry but were explicitly told not to edit the shared
`settings.py`/`schema.py` files themselves (to avoid merge conflicts across
parallel worktrees). Instead they hardcoded a local value with a
`# TODO(settings): ...` or `# TODO(schema): ...` comment and flagged it in
their final report. **P2-A's job is to resolve those deferred TODOs now
that Phase 1 is fully merged and there is no parallel-worktree conflict
risk for these specific files.**

This is Phase 2, Wave 1 — one of three tasks (P2-A, P2-B, P2-E1) starting
in parallel from the same `master` baseline. The other two are:
implementing a shared per-stock lag/alignment utility
(`smart_beta/data/align.py`, does not exist yet — do not create it
yourself, another worker owns it) and exposing reusable primitives from
the portfolio-sort engine. **Assume neither of their deliverables exists
yet in your worktree**, and do not import from files owned by another
task. All three Wave 1 tasks touch completely disjoint files, so there is
no need to coordinate or wait on them.

**Your working directory for this task is:**

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-a-contract-reconciliation
```

`cd` there before running any command. It is a git worktree on branch
`phase2/task-a-contract-reconciliation`, branched from `master`. A venv
already exists at `.venv` with the project installed
(`pip install -e ".[dev]"`) and `.venv/bin/pytest` passing (115/115) before
you start. If anything looks stale, re-run
`.venv/bin/pip install -e ".[dev]"` from that directory. Run tests with
`.venv/bin/pytest`.

## File ownership

**You may create or modify exactly these four files, and no others:**

- `smart_beta/config/settings.py`
- `smart_beta/data/schema.py`
- `smart_beta/factors/base.py`
- `tests/test_schema.py`

**You must not modify anything else**, including but not limited to:
`smart_beta/data/align.py` (does not exist — another Wave 1 worker owns
it), `smart_beta/engines/*`, `smart_beta/benchmarks/*`,
`smart_beta/factors/beta.py`, `smart_beta/data/universe.py`,
`smart_beta/data/sources/*`, `smart_beta/pipelines/*`, and any test file
other than `tests/test_schema.py` (in particular, do not touch
`tests/test_beta.py` even though it imports names you are moving — see
the re-export requirement below, which is designed specifically so that
file does not need to change).

## What to do

### 1. Add four new fields to `Settings` (`smart_beta/config/settings.py`)

Add these fields to the existing `Settings` frozen dataclass, with exactly
these names, types, and defaults (the defaults must exactly match the
local constants they are replacing, so this is a pure reconciliation, not
a behavior change — nothing in Phase 1 code reads them yet; that wiring
happens in a later phase, not here):

```python
fama_macbeth_min_obs: int = 3
benchmark_size_legs: int = 2
benchmark_char_legs: int = 3
turnover_abnormal_window_months: int = 6
```

Context for each, so you can write an accurate docstring comment: `3` was
`smart_beta/engines/fama_macbeth.py`'s local `_MIN_OBS_PER_PERIOD`
(minimum cross-sectional observations per Fama-MacBeth regression period);
`2`/`3` were `smart_beta/benchmarks/capm.py`'s local `_N_SIZE_LEGS`/
`_N_CHAR_LEGS` (the 2x3 sort arity used by FF3/FF5/CH-3/CH-4 portfolio
construction); `6` was `smart_beta/benchmarks/ch4.py`'s local
`_TURNOVER_WINDOW` (trailing window, in months, used to estimate "normal"
turnover for the abnormal-turnover sentiment proxy). You do not need to
edit `fama_macbeth.py` or `benchmarks/*.py` to point at these new fields —
that wiring is a separate, later Phase 2 task (P2-D and P2-E2, Wave 2).
Your job here is only to make the fields exist on `Settings` with the
right defaults.

### 2. Add `FACTOR_PANEL_SCHEMA` and `VALUE_COL` to `smart_beta/data/schema.py`

`smart_beta/factors/base.py` currently defines these locally:

```python
VALUE_COL = "value"

FACTOR_PANEL_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={DATE_COL: "datetime", STOCK_COL: "string", VALUE_COL: "float"},
)
```

with a comment: `# TODO(schema): promote this to smart_beta/data/schema.py
once the shared schema module is unfrozen.` Move this definition
(unchanged) into `smart_beta/data/schema.py`, alongside the other
`*_SCHEMA` constants already there (`RETURN_PANEL_SCHEMA`,
`MARKET_CAP_PANEL_SCHEMA`, etc. — follow the same style).

### 3. Update `smart_beta/factors/base.py` to import from `schema.py`

Replace the local definitions with an import:

```python
from smart_beta.data.schema import DATE_COL, STOCK_COL, PanelSchema, FACTOR_PANEL_SCHEMA, VALUE_COL
```

**Important:** `FACTOR_PANEL_SCHEMA` and `VALUE_COL` must still be
importable from `smart_beta.factors.base` after this change (i.e.
`from smart_beta.factors.base import FACTOR_PANEL_SCHEMA, VALUE_COL` must
keep working, since `tests/test_beta.py` — which you must not modify —
does exactly this import). A plain `import` of the names into the module
namespace is sufficient; add them to `factors/base.py`'s `__all__` if it
has one (check the current file — it may not) so this re-export is
explicit rather than incidental. Remove the now-resolved
`# TODO(schema): ...` comment.

### 4. Add an identity test to `tests/test_schema.py`

Add a test proving the two modules genuinely share one definition, not two
copies that happen to look the same:

```python
def test_factor_panel_schema_is_shared_with_factors_base():
    from smart_beta.factors.base import FACTOR_PANEL_SCHEMA as base_schema
    from smart_beta.factors.base import VALUE_COL as base_value_col
    assert base_schema is FACTOR_PANEL_SCHEMA
    assert base_value_col is VALUE_COL
```

(adjust import placement/style to match the rest of the file.)

## Non-goals

- Do not wire the new `Settings` fields into `fama_macbeth.py` or
  `benchmarks/*.py` — that is explicitly out of scope for this task (it is
  P2-D and P2-E2, in Wave 2).
- Do not create `smart_beta/data/align.py` — a parallel task owns it.
- Do not touch `engines/portfolio_sort.py` — a parallel task owns it.
- This is a pure relocation/addition. Do not change any existing
  behavior, rename any existing public name other than moving it to a new
  home with a re-export, or "clean up" anything else you notice while in
  these files.

## Required tests / acceptance criteria

- All 115 pre-existing tests must continue to pass **unchanged** — this is
  the proof that moving `FACTOR_PANEL_SCHEMA`/`VALUE_COL` did not break
  anything that imports them (in particular `tests/test_beta.py`, which
  you must not need to touch).
- The new identity test in `tests/test_schema.py` passes.
- `git diff --stat master` (or against the branch's own merge-base) must
  show changes to exactly these four files:
  `smart_beta/config/settings.py`, `smart_beta/data/schema.py`,
  `smart_beta/factors/base.py`, `tests/test_schema.py`. If your diff
  touches anything else, that is a sign you've gone out of scope — revert
  it.

## When done

Run the full test suite (`.venv/bin/pytest`) from your worktree, commit
your changes on branch `phase2/task-a-contract-reconciliation`, and report
back:

(a) confirmation of the four new `Settings` fields and their defaults;
(b) confirmation that `FACTOR_PANEL_SCHEMA`/`VALUE_COL` now live in
`schema.py` and are re-exported from `factors/base.py`;
(c) test results (should be 116: the 115 pre-existing plus your new
identity test);
(d) the `git diff --stat` output, so file-ownership compliance can be
verified at a glance.

Do not merge your branch, do not touch `master`, and do not modify files
outside the four listed above.
