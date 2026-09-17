# Phase 4C Worker Task — P4C-2: TradabilityPolicy Contract

## Background (read this first)

Read `worker_tasks/phase4c/phase4c-plan.md` in full first. Master is at
`634c31c`.

**Why this task exists.** `smart_beta/data/universe.py::build_tradable_
universe` currently hardcodes four China-A-share-specific trading-status
flags (`is_suspended, is_limit_up, is_limit_down, is_st`, from
`smart_beta.data.schema.TRADING_STATUS_COLS`). Tiingo's PIT trading-status
output (`smart_beta/vendors/tiingo/returns_and_market_cap.py`) has **none**
of them — only `is_zero_volume`. Read both the current
`build_tradable_universe` implementation and Tiingo's trading-status
mapper before writing anything, so your policy implementations reproduce
real behavior, not a guess.

**This task builds the policy contract and both concrete implementations
only.** It does **not** touch `smart_beta/data/universe.py` itself — that
amendment (injecting the policy) is a separate, later task (P4C-5), which
depends on this one. Splitting them this way keeps `universe.py`'s
ownership to exactly one task, in its own wave (see the plan document's
"Ownership collisions" section for why).

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-2-tradability`
on branch `phase4c/task-p4c-2-tradability`, branched from `master` at
`634c31c`. Run tests with `.venv/bin/pytest`.

This is Phase 4C, Wave 1 — one of four parallel tasks. Assume P4C-1,
P4C-3, P4C-4 do not exist in your worktree; do not import from them.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/research_inputs/tradability.py`
- `tests/test_research_inputs_tradability.py`

**Never create, under any circumstance:**
- `smart_beta/research_inputs/__init__.py`

This file is unconditionally and solely owned by P4C-1. P4C-1 not yet
being merged when you start from the Wave-1 base commit is the
**expected** situation under the parallel Wave-1 DAG (P4C-1, P4C-2,
P4C-3, P4C-4 all start from the same commit) — it is not evidence of an
early start. You do not need it to exist: write
`smart_beta/research_inputs/tradability.py` directly. Python's implicit
namespace-package support (PEP 420) means this module imports correctly
as `smart_beta.research_inputs.tradability` with no `__init__.py`
present anywhere in `research_inputs/` — you do not need to verify this
claim, just rely on it.

**You must not modify anything else**, including
`smart_beta/data/universe.py` (P4C-5's file, a later, dependent task —
you build the policy the amendment will consume, you do not perform the
amendment), `smart_beta/data/schema.py`, `smart_beta/vendors/tiingo/*`,
`smart_beta/pit/*`, or `pyproject.toml`.

## What to build

```python
from __future__ import annotations

import abc

import pandas as pd

from smart_beta.config.settings import Settings


class TradabilityPolicy(abc.ABC):
    """Market-specific rule for whether a (date, stock_id) observation is
    tradable. Lives above PITDataSource/DataSource entirely -- this
    module never imports a vendor adapter. Concrete policies decide what
    "tradable" means for their market; the ABC only fixes the shape.
    """

    @abc.abstractmethod
    def evaluate(
        self,
        trading_status: pd.DataFrame,
        listing_info: pd.DataFrame,
        market_cap: pd.DataFrame,
        keys: pd.DataFrame,          # (date, stock_id) pairs to evaluate
        settings: Settings,
    ) -> pd.DataFrame:
        """Returns a frame with the same (date, stock_id) keys as `keys`,
        an `is_tradable` bool column, and MAY carry additional diagnostic
        columns (e.g. flagging listing/delisting uncertainty) -- consumers
        that don't care about diagnostics use only `is_tradable`; nothing
        breaks for them if a policy adds more columns.
        """
```

### `ChinaAShareTradabilityPolicy`

Reproduces **exactly** the logic currently inline in
`build_tradable_universe` (listing age, the four trading-status flags,
bottom-`bottom_mcap_exclude_pct` market-cap exclusion) — extracted, not
rewritten. Read the real current function body and copy its logic
faithfully; do not "improve" it. This is what P4C-5 will wire in as the
*default* policy, and its whole purpose is behavior preservation.

