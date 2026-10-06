# PQ2-D-E1 — Oracle-containment tests are conditional on the M§11 winsorization-block precondition (FROZEN)

- **Status:** FROZEN. Authorized by "P-Q v2 WAVE-3 RECOVERY — PQ2-D-E1 ERRATUM
  AUTHORIZED", on master `9083ac5203cf426ba649dde6be4879aa0705b255`.
- **Amends:** `worker_tasks/pq_v2/task-pq2-d-simple-assignment.md` (committed in
  `b2db30b`, SHA-256
  `9bb96668a6db2708dd444f7e8c07d4ce1cd6995a03eb06b54b532a4c78e4d7c1`), §2 "Oracle containment" bullet only. It is a **task-spec defect**.
  The methodology and CS/ED/CE IDs are unchanged.
- **Defect:** D §2 requires oracle containment on `pqv2_oracle.CASES["nan"]`
  unconditionally.
  - In that world the M§11 shared precondition **fails**: two clean members are clipped
    to the high cap; the tied block (2) exceeds s_G = 1; `rank(method="first")` promotes
    the later row. `pqv2_core.winsor_block_ok` is False.
  - **M§11 / M§13:** Prop. 4 (simple coverage) holds only when that precondition holds.
    Otherwise L_t = NOT_ASSESSED (`PQV2_WINSOR_BLOCK`, applied by PQ2-F). An
    unconditional containment assertion therefore tests the theorem outside its
    certified domain.
  - **Found by:** the PQ2-D worker (Wave 3), which correctly stopped.

## E1.1 Replacement rule (D §2, the "Oracle containment" bullet)

For each oracle world used by D, compute
`ok = pqv2_core.winsor_block_ok(lo, hi, world.q, s_1, s_G)` from D's exported per-member
intervals (clean [v, v]; uncertain (−∞, +∞)).
- **If `ok` is True:** the full original assertions apply unchanged (reachable top/bottom
  sets inside the Prop. 4 family; `ls_min` ≥ L; `turnover_max` ≤ TO^hi).
- **If `ok` is False:** the certified result is NOT_ASSESSED with the frozen reason
  `PQV2_WINSOR_BLOCK`. The test asserts:
  - `ok is False`;
  - that the reason the frozen architecture assigns is exactly
    `pqv2_core.NotAssessedReason.WINSOR_BLOCK` (`PQV2_WINSOR_BLOCK`).
  - **Where the check lives:** per plan §6.3 and the PQ2-F spec step 1, the check runs
    in PQ2-F over the intervals PQ2-D exports. **No D production or interface change
    is made.**
  - The test **may** also demonstrate, as diagnostic evidence, that the oracle reaches a
    set outside the Prop. 4 family.
  - This must hold for `CASES["nan"]`. Prop. 4 containment is **not** required on such
    worlds.

## E1.2 Coverage must not be lost (added requirements)

1. **A NaN world that satisfies the precondition:** D's test file constructs, through the
   oracle's **public** `World` / `Stock` API (no oracle file is modified), a
   simple-expression world containing a NaN-valued universe member, a suspect, and
   `winsor_block_ok` True. The full containment assertions run on it.
2. **Anti-vacuity guard:** at least **four** of D's oracle worlds (including the new NaN
   world) must satisfy **every** Prop. 4 precondition and run full containment. If fewer
   do, the test fails.
   - The test prints, and the completion report lists, the worlds receiving full coverage
     and the worlds excluded, each with the named failing frozen precondition.
   - **Precondition checks are not an escape hatch.**
3. **Block-failure fixture:** `CASES["nan"]` (and `CASES["winsor_block_failure"]` where it
   is a simple world) are asserted to fail the precondition.

## E1.3 Unchanged

The D implementation semantics are unchanged: clean order by **unwinsorized** values and
the exported winsorization intervals (resolution option 3 is **rejected**: it would
change the frozen interface). All D mutations are unchanged (they target production code). No test-side mutation is added; the anti-vacuity guard in E1.2 protects the conditional assertion.

## E1.4 Scope

D-owned files only. No oracle, core or other-task changes. If E1 would require a D
production or interface change, STOP and report.
