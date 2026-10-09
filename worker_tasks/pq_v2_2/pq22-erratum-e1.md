# PQ22-E1 — authority selection instead of a CURRENT_AUTHORITY switch (FROZEN 2026-10-08; user-approved option A)

- **Defect class:** a **specification defect (planner)** in the frozen P-Q v2.2
  implementation plan (`212fcd82…`) §2 and in PQ22-A/C/H.
- **What went wrong:** the plan set `CURRENT_AUTHORITY = AUTHORITY_V2_2` without
  accounting for a frozen consumer that binds P-Q v2.1. That consumer is Campaign-2:
  - its precommitment `0815259b…`;
  - its `campaign2_id` `3a24ab7d…` and the C2-E2 registration payloads;
  - `campaign2.py`, which takes `_core.METHODOLOGY_SHA256` / `ED_ID` / `CE_ID`;
  - `runner2.py`, which requires `CURRENT_AUTHORITY is AUTHORITY_V2_1`.
- **Evidence:** on PQ22-A `b7e0138`, `tests/test_campaign2_executor.py::test_wrap_declaration_binds_v2_1`
  fails (the methodology `ab12886c…` is not `66772bb1…`).
- **The PQ22-A worker implemented the frozen spec correctly.** The defect is not theirs.

## Correction (option A — APPROVED, "两份都按建议")

1. **`CURRENT_AUTHORITY` stays `AUTHORITY_V2_1`.** Campaign-2, `campaign2.py`,
   `runner2.py` and every v2.1 path stay byte-unchanged.
2. **`AUTHORITY_V2_2` is added as an explicitly selectable authority:**
   - `ADMITTED_AUTHORITIES = (AUTHORITY_V2_1, AUTHORITY_V2_2)`;
   - `verify_authority(authority)` verifies the base document **whenever** it is set,
     not only for the current authority;
   - new `require_admitted_authority(methodology_sha256, ed_id, ce_id) ->
     MethodologyAuthority`;
   - `require_current_authority` is unchanged.
3. **C:**
   - `certify(spec, *, authority=CURRENT_AUTHORITY)`. v2.1 is the exact enumerated-key
     behaviour; v2.2 is the A§15A grammar. Any other authority raises;
   - `CertifiedClass.ed_id` / `.ce_id` come from the authority passed;
   - `dependency_windows` is authority-independent. The v2.2-only node rules (Add/Sub
     union, generic dv) are reachable only through a v2.2 certification.
4. **H:**
   - a declaration must match **one** admitted authority (`require_admitted_authority`);
     that authority is passed to `certify`;
   - diagnostics record the authority version, plus `base_methodology_sha256` when it is
     set;
   - v2.1 declarations produce byte-identical v2.1 diagnostics (the Campaign-2 path).
     Under v2.2 the C1 diagnostics are equal after substituting the identity fields
     (plan §1, unchanged).
5. **Recertification and ADM** for v2.2 run with `authority=AUTHORITY_V2_2` explicitly.
6. **Deferred:** switching `CURRENT_AUTHORITY` to v2.2. It needs its own decision, once
   no frozen consumer binds v2.1 (for example after Campaign-2 is executed or retired).
7. **Mutation changes:**
   - V22-M08 is replaced by **V22-M08′**, "CURRENT_AUTHORITY switched to v2.2" (must be
     killed by a Campaign-2-binding test in A);
   - add V22-M13, "v2.1 certification falls through to the grammar" (C);
   - add V22-M14, "a declaration bound to a non-admitted authority accepted" (H).
8. **Repair:** PQ22-A-R1, by the same worker on its branch.

## Option B (rejected)

- **What it does:** rebind Campaign-2 to v2.2, amending the frozen precommitment, C2-E2,
  `campaign2.py` and `runner2.py`.
- **Cost:** it re-opens a frozen campaign, changes `campaign2_id`, and needs a C2-LX-R2
  plus a new dry run.

## Binding effect on the frozen PQ22 specs

- This erratum **overrides** plan §2 row A, §5 V22-M08, and the PQ22-A/C/H task specs
  wherever they conflict.
- **C:** `certify(spec, *, authority=CURRENT_AUTHORITY)`, and the tests cover both
  authorities.
- **H:** declarations are resolved through `require_admitted_authority`; the Campaign-2
  v2.1 path stays byte-identical.
