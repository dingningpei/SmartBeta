# Phase 4B Worker Task — P4B-7: Listing/Delisting with Corroboration (Wave 2)

## Background (read this first)

Read `worker_tasks/phase4b/phase4b-plan.md` in full first. This task
starts only after Wave 1 is merged and Barrier 1 is green; confirm your
worktree's `master` merge-base contains
`smart_beta/vendors/tiingo/{client,identifiers}.py` before starting.

**Your task, P4B-7, maps Tiingo's security metadata into
`PIT_LISTING_INFO_SCHEMA`, and owns the delisting-corroboration policy.**
The concrete trap: Tiingo's raw `endDate` metadata field for a security is
not, by itself, proof that the security was actually delisted on that
date — it can just mean "last date we have a row for," which (per P4B-4's
findings) can include a terminal zero-volume EOD row. Asserting
`delist_date` directly from `endDate` would risk conflating "last recorded
row" with "confirmed delisting," which is exactly the survivorship-bias
risk Phase 3's compliance suite (`smart_beta/pit/compliance.py`, already
merged) is designed to catch — this task's job is to not hand it a
mistake to catch.

This is one of four Wave 2 tasks (P4B-4 returns/market cap, P4B-5
corporate actions, P4B-6 fundamentals). You touch completely disjoint
files from all three. **You do not depend on P4B-4's code** — you inspect
raw EOD rows yourself, independently, for the corroboration check below,
rather than importing P4B-4's mapped output (both tasks are Wave 2,
parallel, no dependency between them; the shared `is_zero_volume`
*policy* — a row's volume field being exactly 0 — is frozen here and in
P4B-4's spec identically, so both can be built without either depending on
the other's code).

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-7-listing`
on branch `phase4b/task-p4b-7-listing`, branched from `master` after Wave
1 merges. Run tests with `.venv/bin/pytest`.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/vendors/tiingo/listing.py`
- `tests/test_tiingo_listing.py`
- `tests/fixtures/tiingo/listing/` — any fixture files you need

**You must not modify anything else**, including
`smart_beta/pit/view.py` (Phase 3, finished — read it, do not touch it;
`AsOfSnapshot.listing_info()` masks a future `delist_date` only
`if DELIST_DATE_COL in known.columns`, at `smart_beta/pit/view.py:199` —
this is why `delist_date` must be a real, populated column in your
output even though `PIT_LISTING_INFO_SCHEMA` does not enforce its dtype),
`smart_beta/vendors/tiingo/{client,identifiers,calendar_source,
returns_and_market_cap,corporate_actions,fundamentals}.py`,
`pyproject.toml`, or any fixture subdirectory other than your own.

## The APIs you consume (already merged — read the actual files, this is a summary)

```python
# smart_beta.vendors.tiingo.client
class TiingoClient:
    def get_meta(self, ticker: str) -> dict: ...
    def get_eod_prices(self, ticker, start_date, end_date) -> list[dict]: ...
def replay_transport(recordings) -> Transport: ...

# smart_beta.vendors.tiingo.identifiers
def resolve_stock_id(meta: dict) -> ResolvedIdentifier: ...

# smart_beta.pit.schema
from smart_beta.pit.schema import (
    STOCK_COL, LIST_DATE_COL, DELIST_DATE_COL, PIT_LISTING_INFO_SCHEMA,
    validate_panel,
)
```

Note `PIT_LISTING_INFO_SCHEMA`'s dtypes only require `stock_id` and
`list_date` (`smart_beta/pit/schema.py:155-158`) — `delist_date` is not
schema-enforced, but is required in practice for `AsOfSnapshot` to mask
future delistings correctly (see above). Include it always, as a real
`pd.Timestamp` or `pd.NaT` column, never omitted.

## What to build

