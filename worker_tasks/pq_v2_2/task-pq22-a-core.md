# PQ22-A — P-Q v2.2 core authority (FROZEN 2026-10-08)

- **Authority:** plan `docs/discovery_dataset_v1_2_pq_v2_2_implementation_plan.md` §1–§2;
  amendment A§15; base task `worker_tasks/pq_v2_1/task-pq21-a-core.md`.
- **Branch:** `pq2/task-pq22-a` from the spec-freeze master (named in the launch instruction).
- **Owned:** `smart_beta/dataset_v1/pqv2_core.py`, `tests/test_pqv2_core.py`,
  `tests/fixtures/pqv2/core/**`. Anything else → STOP.

## Work

1. **Authority fields.** Add the optional `MethodologyAuthority` fields
   `base_methodology_relative: str | None = None` and
   `base_methodology_sha256: str | None = None`. `AUTHORITY_V2` and `AUTHORITY_V2_1` are
   byte-unchanged in value.
2. **`AUTHORITY_V2_2`:**

   | Field | Value |
   |---|---|
   | version | `"P-Q v2.2"` |
   | methodology_relative | `docs/discovery_dataset_v1_2_pq_v2_2_amendment.md` |
   | methodology_sha256 | `ab12886cdbac42725bcc283d2e37567e15340b3b80cb603f2a4df8a31a77af76` |
   | cs_id | unchanged (`432de097…`) |
   | ed_id | `a67afc6b319721da5a11f419681ca1d489046e627dfe43242ea6eb54f36388ef` |
   | ce_id | `81cafb32030681023be9328c0ceda1d9d78e502071377692b19dc302ce699d5d` |
   | base_methodology_relative | the v2.1 document |
   | base_methodology_sha256 | `66772bb1…5d1e` |

   Set `CURRENT_AUTHORITY = AUTHORITY_V2_2`. The module constants `METHODOLOGY_SHA256`,
   `ED_ID` and `CE_ID` follow `CURRENT_AUTHORITY`.
3. **`verify_authority(authority=CURRENT_AUTHORITY)`:** it also hashes the base document
   when it is set; a mismatch raises. `require_current_authority` rejects the v2 and v2.1
   identity triples.
4. **Canonical lines.** Add an independent recomputation helper (tests only): the ED and
   CE canonical lines (plan §1, byte-exact) hash to the constants.

## Tests

- the constants equal plan §1;
- the canonical-line recomputation;
- tampering with the amendment, and separately with the base v2.1 document → raises;
- the v2.1 and v2 authorities still verify against their preserved documents;
- the stale v2.1 ED/CE → rejected;
- all existing core tests pass.

## Mutations (`tests/fixtures/pqv2/core/mutations.json`)

V22-M08 (the v2.1 identities stay current), V22-M09 (the base not verified), A22-M1
(the CE line hash taken from the v2.1 line). Every existing mutation stays killed.

## Stop

- a constant cannot be reproduced from the committed documents;
- any non-owned file change appears necessary.
