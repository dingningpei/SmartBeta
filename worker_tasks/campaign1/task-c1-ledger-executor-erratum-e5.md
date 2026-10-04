# C1-LX-E5 — Production runner: overlay loader, bindings, expected realizations, fail-closed K-PIT preflight, entry point (frozen)

- **Status:** FROZEN. Approved by the user ("批准 C1-LX-E5，输出放 data_store/campaign1/"),
  on master `3ee0bf5`.
- **The implementation is authorized together with this erratum.** Merging needs a
  separate authorization. **Running the entry point (Campaign-1 execution) is NOT
  authorized.**
- **Origin:** CAMPAIGN-1 PRE-EXECUTION BARRIER **HOLD**, findings H-1, H-2 and H-3.
- **Effective authority:** C1-LX task (`118bcf41…`) + E1 (`6cc544ba…`) + E2
  (`90251a1d…`) + E3 (`e070a6a7…`) + E4 (`1c5a001c…`) + **E5**, governed by
  `docs/campaign1_identity_architecture_correction.md` (`50f9929f…`) and
  `docs/campaign1_execution_readiness_evidence.md` (`70b920bf…`).
- **Base:** master `3ee0bf5` or the commit that freezes this document.
- **Owned files:**
  - `smart_beta/campaign/runner.py` (new);
  - `smart_beta/campaign/campaign1.py` (E5.4 only);
  - `tests/test_campaign1_runner.py` (new);
  - `tests/test_campaign1_executor.py` (E5.4 test adjustments only);
  - `tests/fixtures/campaign1/runner/**` (new, including `mutations.json`).
- **Must not modify:** `quarantine.py`, `pq_evaluation.py`, `pq_reporting.py`, D7b,
  D7c, Phase-6/7/8/9/10 code, and every frozen document.

## E5.1 Overlay loader (H-1)

`load_campaign_overlay(dataset_root) -> quarantine.Overlay`:
1. Call the existing **`quarantine.verify_overlay(dataset_root)`**. It re-hashes every
   overlay file against `pq.json` `files_sha256`. Use its returned manifest.
2. **Assert:**
   - `pq_id == "pq-59c3bd7abe08eef5"`;
   - `pq_hash == "baa920ddfc52805c70445abfb8650e2969d341ae9383e1b1a0c6a788fcc95f2c"`;
   - `identity_hash == "a065493d295883f425d499816a98ac55a03cd67a1086bd7b64249b90fe76b9da"`;
   - `quarantine_rule_hash == quarantine.QUARANTINE_RULE_HASH`
     (`df5bec69…b886`).
3. **Construct** `quarantine.Overlay(...)` from that directory:
   - `flags` = `flags.parquet`;
   - `primitive_states` = `primitive_states.parquet`;
   - `input_states` = `input_states.parquet`;
   - `label_states` = `label_states.parquet`;
   - `liquidity_exposure` = `liquidity_exposure.parquet`;
   - `identity` = parsed `dataset_identity.json`;
   - `pq_id` and `pq_hash` from the manifest.

   Read with `pandas.read_parquet` without transformation. `label_states` holds
   quarantine **states** only. No label or return values are read.
4. **Failure:** any failure raises `C1BindingError`, which is a pre-open Stage-A failure
   under E3.

## E5.2 Production bindings (H-2)

`production_bindings(dataset_root, *, knowledge_log_path) -> CampaignBindings`. Every
value is derived from its authoritative source **and** asserted against the frozen
value. Any mismatch raises `C1BindingError`.

