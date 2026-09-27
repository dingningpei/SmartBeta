# D5 — QC suite (Discovery Dataset v1, Wave 3) — FROZEN SPEC

- **Status:** FROZEN (the user approved the freeze on 2026-09-27).
  Implementation is **not yet authorized**.
- **Authority:** contract (SHA-256 `d03f3554…fa80e3`) §§3, 4, 5, 7, 8, 10, 13,
  15 and 16 (gates 1–10); `dataset-v1-wave2-plan.md` decisions R-1, R-2 and
  R-3 and the interfaces in §3.1–3.6; `dataset-v1-1-decisions.md` (Q-D6-1).
- **Owned files (new):** `smart_beta/dataset_v1/qc.py`,
  `tests/test_dataset_v1_qc.py`, `tests/fixtures/dataset_v1/d5/**`.
- **Production inputs (read-only; every hash verified):**
  - batch `b-c21b48190918f21f` (manifest and raw files);
  - D3 build `n-f874d3ecb61374d4` (`normalized_hash f93093a8…`);
  - D4 run `i-a21f1e46621dee94` (`identity_hash 1023dbca…`);
  - the D1 pool (`8820de16…`) and the snapshot (`f6b1d6c2…`);
  - the trusted calendar.
- **Invariants:**
  - D5 **deletes and modifies nothing**. Input hashes are identical before
    and after a run.
  - **Every finding receives exactly one disposition.**
  - D5 **never decides D6 eligibility.**
  - Whole-window missingness is a **diagnostic only**.
  - Outputs are deterministic and contain no timestamps.
  - The reserve firewall (below) holds for every output.

## 1. Disposition vocabulary

| Disposition | Meaning |
|---|---|
| `VALID` | passes every applicable check |
| `CORRUPT` | an integrity, parse or consistency violation in vendor data |
| `LEGIT_MISSING` | an expected absence (holiday, NOT_YET_LISTED, ENDED); no row is ever fabricated |
| `INSUFFICIENT_COVERAGE` | data inadequate or unreachable for the key |
| `UNCERTIFIED_SEMANTICS` | meaning not certified; handled exactly as the contract rule says |

`LEGIT_ZERO_VOLUME` is `VALID`: under contract §5 a zero-volume row is a valid
session with DV = 0.

## 2. Frozen dispositions of the production facts

| Fact | Disposition | Treatment |
|---|---|---|
| 4 `OFF_CALENDAR_ROW` (CBL 2026-02-16; PRS 2026-02-16, 2026-04-03, 2026-05-25) | `CORRUPT` security-day | Retained in D3 and counted. Excluded from every session-based computation, since the row is not a session. Reserve partition: identifiers and count only |
| 685,963 `LEGIT_ZERO_VOLUME` | `VALID` | Retained; counted per partition (gate 7) |
| 504 `IDENTITY_AMBIGUOUS` keys | `UNCERTIFIED_SEMANTICS` (security), carrying the D4 reason | Excluded downstream under contract §4; retained; counts and list reported (gate 3) |
| 330 `HISTORY_UNREACHABLE` keys | `INSUFFICIENT_COVERAGE`, reason `HISTORY_UNREACHABLE:<D4 reason>` | Listed; disclosed as a survivorship-loss contributor |
| 34 `NO_OBSERVATIONS` keys | `INSUFFICIENT_COVERAGE`, reason `NO_OBSERVATIONS:<sub-reason>` | Gate 2 (symbol level) |
| 10 `UNREQUESTABLE_IDENTIFIER` keys | `INSUFFICIENT_COVERAGE`, reason `UNREQUESTABLE_IDENTIFIER` | Gate 2; no class is inferred |
| `INTERVAL_MISMATCH` security-days (production: 4 MTL rows, 2022-12-16 to 12-21) | `UNCERTIFIED_SEMANTICS` security-day | Retained and flagged; excluded from that key's session inputs |

## 3. Checks

Findings are recorded per security-day or per security, each with exactly one
disposition.

**L0 — provenance** (gates 1 and 9; zero tolerance):
- expected set = status set = `source_summary` set;
- the D3 `build_inputs` bindings (contract, pool, snapshot, manifest files,
  acquisition record) and the D4 → D3 binding;
- every raw file re-hashes;
- every D3 and D4 file re-hashes to its recorded hash.

**L1 — observation** (gates 5 and 6), over every D3 observation row:
- `OHLC_INCONSISTENT`: `low > min(open, close)` or `max(open, close) > high`.
  Disposition `CORRUPT`.
- `NONPOSITIVE_PRICE`: any of open, high, low or close is ≤ 0. Disposition
  `CORRUPT`.
- `MALFORMED_CA`: carried from D3. Disposition `CORRUPT`.
- `OFF_CALENDAR_ROW`: carried from D3. Disposition `CORRUPT`.
- `CORP_ACTION_SUSPECT` (§8): for an `INTERVAL_ASSIGNED` key, between
  consecutive usable sessions (present, on-calendar, not CORRUPT, not
  INTERVAL_MISMATCH), `|close_t / close_{t−1} − 1| > 0.5` with no split or
  dividend event dated t in D3 `ca_events`.
  - Disposition `UNCERTIFIED_SEMANTICS`; the day is retained and never dropped.
  - The ratio is computed transiently and **never written to any output**, in
    either partition.

