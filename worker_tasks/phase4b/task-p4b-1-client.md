# Phase 4B Worker Task — P4B-1: Tiingo HTTP Client (Wave 1)

## Background (read this first)

`smart_beta` completed Phase 3: a vendor-independent point-in-time (PIT)
data foundation under `smart_beta/pit/`, merged and tagged
`phase3-complete` at commit `aae4c72` (383/383 tests green). Phase 4B's
job is the *first real vendor adapter* against that foundation: Tiingo,
covering US equities. Read `worker_tasks/phase4b/phase4b-plan.md` in full
before starting — it is the frozen architecture record for all nine P4B
tasks, including five policies that bind on later tasks but not
meaningfully on this one. You are not expected to re-derive anything from
it; just be aware of the whole shape.

**Your task, P4B-1, is the raw Tiingo HTTP client** — one of three Wave 1
tasks (P4B-2 identifiers, P4B-3 calendar; both independent of you and of
each other). This is the lowest layer: a thin wrapper that fetches
Tiingo's native JSON responses and returns them unmodified. **It does no
PIT mapping, no schema validation, no adjustment, no identifier
resolution.** Every later task calls through this client (in fixture-replay
mode for their tests) rather than talking to Tiingo directly.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-1-client`
on branch `phase4b/task-p4b-1-client`, branched from `master` at
`aae4c72`. (This worktree does not exist yet as of this spec being
written — it will be created before you start.) A venv will exist at
`.venv` with the project installed (`pip install -e ".[dev]"`) and
`.venv/bin/pytest` passing 383/383 before you start. Run tests with
`.venv/bin/pytest`.

This is Phase 4B, Wave 1 — you touch completely disjoint files from P4B-2
and P4B-3. Do not import from either; assume neither exists in your
worktree.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/vendors/__init__.py` (empty marker — this is a new
  multi-vendor package; other vendors, e.g. a future SEC/EDGAR adapter,
  will live alongside `tiingo/` later. Do not add exports.)
- `smart_beta/vendors/tiingo/__init__.py` (empty marker — same reasoning
  as `smart_beta/pit/__init__.py`; multiple Phase 4B tasks will add
  modules under this package across Waves 1-3. Do not add exports.)
- `smart_beta/vendors/tiingo/client.py`
- `tests/test_tiingo_client.py`
- `tests/fixtures/tiingo/client/` — any fixture files you need, all under
  this one subdirectory

**You must not modify anything else.** In particular:
- Do not touch `pyproject.toml` — no new dependency is needed (see below).
- Do not create `identifiers.py`, `calendar_source.py`,
  `returns_and_market_cap.py`, `corporate_actions.py`, `fundamentals.py`,
  `listing.py`, or `source.py` under `vendors/tiingo/` — those are other
  tasks.
- Do not touch anything under `smart_beta/pit/`, `smart_beta/data/`,
  `smart_beta/factors/`, `smart_beta/engines/`, `smart_beta/benchmarks/`,
  `smart_beta/pipelines/`, `smart_beta/config/`, or any existing test file.
- Do not write into any fixture subdirectory other than
  `tests/fixtures/tiingo/client/`.

## What to build

A client with exactly four public data methods, an injectable transport
for testability, and a fixture-replay helper every later task will reuse.
**No new dependency:** use only `urllib.request`/`urllib.parse`/`json`/
`os` from the standard library for the live transport — do not add
`requests` or anything else to `pyproject.toml`.

