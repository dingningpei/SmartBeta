# D7a — Development observed-return evidence (Dataset v1.1) — FROZEN BEFORE IMPLEMENTATION

- **Status:** FROZEN BEFORE IMPLEMENTATION (user decisions of 2026-09-28).
  Implementation requires a separate launch authorization.
- **Scope name:** this file specifies **D7a only**. D7b (the TrustedInput
  bridge and evaluation-time T0/TS) needs its own frozen spec. P7-B stays the
  owner of forward-return compounding.
- **Authority, in precedence order** (on any conflict, stop and report):
  1. `docs/discovery_dataset_v1_contract.md` §§7, 8, 9, 10, 11, 12, 14 —
     `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`;
  2. `docs/discovery_dataset_v1_1_d7_clarification.md` (8efbd96) —
     `bc8e6920d31dd429beb62030123a721a7293558ba57fe8e3d4d6cd6368a96946`;
  3. `docs/discovery_dataset_v1_1_d6_clarification.md` (5238303) —
     `e3cb52a892f533c41c5c0d1d927cd54d7e200ef2a28e56e76efdf01227aa17ea`
     (H2 existence, H3 usable rows);
  4. `docs/discovery_dataset_v1_1_d6_duplicate_key_clarification.md`
     (c9de712) — `7d34c6037040f8998966221fd1a68db1e9f2f9e74004d746081464035ebb22a0`;
  5. `worker_tasks/phase7/phase7-plan.md` §8.1 —
     `8b2009533ee00e8a04e0c65b6853711f01387592e42f60c0b97170d23183a13e`;
  6. `worker_tasks/dataset_v1/task-d6-universe.md` §5 (the usable-row
     definition, reused verbatim) —
     `25927941d9ec4860edbd3a9e5511d6ef4f27d5b5189a183c6bca8e972653fe18`.
- **Owned files (new):** `smart_beta/dataset_v1/returns.py`,
  `tests/test_dataset_v1_returns.py`, `tests/fixtures/dataset_v1/d7/**`.
- **Read-only imports allowed:**
  - `smart_beta.vendors.tiingo.calendar_source.build_nyse_calendar`;
  - `smart_beta.pit.corporate_actions.compute_adjusted_returns` and the
    schema constants it validates against;
  - `smart_beta.config.settings.DEFAULT_SETTINGS` (only to derive an explicit
    settings value, §5.3);
  - tests only: `smart_beta.evaluation.forward_returns.align_forward_returns`
    (test 44).
  - D7a must not import `smart_beta.dataset_v1.universe` or re-derive any D6
    decision.
- **Must not modify** any existing file or upstream artifact.
- **Provider or network calls: 0.**

## 1. Scope

- D7a stores **factual observed evidence** for the 90 development
  formations:
  - daily total returns from the contract §8 path;
  - per-session status;
  - per-(formation, member, h) path evidence for every h = 1…63;
  - retrospective D4 terminal metadata;
  - calendar endpoint and purge metadata;
  - provenance.
- **D7a does not store:**
  - a compounded forward return (P7-B owns compounding);
  - any T0 or TS value, τ or synthetic proceeds;
  - any factor value, factor rank, leg, quantile or predicted-high/low
    assignment;
  - any reserve-period market observation, or any exact `listed_end` after
    2023-06-30.
- **D7a does not perform:** universe construction or reranking, evaluation,
  portfolios, metrics or reserve work.

## 2. Upstream bindings (exact; fail closed on any mismatch; never "latest")

| Stage | Artifact | Top-level hash (from its manifest) |
|---|---|---|
| D3 | `n-f874d3ecb61374d4` | `normalized_hash f93093a8b8fa07521191d687560c2c26ab16630ebeed35d68aa12867def47fb3` |
| D4 | `i-a21f1e46621dee94` | `identity_hash 1023dbca39fd061d52fd26eeb6302f4a8dec467a8ea996b1903da801306ae730` |
| D5 | `q-5d1028655f8c82e1` | `qc_hash 3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3` |
| D6 | `u-6150bc6ac2866b7f` | `universe_hash 6e8539b7acb4ed4559846234b0b26e6f1d4e6d34f7e0a5d1e2290134d563ed87` |

D-SC is **not** bound. D6 already owns admission.

**Allowlist.** These are the only data files D7a may open, relative to
`<root>`. Each must match its manifest's `files_sha256` entry:

