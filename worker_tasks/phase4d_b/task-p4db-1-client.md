# Phase 4D-B Worker Task — P4DB-1: Tushare Proxy Client (Wave 1)

## Background (read this first)

Read `worker_tasks/phase4d_b/phase4d-b-plan.md` in full before starting —
it is the frozen architecture record for all nine P4DB tasks. Phase 4D-B
implements `TushareAShareSource`, a China A-share
`smart_beta.pit.source.PITDataSource`, backed by Tushare data reached
through a third-party proxy (`https://pcd.mobcvb.cn/tushare/pro`,
`X-API-Key` header auth, token in the `TUSHARE_PROXY_TOKEN` environment
variable — **never** print, log, or commit this token; every fixture you
record must have the token stripped from request URLs/headers before it
is written to disk).

**Your task, P4DB-1, is the transport layer** — one of three Wave 1 tasks
(P4DB-2 identifiers, P4DB-3 calendar; both independent of you and of each
other). This is the lowest layer: it fetches raw JSON from the proxy and
returns it, with all the proxy's own transport quirks handled here and
nowhere else in the adapter. **It does no PIT mapping, no schema
validation, no knowledge-date parsing, no identifier resolution.** Every
later task calls through your `TushareClient` protocol (in fixture-replay
mode for their tests).

**Critical design requirement (plan.md policy 11): the proxy's quirks
must not leak.** Every other Phase 4D-B module depends only on the
abstract `TushareClient` protocol you define here, never on
`ProxyTushareClient` or any proxy-specific exception. This is what lets a
future official-API transport be a drop-in replacement.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-1-client`
on branch `phase4d_b/task-p4db-1-client`, branched from `master` at
`4a3b859`. (This worktree does not exist yet as of this spec being
written.) Run tests with `.venv/bin/pytest`.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/vendors/tushare/__init__.py` (empty marker)
- `smart_beta/vendors/tushare/client.py` — the `TushareClient` protocol
  and shared exception types
- `smart_beta/vendors/tushare/proxy_client.py` — `ProxyTushareClient`,
  the one concrete implementation this phase ships
- `tests/test_tushare_client.py`
- `tests/fixtures/tushare/client/` — fixture files, all under this one
  subdirectory

**You must not modify anything else.** Do not touch `pyproject.toml` (no
new dependency should be needed — `urllib.request`/`json`/stdlib, or
`requests` if it is already a declared dependency; check
`pyproject.toml` first and use whichever is already there, do not add
one). Do not create `identifiers.py`, `calendar_source.py`,
`market_data.py`, `corporate_actions.py`, `fundamentals.py`,
`listing.py`, or `source.py`. Do not touch `smart_beta/pit/*`,
`smart_beta/data/*`, `smart_beta/vendors/tiingo/*`,
`smart_beta/vendors/__init__.py`, or any existing test file.

## API shape to implement

```python
# smart_beta/vendors/tushare/client.py
class TushareAPIError(Exception): ...
class TushareEmptyResponseError(TushareAPIError): ...       # policy 11
class TushareNonDeterministicResponseError(TushareAPIError): ...  # policy 11

class TushareClient(Protocol):
    def fetch(self, api_name: str, **params: str) -> dict: ...
    # Returns the parsed JSON body's `data` payload as
    # {"fields": [...], "items": [[...], ...]} -- or raises.

# smart_beta/vendors/tushare/proxy_client.py
class ProxyTushareClient:
    def __init__(self, base_url: str, token_env_var: str = "TUSHARE_PROXY_TOKEN", ...): ...
    def fetch(self, api_name: str, **params: str) -> dict: ...
```

Adjust names/signatures as real investigation dictates; report any
deviation.

## Required transport policies (plan.md policy 11 — implement all of these)

1. **Bounded retry with backoff on `429`/`503`.** The real proxy returns
   `{"ok": false, "error": "upstream_pool_exhausted"}` (503) and
   `{"ok": false, "error": "rate_limited"}` (429) on cold/contended
   calls; both were observed to succeed on retry within ~10-15 seconds.
   Implement a capped exponential backoff (e.g. base 2-4s, cap ~4
   attempts) and raise `TushareAPIError` (not silently return empty) if
   the ceiling is exceeded.
2. **Date-range validation surfaced, not swallowed.** The proxy enforces
   its own ≤366-day cap on `start_date`/`end_date` pairs and returns
   `{"ok": false, "error": "date_range_too_large", "message": "date
   range exceeds 366 days; paginate the request"}` (HTTP 400). Raise a
   distinguishable `TushareAPIError` subtype or re-raise with this
   message intact — do not retry a 400.
