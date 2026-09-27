# D4 — Identity resolver (Discovery Dataset v1, Wave 2)

- **Authority:** contract (SHA-256 `d03f3554…fa80e3`) §4 and §18; plan
  decisions D4-1, D4-2 and D4-3; the frozen interface in plan §3.
- **Owned files (new):** `smart_beta/dataset_v1/identity.py`,
  `tests/test_dataset_v1_identity.py`, `tests/fixtures/dataset_v1/d4/**`.
- **Must not import** `normalize.py` or `acquisition.py`. D4 tests against its
  own fixtures that follow the plan-§3 interface.

## Purpose

Assign each D1 pool `security_key` exactly one fail-closed identity state,
using D3's `source_summary` and observation dates together with **all**
supported-tickers snapshot rows. D4 never claims permanent identity:
`IDENTIFIER_CONTINUITY_NOT_CERTIFIED` is always retained.
`INTERVAL_ASSIGNED` means the data is *consistent with* exactly one listed
row. It does not establish identity.

## Public API

```python
resolve_identity(root, normalized_build_id, pool_path, snapshot_path) -> dict   # returns identity.json content
```

CLI:

```
python -m smart_beta.dataset_v1.identity resolve --root <root> --build-id <id> --pool <path> --snapshot <path>
```

## Definitions (frozen)

- **Calendar:** the trusted `build_nyse_calendar("2015-01-02", "2027-12-31")`.
  `A = 2015-01-02`; `C` comes from `build_inputs`.
- **Candidate rows of ticker T:** every snapshot CSV row whose `ticker`
  equals T **exactly**, whatever its exchange, assetType or currency (D4-1).
  - A candidate row with a blank or unparseable `startDate` or `endDate`, or
    with `startDate > endDate`, is **invalid**.
  - Duplicate identical rows are kept as separate rows.
- **Pool rows of T:** the `listed_intervals` of T in the D1 pool. Each is
  matched to exactly one candidate row with equal `exchange`, `startDate`
  and `endDate`. A pool row with no match is a typed input-inconsistency
  error; D4 aborts.
  - If it matches two or more identical candidate rows, all of them count
    as pool rows. They overlap, so rule 5 applies.
  - In production, all 11,231 pool rows match exactly one row (planner
    check, 2026-09-27).
- **Overlap (D4-2, D4-3):** two candidate rows `a` and `b` overlap when
  `a.startDate <= b.endDate` and `b.startDate <= a.endDate`, using inclusive
  unclipped dates. A boundary touch therefore overlaps, and so do identical
  or same-start rows.
- **Clipped interval of row x:** `s = max(x.startDate, A)`,
  `e = min(x.endDate, C)`.
  - The row is **containment-eligible** only if `s <= e`.
  - `si` is the calendar index of the first session ≥ s; `ei` is the index
    of the last session ≤ e.
- **Containment (tolerance 5 sessions):** with `fi` and `li` the calendar
  indices of the observed `first_date` and `last_date`, the span fits x when
  x is containment-eligible, `fi >= si - 5` and `li <= ei + 5`.
  - An observed date that is not a session maps to the index of the first
    session ≥ that date for `fi`, and the last session ≤ that date for `li`.
- **Post-C shadow:** some candidate row of T has `startDate > C`.

## States (one per pool `security_key`)

Precedence is applied per ticker, in this order:

| # | Condition on ticker T | State of every pool key of T | `reason` |
|---|---|---|---|
| 1 | `acquisition_status == UNREQUESTABLE_IDENTIFIER` | `UNREQUESTABLE_IDENTIFIER` | `REQUEST_PATTERN_MISMATCH` |
| 2 | `acquisition_status == EMPTY` | `NO_OBSERVATIONS` | `SHADOWED_BY_POST_C_LISTING` if there is a post-C shadow, else `UNEXPLAINED` |
| 3 | another non-OK status (RETRY_EXHAUSTED, MALFORMED, ENTITLEMENT_ERROR, NONDETERMINISTIC) | `NOT_ACQUIRED` | the status name |
| 4 | OK, and any candidate row is invalid | `IDENTITY_AMBIGUOUS` | `INVALID_SNAPSHOT_ROW` |
| 5 | OK, and any two candidate rows overlap | `IDENTITY_AMBIGUOUS` | `OVERLAPPING_LISTED_ROWS` |
| 6 | OK, and the span fits ≥ 2 candidate rows | `IDENTITY_AMBIGUOUS` | `MULTIPLE_FIT` |
| 7 | OK, and the span fits 0 candidate rows | `IDENTITY_AMBIGUOUS` | `NO_FIT` |
| 8 | OK, and the span fits exactly 1 candidate row x, and x is a pool row with key K | K: `INTERVAL_ASSIGNED` (`FITS_ONE_LISTED_ROW`); every other pool key of T: `HISTORY_UNREACHABLE` (`ASSIGNED_TO_OTHER_ROW`) | |
| 9 | OK, and the span fits exactly 1 candidate row x, and x is not a pool row | `HISTORY_UNREACHABLE` | `ASSIGNED_TO_NONPOOL_ROW` |