| Relative path | SHA-256 |
|---|---|
| `normalized/n-f874d3ecb61374d4/observations.parquet` (development partition) | `557001d0e9c617befd3c036683f171094e86e19badb760098f40c08f3f56596e` |
| `normalized/n-f874d3ecb61374d4/ca_events.parquet` (development partition) | `afd4a2986c934d84c2b23eaf68f23ee18746e7c929c6b8625b6c9911e4a2b109` |
| `derived/identity/i-a21f1e46621dee94/identity_assignment.parquet` | `fee4614009095773f75fb832343a5fa9b87b9de1cf363125567222b08fad3f8b` |
| `derived/qc/q-5d1028655f8c82e1/day_flags.parquet` (development partition) | `f4010ebeabf5cfae2684f3658a5f19f922f15e2c8898410049f2148440cf7891` |
| `derived/universe/u-6150bc6ac2866b7f/universe_development.parquet` | `f933a67ff8c83ad9f6c7937a76136d93419cedb0ec79cec617df8205b3d38d8b` |
| `derived/universe/u-6150bc6ac2866b7f/formations.parquet` | `dcd547fe6f6e8c7e70c298a9821ad7d14ee801c959a7aeacaa5aba5803cc35a1` |

**Manifests,** read only for binding (file SHA-256 in parentheses):
- `normalized/n-f874d3ecb61374d4/build.json`
  (`f11d9b05d5cbe2a736d991ea90d4b56f469f855f33b4168a4a4af14b87666450`);
- `derived/identity/i-a21f1e46621dee94/identity.json`
  (`b8ed3d23979f8cbb9f320fae96a8cea968b16332fd1000fa1cdedca15df43aa4`);
- `derived/qc/q-5d1028655f8c82e1/qc_report.json`
  (`16c582900b77a328ffaee8fdb78b8bc4b7920a0b48ebbc98f7a2e7e62c769ae1`);
- `derived/universe/u-6150bc6ac2866b7f/universe.json`
  (`8e5e4ecd1729c2bbf75b1ac1bdd5d497f9f2a360061edb407fc323feb433d737`).

**Other bindings:**
- The six normative documents in the authority list are hash-bound. A
  mismatch fails closed.
- Bindings are a frozen `Bindings` value. Production constants are the
  default; tests inject synthetic bindings. The allowlist is derived from the
  bindings.
- No directory listing is ever used to find an artifact. A newer or other
  `u-*`, `q-*`, `i-*` or `n-*` directory is ignored.

## 3. Reserve firewall (mechanical; not discipline)

- **Guarded resolver.** Every file open goes through one resolver, which
  raises `ReserveFirewallError` (typed, fail closed) if:
  - any path component equals `reserve_sealed`, `raw` or `dsc_evidence`;
  - the path is not in the allowlist.
- **Forbidden reads,** including value-free ones:
  - D3 `source_summary.parquet` and `obs_flags.parquet`;
  - D4 `interval_mismatch.parquet`;
  - D5 `security_qc.parquet`;
  - D6 `audit_development.parquet`;
  - every D-SC file and every other file.
- **Post-load assertions** (`ReserveFirewallError`):
  - every loaded observation, day-flag and corporate-action date is
    < 2023-07-03;
  - every `universe_development` formation date is ≤ 2023-06-30.
- **Output assertions,** before any write:
  - no `daily_returns.date`, `labels.observed_segment_end`,
    `labels.first_invalid_session` or `members.listed_end_dev` value is
    ≥ 2023-07-03;
  - `horizons.endpoint` is the only column that may hold a date
    ≥ 2023-07-03. It is calendar metadata, and those rows carry
    `crosses_reserve = true`.
- A failed run writes no artifact.

## 4. Calendar, formations and endpoints

- **Calendar:** `build_nyse_calendar(2015-01-02, 2027-12-31)`. `S` is its
  ordered session list.
- **Formations:** the distinct `formation_date` values of the bound
  `universe_development.parquet`.
  - Assert they equal the development rows of the bound `formations.parquet`
    (`partition == DEVELOPMENT`): **90** dates, the first 2016-01-29 and the
    last 2023-06-30.
  - D7a never regenerates the schedule on its own authority.
