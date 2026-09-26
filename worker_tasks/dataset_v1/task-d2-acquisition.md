# D2 — Resumable Tiingo EOD acquisition and manifest

- **Base:** master at the planning commit that adds `worker_tasks/dataset_v1/`,
  which descends from `7348a31`.
- **Contract:** `docs/discovery_dataset_v1_contract.md`, SHA-256
  `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`.
- **Governing sections:** contract §§7, 13, 14, 15, 16, 19, 20, 21, 22.
- **Shared rules:** `dataset-v1-wave1-plan.md` (the expected-pool interface,
  ownership, security, stop conditions).

## Scope

Implement `smart_beta/dataset_v1/acquisition.py`. It is the bounded,
resumable acquisition mechanism for the per-ticker Tiingo EOD range. It does
**not** start any acquisition during implementation or tests.

## Required behaviour

### 1. Input
- Read the expected-pool artifact (plan interface) and verify:
  - its `schema`;
  - `contract_sha256` equals the constant above;
  - the SHA-256 of its bytes.

  Any mismatch is a fail-closed error.
- Do **not** import D1 code.

### 2. Request
- One request per `ticker`: `GET {smart_beta.vendors.tiingo.client.BASE_URL}/tiingo/daily/{urllib.parse.quote(ticker)}/prices`,
  with `startDate=<acquisition_start>` and `endDate=<C>`, exactly as
  `TiingoClient.get_eod_prices` builds it.
- Auth uses the `Authorization: Token <key>` header, with the key read at call
  time from `TIINGO_API_KEY` and never logged.
- **Do not modify `smart_beta/vendors/tiingo/client.py`.** Implement an
  injectable transport in D2:

  `(path, params, timeout_s) -> (status:int, raw_bytes:bytes)`

  - The live implementation uses `urllib.request` with a finite timeout.
  - Tests inject fakes.
  - The key must never appear in URLs, exceptions, manifest records or
    stdout.

### 3. Raw storage (contract §14)
- Store the **exact received bytes** as
  `raw/<batch_id>/<ticker>.json.gz` under a caller-supplied root. The default
  root is `~/Developer/personal/smart_beta/data_store/discovery_dataset_v1/`,
  but tests always pass `tmp_path`.
- The gzip must be deterministic (`mtime=0`, fixed filename field).
- A ticker must match `^[A-Z0-9][A-Z0-9.\-]*$`; otherwise it fails closed with
  a typed error and no path is constructed.
- Raw files are immutable: never overwritten. A re-fetch that differs is
  written as `<ticker>.refetch-<n>.json.gz`.

### 4. Response classification
The request's terminal status is determined as follows:
- **200** with a body that parses as a JSON list:
  - an empty list → `EMPTY`;
  - a list whose every element is an object containing `date`, `close`,
    `volume`, `divCash` and `splitFactor` → `OK`;
  - anything else (a non-list, a missing key, or a truncated or partial body
    that fails to parse) → `MALFORMED`.
- For `OK`, record the row count and the first and last `date`. No other
  content validation is done; that is QC in D5.

### 5. Failure handling (contract §15), at most 3 attempts per request
- **Timeout, connection error or 5xx:** retry with exponential backoff, using
  injectable sleep and a bounded maximum delay. After 3 attempts the status is
  `RETRY_EXHAUSTED`.
- **429:** retry with backoff up to 3 attempts. If it is still 429, the batch
  stops cleanly with batch state `QUOTA_EXHAUSTED`. The ticker remains
  `PENDING` (not failed), and the batch is resumable. This reconciles
  contract §15's "429 retry" with "quota exhausted → clean stop".
- **Other 4xx** (entitlement): `ENTITLEMENT_ERROR`, no retry.
- **Interruption** (exception or signal mid-batch): already-written records
  stay intact, and a resume continues from them.

### 6. Resume and idempotence
- A resume on the same `batch_id` skips tickers whose latest status is `OK`
  **and** whose stored file re-hashes to the manifest's raw SHA-256.
- A hash failure on a stored OK file marks the ticker `PENDING` for refetch
  and records `CORRUPT_LOCAL`.
- Running the same batch twice with no work left makes **zero** transport
  calls.

### 7. NONDETERMINISTIC
- Refetching an `OK` ticker happens only in an explicit `refetch` mode.
- If the new body hash differs, keep both files and set the status to
  `NONDETERMINISTIC`.

### 8. `batch_id`
- `batch_id = "b-" + sha256(canonical_json({contract_sha256, snapshot_sha256, expected_pool_sha256, C, created_at_utc}))[:16]`.
- It is created once, persisted in the batch header, and reused on resume.

