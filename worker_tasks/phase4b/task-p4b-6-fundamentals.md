# Phase 4B Worker Task — P4B-6: Fundamentals, Fail-Closed Reconciliation (Wave 2)

## Background (read this first)

Read `worker_tasks/phase4b/phase4b-plan.md` in full first. This task
starts only after Wave 1 is merged and Barrier 1 is green; confirm your
worktree's `master` merge-base contains
`smart_beta/vendors/tiingo/{client,identifiers}.py` before starting.

**Your task, P4B-6, is the most correctness-sensitive piece of Phase 4B.**
It went through three rounds of architecture review, each catching a
real mistake in the previous draft — read this whole section, not just
the code sketch, before writing anything.

**Mistake 1 (caught in review):** an early draft derived `report_period_end`
from `(year, quarter)` using calendar-quarter arithmetic. This is wrong.
AAPL's fiscal calendar is non-calendar: fiscal Q3 2026 ends **2026-06-27**,
not 2026-06-30. No calendar-quarter arithmetic is permitted anywhere in
this task.

**Mistake 2 (caught in review):** treating `is_restatement=False` as if it
meant "verified not restated." It does not. Tiingo's `asReported`
endpoint, under current plan-tier access, returns at most one vintage per
fact — we do not know whether that is a genuine data limitation or just
an untested case (a real restatement specimen, RGEN 2024 Q2, returned
HTTP 400 under current access — see P4B-1's fixtures). `False` here is a
schema-constrained placeholder, not a claim. Say so explicitly in your
module docstring, in these words: `False != verified non-restated;
restatement status remains NOT CERTIFIED.`

**Mistake 3 (this is the newest, strictest correction, and the one you
must not repeat):** reconciling `asReported` values to a `report_period_end`
by an inner join that silently drops unmatched periods, or by any logic
that infers/forward-fills/back-fills a period end, or that picks an
arbitrary one of several duplicate matches. **All of these are now
forbidden by name.** The frozen invariant is below, and it is
non-negotiable: **exactly one match required, or fail closed.**

This is one of four Wave 2 tasks (P4B-4 returns/market cap, P4B-5
corporate actions, P4B-7 listing). You touch completely disjoint files
from all three.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-6-fundamentals`
on branch `phase4b/task-p4b-6-fundamentals`, branched from `master` after
Wave 1 merges. Run tests with `.venv/bin/pytest`.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/vendors/tiingo/fundamentals.py`
- `tests/test_tiingo_fundamentals.py`
- `tests/fixtures/tiingo/fundamentals/` — any fixture files you need

**You must not modify anything else**, including
`smart_beta/pit/fundamentals.py` (Phase 3, finished — you never call or
reimplement `latest_known_value`; this task produces only raw facts for
it to later consume), `smart_beta/vendors/tiingo/{client,identifiers,
calendar_source,returns_and_market_cap,corporate_actions}.py`,
`listing.py` (sibling task), `pyproject.toml`, or any fixture subdirectory
other than your own.

## The APIs you consume (already merged — read the actual files, this is a summary)

```python
# smart_beta.vendors.tiingo.client
class TiingoClient:
    def get_fundamentals_asreported(self, ticker, start_date=None, end_date=None) -> list[dict]: ...
    def get_fundamentals_normalized(self, ticker, start_date=None, end_date=None) -> list[dict]: ...
def replay_transport(recordings) -> Transport: ...

# smart_beta.vendors.tiingo.identifiers
def resolve_stock_id(meta: dict) -> ResolvedIdentifier: ...

# smart_beta.pit.schema
from smart_beta.pit.schema import (
    STOCK_COL, REPORT_PERIOD_END_COL, FIELD_COL, KNOWLEDGE_DATE_COL,
    IS_RESTATEMENT_COL, VALUE_COL, FUNDAMENTALS_FACT_SCHEMA, validate_panel,
)
```

`FUNDAMENTALS_FACT_SCHEMA`'s key is
`(stock_id, report_period_end, field, knowledge_date)`, and its dtypes
require `is_restatement` as a `bool` (`smart_beta/pit/schema.py:111-121`)
— you must emit one, per the "schema-constrained placeholder" framing
above, never as a claim.

## The frozen reconciliation policy — read this twice

Tiingo returns two different fundamentals responses:

| Response | Field | Maps to |
|---|---|---|
| `get_fundamentals_asreported` | `statementData` line items | canonical **fact values** |
| `get_fundamentals_asreported` | `date` | `knowledge_date` |
| `get_fundamentals_normalized` | `date` | `report_period_end` **lookup value only** |

The two responses may have different fiscal-period coverage — this has
already been observed empirically, so do not assume they line up 1:1.
Reconciliation is by **fiscal identity** (the real Tiingo fiscal-year and
fiscal-quarter fields — confirm the exact field names against your real
fixture; do not assume they are literally `year`/`quarter` without
checking), never by date, never by position/order.

```python
class FiscalPeriodReconciliationError(Exception):
    """Raised when reconciling one asReported statement to its
    report_period_end does not yield exactly one match in the
    normalized response."""

def reconcile_report_period_end(
    as_reported_statement: dict,
    normalized_statements: list[dict],
) -> pd.Timestamp:
    """Find every entry in `normalized_statements` whose fiscal identity
    (fiscal year + fiscal quarter) matches `as_reported_statement`'s own
    fiscal identity.

    count == 1  -> return that one entry's `date` field as report_period_end.
    count == 0  -> raise FiscalPeriodReconciliationError. NEVER silently
                   drop this statement, NEVER infer a calendar-quarter-end
                   date, NEVER forward/back-fill from an adjacent period.
    count > 1   -> raise FiscalPeriodReconciliationError. NEVER choose an
                   arbitrary duplicate.

    NEVER return as_reported_statement's own `date` field here -- that
    field is knowledge_date, and using it as report_period_end is exactly
    the "use knowledge_date as report_period_end" mistake this function
    exists to make impossible.
    """

def map_asreported_to_fundamentals(
    stock_id: str,
    as_reported_statements: list[dict],
    normalized_statements: list[dict],
    fields: Sequence[str],
) -> pd.DataFrame:
    """FUNDAMENTALS_FACT_SCHEMA-conforming, plus provenance columns.

    For every statement in as_reported_statements: call
    reconcile_report_period_end. If it raises for ANY statement in the
    batch, let the exception propagate and abort the WHOLE call -- do not
    catch it, skip that one statement, and return a partial frame missing
    just the bad row. A partial result that silently omits one unreconciled
    period IS the "silently drop the fiscal period" anti-pattern this
    policy forbids, just relocated one layer up. Fail the whole batch,
    loudly, every time.

    For each successfully reconciled statement, emit one row per requested
    field present in `statementData` (restricted to `fields`, and further
    restricted to your derived-field exclusion allowlist below).

    is_restatement = False for every row (see the framing above; state it
    again in this function's own docstring, not just the module's).
    """
```

## Derived-field exclusion (unchanged policy, still required)

Tiingo's fundamentals responses may include vendor-computed/derived
fields (e.g. a Piotroski F-score or similar) that can change retroactively
with no `knowledge_date` attached. These must be **structurally
excluded** from the fields this module can ever emit — not merely
unused by convention, but unreachable. Maintain an explicit allowlist of
raw line-item field names (revenue, net income, total assets, or whatever
your real fixture's `statementData` actually contains as genuine reported
line items) and reject/ignore anything outside it, with a test proving a
known derived field cannot reach the output even if a caller asks for it
in `fields`.

## Fixture inputs

Capture, under `tests/fixtures/tiingo/fundamentals/`:

1. **AAPL, both `asReported` and normalized responses**, covering fiscal
   Q3 2026. Required exact values for your test:
   ```
   report_period_end = 2026-06-27
   knowledge_date    = 2026-07-31
   ```
   If your real captured data differs from these exact values, that means
   this spec's assumed specimen is wrong — stop, report the discrepancy
   with your actual captured values, and do not silently substitute your
   own numbers into this spec's required test without flagging it.
2. **A constructed (labeled-as-such) variant** of the normalized response
   with one fiscal-identity entry removed — used for the "0 matches"
   adversarial test.
3. **A constructed (labeled-as-such) variant** of the normalized response
   with one fiscal-identity entry duplicated — used for the ">1 matches"
   adversarial test.
4. RGEN's `asReported` 400 response (reuse the real captured specimen from
   P4B-1's fixtures if convenient, or re-record your own copy in your own
   subdirectory) — not for reconciliation logic directly, but to confirm
   `TiingoClient` raising `TiingoAPIError` is what this module's caller
   sees for an uncertifiable specimen, rather than this module trying to
   catch and paper over it.