```python
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Callable, Mapping, MutableMapping

# (path, query_params) -> (http_status_code, parsed_json_body)
Transport = Callable[[str, Mapping[str, str]], "tuple[int, object]"]

BASE_URL = "https://api.tiingo.com"


class TiingoConfigError(Exception):
    """Raised at construction when no API key is available anywhere
    (neither passed explicitly nor via the TIINGO_API_KEY environment
    variable) and no test transport was injected either."""


class TiingoAPIError(Exception):
    """Raised when a call's HTTP status is not 200. Carries enough to
    diagnose without re-issuing the call."""

    def __init__(self, path: str, params: Mapping[str, str], status_code: int, body: object) -> None:
        self.path = path
        self.params = dict(params)
        self.status_code = status_code
        self.body = body
        super().__init__(
            f"Tiingo request to {path} with params={dict(params)} "
            f"returned status {status_code}: {body!r}"
        )


class TiingoClient:
    """Thin wrapper over Tiingo's REST API. Returns Tiingo-native JSON
    shapes verbatim -- no PIT semantics, no schema mapping, no adjustment.
    That is every later Phase 4B task's job, not this client's.
    """

    def __init__(
        self,
        api_key: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        """`api_key` falls back to the `TIINGO_API_KEY` environment
        variable. If neither is set AND no `transport` is injected, raise
        `TiingoConfigError` immediately -- never silently send a
        keyless request. When `transport` IS injected (the fixture-replay
        path every test uses), a missing key is fine: the transport never
        touches the network.
        """

    def get_eod_prices(self, ticker: str, start_date: str, end_date: str) -> list[dict]:
        """Tiingo's daily EOD price endpoint for `ticker` over
        [start_date, end_date] (ISO date strings). Returns the parsed JSON
        array of per-day dicts verbatim -- do not rename, reshape, or drop
        any field. Confirm the exact path/param names against Tiingo's
        official API docs and your recorded fixture; a reasonable starting
        point is `GET /tiingo/daily/{ticker}/prices?startDate=...&endDate=...`,
        but do not treat that as gospel if the real API differs -- verify
        it against a real response you actually captured.
        """

    def get_meta(self, ticker: str) -> dict:
        """Tiingo's security metadata endpoint for `ticker` (a reasonable
        starting point: `GET /tiingo/daily/{ticker}`). Must include
        whatever field(s) P4B-2's `permaTicker` investigation will need --
        confirm the response actually contains a `permaTicker`-like field
        by inspecting a real captured response; if it does not, say so
        plainly in your report (do not guess a field name that isn't
        there).
        """

    def get_fundamentals_asreported(
        self, ticker: str, start_date: str | None = None, end_date: str | None = None
    ) -> list[dict]:
        """Tiingo's fundamentals statements endpoint for `ticker`, called
        WITH the `asReported=true` parameter. Returns the parsed JSON array
        of per-statement dicts verbatim, including whatever line-item
        (`statementData`) and date/fiscal-identity fields Tiingo actually
        returns.
        """

    def get_fundamentals_normalized(
        self, ticker: str, start_date: str | None = None, end_date: str | None = None
    ) -> list[dict]:
        """The SAME endpoint family, called WITHOUT `asReported=true`.
        P4B-6 uses this response's `date` field only, as report-period-end
        metadata -- never its `statementData` values as canonical facts.
        This client method just fetches; it has no opinion about how its
        result is used downstream.
        """


def replay_transport(recordings: Mapping[str, "tuple[int, object]"]) -> Transport:
    """Build a deterministic, network-free `Transport` that returns a
    fixed `(status_code, body)` pair keyed by request path (query params
    are not part of the lookup key -- one fixture scenario calls one
    specific endpoint/ticker combination, so path alone is an
    unambiguous key in practice). Raises `KeyError` with the unmatched
    path if a test calls an endpoint it didn't provide a recording for --
    never returns a silently-empty or fabricated response.

    This is the ONE reusable fixture-replay mechanism every later Phase 4B
    task's tests will import and use; do not let any other task
    reimplement this.
    """
```

The live (non-test) transport must issue a real HTTPS GET via
`urllib.request`, attach the API key as Tiingo's documented auth
mechanism requires (confirm against real docs — typically either a
`Authorization: Token <key>` header or a `token` query parameter; use
whichever the real API actually accepts, verified against one real
successful call), parse the JSON body, and return `(status_code, body)`
without raising — `TiingoClient._call` (or equivalent internal glue) is
where a non-200 status becomes `TiingoAPIError`, not the transport itself.

## Fixture inputs

Capture your own real, recorded specimens under
`tests/fixtures/tiingo/client/` by running each of the four methods once
against the LIVE API (using a real key from the `TIINGO_API_KEY`
environment variable — never commit the key itself, only the response
body) and saving the raw JSON response. You need at minimum:

- AAPL EOD prices spanning its 2020-08-31 4-for-1 split (a short window is
  fine, e.g. 2020-08-20 through 2020-09-05).
