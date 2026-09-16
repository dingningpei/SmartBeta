# Phase 4B Worker Task — P4B-4: Raw Returns, Market Cap, Trading Status (Wave 2)

## Background (read this first)

Read `worker_tasks/phase4b/phase4b-plan.md` in full first. This task
starts only after Wave 1 (P4B-1 client, P4B-2 identifiers, P4B-3 calendar)
is merged to `master` and Barrier 1 is green. Confirm your worktree's
`master` merge-base actually contains
`smart_beta/vendors/tiingo/{client,identifiers}.py` before starting; if it
does not, stop and report rather than proceeding against a stale base.

**Your task, P4B-4, maps Tiingo's EOD price data to three of
`PITDataSource`'s seven methods' worth of raw facts:** `get_raw_returns`,
`get_market_cap`, and `get_trading_status`. All three are owned by this
one task because, for Tiingo/US equities, all three are derived from the
*same* EOD row — unlike China A-shares (the original `smart_beta` design
target), there is no separate suspension/limit-up/limit-down/ST data
source in scope here; the only trading-status signal available is
volume-derived.

**The concrete trap this task exists to handle correctly:** a delisted
security's Tiingo history can include a terminal EOD row with
`volume == 0`. That row is real data Tiingo actually returned — it must
never be silently dropped — but its presence must never be read as
evidence the security was actually tradable that day. This task is where
that distinction becomes code, not just a documented intention.

This is one of four Wave 2 tasks (P4B-5 corporate actions, P4B-6
fundamentals, P4B-7 listing). You touch completely disjoint files from all
three; do not import from them.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-4-returns-market-cap`
on branch `phase4b/task-p4b-4-returns-market-cap`, branched from `master`
after Wave 1 merges. Run tests with `.venv/bin/pytest`.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/vendors/tiingo/returns_and_market_cap.py`
- `tests/test_tiingo_returns_market_cap.py`
- `tests/fixtures/tiingo/returns_market_cap/` — any fixture files you need

**You must not modify anything else**, including
`smart_beta/vendors/tiingo/{client,identifiers,calendar_source}.py`,
`smart_beta/pit/*`, any sibling Wave-2 module
(`corporate_actions.py`, `fundamentals.py`, `listing.py` do not exist yet
— other tasks own them), `pyproject.toml`, or any fixture subdirectory
other than your own.

## The APIs you consume (already merged — read the actual files, this is a summary)

```python
# smart_beta.vendors.tiingo.client
class TiingoClient:
    def get_eod_prices(self, ticker: str, start_date: str, end_date: str) -> list[dict]: ...
def replay_transport(recordings: Mapping[str, tuple[int, object]]) -> Transport: ...

# smart_beta.vendors.tiingo.identifiers
@dataclass(frozen=True)
class ResolvedIdentifier:
    stock_id: str
    is_permanent: bool
    source_field: str
def resolve_stock_id(meta: dict) -> ResolvedIdentifier: ...

# smart_beta.pit.schema
from smart_beta.pit.schema import (
    DATE_COL, STOCK_COL, RAW_RETURN_COL, PIT_RAW_RETURN_PANEL_SCHEMA,
    FLOAT_MARKET_CAP_COL, TOTAL_MARKET_CAP_COL, PIT_MARKET_CAP_SCHEMA,
    PIT_TRADING_STATUS_SCHEMA, validate_panel,
)
```

Recall `PIT_TRADING_STATUS_SCHEMA` only enforces `(date, stock_id)` as key
columns — flag columns beyond that are open-ended/per-vendor by design
(`smart_beta/pit/schema.py:160-164`). This is what lets you add an
adapter-specific flag without touching Phase 3 at all.

## What to build

```python
from __future__ import annotations
from typing import Sequence
import pandas as pd

_PROVENANCE_VENDOR = "tiingo"

def map_eod_to_raw_returns(
    eod_rows: list[dict], stock_id: str, source_endpoint: str = "get_eod_prices",
) -> pd.DataFrame:
    """PIT_RAW_RETURN_PANEL_SCHEMA-conforming, plus provenance columns
    `_source_vendor`, `_source_endpoint`, `_ingested_at`.

    Computes simple returns from consecutive raw `close` values (NOT
    `adjClose` -- this adapter is a RAW source; adjustment is exclusively
    `compute_adjusted_returns`'s job, applied downstream, never here).
    Confirm the exact Tiingo field name for the raw close against your
    fixture. The first row of any contiguous series has no prior close and
    is excluded (no return is defined for it) -- document this explicitly,
    do not emit a NaN or a zero.

    `eod_rows` is assumed sorted by date; if it is not, sort it defensively
    (do not trust caller ordering silently).
    """

def map_eod_to_market_cap(
    eod_rows: list[dict], stock_id: str, source_endpoint: str = "...",
) -> pd.DataFrame:
    """PIT_MARKET_CAP_SCHEMA-conforming, plus provenance columns.

    INVESTIGATE FIRST: does Tiingo expose a market-cap or
    shares-outstanding figure at all under current plan-tier access (a
    plausible source is a Tiingo fundamentals "daily metrics" endpoint,
    NOT the basic EOD price endpoint, which typically has no share-count
    field)? If you need a new TiingoClient method to fetch it, you may NOT
    add one yourself -- TiingoClient is P4B-1's file, already merged and
    frozen for this wave. If a genuinely necessary method is missing,
    STOP and report this as a blocking gap rather than reaching around the
    boundary; do not monkeypatch or duplicate client logic inline here.

    If a total-market-cap-like figure IS obtainable: use it as
    `total_mcap`. Tiingo is NOT expected to expose a float-adjusted figure
    distinct from total market cap for US equities (unlike China A-shares,
    the original design target, where the distinction is critical). If
    that is what you find: set `float_mcap = total_mcap` explicitly, and
    say so in the module docstring in these words: "float_mcap is set
    equal to total_mcap; Tiingo does not expose a distinct float-adjusted
    figure under current access. This is a named approximation, not a
    verified equivalence." Do NOT silently invent a discount factor.

    If NO market-cap-capable data is obtainable at all under current
    access: fail closed -- raise a clearly-named exception rather than
    fabricating or estimating a value -- and report this plainly; do not
    let `get_market_cap` return a schema-conformant but made-up number.
    """

def map_eod_to_trading_status(
    eod_rows: list[dict], stock_id: str, source_endpoint: str = "get_eod_prices",
) -> pd.DataFrame:
    """PIT_TRADING_STATUS_SCHEMA-conforming, plus provenance columns and
    one adapter-specific flag column: `is_zero_volume` (bool), True where
    the row's volume field is exactly 0, else False. This is the ONLY
    trading-status signal this adapter provides -- do not invent
    `is_suspended`/`is_limit_up`/`is_limit_down`/`is_st` values; either
    omit those canonical China-A-share-oriented flag columns entirely
    (permitted -- they are not required by PIT_TRADING_STATUS_SCHEMA) or,
    if you include them for structural consistency with a future
    multi-market caller, set them to False everywhere and say so plainly
    in the docstring -- never leave it ambiguous which is happening.

    A zero-volume row is PRESERVED in this function's output (every row of
    `eod_rows` gets a trading-status row) -- this function's job is to
    FLAG the condition, not decide anything about survivorship or
    delisting; that reasoning belongs to P4B-7, which corroborates a
    *sustained* zero-volume tail before asserting a delist_date. This
    function operates purely per-row, with no memory of history.
    """
```

