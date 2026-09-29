# D8 — Reserve development firewall and reserve ARTIFACT registration (Dataset v1.1) — FROZEN BEFORE IMPLEMENTATION

- **Status:** FROZEN BEFORE IMPLEMENTATION (user decisions of 2026-09-29).
  Implementation needs a separate launch authorization. The **production
  Knowledge-PIT append** needs its own further authorization (§10).
- **Authority, in precedence order** (on any conflict, stop and report):
  1. `docs/discovery_dataset_v1_contract.md` §§12, 14, 16 (gate 12), 20 —
     `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`;
  2. `docs/discovery_dataset_v1_1_d8_clarification.md` (2245cbb) —
     `9a3f218d67c7e7456b2a5c9439a2d1fffe202e5beca3299afb31439b919b24c0`;
  3. `worker_tasks/phase10/phase10-plan.md` §§5.1, 5.2, 5.4, 6.2, 6.5, 13.1 —
     `e02d5f7f08aca22dc94e6f6a694bd640e89036d577bfc82c4a365131e11b1b7a`;
  4. `docs/phase10_confirmatory_evidence_certification.md` —
     `c8f68029ecf6d3fd59781359094eb3a29aa4eadc6bb5b17db8933594ad4219ed`;
  5. `worker_tasks/dataset_v1/task-d3-normalizer.md` §§4–6 —
     `386ea2a133645b50f0aeeeeba923c5fe6c63b9e10f50eb527a565e0b43bfe75d`;
  6. `worker_tasks/dataset_v1/task-d4-identity.md` —
     `a0bcb92906bb0dfdddc7106578a2e36e1cce500d3edaa6189e5d3afa652ec16f`;
  7. `docs/discovery_dataset_v1_acquisition_record.md` §2 —
     `256fa30f1372b08221784bd723eb74aaa469a00e07a012fb569aeac91630a0cf`.
- **Owned files (new):**
  - `smart_beta/dataset_v1/reserve_firewall.py` (the accessor);
  - `smart_beta/dataset_v1/reserve_artifact.py` (footprint, payload,
    registration);
  - `tests/test_dataset_v1_reserve_firewall.py`;
  - `tests/test_dataset_v1_reserve_artifact.py`;
  - `tests/fixtures/dataset_v1/d8/**`, including `mutations.json`.
- **Must not modify** any existing file. In particular, no Phase-10 file and no
  D6/D7a/D7b file.
- **Provider or network calls: 0.**

**Phase-10 code used read-only, with these SHA-256 values pinned and failing
closed on mismatch:**

| File | SHA-256 |
|---|---|
| `smart_beta/science/knowledge.py` | `7a4ab759ab20d8acf945a1de3246653a572069696a2f2bf791d155c6b6932fec` |
| `smart_beta/science/footprint.py` | `779dfd1e691279d081f5bc935d61957734a901ac9783fc695fa168858fe26a88` |
| `smart_beta/science/contracts.py` | `8e8b2f9c4f133e34f09794fedc094c80198ecf64440c727e13209b43488dbd2d` |
| `smart_beta/vendors/tiingo/calendar_source.py` | `79bca35ddd46f87102e90f6ce0c2d51a8b0d16f1fe9caf56b7674b43fce8e23f` |

## 1. Bindings (exact; fail closed; never "latest")

| Object | Binding |
|---|---|
| D3 build | `n-f874d3ecb61374d4`, `normalized_hash f93093a8b8fa07521191d687560c2c26ab16630ebeed35d68aa12867def47fb3`, `build.json` `f11d9b05d5cbe2a736d991ea90d4b56f469f855f33b4168a4a4af14b87666450` |
| D3 development files (accessor) | `observations.parquet` `557001d0…6596e`, `obs_flags.parquet` `256ae777…a053`, `ca_events.parquet` `afd4a298…b109` (full values as in `build.json`) |
| D3 reserve files (artifact) | the three hashes in clarification §3 |
| D4 identity | `i-a21f1e46621dee94`, `identity_hash 1023dbca39fd061d52fd26eeb6302f4a8dec467a8ea996b1903da801306ae730`, `identity.json` `b8ed3d23979f8cbb9f320fae96a8cea968b16332fd1000fa1cdedca15df43aa4`, `identity_assignment.parquet` `fee4614009095773f75fb832343a5fa9b87b9de1cf363125567222b08fad3f8b` |
| Smoke batch manifest `b-10cd8b2e2ef11a98` | `attempts.jsonl` `1b54b93df22c7dd459d19c8455251a28a86898adc81045e1f161c8e9e4a04549`, `batch.json` `42c3d7c3e132a9116d50b083f493b22e8dc9b6ecd142f4a5b9b356b8117fab18`, `status.json` `4f7b59cf0d91b08c46432cf7c984c525dfeec92712872c7cc6fad361f8b87ca3` |
| Production batch manifest `b-c21b48190918f21f` | `attempts.jsonl` `197896599e9127eab75792bf2f2a547b72dcf53f109c27565519d9a91608d6a6`, `batch.json` `0cc240a4ee813493e85424f75e3829c9df437938fbeac9b297fbff434a028214`, `status.json` `595a612cc6daecd3c438696876952fcffbf917abb596d874b383c50f56b1fe3d` (acquisition record §2) |
| K log (production) | `<root>/../knowledge_pit/knowledge.jsonl`, i.e. `data_store/knowledge_pit/knowledge.jsonl`, compared by resolved real path |