- AAPL meta.
- TWTR meta (delisted security — confirms the endpoint still answers for a
  name that no longer trades).
- AAPL fundamentals, `asReported=true`, covering at least fiscal Q3 2026.
- AAPL fundamentals, default/normalized, the same coverage.
- RGEN fundamentals, `asReported=true` — this call is expected to fail
  under current plan-tier access (documented finding: HTTP 400 for
  non-DOW-30 tickers). Capture the actual failing response/status; do not
  simulate it by hand.

Write a small, separate, non-test script (e.g.
`scripts/fetch_tiingo_fixtures.py` at the repo root, or inline instructions
in your final report — your choice, but if you add a script it must live
outside `tests/` so `pytest` never collects or runs it) to make these six
calls and save their raw JSON bodies as files under
`tests/fixtures/tiingo/client/`. This script is not itself required to
have tests; it is a one-time recording tool, not part of the shipped
package's public surface. **If you add this script, note its path clearly
in your final report so later Wave 2 tasks can reuse the same technique.**

## Required tests

1. **Passthrough correctness.** Given a `replay_transport` fixture built
   from your recorded AAPL EOD JSON, `get_eod_prices` returns exactly that
   parsed data (field names, values, ordering all unchanged).
2. **Config error on missing key.** Constructing `TiingoClient()` with no
   `api_key` argument, no `transport`, and (within the test, via
   `monkeypatch`) no `TIINGO_API_KEY` environment variable raises
   `TiingoConfigError`. Constructing it with only a `transport` injected
   and no key at all succeeds (key is irrelevant to the replay path).
3. **Non-200 becomes `TiingoAPIError`, not silently swallowed.** Using your
   recorded RGEN 400 response as a `replay_transport` fixture,
   `get_fundamentals_asreported("RGEN", ...)` raises `TiingoAPIError` with
   `status_code == 400` and the real captured body accessible via
   `exc.body`.
4. **`asReported=true` is actually passed, and only for the asReported
   method.** Using a spy transport (a plain Python function you write in
   the test, not `replay_transport`) that records the `params` dict it was
   called with, assert `get_fundamentals_asreported` passes
   `asReported="true"` (or whatever exact string/bool Tiingo's real API
   expects — confirmed against your recorded fixture's actual successful
   call) and `get_fundamentals_normalized` does not.
5. **`replay_transport` raises `KeyError` for an unrecorded path**, not a
   silent empty/default response.
6. **No live network call anywhere in the test suite.** Every test
   constructs `TiingoClient` with an explicit `transport=` argument; grep
   your own test file to confirm no test omits it.

## Non-goals

- Do not implement any PIT schema mapping, `stock_id` resolution, or
  adjustment logic here — every method returns Tiingo-native shapes only.
- Do not implement retries, rate-limiting, or caching. A production
  hardening pass is explicitly out of scope for this PoC.
- Do not add `requests` or any other new dependency to `pyproject.toml`.
- Do not guess Tiingo's exact endpoint paths or field names without
  verifying them against a real captured response — if something in this
  spec's sketch turns out to be wrong once you look at the real API,
  fix it and say so in your report; do not silently code around a wrong
  assumption without flagging it.

## Acceptance criteria

- All 383 pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly: `smart_beta/vendors/__init__.py`,
  `smart_beta/vendors/tiingo/__init__.py`, `smart_beta/vendors/tiingo/client.py`,
  `tests/test_tiingo_client.py`, files under `tests/fixtures/tiingo/client/`,
  and (optionally) one new script file outside `tests/` if you added one —
  nothing else.
- `pyproject.toml` is unchanged.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-1-client
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch `phase4b/task-p4b-1-client`,
touching only the files listed above.

## When done

Report: (a) the exact `TiingoClient`/`TiingoAPIError`/`TiingoConfigError`/
`replay_transport` signatures you implemented, confirming or explaining
any deviation from this spec's sketch; (b) the real Tiingo endpoint
paths/param names/auth mechanism you confirmed, if different from this
spec's placeholders; (c) test results; (d) `git diff --stat`; (e) the path
of your fixture-capture script, if you wrote one. Do not merge, do not
touch `master`, do not modify files outside the list above.
