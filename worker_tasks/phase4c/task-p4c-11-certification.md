# Phase 4C Worker Task — P4C-11: Certification Gate (Wave 5, final gate)

## Background (read this first)

Read `worker_tasks/phase4c/phase4c-plan.md` in full first. This task
starts only after P4C-8, P4C-9, and P4C-10 are all merged and Barrier 4b
(the real full-suite-green gate) is confirmed. Confirm your worktree's
`master` merge-base contains all three before starting.

**This is a certification gate, not a documentation task.** Mirror the
discipline `worker_tasks/phase4b/task-p4b-9-certification.md` established:
produce an explicit, honest, executable-backed report with
PASS/FAIL/NOT CERTIFIED/`NOT RUN — reason` dispositions, never a prose
summary asserting something no test actually checked.

## File ownership

**You may create exactly these files, and no others:**

- `docs/phase4c_engine_migration.md`
- `tests/test_phase4c_certification.py`
- `tests/fixtures/phase4c_certification/` — any fixture files you need

**You must not modify anything else**, including any file under
`smart_beta/pipelines/`, `smart_beta/benchmarks/`, `smart_beta/
research_inputs/`, `smart_beta/data/`, `smart_beta/factors/`,
`smart_beta/engines/`, `smart_beta/pit/`, `smart_beta/vendors/`, or
`pyproject.toml`. This task never edits production code, only exercises
it.

## What to verify (each becomes an explicit disposition in the report, backed by a real test in `tests/test_phase4c_certification.py`)

1. **No migrated pipeline/benchmark imports legacy `DataSource`.**
   Statically inspect (e.g. via `ast`, or a simple source-text grep
   executed from a test, not just eyeballed) `smart_beta/pipelines/
   {_common,beta_portfolio,fama_macbeth_premium}.py` and
   `smart_beta/benchmarks/{capm,ff3,ff5}.py` for any import of
   `smart_beta.data.sources.base` — assert none exists.
2. **`ch3.py`/`ch4.py` still import it.** The inverse check — confirm
   `smart_beta/benchmarks/{ch3,ch4}.py` still import
   `smart_beta.data.sources.base.DataSource`, proving they were
   genuinely left untouched, not accidentally migrated or accidentally
   broken.
3. **No `pipelines/benchmarks/engines/factors` file imports any vendor
   module.** Same static-inspection technique, checking for any import
   of `smart_beta.vendors` anywhere under `smart_beta/pipelines/`,
   `smart_beta/benchmarks/`, `smart_beta/engines/`, `smart_beta/factors/`,
   and `smart_beta/research_inputs/` itself.
4. **Frozen Phase-2 lag invariants remain true, reproduced directly, not
   just re-run from existing tests.** `beta(t-1)->return(t)` and
   `market_cap(t-1)->weighting return(t)` — construct (or reuse) a
   scenario with a hand-computable expected value proving the lag is
   real, for at least `build_beta_sorted_portfolios`.
5. **Realized-return consumers use `adj_ret`.** Reproduce the
   split-adjustment regression (real AAPL 2020-08-31 specimen) end to
   end through at least one migrated pipeline and one migrated
   benchmark, confirming the adjusted (not raw) return is what actually
   entered the computation.
6. **Weighting/screening uses `total_mcap`.** Confirm no migrated file's
   real, executed code path ever reads `float_mcap`.
7. **Incomplete fundamentals cannot silently shrink the sample under the
   default policy.** Reproduce P4C-9's (and P4C-10's, for FF3/FF5)
   strict-by-default regression directly here, independently, against
   the real merged code — not by importing and re-running their test
   files, but by calling the real pipeline/benchmark functions yourself
   with a genuinely unreconcilable real fixture and confirming
   `FundamentalsCoverageError` is raised.
8. **Tradability policy is explicit, not fabricated.** Confirm
   `USZeroVolumeTradabilityPolicy` never claims equivalence to
   `is_suspended` (re-read its docstring/behavior directly) and that no
   migrated pipeline/benchmark has a default `policy` value (inspect the
   real function signatures — a default would silently violate this).
