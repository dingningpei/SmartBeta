# D6 — Development PIT universe construction (Dataset v1.1) — FROZEN BEFORE IMPLEMENTATION

- **Status:** FROZEN BEFORE IMPLEMENTATION (the user approved it on
  2026-09-28). Implementation requires a separate launch authorization.
- **Authority, in precedence order** (on any conflict, stop and report):
  1. `docs/discovery_dataset_v1_contract.md` §§3, 4, 5, 6, 9, 10, 11, 12, 16
     (gate 11) — `d03f3554…fa80e3`;
  2. `docs/discovery_dataset_v1_1_classification_amendment.md` (b6225b6) —
     `f89a7128…c623`;
  3. `docs/discovery_dataset_v1_1_identity_binding_clarification.md`
     (b9d9e88) — `57da1603…edfa9`;
  4. `docs/discovery_dataset_v1_1_d6_clarification.md` (5238303) —
     `e3cb52a892f533c41c5c0d1d927cd54d7e200ef2a28e56e76efdf01227aa17ea`;
  5. `worker_tasks/dataset_v1/dataset-v1-1-decisions.md` §2 (Q-D6-1);
  6. `task-d5-qc.md` and `task-dsc-classifier.md` (their interfaces).
- **Owned files (new):** `smart_beta/dataset_v1/universe.py`,
  `tests/test_dataset_v1_universe.py`, `tests/fixtures/dataset_v1/d6/**`.
- **Read-only imports allowed:**
  - `smart_beta.vendors.tiingo.calendar_source.build_nyse_calendar`;
  - `smart_beta.dataset_v1.classification.class_state_at` and
    `policy_state_at` (the D-SC public query API; classification is never
    re-derived).
- **Must not modify** any existing file or any upstream artifact.
- **Provider or network calls: 0.**

## 1. Scope

- D6 builds the **development** PIT universe for the **90 development
  formations only**.
- Reserve formations are **DEFERRED / NOT ASSESSED** (D6 clarification H1).
- D6 performs no factor computation, returns, evaluation, portfolios, T0/TS
  scenarios (D7) or reserve work (D8 or confirmation).

## 2. Upstream bindings (exact; fail closed on any mismatch; never "latest")

| Stage | Artifact | Top-level hash (from its manifest) |
|---|---|---|
| D3 | `n-f874d3ecb61374d4` | `normalized_hash f93093a8b8fa07521191d687560c2c26ab16630ebeed35d68aa12867def47fb3` |
| D4 | `i-a21f1e46621dee94` | `identity_hash 1023dbca39fd061d52fd26eeb6302f4a8dec467a8ea996b1903da801306ae730` |
| D5 | `q-5d1028655f8c82e1` | `qc_hash 3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3` |
| D-SC | `c-27b6dabd0db0cb15` | `dsc_hash 2ee4940c14a85048fddd9e3e675c3e9a6f40a11c8887192a5e61939adfc62b60` |

**Allowlist.** These are the only data files D6 may open, relative to
`<root>`. Each must match its manifest's `files_sha256` entry:

| Relative path | SHA-256 |
|---|---|
| `normalized/n-f874d3ecb61374d4/observations.parquet` (development partition only) | `557001d0e9c617befd3c036683f171094e86e19badb760098f40c08f3f56596e` |
| `derived/identity/i-a21f1e46621dee94/identity_assignment.parquet` | `fee4614009095773f75fb832343a5fa9b87b9de1cf363125567222b08fad3f8b` |
| `derived/qc/q-5d1028655f8c82e1/day_flags.parquet` (development partition only) | `f4010ebeabf5cfae2684f3658a5f19f922f15e2c8898410049f2148440cf7891` |
| `derived/classification/c-27b6dabd0db0cb15/class_timeline.parquet` | `e70d5c91b3f7bce6b5dee3337d47ec3f0c5e7700b35300d4ef3fc8e48b82caeb` |
| `derived/classification/c-27b6dabd0db0cb15/policy_timeline.parquet` | `988087c5c0a8daf5a2674eb4b4b8db7a0e2fce141478e34b2d63d20bfb651c8e` |

The manifests are read only for binding:
- `normalized/n-f874d3ecb61374d4/build.json`;
- `derived/identity/i-a21f1e46621dee94/identity.json`;
- `derived/qc/q-5d1028655f8c82e1/qc_report.json`;
- `derived/classification/c-27b6dabd0db0cb15/dsc.json`.