- The set of manifest batches must be exactly these two directories. Any other
  batch directory fails closed with `D8HoldError`.
- Bindings are a frozen value. Production constants are the default; tests
  inject synthetic bindings.

## 2. Development accessor (`reserve_firewall.py`)

```python
DevelopmentAccessor(root, *, bindings=None)
DevelopmentAccessor.read(component, *, start, end, columns, tickers=None) -> pd.DataFrame
```

**Arguments:**
- `component` ∈ {`observations`, `obs_flags`, `ca_events`}. Their date columns
  are `date`, `date` and `effective_date`.
- `start` and `end` accept a `datetime.date` or a strict `YYYY-MM-DD` string
  only. A `datetime`, a tz-aware or naive `Timestamp`, or any other type is
  rejected.
- `columns` must be a subset of the frozen D3 schema of that component; an
  unknown column is rejected.
- `tickers`, if given, is a non-empty list of strings.

**Checks, in order, all before any file is opened:**
1. Parse and validate the arguments; otherwise `AccessorInputError`.
2. `start ≤ end`; otherwise `AccessorInputError` (an empty range).
3. `end < 2023-07-03`; otherwise **`ReserveFirewallError`**. This covers an
   exact boundary request, a start inside the reserve, an end inside the
   reserve, and a range crossing the boundary.
4. Resolve the path only from the bindings:
   `normalized/n-f874d3ecb61374d4/<component>.parquet`.
   - A path component `reserve_sealed`, `raw` or `dsc_evidence`, or `..`,
     raises `ReserveFirewallError`.
   - The resolved real path must lie under the resolved
     `<root>/normalized/n-f874d3ecb61374d4/`. A symlink escaping it raises
     `ReserveFirewallError`.
   - The API has no path, artifact-id or "metadata" parameter.
5. Verify the file's SHA-256 against the binding.
6. Read with `pyarrow.parquet.read_table(path, columns=..., filters=[(date_col,
   ">=", start), (date_col, "<=", end)] + ticker filter)`. Never read and then
   filter.
7. Post-load assertion: every returned date lies in `[start, end]` and
   < 2023-07-03. Otherwise `ReserveFirewallError`, and nothing is returned.
8. Return the rows sorted by (ticker or `stock_id`, date, `raw_row_index` or
   `action_type`), stable.

There is no reserve metadata or schema pass-through, and no reserve file is
ever opened.

## 3. Footprint (`reserve_artifact.py`) — clarification §§4–5

1. **Verify** the bindings, the pinned Phase-10 code hashes, and each reserve
   file's SHA-256, by streaming bytes only. Any mismatch fails closed.
2. **Schema:** read the reserve `observations` parquet **schema metadata**. It
   must equal the frozen 16 names `ticker, date, open, high, low, close,
   adjOpen, adjHigh, adjLow, adjClose, divCash, splitFactor, volume, adjVolume,
   raw_row_index, raw_sha256`. Otherwise `D8HoldError`.
3. **Projected key read,** with `columns=` only:
   - `observations[ticker, date]`;
   - `obs_flags[ticker, date]`;
   - `ca_events[stock_id, effective_date]`.
4. **Domain check:** the `obs_flags` keys and the `ca_events` keys (as
   ticker/date) must each be a subset of the `observations` (ticker, date)
   keys. Otherwise `D8HoldError`.
