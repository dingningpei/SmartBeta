# C2-LX erratum C2-E2 + repair C2-LX-R1 — bound hypothesis identities and registration payloads (FROZEN 2026-10-08; user-approved)

## 1. Defects

- **Implementation defect (C2-LX `1b923c3`, merged in `3097502`).**
  `campaign2.EXPECTED_HYPOTHESIS_IDS` is `{}`, and the comment refers to a function
  (`_expected_hypothesis_ids`) that does not exist. Consequences:
  - `hypothesis_identities(root)` with defaults always raises on real inputs;
  - the executor passes `EXPECTED_HYPOTHESIS_IDS or None`, which **disables** the binding
    check;
  - the tests monkeypatched the value, so neither problem was detected.

  C1-LX binds frozen constants (`campaign1.EXPECTED_HYPOTHESIS_IDS`); C2-LX §1.1 applies
  C1-LX mutatis mutandis, so C2-LX should have done the same.
- **Specification gap (planner).** The registration plan (`bacca410…a29a`) did not
  byte-freeze the PROGRAM_FREEZE and HYPOTHESIS_FREEZE payloads. They are frozen in §3
  below.

## 2. Frozen bound identities

These were computed by the planner from the frozen real D7c manifest through the merged
definition path, with no factor evaluation and no parquet reads.

| Label | `hypothesis_definition_hash` | `campaign2_hypothesis_id` |
|---|---|---|
| C1-H1 | `ce26a34da8a62912f10a50ad9c25447b62ecb1b56e2d5a031913691785420e14` | `e9375f9fa0426cec18db9dac35c6351f11aee44f5ff5cbd6c3e1d2b272ad3ddc` |
| C1-H2 | `e2503e5240cce5b38071af96d19d11e118ec78dd84ae4152415a6ed6ce70fce9` | `feb2ef7c00eca805beaa046b3a90191baa44c3d35dc43ee869afd0aef785e1c2` |
| C1-H3 | `6bd890da620263418709623fc23963cb82e258b2b2bfc8a01f600e132be15da2` | `4bf41f5b771be1fcb0894411f020e45873c080bce0a02bc58b96280991c2e867` |
| C1-H4 | `095b126b5227c180bd49568daa02222c3e77ee460917b5e2f4d263ab1e2cc1d0` | `dcf37574370a4352369a6547f0f77ccc9f934a2a0601344092d7d024f14b9659` |
| C1-H5 | `e859f7aa604b3fca1a19b2ea16e87ec5d056064a6e15c4206ebba2727a8b176a` | `bca56c6272404eb5e464cdcd4874d63a83336ba2d3aa13db55a90e1090a8688e` |
| C1-H6 | `ba7714468f9b3641e249764e1b076491b32531b16a2bfd5da51e2c29b6dad7ac` | `32e5f90651471d5d7550d541171c79d352e1bdc9442a5ab6c35d9cb4fa81489b` |
| C1-H7 | `efd345b3c280ca81905b7a6e655e2e30ff4c4e8b01173fa949fea4dddfaba8d3` | `0bf4ae4b057c54bf5188cc304bb73aeffdd433084303b4e15c65cc63e9bc5a27` |

- **Expected `campaign2_id`** (production bindings at master `3097502`):
  `3a24ab7df8339a0587b3ac37d1dafae1b415bf359d87e05a63c7e9b4df4f4379`.
  - It is recorded for the registration pre-write barrier.
  - It is **not** a code constant, because it depends on the runtime bindings.

## 3. Frozen registration payloads

- **Encoding:** canonical JSON. **Channel:** `HUMAN` for all nine records.
- **Sequence:** seq 10–18, appended after K-PIT seq 9 `13d04ef4…d262` (file
  `190d53f0…f6a2`).

| seq | Record | Payload |
|---|---|---|
| 10 | `HUMAN_DECISION` (`ESTIMAND_POLICY`) | `{"decision_kind":"ESTIMAND_POLICY","actor_role":"OPERATOR","consulted_all_prior":true}` ∪ `campaign2.estimand_policy_content()`; refs empty |
| 11 | `HUMAN_DECISION` (`PROGRAM_FREEZE`) | `{"decision_kind":"PROGRAM_FREEZE","actor_role":"OPERATOR","consulted_all_prior":true,"campaign2_id":<§2>,"precommitment_sha256":…,"pq_methodology_sha256":…,"pq_cs_id":…,"pq_ed_id":…,"pq_ce_id":…}`; refs empty |
| 12–18 | `HYPOTHESIS_FREEZE`, C1-H1…H7 in order | `{"hypothesis_id","factor_spec_hash","hypothesis_definition_hash"}` from §2; `influenced_by = [hash(seq10), hash(seq11)]`; footprint and event_time null; no `proposal_id` |

## 4. C2-LX-R1 repair (same worker; owned files unchanged from C2-LX)

1. Set `EXPECTED_HYPOTHESIS_IDS` and `EXPECTED_HYPOTHESIS_DEFINITION_HASHES` to the §2
   literals. Remove the dangling `_expected_hypothesis_ids` comment.
2. The executor binds **unconditionally** to `EXPECTED_HYPOTHESIS_IDS`: no `or None`, and
   no path where the check is skipped. An empty or partial mapping is a `C2BindingError`.
3. **Tests:**
   - the constants equal the §2 table exactly (7 entries);
   - with the mapping cleared or made partial, both the executor and
     `hypothesis_identities` fail closed;
   - synthetic-manifest tests may still monkeypatch the mapping, but at least one test
     must run the executor path **without** monkeypatching the binding check out.
4. **New mutations (must be killed):**
   - C2-M11: the `or None` bypass is restored;
   - C2-M12: one §2 ID is altered;
   - C2-M13: an empty mapping is accepted.
5. Exactly one commit. All C2-M1…M10 must stay killed.
