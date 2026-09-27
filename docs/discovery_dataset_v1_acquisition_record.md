# Discovery Dataset v1 — Acquisition Provenance Record

This record is **data-free**. Contract §14 permits committing a manifest
summary of hashes, counts and statuses. It **adds** provenance and changes no
acquisition evidence: the batch header, `status.json`, `attempts.jsonl` and
every raw file are left exactly as written by the acquisition tool.

Downstream builds bind to this record through `acquisition_record_sha256`, the
SHA-256 of this file's exact committed bytes. That hash is recorded outside the
file, in the committing message and in the Wave-2 plan.

- **Contract:** `docs/discovery_dataset_v1_contract.md`, SHA-256
  `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`
  (frozen, unchanged).
- **Evidence classes:** follow `CLAUDE.md` §8. Every count below is
  LIVE-RECORDED acquisition metadata, verified by the planner's structural
  audit. None of it is a research outcome.

## 1. Batch identity and bindings

| Item | Value |
|---|---|
| `batch_id` | `b-c21b48190918f21f` (the only production batch) |
| Data-store root | `~/Developer/personal/smart_beta/data_store/discovery_dataset_v1/` (outside Git) |
| Header `created_at_utc` | `2026-09-27T00:05:53Z` |
| C | `2026-08-31` |
| Requested range | `startDate=2015-01-02`, `endDate=2026-08-31`, identical on every attempt |
| Expected-pool SHA-256 (D1) | `8820de1689821fc97e6d0dc1ae501bc8f56c7dffeab2677939a2ae505b0cf8a3` (`production_v1/PRODUCTION_expected_pool_C2026-08-31.json`) |
| Supported-tickers snapshot SHA-256 | `f6b1d6c2befaac8a8ca711f05512786753d8f5eeadb450f40d2aac6e46a94124` (`smoke_starter_live/snapshot/supported_tickers.csv`) |
| Manifest schema | `discovery-dataset-v1-manifest-v1` |
| Header `acquisition_tool_sha256` | `aa265cb8c9db0a94a8a50137fd647f6ee38077e0071c21f35cc0a30e61b2c9ac`, which is exactly `smart_beta/dataset_v1/acquisition.py` at D2-C commit `0bf5ac5d09f24275727661ade5b10ac56d2f7862` |
| Header `adapter_code_sha256` | `ec4a351578dbd7e776a2288a8bc12d81e125efb6c045a5c1da4fac141a4c0772` (`smart_beta/vendors/tiingo/client.py`; unchanged through `2684717`) |

## 2. Final manifest file hashes (state COMPLETE)

| File (under `manifest/b-c21b48190918f21f/`) | SHA-256 |
|---|---|
| `batch.json` | `0cc240a4ee813493e85424f75e3829c9df437938fbeac9b297fbff434a028214` |
| `status.json` (= summary `manifest_sha256`) | `595a612cc6daecd3c438696876952fcffbf917abb596d874b383c50f56b1fe3d` |
| `attempts.jsonl` (10,896 lines) | `197896599e9127eab75792bf2f2a547b72dcf53f109c27565519d9a91608d6a6` |

| Run log (under `production_v1/`) | SHA-256 |
|---|---|
| `run1_stderr.log` | `a498216a6a86e3c80a18d71f2644f5a96030b6c97e79692b046751c0652f2eb8` |
| `run1_summary.json` | empty file (`e3b0c442…b855`) |
| `run2_summary.json` | `1d0d04e18a1da9b0d243fd127487122300a42981a7842e1847a126b5b1ee3566` |
| `run2_stderr.log` | empty file (`e3b0c442…b855`) |

## 3. Implementation segments

The batch was **created under D2-C and resumed/completed under D2-D**. No
single implementation version produced the whole batch.