- **endpoint(f, h),** for h = 1…63: the h-th element of `S` strictly after f.
- **crosses_reserve(f, h):** `endpoint(f, h) ≥ 2023-07-03`. Since realized
  sessions are f+1 … endpoint, this is exactly the case where the realized
  interval contains a reserve session.
- **Evidence end:** `E_f = min(endpoint(f, 63), 2023-06-30)`.

## 5. Daily-return evidence

### 5.1 Population (B4)

- **Members** = every row of `universe_development.parquet` with
  `top_1000 == true`. `formation_date`, `security_key`, `rank`, `top_200`,
  `top_500` and `top_1000` are copied verbatim.
- Assert that, for every formation, the member count equals
  `min(1000, n_eligible)` from `formations.parquet`, and ranks are 1…count.
- **Identity:** each member key must match **exactly one** D4
  `identity_assignment` row, and that row must have
  `state == INTERVAL_ASSIGNED`. Otherwise raise a typed
  `D7ConsistencyError`.
  - D4 may repeat inadmissible keys (duplicate-key clarification); none may
    be a member.
  - `ticker`, `listed_start` and `listed_end` come from that row.
- **Existence:** assert `listed_end ≥ f` for every member (D6 H2). If
  `listed_end < f`, raise `D7ConsistencyError`.
- **No survivor filtering:** members are never joined, filtered or
  intersected with any later observation, later symbol list or outcome.

### 5.2 Rows and usable rows (reused from D6 §5)

- The rows of member key K are the D3 development observations whose
  `ticker` equals K's ticker. They are joined to D5 development `day_flags` on
  (`ticker`, `date`, `raw_row_index`).
- A row is **unusable** if it carries a D5 day flag with:
  - disposition `CORRUPT` (OHLC_INCONSISTENT, NONPOSITIVE_PRICE, MALFORMED_CA,
    DUPLICATE_DATE, OFF_CALENDAR_ROW);
  - class `INTERVAL_MISMATCH`; or
  - class `VENDOR_NULL`.
- A **usable row** is a D3 row on a trusted session that is not unusable, with
  raw `close > 0`. CORP_ACTION_SUSPECT does **not** make a row unusable.
- A **usable session** for K has exactly one usable row. If a session has more
  than one usable row, raise `D7ConsistencyError`. Duplicate-date rows are
  CORRUPT, so this should never happen.
- Off-calendar rows are never sessions and never serve as a previous session.
- Volume is never read for returns, so a legitimate zero-volume row is
  usable.
- `adj*` fields are never read.

### 5.3 Session status and the §8 primitive

**Evidence sessions of K:** the union, over the formations f at which K is a
member, of the sessions s in `S` with f < s ≤ E_f.

**Status.** For each evidence session s, with p = the previous element of `S`,
the status is the first matching row:

| # | Condition | `session_status` |
|---|---|---|
| 1 | `listed_end ≤ 2023-06-30` and s > `listed_end` | `POST_TERMINAL` |
| 2 | K has no D3 row dated s | `NO_ROW` |
| 3 | rows dated s exist, but none is usable | `ROW_UNUSABLE` |
| 4 | K has no D3 row dated p | `PREV_NO_ROW` |
| 5 | rows dated p exist, but none is usable | `PREV_ROW_UNUSABLE` |
| — | otherwise | `VALID` |

**Raw return (VALID only):** `raw_ret(s) = float64(close_s) / float64(close_p) − 1`.
- There is no fill, no carry-forward and no substitute previous close.
- p is always the previous trusted session, never "the last available row".
- r(f+1) therefore needs a usable close at f. That need comes from the
  primitive itself; there is no separate requirement.

**Corporate actions** (contract §8; the Phase-4B mapper semantics):
- **Preconditions,** checked for each member key K over the events of its
  ticker with `listed_start(K) ≤ effective_date ≤ min(listed_end(K), 2023-06-30)`.
  Any violation raises `D7ConsistencyError`:
  - `effective_date` is a trusted session. Otherwise it is an
    **unmatched corporate action** and fails closed.
  - `is_superseded == false`.
  - `knowledge_date == effective_date`.
  - `adjustment_factor` is finite and > 0.
- **Call:** `compute_adjusted_returns(raw_panel, ca_panel, as_of=2023-06-30,
  calendar=None, settings=replace(DEFAULT_SETTINGS, pit_availability_buffer_days=0))`.
  - `raw_panel` holds the VALID sessions only, with `stock_id = security_key`.
  - `ca_panel` holds the events of K's ticker, relabelled `stock_id = security_key`.
  - Matching is on **exact dates**. An event dated s applies only to the
    return dated s.