5. **Subject resolution** (D4, clarification §4):
   - Map key K = the unique `INTERVAL_ASSIGNED` D4 row with the row's ticker
     and `listed_start ≤ date ≤ listed_end`, where the key appears once in D4.
   - The subject is K when a key is found. Otherwise it is the raw ticker,
     which is unmapped.
   - `SECURITY_MAP` = {K: `"SEC:US:" + K`} over the eligible D4 keys, hashed
     with `contracts.content_hash`.
6. **Frame:** a `_SchemaBackedKeyFrame`.
   - `.columns` = the schema names minus the provenance columns, i.e. `subject`,
     `date` and the 14 economic columns.
   - `frame[["subject", "date"]]` returns the projected key rows.
   - Any request for a non-key column raises `D8FirewallError`, so economic
     values are not materializable.
7. `footprint_from_panel(frame, {"subject": "subject", "date": "date"},
   VARIABLE_MAP, SECURITY_MAP, {}, build_nyse_calendar(date(2015,1,2),
   date(2027,12,31)))`, with the default rules version. `VARIABLE_MAP` is
   exactly clarification §4, each entry `{"derived_variable": <name>}`, and is
   hashed with `content_hash`.
8. **The result is used as returned.** An undeterminable footprint keeps
   `blocks == ()` and the exact `unresolved` list Phase 10 produces. There is
   no filtering, no subset footprint and no rewritten reasons.
9. **Counts** by reason class (`unmapped_subject`, `unmapped_column`,
   `invalid_date`, other) are derived from `footprint.unresolved` for
   reporting.
   - Production counts are never hard-coded.
   - A determinable result is also accepted, if the data really are fully
     resolvable.

## 4. `available_from` — the deterministic algorithm

- Take the tickers present in the projected reserve `observations`.
- Across both bound manifests' `attempts.jsonl`, keep every record that meets
  all of these:
  - `http_status == 200` and `error_class is None`;
  - `row_count > 0`;
  - `ticker` is one of those tickers;
  - `last_date[:10] >= "2023-07-03"`, meaning the body contains reserve-period
    rows.
- `available_from` = the minimum of `retrieved_at_utc[:10]` over those
  records, as an ISO date.
- If no record qualifies, raise `D8HoldError`.
- The contributing batch and attempt are recorded in the payload.

## 5. ARTIFACT record

Built with `KnowledgeRecord` through `KnowledgeLog.append`:
- `kind = ARTIFACT`, `channel = SYSTEM`, `program_id = None`;
- all four ref lists empty, `event_time = None`;
- `footprint` = the footprint body;
- `recorded_at` = the clock of the log.

**Payload:**

| Key | Value |
|---|---|
| `packaging_hash` | clarification §3 |
| `sealed` | `true` |
| `available_from` | §4 |
| `source_label` | `discovery_dataset_v1:reserve:n-f874d3ecb61374d4` |
| `d8_sealed_semantics` | the verbatim prospective meaning from clarification §3, plus the sentence "sealed=true does not assert historical non-exposure" |
| `d8_package` | D3 build id, `normalized_hash`, and the three reserve file hashes |
| `d8_footprint_read` | purpose, projected columns per component, the D3 build id, the SHA-256 of `reserve_artifact.py`, the spec and clarification SHA-256 (time = envelope `recorded_at`) |
| `d8_maps` | `security_map_hash`, `variable_map_hash`, `market_series_map_hash` (of `{}`), the calendar hash (Phase-10 session-tuple rule), the derivation rules version, the D4 `identity_hash`, and the unresolved counts by class |
| `d8_available_from_evidence` | contributing batch id, ticker, attempt, `retrieved_at_utc`; both manifest hash sets |
| `d8_historical_disclosure` | the clarification §7 table, verbatim, plus "not exhaustive" |
| `d8_evidence_role` | `"UNKNOWN_EXPOSURE (not upgraded by registration; absence of ACCESS is not proof of no access)"` |

- The payload is deterministic. Only the envelope's `recorded_at` and
  `record_hash` vary.

## 6. Registration modes and append semantics

```python
register_reserve_artifact(root, log_path, *, mode, authorization=None, bindings=None) -> RegistrationResult
```

**Modes:**
- **`TEST`:** refuses when the resolved `log_path` equals the production path
  (symlink- and relative-path-safe).
- **`DRY_RUN`:** copies the production log, if one exists, to a tmp path and
  appends there. The production file stays byte-identical.
- **`PRODUCTION`:** requires `log_path` to equal the production path and
  `authorization == "D8-PRODUCTION-REGISTRATION-AUTHORIZED"`. Otherwise it is
  refused.

