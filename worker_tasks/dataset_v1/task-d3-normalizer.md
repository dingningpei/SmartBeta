# D3 — Normalizer (Discovery Dataset v1, Wave 2)

- **Authority:** contract (SHA-256 `d03f3554…fa80e3`) §§7, 8, 10, 13, 14, 16.10
  and 19; `dataset-v1-wave2-plan.md`. The frozen interface is plan §3.
- **Owned files (new):** `smart_beta/dataset_v1/normalize.py`,
  `tests/test_dataset_v1_normalize.py`, `tests/fixtures/dataset_v1/d3/**`.
- **Launch precondition:** plan §4 P1 (pyarrow available).

## Purpose

Turn a COMPLETE acquisition batch into the canonical, byte-reproducible
normalized build defined in plan §3. The build is keyed by **source record**
(`batch_id`, requested `ticker`) only. D3 never assigns or implies a
security identity.

## Public API

```python
build_normalized(root, batch_id, acquisition_record_path) -> dict   # returns build.json content
```

CLI:

```
python -m smart_beta.dataset_v1.normalize build --root <root> --batch-id <id> --acquisition-record <path>
```

The CLI prints `build_id` and `normalized_hash` only.

## Required behaviour

1. **Preconditions (a typed error before any write if one fails).**
   - `status.json` has `batch_state == "COMPLETE"`.
   - The header and status bind the frozen contract hash.
   - Every expected ticker has a terminal status.
   - The acquisition record file exists; its SHA-256 goes into `build_inputs`.
   - The existing batch files are **read-only** to D3. Nothing under
     `manifest/` or `raw/` is ever written.
2. **Raw verification.** Every raw file referenced by `status.json` (OK and
   EMPTY) must exist, gunzip cleanly and re-hash, after decompression, to its
   recorded `raw_sha256`. On any mismatch, D3 aborts with a typed error that
   names the ticker, and writes no build.
3. **Parsing (strict).**
   - An OK body must be a JSON array of objects, each containing exactly the
     13 contract fields: `date close high low open volume adjClose adjHigh
     adjLow adjOpen adjVolume divCash splitFactor`.
   - `date` must match `^\d{4}-\d{2}-\d{2}T00:00:00\.000Z$`. The session date
     is its first 10 characters.
   - Numeric fields must be JSON numbers or null. `volume` and `adjVolume`
     must be integers or null.
   - Any violation (a missing or extra key, a wrong type, another date form)
     aborts the build with a typed error naming the ticker and
     `raw_row_index`. D3 never coerces and never drops a row silently.
   - An EMPTY body produces zero observations. A missing raw file for an
     UNREQUESTABLE_IDENTIFIER is expected.
4. **Observations.**
   - One row per raw row, with all 13 fields kept at full precision.
   - `raw_row_index` and `raw_sha256` are added.
   - There is **no** fill, **no** date shifting, **no** de-duplication and
     **no** use of `adj*` fields beyond storing them.
   - Rows are written to `normalized/` when `date < 2023-07-03`, and to
     `reserve_sealed/` otherwise.
5. **Observation flags** (`obs_flags`, long format; columns `ticker`,
   `date`, `raw_row_index`, `flag`; sorted by all four; partitioned like
   observations).
   - The flags depend on nothing outside the row and the calendar:
     - `OFF_CALENDAR_ROW`: the date is not a trusted NYSE session. The row is
       kept.
     - `DUPLICATE_DATE`: every row of a ticker that shares a date.
     - `VENDOR_NULL`: `close` or `volume` is null.
     - `LEGIT_ZERO_VOLUME`: `volume == 0` and `close` is not null.
     - `MALFORMED_CA`: `splitFactor` is null, NaN or ≤ 0; or `divCash` is
       null, NaN or < 0; or `divCash > 0` and `close` is null or ≤ 0.
   - D3 does **not** compute NOT_YET_LISTED, ENDED, listed-session
     VENDOR_MISSING, TERMINATION_MISMATCH, INTERVAL_MISMATCH or the > 5% rule
     (plan R-1).
