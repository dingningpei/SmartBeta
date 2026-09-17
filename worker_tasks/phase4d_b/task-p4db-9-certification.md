# Phase 4D-B Worker Task — P4DB-9: Certification (Wave 4)

## Background (read this first)

Read `worker_tasks/phase4d_b/phase4d-b-plan.md` in full, especially its
"Certification criteria" section — this task's deliverable must satisfy
every item listed there, with no silent omission. Read
`docs/phase4b_tiingo_certification.md` (Tiingo's equivalent, if present)
or `docs/phase4c_engine_migration.md` and
`tests/test_phase4c_certification.py` for the house style of a
certification document and its accompanying test: **every considered
item gets an explicit `PASS`/`FAIL`/`NOT CERTIFIED`/`NOT RUN — reason`
line, and a test enforces that the document contains every required
line** (a completeness invariant, not just a prose report).

This is the Wave 4 task — depends on P4DB-8 (merged, Barrier 3 passed).
You are the only task in this wave.

**Your working directory** will be a git worktree at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-9-certification`
on branch `phase4d_b/task-p4db-9-certification`, branched from `master`
after Barrier 3.

## File ownership

**You may create exactly:**

- `docs/phase4d_b_tushare_certification.md`
- `tests/test_phase4d_b_certification.py`
- `tests/fixtures/tushare/certification/` (only for specimens not
  already covered by an earlier task's fixtures — prefer reuse)

**You must not modify** any `vendors/tushare/*.py` file,
`smart_beta/pit/*`, `smart_beta/data/*`, or any existing test file.

## Required certification report content (every line below, verbatim or as the specific finding requires)

Run Phase 3's existing `pit.compliance.check_*` functions against the
real, assembled `TushareAShareSource` using the real specimens named
throughout this plan (do not substitute synthetic data for any item
where a real specimen was already proven reachable). For each:

1. `SCHEMA CONFORMANCE = <PASS/FAIL>` (all seven `PITDataSource`
   methods).
2. `CUMULATIVE VS SINGLE-QUARTER RECONCILIATION = PASS` — cite the exact
   `000001.SZ` FY2022 numbers (138,265,000,000 − 92,022,000,000 =
   46,243,000,000, matching the independently-tagged `report_type=2`
   record exactly) as run through the real assembled source, not just
   asserted from the planning dossier.
3. `CHINA FUNDAMENTALS VINTAGE CAPABILITY = PASS` — cite `600518.SH` and
   `002450.SZ`, run through the real assembled source.
4. `CHINA FUNDAMENTALS VINTAGE COVERAGE COMPLETENESS = NOT CERTIFIED` —
   name `002069.SZ` FY2017 explicitly as the standing counterexample.
   **Do not omit this line even though item 3 is a PASS** — this is the
   capability-vs-coverage distinction that must never collapse into one
   status.
5. `KNOWLEDGE-DATE RULE = PASS` (as an adapter-internal parsing policy)
   — cite the `002450.SZ` stale-`ann_date`/`f_ann_date=20210228`
   specimen and confirm both the firing and non-firing fallback cases
   from P4DB-6 pass against the assembled source. Include the explicit
   caveat sentence from plan.md policy 3: this is a Tushare-proxy-
   adapter-scoped parsing rule, not a general claim about `f_ann_date`'s
   meaning for any other vendor or access path.
6. `CH3 NI-EX-NONRECURRING = FIELD-LEVEL, VINTAGE-JOIN-DEPENDENT, NOT A
   SINGLE CANONICAL FIELD` — cite the `600518.SH`/`002450.SZ`
   suppression tests and at least one successful-join specimen, run
   through the assembled source.
7. `TOTAL MARKET CAP = PASS (canonical)`. `FLOAT MARKET CAP = DIAGNOSTIC
   ONLY, NOT INDEPENDENTLY CERTIFIED` — do not let this read as a PASS.
8. `CH4 TURNOVER FEASIBILITY = PASS (mechanical only)` — cite the
   `000001.SZ` 2013-2014 window (295 rows, 251 usable, distinct
   `total_share` values `512,335 / 819,736 / 952,075`), explicitly
   stating this is not a PIT-immutability certification of historical
   `total_share`.
9. `CHANGING ADJ_FACTOR RECONSTRUCTION = PASS` — cite the `000001.SZ`
   2013-06-20 specimen (`total_share` ×1.6, `adj_factor` 36.173→58.387).
10. `IDENTIFIER CONTINUITY = <status from P4DB-2's report>` — carry
    forward exactly whatever P4DB-2 found; if unchanged from `NOT
    CERTIFIED`, say so explicitly rather than omitting the line because
    "nothing changed."
11. `DELISTING CORROBORATION = <status from P4DB-7's report>` — same
    discipline; if no reachable delisted specimen was found, the literal
    line must say so (mirroring Tiingo's honest TWTR outcome), never a
    silently-omitted item.
12. `PROXY DETERMINISM = <PASS/FAIL>` — run the canonical-consistency
    check (P4DB-1 policy 11) across every certification specimen's
    fixture-recording history; if `TushareNonDeterministicResponseError`
    fired for any of them during any task's fixture recording (check
    each task's own report for this), this must be `FAIL`, naming the
    offending request — do not let a lower-severity issue be smoothed
    into a PASS.
13. `PROXY SUFFICIENT FOR PHASE 4D-B POC = YES`.
14. `PROXY SUFFICIENT FOR PRODUCTION = NO` — restate the reasons
    (provenance/ToS/dependency risk from the original access
    characterization, plus the blank-out and stale-ann_date failure
    modes this phase's own evidence found).
15. `TUSHARE LICENSING/ATTRIBUTION = <PASS/FAIL>` — verify every
    committed fixture across all eight prior tasks carries
    `source_vendor=Tushare` and retrieval provenance (per the existing
    licensing disposition: attribution required, no bulk redistribution)
    — this is a cross-task audit, not just your own fixtures.

## The completeness invariant test

Write `tests/test_phase4d_b_certification.py` mirroring
`tests/test_phase4c_certification.py`'s pattern: parse
`docs/phase4d_b_tushare_certification.md` and assert every one of the 15
line-items above is present with an explicit status — the test must fail
if any item is missing, not just if a status is wrong. This is the
mechanism that makes "no silent omission" enforceable rather than
aspirational.

## Fixture inputs

Reuse fixtures from every prior task's own subdirectory; record new ones
under `tests/fixtures/tushare/certification/` only for scenarios not
already covered (e.g., running a full end-to-end query spanning multiple
Wave 2 modules' outputs together, if no earlier task's fixtures cover
that combination).

## Non-goals

No new adapter mapping logic — if a certification run reveals a genuine
defect in an already-merged Wave 1/2/3 module, do not fix it here; report
it as a specific, named finding for a follow-up task, exactly as P4DB-8
is instructed to do for its own findings.

## Acceptance criteria

- `.venv/bin/pytest` green, full suite unaffected.
- `docs/phase4d_b_tushare_certification.md` contains all 15 required
  lines, each backed by a real specimen run through the real assembled
  `TushareAShareSource` (not a re-statement of the planning dossier's
  claims without re-verification) or an explicit `NOT RUN — reason`.
- The completeness-invariant test fails if any line is removed (verify
  this yourself by temporarily deleting one line and confirming the test
  catches it, then restoring the line — do not leave this untested
  reasoning implicit).

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-9-certification
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4d_b/task-p4db-9-certification`, touching only the files listed
above.

## When done

Report: (a) the full certification document's 15 line-item statuses,
verbatim; (b) any status that changed from what this plan expected
(e.g. if a re-run specimen behaved differently than the planning
dossier's evidence — proxy state may have changed since the original
investigation), with evidence; (c) any Wave 1/2/3 defect found and its
proposed follow-up task shape; (d) test results, including the
completeness-invariant self-test; (e) `git diff --stat`. Do not merge,
do not touch `master`.