- **Adjusted return:** `daily_total_return(s)` = the resulting `adj_ret`, i.e.
  `(1 + raw_ret) · Π(adjustment_factor of events dated s) − 1`.
- **Guard:** every stored `daily_total_return` must be finite and > −1.
  Otherwise raise `D7ConsistencyError`. A valid primitive cannot produce
  exactly −100%, because both closes are > 0 and every factor is > 0.
- **Event count:** `ca_event_count(s)` = the number of events dated s,
  recorded for every evidence session.
- **Suspect flag:** `corp_action_suspect(s)` is true iff the usable row at s
  carries D5 class CORP_ACTION_SUSPECT. It is informational only.

## 6. Per-label path evidence (every h = 1…63)

For every member (f, K) and every h:
- e = endpoint(f, h);
- the h sessions `s_1 … s_h` of `S` strictly after f.

**If `crosses_reserve(f, h)`:** `label_state = PURGED_RESERVE_CROSSING`, and
every other evidence field is null. Nothing after 2023-06-30 is read or
derived.

**Otherwise:**
- `terminal_affected = (listed_end ≤ 2023-06-30) and (f ≤ listed_end ≤ e)`.
  This is clarification §6, including `listed_end == f`. A listing that
  continues beyond development is never affected for a non-crossing label.
- **Observed segment** = `[s_i : s_i ≤ listed_end]` if terminal-affected,
  otherwise all of `s_1 … s_h`.
  - `n_observed_sessions` = its length.
  - `n_post_terminal_sessions = h − n_observed_sessions`.
  - Those sessions are `POST_TERMINAL`, never missing.
- `n_invalid_sessions` = the segment sessions whose status is not VALID.
  `first_invalid_session` and `first_invalid_status` belong to the first
  such session; both are null if there is none.
- `observed_path_complete = (n_invalid_sessions == 0)`. It is vacuously true
  for an empty segment.
- `observed_segment_end` = the last segment session, or null if the segment
  is empty.
- **`label_state`:**
  - `EMPTY_OBSERVED_PATH_IDENTITY` if terminal-affected and
    `n_observed_sessions == 0`. The empty-product identity R_obs = 0 applies.
    This is **not** an observed zero return, and no daily return row is
    synthesized for it.
  - otherwise `COMPLETE` if `observed_path_complete`, else `INCOMPLETE`.
- **Terminal status comes only from `listed_end`:** never from row cessation,
  the last row, a gap or any session after e. A missing row is never
  converted into a terminal event, and a terminal event never erases a
  pre-terminal invalid session.

**Temporal invariant (frozen):**
- Each label field depends only on:
  - the rows, day flags and corporate actions dated ≤ e;
  - D6 membership;
  - the retrospective D4 `listed_end`, which is the authorized metadata
    source.
- Nothing dated after e may change a label for (f, K, h).
- Evidence needed only for h₂ > h₁ never changes the label for h₁.

## 7. Outputs

Under `<root>/derived/returns/<returns_id>/`, where
`returns_id = "r-" +` the first 16 hex characters of
SHA-256(canonical_json of: every binding hash, the document hashes, and the
SHA-256 of `returns.py`).

| File | Columns (types) | Rows and order |
|---|---|---|
| `members.parquet` | `formation_date` date32, `security_key` string, `ticker` string, `rank` int64, `top_200` bool, `top_500` bool, `top_1000` bool, `listed_end_dev` date32 (null unless `listed_end ≤ 2023-06-30`), `listing_status` string (`ENDS_WITHIN_DEVELOPMENT` or `CONTINUES_BEYOND_DEVELOPMENT_PERIOD`), `terminal_evidence_source` string (constant `D4_RETROSPECTIVE_LISTED_END`) | one row per member; sorted by (formation_date, rank) |
| `horizons.parquet` | `formation_date` date32, `h` int64, `endpoint` date32, `crosses_reserve` bool | 90 × 63 rows; sorted by (formation_date, h) |
| `daily_returns.parquet` | `security_key` string, `date` date32, `prev_session` date32, `session_status` string, `daily_total_return` float64 (non-null iff VALID), `ca_event_count` int64, `corp_action_suspect` bool | one row per (K, evidence session); sorted by (security_key, date) |
| `labels.parquet` | `formation_date` date32, `security_key` string, `h` int64, `endpoint` date32, `label_state` string, `terminal_affected` bool (null iff purged), `observed_path_complete` bool (null iff purged), `n_observed_sessions` int64, `n_post_terminal_sessions` int64, `n_invalid_sessions` int64, `observed_segment_end` date32, `first_invalid_session` date32, `first_invalid_status` string (last six null when purged or when not applicable) | members × 63 rows; sorted by (formation_date, security_key, h) |
| `returns.json` | canonical JSON with no timestamps: bindings, document hashes, constants (as_of, buffer, Hmax, reserve start), counts per `session_status` and `label_state`, terminal-affected count per h, the purge count, the member count, the D7b obligations (§8), the certification-boundary text, `files_sha256`, and `returns_hash = SHA-256(canonical_json(files_sha256))` | — |

