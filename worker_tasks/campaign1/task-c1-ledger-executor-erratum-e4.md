# C1-LX-E4 — Erratum to the C1-LX task spec: definition-based hypothesis identity + realization provenance (frozen; standalone)

- **Status:** FROZEN. Approved by the user ("批准两份草稿，冻结并提交"). The worker
  implementation needs a separate authorization.
- **Authority:** `docs/campaign1_identity_architecture_correction.md` (commit `3f8d716`,
  SHA-256 `50f9929f10b3e037b6f554eea4cdf08868e54a5b1a346338c0cdd4224a9734b8`;
  referred to below as "the Correction").
- **Amends:** `worker_tasks/campaign1/task-c1-ledger-executor.md` (`118bcf41…36fd`) +
  E1 (`6cc544ba…e9ca`) + E2 (`90251a1d…778c`) + E3 (`e070a6a7…4c07`). None of these
  documents is modified.
- **Effective authority after freeze:** original task + E1 + E2 + E3 + E4.
- **Base:** master at or after `3f8d716`. The merged C1-LX code is at
  `1e4d1df`.
- **Owned files:**
  - `smart_beta/campaign/campaign1.py`;
  - `tests/test_campaign1_executor.py`;
  - `tests/test_campaign1_identity.py` (new);
  - `tests/fixtures/campaign1/ledger/mutations.json`.
- **Must not modify:** Phase-6/7/8/9/10 code, PQA, PQB, PQC, D7b, D7c, and every
  frozen document.

## E4.1 Hypothesis identity (replaces C1-LX §2.2–§2.3 and §9 `hypothesis_identities`)

1. **New constants:**
   - `ENGINE_SEMANTICS_FILES`: the 10 repository paths listed in Correction §3/§6, in
     that order. They are hashed at run time from the repository; the map is
     key-sorted by canonical JSON.
   - `TI_MANIFEST_RELATIVE = "derived/trusted_inputs/ti-aa23fabd3db4e595/trusted_inputs.json"`
     (under the dataset root).
   - `HYPOTHESIS_DEFINITION_SCHEMA = "campaign1-hypothesis-definition-v1"`.
   - `HYPOTHESIS_ID_DOMAIN = "campaign1-hypothesis-id-v1"`.
   - `EXPECTED_HYPOTHESIS_IDS`: label → the 7 `hypothesis_id`s in Correction §4.
2. **`hypothesis_definition(spec, *, root) -> dict`** builds exactly the Correction §3
   object:
   - `ti_id` and `ti_hash` are read from the manifest JSON;
   - `manifest_sha256` is the SHA-256 of the manifest bytes;
   - `engine_semantics` is the SHA-256 of each listed file.

   It reads **no** parquet and evaluates **no** factor.
3. **`hypothesis_definition_hash(definition)`** = `registry.content_hash(definition)`.
   **`campaign1_hypothesis_id(definition_hash)`** =
   `registry.content_hash({"domain": HYPOTHESIS_ID_DOMAIN, "hypothesis_definition_hash": …})`.
   `hypothesis_id_for` is **removed from Campaign production code**. The E2.2
   permitted-import list shrinks accordingly to `content_hash` and `canonical_json`.
4. **`hypothesis_identities(root)`** returns, per C1-H1…H7 in frozen order: `label`,
   `factor_spec_hash`, `hypothesis_definition_hash` and `hypothesis_id`.
   - It asserts every `hypothesis_id` equals `EXPECTED_HYPOTHESIS_IDS`.
   - On a mismatch it raises `C1BindingError` (a Stage-A / pre-open failure under E3).
   - No factor evaluation takes place.

## E4.2 Factor realization (replaces C1-LX §2.2 provenance use)

1. **Rename:** `factor_provenance_hash(spec, inputs)` → **`factor_realization_hash(spec, inputs)`**.
   The computation is unchanged (`evaluate_factor(spec, aliased).content_hash`).
   `RealInputAdapter.provenance_of` → `realization_of`, and the executor parameter
   `provenance_of` → `realization_of`.
2. **Expected realizations:**
   - The executor takes `expected_realizations: Mapping[label, sha256]`. These are the
     agreed values from the execution-readiness barrier's two independent runs.
   - They are recorded in `CAMPAIGN_OPEN` as `expected_factor_realizations`, which is
     part of the E1/§6 lock comparison.
   - A real run with a missing or incomplete mapping is a pre-open Stage-A failure (E3)
     and writes nothing.
