# C4M1-3 — review return R2 (found on real data after merge `004443b`)

**Found by:** the planner's real run (2026-10-20).
- `load_run_pool` on the four real M1-e states yields 55 / 47 / 85 / 90 EVALUATED.
- The M1-e reports record 50 / 41 / 62 / 73.
- The difference is exactly the reports' NOT_ASSESSED counts (5 / 6 / 23 / 17).

**Why:**
- In M1-e, NOT_ASSESSED is decided by the *recomputed* L-TD rank statistic, not by a
  state field. A candidate can carry `assessed: true` and `not_assessed_reason: null` in
  the state, but under the P-Q rule its L-TD statistic is NOT_ASSESSED.
- The merged code **raises** on such a candidate in phase A. The real run would therefore
  stop after warming the cache, without a report.
- The CLI never passes `expected_evaluated`, so the frozen hard count check (draft §1:
  "counts must equal the report's 50 / 41 / 62 / 73") is not enforced before the work.

The planner's merge review missed both points. This is an implementation defect against the
frozen spec, which already says that eligible includes "a VALID training rank statistic".

## R3 — eligibility includes the VALID rank statistic; the count check is enforced

- **Eligible:** the state conjuncts (certified, evaluated, assessed, no refusal reason)
  **and** the recomputed training rank statistic at the run's cut is VALID.
  - A candidate that fails only the last conjunct is labelled `NOT_ASSESSED`, with
    `reason` = the rank statistic's reason, and excluded.
  - It does **not** raise.
  - The raise remains only for a genuine inconsistency, such as a spec hash mismatch or a
    missing cached series.
- **CLI:**
  - When `--m1e-reports` is supplied, read each run's `counts["EVALUATED"]` and pass it as
    the expected count.
  - The comparison happens after the VALID filter and **before any selection file is
    written**.
  - A mismatch writes a report with `REPRODUCTION_FAILED`, writes no selection files, and
    exits non-zero.
- **Tests:**
  1. A fixture state whose candidate has `assessed: true` / `not_assessed_reason: null`, but
     whose series makes the L-TD statistic NOT_ASSESSED, is excluded and labelled
     NOT_ASSESSED.
  2. The expected-count mismatch path writes no selection file and exits 1.
  3. The CLI derives the expected counts from a supplied M1-e-shaped report.
- **Unchanged:** the series cache format, S0–S3, the flags, and spawn. The planner's
  existing cache (277 specs) must stay valid.

Commit once: `fix(c4m1-3): review R3 (VALID rank stat in eligibility; enforced EVALUATED
count)`. Report the SHA and the test result.