| Field | Source | Frozen value |
|---|---|---|
| `dataset_id` | `pq_evaluation.DATASET_ID` | `discovery_dataset_v1_2` |
| `dataset_identity_hash` | `quarantine.verify_dataset_v1_2_identity(dataset_root, knowledge_log_path=…)`. This reads repo documents and inherited-artifact **manifests** only, and checks only that K-log `records[0]` is the D8 genesis | `a065493d295883f425d499816a98ac55a03cd67a1086bd7b64249b90fe76b9da` |
| `amendment_sha256` | `quarantine.AMENDMENT_SHA256` and the SHA-256 of `docs/discovery_dataset_v1_2_pq_amendment.md` | `9261a55b9b82a5708dded65bfd3083d5a960cfa4375681b1b23390da62fb3ed0` |
| `admission_sha256` | the SHA-256 of `docs/discovery_dataset_v1_2_development_admission.md` | `8c720c29938497aca98716dc317b77d0b68ea8126db33b35459fdf90132129ee` |
| `pq_id`, `pq_hash` | the E5.1 manifest | as in E5.1 |
| `quarantine_rule_hash` | `quarantine.QUARANTINE_RULE_HASH` and the SHA-256 of `docs/discovery_dataset_v1_2_pq_quarantine_rules.md` | `df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886` |
| `r3_identity_hash` | the same as `dataset_identity_hash`, which is the R3 identity | `a065493d…b9da` |
| `pq_reporting_sha256` | the run-time SHA-256 of `smart_beta/dataset_v1/pq_reporting.py` | `8a892999a4b887b92a212a53571f1e28cbec63ca976741f13175b75a432341dd` |
| `pq_evaluation_sha256` | the run-time SHA-256 of `smart_beta/dataset_v1/pq_evaluation.py` | `322570ec810516c6b16493c6a060fe26d1479078477f14b4e05d6b8ea0b8aca3` |
| `partition_id` | `partition_id(build_partition())` | `b4b56dbfc82377930b8bf87a1d8039a69ccbf7d42d5faf234d3bb8cbbcb936fb` |

## E5.3 Expected realizations (H-2)

`expected_realizations_from_evidence(path=docs/campaign1_execution_readiness_evidence.md) -> dict[label, sha256]`:
1. Assert the file's SHA-256 is
   `70b920bfc7346794bb82d0a757d32491d72eaf961d1be7d99b47d7026769ccd4`.
2. Parse the 7 results-table rows.
3. Assert:
   - the labels are C1-H1…C1-H7 in order;
   - each row's `hypothesis_id` equals `EXPECTED_HYPOTHESIS_IDS`;
   - each `factor_spec_hash` equals the frozen spec hash.
4. Also assert the parsed map equals the module constant `EXPECTED_FACTOR_REALIZATIONS`
   (the 7 values below). The parse is cross-checked against the constant; neither one
   alone is the source.

```
C1-H1 c1dfedb7e77758b11cda2547f20ed3c012d3ffc48d26d0ca627322ff0041c714
C1-H2 e8d94efb31e5b4063869aa2e1de5eedd5d244fe5812eb9abb5aa2b0106c8243f
C1-H3 72d9f8538bcc8fa9631dfc4c0f4a13301189fd2cf60d4e5960ebcda0fe9f52cf
C1-H4 5f490ac4df78c213b3e12c3433c1fcbf740563c3b3ed52fbda5b318a83146c25
C1-H5 201fed58d42c2e9c247c914cad1abc8c6fc39644ebc66e811a2dd923807c49e9
C1-H6 93cf20645ca8389e0be21bd24000a5f3d1328166f84e9741299f9a0b4ea2d280
C1-H7 529fdf7b2b8ca69200b8d64a3123de565ca1906c157eaaf8d722f9b79bd7657d
```

## E5.4 Fail-closed K-PIT preflight (H-3; `campaign1.py`)

`CampaignExecutor` with `require_kpit=True` (the default) and
`knowledge_log_path is None` must **refuse to open**:
- it returns the typed stop `GOVERNANCE_CONFLICT`;
- it writes nothing (pre-open Stage A, E3.1).

The silent `KpitPreflight(True, None, ())` path is only reachable with an explicit
`require_kpit=False`, which is a test-only switch. `runner.py` must never pass
`require_kpit=False`. A static test checks this. Existing executor tests that relied
on the old fail-open default are updated to pass `require_kpit=False` explicitly. No
other behaviour changes.

## E5.5 Entry point and output location (H-2; user decision)

**Fixed defaults** (the CLI takes no flags that change semantics):
- `DATASET_ROOT = <repo>/../data_store/discovery_dataset_v1`;
- `KNOWLEDGE_LOG = <repo>/../data_store/knowledge_pit/knowledge.jsonl`;
- **`OUTPUT_ROOT = <repo>/../data_store/campaign1`** (user decision).

