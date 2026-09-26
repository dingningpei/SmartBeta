# D1 — Candidate-pool builder

- **Base:** master at the planning commit that adds `worker_tasks/dataset_v1/`,
  which descends from `7348a31`.
- **Contract:** `docs/discovery_dataset_v1_contract.md`, SHA-256
  `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`.
- **Governing sections:** contract §§2, 3, 4, 13, 16, 19, 20, 22.
- **Shared rules:** `dataset-v1-wave1-plan.md` (interface, ownership,
  security, stop conditions).

## Scope

Implement `smart_beta/dataset_v1/candidate_pool.py`. It deterministically
transforms a Tiingo supported-tickers snapshot file (the CSV with columns
`ticker, exchange, assetType, priceCurrency, startDate, endDate`) plus a
cutoff C into the expected-pool artifact defined in the plan. It does no
network I/O. Callers supply the snapshot as a local file path.

## Required behaviour

1. **Snapshot binding.** `snapshot_sha256` is the SHA-256 of the input file
   bytes.
2. **Validating C (fail closed).**
   - C must be an NYSE session in the trusted calendar
     (`smart_beta.vendors.tiingo.calendar_source.build_nyse_calendar`).
   - It must be the last NYSE session of its calendar month.
   - It must satisfy C ≥ 2016-01-04 and C ≤ 2027-12-31, because the calendar's
     holiday coverage ends in 2027 (§2, §20).
   - Any failure raises a typed error.
3. **Static filters (§3 rules 1–3):**
   - exchange ∈ {NYSE, NASDAQ, NYSE MKT, AMEX};
   - priceCurrency = USD and assetType = Stock;
   - suffix exclusion: take `segments = ticker.split("-")[1:]`. A row is
     excluded if **any** segment starts with `P`, **or** starts with `WS`, **or**
     equals one of `W`, `U`, `R`, `RT`, `WI`. This formalizes the contract's
     `-P…`, `-WS…`, `-W`, `-U`, `-R`, `-RT`, `-R-W`, `-WI`.
   - Share-class segments such as `A` and `B` are kept, and ADRs get no special
     handling.
   - Rows with an empty or unparsable start or end date are excluded under the
     reason `invalid_dates` and counted.
4. **Listed start (§3 rule 4).** `listed_start = max(startDate, 2015-01-02)`.
5. **Acquisition-pool membership** (§3 "Acquisition pool"). A row that passes
   the static filters is in the pool if and only if:
   - its listed interval overlaps [2015-01-02, C], meaning
     `startDate ≤ C` and `endDate ≥ 2015-01-02`; **and**
   - it can reach listing age 252 inside [2016-01-04, C]. Let S252 be the
     252nd NYSE session counted from `listed_start` inclusive. The condition
     holds if and only if S252 exists in the trusted calendar, S252 ≤ C, and
     S252 < `min(endDate, C)`. That last inequality means an age-252 row can
     exist at some f−1 < f while the security is still listed.

   Age is counted on the trusted calendar only, never from vendor observations
   (N1).
6. **Identity (§4).**
   - `security_key = "TIINGO:" + ticker + ":" + startDate`, using the original
     `startDate`, not the clamped value.
   - Rows are grouped by ticker. A ticker with more than one passing row gets
     `duplicate_ticker = true` and keeps all its intervals.
   - D1 does **not** resolve ambiguity; that belongs to D4.
7. **Artifact.**
   - Emit the plan's canonical expected-pool JSON with `contract_sha256` set to
     the constant above.
   - Include `counts`: total rows, rows after static filters, pool rows, pool
     tickers, duplicate tickers.
   - Include `exclusions`: per-reason counts (`exchange`, `currency`,
     `asset_type`, `suffix`, `invalid_dates`, `no_overlap`,
     `age_unreachable`), each row counted once under the **first** failing
     rule in the order listed.
   - Output must be byte-identical across runs and independent of input row
     order.
8. **API.** At minimum:
   - `build_expected_pool(snapshot_path, C) -> dict`;
   - `write_expected_pool(pool, path) -> sha256`.

   Add a small CLI entry point under `if __name__ == "__main__"` that performs
   **no network** access.

## Non-goals

Fetching the snapshot or any EOD data, identity resolution from EOD rows,
liquidity or universe computation, any factor or return computation, and any
reserve access. No Tushare, no proxy, no contract edits.

## Required tests (`tests/test_dataset_v1_candidate_pool.py`; fixtures under `tests/fixtures/dataset_v1/d1/`)

Use a small **synthetic** supported-tickers CSV fixture. Do not copy vendor
data in bulk.

- Each static filter, including every suffix case: `BAC-P-L`, `XYZ-PA`,
  `ABC-WS`, `ABC-WS-A`, `ABC-W`, `ABC-U`, `ABC-R`, `ABC-RT`, `ABC-R-W`,
  `ABC-WI` are excluded; `BRK-B`, `BF-A` and plain tickers are kept.
- Listed-start clamping.
- The age-252 boundary: a row reaching exactly 252 sessions on C is excluded
  under the strict `<`; one session earlier is included. Also cover a row
  ending before reaching age 252, and a pre-2015 listing.
- Overlap boundaries: an endDate of 2015-01-02 is kept; a startDate after C is
  excluded.
- Duplicate or reused tickers, with the flag set and intervals kept and
  sorted.
- Validation of C: not a session, not month-end, before 2016-01-04, after
  2027-12-31.
- Determinism: shuffled input rows give byte-identical output, and the
  SHA-256 is stable.
- Snapshot hash binding.
- Exclusion accounting follows first-failing-rule order.

## Adversarial tests

- A row with the far-future endDate must be included on its own merits, with
  no preference for survivors. A long-surviving and a short-lived security
  with the same start are both included if both reach age 252.
- No code path opens a network socket: monkeypatch `socket.socket.connect` to
  raise.
- Vendor-missing observations cannot affect age, because D1 reads no
  observations.

## Acceptance

- All tests pass under the plan's scrubbed command.
- The full suite stays green, with the Tiingo live skip as the only skip.
- Only owned files are changed.
- The contract is untouched.
- There is exactly one completion commit on the task branch.

## Worker final report

- files changed;
- test counts (task file and full suite);
- a mapping from each required and adversarial test to the spec item it
  covers;
- the completion commit SHA;
- the line `sealed files modified: NONE`;
- any ambiguity raised.
