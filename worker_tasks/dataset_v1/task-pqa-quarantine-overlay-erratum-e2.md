# PQA — Specification Erratum E2: complete, fail-closed parent bindings (ADOPTED)

- **Status:** ADOPTED. The user authorized it on 2026-10-01, after the P-Q
  Integration Barrier stopped at I2.
- **This is a standalone erratum.** It corrects
  `worker_tasks/dataset_v1/task-pqa-quarantine-overlay.md` (bab2296),
  `92e175f568b7c9c2257418cf0ad294cf21c079dfb9e9628ed3be3cbbf9743f44`, §2 and §11.
  That file is **not** edited.
- **The governing PQA authority is now** the frozen PQA spec **plus E2**. Where they
  differ, E2 governs.
- **Classification:**
  - **implementation defect:** empty file and manifest hashes are silently skipped,
    and the D4 manifest-field binding uses the manifest's file SHA-256;
  - **planner specification gap:** PQA §2 gave the D4 manifest file hash but not
    the D4 `identity_hash` field value;
  - **planner review miss:** `default_bindings()` was not checked item by item.
- **Methodology impact:** NONE. **Behaviour change:** the bindings become strictly
  fail-closed. The quarantine semantics are unchanged.

## E2.1 Binding rule (strengthens §2)

**Every bound stage must verify three things, exactly and fail-closed:**
1. **(a)** the SHA-256 of the manifest **file**;
2. **(b)** the manifest's named hash **field** value;
3. **(c)** the SHA-256 of every bound data file.

**Empty or missing values:**
- An empty or missing expected hash in the **production** bindings
  (`default_bindings()`) raises `QuarantineBindingError` **before any data file is
  read**.
- An empty string never means "skip".
- Synthetic test bindings must also be complete. Tests that exercise the
  empty-hash guard construct deliberately incomplete bindings and expect the error.

## E2.2 Exact production bindings (complete table; supersedes the §2 table where they differ)

| Stage / artifact | Manifest file → SHA-256 | Manifest field = value | Data files → SHA-256 |
|---|---|---|---|
| D5 `q-5d1028655f8c82e1` | `derived/qc/q-5d1028655f8c82e1/qc_report.json` → `16c582900b77a328ffaee8fdb78b8bc4b7920a0b48ebbc98f7a2e7e62c769ae1` | `qc_hash` = `3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3` | `day_flags.parquet` → `f4010ebeabf5cfae2684f3658a5f19f922f15e2c8898410049f2148440cf7891` |
| D4 `i-a21f1e46621dee94` | `derived/identity/i-a21f1e46621dee94/identity.json` → `b8ed3d23979f8cbb9f320fae96a8cea968b16332fd1000fa1cdedca15df43aa4` | `identity_hash` = `1023dbca39fd061d52fd26eeb6302f4a8dec467a8ea996b1903da801306ae730` | `identity_assignment.parquet` → `fee4614009095773f75fb832343a5fa9b87b9de1cf363125567222b08fad3f8b` |
| D7c `ti-aa23fabd3db4e595` | `derived/trusted_inputs/ti-aa23fabd3db4e595/trusted_inputs.json` → `d8d40099ba978604727a7d316274a2d3e7f8210baf8cb35e4ca687435fd8f8f0` | `ti_hash` = `30ee0c51602cf792e8cb7654c2501f52c7d664675dbf78845c903df6683c2ae6` | `daily_total_return.parquet` → `511bcf4ac4ea2bd02158438732e0fdf19c04d783b88ca7a01ff5a364ec8e688c`; `dollar_volume.parquet` → `c8a632ef455ecec5f3caa95816ec0bb71789e3ed51b9447765021c697fa27780`; `total_return_index.parquet` → `53a48a65a70d16b80374b926b56c50922ebfc521d3f8c1677380343a8ccd5e8e`; `segments.parquet` → `4ea4df04c3e581f1750229c255ed1f5c5ec5055ad6fe954483cc7ae5b76cc384` |
| D7a `r-34565f074d64ccda` | `derived/returns/r-34565f074d64ccda/returns.json` → `6093bec9730e5bd0a6e9e1410db165fca148e3be1b01ef2534db12e34370886e` | `returns_hash` = `4c487e47fd613f3f272109d14b43a27e061f9cc31e5b6d6bcefd4abc97994861` | `labels.parquet` → `262b8b1d445dfb329d33359966a9deba67bd5999ccdc01dd8183eda6f4f2645e`; `members.parquet` → `fcb2c9df2918a7bd43399b220f9595fc94d8a5dc49542219698ecafb7c6b4488`; `horizons.parquet` → `61a2ac310021f153676445de7f0122ef74f67910e6506a6e5ef1e286f3d1ccbb` |
| D6 `u-6150bc6ac2866b7f` | `derived/universe/u-6150bc6ac2866b7f/universe.json` → `8e5e4ecd1729c2bbf75b1ac1bdd5d497f9f2a360061edb407fc323feb433d737` | `universe_hash` = `6e8539b7acb4ed4559846234b0b26e6f1d4e6d34f7e0a5d1e2290134d563ed87` | `universe_development.parquet` → `f933a67ff8c83ad9f6c7937a76136d93419cedb0ec79cec617df8205b3d38d8b` |

**Provenance of the D4 field value:**
- `1023dbca…e730` is the `identity_hash` recorded in the D4 manifest. It is also the
  D4 `manifest_hash` frozen in the D7c artifact's own bindings
  (`trusted_inputs.json` `/bindings/stages[1]/manifest_hash`).
- It is therefore part of the frozen lineage, **not** a value discovered at barrier
  time.
- `b8ed3d23…aa4` is the D4 manifest **file** SHA-256 (rule (a)), as originally
  listed in §2.

## E2.3 Required tests (added to §11)

1. **Production completeness:** every hash in `default_bindings()` is non-empty, is
   64 lowercase hex characters, and equals the E2.2 value, exhaustively.
2. **An empty expected hash** for a data file, a manifest file or a manifest field
   raises `QuarantineBindingError`, and no data file is opened (spy).
3. **A wrong manifest-file hash, wrong field value or wrong data-file hash** each
   raise `QuarantineBindingError`.
4. **The D4 field binding uses the field value**, not the file SHA-256. A mutation
   swapping them must be killed.

## E2.4 Required mutations (added to §12, numbered after the current set)

- skip a data-file hash when empty;
- skip a manifest-file hash when empty;
- skip the manifest-field check when empty;
- the D4 field bound to the file SHA-256;
- D5 day_flags hash removed from production bindings;
- D4 identity_assignment hash removed from production bindings.

## E2.5 Consequences

- **PQB pin:** PQB's pinned SHA-256 for `quarantine.py` must be updated to the
  repaired file's hash. That is the only PQB change, in PQB's owned file, as a
  separate reviewed commit.
- **Barrier:** the P-Q Integration Barrier restarts from I1 after the repairs merge.
