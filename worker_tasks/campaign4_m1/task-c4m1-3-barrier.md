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