9. **Risk-free is separately injected.** Confirm no migrated
   pipeline/benchmark file calls a `.get_risk_free` method on anything
   other than an injected `RiskFreeProvider`.
10. **Identifier-continuity limitations remain machine-visible.** Run
    `research_inputs.identifier_continuity.check_identifier_continuity`
    against real Tiingo-resolved identifiers (reuse real fixtures) and
    confirm `IDENTIFIER CONTINUITY = NOT CERTIFIED`-equivalent evidence
    (`certified=False`) is what a Tiingo-backed research run would
    actually see — this task does not change that finding, it certifies
    it is still correctly surfaced after migration.
11. **Behavior-preservation numeric regressions pass.** Re-run (by
    calling the real functions directly, not by trusting the earlier
    tasks' own test suites) at least one China/synthetic-backed scenario
    per migrated pipeline/benchmark, confirming numeric equivalence to
    the pre-Phase-4C behavior documented in each task's own report.
12. **Real Tiingo end-to-end paths work only for the explicitly supported
    scope.** Run each migrated pipeline/benchmark against the real
    `TiingoPITSource`, and if any fails for a reason already known and
    certified in Phase 4B (e.g. TWTR fundamentals plan-tier 400), report
    that as the expected, correct outcome — never as a Phase 4C defect,
    and never smoothed into a false PASS.
13. **Phase-4B NOT CERTIFIED findings remain NOT CERTIFIED.** Explicitly
    re-state, per finding, whether Phase 4C's migration changed anything
    about it: `RESTATEMENT/VINTAGE RECONSTRUCTION`, `IDENTIFIER
    CONTINUITY`, `FLOAT MARKET CAP`, `TWTR DELISTING CORROBORATION`,
    `FUNDAMENTALS RANGED-QUERY SEMANTICS` — the expected, required
    finding for all five is "unchanged, still NOT CERTIFIED"; if your own
    testing surfaces genuinely new evidence that would change any of
    them, stop and report it explicitly rather than updating the
    disposition yourself.

## The certification report document

Write `docs/phase4c_engine_migration.md` by hand, containing at minimum:
a disposition line (PASS/FAIL/NOT CERTIFIED/`NOT RUN — reason`) for each
of the 13 items above; the five Phase 4B NOT CERTIFIED lines restated
verbatim with "unchanged" confirmed; a short "what remains deferred"
list (CH3/CH4 PIT migration, Phase 4D China adapter, a real production
`RiskFreeProvider` data source, FactorSpec/Phase 5); and a completeness
statement mirroring `docs/phase4b_tiingo_certification.md`'s own
discipline — no considered item silently absent.

## Non-goals

- Do not fix anything you find broken — report it as a FAIL or a
  blocker, and stop; do not patch production code to make your own
  certification pass.
- Do not weaken any fail-closed or NOT CERTIFIED semantic to obtain a
  cleaner report.
- Do not touch `benchmarks/ch3.py`/`ch4.py` even if you find their tests
  or code have drifted from what you expect — report the drift.

## Acceptance criteria

- `tests/test_phase4c_certification.py` passes in full, and every
  assertion in it is backed by real, executed code — no test that only
  checks a string literal is present in the markdown without a
  corresponding executable check elsewhere (mirroring P4B-9's own
  discipline: a markdown-presence check is acceptable only as a
  synchronization guard alongside a real check, never as the sole proof
  of a claim).
- `docs/phase4c_engine_migration.md` contains all 13 dispositions plus
  the five restated NOT CERTIFIED lines.
- Full suite green.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly the three file/directory locations listed under
  File ownership.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-11-certification
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch `phase4c/task-p4c-11-certification`,
touching only the files listed above. **Do not merge this branch to
master** — report back to the Planner for final review before any merge.

## If you discover a contract contradiction

Stop and report it rather than improvising or silently patching anything.

## When done

Report: (a) the full 13-item disposition tally; (b) the five restated
Phase-4B NOT CERTIFIED lines and confirmation none changed; (c) any
genuinely new evidence you found that could change one of them (do not
apply the change yourself); (d) test results; (e) `git diff --stat`; (f)
the full text of `docs/phase4c_engine_migration.md`. Do not touch
`master`, do not modify files outside the list above.