**Under `KnowledgeLog.locked(blocking=False)`** (a lock failure surfaces as an
error):
1. Read the log with `read_records`. A `TruncatedTail` or an integrity error
   is raised and never repaired.
2. Look for an existing `ARTIFACT` with the same `source_label`.
   - Payload and footprint identical to the new build (ignoring `recorded_at`
     and `record_hash`): **no-op**, returning the existing record.
   - Otherwise (a different `packaging_hash` or any other difference):
     `D8ConflictError`, with no append.
3. If there is no such artifact and the log has at least one record:
   `D8HoldError` (clarification §2), with no append.
4. If the log is empty or absent: append once. The result must have `seq == 0`
   and `prev_hash` = 64 zeros, and `read_records` must verify afterwards.

D8 never writes `ACCESS` or `EXPOSURE_DECLARATION` records, never rewrites or
truncates a log, and never touches D3/D4/D5 files or anything under
`reserve_sealed/`.

**CLI:** `python -m smart_beta.dataset_v1.reserve_artifact --root <root> --mode {dry-run,production} [--authorization TOKEN]`.
It prints the mode, `record_hash`, `seq`, `determinable`, the unresolved
counts, `packaging_hash` and `available_from`.

## 7. Required tests (synthetic fixtures; socket guard; credential sentinel)

**Accessor**
1. A range wholly before the boundary returns rows.
2. `end = 2023-06-30` is accepted.
3. `end = 2023-07-03` is rejected.
4. `start ≥ 2023-07-03` is rejected.
5. A range crossing the boundary is rejected.
6. An end inside the reserve is rejected.
7. `start > end` is rejected.
8. `start == end` is a valid single day.
9. A malformed string is rejected.
10. `datetime` and tz-aware `Timestamp` values are rejected.
11. A naive `Timestamp` and other types are rejected.
12. No read happens before rejection (a spy on `read_table` counts 0 on every
    rejection).
13. The read uses the date filters (spy); read-then-filter is absent.
14. A poisoned development file containing a reserve-dated row raises at the
    post-load assertion and returns nothing.
15. Each component is enforced, including `ca_events` via `effective_date`.
16. An unknown column is rejected; there is no metadata method.
17. Tampered bindings containing `reserve_sealed`, `raw`, `dsc_evidence` or
    `..` are refused.
18. A symlink escaping the development directory is refused.
19. A binding hash mismatch fails closed.
20. The ticker filter is correct.
21. An audit hook shows no path under `reserve_sealed` is ever opened by the
    accessor.
22. Output order is deterministic, and permuted fixtures give identical
    output.
23. The column projection is honored.
24. D6, D7a and D7b modules do not import `reserve_firewall`.

**Footprint and payload**

25. The VariableMap is exact (14 columns; the kinds per clarification §4), and
    its hash equals `content_hash(VARIABLE_MAP)`.
26. The provenance columns are absent from `frame.columns`.
27. An extra economic column in the schema: `unmapped_column` → undeterminable
    (with a fixture schema) and the HOLD rule fires in production mode.
28. A schema differing from the frozen 16 names raises `D8HoldError`.
29. `_SchemaBackedKeyFrame` refuses a non-key column request.
30. The projected reads request only the allowed key columns (spy on `columns=`).
31. The SecurityMap holds only unique `INTERVAL_ASSIGNED` keys; hash checked.
32. A row maps only inside its key's assigned interval.
33. A row outside every interval is unresolved.
34. An `IDENTITY_AMBIGUOUS` ticker's rows are unresolved.
35. `HISTORY_UNREACHABLE`, `NO_OBSERVATIONS` and `UNREQUESTABLE` rows are
    unresolved.
36. A duplicated D4 key never maps.
37. `SEC:US:<ticker>` never appears (no ticker fallback).
38. One unresolved row makes the whole footprint undeterminable: `blocks == ()`,
    `determinable is False`, `unresolved` non-empty.
39. An all-resolvable fixture gives a determinable footprint with the expected
    PRICE_LEVEL, TRADING_ACTIVITY and PRICE_CHANGE blocks.
40. With 99% of rows mapped and 1% unresolved, the result is still
    undeterminable; a subset footprint is never substituted.
41. The calendar is the bound NYSE calendar; its absence gives undeterminable.
42. The MarketSeriesMap is empty and its hash is recorded.
43. The derivation rules version is the Phase-10 default.
44. An `obs_flags` key outside the observations keys raises `D8HoldError`; flags
    on existing rows add no observation.