### `USZeroVolumeTradabilityPolicy`

Uses **only** `is_zero_volume` (from Tiingo's real trading-status output)
plus the same listing-age and bottom-cap-exclusion logic (those two parts
are genuinely market-agnostic already). Its own module-level docstring
and this class's docstring must state, in words close to these:
`is_zero_volume is a structurally weaker, different signal than an
exchange-issued suspension flag; this policy does not claim or assume
equivalence.` Its `evaluate` output must include a diagnostic column
(name it clearly, e.g. `delisting_uncertain: bool`) that is `True` for any
`(date, stock_id)` where `listing_info`'s `delist_date` is `NaT` **and**
the trailing observations near that date show sustained `is_zero_volume`
(you do not need to reproduce P4B-7's exact 5-day corroboration constant
here — that logic lives in the vendor adapter; this diagnostic only
surfaces that *some* listing/delisting uncertainty may exist for the
observation, using whatever's already present in the supplied
`trading_status`/`listing_info` frames, without re-deriving vendor
policy). If you find you cannot compute a meaningful version of this
diagnostic from the inputs alone without duplicating vendor logic, it is
acceptable to set it to `False` everywhere with an explicit, honest
docstring note saying so — do not fabricate a stronger signal than the
inputs support.

## Required tests

1. `ChinaAShareTradabilityPolicy.evaluate(...)` produces output
   identical to a direct call to the real, current, unmodified
   `build_tradable_universe` for several of `test_universe.py`'s existing
   fixture scenarios (listing age, delist, each of the four flags,
   bottom-cap exclusion, missing flag/missing market cap) — reproduce
   those exact scenarios in your own test file and assert the same
   `is_tradable` values `build_tradable_universe` produces today.
2. `USZeroVolumeTradabilityPolicy`: a stock with `is_zero_volume=True` on
   a given date is not tradable that date; one with `is_zero_volume=False`
   and otherwise-passing checks is tradable.
3. `USZeroVolumeTradabilityPolicy` never reads or requires
   `is_suspended`/`is_limit_up`/`is_limit_down`/`is_st` — construct a
   `trading_status` frame that has only `is_zero_volume` (no China
   columns at all, matching Tiingo's real shape) and confirm it still
   evaluates correctly. This is the direct regression test for the
   defect this task exists to prevent.
4. The `delisting_uncertain` diagnostic (or its documented always-`False`
   fallback) is present and correctly typed.
5. Both policies: missing/NaN market cap or trading-status data is
   treated conservatively (not tradable), matching the existing
   documented conservative-default behavior.
6. Neither policy mutates any input frame.
7. Both satisfy `isinstance(..., TradabilityPolicy)`.

## Non-goals

- Do not modify `smart_beta/data/universe.py`.
- Do not reproduce P4B-7's exact `_MIN_CORROBORATING_ZERO_VOLUME_DAYS`
  corroboration algorithm here — that stays owned by the vendor adapter;
  this policy only consumes whatever trading-status/listing-info it's
  given.
- Do not import `smart_beta.vendors.tiingo.*` — construct your own
  hand-built test fixtures shaped like Tiingo's real output, not a live
  or replayed vendor call.
- Do not add a third policy or attempt a China/US "unified" flag scheme.

## Acceptance criteria

- All pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/research_inputs/tradability.py` and
  `tests/test_research_inputs_tradability.py` — no `__init__.py` file
  anywhere in the diff.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-2-tradability
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch `phase4c/task-p4c-2-tradability`,
touching only the two files listed above.

## If you discover a contract contradiction

Stop and report it rather than improvising a different contract.

## When done

Report: (a) the exact `TradabilityPolicy` ABC and both implementations'
signatures; (b) confirmation `ChinaAShareTradabilityPolicy` reproduces
`build_tradable_universe`'s real current output exactly, with the
scenarios you checked; (c) whether you could compute a meaningful
`delisting_uncertain` diagnostic or had to use the honest always-`False`
fallback, and why; (d) test results; (e) `git diff --stat`. Do not merge,
do not touch `master`, do not modify files outside the list above.
