# D2-D — Truncated / wire-level HTTP transport failure handling (bounded D2 correction)

- **Base:** master at the commit that adds this file (descends from `0bf5ac5`).
- **Contract:** `docs/discovery_dataset_v1_contract.md`, SHA-256
  `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`. It is
  frozen and unchanged by this task.
- **Governing authority:** contract §15 (a partial or truncated response must
  never be silent; bounded retries), §13 (manifest), the D2 spec
  `task-d2-acquisition.md`, D2-C `task-d2c-unrequestable-identifier.md`, and
  `dataset-v1-wave1-plan.md` (security and stop rules).
- **Owned files (modify only):** `smart_beta/dataset_v1/acquisition.py`,
  `tests/test_dataset_v1_acquisition.py`, `tests/fixtures/dataset_v1/d2/**`.
- **Forbidden:** D1, the contract, the vendor client, every other file, any
  live call, and any change to production data.

## Observed defect

In `make_live_transport`, the `urlopen(...)`/`response.read()` path catches
only `URLError`, `TimeoutError` and `OSError`. A real Tiingo response was
truncated and raised `http.client.IncompleteRead`, which is an
`http.client.HTTPException` and not an `OSError`. It escaped the retry path,
crashed the run and left **no** attempt record.

`exc.read()` in the `HTTPError` branch can raise the same way.

## Required semantics

1. **Narrow exception set.** In the live transport, around **both** read sites
   (`response.read()` and `exc.read()`), catch **exactly** these classes and
   re-raise each as the existing `TransportError`, `from exc`:
   - `http.client.IncompleteRead`
   - `http.client.BadStatusLine` (this includes `RemoteDisconnected`, which is
     already an `OSError`)
   - `http.client.LineTooLong`

   Keep the existing `URLError`/`TimeoutError`/`OSError` handling unchanged.
   **Do not** catch `Exception`, `BaseException` or bare
   `http.client.HTTPException`. `InvalidURL`, `UnknownProtocol`,
   `UnknownTransferEncoding`, `UnimplementedFileMode`, `NotConnected` and
   `ImproperConnectionState` and its subclasses must still propagate: they
   indicate programming or configuration errors, not network failures.

   Document this rationale in a short comment.
2. **Attempt audit.** Every retryable transport failure, the three above
   included, produces an `attempts.jsonl` line through the existing retryable
   path. It records `http_status: null`, `error_class: "TransportError"`, the
   attempt number, the requested range and the timestamp.
   - Add one optional field, `transport_cause`: the class name of the
     underlying exception (for example `"IncompleteRead"`). It holds no
     message text, no URL and no header.
   - Provider HTTP failures keep a non-null `http_status`. Transport failures
     have a null `http_status` and a `transport_cause`. That is how the two
     are distinguished.
   - Never write `raw_sha256`, rows, dates or `raw_file` for a failed attempt.
3. **Retry.** The existing policy is unchanged: at most `MAX_ATTEMPTS` (3)
   with bounded backoff.
   - A truncation followed by success ends `OK`, and both attempts are logged.
   - Truncation on every attempt ends `RETRY_EXHAUSTED`, which is the existing
     terminal status. It never stays PENDING and never crashes, and the batch
     continues to later tickers.
   - **No new status.**
4. **No partial body.** A truncated body never becomes a raw artifact and
   never overwrites or degrades an existing verified OK file.
5. **Compatibility with the existing production batch.** No schema migration
   is needed.
   - Old attempt lines without `transport_cause` remain valid.
   - Resuming an existing batch (header, status and attempts already present)
     works unchanged.
   - Verified OK, EMPTY and UNREQUESTABLE_IDENTIFIER records are skipped with
     zero calls.

## Required tests

Use fake transports or a monkeypatched `urllib.request.urlopen`, `tmp_path`,
an injectable clock, no-op sleep, and a socket-blocking guard.

1. IncompleteRead on attempt 1, then success.
2. The failed attempt is logged with `http_status: null`, `error_class:
   "TransportError"`, `transport_cause: "IncompleteRead"` and no response
   fields.
3. The successful attempt is logged.
4. Only the complete body is persisted, and its hash matches the full body.
5. No raw file exists from the failed attempt.
6. IncompleteRead on all 3 attempts produces 3 logged attempts.
7. That ticker ends `RETRY_EXHAUSTED`, visible and terminal.
8. The next ticker in the same run still proceeds.
9. Resume is deterministic: a second `run_batch` makes zero calls for
   terminal tickers.
10. A verified OK record is not degraded by later truncation (for example
    under refetch).
11. EMPTY is unchanged.
12. UNREQUESTABLE_IDENTIFIER is unchanged.
13. `URLError` handling is unchanged.
14. Timeout handling is unchanged.
15. `InvalidURL` or `ValueError` raised inside the transport are **not**
    retried: they propagate, and the batch becomes INTERRUPTED.
16. A sentinel `TIINGO_API_KEY` appears in no attempt line, status or stderr.
17. Compatibility: an existing-schema batch (attempt lines without
    `transport_cause`, with mixed OK/EMPTY/UNREQUESTABLE/PENDING records)
    resumes and completes correctly.

Also cover `exc.read()` truncation in the HTTPError branch, and
`BadStatusLine` and `LineTooLong` retry.

All existing D2 tests must keep passing.

## Acceptance

- The scrubbed task tests and full suite pass.
- Only owned files are changed.
- There is exactly one new commit on the task branch.

## Report

- files changed;
- test counts;
- a mapping from each test to its item;
- the commit SHA;
- the line `sealed files modified: NONE`;
- the exception-set rationale.