3. **Identity establishment** (E2.3 item 2 is replaced): item 2 becomes "the
   hypothesis's `hypothesis_definition_hash` and `hypothesis_id` have been computed and
   validated against `EXPECTED_HYPOTHESIS_IDS`". The realization is **not** part of
   identity establishment.
4. **After establishment**, inside the primary attempt and before any PQB build,
   compute `factor_realization_hash` and compare it with `expected_realizations[label]`.
   - **Equal:** proceed. `PRIMARY_ATTEMPT` records `factor_realization_hash`.
   - **Unequal:** Stage B. Record `NOT_ASSESSED(BINDING_OR_IDENTITY_FAILURE)` with
     `failure_detail = "REALIZATION_MISMATCH"` and both hashes, consume the slot, then
     `CAMPAIGN_STOP INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE` (precommitment §11
     "determinism failure").
   - **Realization computation raises** (typed `C1RealizationError`): Stage B with
     `failure_detail = "REALIZATION_FAILURE"`.
   - Never treat the case as a different hypothesis. Never continue to evaluation.
5. **Diagnostics** (TOP_200/1000) reuse the same realization; they record its hash and
   nothing else.
6. **Replay:** unchanged (C1-LX §6, E2.3, E3). A replay producing a different
   realization hash fails closed (`C1ReplayMismatchError`). The record is never
   rewritten.

## E4.3 Unchanged

`campaign_attempt_id` formula (it takes the new `hypothesis_id`), `family_id`,
`SearchPolicy`, screen, dispositions, budgets, folds, the K-PIT preflight logic (it
compares (`hypothesis_id`, `factor_spec_hash`) pairs, now with the new IDs), the
ledger, E3 terminal semantics, and the firewalls.

## E4.4 Required tests (added; existing tests updated only where the renamed / re-derived identity requires)

1. The 7 definitions and IDs reproduce Correction §4 exactly, built from the real
   manifest bytes and repository files. These are metadata reads only.
2. Changing each component changes the ID: `factor_spec_hash`, `ti_id`, `ti_hash`,
   `manifest_sha256`, any one engine file hash, adding or removing an engine file, the
   schema string, the domain string. These are fixture-based.
3. `hypothesis_identities` performs no factor evaluation (a spy on `evaluate_factor`,
   `load_trusted_inputs` and the parquet read asserts zero calls).
4. Domain separation: no Campaign ID equals `registry.hypothesis_id_for(x)` for the
   definition hashes or IDs. The AST check confirms `hypothesis_id_for` is absent from
   `smart_beta/campaign/**`.
5. Realization equal → the attempt proceeds and records the hash. Unequal → Stage-B
   record with `REALIZATION_MISMATCH`, slot consumed, Campaign stop. A realization
   exception → `REALIZATION_FAILURE`, same path.
6. Missing `expected_realizations` for a real run → zero records (pre-open Stage A).
7. K-PIT preflight accepts the new IDs and rejects the legacy ID
   `09bb4ecf…38b1`.
8. Every pre-existing C1-LX/E1–E3 behaviour test still passes.

## E4.5 Mutations

**The existing 30 cases remain.** Where an existing mutation's `old` string no longer
exists because of the E4 rename, it must be re-targeted to the equivalent new code with
the **same semantic intent**, each re-target listed in the report. Deletion is
forbidden.

**New mutations:**
- LX-E4-M1: `hypothesis_id` derived via `hypothesis_id_for` again;
- LX-E4-M2: `manifest_sha256` dropped from the definition;
- LX-E4-M3: one engine file dropped from `ENGINE_SEMANTICS_FILES`;
- LX-E4-M4: the domain tag removed or changed;
- LX-E4-M5: the realization included in the definition (the ID depends on factor
  values);
- LX-E4-M6: realization mismatch ignored (proceeds to evaluation);
- LX-E4-M7: realization mismatch treated as a new hypothesis or attempt (a new ID or
  slot instead of Stage B);
- LX-E4-M8: the `EXPECTED_HYPOTHESIS_IDS` assertion removed;
- LX-E4-M9: `expected_realizations` not lock-compared in `CAMPAIGN_OPEN`.

**Frozen total:** 30 + 9 = 39 cases. LX-M13 remains the documented equivalent.

## E4.6 Firewall

Synthetic fixtures only. Exceptions: tests 1 and 7 read the real D7c **manifest JSON
bytes** and the repository code files; they read no parquet and no data values.
- No factor evaluation on real inputs.
- No production K-PIT write, no network, credentials scrubbed.