- **Column whitelist:** no other column may appear. In particular, no `t0`,
  `ts`, `tau`, `leg`, `quantile`, `factor`, `scenario`, `forward_return` or
  exact post-development date column.
- **Determinism:**
  - stable sorts; no dependence on input row order, filesystem order,
    dict/set iteration, timezone (`TZ`) or locale (`LC_ALL`);
  - float64 values written through pyarrow, with no rounding;
  - no wall-clock values;
  - two builds are byte-identical;
  - an existing output with different bytes raises an error and is never
    overwritten;
  - writes are atomic (temporary directory, then rename).
- **CLI:** `python -m smart_beta.dataset_v1.returns --root <root>` prints
  `returns_id` and `returns_hash` only.

## 8. D7b obligations (recorded; not implemented here)

D7b's own spec must, at minimum:
1. remove `crosses_reserve` labels **before** calling P7-B, so they are never
   reported as `insufficient_forward_window`;
2. build the P7-B return panel on the full trusted-session date axis, so
   that P7-B's h counts trusted sessions;
3. never treat `POST_TERMINAL` or `EMPTY_OBSERVED_PATH_IDENTITY` as missing;
4. apply T0/TS only at evaluation time, after leg assignment, with
   R_scenario = (1 + R_obs)(1 + τ) − 1 (clarification §8);
5. decide explicitly how terminal-affected but INCOMPLETE paths enter the
   statistics. D7a takes no position on this.

## 9. Required tests

All tests use synthetic `tmp_path` fixtures with injected bindings, a socket
guard and a credential sentinel. None touches the real data_store.

**Clock and horizon**
1. h = 1: the label uses only the session return dated f+1, and
   endpoint = the next session.
2. h = 63 = Hmax. The horizons table has exactly h = 1…63.
3. The return dated f never enters any segment.
4. Changing close(f−1) or any row before f changes no label.
5. Endpoints across weekends and holidays use trusted sessions, not calendar
   days.

**Terminal classification**

6. `listed_end == f`: affected for every h; `EMPTY_OBSERVED_PATH_IDENTITY`;
   `n_post_terminal == h`.
7. `listed_end ==` the first session after f: affected for every h; a
   one-session segment.
8. `listed_end == endpoint(f, h)`: affected at h and above, not affected
   below h.
9. `listed_end > endpoint`: not affected.
10. `listed_end < f`: `D7ConsistencyError`.
11. `listed_end` on a non-session date: classified by date comparison.
12. Rows stop before `listed_end` (cessation): not terminal before
    `listed_end`; INCOMPLETE.
13. Adding or removing a row at endpoint+1 changes nothing for (f, K, h).
14. A temporary gap that later resumes: INCOMPLETE, not affected.
15. An ordinary missing row inside the window: `NO_ROW`, and the next session
    is `PREV_NO_ROW`.
16. A missing row at the endpoint: INCOMPLETE.
17. A missing pre-terminal row: affected **and** INCOMPLETE.
18. Sessions after `listed_end` are `POST_TERMINAL`, even when
    INTERVAL_MISMATCH rows exist there.
19. A missing close(f): r(f+1) is `PREV_NO_ROW`; INCOMPLETE; no fill.
20. An unusable (corrupt) row at f: `PREV_ROW_UNUSABLE`.
21. Terminal and incomplete are both recorded.

**Row QC**