**`preflight_campaign1(...) -> dict`** is **read-only**. It writes no file and creates
no directory. It:
- performs E5.1, E5.2 and E5.3, and C1-LX `verify_kpit_ordering` with
  `hypothesis_identities(root)`;
- computes `campaign1_id`;
- asserts the output directory `OUTPUT_ROOT/<campaign1_id>/` does **not** yet contain
  a ledger;
- returns hashes and identities only.

It performs no factor evaluation, no PQB/D7b build and no label/return read. A trap
test checks this.

**`run_campaign1(...)`:**
1. run `preflight_campaign1`;
2. create `OUTPUT_ROOT/<campaign1_id>/`;
3. use the ledger `…/ledger.jsonl`;
4. construct `CampaignExecutor` with the E5.2 bindings, E5.1 overlay, E5.3
   `expected_realizations`, `knowledge_log_path=KNOWLEDGE_LOG`, `require_kpit=True` and
   the real-input adapter defaults;
5. `run()`;
6. write `report.json` and `report.md` via `render_report` into the same directory.

**Rules:**
- The output directory must resolve outside the Git working tree; otherwise
  `C1BindingError`.
- `python -m smart_beta.campaign.runner preflight` runs the preflight.
  `python -m smart_beta.campaign.runner run` performs execution and **must not be
  invoked in this task**.

## E5.6 Required tests (synthetic; no real factor evaluation, no real PQB/D7b, no K-PIT write)

1. The overlay loader succeeds on a synthetic overlay directory with a valid manifest.
   It fails on a tampered file hash, a wrong `pq_id`/`pq_hash`/`identity_hash`, or a
   missing file.
2. `production_bindings` succeeds with stubbed authorities and fails on each single
   field mismatch.
3. Evidence parsing:
   - the correct file passes;
   - a byte-modified file fails the SHA check;
   - a reordered, altered or missing row fails;
   - a parsed map that differs from the constant fails.
4. E5.4: `knowledge_log_path=None` with the default `require_kpit` gives
   `GOVERNANCE_CONFLICT` and zero records. A static check confirms `runner.py` never
   passes `require_kpit=False`.
5. `preflight_campaign1` makes zero writes (directory listing unchanged), opens no
   ledger, and makes no `evaluate_factor`, `build_pq_diagnostics` or
   D7b/label/return read (traps).
6. The output directory inside the Git tree is refused. An existing ledger is refused
   by the preflight.
7. **Real read-only metadata checks** (allowed): on the real `data_store`,
   `load_campaign_overlay` manifest assertions, `production_bindings` and
   `expected_realizations_from_evidence` succeed. These read the overlay **manifest**
   and file hashes (via `verify_overlay`), the repo documents and the manifests.
   Loading the real parquet contents into an `Overlay` is permitted, but only its
   identity fields are asserted.
8. All existing campaign tests pass.

**Mutations** (new runner set; the 39 existing cases are unchanged):
- E5-M1: the `verify_overlay` call is removed;
- E5-M2: the `pq_hash` assertion is removed;
- E5-M3: an overlay field mapping is swapped (e.g. `input_states` ↔
  `primitive_states`);
- E5-M4: one binding assertion is removed;
- E5-M5: `r3_identity_hash` is decoupled from `dataset_identity_hash`;
- E5-M6: the evidence SHA check is removed;
- E5-M7: the evidence parse is not cross-checked against the constant;
- E5-M8: K-PIT fail-open is restored when the path is None;
- E5-M9: `runner` passes `require_kpit=False`;
- E5-M10: the preflight creates the output directory;
- E5-M11: the output directory inside Git is accepted;
- E5-M12: an existing ledger is not refused.

Every non-equivalent mutation must be killed.

## E5.7 Non-changes

| Item | Change |
|---|---|
| Hypotheses / IDs / FactorSpecs | NONE |
| `family_id`, m, α, critical value | NONE |
| Screen / dispositions | NONE |
| Budgets / stops (except the E5.4 fail-closed fix, which restores C1-LX §9) | NONE |
| Folds | NONE |
| Dataset / overlay | NONE |
| Phase code | NONE |
| Empirical information used | NONE |
