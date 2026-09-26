# D2-C — Unrequestable-identifier handling (bounded D2 correction)

- **Base:** master at the commit that adds this file (descends from `975e51e`).
- **Contract:** `docs/discovery_dataset_v1_contract.md`, SHA-256
  `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`. It is
  frozen and unchanged by this task.
- **Governing authority:** contract §§13, 15, 16 ("no silent gaps"), the D2
  spec `task-d2-acquisition.md`, and `dataset-v1-wave1-plan.md` (security and
  stop rules).
- **Owned files (modify only):** `smart_beta/dataset_v1/acquisition.py`,
  `tests/test_dataset_v1_acquisition.py`, `tests/fixtures/dataset_v1/d2/**`.
- **Forbidden:** D1 (`candidate_pool.py` and its tests), the contract, the
  vendor client, and every other file.

## Problem

`create_batch` calls `validate_ticker` on every expected-pool identifier. It
aborts the **whole batch** when one identifier fails the request-path pattern
`^[A-Z0-9][A-Z0-9.\-]*$`.

The real D1 production pool contains identifiers with spaces or slashes, for
example `SO 6.75 08-01-22` and `BC/PA`. D1 correctly preserves vendor
identifiers verbatim.

## Required semantics

1. **New terminal disposition `UNREQUESTABLE_IDENTIFIER`.**
   - It means only: "a provider identifier present in the frozen D1 expected
     pool that the D2 request path cannot safely represent or request."
   - It carries **no** security-type meaning: not invalid, not non-common, not
     a warrant, preferred or note, and not ineligible.
   - Add it to `TERMINAL_STATUSES`.
2. **`create_batch` must not abort.**
   - Every pool identifier is kept, with its original string preserved exactly.
   - An identifier that fails `TICKER_PATTERN` is recorded in the header's
     `tickers` entries with `requestable: false`. Requestable entries get
     `requestable: true`.
   - Its initial status record is:
     `{"status": "UNREQUESTABLE_IDENTIFIER", "reason_code": "REQUEST_PATTERN_MISMATCH", "request_pattern": TICKER_PATTERN.pattern, "attempts": 0}`.
   - That record must **not** contain `http_status`, `raw_file`, `raw_sha256`,
     `row_count`, `first_date`, `last_date` or `retrieved_at_utc`. Those fields
     require a real response; omit them.
   - Do not hard-code identifiers or counts; the generic pattern check decides.
3. **No request, no file.** An UNREQUESTABLE_IDENTIFIER never reaches the
   transport. No raw path is built and no raw file is written. In
   `attempts.jsonl` either no attempt line is written for it, or exactly one
   creation-time disposition line with `attempt: 0` and no response fields.
   Choose one and test it.
4. **Resume and idempotence.** On every `run_batch` or resume it is skipped:
   zero transport calls, no new attempt lines, and its record unchanged.
5. **Refetch.**
   - The default selection (OK tickers) never includes it.
   - An explicit `refetch(tickers=[<unrequestable>])` raises a typed error
     **before** any transport call and makes no state change. Existing
     verified OK records remain untouched, as in the `d947ad9` guarantee.
6. **Batch state and reconciliation.**
   - The expected set equals the status set.
   - UNREQUESTABLE_IDENTIFIER counts as terminal for `COMPLETE`, but it is
     not OK, not EMPTY, not a provider failure and not a request. The summary
     `counts` show it separately.
   - `requests_made` excludes it.
7. **Unchanged:** batch_id computation, header bindings (contract, snapshot,
   pool hashes), requestable-ticker behaviour, retry, quota, NONDETERMINISTIC
   and CORRUPT_LOCAL.

## Required tests

Use fake transport, `tmp_path`, an injectable clock, no-op sleep, and a
socket-blocking guard.

1. One requestable identifier plus one with a space: the batch is created,
   and the space identifier is UNREQUESTABLE_IDENTIFIER.
2. A slash identifier (`BC/PA`) is UNREQUESTABLE_IDENTIFIER.
3. `create_batch` succeeds, and the header lists both identifiers with the
   correct `requestable` flag.
4. The status record has the exact fields in item 2 and none of the response
   fields.
5. The transport spy records zero calls for the unrequestable identifier.
6. No raw file or path exists for it: the raw directory listing contains only
   requestable files.
7. The expected set equals the status set, and COMPLETE is reached when every
   requestable ticker is terminal.
8. A resume (a second `run_batch`) makes zero calls and adds no attempt lines
   for it; the record is byte-identical.
9. Ordinary requestable identifiers still go through OK, EMPTY and 5xx retry
   normally in a mixed batch.
10. A verified OK record is unaffected by the new path, including an explicit
    refetch of an unrequestable identifier, which raises before any call.
11. The original identifier string is preserved exactly (spaces, slash) in
    the header and status.
12. No security-type inference: the record has no type or classification
    keys, and `reason_code` is exactly `REQUEST_PATTERN_MISMATCH`.

**Adversarial test:** a pool made only of unrequestable identifiers reaches
COMPLETE with zero transport calls.

Keep every existing D2 test passing unchanged. The obsolete expectation that
`create_batch` raises on an invalid pool ticker may be replaced by the new
semantics; explain this in the report.

## Acceptance

- The scrubbed task tests and full suite pass (see the plan's command).
- Only owned files are changed.
- There is exactly one new commit on the task branch.

## Report

- files changed;
- test counts;
- a mapping from each test to its item;
- the commit SHA;
- the line `sealed files modified: NONE`;
- which attempts.jsonl choice was made (item 3).