45. A `ca_events` key outside the observations keys raises `D8HoldError`; valid
    events add no block beyond the mapped PRICE_CHANGE.
46. `packaging_hash` equals `content_hash` of the three bound path→hash
    entries.
47. A tampered reserve file (hash mismatch) fails closed.
48. The payload's required keys and types pass Phase-10 validation, with
    `sealed is True` and the exact `source_label`.
49. `available_from` is the minimum qualifying `retrieved_at_utc` date, and an
    earlier smoke-batch record wins.
50. Non-200, `error_class`, `row_count 0`, `last_date < 2023-07-03` and
    non-artifact-ticker records are excluded.
51. No qualifying record raises `D8HoldError`.
52. A manifest hash mismatch, or an unexpected third batch directory, fails
    closed.
53. `available_from` is taken from the evidence, not from the boundary, build,
    registration or current date (fixture with distinct dates).
54. The historical-disclosure payload matches clarification §7 verbatim and
    includes "not exhaustive".
55. `d8_sealed_semantics` includes the non-exposure disclaimer.
56. `d8_evidence_role` says UNKNOWN_EXPOSURE, and no `NOT_EXPOSED` string
    appears anywhere.
57. No `ACCESS` or `EXPOSURE_DECLARATION` record is appended.
58. An undeterminable footprint is accepted by `KnowledgeLog.append`, and
    `read_records` verifies it.
59. The envelope has `channel SYSTEM`, `program_id None`, empty refs and
    `event_time None`.
60. `overlap(fp, any)` returns UNDETERMINABLE for the undeterminable footprint
    (no role upgrade).
61. The footprint-read provenance is complete (purpose, columns, code and spec
    hashes).
62. The unresolved reasons are exactly as Phase 10 returned them.

**Registration and log**

63. An empty or absent tmp log gives `seq 0` with `prev_hash` of zeros.
64. A log with an unrelated record raises `D8HoldError` with no append (bytes
    unchanged).
65. A rerun with an identical artifact is a no-op returning the existing
    record; file bytes are unchanged.
66. A different `packaging_hash` with the same `source_label` raises
    `D8ConflictError` with no append.
67. The same `packaging_hash` with a different payload or footprint raises
    `D8ConflictError`.
68. A `TruncatedTail` log raises and is not repaired (bytes unchanged).
69. The log is append-only: the prior bytes are an exact prefix after append.
70. A held lock surfaces an error with no append.
71. `TEST` mode refuses the production path, including via a symlink and a
    relative path.
72. `PRODUCTION` mode needs the exact path and token; a wrong token is refused.
73. `DRY_RUN` leaves the production log bytes (or its absence) unchanged.
74. `DRY_RUN` and `PRODUCTION`-mode (on tmp) payloads and footprints are
    identical.
75. The payload and footprint are identical across runs, `TZ`/`LC_ALL`
    changes and permuted fixtures.
76. The reserve economic columns are never materialized (spy on every
    `read_table` call).
77. No write happens under `reserve_sealed/`, `normalized/`, `derived/` or
    `manifest/` (tree hashes unchanged).
78. The CLI dry run prints the required fields and writes only to tmp.
79. No network (socket guard) and no credential read.
80. The pinned Phase-10 code hash mismatch fails closed.

## 8. Required mutations (`tests/fixtures/dataset_v1/d8/mutations.json`; planner re-executes every one)

