# Discovery Dataset v1 — Wave 2 (D3 + D4) plan

**Authority:** `docs/discovery_dataset_v1_contract.md`, SHA-256
`d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`. The
contract is frozen and prevails over this plan wherever they disagree.

**Acquisition record:** `docs/discovery_dataset_v1_acquisition_record.md`,
`acquisition_record_sha256 =
256fa30f1372b08221784bd723eb74aaa469a00e07a012fb569aeac91630a0cf`, committed
in `1730303`.

**Base:** master `2684717`, plus the Wave-2 planning commits.

**Approval:** the post-acquisition architecture review was approved by the
user on 2026-09-27, together with decisions D4-1, D4-2 and D4-3 below.

**Scope:** D3 (normalizer) and D4 (identity resolver) only. D5, D-SC and
D6–D8 are out of scope for this wave, and so is any provider call.

## 1. Tasks, ownership and parallelism

| Task | Spec | Owns (new files only) |
|---|---|---|
| D3 | `task-d3-normalizer.md` | `smart_beta/dataset_v1/normalize.py`, `tests/test_dataset_v1_normalize.py`, `tests/fixtures/dataset_v1/d3/**` |
| D4 | `task-d4-identity.md` | `smart_beta/dataset_v1/identity.py`, `tests/test_dataset_v1_identity.py`, `tests/fixtures/dataset_v1/d4/**` |

- **D3 and D4 are implemented in parallel.**
  - D4 depends only on the frozen interface in §3. It **never imports D3
    code** and tests against its own fixtures that follow that interface.
  - At run time on production data, D3 runs first and D4 consumes D3's build.
  - No file is shared between the tasks.
- **Neither task may touch:**
  - any existing file, including `smart_beta/dataset_v1/__init__.py`,
    `candidate_pool.py`, `acquisition.py`, `smart_beta/vendors/**`,
    `smart_beta/pit/**` and sealed Phase-4B/6–10 code;
  - the contract and the acquisition record;
  - `pilot_evidence/**`;
  - Phase-5B resources.
- **Read-only imports allowed:**
  - `smart_beta.vendors.tiingo.calendar_source.build_nyse_calendar`, for both
    tasks;
  - `smart_beta.vendors.tiingo.corporate_actions.map_eod_to_corporate_actions`,
    for D3 only;
  - `smart_beta.dataset_v1.acquisition` public constants and helpers
    (`canonical_json`, `TERMINAL_STATUSES`), for D3 only. D4 must not import
    it.

## 2. Approved decisions (binding)

| ID | Decision |
|---|---|
| D4-1 | Identity fit is tested against **all** supported-tickers snapshot rows whose `ticker` equals the requested ticker exactly, not only D1 pool rows. |
| D4-2 | A **boundary touch** (one row's `endDate` equals another row's `startDate`, or the reverse) is an overlap and fails closed as `IDENTITY_AMBIGUOUS`. |
| D4-3 | Overlap analysis covers **all** of the ticker's snapshot rows, including rows outside the pool. |
| R-1 | §10 ownership refinement: D3 owns only the §10 classes that do not depend on identity. The listing-relative classes (NOT_YET_LISTED, ENDED, VENDOR_MISSING on listed sessions, TERMINATION_MISMATCH, INTERVAL_MISMATCH application, the > 5% exclusion) belong to D5, after D4 interval assignment. This clarifies implementation ownership and does not change dataset semantics. |
| R-2 | D5 owns QC gates 1–10. Gates 11–14 remain acceptance criteria of D6, D7 and D8 and of the final certification barrier. |
| R-3 | Reserve-period gate-5 and gate-6 checks (D5) may run structurally over reserve rows but emit only the flags and counts the frozen QC contract requires. |
| R-4 | The D3 build binds `acquisition_record_sha256`. The immutable acquisition evidence is never rewritten. |

## 3. Frozen D3 ↔ D4 interface

### 3.1 Directories and encoding

D3 writes **one normalized build** under the data-store root (default
`~/Developer/personal/smart_beta/data_store/discovery_dataset_v1`):