### 9. Manifest (contract §13)

**Batch header** at `manifest/<batch_id>/batch.json`, containing:
- `batch_id`, `contract_sha256`, `snapshot_sha256`, `expected_pool_sha256`,
  `acquisition_start`, `C`, `created_at_utc`;
- the acquisition-tool code SHA-256 (hash of `acquisition.py` bytes);
- the adapter code SHA-256 (hash of `smart_beta/vendors/tiingo/client.py`
  bytes);
- the manifest schema version.

**Attempt log** at `manifest/<batch_id>/attempts.jsonl`: one canonical-JSON
line per attempt, containing:
- ticker, `security_keys`, endpoint, requested range;
- `retrieved_at_utc` (injectable clock), HTTP status or error class, attempt
  number;
- `raw_sha256`, row count, first and last date where applicable.

**Status summary** at `manifest/<batch_id>/status.json`:
- canonical JSON, sorted by ticker, with the latest terminal status per
  ticker plus batch state;
- `manifest_sha256` is the SHA-256 of `status.json` bytes;
- it is deterministic given the same attempt outcomes and clock.

**Never** include credentials in any manifest file.

### 10. Terminal statuses
`OK`, `EMPTY`, `MALFORMED`, `ENTITLEMENT_ERROR`, `RETRY_EXHAUSTED` and
`NONDETERMINISTIC` are terminal. `PENDING` is non-terminal.

Batch states are `RUNNING`, `COMPLETE` (every ticker has a terminal status),
`QUOTA_EXHAUSTED` and `INTERRUPTED`. **No ticker may silently disappear.**

### 11. API
At minimum:
- `create_batch(pool_path, root, clock) -> batch_id`;
- `run_batch(batch_id, root, transport, sleep, clock, max_requests=None) -> summary`;
- `refetch(...)`.

`max_requests` is a hard cap for later bounded smoke runs; exceeding it stops
the batch cleanly. Provide a CLI under `__main__` that requires an explicit
`--live` flag and an explicit `--max-requests` to touch the network. Tests
never pass `--live`.

## Non-goals

Normalization, missingness classes, identity resolution, QC, the universe,
TrustedInput, delisting scenarios and the reserve firewall (D3–D8). Also
bulk or live acquisition, the smoke test, the Power purchase, any factor work,
and any contract, Phase-6–10 or vendor-client modification.

## Required tests (`tests/test_dataset_v1_acquisition.py`; fixtures under `tests/fixtures/dataset_v1/d2/`)

All tests use a fake transport, `tmp_path`, an injectable clock and a no-op
sleep.

- Expected-pool verification: wrong schema, wrong contract hash, and file-hash
  mismatch all fail.
- Request construction: path, params and header name; the key is never in the
  URL.
- Storage: the exact bytes are stored, gzip is deterministic, the invalid
  ticker pattern is rejected.
- Classification: `OK`, `EMPTY`, `MALFORMED` (non-list, missing key,
  truncated bytes).
- 5xx then success; 5xx ×3 → `RETRY_EXHAUSTED`; timeout ×3.
- 429 ×3 → `QUOTA_EXHAUSTED` with the ticker `PENDING`, then a resume
  completes.
- 4xx → `ENTITLEMENT_ERROR` with a single attempt.
- An interrupted run (the fake raises mid-batch) followed by a resume gives no
  duplicate records and no lost tickers.
- Idempotence: a completed batch re-run makes 0 transport calls.
- Corrupt local OK file → `CORRUPT_LOCAL`, then refetch.
- Refetch mode: a different body → `NONDETERMINISTIC` with both files kept; the
  same body keeps `OK`.
- Manifest determinism: identical inputs, outcomes and clock give an identical
  `status.json` SHA-256; `batch_id` is stable across a resume.
- `max_requests` cap: a clean stop.

## Adversarial tests

- No test opens a socket: `socket.socket.connect` is monkeypatched to raise.
- With `TIINGO_API_KEY` set to a sentinel value inside the test, the sentinel
  appears in no file under the root and nowhere in captured stdout/stderr.
- A fake that returns a partial body with status 200 cannot be recorded as
  `OK`.
- A resume never re-requests a verified OK ticker.

## Acceptance

- All tests pass under the plan's scrubbed command.
- The full suite stays green, with the Tiingo live skip as the only skip.
- Only owned files are changed.
- `client.py` and the contract are untouched.
- There is exactly one completion commit on the task branch.

## Worker final report

- files changed;
- test counts (task file and full suite);
- a mapping from each test to its spec item;
- the completion commit SHA;
- the line `sealed files modified: NONE`;
- any ambiguity raised.
