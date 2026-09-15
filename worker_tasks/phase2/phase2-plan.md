# Phase 2 Execution Plan (approved, with 2 revisions)

Baseline: `master` at commit `37f64c9` (Phase 1 integration complete, six
tasks A-F merged, 115/115 tests green, no Phase 1 feature branches or
worktrees remaining).

This plan was reviewed and approved with two revisions before execution:

1. **P2-A test ownership.** P2-A's identity test lives in `tests/test_schema.py`,
   which is therefore explicitly part of P2-A's file ownership (not a gap
   between "requires a new test" and "only touches 3 files").
2. **P2-F is a standalone final wave.** P2-F (pipeline integration) is not
   part of Wave 2. It is Wave 3, created only after P2-D and P2-E2 are both
   independently reviewed, merged, and master is green. P2-D and P2-E2 are
   hard dependencies of P2-F (not just "soft, recommended" as in the earlier
   draft).

## Tasks

### P2-A — Contract reconciliation (Settings/schema)

Land every deferred `Settings`/`schema.py` addition flagged in the Phase 1
integration review, in one place, before anything downstream depends on
them.

- **May create/modify:** `smart_beta/config/settings.py`,
  `smart_beta/data/schema.py`, `smart_beta/factors/base.py`,
  `tests/test_schema.py`.
- **Must not modify:** anything else — `engines/*`, `benchmarks/*`,
  `data/align.py` (does not exist yet; created by P2-B, a parallel Wave 1
  task — do not create it here), `data/universe.py`, `data/sources/*`,
  `factors/beta.py`, `pipelines/*`, any test file other than
  `tests/test_schema.py`.
- **Dependencies:** none.
- **Parallel-safe:** yes, alongside P2-B and P2-E1 (disjoint files).

### P2-B — Canonical alignment contract + shared lag utility

Define the long-format panel alignment convention (a characteristic at
date *t* explains the return at *t+1* by default; an "as-of" factor panel
like beta is inclusive of *t* and must be explicitly lagged before being
paired with a same-date return) and implement the single reusable utility
that operationalizes it, generalizing Task D's `lag_characteristics`.

- **May create:** `smart_beta/data/align.py` (new), `tests/test_align.py`
  (new).
- **Must not modify:** anything else, in particular
  `smart_beta/engines/fama_macbeth.py` (consuming the new utility there is
  P2-D's job, in Wave 2, not this task's).
- **Dependencies:** none.
- **Parallel-safe:** yes, alongside P2-A and P2-E1 (disjoint files).

### P2-E1 — Expose reusable primitives from the portfolio-sort engine

Without changing `sort_portfolios`/`double_sort_portfolios`/
`long_short_return`'s existing public behavior, promote the internal
group-assignment and group-return-stat helpers to a public, documented,
directly-testable surface so `benchmarks/*` (P2-E2, Wave 2) can reuse them
instead of maintaining a private duplicate.

- **May modify:** `smart_beta/engines/portfolio_sort.py`,
  `tests/test_portfolio_sort.py`.
- **Must not modify:** anything else — `benchmarks/*`,
  `engines/fama_macbeth.py`, `engines/inference.py`, `factors/*`, `data/*`.
- **Dependencies:** none.
- **Parallel-safe:** yes, alongside P2-A and P2-B (disjoint files).

### P2-D — Fama-MacBeth: consolidate lag usage and Newey-West (Wave 2)

Refactor `fama_macbeth.py` to use `data.align.lag_panel` instead of its
local `lag_characteristics`, use `engines.inference.newey_west_ols` instead
of its local `_newey_west_mean`, and read `settings.fama_macbeth_min_obs`
instead of the local `_MIN_OBS_PER_PERIOD` constant.

- **May modify:** `smart_beta/engines/fama_macbeth.py`,
  `tests/test_fama_macbeth.py`.
- **Dependencies:** P2-A, P2-B (hard — both must be merged first).
- **Parallel-safe:** yes, alongside P2-E2, once Wave 1 is merged.

### P2-E2 — Benchmarks: consolidate portfolio-sort duplication and lag usage (Wave 2)

Replace `benchmarks/capm.py`'s private group-assignment/value-weighting
helpers with calls into `engines.portfolio_sort`'s newly-public primitives,
and replace inline per-stock shifts with `data.align.lag_panel`.

- **May modify:** `smart_beta/benchmarks/{capm,ff3,ff5,ch3,ch4}.py`,
  `tests/test_benchmarks.py`.
- **Dependencies:** P2-B, P2-E1 (hard — both must be merged first).
- **Parallel-safe:** yes, alongside P2-D, once Wave 1 is merged.

### P2-F — Pipeline layer + end-to-end tests (Wave 3, standalone)

Populate the empty `smart_beta/pipelines/` stubs with orchestration
functions wiring universe -> beta (explicitly lagged) -> portfolio sort /
Fama-MacBeth -> inference, with the lag step enforced and tested rather
than left to convention.

- **May create:** content for the existing empty `smart_beta/pipelines/*`
  stubs, `tests/test_pipelines.py` (new, includes end-to-end tests).
- **Must not modify:** `engines/*`, `factors/*`, `benchmarks/*`, `data/*`.
- **Dependencies:** P2-A, P2-B (needed directly) **and, per revision 2,
  P2-D and P2-E2 as hard dependencies** — P2-F is not created until Wave 2
  is fully merged and master is green.
- **Parallel-safe:** N/A — sole occupant of Wave 3.

## Dependency DAG

```
Wave 1 (parallel, no deps)      P2-A   P2-B   P2-E1
                                  |      |  \    |
                                  |      |   \   |
Barrier 1: all 3 merged,          |      |    \  |
full suite green                 |      |     \ |
                                  v      v      v v
Wave 2 (parallel)                P2-D          P2-E2
                                  (needs A,B)   (needs B,E1)
                                     \            /
                                      \          /
Barrier 2: both merged,               \        /
full suite green                       v      v
Wave 3 (standalone)                     P2-F
                                    (needs A,B,D,E2)
```

## Execution waves

- **Wave 1:** P2-A, P2-B, P2-E1 — 3 parallel Pi workers, fully disjoint
  files.
- **Barrier 1:** all three independently reviewed, merged, full master
  suite green.
- **Wave 2:** P2-D, P2-E2 — 2 parallel Pi workers, fully disjoint files.
- **Barrier 2:** both independently reviewed, merged, full master suite
  green.
- **Wave 3:** P2-F — single Pi worker, created from master only after
  Barrier 2, since P2-D and P2-E2 are hard dependencies.

## Category D — deferred, not scheduled

Real E/P field, turnover/volume field, and any other `DataSource` gap
`benchmarks/ch3.py`/`ch4.py` currently proxy around. Backlog only; no task
ID, no branch. Revisit only when a real data vendor is scheduled.

## Status

This document covers the full plan. **Only Wave 1 (P2-A, P2-B, P2-E1) is
being executed now.** Wave 2 and Wave 3 worker specs, worktrees, and
branches are not created until their respective barriers are cleared.