3. **Empty-success handling.** For some endpoints (`adj_factor` was the
   confirmed real case), the proxy returns HTTP 200 with `code: 0` and
   `data.items: []` even when data genuinely exists (confirmed via
   retry). Implement: retry up to the same backoff ceiling as policy 1
   when `items` is empty AND the request's own parameters indicate data
   should plausibly exist (you cannot know this in general — implement
   this as an opt-in `retry_on_empty: bool` parameter to `fetch()`,
   defaulting to `False`, that callers who know their query should be
   non-empty can set); if still empty after retries, raise
   `TushareEmptyResponseError` rather than returning the empty payload
   silently. Document this default-`False` choice: `fetch()` cannot
   distinguish "genuinely no data for this security/period" from "proxy
   flakiness" without caller context.
4. **Canonical-consistency check for fixture recording.** Provide a
   small fixture-recording helper (used by every later task, including
   yourself for your own tests) that, when recording a *new* fixture,
   issues the same request 2-3 times and compares the non-empty payloads
   byte-for-byte; if they differ, raise
   `TushareNonDeterministicResponseError` naming the request and both
   payloads, rather than picking one silently. This does not run during
   normal `replay_transport`-backed test execution (which replays one
   recorded response) — it is a recording-time safeguard only.
5. **`replay_transport`.** Mirror Tiingo's `replay_transport` pattern
   (`smart_beta/vendors/tiingo/client.py` — read it) for offline,
   deterministic tests: a transport that serves recorded fixture files
   keyed by `(api_name, sorted(params.items()))` instead of hitting the
   network. **Zero live network calls in `pytest`** — every test in this
   entire phase, across all nine tasks, runs against fixtures you make
   possible here.
6. **Token handling.** Read `TUSHARE_PROXY_TOKEN` from the environment
   at call time, never at import time (so tests can run without the
   token being set, using `replay_transport`). Never include the token
   in any exception message, log line, or fixture file.

## Fixture inputs

Real specimens confirmed reachable in the 4D-A investigation (verify
they are still reachable at implementation time; if the proxy is
unreachable, escalate rather than fabricating a fixture):
- `daily`, `ts_code=000001.SZ`, `start_date=20230101`, `end_date=20230115`
  — for a basic non-empty response fixture.
- `adj_factor`, `ts_code=000001.SZ`, `trade_date=20230113` — for the
  empty-success retry fixture (record both an initial empty response and
  a subsequent non-empty one if the flakiness reproduces; if it does not
  reproduce at recording time, record what you get and note this in your
  report — do not synthesize a fake empty response).
- Any request with a >366-day range — for the `date_range_too_large`
  fixture.

## Required tests

- `fetch()` against a recorded non-empty fixture returns the parsed
  `{"fields": [...], "items": [...]}` shape correctly.
- `date_range_too_large` raises without retrying (assert retry count).
- A recorded `upstream_pool_exhausted`/`rate_limited` fixture followed by
  a recorded success fixture: `fetch()` retries and returns the success.
- Exceeding the retry ceiling on all-failure fixtures raises
  `TushareAPIError`.
- `retry_on_empty=True` against an empty-then-nonempty fixture pair
  returns the nonempty result; `retry_on_empty=False` (default) returns
  the empty result immediately without retrying.
- The canonical-consistency recording helper raises
  `TushareNonDeterministicResponseError` when fed two differing
  synthetic non-empty payloads for the same request (this can be a unit
  test of the helper in isolation, not a live-network test).
- Token is never present in any exception's `str()` (assert by setting a
  recognizable dummy token value and grepping every raised exception's
  message).

## Non-goals

No PIT schema mapping. No knowledge-date logic. No identifier
resolution. No calendar logic. Do not implement `get_*` methods matching
`PITDataSource` — that is P4DB-8's job, consuming your client.

## Acceptance criteria

- `.venv/bin/pytest` green, including all new tests, with the rest of
  the suite unaffected (run the full suite, not just your new file).
- Zero live network calls during `pytest` (verify by running with network
  disabled, e.g. `--disable-socket` if available, or by inspection).
- No proxy-specific name (`ProxyTushareClient`, `upstream_pool_exhausted`,
  etc.) appears in the docstring of `TushareClient` itself — the protocol
  is transport-neutral.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-1-client
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch `phase4d_b/task-p4db-1-client`,
touching only the files listed above.

## When done

Report: (a) the exact `TushareClient`/`ProxyTushareClient`/exception
signatures you implemented, confirming or explaining any deviation from
this spec's sketch; (b) whether the empty-success flakiness on
`adj_factor` reproduced at recording time, and what you recorded instead
if not; (c) test results; (d) `git diff --stat`; (e) the path of your
fixture-capture script. Do not merge, do not touch `master`, do not
modify files outside the list above.
