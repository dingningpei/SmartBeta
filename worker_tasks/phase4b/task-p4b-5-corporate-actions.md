# Phase 4B Worker Task — P4B-5: Corporate Actions (Wave 2)

## Background (read this first)

Read `worker_tasks/phase4b/phase4b-plan.md` in full first. This task
starts only after Wave 1 is merged and Barrier 1 is green; confirm your
worktree's `master` merge-base contains
`smart_beta/vendors/tiingo/{client,identifiers}.py` before starting.

**Your task, P4B-5, maps Tiingo's split/dividend data into
`CORPORATE_ACTIONS_SCHEMA`.** This is a *raw-facts-only* mapping — the
frozen Phase 3 architecture (`smart_beta/pit/corporate_actions.py`,
already merged) is the ONE place adjustment happens. You never call it,
never reimplement it, and never pre-adjust anything here.

**The one genuinely tricky correctness point in this task is the
dividend adjustment-factor formula**, and it is frozen below, not left for
you to derive under time pressure — read it carefully, because it differs
from the split case in a way that matters.

**A second, separate requirement, added after review: proving the formula
is algebraically correct is not the same as proving Tiingo's `divCash`
field means what the formula assumes.** The formula's correctness is a
closed mathematical fact given its inputs; whether Tiingo's `divCash` is
actually recorded on the true ex-dividend date, with the true per-share
amount, is a question about the vendor's data, and algebra cannot answer
it. You must establish this with independent evidence before treating
`divCash` as a canonical raw fact — see "Vendor semantics validation"
below, which is a distinct, required step from the formula's own
correctness test.

This is one of four Wave 2 tasks (P4B-4 returns/market cap, P4B-6
fundamentals, P4B-7 listing). You touch completely disjoint files from all
three.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-5-corporate-actions`
on branch `phase4b/task-p4b-5-corporate-actions`, branched from `master`
after Wave 1 merges. Run tests with `.venv/bin/pytest`.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/vendors/tiingo/corporate_actions.py`
- `tests/test_tiingo_corporate_actions.py`
- `tests/fixtures/tiingo/corporate_actions/` — any fixture files you need

**You must not modify anything else**, including
`smart_beta/pit/corporate_actions.py` (Phase 3, finished — you never call
or reimplement `compute_adjusted_returns`; this task produces only raw
facts for it to later consume), `smart_beta/vendors/tiingo/{client,
identifiers,calendar_source,returns_and_market_cap}.py`, `fundamentals.py`
or `listing.py` (sibling tasks, do not exist in your worktree yet),
`pyproject.toml`, or any fixture subdirectory other than your own.

## The APIs you consume (already merged — read the actual files, this is a summary)

```python
# smart_beta.vendors.tiingo.client
class TiingoClient:
    def get_eod_prices(self, ticker, start_date, end_date) -> list[dict]: ...
def replay_transport(recordings) -> Transport: ...

# smart_beta.vendors.tiingo.identifiers
def resolve_stock_id(meta: dict) -> ResolvedIdentifier: ...

# smart_beta.pit.schema
from smart_beta.pit.schema import (
    STOCK_COL, EFFECTIVE_DATE_COL, ACTION_TYPE_COL, ADJUSTMENT_FACTOR_COL,
    KNOWLEDGE_DATE_COL, IS_SUPERSEDED_COL, CORPORATE_ACTIONS_SCHEMA,
    validate_panel,
)

# smart_beta.pit.corporate_actions (READ ONLY -- do not call this from
# this task's production code; it is here so you understand exactly what
# formula your adjustment_factor values feed into)
def compute_adjusted_returns(raw_returns, corporate_actions, as_of, calendar=None, settings=...) -> pd.DataFrame:
    """adjusted_ret = (1 + raw_ret) * factor - 1, where factor is the
    product of adjustment_factor over every visible action for that
    (stock_id, effective_date)."""
```

`CORPORATE_ACTIONS_SCHEMA`'s key is
`(stock_id, effective_date, action_type, knowledge_date)`. Tiingo's EOD
response has no separate announcement/declaration date distinct from the
effective (ex-)date — confirm this against a real fixture; if you find
Tiingo *does* expose one, use it and say so. Otherwise, per the frozen,
conservative default policy: `knowledge_date = effective_date`.

## What to build

