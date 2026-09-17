# Phase 4D-B Worker Task — P4DB-9: Certification (Wave 4)

## Background (read this first)

**Amended by the 2026-09-17 independent audit (Patch 5). This file is
now the single normative source for the required certification
line-item list** — `phase4d-b-plan.md`'s own "Certification criteria"
section has been reduced to a pointer back to this file, to eliminate
the drift the audit found between two independently-numbered copies of
what was meant to be the same list. If you ever find `phase4d-b-plan.md`
describing a different count or a different item than this file, this
file wins; report the discrepancy, do not silently reconcile it
yourself.

Read `docs/phase4c_engine_migration.md` and
`tests/test_phase4c_certification.py` for the house style of a
certification document and its accompanying test. That test's own
docstring states the doctrine this task must follow exactly: **"a
markdown-presence check exists only as a synchronization guard alongside
the executable checks, never as the sole proof of a claim."** Every
`PASS`/`FAIL`/`NOT CERTIFIED`/`NOT RUN — reason` line below must
correspond to a real, executable assertion in
`tests/test_phase4d_b_certification.py` against the real, assembled
`TushareAShareSource` — the markdown document is evidence of what was
found, not the proof itself, and a future re-run of this suite must be
capable of catching a regression, not just a missing line.

**Frozen dispositions — no fixture result, however clean, may change
these four exact values.** `CHINA FUNDAMENTALS VINTAGE CAPABILITY =
PASS`, `CHINA FUNDAMENTALS VINTAGE COVERAGE COMPLETENESS = NOT
CERTIFIED`, `PROXY SUFFICIENT FOR PHASE 4D-B POC = YES`, `PROXY
SUFFICIENT FOR PRODUCTION = NO`. These are architectural/evidentiary
conclusions from the Phase 4D-A/A.2/A.3 investigation and this phase's
own audit, not open questions this task re-derives from scratch.
Proxy-observed evidence, however extensive, must never be read as
certifying direct official-Tushare API behavior at any point tier — if
your certification run produces evidence that seems to argue for
upgrading any of these four, **do not upgrade them; report the
seemingly-contradictory evidence as a specific, named finding for
independent review instead.**

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
16. **(New, Patch 4.)** `KNOWN-MISSING (BLANK-OUT) HANDLING = PASS` —
    cite the `002450.SZ` FY2016/17 blank-out specimen (P4DB-6 policy 6),
    **run through the fully assembled `TushareAShareSource`, not merely
    re-cited from P4DB-6's own unit test.** Explicitly confirm, as
    separate assertions, that the vendor-blanked value is machine-visible
    as `is_blank_out=True` at the assembled-source level and is never
    silently converted into any of: (a) no observation at all (the row
    is absent from the output entirely), (b) a `0.0` value, (c) an
    ordinary `NaN` indistinguishable from any other missing value (i.e.
    a consumer inspecting only `value` and not `is_blank_out` must not
    be able to mistake this for routine unavailability), or (d) evidence
    that no restatement occurred for that period (blank-out and
    restatement status are independent facts — a blanked field says
    nothing about `is_restatement`, and this test must not conflate
    them). This is executable negative evidence, not a re-statement of
    P4DB-6's own claim.

## The completeness invariant test

Write `tests/test_phase4d_b_certification.py` mirroring
`tests/test_phase4c_certification.py`'s pattern: parse
`docs/phase4d_b_tushare_certification.md` and assert **two** things, not
one:

1. **Presence.** Every one of the 16 line-items above is present with an
   explicit status — the test must fail if any item is missing.
2. **Frozen values.** Items 3 (`CHINA FUNDAMENTALS VINTAGE CAPABILITY`),
   4 (`CHINA FUNDAMENTALS VINTAGE COVERAGE COMPLETENESS`), 13 (`PROXY
   SUFFICIENT FOR PHASE 4D-B POC`), and 14 (`PROXY SUFFICIENT FOR
   PRODUCTION`) must carry **exactly** the frozen values stated in
   Background above (`PASS`, `NOT CERTIFIED`, `YES`, `NO` respectively)
   — the test must fail if any of these four specific values has
   changed, not merely if the line is missing. This is what makes the
   Background section's "no fixture result may upgrade these" rule
   enforceable rather than aspirational, and it is the specific gap the
   2026-09-17 audit found: a presence-only check cannot catch
   `PROXY SUFFICIENT FOR PRODUCTION = YES` being written by a future
   worker who believes a clean run justifies it.

Per the doctrine in Background, this markdown-parsing test is a
**synchronization guard only** — it does not, by itself, prove any of
the 16 claims. Each numbered item's real proof is the corresponding
`pit.compliance.check_*` call (or equivalent direct assertion) against
the real, assembled `TushareAShareSource`, in this same test file or a
sibling one you write, actually exercised — not merely asserted to exist
in prose.

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
- `docs/phase4d_b_tushare_certification.md` contains all 16 required
  lines, each backed by a real specimen run through the real assembled
  `TushareAShareSource` (not a re-statement of the planning dossier's
  claims without re-verification) or an explicit `NOT RUN — reason`.
- The completeness-invariant test fails if any line is removed (verify
  this yourself by temporarily deleting one line and confirming the test
  catches it, then restoring the line — do not leave this untested
  reasoning implicit).
- The completeness-invariant test also fails if item 4, 13, or 14's
  value is changed to anything other than its frozen value (verify this
  yourself the same way: temporarily change `PROXY SUFFICIENT FOR
  PRODUCTION = NO` to `= YES` in the document, confirm the test catches
  it, then restore it).

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

Report: (a) the full certification document's 16 line-item statuses,
verbatim; (b) any status that changed from what this plan expected
(e.g. if a re-run specimen behaved differently than the planning
dossier's evidence — proxy state may have changed since the original
investigation), with evidence — **explicitly flag, rather than silently
apply, any case where evidence seemed to argue for upgrading one of the
four frozen values**; (c) any Wave 1/2/3 defect found and its proposed
follow-up task shape; (d) test results, including both halves of the
completeness-invariant self-test (presence and frozen-value); (e)
`git diff --stat`. Do not merge, do not touch `master`.
