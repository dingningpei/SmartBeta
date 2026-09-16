# Phase 4B Worker Task — P4B-8: TiingoPITSource Assembly (Wave 3)

## Background (read this first)

Read `worker_tasks/phase4b/phase4b-plan.md` in full first. This task
starts only after ALL of Wave 2 (P4B-4, P4B-5, P4B-6, P4B-7) is merged and
Barrier 2 is green. Confirm your worktree's `master` merge-base contains
all four Wave 2 modules before starting.

**Your task, P4B-8, is pure composition.** You implement
`TiingoPITSource(PITDataSource)`, wiring the seven abstract methods to the
four Wave 2 mapping modules plus the Wave 1 client/identifiers/calendar
modules. You write no new mapping logic, no new correctness rules — every
substantive decision was made in Waves 1-2. If you find yourself writing
anything that looks like a new business rule here, stop: either it belongs
in one of the already-merged modules (and you've found a real gap to
report, not silently patch around) or it's out of scope for Phase 4B.

**One genuinely new decision belongs here, though:** `PITDataSource`'s
abstract methods take no ticker/universe parameter — but Tiingo has no
practical "give me every security" bulk endpoint under this PoC's access
level. `TiingoPITSource` must therefore be constructed with an explicit,
fixed list of tickers it covers. This is a necessary, PoC-scoped design
decision, not a workaround to hide — document it plainly.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-8-source-assembly`
on branch `phase4b/task-p4b-8-source-assembly`, branched from `master`
after Wave 2 merges. Run tests with `.venv/bin/pytest`.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/vendors/tiingo/source.py`
- `tests/test_tiingo_source.py`
- `tests/fixtures/tiingo/source/` — any fixture files you need

**You must not modify anything else**, including any file under
`smart_beta/vendors/tiingo/` other than `source.py` (all six are
already-merged, frozen inputs to this task), `smart_beta/pit/*`,
`pyproject.toml`, or any fixture subdirectory other than your own.

## The APIs you consume (all already merged — read the actual files)

```python
# smart_beta.pit.source
class PITDataSource(abc.ABC):
    def trading_calendar(self) -> TradingCalendar: ...
    def get_raw_returns(self, start, end) -> pd.DataFrame: ...
    def get_corporate_actions(self, start, end) -> pd.DataFrame: ...
    def get_market_cap(self, start, end) -> pd.DataFrame: ...
    def get_fundamentals(self, start, end, fields) -> pd.DataFrame: ...
    def get_trading_status(self, start, end) -> pd.DataFrame: ...
    def get_listing_info(self) -> pd.DataFrame: ...

# smart_beta.vendors.tiingo.client
class TiingoClient: ...
def replay_transport(recordings) -> Transport: ...

# smart_beta.vendors.tiingo.identifiers
def resolve_stock_id(meta: dict) -> ResolvedIdentifier: ...

# smart_beta.vendors.tiingo.calendar_source
def build_nyse_calendar(start, end) -> TradingCalendar: ...

# smart_beta.vendors.tiingo.returns_and_market_cap
def map_eod_to_raw_returns(eod_rows, stock_id, source_endpoint=...) -> pd.DataFrame: ...
def map_eod_to_market_cap(eod_rows, stock_id, source_endpoint=...) -> pd.DataFrame: ...
def map_eod_to_trading_status(eod_rows, stock_id, source_endpoint=...) -> pd.DataFrame: ...

# smart_beta.vendors.tiingo.corporate_actions
def map_eod_to_corporate_actions(eod_rows, stock_id, source_endpoint=...) -> pd.DataFrame: ...

# smart_beta.vendors.tiingo.fundamentals
def map_asreported_to_fundamentals(stock_id, as_reported_statements, normalized_statements, fields) -> pd.DataFrame: ...

# smart_beta.vendors.tiingo.listing
def map_meta_to_listing_info(meta, eod_rows, stock_id) -> pd.DataFrame: ...
```

(Exact signatures may have shifted slightly during Waves 1-2's actual
implementation — read the real merged files, this is a summary written
before they existed.)

## What to build