22. OHLC_INCONSISTENT row: `ROW_UNUSABLE`.
23. DUPLICATE_DATE rows: unusable.
24. INTERVAL_MISMATCH row: unusable.
25. VENDOR_NULL row: unusable.
26. CORP_ACTION_SUSPECT row: VALID, with the flag true.
27. Zero-volume row: VALID.
28. Off-calendar row: never a session or a previous session.

**Corporate actions**

29. A dividend applies only on its exact ex-date: (1 + raw)(1 + div/close) − 1.
30. A split applies on its exact date.
31. A split and a dividend on the same date: the factors multiply.
32. An event on a non-session date: `D7ConsistencyError` (unmatched fails
    closed).
33. A superseded event, or knowledge_date ≠ effective_date: error.
34. An event on an unusable-row session: no adjustment leaks to the next
    session.
35. Changing any `adj*` field leaves the output byte-identical.
36. A synthetic factor of 0 that would give ≤ −100%: error.
37. A non-finite return: error.

**Population and survivorship**

38. Top-1000 preservation: members equal D6 top_1000 exactly; rank and flags
    verbatim; top-200 ⊆ top-500 ⊆ top-1000.
39. A security that disappears from later vendor data (no rows after f,
    `listed_end` far away) stays present with all 63 labels.
40. Survivor-intersection adversary: a member with no later observations and
    absent from any later symbol set is still present in members, labels and
    daily_returns.
41. A member key missing from D4, duplicated in D4, or not
    INTERVAL_ASSIGNED: error.
42. A member count ≠ min(1000, n_eligible), or non-contiguous ranks: error.

**Reserve**

43. Reserve-crossing horizon: `crosses_reserve` is true, the label is
    `PURGED_RESERVE_CROSSING` with null evidence, and no daily_returns row is
    ≥ 2023-07-03.
44. Purge-before-P7-B contract: unmodified `align_forward_returns`, given a
    development-only panel, reports such a label as
    `insufficient_forward_window`. D7a flags it PURGED (never INCOMPLETE),
    which proves the §8.1 purge must happen upstream.
45. Refusal of a `reserve_sealed`, `raw` or `dsc_evidence` path.
46. Refusal of non-allowlisted paths: `security_qc`, `source_summary`,
    `obs_flags`, `audit_development`, `interval_mismatch`, D-SC.
47. An injected reserve-dated observation, day flag or corporate action:
    `ReserveFirewallError`.
48. Future exact `listed_end` (after 2023-06-30): `listed_end_dev` is null,
    the status is `CONTINUES_BEYOND_DEVELOPMENT_PERIOD`, and the date string
    appears in no output byte.
49. `listed_end == 2023-06-30`: exposed exactly.

**Semantic separation**

50. The empty-path identity is not an observed zero: no daily row is created,
    the state is `EMPTY_OBSERVED_PATH_IDENTITY`, `observed_path_complete` is
    true, and the label is not INCOMPLETE.
51. Column whitelist: no T0, TS, τ, leg, quantile, factor, scenario or
    forward-return column in any output.
52. No hard-coded horizon: every non-crossing (f, K) has labels for all
    h = 1…63.

**Determinism and bindings**

53. Permuting every input table: byte-identical outputs.
54. Changing `TZ` and `LC_ALL`: byte-identical outputs.
55. Two builds are byte-identical; an existing output with different bytes
    raises an error and is not overwritten.
56. A binding-hash mismatch for each file, and a document-hash mismatch: fail
    closed.
57. A decoy newer `u-*`, `q-*`, `i-*` or `n-*` directory: ignored; only the
    bound ids are used.

**Temporal properties**

58. Endpoint invariance (property test): for random (f, K, h), altering or
    appending any D3 row, D5 flag or corporate action dated after
    endpoint(f, h), or changing `listed_end` while it stays > endpoint,
    leaves that label row identical.
59. Horizon nesting (property test): for h₁ < h₂, altering evidence in
    (endpoint(h₁), endpoint(h₂)] leaves every h₁ label identical.
60. Membership immutability: altering any market input leaves members
    identical to the D6 rows.

## 10. Required mutations (each must fail the suite)

