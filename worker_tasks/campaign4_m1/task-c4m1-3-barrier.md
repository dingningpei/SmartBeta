# C4M1-3 — merge barrier record

- **Review:**
  - Worker commit `5b3eba3` on base `2ceb4d6`, reviewed at its exact SHA. 86 tests re-ran.
  - It was returned with R1–R2 (`task-c4m1-3-review-r1.md`):
    - R1: POOL-LIMITED on the W1 pools only, per frozen §3;
    - R2: `spawn` instead of `fork`, because of the multi-threaded fork deadlock warning.
  - Fixed in `3023a80`. Reviewed at that SHA; 88 tests re-ran with
    `-W error::DeprecationWarning`.
- **Accepted as recorded:**
  - the cached series basis is `l_series`, the series the frozen rank and test path
    consumes;
  - the test headline follows the frozen `test_statistics` convention (C4M1-1 E1 R3);
  - the optional `--assembly-run-root` flag. The evaluation writes nothing there (planner
    check), so parallel workers can share it.
- **Planner sizing check (real IS):**
  - one production evaluation: 143 s wall, 3.34 GB peak RSS;
  - the real run uses `--jobs 3`;
  - K-PIT unchanged.
- **Protected paths:** only the 4 owned files changed.
- **Merge:** `004443b` (`--no-ff`, of `3023a80`).
- **Barrier (2026-10-19):** 6977 passed, 1 skipped, 0 failed (854 s). **PASS.**

## Post-merge return R3 (2026-10-20)

- **Found on real data:**
  - `load_run_pool` gave 55 / 47 / 85 / 90 EVALUATED against M1-e's 50 / 41 / 62 / 73.
  - It counted candidates whose recomputed L-TD statistic is NOT_ASSESSED, and phase A
    would have raised on them.
  - The CLI did not enforce the frozen count check.
  - The planner's merge review missed both points. Return: `task-c4m1-3-review-r2.md`.
- **Fix:** `b54a5e4`, reviewed at that SHA, 91 tests re-run.
  - The VALID rank statistic is now an eligibility conjunct: excluded, not raised.
  - The counts come from `--m1e-reports` and are checked before any selection file is
    written.
- **Merge:** `0a48228` (`--no-ff`).
- **Barrier (2026-10-20):** 6980 passed, 1 skipped, 0 failed (949 s). **PASS.**
- **No result was produced before the fix:** the interrupted and pre-fix runs only filled
  the series cache, whose format is unchanged.
