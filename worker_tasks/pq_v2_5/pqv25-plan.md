# P-Q v2.5 — wave 1 plan: PQ25-A (authority) + PQ25-G (TD oracle) (DRAFT for freeze; not authorized)

## Authority

- design `docs/discovery_dataset_v1_2_pq_v2_5_terminal_td_design_draft.md` (frozen, SHA-256
  `5af8c4bf…4c4460`);
- erratum `docs/discovery_dataset_v1_2_pq_v2_5_erratum_e1.md` (SHA-256 `62d9bf32…95fdc2a`).

## Binding constraints (all v2.5 tasks)

1. **Bound files are untouched.** No file in `pqv2_core.PINNED_CODE` or in
   `campaign3_run.CAMPAIGN3_CODE_FILES` / `PQ_PINNED_FILES` / `RESERVE_CHAIN_FILES` is
   modified. They are import-only.
2. **Location:** all new code lives in the new package `smart_beta/dataset_v1/pqv25/`.
3. **No data or side effects:** synthetic fixtures only; no `data_store` read, no network,
   no K-PIT write.
4. **The frozen C3-bound hash table** is `worker_tasks/pq_v2_5/c3_bound_sha256.json`
   (22 files, SHA-256 `50a69b8e…de48db994`).
   - The planner captured it from master and verified that it equals, 22 of 22, the
     `code_sha256` map in the Campaign-3 CAMPAIGN record (K-PIT seq 46).

## Task PQ25-A — `pqv25/authority.py`

### Contents

- **`AUTHORITY_V2_5`:** a `pqv2_core.MethodologyAuthority` with:

  | Field | Value |
  |---|---|
  | `version` | `"2.5"` |
  | `methodology_relative` | the design document path |
  | `methodology_sha256` | `5af8c4bffed24062977b0442cfeaff388e2042eedd173f5300893b45252c4460` |
  | `cs_id` | `AUTHORITY_V2_4.cs_id` (inherited) |
  | `ed_id` | `cfd767331c63ca9d2d85e4559830d3cbc88ed4cb5ae4ea098961d52666f4ffe2` |
  | `ce_id` | `882725daca1bae14faa91b25a3fc6c05e36045ffa25bf97035b5212ed85cbf82` |
  | `base_methodology_relative` | `docs/discovery_dataset_v1_2_pq_v2_4_fold_firewall.md` |
  | `base_methodology_sha256` | `695973b9f1c7fcbc4979527a7b20ee0150fc5831da34722df5a7a37416aebed0` |

- **`ERRATA_V2_5`:** `(("E1", "docs/discovery_dataset_v1_2_pq_v2_5_erratum_e1.md",
  "62d9bf32fe0ed57830ae1c469dcdf30c18d350b5d4331315c5779875895fdc2a"),)`.
- **`TD_D`:** `-0.55`, as a module literal. It must equal the value in the ED line;
  test this.
- **`C3_BOUND_SHA256`:** the frozen table. Load it from the JSON file **and** check it
  against an embedded SHA-256 of that file (`50a69b8e…`).
- **`verify_authority_v2_5(repo_root=None) -> None`:** raises
  `pqv2_core.PQv2BindingError` on any failure. It runs these checks in this order
  (erratum E1.3):
  1. the design document SHA, the E1 SHA, and the base v2.4 document SHA;
  2. `pqv2_core.verify_authority(root, pqv2_core.AUTHORITY_V2_4)`, the full frozen chain
     plus `PINNED_CODE`;
  3. the identity lines:
     - CS = `pqv2_core.identity_lines(root, AUTHORITY_V2_4)["CS"]`;
     - ED/CE = `pqv2_core._extract_code_block_after(lines, "ED-ID v2.5" / "CE-ID v2.5")`
       on the design document;
     - the SHA-256 of each equals the authority's ids;
  4. every file in `C3_BOUND_SHA256` hashes to its value.

### Tests (`tests/test_pqv25_authority.py`)

1. **The master pass:** it passes on master.
2. **Tamper cases**, each in a temp copy of the needed files and each must raise:
   - the design document;
   - the E1 document;
   - the base document;
   - one ED character;
   - one C3-bound file;
   - one `PINNED_CODE` file;
   - the JSON table.
3. **Line hashes:** the ED/CE lines extracted from the design document hash to the
   literal ids.
4. **The d value:** `TD_D == -0.55`, and the ED line contains `d_minus_0p55`.
5. **Frozen modules untouched:** no import of `pqv25` mutates `pqv2_core` state, and
   `ADMITTED_AUTHORITIES` is unchanged.

### Owned files

- `smart_beta/dataset_v1/pqv25/__init__.py`
- `smart_beta/dataset_v1/pqv25/authority.py`
- `tests/test_pqv25_authority.py`

## Task PQ25-G — independent TD oracle (`tests/oracles/pqv25_td_oracle.py`)

**Independence.** The oracle is written from the design §1 and E1 **only**. It must not
import `delisting_bridge`, `pqv2_*` or `pqv25` modules other than for type-free
constants. This is the PQ22-G/PQ23-G oracle pattern.

### Content: brute-force, member-level

**Inputs:** a synthetic formation, i.e. members with:
- B group (1..G);
- economic leg derived from the group and the sign as in the design §1 (long = group G
  for sign +1, etc.);
- admissibility state;
- r_obs;
- the Candidate-E correction factors (lo, hi).

**Outputs:**
1. the per-member `v^T0`, `v^TS`, `v^TD(d)` using the exact expressions
   `(1.0 + r) * (1.0 + tau) - 1.0`;
2. the per-member scenario label bounds `x_lo = (1.0 + v^X) * lo - 1.0`,
   `x_hi = (1.0 + v^X) * hi - 1.0`;
3. for the SIMPLE (exact-assignment) class: the per-period gross long-short under B
   (equal-weight leg means of finite values) and the L lower envelope (the long-leg mean
   of the lo bounds minus the short-leg mean of the hi bounds), for T0, TS and TD.
   Sandwich classes are out of oracle scope; they are covered by the §5.1/§5.2 equivalence
   barrier.

### Tests (`tests/test_pqv25_td_oracle.py`)

1. **d = −1:** TD equals TS bit for bit, member and period, on 200 seeded random
   formations.
2. **d = 0:** TD equals T0 bit for bit.
3. **Bounds and order:** lo ≤ hi is preserved for every scenario and d ∈ [−1, 0].
4. **Monotonicity:** L-TD is non-increasing as d decreases from 0 to −1 (more stress →
   lower L), on seeded formations.
5. **Hand-computed case:** one fully hand-computed small formation (literals in the
   test).

### Owned files

- `tests/oracles/pqv25_td_oracle.py`
- `tests/test_pqv25_td_oracle.py`
- `tests/fixtures/pqv25/oracle/**`

## DAG and launch

- PQ25-A and PQ25-G are independent, with disjoint files, so they run in parallel.
- Each gets one Pi worker via Herdr (CLAUDE.md §2):
  - `worktrees/task-pq25-a-authority`;
  - `worktrees/task-pq25-g-oracle`.
- Review, merge and barrier follow CLAUDE.md §4.
- **Additional barrier check:** `verify_authority_v2_5()` passes on the merged master.