```python
from __future__ import annotations
import pandas as pd

_MIN_CORROBORATING_ZERO_VOLUME_DAYS = 5  # named constant, not a magic number

def map_meta_to_listing_info(
    meta: dict, eod_rows: list[dict], stock_id: str,
) -> pd.DataFrame:
    """PIT_LISTING_INFO_SCHEMA-conforming (stock_id, list_date, delist_date),
    plus provenance columns.

    list_date = meta's start-date field (confirm exact field name against
    your fixture -- a plausible candidate is `startDate`).

    delist_date is asserted ONLY when corroborated. Corroboration rule:
    among `eod_rows` sorted by date, find the trailing run of consecutive
    rows ending at meta's end-date field (confirm exact field name --
    plausible candidate `endDate`) that all have volume == 0. If that run
    has length >= _MIN_CORROBORATING_ZERO_VOLUME_DAYS trading rows AND no
    row in `eod_rows` exists with a date after meta's end-date field,
    delist_date = that end-date. Otherwise, delist_date = pd.NaT.

    A single isolated zero-volume day at the tail is NOT sufficient
    corroboration on its own -- this is the exact case that must NOT be
    misread as delisting.

    If Tiingo's meta response turns out to expose an explicit
    delisting-status field (confirm by inspecting a real response; do not
    assume one exists without checking) that is MORE authoritative than
    this volume-based heuristic, use it instead and document the change --
    but do not assume it exists without verifying.

    Never mutates `eod_rows`.
    """
```

## Fixture inputs

Capture, under `tests/fixtures/tiingo/listing/`:

1. **TWTR meta + EOD rows** including its terminal zero-volume run before
   delisting (enough rows to see the actual length of that run — capture
   at least 10 trading days of trailing history, not just the final day).
2. **AAPL meta + EOD rows** (active security — `endDate` should be
   recent/absent-of-delisting-signal, `delist_date` must resolve to
   `NaT`).
3. **A constructed (labeled-as-such) adversarial fixture**: an EOD row
   sequence with exactly one isolated zero-volume day mid-series, followed
   by normal nonzero-volume trading resuming, with `meta['endDate']` set
   to a date well AFTER that isolated day (i.e., the security is not
   actually near its listed end-date at the isolated zero-volume point) —
   used to prove the corroboration window only looks at the trailing run
   ending at the real end-date, not any zero-volume day anywhere in
   history.
4. **A constructed (labeled-as-such) adversarial fixture**: a trailing run
   of zero-volume days shorter than `_MIN_CORROBORATING_ZERO_VOLUME_DAYS`
   at the tail — used to prove insufficient corroboration correctly
   yields `NaT`, not a guess.

## Required tests

1. **TWTR**: `delist_date` correctly resolves to the real recorded
   end-date, given the real corroborating trailing run.
2. **AAPL**: `delist_date` is `NaT`; `list_date` correctly parsed from the
   real meta.
3. **Isolated mid-history zero-volume day (fixture #3) does not trigger a
   delisting assertion** — `delist_date` is `NaT` despite a zero-volume
   day existing somewhere in the series.
4. **Insufficient trailing run length (fixture #4) does not trigger a
   delisting assertion** — `delist_date` is `NaT`, and this is distinct
   from test 3 (this one has the SHORT run ending exactly at the tail;
   test 3 has an isolated day NOT at the tail — make sure your test names
   and assertions make the distinction clear, since both produce the same
   `NaT` outcome for different reasons and a reviewer needs to see that
   both reasons are actually exercised).
5. **Schema conformance** via `validate_panel`, including confirming
   `delist_date` is present as a real column (not merely absent-and-thus-
   schema-compliant) for both the TWTR and AAPL cases.
6. **Provenance columns present.**
7. **No input mutation** of `eod_rows`.

## Non-goals

- Do not implement `get_raw_returns`, `get_market_cap`,
  `get_trading_status`, `get_corporate_actions`, or `get_fundamentals`.
- Do not import P4B-4's `returns_and_market_cap.py` — inspect raw EOD
  rows directly, independently, per the corroboration rule above.
- Do not lower `_MIN_CORROBORATING_ZERO_VOLUME_DAYS` to make a fixture
  pass more easily — pick the fixtures to match the frozen constant, not
  the other way around.

## Acceptance criteria

- All pre-existing tests (383 plus Wave 1's additions) continue to pass
  unchanged.
- All required tests above pass, distinguishing the two different `NaT`
  reasons (tests 3 and 4) explicitly.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/vendors/tiingo/listing.py`,
  `tests/test_tiingo_listing.py`, and files under
  `tests/fixtures/tiingo/listing/`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-7-listing
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch `phase4b/task-p4b-7-listing`,
touching only the files listed above.

## When done

Report: (a) the exact meta field names you used for start/end date; (b)
whether Tiingo exposes an explicit delisting-status field, and whether
you used it instead of the volume-heuristic; (c) test results; (d)
`git diff --stat`. Do not merge, do not touch `master`, do not modify
files outside the list above.
