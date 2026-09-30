# Discovery Dataset v1.1 — Gate-6 Specimen Audit: Pre-Network Manifest (DATA-FREE)

- **Status:** G6-E1 output, sealed **before any network request** (spec
  `worker_tasks/dataset_v1/task-g6-specimen-audit.md` §5 and §15). No SEC
  request has been made. No filing content, extraction or audit outcome
  exists.
- **Data-free:** this file holds hashes, counts and provenance only. It
  contains no security keys, observation dates, prices, ratios, filing
  contents or contact details. The specimen and fetch-plan rows stay in
  `data_store` (contract §14).

## Authority

| Item | Commit | SHA-256 |
|---|---|---|
| Contract | 7348a31 | `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3` |
| Gate-6 clarification | cab82e0 | `0b529dca209b4fa9f54f9662bb3d9205f07b0056232532b2a529a3a513f43789` |
| Gate-6 spec | 05ef922 | `1dc4c7e0f8a09ebe4e6322d98d42c17aa95301b3aed8bf7321ad1bf6e637e7e2` |
| A1/A2 addendum | 4695544 | `9960d3e19f2966526c82767cfb15fd7e4a50383b2c42f83dcc9de41b130dcfa6` |

## Software

- **G6-SW:** merged at 9372a7c (reviewed HEAD d4f98fe).
- **Performance repair:** merged at 2bd9829 (reviewed HEAD 39b1fe9). It is
  behaviour-preserving: synthetic artifacts are byte-identical to d4f98fe.
- **Module hashes:**
  - `smart_beta/dataset_v1/gate6_audit.py`: `819c927a95c6588d3259d759cec3c588b0fdc7dc757a8a4bf54f47c9a7de4882`
  - `smart_beta/dataset_v1/gate6_sec_fetch.py`: `e1d258f7678180ad55a8d6c2b63445a0158e002d78ee9367b287c60f14733b02`
  - `code_sha256`: `dc14575fad40c7059e2309393bdc80b8155819011ab92c8274c9c8dde0385d8d`

## Production input bindings (verified before selection)

| Input | Binding |
|---|---|
| D5 `day_flags` (development) | `f4010ebeabf5cfae2684f3658a5f19f922f15e2c8898410049f2148440cf7891`; `qc_hash` `3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3` |
| D3 `observations` / `ca_events` (development, build `n-f874d3ecb61374d4`) | `557001d0e9c617befd3c036683f171094e86e19badb760098f40c08f3f56596e` / `afd4a2986c934d84c2b23eaf68f23ee18746e7c929c6b8625b6c9911e4a2b109` |
| D4 `identity_assignment` | `fee4614009095773f75fb832343a5fa9b87b9de1cf363125567222b08fad3f8b` |
| D7c `daily_total_return` (`ti-aa23fabd3db4e595`) | `511bcf4ac4ea2bd02158438732e0fdf19c04d783b88ca7a01ff5a364ec8e688c` |
| D-SC `issuer_mapping` (`c-27b6dabd0db0cb15`) | `dbfb424e50ccdc8127ba9064228738cfc105d5d4e7d4071694a27e28d3e127b5` |
| D-SC `dsc.json` | file `23877643018f28b4776e092be1c53d048f0c51a810544f073f9a68f57abcc030`; `dsc_hash` `2ee4940c14a85048fddd9e3e675c3e9a6f40a11c8887192a5e61939adfc62b60` |
| SEC full-index | all 122 files `master_1993Q1.gz` … `master_2023Q2.gz`, each verified against `dsc.json` `sec_index_files_sha256`; rows filed after 2023-06-30 discarded |

## E1 result

- **`g6_id`:** `g6-2264dbcac2a09a3d` (artifacts under
  `data_store/discovery_dataset_v1/derived/qc_audit/g6-2264dbcac2a09a3d/`).
- **Population:**
  - 17,469 development CORP_ACTION_SUSPECT observations over 2,674
    securities; all UNCERTIFIED_SEMANTICS; no reserve row;
  - `population_sha256` `d2153329b8c13a5d20d2bbe351972e048c9dc421216051e08633ffda68fb2783`.
- **Specimen:**
  - 180 specimens over 180 distinct securities (S3 20, S1 120, S2 40); no
    shortfalls;
  - `specimen_sha256` `c0c15d20b4935a86d8ca75745425fc18b560c5e0619f908a3a1f3b8705d23b1e`.
- **Fetch plan:**
  - 1,000 specimen→filing associations and 1,000 unique planned GETs (no
    shared filings), under the cap of 2,500;
  - at most 12 filings per specimen; allowlisted forms only; mapping states
    for 142 specimens are MAPPED, the rest NO_ISSUER_NAME 13,
    NO_ISSUER_MAPPING 13, NO_FILINGS_IN_WINDOW 7 and
    AMBIGUOUS_ISSUER_MAPPING 5;
  - `fetch_plan_sha256` `98aa5f81e80b41f41af8672af3e24e3f2c897bb8ebfef8771038762eacebca66`.
- **E2 verifier:** the fetch-plan hash, verified offline through the E2
  pre-fetch path (`SecFetcher` constructor), matches. 0 requests.
- **Determinism:** a clean rerun under `TZ=Asia/Tokyo LC_ALL=C` produced the
  same `g6_id`, the same hashes and byte-identical artifacts.

## E1 artifact hashes

| File | SHA-256 |
|---|---|
| `fetch_plan.parquet` | `b5f33fbab7b77e3969e4e253db33bb2255df39d239e4c4e76e132e0205e23a1a` |
| `population.json` | `e163018c66918201e09ad025b9ef3d26eb1e315ce4dd16b4deb9f63239dae932` |
| `specimen.json` | `1d61a8f6058720d03c12ba95bee0c909e074cfd7c6065e88eee31487f7550cad` |
| `specimen.parquet` | `098d285bc8062a15ee7df72ea39c04e36e982cfc398c179800e2d726a2925ed0` |
| `specimen_manifest.md` | `2c5270b234183bfffe1473da1802688e37ee784e87c4d168ecefd1a3c2d5df2c` |

## Generated manifest (verbatim; SHA-256 `2c5270b234183bfffe1473da1802688e37ee784e87c4d168ecefd1a3c2d5df2c`)

```
# Gate-6 specimen audit — pre-network manifest (data-free)

- algorithm: `g6-specimen-v1`
- population_sha256: `d2153329b8c13a5d20d2bbe351972e048c9dc421216051e08633ffda68fb2783`
- specimen_sha256: `c0c15d20b4935a86d8ca75745425fc18b560c5e0619f908a3a1f3b8705d23b1e`
- fetch_plan_sha256: `98aa5f81e80b41f41af8672af3e24e3f2c897bb8ebfef8771038762eacebca66`
- specimen_size: 180

## per-cell counts
- S3:: 20
- S1:B1: 30
- S1:B2: 30
- S1:B3: 30
- S1:B4: 30
- S2:B1: 10
- S2:B2: 10
- S2:B3: 10
- S2:B4: 10

## shortfalls
- S3:: 0
- S1:B1: 0
- S1:B2: 0
- S1:B3: 0
- S1:B4: 0
- S2:B1: 0
- S2:B2: 0
- S2:B3: 0
- S2:B4: 0

## mapping-state counts
- AMBIGUOUS_ISSUER_MAPPING: 5
- MAPPED: 142
- NO_FILINGS_IN_WINDOW: 7
- NO_ISSUER_MAPPING: 13
- NO_ISSUER_NAME: 13
```