```python
from __future__ import annotations
import pandas as pd

def map_eod_to_corporate_actions(
    eod_rows: list[dict], stock_id: str, source_endpoint: str = "get_eod_prices",
) -> pd.DataFrame:
    """CORPORATE_ACTIONS_SCHEMA-conforming, plus provenance columns.

    Tiingo's EOD response includes a split-factor field and a dividend-cash
    field directly per row (confirm the exact field names against your
    fixture; likely candidates are `splitFactor` and `divCash`, but verify
    rather than assume).

    SPLIT rows: for any row where the split field is not 1.0 (or its
    documented "no split" sentinel), emit one action_type="split" row at
    that row's date, with adjustment_factor = the field's value AS TIINGO
    REPORTS IT, unmodified. (Sanity check: for a 4-for-1 split this should
    be 4.0. This is the same convention Phase 3's synthetic fixture already
    uses for its own corporate-action test -- a 2-for-1 split there uses
    adjustment_factor=2.0 -- so this is not a new convention, just this
    adapter's first real application of it.)

    DIVIDEND rows: for any row where the dividend-cash field is nonzero,
    emit one action_type="dividend" row at that row's date, with:

        adjustment_factor = 1 + (divCash / close)

    where `close` is THIS SAME ROW's raw close (the ex-dividend close on
    the effective_date itself) -- NOT the previous day's close. This exact
    formula is frozen because it is the unique value that makes
    compute_adjusted_returns's (1+raw_ret)*factor-1 formula recover the
    correct total return from a raw (price-only) ex-dividend return. Do
    not substitute the previous day's close, and do not derive a different
    formula without first re-deriving it algebraically and showing your
    work in the module docstring or your report -- this one has already
    been checked and must not silently drift.

    knowledge_date = effective_date for every row (the frozen conservative
    default -- confirmed absence of a distinct announcement-date field in
    your fixture, or use that field instead if you find one and document
    the change).

    is_superseded = False for every row -- Tiingo's EOD response has no
    amendment/withdrawal concept for these; document this plainly rather
    than leaving it unexplained.

    If a single date has BOTH a split and a dividend (rare but possible),
    emit BOTH rows (different action_type) rather than trying to combine
    them into one -- compute_adjusted_returns already multiplies factors
    across all matching actions for a (stock_id, date), so two correctly
    computed factors combine correctly as long as each is independently
    correct in isolation. See the required adversarial test below, which
    you must verify by hand, not just assume.
    """
```

## Vendor semantics validation (required, separate from the algebra check)

Before treating `divCash` as a canonical raw fact, establish with evidence
— not by assuming the formula's own correctness proves it — that:

1. Tiingo's `divCash` value for a given EOD row is associated with the
   **actual ex-dividend date** the adjustment convention requires (the
   date the raw close already reflects the price drop from), rather than
   the declaration date, record date, or payment date. These are
   genuinely different dates for a real dividend and a vendor could
   plausibly attach the cash amount to any of them.
2. The per-share cash amount itself corresponds to the real dividend for
   that event (not, e.g., an annualized figure or a different
   distribution).

Evidence must come from at least one of:
- Tiingo's own official documentation, if it states this plainly enough
  to settle the question, and/or
- cross-checking at least one real dividend event in your fixture against
  an independent, authoritative public source for that company's
  ex-dividend date and per-share amount (e.g. the company's own
  investor-relations dividend history, or another public dividend
  calendar not derived from Tiingo).

Record the resulting finding explicitly in `corporate_actions.py`'s module
docstring, in a form as unambiguous as P4B-2's `is_permanent` finding,
e.g.: `DIVIDEND_SEMANTICS_CERTIFIED = True/False`, with one or two
sentences of supporting evidence.