**L2 — security/interval** (gates 3, 4 and 8), for `INTERVAL_ASSIGNED` keys
over in-window listed sessions `S = trusted sessions in
[max(listed_start, 2015-01-02), min(listed_end, C)]`:
- A **usable row** is present, on-calendar, not CORRUPT and not
  INTERVAL_MISMATCH.
- **Classes:**
  - `NOT_YET_LISTED`: sessions in S before the first usable row
    (`LEGIT_MISSING`);
  - `ENDED`: sessions in S after the last usable row (`LEGIT_MISSING`);
  - `VENDOR_MISSING`: sessions in S between the first and last usable rows
    with no usable row;
  - `VENDOR_NULL`: from D3;
  - `TERMINATION_MISMATCH`: the last usable row is more than 5 sessions before
    `min(listed_end, C)`. It is a security-level `UNCERTIFIED_SEMANTICS`
    **flag only**; D5 excludes nothing.
- **Whole-window diagnostic:** `(VENDOR_MISSING + VENDOR_NULL) / |S from the
  first usable row|`. **Diagnostic only.** It never feeds eligibility
  (Q-D6-1).
- **PIT support output:** every `VENDOR_MISSING` session (a date with no row)
  and every unusable-row session is emitted in `day_flags` with
  `(security_key, date, class)`, so that D6 can compute the Q-D6-1 expanding
  PIT rule using only sessions ≤ f−1. D5 never computes that rule.

**L3 — panel** (gates 4, 7 and 10):
- row and date coverage against the calendar, and missingness distributions,
  both as counts;
- zero-volume counts;
- **gate 10:** rebuild D3 into a temporary root and compare it byte for byte
  with `files_sha256`. A parameter lets tests use small fixtures.

## 4. Gate PASS rules

- **Gates 1, 9 and 10:** zero failures.
- **Gate 2:** non-OK symbols are at most 1% and every one is listed
  (production 42/10,903).
- **Gates 3–8:** every finding has exactly one disposition, counts reconcile
  with the D3 and D4 totals, and output is deterministic.

No threshold is chosen or tuned using factor or performance information.

## 5. Outputs

Under `<root>/derived/qc/<qc_id>/`, where `qc_id = "q-" +` the first 16 hex
characters of the SHA-256 of `canonical_json` of the input hashes plus the
code hash.

- **`qc_report.json`:**
  - input bindings;
  - gate results (PASS or FAIL, with counts);
  - counts per disposition and reason;
  - reserve results as counts only;
  - `files_sha256`;
  - `qc_hash`.
- **`security_qc.parquet`:** one row per pool row, with:
  - `security_key`, `ticker`, `identity_state`, `security_disposition`;
  - `reasons` (canonical JSON list);
  - the diagnostic counts (`n_S`, `n_not_yet_listed`, `n_ended`,
    `n_vendor_missing`, `n_vendor_null`, `n_corrupt_days`,
    `n_uncertified_days`);
  - `whole_window_missing_rate_diagnostic`;
  - `termination_mismatch`;
  - `first_usable_date`, `last_usable_date`.
- **`day_flags.parquet`** (sessions before 2023-07-03) and
  **`reserve_sealed/<qc_id>/day_flags.parquet`** (sessions on or after
  2023-07-03):
  - columns `security_key`, `ticker`, `date`, `raw_row_index` (nullable),
    `class_or_flag`, `disposition`;
  - sorted by all columns.

## 6. Reserve-firewall output contract

For dates on or after 2023-07-03, outputs contain **only**:
- identifiers;
- dates;
- flag and class names;
- dispositions;
- integrity results;
- counts.

**Never written:**
- prices, volumes or adjusted fields;
- ratios or returns;
- value distributions, quantiles or means;
- any factor or performance information.

`security_qc` diagnostic counts are structural session counts, not values.
A test asserts that no output has a float column other than
`whole_window_missing_rate_diagnostic`, which is a missingness fraction and
not an economic value.

## 7. Required tests

1. **Every check:** seeded corruption for each L0–L3 check.
2. **Each production fact in §2**, as a fixture with the exact disposition.
3. **Exactly one disposition per finding.**
4. **The no-deletion invariant:** input hashes are unchanged.
5. **Reserve schema:** no value columns, and no reserve values in
   `qc_report.json`.
6. **The CORP_ACTION_SUSPECT ratio is never persisted**, verified by a
   column scan of every output.
7. **The whole-window diagnostic** is never used for any disposition or
   exclusion.
8. **`day_flags` supports PIT:** a D6-style expanding computation at f,
   evaluated on the fixture, is unchanged when rows after f−1 are altered.
9. **Determinism:** two runs are byte-identical.
10. **Binding refusals and gate-10 rebuild comparison.**
11. **No network** (socket guard), and a credential sentinel is absent.

## 8. Mutation and adversarial tests (must fail the suite)

- treating an off-calendar row as a session;
- treating zero volume as missing;
- writing the CA ratio;
- using the whole-window rate as an exclusion;
- letting `HISTORY_UNREACHABLE` or `NO_OBSERVATIONS` count as usable;
- dropping `INTERVAL_MISMATCH` days instead of flagging them;
- skipping raw re-hash;
- letting a post-f row change a PIT fixture result.

## 9. Forbidden

- deleting or modifying any input;
- deciding eligibility, classification or the universe;
- returns, dollar volume or statistics of value fields beyond the transient
  CA ratio;
- provider calls;
- editing non-owned files.

## Report

- files changed;
- test counts and a test-to-item mapping;
- the commit SHA;
- the line `sealed files modified: NONE`.