The four normative documents in the authority list are also hash-bound.

**Bindings are a frozen `Bindings` value.** Production constants are the
default; tests inject synthetic bindings. The allowlist is derived from the
bindings.

## 3. Reserve firewall (mechanical; not discipline)

- **Guarded resolver.** Every file open goes through one resolver, which
  raises `ReserveFirewallError` (typed, fail closed) if:
  - any path component equals `reserve_sealed`;
  - the path is not in the allowlist;
  - a requested formation date is ≥ 2023-07-03.
- A failed run writes no artifact.
- **Forbidden reads**, even though some are value-free: anything under
  `reserve_sealed/`, `raw/`, D3 `source_summary.parquet` (its counts span
  the reserve), D5 `security_qc.parquet` (its diagnostics span the reserve),
  and every other file.
- **Post-load assertion:** every loaded observation and day-flag date is
  earlier than 2023-07-03. Any row on or after that date raises
  `ReserveFirewallError`.
- D-SC queries use cutoffs ≤ 2023-06-29 only.
- **No output** contains any reserve-derived price, volume, dollar volume,
  rank, membership, eligible count or cross-sectional statistic. Reserve
  formation dates may be listed only as `DEFERRED` rows in `formations`,
  with no market-derived field.

## 4. Formation schedule

- Calendar: `build_nyse_calendar("2015-01-02", "2027-12-31")`.
- **Full schedule** = `month_end_trading_dates("2016-01-04", "2026-08-31")`:
  - assert **128** dates, the first 2016-01-29 and the last 2026-08-31;
  - **development** = the dates ≤ 2023-06-30: assert **90**, the last
    2023-06-30;
  - **reserve** = the dates ≥ 2023-07-03: assert **38**, the first
    2023-07-31.
  - The dates are generated, never hard-coded; the assertions check the
    generation.
- **f** = the formation session. **cutoff** = f−1 =
  `calendar.previous_trading_day(f)`.
- **Liquidity window W_f** = the **63 trusted NYSE sessions immediately
  preceding f**, i.e. f−63 … f−1, cardinality exactly 63.

## 5. Per-formation, per-key evaluation

**Population at f:** every D4 key with `listed_start ≤ f−1` **and**
`listed_end ≥ f` (H2, unclipped D4 dates). Keys failing that are counted in
the manifest as `NOT_LISTED_AT_FORMATION` and get no audit row.

**Rows of key K:**
- D3 development observations whose `ticker` equals K's ticker.
- Each D3 row is joined to D5 development `day_flags` on (`ticker`, `date`,
  `raw_row_index`).
- A row is **unusable** if it carries a D5 day flag with disposition
  `CORRUPT` (OHLC_INCONSISTENT, NONPOSITIVE_PRICE, MALFORMED_CA,
  DUPLICATE_DATE, OFF_CALENDAR_ROW), class `INTERVAL_MISMATCH`, or class
  `VENDOR_NULL`.
- A **usable row** is a D3 row on a trusted session that is not unusable.
  CORP_ACTION_SUSPECT does **not** make a row unusable (H3).
- A **usable session** is a trusted session with at least one usable row.
  Duplicate-date rows are both CORRUPT, so such a session has none.
- **Liquidity-valid session:** a usable row with raw `close > 0` and raw
  `volume` not null. Then DV = `float64(close) * float64(volume)`; zero
  volume is valid with DV = 0.

**Checks, with one primary reason code** (the first failing check, in this
order):

| # | Check | Reason code |
|---|---|---|
| 1 | D4 `state == INTERVAL_ASSIGNED` | `IDENTITY_NOT_ADMISSIBLE` |
| 2 | exchange ∈ {NYSE, NASDAQ, NYSE MKT, AMEX} (§3.1) | `EXCHANGE_NOT_ELIGIBLE` |
| 3 | `class_state_at(K, f−1).state == COMMON_STOCK` | `NOT_COMMON_STOCK` (class state and reason recorded) |
| 4 | `policy_state_at(K, f−1) != EXCLUDED` | `ISSUER_POLICY_EXCLUDED` |
| 5 | `policy_state_at(K, f−1) != ISSUER_STATUS_UNRESOLVED` | `ISSUER_POLICY_UNRESOLVED` |
| 6 | listing age = #trusted sessions in [max(listed_start, 2015-01-02), f−1] ≥ 252 | `LISTING_AGE_INSUFFICIENT` |
| 7 | Q-D6-1 denominator D_f > 0 | `PIT_MISSINGNESS_EMPTY` |
| 8 | Q-D6-1 ratio M_f / D_f ≤ 0.05 | `PIT_MISSINGNESS_EXCEEDED` |
| 9 | liquidity-valid sessions in W_f ≥ 50 | `TRAILING_LIQUIDITY_INSUFFICIENT` |
| — | all pass | `ELIGIBLE` |