- Only `INTERVAL_ASSIGNED` keys may later carry observations. Every other
  state excludes the key and counts it.
- D4 deletes nothing.

## Output

As plan §3.6. The `identity_assignment` columns:

| Column | Type | Meaning |
|---|---|---|
| `security_key` | string | |
| `ticker` | string | |
| `exchange` | string | |
| `listed_start` | date32 | |
| `listed_end` | date32 | |
| `state` | string | |
| `reason` | string | |
| `acquisition_status` | string | |
| `candidate_row_count` | int64 | |
| `pool_row_count` | int64 | |
| `fit_row_count` | int64 | |
| `overlap` | bool | |
| `post_c_shadow` | bool | |
| `identifier_continuity` | string | always `"NOT_CERTIFIED"` |
| `evidence` | string | canonical JSON: candidate rows (exchange, assetType, priceCurrency, startDate, endDate, `in_pool`), fitted row indices, overlapping pairs, `fi`/`li`/`si`/`ei` values, `tolerance_sessions: 5` |

**`interval_mismatch`:** for each `INTERVAL_ASSIGNED` key, every observation
of its ticker (both partitions) dated before the key's unclipped
`listed_start` or after its unclipped `listed_end`.

**`identity.json`:**
- counts by state and by (state, reason);
- `n_pool_keys` must equal the pool row count;
- the number of `INTERVAL_ASSIGNED` keys;
- the `interval_mismatch` row count.

The output is byte-reproducible and contains no timestamps.

## Required tests

The fixtures reproduce the real structural specimens, with synthetic dates of
the same shape.

1. An ordinary single-row ticker (A): `INTERVAL_ASSIGNED`.
2. History in a non-pool row (AAAP-like: the pool row ended 2018, and a
   later non-pool row covers the span): `HISTORY_UNREACHABLE /
   ASSIGNED_TO_NONPOOL_ROW`.
3. The span fits a pool row **and** a non-pool row (AGTI/CCRD-like):
   `IDENTITY_AMBIGUOUS / MULTIPLE_FIT`. A pool-only implementation would
   wrongly assign it.
4. A boundary touch (ABST-like: `end == next start`): `OVERLAPPING_LISTED_ROWS`.
5. Same-start duplicate rows (OKE-like): `OVERLAPPING_LISTED_ROWS`.
6. Strict overlap (ACI-like): `OVERLAPPING_LISTED_ROWS`.
7. An overlap created only by a **non-pool** row (D4-3):
   `OVERLAPPING_LISTED_ROWS`.
8. Disjoint pool rows with the span in the second: the second key is
   `INTERVAL_ASSIGNED` and the first is `HISTORY_UNREACHABLE /
   ASSIGNED_TO_OTHER_ROW`.
9. No fit (CTV-like): `NO_FIT`.
10. EMPTY with a post-C row (HOS-like): `NO_OBSERVATIONS /
    SHADOWED_BY_POST_C_LISTING`. EMPTY without one: `UNEXPLAINED`.
11. `UNREQUESTABLE_IDENTIFIER` and each other non-OK status.
12. An invalid candidate row: `INVALID_SNAPSHOT_ROW`.
13. Tolerance edges: first date exactly `si - 5` fits and `si - 6` does not;
    the same at `ei`. Clipping at A and at C (an active row whose `endDate`
    is after C). A row entirely after C is never containment-eligible.
14. An off-calendar observed date is mapped as defined.
15. `interval_mismatch` lists exactly the rows outside the unclipped listed
    interval of an assigned key.
16. A binding refusal: a pool or snapshot hash that differs from
    `build_inputs` aborts. An unmatched pool row aborts.
17. Determinism: two runs are byte-identical. There is exactly one row per
    pool key, and the counts reconcile.
18. D4 never reads value columns (spy or column-projection check), makes no
    network use, and a credential sentinel is absent.

## Mutation and adversarial tests (must fail the suite)

- comparing against pool rows only;
- dropping the overlap check or treating boundary touches as disjoint;
- ignoring non-pool rows in the overlap check;
- widening the tolerance to 6 or narrowing it to 4;
- assigning on `fit == 1` when that row is not in the pool;
- letting an EMPTY ticker reach `INTERVAL_ASSIGNED`.

## Report

- files changed;
- test counts and a test-to-item mapping;
- the commit SHA;
- the line `sealed files modified: NONE`.