## Fixture inputs

Capture, under `tests/fixtures/tiingo/returns_market_cap/`, using
`TiingoClient` (already merged) fed a real key, or by reusing raw JSON you
can independently verify:

1. AAPL EOD rows spanning the 2020-08-31 split window (reuse/re-record;
   you do not need to import P4B-1's own fixture files, capture your own
   copy in your own subdirectory).
2. TWTR EOD rows including its terminal zero-volume row(s) before
   delisting.
3. Whatever endpoint you determine provides market-cap-capable data (or
   a fixture proving it does NOT, if that's your finding) for at least
   AAPL.
4. A short, hand-constructable (not necessarily fetched) EOD row sequence
   with one isolated zero-volume day in the *middle* of otherwise-normal
   trading (an illiquid day, not a delisting) — used to prove your
   trading-status mapping doesn't conflate the two situations, which is
   exactly P4B-7's later concern but worth a clean, isolated unit test
   here at the row-flagging level too.

## Required tests

1. **Return computation correctness**, AAPL, at least one ordinary day —
   hand-computed expected value from the recorded `close` values.
2. **First-row exclusion.** The earliest date in a fixture's series has no
   raw-return row.
3. **TWTR zero-volume row is present in `get_raw_returns`'s AND
   `get_trading_status`'s output, never dropped.** The return value on
   that row reflects whatever Tiingo's actual close-to-close price change
   was (likely ~0 if the price literally did not move, but assert the
   real observed value, not an assumed one) — the point of this test is
   presence and correct flagging, not a specific return value.
4. **`is_zero_volume` is `True` exactly on zero-volume rows and `False`
   elsewhere**, using both the TWTR terminal-row fixture and the isolated
   mid-history zero-volume fixture — confirming the flag fires
   consistently regardless of *where* in the series the zero-volume day
   falls (this function has no delisting logic; that distinction is
   P4B-7's job, not this one's).
5. **Market cap:** either (a) a passing test demonstrating correct
   `total_mcap`/`float_mcap` values against your fixture, with an explicit
   assertion (and docstring note) if `float_mcap == total_mcap` by
   design, or (b) if market-cap data is genuinely unobtainable, a test
   confirming `map_eod_to_market_cap` raises your named exception rather
   than returning fabricated data — pick whichever matches your actual
   investigation's outcome, but the test suite must not be silent about
   which one it is.
6. **Schema conformance** for all three functions' outputs via
   `validate_panel`.
7. **Provenance columns present** on all three functions' outputs
   (`_source_vendor == "tiingo"`, `_source_endpoint` set, `_ingested_at`
   set) — and confirm (by reading `smart_beta/pit/corporate_actions.py`
   and `smart_beta/pit/fundamentals.py`, already merged) that these extra
   columns do not break `validate_panel`'s existing behavior, since it
   only checks required columns.

## Non-goals

- Do not implement `get_corporate_actions`, `get_fundamentals`, or
  `get_listing_info` — other tasks.
- Do not implement delisting-corroboration logic — that is P4B-7's job;
  this task's trading-status output is purely per-row.
- Do not add a new `TiingoClient` method yourself if you find you need
  one — stop and report instead (see the market-cap investigation note
  above).

## Acceptance criteria

- All pre-existing tests (383 plus whatever Wave 1 added) continue to
  pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/vendors/tiingo/returns_and_market_cap.py`,
  `tests/test_tiingo_returns_market_cap.py`, and files under
  `tests/fixtures/tiingo/returns_market_cap/`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-4-returns-market-cap
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4b/task-p4b-4-returns-market-cap`, touching only the files listed
above.

## When done

Report: (a) the exact function signatures implemented; (b) your
market-cap investigation's outcome (obtainable/approximated/unobtainable,
and which); (c) test results; (d) `git diff --stat`. Do not merge, do not
touch `master`, do not modify files outside the list above.
