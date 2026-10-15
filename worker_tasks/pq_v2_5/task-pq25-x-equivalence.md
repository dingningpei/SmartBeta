# P-Q v2.5 — wave 4: task PQ25-X (real-data equivalence barrier) (FROZEN 2026-10-14; launch authorized)

## Authority

- design §5 (frozen) and erratum E1;
- the binding constraints in `worker_tasks/pq_v2_5/pqv25-plan.md`;
- the PQ25-T review note: float comparisons use **bit patterns**, so `-0.0 ≠ 0.0`;
- base: master at freeze (v2.5 waves 1–3 merged at `b764048`).

## Purpose

Every v2.5 equivalence so far is **synthetic**. PQ25-X proves the frozen design §5
properties **on real IS data**, before any Campaign-4 use of TD.

## Factor set (11; IS fold only)

- the six M0-2 factors (`smart_beta.diagnostics.known_factors.build_known_factors()`);
- the five Campaign-3 TB_SELECTION candidates, loaded from persisted provenance exactly
  as `scripts/run_m0_ts_decomposition.py` does (`--diagnostics-dir` + `--c3-digest` ×5,
  in IS-rank order).

## Content (owned, all new)

### 1. `smart_beta/dataset_v1/pqv25/equivalence.py`

**`check_equivalence(assembly, factor, *, fold) -> FactorEquivalence`** runs, for one
factor:
- the frozen `build_pqv2_diagnostics` once, giving `base_frozen`;
- `build_pqv25_diagnostics` at d = −1, d = 0 and d = TD_D.

**Checks** (each a named boolean, plus the first mismatching field path):

| Check | Requirement |
|---|---|
| **E1** | at d = −1, every TD cell (`B-TD`, `L-TD`, or `-K0`/`-K1` for event factors) equals the same base's TS cell, **bitwise**, on value, t_stat, n_obs, status, reason, n_index, support, period_identities, period_statuses, period_values, period_reasons, period_causes and cause_union |
| **E2** | the same at d = 0 against the T0 cells |
| **E3** | the `base` carried inside every `PQv25Diagnostics` has a canonical serialization **byte-identical** to `base_frozen`'s, so the wrapper never mutates frozen output. Use the frozen diagnostics' own canonical form or hash if one exists; otherwise a canonical JSON with floats encoded as their bit-pattern hex |
| **E4** | at d = TD_D, descriptive only: per period, L-TS ≤ L-TD ≤ L-T0 where all three are assessed. Count violations |
| **Not assessed** | if `base_frozen.not_assessed` is set, the TD cells must be NOT_ASSESSED with the same reason; this counts toward E1/E2 |

**Report:** `pqv25_equivalence.json` (canonical, content-hashed) and `.md`.

| Section | Contents |
|---|---|
| Per factor | E1–E4, the first mismatch path if any, cells compared, and the d = TD_D descriptive table: B/L × T0/TS/TD EWC t, computed with the frozen `EWCT23OneSided` exactly as C4M0-2 does |
| Overall | **PASS** iff E1, E2 and E3 hold for all 11 factors. Otherwise **FAIL**. E4 violations are reported, not gating, because the design's monotonicity is a property of each label, and period-level L sandwich effects are reported for judgment |

### 2. `scripts/run_pqv25_equivalence.py`

- **Arguments:** the same as `scripts/run_m0_ts_decomposition.py` (dataset root, K-PIT
  path, run root, output dir, C3 digests).
- **Diagnostic-only identity:** it reuses `run_m0_known_factors.build_generator`.
- **Exit code:** 0 on PASS, 3 on FAIL.

### 3. Tests: `tests/test_pqv25_equivalence.py` (synthetic only)

1. E1–E3 pass on the parity / M0-4 terminal / PQ23 event fixtures.
2. **Bit sensitivity:** an injected `-0.0` vs `0.0` difference in one TD period value is
   detected as an E1 failure, with its field path.
3. An injected change to a carried `base` cell is detected as an E3 failure.
4. A NOT_ASSESSED base propagates and passes.
5. **The real-assembly policy path:** the script's policy identity path is covered (the
   C4M0-2 R2 lesson).
6. **Determinism:** two runs produce byte-identical JSON.

## Execution (the planner's real run; the same protections as M0-2/M0-4)

- **Isolation:** a K-PIT **copy** plus a scratch run root. The production K-PIT SHA-256 is
  checked before and after.
- **Firewall:** IS only, credentials scrubbed, no network.
- **Expected time:** about 11 factors × (1 frozen + 3 v2.5 runs). The planner runs it in
  the background.
- **Result document:** `docs/discovery_dataset_v1_2_pq_v2_5_equivalence_result.md`.
- **On PASS:** P-Q v2.5 is declared IMPLEMENTED and EQUIVALENCE-PROVEN on IS. That is
  **not** an admission of v2.5 for confirmation use, which remains a separate
  user-authorized step (erratum E1.3).
- **On FAIL:** **STOP.** Report the first mismatch. Nothing is patched.

## Owned files (all new)

- `smart_beta/dataset_v1/pqv25/equivalence.py`
- `scripts/run_pqv25_equivalence.py`
- `tests/test_pqv25_equivalence.py`
- `tests/fixtures/pqv25/equivalence/**`

## Launch

- One Pi worker via Herdr, worktree `worktrees/task-pq25-x-equivalence`.
- **Stop rule** as in the previous waves.

## Freeze (user, 2026-10-14: "冻结， 授权启动")

This spec is frozen, including the PASS/FAIL rule, before any real-data v2.5 result. The PQ25-X Pi launch is authorized, as is the planner real IS run after merge.

## Barrier (2026-10-14)

- Merged: PQ25-X `0029e31` (merge `2d9604a`); 48 tests re-run independently.
- Full suite at `2d9604a`: 6865 passed, 1 skipped, 882 s (exit 0).
- `verify_authority_v2_5()` passes on master.
- Real IS run: **PASS** (E1/E2/E3 all 11 factors, 52 TD cells, E4 violations 0);
  production K-PIT hash unchanged. Result: `docs/discovery_dataset_v1_2_pq_v2_5_equivalence_result.md`.