M1 `end >= boundary` becomes `end > boundary` · M2 allow exactly 2023-07-03 · M3 read then filter (drop the pyarrow filters) · M4 ignore crossing ranges (check `start` only) · M5 accept a `reserve_sealed` path · M6 skip the symlink real-path check · M7 accept datetime or tz values · M8 skip the post-load assertion · M9 allow any column · M10 skip the development file hash check · M11 weaken `packaging_hash` (hash two files, or the wrong canonical form) · M12 skip reserve file hash verification · M13 `sealed = false` · M14 `available_from` = registration date · M15 `available_from` = 2023-07-03 · M16 `available_from` = max instead of min · M17 drop the `last_date` qualifying filter · M18 omit the footprint from the envelope · M19 build the footprint from resolvable rows only · M20 drop unresolved rows · M21 ticker fallback `SEC:US:<ticker>` · M22 force-map IDENTITY_AMBIGUOUS keys · M23 map rows outside the interval · M24 map duplicated keys · M25 include provenance columns as variables (or map them to a fake kind) · M26 map an unknown column silently · M27 wrong kind for divCash/splitFactor · M28 wrong kind for volume · M29 drop open/high/low from PRICE_LEVEL · M30 skip the obs_flags domain check · M31 skip the ca_events domain check · M32 skip the schema check · M33 allow the frame to materialize value columns · M34 project extra reserve columns · M35 empty calendar · M36 duplicate ARTIFACT append on rerun · M37 overwrite or truncate the log · M38 treat a conflicting `packaging_hash` as a no-op · M39 repair a TruncatedTail · M40 append when the log already has unrelated records · M41 force `seq = 0` via direct write · M42 append an ACCESS backfill · M43 append a NOT_EXPOSED declaration · M44 `d8_evidence_role` upgraded (claims a confirmation role) · M45 drop the non-exposure disclaimer · M46 drop a historical-disclosure row · M47 `TEST` mode accepts the production path · M48 `PRODUCTION` without the token · M49 `DRY_RUN` writes to the production log · M50 nondeterministic payload (timestamp or unordered set in the payload) · M51 skip the manifest hash check · M52 accept a third batch · M53 skip the Phase-10 code hash pin · M54 rewrite the unresolved reasons · M55 accessor accepts start > end · M56 accessor ticker filter ignored

**56 mutations.** Any surviving non-equivalent mutation means STOP BEFORE
MERGE.

## 9. Implementation barrier (planner; no production append)

- **Review,** plus 80 tests.
- **Mutations:** all 56 in the manifest, **run by the planner**, plus
  planner-independent conditional forms and a no-op control.
- **Full suite:** credential-scrubbed.
- **Production metadata dry run** (`DRY_RUN` against real data; tmp log only):
  - bindings and schema verified;
  - domain checks pass;
  - footprint computed, with the determinable flag and the unresolved counts
    by class reported;
  - `packaging_hash` and `available_from` reported, with the contributing
    evidence;
  - an audit hook shows only the allowed files were opened, and the
    `read_table` spy shows only the allowed key columns were materialized;
  - the production log path is unchanged or absent.
- **Determinism:** a second dry run under `TZ=Asia/Tokyo LC_ALL=C` gives an
  identical payload and footprint.

## 10. Production append barrier (separate authorization only)

1. Verify that `data_store/knowledge_pit/knowledge.jsonl` is absent or holds
   no records. Otherwise **D8 IMPLEMENTATION HOLD**.
2. Run `PRODUCTION` mode once. Verify:
   - `seq 0` and `prev_hash` of zeros;
   - `read_records` verifies;
   - the payload equals the dry-run payload.
3. Rerun: a no-op returning the same `record_hash`.
4. Report the `record_hash`, the footprint `determinable` flag and unresolved
   counts, `packaging_hash` and `available_from`.

## 11. Certification boundary and nonclaims

**Proposed certification:**
> D8 certifies a prospective Dataset-v1 development-access firewall that
> rejects, before any row is materialized, every request touching a session
> ≥ 2023-07-03. It also certifies the deterministic registration of the
> already-possessed D3 reserve package as a sealed Knowledge-PIT ARTIFACT (the
> genesis record of the research-environment log), with truthful disclosure of
> known pre-registration machine and structural interactions, and without
> inferring historical non-exposure.
>
> The artifact's Phase-10 footprint is UNDETERMINABLE, because some reserve
> observations cannot be mapped through the frozen D4 identity authority. This
> is an accepted scientific-evidence limitation, neither hidden nor repaired by
> filtering the reserve. This historical artifact is therefore not currently
> usable as determinable Phase-10 confirmation evidence, and its exposure
> status is UNKNOWN_EXPOSURE.
>
> D8 certifies the firewall and truthful registration and provenance, not
> historical confirmation eligibility.

(If the real data turn out to be fully resolvable, the second paragraph is
restated with the computed result; the claim is never upgraded beyond what
Phase 10 returns.)

**Not claimed:**
- historical non-exposure, or the absence of machine or human reads;
- confirmation eligibility or `CONFIRMATION_HISTORICAL_RECORDED`;
- G1/G2/G3 quality or hypothesis independence;
- reserve outcome integrity beyond the bound package identity;
- Campaign-1 readiness by itself;
- alpha, predictive validity or production readiness.

The Dataset-v1 identity, delisting (`DELISTING_RETURN_NOT_CERTIFIED`) and
survivorship nonclaims remain.

## Report

- files changed;
- test and mutation counts, with test-to-item and mutation-to-test maps;
- the commit SHA;
- the line `sealed files modified: NONE`.
