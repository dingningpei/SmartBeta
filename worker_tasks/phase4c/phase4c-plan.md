# Phase 4C Frozen Architecture: Engine Migration onto the Trusted PIT Boundary

Baseline: `master` at commit `634c31c`, tagged `phase4b-complete`
(713 passed / 1 skipped without a live Tiingo credential).

This document is the frozen record of Phase 4C's design, after an
architecture review round that corrected two drafts (a too-permissive
fundamentals-coverage default, and a "compatibility shim" framing that
risked recreating the legacy `DataSource` contract). It is the reference
every P4C-1..11 worker spec points back to instead of re-deriving context.

## Scope

Migrate the Phase 0-2 research engines (universe construction, beta
estimation, portfolio sorts, Fama-MacBeth, CAPM/FF3/FF5 benchmarks) onto
`smart_beta.pit.source.PITDataSource` / `smart_beta.pit.view.PointInTimeView`
so they no longer depend on `smart_beta.data.sources.base.DataSource`,
**without** coupling any research engine, pipeline, or benchmark to
`smart_beta.vendors.tiingo` by name.

```
Vendor (Tiingo, future SEC/EDGAR, future China vendor)
    |
PITDataSource adapter                          (frozen, Phase 4B)
    |
PointInTimeView / AsOfSnapshot                 (frozen, Phase 3)
    |
smart_beta.research_inputs.*                   (NEW, Phase 4C -- this
    |                                            phase's real deliverable)
Research engines (factors/, engines/, data/align.py) -- UNCHANGED
    |
pipelines/*, benchmarks/{capm,ff3,ff5}.py       -- signatures migrated
```

`smart_beta.research_inputs` is a **new trusted research-input contract**,
not a translation layer back to the old `DataSource` shape. Every
consequential choice (adjusted-vs-raw returns, total-vs-float market cap,
which trading-status semantics count as "tradable", how fundamentals
coverage gaps are handled, where risk-free data comes from, whether
identifier continuity is safe for a given run) is made **explicit in a
name or a type**, never hidden behind a column name that happens to match
the legacy schema.

**Deferred, not built here:** `benchmarks/ch3.py`/`ch4.py` (Liu-Stambaugh-
Yuan is explicitly a China-A-share-specific model; its own existing
docstring already documents its turnover proxy as a placeholder even for
China -- no US equivalent is fabricated). A real China `PITDataSource` and
CH3/CH4's PIT migration are both Phase 4D's job. `smart_beta.data.sources.
base.DataSource` and `smart_beta.data.sources.synthetic.SyntheticDataSource`
are **not removed or deprecated** -- `ch3.py`/`ch4.py` and their tests keep
using them unchanged throughout Phase 4C.

## Three frozen clarifications (binding on every task below)