6. **Corporate-action table** (`ca_events`).
   - For each OK ticker, call the Phase-4B
     `map_eod_to_corporate_actions(rows, stock_id=<ticker>, source_endpoint=<manifest endpoint>)`
     on that ticker's raw rows **excluding MALFORMED_CA rows**, which are
     pre-validated so the mapper never raises on them.
   - Add a `batch_id` column.
   - **Drop the mapper's `_ingested_at` column**, which is wall-clock and
     would break reproducibility.
   - Keep every other mapper column unchanged.
   - Sort by (`stock_id`, `effective_date`, `action_type`) and partition by
     `effective_date`.
   - The Phase-4B module is not modified.
7. **`source_summary`:** exactly as plan §3.3, one row per expected
   identifier, with the manifest-consistency invariant enforced.
8. **Build header and reproducibility:** `build.json` as plan §3.2.
   - `build_id` is derived only from `build_inputs`.
   - If `<root>/normalized/<build_id>/` already exists, D3 rebuilds into a
     temporary directory and compares. Identical content succeeds without
     change. Different content raises a typed `NondeterministicBuildError`
     and never overwrites.
   - Writes are atomic: temporary directory, then rename.
9. **Forbidden:**
   - returns of any kind, dollar volume, or statistics of value fields;
   - `security_key` assignment or any identity inference;
   - eligibility, listing age or classification;
   - CORP_ACTION_SUSPECT (this is D5's);
   - provider calls;
   - editing any non-owned file.

## Required tests

All tests use synthetic fixtures under `tmp_path` that follow the D2 manifest
layout.

1. A happy path on a mixed batch (OK, EMPTY, UNREQUESTABLE, multi-row)
   produces all plan-§3 files, and `source_summary` has one row per expected
   identifier.
2. All 13 fields round-trip exactly (floats bit-identical; integers,
   including large volumes).
3. The partition split at 2023-07-03: 2023-06-30 goes to `normalized/` and
   2023-07-03 goes to `reserve_sealed/`, for observations, flags and
   `ca_events`.
4. An `OFF_CALENDAR_ROW` fixture (a row dated on an NYSE holiday, e.g.
   2026-04-03) is retained and flagged, not moved.
5. `DUPLICATE_DATE`: both rows are retained and flagged, in `raw_row_index`
   order.
6. `VENDOR_NULL` and `LEGIT_ZERO_VOLUME` flags.
7. `MALFORMED_CA` for each branch: `splitFactor` 0, negative or NaN;
   negative `divCash`; a dividend with close 0 or null. The build does not
   crash, and the row is excluded from the mapper input but kept in
   observations.
8. `ca_events` equals the direct mapper output minus `_ingested_at`, plus
   `batch_id`, for a split row and a dividend row.
9. Byte-reproducibility: two builds give identical file bytes and an
   identical `build.json`, including when the clock or `TZ` is changed
   between builds (the mapper's wall clock must not leak).
10. Existing-build handling: identical content is a no-op; a tampered
    existing file raises `NondeterministicBuildError` and is not overwritten.
11. A raw hash mismatch, or a missing or corrupt gzip, aborts with no build
    directory created.
12. A strict-parse abort for each of: a missing key, an extra key, a string
    number, a non-midnight date, a float volume.
13. The manifest-consistency invariant: a status `row_count` or `first_date`
    that disagrees with the raw file aborts.
14. A non-COMPLETE batch, or a contract-hash mismatch, is refused.
15. The acquisition-record hash and manifest file hashes appear in
    `build_inputs`. Changing the record changes `build_id`.
16. Structural guarantees:
    - no output column or value contains a `security_key`-style identity
      assignment (only the carried `security_keys` JSON in `source_summary`);
    - `adj*` fields are never read for any derived column;
    - no `manifest/` or `raw/` file is modified: compare hashes before and
      after the build.
17. No network use (socket guard), and a credential sentinel is absent from
    every output and log.

## Mutation and adversarial tests (must fail the suite)

- dropping or shifting the off-calendar row;
- forward-filling a gap;
- de-duplicating dates;
- deriving anything from `adjClose`;
- keeping `_ingested_at`;
- skipping raw re-hashing;
- coercing `"12.3"` to a float;
- putting 2023-07-03 in the development partition.

## Report

- files changed;
- test counts and a test-to-item mapping;
- the commit SHA;
- the line `sealed files modified: NONE`;
- the pyarrow and pandas versions used.