```
<root>/normalized/<build_id>/build.json
<root>/normalized/<build_id>/source_summary.parquet
<root>/normalized/<build_id>/observations.parquet        # dates <  2023-07-03
<root>/normalized/<build_id>/ca_events.parquet           # effective_date <  2023-07-03
<root>/normalized/<build_id>/obs_flags.parquet           # dates <  2023-07-03
<root>/reserve_sealed/<build_id>/observations.parquet    # dates >= 2023-07-03
<root>/reserve_sealed/<build_id>/ca_events.parquet
<root>/reserve_sealed/<build_id>/obs_flags.parquet
```

- **Parquet:** written with pandas and `engine="pyarrow"`, `index=False`,
  `compression="zstd"`, and pandas/pyarrow defaults otherwise.
- **Rows** are sorted as each table specifies. `build.json` is canonical JSON:
  sorted keys, `(",", ":")` separators, ASCII, one trailing `"\n"`.

### 3.2 `build.json` (D3 → D4 binding)

```json
{
  "schema": "discovery-dataset-v1-normalized-build-v1",
  "build_id": "n-<first 16 hex of SHA-256 of canonical_json(build_inputs)>",
  "build_inputs": {
    "contract_sha256": "<64 hex>",
    "batch_id": "b-…",
    "expected_pool_sha256": "<64 hex>",
    "snapshot_sha256": "<64 hex>",
    "C": "YYYY-MM-DD",
    "acquisition_start": "2015-01-02",
    "reserve_start": "2023-07-03",
    "manifest_files_sha256": {"batch.json": "…", "status.json": "…", "attempts.jsonl": "…"},
    "acquisition_record_sha256": "<64 hex>",
    "normalizer_code_sha256": "<SHA-256 of normalize.py bytes>",
    "ca_mapper_code_sha256": "<SHA-256 of smart_beta/vendors/tiingo/corporate_actions.py bytes>",
    "calendar_code_sha256": "<SHA-256 of smart_beta/vendors/tiingo/calendar_source.py bytes>"
  },
  "files_sha256": {"<relative path under <root>>": "<64 hex>"},
  "normalized_hash": "<SHA-256 of canonical_json(files_sha256)>",
  "counts": {"<name>": 0}
}
```

`build.json` contains **no timestamps**. Rebuilding from the same inputs must
give byte-identical files and an identical `build.json`.

### 3.3 `source_summary.parquet` (the D4 input)

There is exactly **one row per expected identifier** in the batch header:
10,903 in production, **including** EMPTY, UNREQUESTABLE_IDENTIFIER and any
other non-OK status. Rows are sorted by `ticker`, using Python string order.

| Column | Type | Meaning |
|---|---|---|
| `ticker` | string | vendor identifier, verbatim (the source-record key; **never** a security identity) |
| `security_keys` | string | canonical JSON list of the header's `security_keys` for this ticker (carried, not interpreted) |
| `requestable` | bool | from the header |
| `acquisition_status` | string | terminal status from `status.json` |
| `raw_file` | string, nullable | from `status.json` |
| `raw_sha256` | string, nullable | from `status.json`; re-verified by D3 |
| `row_count` | int64 | number of rows parsed (0 when there are no rows) |
| `first_date` | date32, nullable | first observation session date |
| `last_date` | date32, nullable | last observation session date |
| `n_off_calendar` | int64 | rows whose date is not a trusted NYSE session |
| `n_duplicate_date` | int64 | rows sharing a date with another row of the same ticker |
| `n_vendor_null` | int64 | rows with null `close` or null `volume` |
| `n_zero_volume` | int64 | rows with `volume == 0` |
| `n_malformed_ca` | int64 | rows flagged MALFORMED_CA |
| `n_in_span_gap_sessions` | int64 | trusted sessions in [`first_date`, `last_date`] with no row |

**Invariant:** for every OK ticker, `row_count`, `first_date` and `last_date`
equal the manifest's values. D3 aborts otherwise.

### 3.4 `observations.parquet` (both partitions)

- **Sort order:** (`ticker`, `date`, `raw_row_index`).
- **Columns:**
  - `ticker` string, `date` date32;
  - `open`, `high`, `low`, `close`, `adjOpen`, `adjHigh`, `adjLow`,
    `adjClose`, `divCash`, `splitFactor` as float64;
  - `volume`, `adjVolume` as nullable Int64;
  - `raw_row_index` int64: the zero-based position in the raw JSON array;
  - `raw_sha256` string.