1. **Fundamentals chunking is a retrieval-granularity detail, not a fiscal
   claim.** `interval_width_days` divides a wide `[start, end]` request into
   deterministic, contiguous, non-overlapping calendar sub-ranges purely so
   each individual `PITDataSource.get_fundamentals` call stays narrow enough
   to avoid the demonstrated wide-range coverage failure (P4B-9's finding).
   It is never treated as, or documented as, a fiscal quarter boundary --
   the orchestration does not know or claim to know any company's real
   fiscal calendar.
2. **A NOT CERTIFIED or fail-closed signal is never silently absorbed by a
   default.** Fundamentals coverage defaults to strict completeness
   (raises unless the caller explicitly opts into partial results, and even
   then the coverage evidence is never discarded). Identifier-continuity
   evidence survives every override mode, including `allow`. No task may
   turn a Phase 4B NOT CERTIFIED finding into an implicit PASS.
3. **Every consequential choice is explicit, not inherited from the old
   schema.** `adj_ret` (never `ret`), `total_mcap` (never `float_mcap`,
   never a silent default to it), an explicit `TradabilityPolicy` object
   (never a hardcoded flag list), a separate `RiskFreeProvider` (never a
   method on `PITDataSource`).

## Subpackage layout

```
smart_beta/research_inputs/
    __init__.py                    # empty marker (multi-task-owned)
    risk_free.py                   # RiskFreeProvider ABC + reference impls   [P4C-1]
    tradability.py                 # TradabilityPolicy ABC + China/US impls   [P4C-2]
    fundamentals_coverage.py       # coverage-aware retrieval, fail-closed    [P4C-3]
    identifier_continuity.py       # structured continuity guard/evidence     [P4C-4]
    inputs.py                      # get_realized_returns / get_capitalization_weights /
                                    # get_tradability -- the actual trusted-input
                                    # functions, wiring the above + PointInTimeView [P4C-6]
smart_beta/data/universe.py        # amended: injectable TradabilityPolicy    [P4C-5]
smart_beta/pipelines/_common.py    # amended: consumes research_inputs        [P4C-7]
smart_beta/pipelines/beta_portfolio.py       # amended                         [P4C-8]
smart_beta/pipelines/fama_macbeth_premium.py # amended                         [P4C-9]
smart_beta/benchmarks/{capm,ff3,ff5}.py      # amended, one task (shared helpers) [P4C-10]
docs/phase4c_engine_migration.md              # certification record            [P4C-11]
```

## Task table

| Task | Objective | Owned production files | Owned test files | Depends on | Wave |
|---|---|---|---|---|---|
| P4C-1 | `RiskFreeProvider` contract + reference impls | `research_inputs/{__init__,risk_free}.py` | `tests/test_research_inputs_risk_free.py` | none | 1 |
| P4C-2 | `TradabilityPolicy` contract + China/US impls | `research_inputs/tradability.py` | `tests/test_research_inputs_tradability.py` | none | 1 |
| P4C-3 | Fail-closed fundamentals coverage orchestration | `research_inputs/fundamentals_coverage.py` | `tests/test_research_inputs_fundamentals_coverage.py` | none | 1 |
| P4C-4 | Identifier-continuity structured guard | `research_inputs/identifier_continuity.py` | `tests/test_research_inputs_identifier_continuity.py` | none | 1 |
| P4C-5 | Inject `TradabilityPolicy` into `build_tradable_universe` | `data/universe.py` (amend) | `tests/test_universe.py` (amend, additive only) | P4C-2 | 2 |
| P4C-6 | Trusted Research Inputs (the central boundary) | `research_inputs/inputs.py` | `tests/test_research_inputs.py` | P4C-1, P4C-2, P4C-3, P4C-4, P4C-5 | 3 |
| P4C-7 | Migrate `pipelines/_common.py`; split shared test infra | `pipelines/_common.py` (amend) | new `tests/test_pipelines_common.py`; **mechanically splits** `tests/test_pipelines.py` into `tests/test_beta_portfolio_pipeline.py` + `tests/test_fama_macbeth_pipeline.py` (moves bodies as-is, does not fix their now-stale calls) and deletes the old file | P4C-6 | 4a |
| P4C-8 | Migrate `pipelines/beta_portfolio.py` | `pipelines/beta_portfolio.py` (amend) | `tests/test_beta_portfolio_pipeline.py` (created empty-shell by P4C-7; P4C-8 fixes/extends its content) | P4C-7 | 4b |
| P4C-9 | Migrate `pipelines/fama_macbeth_premium.py` | `pipelines/fama_macbeth_premium.py` (amend) | `tests/test_fama_macbeth_pipeline.py` (created empty-shell by P4C-7; P4C-9 fixes/extends its content) | P4C-7 | 4b |
| P4C-10 | Migrate `benchmarks/{capm,ff3,ff5}.py` (one task; shared private helpers) | `benchmarks/capm.py`, `benchmarks/ff3.py`, `benchmarks/ff5.py` (amend) | splits `tests/test_benchmarks.py` into `tests/test_benchmarks_capm_ff.py` (migrated) and leaves a `tests/test_benchmarks_ch.py` behind, unmigrated, still `DataSource`-based, for CH3/CH4 | P4C-6 | 4b |
| P4C-11 | Certification gate | `docs/phase4c_engine_migration.md` | `tests/test_phase4c_certification.py` | P4C-8, P4C-9, P4C-10 | 5 |

## Dependency DAG

```
Wave 1 (parallel -- four disjoint new files)
  P4C-1   P4C-2   P4C-3   P4C-4
Barrier 1: each task's own adversarial tests green; full suite green
  (nothing existing touched yet).

Wave 2 (single task -- amends the one shared file data/universe.py)
  P4C-5  (needs P4C-2)
Barrier 2: build_tradable_universe behavior-preservation proven
  byte-identical for every pre-existing fixture with no policy argument
  passed. Full suite green.

Wave 3 (single task -- the central boundary)
  P4C-6  (needs P4C-1, P4C-2, P4C-3, P4C-4, P4C-5)
Barrier 3: schema/behavior tests against both a PIT-wrapped synthetic
  fixture and the real merged TiingoPITSource. Full suite green.

Wave 4a (single task -- MUST land before Wave 4b; NOT parallel with it)
  P4C-7  (needs P4C-6)
Barrier 4a: P4C-7's OWN new tests (test_pipelines_common.py) green.
  **Full suite is NOT required to be green at this barrier** --
  pipelines/beta_portfolio.py and pipelines/fama_macbeth_premium.py are
  expected to fail after P4C-7 lands, until P4C-8/P4C-9 land in Wave 4b.
  This is a documented, deliberate, single-wave-spanning state, not a
  regression. State this explicitly in P4C-7's own report; do not treat
  it as something to silently work around.

Wave 4b (parallel -- three disjoint file groups, all depend on P4C-7 only,
         not on each other)
  P4C-8   P4C-9   P4C-10
Barrier 4b: THE real full-suite-green gate for the whole migration.
  Behavior-preservation regression per migrated function; at least one
  real-TiingoPITSource end-to-end run per task, scoped to what the
  current plan tier genuinely supports.

Wave 5 (standalone)
  P4C-11 (needs P4C-8, P4C-9, P4C-10)
Final Phase 4C barrier: certification gate, PASS/FAIL/NOT CERTIFIED/
  NOT RUN dispositions, full suite green.
```

**Do not create a later-wave worktree before the preceding barrier passes.**
Wave 4a is a real exception to "full suite green" as stated above --
every other barrier requires it unconditionally.

## Ownership collisions found and resolved during planning (read this before writing any task)

- `smart_beta/data/universe.py` and `tests/test_universe.py`: touched by
  exactly one task, P4C-5, in its own wave. Not shared with P4C-2 (which
  owns the *policy* module only, not `universe.py`).
- `tests/test_pipelines.py`: currently a single file mixing
  `build_beta_sorted_portfolios` and `build_fama_macbeth_premium` tests
  (some functions, e.g. determinism, test both in one body). P4C-8 and
  P4C-9 each need to change one of those two functions' signatures, so
  both would otherwise need to edit this one file -- a real collision
  inside a parallel wave. Resolved by giving the mechanical split to
  **P4C-7 alone** (Wave 4a, not parallel with anything): it moves the
  beta-portfolio-relevant test bodies into a new `tests/test_
  beta_portfolio_pipeline.py` and the Fama-MacBeth-relevant bodies into a
  new `tests/test_fama_macbeth_pipeline.py` **as-is** (their calls will be
  stale/broken until P4C-8/P4C-9 fix them -- consistent with Wave 4a's
  documented non-green state), deletes the old `tests/test_pipelines.py`,
  and updates `_common.py`'s module docstring (which currently names
  `tests/test_pipelines.py` as the one file allowed to import its private
  helpers) to name the two new files instead. P4C-8 owns and finishes
  `tests/test_beta_portfolio_pipeline.py`; P4C-9 owns and finishes
  `tests/test_fama_macbeth_pipeline.py`. Disjoint from that point on.