| Segment | Attempt lines | UTC span | Code | Ended |
|---|---|---|---|---|
| 1 | 1 – 3,920 | 00:06:06Z – 00:53:40Z | D2-C: `acquisition.py` SHA-256 `aa265cb8…`, commit `0bf5ac5` | `INTERRUPTED` by an unhandled `http.client.IncompleteRead` on FTNT |
| 2 | 3,921 – 10,896 | 03:07:37Z – 04:44:53Z | D2-D: `acquisition.py` SHA-256 `65f3ec73c18c6251cb481bc8d63c788965e3f46ec1e26509369861ccbcbcf809`, master `268471797c4345e92475406c0b2526cd44604972` | `COMPLETE` |

- A first segment-2 launch failed at argument parsing (flag order) **before
  any request**, so it made zero provider calls and left no state change.
- In segment 2, the first request was for FTNT. Every terminal record from
  segment 1 was skipped with zero calls. The 3,929 segment-1 terminal records
  and 3,919 raw files are byte-identical before and after segment 2 (verified
  by the planner against a pre-resume snapshot).

## 4. Outcome counts

| Terminal status | Count |
|---|---|
| OK | 10,861 |
| EMPTY | 32 |
| UNREQUESTABLE_IDENTIFIER | 10 |
| RETRY_EXHAUSTED / MALFORMED / ENTITLEMENT_ERROR / NONDETERMINISTIC | 0 / 0 / 0 / 0 |
| **Total = expected** | **10,903** |

**Requests**

| Item | Segment 1 | Segment 2 |
|---|---|---|
| Logged attempts | 3,920 | 6,976 |
| HTTP 200 | 3,919 | 6,974 |
| Handled transport failures | 1 (logged before D2-D, so its line has no `transport_cause`) | 2: one `URLError` (IMUX), one `TimeoutError` (INDB) |

- **Whole batch:** 10,896 logged attempts plus 1 unlogged attempt (§5), giving
  **10,897** authenticated requests that can be determined.
- **Never observed:** HTTP 429, 5xx, or the `IncompleteRead` /
  `BadStatusLine` / `LineTooLong` classes in segment 2.

**Raw data**
- 10,893 files: 10,861 OK and 32 EMPTY bodies. No file is unreferenced.
- 16,619,088 rows in OK files; observation dates run from 2015-01-02 to
  2026-08-31.
- About 618 MB gzipped.

**Integrity audit (all OK records):**
- the file exists and gzip decompresses;
- its SHA-256 matches the manifest;
- the row count and first and last dates match the manifest;
- dates are sorted with no duplicates;
- the requested range matches the frozen range;
- the expected set equals the terminal set;
- there are no duplicate terminal identities;
- contract, pool and batch bindings match.

Every check PASSED. The credential-exclusion check also PASSED.

## 5. KNOWN PRE-D2-D AUDIT GAP

One authenticated FTNT request in segment 1 raised
`http.client.IncompleteRead(28303 bytes read, 626111 more expected)`. The
D2-C implementation did not handle that exception class. The run crashed and
**no line was written to `attempts.jsonl`**.

- The only evidence is `production_v1/run1_stderr.log` (hash in §2).
- The attempt is **not fabricated and not backfilled**, and it must never be.
- No partial body was persisted.
- FTNT was re-acquired in segment 2 on its first attempt.
- This limitation affects audit completeness only. It does not affect any
  verified raw record.

## 6. Attempt-log schema compatibility

- Segment-1 failed-attempt lines carry explicit `null` values for
  `raw_sha256`, `row_count`, `first_date` and `last_date`, and have no
  `transport_cause` key.
- Segment-2 failed-attempt lines omit those four keys and carry
  `transport_cause`.
- **For failed-attempt semantics, a missing key equals null.** Old lines are
  never rewritten to normalize the schema.

## 7. Open findings carried forward (not resolved here)

- SECURITY-CLASSIFICATION / IDENTIFIER-SEMANTICS: **OPEN**. It blocks D6.
- Vendor identity continuity, delisting returns and volume semantics:
  **NOT CERTIFIED**.
