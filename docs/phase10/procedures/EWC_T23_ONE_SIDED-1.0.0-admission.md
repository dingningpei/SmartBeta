# EWC_T23_ONE_SIDED v1.0.0 — PROCEDURE_ADMISSION record (K-PIT write evidence)

- **Authorization:** user, 2026-10-09 ("1. 授权"). The authorization covered the PA
  review plus the production K-PIT `PROCEDURE_ADMISSION` write.
- **PA review:** `docs/phase10/procedures/EWC_T23_ONE_SIDED-1.0.0-PA-review.md`
  (`cd5426f3f7404f3251d460f26e236c320e0d6783be4ad799c31f93790b786d0f`, commit `7cbad41`):
  **PA PASS**, with limitations.

## Record written

| Field | Value |
|---|---|
| K-PIT seq | **10** (append base seq 9 `13d04ef4…d262`; previous file `190d53f0…f6a2`) |
| `record_hash` | `ceff79f3c5bcfdbffffe6f0fc0d4ceebfda045bc57195180bb731ca58cdb2e20` |
| kind / channel / program | `HUMAN_DECISION` / `HUMAN` / null |
| `decision_kind` | `PROCEDURE_ADMISSION` (`actor_role` OPERATOR, `consulted_all_prior` true) |
| `procedure_id` / `version` | `EWC_T23_ONE_SIDED` / `1.0.0` |
| `contract_hash` | `ac14e033ec31e6428bdd815011f456daf998b4003be825bb418485c22ff5dd9c` |
| `implementation_source_sha256` | `0cc12eb4913abf132e4d6dadb65bd0cb948748576cfdad94a1c80f9e9735f37f` |
| `validation_dossier` | `docs/phase10/procedures/EWC_T23_ONE_SIDED-1.0.0-validation.md`, `b91cc797…e4bf` |
| `review_record` | the PA review above, verdict `PA PASS`, commit `7cbad41` |
| Post-write K-PIT file SHA-256 | `d1429eeef232c94c31a03ae960d5fc709bd4bd41a01b6535d2c7ad863063ca00` |

## Procedure

1. **Dry run** on a disposable copy of the production log:
   - appended as seq 10 with an intact chain;
   - `inference._admission_from` parses the record;
   - the identity matches the registered implementation (contract hash and runtime
     source SHA-256);
   - the production log was unchanged; the copy was deleted.
2. **Production append** via `KnowledgeLog.append`. Post-write verification:
   - 11 records; seq 10's `prev_hash` equals seq 9's hash; seq 0–9 unchanged;
   - the admission parses and the identity matches.

## Impact on frozen consumers (checked after the write — disclosed)

- **Unaffected:**
  - `quarantine.verify_dataset_v1_2_identity` still verifies (`a065493d…`);
  - the Campaign-2 `production_bindings` and `campaign2_id` are unchanged
    (`3a24ab7d…`);
  - no code hard-codes the K-PIT file hash.
- **Stale:** the frozen **Campaign-2 registration plan**
  (`docs/campaign2_registration_transaction_plan.md`) names the append base seq 9 /
  file `190d53f0…`. A Campaign-2 option (b) production registration would need an
  erratum that rebases it on seq 10 `ceff79f3…` (C2-E2's seq 10–18 become 11–19).
  Campaign-2 option (b) is not authorized; this is recorded, not acted on.
- **Historical evidence** citing `190d53f0…` remains true as of its own date.
  Future barrier I13/ADM-6 checks compare before/after hashes **at run time**.
- **Planner process note:** the frozen-consumer check should have been run **before**
  the write. It found no breaking dependency, but the order was wrong. This is recorded
  for the planner checklist.

## Effect

- `EWC_T23_ONE_SIDED` v1.0.0 is now **admitted** under Phase-10 §9.3. A preregistered
  study may name it, and inference will dispatch to it.
- The PA limitations are binding (ASYMPTOTIC; a thin coverage margin; anti-conservative
  outside the region).
- **No study has been run.**