- `tests/test_benchmarks.py`: a single file with one `pytest.mark.
  parametrize` list covering `compute_market_excess_return`,
  `compute_ff3_factors`, `compute_ff5_factors` (migrating) **and**
  `compute_ch3_factors`, `compute_ch4_factors` (not migrating) together.
  Only one task (P4C-10) touches benchmarks in Wave 4b, so this is not a
  parallel-wave collision, but it is still a real hazard: a careless edit
  could break CH3/CH4's still-valid, still-`DataSource`-based tests.
  Resolved by giving P4C-10 the explicit job of splitting this file into
  `tests/test_benchmarks_capm_ff.py` (migrated signature) and `tests/
  test_benchmarks_ch.py` (left exactly as it was, still exercising CH3/CH4
  against `SyntheticDataSource`, completely unmigrated).
- No other test file collision was found. `smart_beta/data/sources/
  synthetic.py` and `smart_beta/data/sources/base.py` are not owned by any
  Phase 4C task and must not be modified by any of them -- CH3/CH4's tests
  need them to keep working exactly as they are today.
- Fixture directories for the three Wave-4b tasks are reserved by exact
  path, not left to each worker's own naming judgment (a lesson carried
  over from Phase 4B's "your own subdirectory" ambiguity): P4C-8 owns
  `tests/fixtures/phase4c/beta_portfolio/`, P4C-9 owns
  `tests/fixtures/phase4c/fama_macbeth/`, P4C-10 owns
  `tests/fixtures/phase4c/benchmarks/`. P4C-6 owns
  `tests/fixtures/research_inputs/`; P4C-11 owns
  `tests/fixtures/phase4c_certification/`.

## Status

Architecture frozen (prior turn). This document and the eleven task specs
exist. No worktrees, no venvs, no Pi workers, and no production code
changes exist for Phase 4C yet.