## Required tests

1. **Valid case, exact values, non-calendar quarter.** AAPL fiscal Q3
   2026: `report_period_end == pd.Timestamp("2026-06-27")` and
   `knowledge_date == pd.Timestamp("2026-07-31")` — asserted exactly, not
   approximately. A version of this test that instead computed
   `report_period_end` via calendar-quarter-end logic would get
   `2026-06-30` and must fail this assertion; you do not need to write
   that wrong implementation, but you must confirm your real
   implementation would catch it (i.e., do not write a test loose enough
   that both a right and a wrong implementation would pass it).
2. **Missing period-end match → explicit failure.** Using fixture #2,
   `reconcile_report_period_end` (and, at the batch level,
   `map_asreported_to_fundamentals`) raises `FiscalPeriodReconciliationError`
   — and the whole batch call raises, producing no partial DataFrame.
3. **Duplicate period-end matches → explicit failure.** Using fixture #3,
   same exception, same whole-batch-abort behavior.
4. **`knowledge_date` is never substituted for `report_period_end`.**
   Using the AAPL fixture (where the two dates are genuinely different:
   2026-06-27 vs. 2026-07-31), confirm the two output columns are not
   swapped and not accidentally equal due to a bug that would coincidentally
   look right only when the two happen to match.
5. **Derived-field exclusion.** A known derived/computed field cannot
   reach the output even when explicitly requested in `fields`.