**If this cannot be established** (no sufficient documentation, and no
independent source you can obtain confirms the date/amount): do not
silently proceed as if the assumption holds. Either (a) fail closed —
raise a clearly-named exception for dividend rows specifically (split
rows are unaffected; their factor doesn't depend on this question) rather
than emitting an unverified `action_type="dividend"` row, or (b) emit the
row but record `DIVIDEND_SEMANTICS_CERTIFIED = False` in the module
docstring and say so plainly in your final report — pick whichever you
judge better, but the choice and its reasoning must be explicit, not
silent, either way. Whichever path you take, state it in your report in
terms P4B-9 can act on: P4B-9 will add a
`DIVIDEND ADJUSTMENT SEMANTICS = NOT CERTIFIED` line to its report if and
only if `DIVIDEND_SEMANTICS_CERTIFIED` is `False` (or the exception path
was taken) — so your finding must be legible without P4B-9 having to
interpret prose.

This does not change corporate-action ownership: the Tiingo adapter still
produces raw facts only; `compute_adjusted_returns` remains the sole
adjustment owner regardless of the outcome above.

## Fixture inputs

Capture, under `tests/fixtures/tiingo/corporate_actions/`:

1. AAPL EOD rows spanning the 2020-08-31 4-for-1 split.
2. Any real security with a plain cash dividend event in its EOD history
   (many large-caps pay quarterly dividends; pick one with a clean,
   unambiguous `divCash` value and a clear day-over-day price move you can
   hand-verify against). This same event is also your vendor-semantics
   specimen: independently look up (outside Tiingo) that company's real
   ex-dividend date and per-share cash amount for the period your fixture
   covers, and save a short note of what you found and where (e.g. the
   company's investor-relations dividend-history page) alongside the
   fixture file itself, so the cross-check is reviewable later, not just
   asserted in prose.
3. If you can find one under current access, a real security with a
   split AND a dividend on the *same* date — if you cannot find one, you
   may hand-construct a synthetic-but-labeled-as-such row for this one
   specific adversarial test, clearly marked in the fixture file as
   constructed rather than a real recorded specimen (this is the one
   exception to "always real data" in Phase 4B, justified because the
   combination is rare and the point of the test is arithmetic, not vendor
   realism).

## Required tests

1. **Split factor mapping**, AAPL 2020-08-31: `adjustment_factor == 4.0`
   (or whatever the real recorded value is — confirm against your
   fixture), `action_type == "split"`, `knowledge_date == effective_date`.
2. **Dividend factor formula, hand-verified.** Using your real dividend
   fixture: compute `1 + divCash/close` by hand from the recorded numbers,
   assert the function's output matches exactly, AND separately verify
   (as a second, independent check, e.g. via a tiny local reimplementation
   of `(1+raw_ret)*factor-1` in the test itself, NOT by importing
   `compute_adjusted_returns`) that applying this factor to the raw
   ex-dividend return recovers the correct total return
   `(close_t + divCash - close_{t-1}) / close_{t-1}` to a tight tolerance.
   This second check is the anti-tautology safeguard — it must not merely
   confirm the function returns what the formula says, but confirm the
   formula itself is right.
3. **Vendor semantics, independently confirmed (distinct from test 2).**
   Using your independently-sourced ex-dividend date and per-share amount
   from the fixture-inputs step above: assert that Tiingo's `divCash`
   value in your fixture matches the independently-sourced per-share
   amount, and that it is attached to the row whose date matches the
   independently-sourced ex-dividend date. This test must fail if either
   of those two things is not true — it is not satisfied by test 2's
   algebra passing. If your investigation could not obtain independent
   confirmation, this test instead asserts
   `DIVIDEND_SEMANTICS_CERTIFIED is False` (or that the fail-closed
   exception path is what actually happens for a dividend row) — i.e.
   the test always asserts something about the real, honest outcome of
   your investigation, never a value assumed to be true.
4. **No pre-adjustment leakage.** Confirm this module never reads or
   returns anything resembling an adjusted/adjClose value — grep your own
   output columns against `CORPORATE_ACTIONS_SCHEMA`'s key/dtypes only.
5. **Same-date split+dividend combination**, using your fixture from
   input #3: both rows are emitted (two rows, same `effective_date`,
   different `action_type`), and a hand-derived combined true return
   (compute both factors, multiply them, apply to the raw return, compare
   against the economically correct combined outcome you derive
   separately) is verified — do not skip this even if the fixture is
   constructed rather than real; it is exactly the kind of case that has
   never been proven correct before this task.
6. **Schema conformance** via `validate_panel`.
7. **No input mutation** of `eod_rows`.
8. **Provenance columns present.**

## Non-goals

- Do not call or reimplement `compute_adjusted_returns`.
- Do not implement `get_raw_returns`, `get_market_cap`,
  `get_trading_status`, `get_fundamentals`, or `get_listing_info`.
- Do not invent an announcement-date field if Tiingo's real response
  genuinely has none — use the conservative default and say so.

## Acceptance criteria

- All pre-existing tests (383 plus Wave 1's additions) continue to pass
  unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/vendors/tiingo/corporate_actions.py`,
  `tests/test_tiingo_corporate_actions.py`, and files under
  `tests/fixtures/tiingo/corporate_actions/`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-5-corporate-actions
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4b/task-p4b-5-corporate-actions`, touching only the files listed
above.

## When done

Report: (a) the exact field names Tiingo actually uses for split
factor/dividend cash, confirmed against your fixture; (b) the hand-derived
dividend-formula verification's numeric result; (c) your vendor-semantics
finding — the independent source you used, what it showed, and the final
`DIVIDEND_SEMANTICS_CERTIFIED` value (or confirmation that the fail-closed
exception path was used instead, if semantics could not be established);
(d) test results; (e) `git diff --stat`. Do not merge, do not touch
`master`, do not modify files outside the list above.
