# C4M1-3 — review return R1 (commit `5b3eba3`)

- **Reviewed:** `5b3eba3` on base `2ceb4d6`.
- **Clean:**
  - only the 4 owned files changed;
  - 86 tests re-ran and pass;
  - pool reconstruction, S0–S3, the two-phase firewall, and the reproduction check all
    conform.
- **Accepted as recorded:**
  - deviations 1, 3 and 5 of the completion report: the `l_series` basis,
    `--assembly-run-root`, and the two-phase ordering;
  - the test headline uses the frozen `test_statistics` convention (the lower-mean cell,
    C4M1-1 erratum E1 R3). This is required for the bitwise M1-e reproduction.

Return the task for two fixes. Change nothing else.

## R1 — POOL-LIMITED must be evaluated on the W1 pools only (frozen draft §3)

The frozen definition:

> POOL-LIMITED applies if, in both arms, the pool's median **W1** test t is ≤ 0 and the
> training-to-test Spearman correlation is ≤ 0.

The planner clarifies that the Spearman correlation is the one between the S0 training
value and the **W1** test t.

Required:
- `_flag_pool_limited` uses the two W1 runs (treatment_W1, control_W1).
- The flag is true iff, for both:
  - the median is ≤ 0; and
  - the Spearman correlation is ≤ 0.
- The W2 base rates stay in the report per run, as descriptive values. They do not enter
  the flag.
- If a W1 run is absent (for example `--runs` without it), the flag is `null` with the
  reason `"W1-pool-missing"`. It is not `false`.
- Test: a fixture where the W1 pools meet the condition and a W2 pool does not must give
  POOL-LIMITED = true.

## R2 — no `fork` for `--jobs > 1`

The test run prints `DeprecationWarning: This process ... is multi-threaded, use of fork()
may lead to deadlocks in the child`. A real run takes several hours, so a deadlock is not
acceptable.

Required:
- Use the `spawn` start method.
- Each child gets a **picklable, module-level** initializer with plain arguments: the
  dataset root, the K-PIT copy path, the assembly run root. The child builds its own
  assembly and evaluator through `run_c4_walkforward` (by import), as the C3 production
  launcher does.
- Each child still writes its own cache file atomically.
- `test_report_is_identical_for_jobs_one_and_two` must pass under spawn with **no fork
  DeprecationWarning**. Run it with `-W error::DeprecationWarning` for that test.
- If spawn cannot carry the synthetic builder in the test, give the test a module-level
  synthetic factory. Do not fall back to fork.

When done, commit once with `fix(c4m1-3): review R1-R2 (W1-only POOL-LIMITED, spawn
workers)`. Report the SHA and the test result.