- **Scope:** one row per raw row of every OK file. Nothing is dropped,
  shifted or filled.
- **D4 reads only `ticker` and `date`** from both partitions.

### 3.5 What D4 may read

- `build.json`;
- `source_summary.parquet`;
- the `ticker` and `date` columns of both `observations.parquet` files;
- the D1 pool JSON;
- the supported-tickers snapshot CSV;
- the trusted calendar.

D4 must verify that the pool and snapshot SHA-256 values equal
`build_inputs.expected_pool_sha256` and `build_inputs.snapshot_sha256`, and
refuse to run otherwise. **D4 never reads a price or volume column.**

### 3.6 D4 output (consumed later by D5 and D6; frozen here for stability)

```
<root>/derived/identity/<identity_id>/identity.json
<root>/derived/identity/<identity_id>/identity_assignment.parquet
<root>/derived/identity/<identity_id>/interval_mismatch.parquet
```

- **`identity_id`** = `"i-" +` the first 16 hex characters of
  `SHA-256(canonical_json({normalized_build_id, normalized_hash, expected_pool_sha256, snapshot_sha256, identity_code_sha256, tolerance_sessions: 5}))`.
- **`identity_assignment`:** one row per pool `security_key`, sorted by
  `security_key`. The columns and states are defined in `task-d4-identity.md`.
- **`interval_mismatch`:** columns `security_key`, `ticker`, `date`,
  `raw_row_index`, sorted in that order.
- **`identity.json`:** canonical JSON with no timestamps, containing:
  - the inputs and their hashes;
  - `files_sha256`;
  - `identity_hash`;
  - counts per state and reason.

## 4. Launch preconditions (planner; user authorization required)

- **P1 — Parquet engine.**
  - The project venv has no Parquet engine (`pyarrow` and `fastparquet` are
    both absent), while contract §14 requires Parquet for `normalized/`.
  - Before launch, the planner must, **with the user's authorization**:
    - add `pyarrow` to `pyproject.toml` dependencies in its own commit;
    - install one pinned `pyarrow` version into
      `/Users/dingningpei/Developer/personal/smart_beta/main/.venv`;
    - record the version in that commit.
  - Byte-reproducibility (§16.10) is claimed only for a fixed pandas/pyarrow
    environment.
  - If this is not authorized, Wave 2 does not launch. A different format
    would need its own contract-conformance decision.
- **P2:** create Herdr worktrees for D3 and D4 at the post-P1 master, then
  launch the Pi workers (deepseek-v4-flash), with the user's launch
  authorization.

## 5. Security (both tasks)

- **No provider calls.** Tests use `tmp_path` fixtures only. Tests never read
  `~/Developer/personal/smart_beta/data_store/`.
- **Test runs** remove provider keys from the process environment for that run
  only:
  `env -u TIINGO_API_KEY -u TUSHARE_PROXY_TOKEN -u TUSHARE_BASIC_PROXY_TOKEN -u TUSHARE_API_TOKEN -u DEEPSEEK_API_KEY -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u OPENAI_API_KEY PYTHONPATH=$PWD /Users/dingningpei/Developer/personal/smart_beta/main/.venv/bin/python -m pytest -q <args>`
- **Never print, log or persist a credential value.**
- **Research firewall:** no worker computes or reports returns, dollar
  volume, statistics of price or volume values, or any factor or performance
  quantity. Production builds are run by the planner after review, not by
  workers.

## 6. Stop conditions

A worker stops and reports, without guessing, if any of the following occurs:
- the contract, this plan or the task spec is silent or contradictory on a
  behaviour it needs;
- it needs a file outside its ownership or a new dependency;
- it would need a live call or real production data;
- it finds that the frozen interface cannot express a required fact.

## 7. Wave barrier

Each task receives an exact-SHA review, mutation tests and the
credential-scrubbed task and full suites, and is merged with `--no-ff`.

After both are merged, the planner runs, only with authorization:
- the D3 production build, twice, requiring byte-identical output;
- D4 on that build.

The planner reports structural counts only, then **STOPS** before D5.