```python
from __future__ import annotations
from datetime import date
from typing import Sequence
import pandas as pd

from smart_beta.pit.source import PITDataSource
from smart_beta.pit.calendar import TradingCalendar


class TiingoPITSource(PITDataSource):
    """The first real vendor implementation of PITDataSource. Composes the
    Wave 1/2 Tiingo mapping modules; implements no mapping logic of its
    own. Scoped to a fixed, explicit universe of tickers -- Tiingo has no
    practical bulk "all securities" endpoint under this PoC's access
    level, so PITDataSource's ticker-less method signatures are satisfied
    by iterating this fixed list internally, not by any bulk vendor query.
    """

    def __init__(
        self,
        tickers: Sequence[str],
        client: "TiingoClient | None" = None,
    ) -> None:
        """`client` defaults to a real TiingoClient() (live) if not given;
        tests always inject one built with replay_transport. Fetches
        nothing at construction time -- mirrors PointInTimeView's own
        "construction fetches nothing" discipline (smart_beta/pit/view.py),
        for the same reason: predictable, lazy, and testable without
        network access unless a method is actually called.
        """

    def trading_calendar(self) -> TradingCalendar: ...
        # delegates to vendors.tiingo.calendar_source.build_nyse_calendar

    def get_raw_returns(self, start, end) -> pd.DataFrame: ...
        # for each ticker: client.get_eod_prices, resolve_stock_id via
        # client.get_meta, map_eod_to_raw_returns; concatenate

    def get_market_cap(self, start, end) -> pd.DataFrame: ...
    def get_trading_status(self, start, end) -> pd.DataFrame: ...
    def get_corporate_actions(self, start, end) -> pd.DataFrame: ...
    def get_fundamentals(self, start, end, fields) -> pd.DataFrame: ...
    def get_listing_info(self) -> pd.DataFrame: ...
```

Every method's real implementation is a loop over `self._tickers`,
fetching via `self._client`, resolving `stock_id` via
`resolve_stock_id`, mapping via the relevant Wave 2 function, and
concatenating the per-ticker frames — no other logic. Filter each
concatenated frame to `[start, end]` where the relevant Wave 2 function
doesn't already do so (some may accept an unbounded fetch and need date
filtering applied here; check each one's actual behavior rather than
assuming).

## Fixture inputs

A small, focused set under `tests/fixtures/tiingo/source/` — you do not
need to re-verify each mapping module's own correctness in depth (Waves
1-2 already did that); you need enough to prove correct *composition* and
*delegation*. A single ticker (AAPL) across all seven methods' underlying
endpoints, captured the same way prior tasks did, is likely sufficient.

## Required tests

1. **`isinstance(TiingoPITSource([...], client=...), PITDataSource)`**
   is `True`.
2. **Delegation, per method.** For each of the seven methods, use
   `unittest.mock.patch` (or an equivalent spy) on the corresponding Wave
   2/1 function to confirm `TiingoPITSource` calls it exactly once per
   ticker with sensible arguments — proving no method reimplements
   mapping logic inline instead of delegating.
3. **End-to-end AAPL scenario**, at least for `get_raw_returns` and
   `get_fundamentals`: real fixture data flows through the full
   `TiingoPITSource` call and produces a schema-conformant frame matching
   what calling the underlying Wave 2 function directly would produce.
4. **Schema conformance** for all seven methods' outputs via
   `validate_panel` against their respective schemas.
5. **Construction fetches nothing.** Construct `TiingoPITSource` with a
   transport/client that raises if actually called; confirm construction
   alone does not trigger any call.
6. **Multi-ticker concatenation.** With two tickers configured, confirm
   `get_raw_returns` (or another method) returns rows for both, not just
   one.

## Non-goals

- Do not write any new mapping/business logic — if a Wave 1/2 module's
  API doesn't quite fit what you need here, stop and report the gap
  rather than patching around it in `source.py`.
- Do not implement a compositing/merge layer across multiple vendors —
  `TiingoPITSource` is Tiingo-only; that is explicitly deferred.
- Do not attempt to discover the universe of tickers dynamically — the
  fixed, constructor-supplied list is the frozen PoC-scoped design.

## Acceptance criteria

- All pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/vendors/tiingo/source.py`,
  `tests/test_tiingo_source.py`, and files under
  `tests/fixtures/tiingo/source/`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-8-source-assembly
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4b/task-p4b-8-source-assembly`, touching only the files listed
above.

## When done

Report: (a) the exact `TiingoPITSource` constructor and method
signatures implemented; (b) any gap you found in a Wave 1/2 module's API
while wiring this together (even a minor one — report it rather than
patching around it); (c) test results; (d) `git diff --stat`. Do not
merge, do not touch `master`, do not modify files outside the list above.