- M1 include the return dated f in the segment
- M2 shift the segment back by one (include the f−1 → f return)
- M3 endpoint off by one (h+1 or h−1)
- M4 count horizons in calendar days
- M5 fill a missing daily return with 0
- M6 use the last available row as the previous close (bridge a gap)
- M7 drop disappeared securities (intersect with later survivors)
- M8 infer terminal from the last usable row (cessation)
- M9 infer terminal from absence at endpoint+1
- M10 ignore terminal events (`terminal_affected` always false)
- M11 count a terminal event after the endpoint
- M12 strict lower bound `f < listed_end`
- M13 `listed_end == f` → INCOMPLETE/MISSING
- M14 `POST_TERMINAL` → `NO_ROW`
- M15 empty path treated as INCOMPLETE
- M16 empty path stored as an observed zero (synthetic 0 row or state)
- M17 use vendor `adjClose` instead of the §8 path
- M18 apply a corporate action to the next session (calendar resolution)
- M19 silently ignore an unmatched corporate action
- M20 allow a CORRUPT row
- M21 allow an INTERVAL_MISMATCH row
- M22 reject a zero-volume row
- M23 treat CORP_ACTION_SUSPECT as unusable
- M24 store a TS value in D7a
- M25 store a T0/τ value in D7a
- M26 emit a reserve-crossing label as non-purged
- M27 read a `reserve_sealed` path
- M28 accept observations dated ≥ 2023-07-03
- M29 leak an exact `listed_end` after 2023-06-30
- M30 use an observation after the endpoint in a label
- M31 resolve the "latest" artifact directory instead of the bound id
- M32 hard-code horizon 21 (labels only for h = 21)
- M33 remove the finite/> −1 guard
- M34 truncate a crossing horizon at 2023-06-30 instead of purging
- M35 missing row converted into a terminal event

## 11. Production barrier (planner)

The planner reports counts and states only. It never inspects return values,
distributions or any performance quantity.

- **Members:** 90,000 rows over 1,921 distinct keys.
  - Exactly 29 rows have `listed_end == f` (25 formations; 10 in the
    top-500, 3 in the top-200).
  - Each of those is `EMPTY_OBSERVED_PATH_IDENTITY` and terminal-affected for
    every non-crossing h.
- **Horizons:** 5,670 rows. `crosses_reserve` = 126: 2023-03-31 → 1,
  2023-04-28 → 20, 2023-05-31 → 42, 2023-06-30 → 63.
- **Labels:** 5,670,000 rows, of which 126,000 are `PURGED_RESERVE_CROSSING`.
- **Dates:** no daily_returns date ≥ 2023-07-03, and no exact post-development
  `listed_end` anywhere.
- **File-open audit:** only allowlisted paths are opened.
- **Clean rebuild:** a rebuild in a fresh scratch root is byte-identical.
- **Full suite:** a credential-scrubbed full suite at the worker commit.

## 12. Certification boundary and nonclaims

**Proposed certification (on a passing barrier):**
> D7a deterministically reconstructs, from the bound D3/D4/D5/D6 artifacts
> and only development-period observations, the contract-§8 daily total
> returns (raw close-to-close between consecutive usable trusted sessions,
> adjusted by the Phase-4B corporate-action table on exact dates) for every
> D6 development top-1000 formation member.
>
> For every formation, member and horizon 1–63 trusted sessions, it records
> factual path evidence:
> - completeness;
> - typed invalid sessions;
> - POST_TERMINAL sessions;
> - terminal-affected status from the retrospective D4 `listed_end`,
>   including `listed_end == f`;
> - calendar endpoints and reserve-crossing purge flags.
>
> It never infers terminal events from missing data, never reads reserve
> observations, never drops a formation member and stores no scenario
> return.

**Not claimed:**
- alpha, predictive power or factor validity;
- investability, transaction-cost realism, capacity or borrow;
- certified terminal proceeds or delisting returns
  (`DELISTING_RETURN_NOT_CERTIFIED`), or complete delisting-return knowledge;
- historical delisting-knowledge timing (not PIT-certified);
- issuer-identity knowledge timing/continuity or identifier continuity (NOT
  CERTIFIED);
- complete survivorship elimination;
- complex corporate actions beyond the specimen-level certification;
- production trading readiness;
- confirmation evidence, reserve access or reserve consumption;
- any compounded forward return, T0 or TS result. Those belong to P7-B and
  D7b.

## Report

- files changed;
- test counts, with a test-to-item and mutation-to-test mapping;
- the commit SHA;
- the line `sealed files modified: NONE`.