6. **`is_restatement` is always `False`**, and the module docstring
   contains the literal sentence `False != verified non-restated;
   restatement status remains NOT CERTIFIED`.
7. **Schema conformance** via `validate_panel`.
8. **Provenance columns present.**

## Non-goals

- Do not call or reimplement `latest_known_value`.
- Do not implement `get_raw_returns`, `get_market_cap`,
  `get_trading_status`, `get_corporate_actions`, or `get_listing_info`.
- Do not attempt to certify restatement/vintage behavior yourself — that
  determination (and the literal `RESTATEMENT/VINTAGE RECONSTRUCTION =
  NOT CERTIFIED` report line) belongs to P4B-9.
- Do not implement a "best effort" or "partial success" mode for
  reconciliation failures — the whole-batch-abort behavior is required,
  not a suggestion.

## Acceptance criteria

- All pre-existing tests (383 plus Wave 1's additions) continue to pass
  unchanged.
- All required tests above pass, including both fail-closed adversarial
  cases and the exact AAPL fiscal-Q3-2026 values.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/vendors/tiingo/fundamentals.py`,
  `tests/test_tiingo_fundamentals.py`, and files under
  `tests/fixtures/tiingo/fundamentals/`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-6-fundamentals
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4b/task-p4b-6-fundamentals`, touching only the files listed above.

## When done

Report: (a) the exact Tiingo fiscal-identity field names you used for
matching; (b) confirmation that your real captured AAPL fiscal-Q3-2026
values match `report_period_end=2026-06-27`/`knowledge_date=2026-07-31`
(or the discrepancy, if any); (c) test results; (d) `git diff --stat`. Do
not merge, do not touch `master`, do not modify files outside the list
above.