- Only policy `NO_REGULATORY_EXCLUSION_OBSERVED` passes checks 4 and 5. It is
  not treated as certified unrestricted.

**Q-D6-1 (verbatim semantics):**
- `S_f` = trusted sessions in [max(listed_start, 2015-01-02),
  min(listed_end, f−1)].
- `first_f` = the first usable session in `S_f`.
- `D_f` = the sessions in `S_f` from `first_f` through f−1.
- `M_f` = the sessions in `D_f` with no usable row (no row, corrupt or
  vendor-null).
- Off-calendar rows are in neither count. Sessions before `first_f` are in
  neither count.
- If `first_f` does not exist, D_f = 0.
- The ratio is exact rational `M_f / D_f`, compared as `M_f * 100 <= 5 * D_f`
  (integer arithmetic; exactly 5% passes).

**Statistic (eligible keys):** `median_dv` = the median of DV over the
liquidity-valid sessions in W_f.
- Computed in float64 after sorting ascending.
- Odd N: the middle value. Even N: `(a + b) / 2` of the two middle values.
- No winsorization, log transform or normalization.
- Adjusted fields are never read.

**Ranking:**
- Sort eligible keys by (`median_dv` descending, `security_key` ascending by
  Python code-point order).
- `rank` = 1..n.
- `top_200` = rank ≤ 200, `top_500` = rank ≤ 500, `top_1000` = rank ≤ 1000.

**Formation status:**
- `OK` if n_eligible ≥ 500. Otherwise `UNIVERSE_SHORTFALL`, and Gate 11 FAIL
  for that formation.
- N is never reduced, no prior members are carried, and no rescue is
  attempted.
- `top_1000_shortfall = n_eligible < 1000` is recorded as information only.

## 6. Outputs

Under `<root>/derived/universe/<universe_id>/`, where `universe_id = "u-" +`
the first 16 hex characters of SHA-256(canonical_json of all binding hashes,
the document hashes and the SHA-256 of `universe.py`).

| File | Content |
|---|---|
| `universe_development.parquet` | **discovery-safe:** `formation_date`, `security_key`, `rank`, `top_200`, `top_500`, `top_1000`; rows = eligible keys with rank ≤ 1000; sorted by (formation_date, rank) |
| `formations.parquet` | per formation in the full schedule: `formation_date`, `cutoff_date`, `partition` (DEVELOPMENT/RESERVE); for development: `n_population`, `n_eligible`, `status` (OK/UNIVERSE_SHORTFALL), `top_1000_shortfall`; for reserve: `status = DEFERRED` and **all other fields null** |
| `audit_development.parquet` | per development formation × population key: `formation_date`, `security_key`, `ticker`, `identity_state`, `class_state`, `class_reason`, `policy_state`, `listing_age_sessions`, `missing_numerator`, `missing_denominator`, `trailing_valid_sessions`, `median_dv`, `reason`, `rank` (nullable); development rows only; sorted by (formation_date, security_key) |
| `universe.json` | canonical JSON, no timestamps: bindings, document hashes, schedule counts, per-formation status summary, Gate 11 (development: PASS/FAIL per formation; `reserve_gate11: "DEFERRED / NOT ASSESSED"`), certification-boundary text, `files_sha256`, `universe_hash` = SHA-256(canonical_json(files_sha256)) |

**Determinism:**
- stable sorts everywhere, with no dependence on input row order,
  filesystem order, dict or set iteration, timezone or locale;
- float64 values written through pyarrow;
- no wall-clock values;
- two builds must be byte-identical;
- an existing output with different bytes raises an error and is never
  overwritten.

## 7. Required tests

All tests use synthetic `tmp_path` fixtures with injected bindings, a
socket guard and a credential sentinel. None touches the real data_store.

**Adversarial:**
1. a future valid row after f
2. a future missing row
3. a future corrupt row
4. a future delisting after f
5. a key listed shortly before f
6. listing age 251
7. listing age 252
8. 49 liquidity-valid sessions
9. 50 liquidity-valid sessions
10. a zero-volume valid row (DV = 0, counted)
11. a corrupt-only trusted session
12. a vendor-null trusted session
13. an off-calendar corrupt row
14. CLASS_UNRESOLVED
15. an active BDC/fund exclusion
16. ISSUER_STATUS_UNRESOLVED
17. the retrospective-identity-limitation case (D-SC policy is applied as
    given)
18. a DV tie at rank 500/501, resolved by security_key
19. shuffled input rows
20. a changed timezone
21. future D-SC evidence
22. future D5 flags
23. a key delisted immediately after f
24. eligible count 499 (SHORTFALL) and 500 (OK)
25. a reserve access attempt from the development path

**Also:**
26. `reserve_sealed` path refusal
27. a non-allowlisted path refusal
28. an injected reserve-dated row (post-load assertion)
29. no reserve-derived field in any output; reserve formations are
    DEFERRED with nulls
30. top-200 ⊆ top-500 ⊆ top-1000 consistency
31. schedule counts and boundaries (128/90/38, first and last dates)
32. listed_end = f−1 (excluded), listed_end = f (admitted), listed_end > f
    (admitted)
33. a corrupt row with otherwise valid close and volume (not
    liquidity-valid)
34. a CORP_ACTION_SUSPECT but otherwise usable row (counts for missingness
    and liquidity)
35. missingness exactly 5% (pass) and just over 5% (fail)
36. an even-N median convention
37. a binding hash mismatch (fail closed) and a document hash mismatch
38. determinism (two builds byte-identical)

**Future-data invariance (property test):** for random development f,
appending or altering any D3, D5 or D-SC input dated after f−1 (or a
listed_end change that stays ≥ f) leaves U_f and its audit rows identical.

## 8. Required mutations (each must fail the suite)

- using market observations dated f instead of f−1;
- whole-window or future missingness;
- counting sessions before the first usable one as missing;
- ignoring a corrupt trusted session in missingness;
- counting an off-calendar row in the missingness denominator;
- accepting 49 liquidity sessions;
- including a corrupt row in liquidity;
- dropping a valid zero-volume observation;
- using adjusted close;
- using the mean instead of the median;
- ranking ascending;
- removing the deterministic tie-break;
- admitting CLASS_UNRESOLVED;
- admitting ISSUER_STATUS_UNRESOLVED;
- letting a post-f delisting alter U_f;
- reading reserve data during development;
- emitting a reserve ranking or membership;
- silently reducing top-500 after a shortfall.

## 9. Production barrier (planner)

- **Reconciliation target:** the per-formation development `n_eligible` must
  equal, exactly for all 90 formations, the frozen v4 development reference
  `<root>/dsc_evidence/d6_reference_development_v4.csv`
  (SHA-256 `7469e171d592571301f258b0f4d95036219de5a25ba2a9e1ae09530508194484`;
  90 rows; minimum 3,061 at 2016-01-29).
  - That reference was built by the v4 structural measurement under
    identical rules: H2 (c); usable rows excluding D5 CORRUPT,
    INTERVAL_MISMATCH and off-calendar rows; Q-D6-1; age ≥ 252; ≥ 50 in
    W_f; COMMON with no exclusion or unresolved policy. Production has 0
    VENDOR_NULL and 0 non-positive rows.
  - It is a planner barrier input only, never a D6 runtime input.
  - A mismatch means **STOP BEFORE MERGE** and diagnose. The rules are never
    tuned to match.
- **Gate 11:** all 90 development formations have status OK.
- **Two clean builds** are byte-identical.
- **Reserve firewall** checks pass. No reserve D6 artifact exists.

## 10. Certification boundary and nonclaims

D6 completion certifies **only** the development PIT-universe construction
under the frozen Dataset-v1 assumptions:
- **Reserve D6 and reserve Gate 11:** DEFERRED / NOT ASSESSED.
- **Issuer-identity knowledge timing/continuity:** NOT CERTIFIED.
- Historical delisting-knowledge timing is not universally PIT-certified.

**Not claimed:**
- alpha or factor validity;
- production investability;
- delisting returns;
- transaction-cost realism;
- capacity or borrow availability;
- complete survivorship elimination;
- reserve access or confirmation consumption.

## Report

- files changed;
- test counts and a test-to-item mapping;
- the commit SHA;
- the line `sealed files modified: NONE`.
