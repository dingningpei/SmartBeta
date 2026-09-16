# Phase 3 Worker Task — P3-B: Canonical PIT Schemas + Settings

## Background (read this first)

`smart_beta` is a China A-share factor-investing research package. Phases
0-2 built a schema-driven research toolkit on top of a simple `DataSource`
interface with no real point-in-time (PIT) discipline: financial data is
returned as a single value per date, with no distinction between when an
economic fact applied and when a researcher could actually have known it.
Master is green at 143/143 tests as of commit `371be00`, tagged
`phase2-complete`.

The long-term goal is an autonomous factor-discovery system with strict
point-in-time discipline. Phase 3 builds the foundation for that: a new,
vendor-independent `smart_beta/pit/` subpackage. Read
`worker_tasks/phase3/phase3-plan.md` for the full frozen architecture,
roadmap, and task DAG before starting — it records four specific
refinements that this task's design directly implements. You are only
implementing one small, self-contained piece of a larger, already-decided
design; do not redesign anything described there.

**Your task, P3-B, is the canonical PIT schema layer** — one of two Wave 1
tasks (the other, P3-A, builds a `TradingCalendar`; you do not depend on
it and it does not depend on you). Every later Phase 3 task (corporate
actions, fundamentals resolution, the `PITDataSource` interface, the
trusted view, the synthetic fixture, the compliance suite) validates its
panels against the schemas you define here.

**The central design decision you are implementing (frozen, not yours to
revisit):** every PIT entity distinguishes *effective/event time* (when a
fact applies) from *knowledge/availability time* (when it became
knowable) -- but only where that distinction is real and not everywhere
mechanically. The plan document's Refinement 1 table settles this per
entity; the summary you need:

| Entity | Bitemporal? | Effective-time column | Knowledge-time column | Vintage (append-only)? |
|---|---|---|---|---|
| Fundamentals | Yes | `report_period_end` | `knowledge_date` | Yes -- a restatement is a new row with a later `knowledge_date`; the original is never overwritten. `is_restatement` marks non-original vintages. |
| Corporate actions | Yes | `effective_date` | `knowledge_date` | Yes, same pattern -- an amendment/withdrawal is a new row; `is_superseded` marks it. |
| Listing/delisting | No | `list_date`/`delist_date` | -- | No. Survivorship bias is a completeness/query-behavior concern tested later, not a schema-shape concern. |
| Trading status | No | (the date itself) | -- | No. Same-day, immediately-observable facts. |
| Market data (returns), market cap | No, by documented assumption | (the trading date) | -- | Not modeled in Phase 3; deferred, not designed away (the `knowledge_date` name is reserved). |

`KNOWLEDGE_DATE_COL = "knowledge_date"` is the **one** canonical name
reused across every bitemporal schema (fundamentals and corporate
actions), so a single "latest known as of t" resolver algorithm can later
be written once and reused for both, rather than two bespoke
implementations. You are defining the shapes; you are **not** writing that
resolver (that's `pit/fundamentals.py`, a later Wave 2 task).

**Corporate-action adjustment ownership (Refinement 2, for context only --
you are not implementing adjustment logic, just its data shape):** raw
prices/returns and the corporate-actions fact table are exposed separately
by any future `PITDataSource`; a single later module
(`pit/corporate_actions.py`) is the sole place that computes adjusted
returns from the two. This is why you need separate raw-return and
adjusted-return panel shapes (same columns, different semantic lineage)
rather than one ambiguous "the return" schema, and why market cap needs
two explicit, never-conflated columns (`float_mcap`, `total_mcap`) rather
than one ambiguous `mcap` -- Refinement 4's adversarial requirements
specifically include catching float/total market-cap confusion, and that
is only catchable if the schema makes the two columns structurally
distinct from the start.

**Your working directory for this task is:**

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-b-pit-schema
```

`cd` there before running any command. It is a git worktree on branch
`phase3/task-b-pit-schema`, branched from `master` after the Phase 3
architecture freeze. A venv already exists at `.venv` with the project
installed (`pip install -e ".[dev]"`) and `.venv/bin/pytest` passing
(143/143) before you start. If anything looks stale, re-run
`.venv/bin/pip install -e ".[dev]"` from that directory. Run tests with
`.venv/bin/pytest`.

This is Phase 3, Wave 1 -- one of two tasks (P3-A, P3-B) starting in
parallel. **Assume P3-A's deliverable does not exist yet in your
worktree**, and do not import from it (you don't need to -- your schemas
have no dependency on the trading calendar). You touch completely
disjoint files, so there is no need to coordinate or wait on it.

## File ownership

**You may create or modify exactly these three files, and no others:**

- `smart_beta/pit/schema.py` (new)
- `smart_beta/config/settings.py` (additive: exactly one new field, see below)
- `tests/test_pit_schema.py` (new)

**You must not modify anything else**, including `smart_beta/pit/__init__.py`
(already exists as an empty marker -- this package has multiple
independent owners across Phase 3's waves, so it deliberately stays
empty; do not add exports to it), `smart_beta/pit/calendar.py` (does not
exist yet -- P3-A owns it), `smart_beta/data/schema.py` (you **import**
from it, you never edit it -- see below), `smart_beta/data/*` otherwise,
`smart_beta/factors/*`, `smart_beta/engines/*`, `smart_beta/benchmarks/*`,
`smart_beta/pipelines/*`, and every existing test file.

## Reuse, don't duplicate: `smart_beta/data/schema.py`

That module already defines a generic `PanelSchema` class (key columns +
per-column dtype checks + duplicate-key rejection) and a `validate_panel()`
function, plus the constants `DATE_COL = "date"`, `STOCK_COL = "stock_id"`,
and `VALUE_COL = "value"`. **Import these, do not redefine them.** This
keeps exactly one canonical `PanelSchema`/`validate_panel` implementation
package-wide (Phase 2's final architecture audit specifically checked for
and confirmed there was no such duplication anywhere else; do not
introduce the first one). Read that file before writing yours -- your new
schemas should look and behave like siblings of `RETURN_PANEL_SCHEMA`,
`MARKET_CAP_PANEL_SCHEMA`, etc. already there, just in the new `pit`
namespace and with the bitemporal columns this task adds.

```python
from smart_beta.data.schema import (
    DATE_COL,
    STOCK_COL,
    VALUE_COL,
    PanelSchema,
    SchemaError,
    validate_panel,
)
```

## What to build

### 1. Bitemporal column-name constants

```python
KNOWLEDGE_DATE_COL = "knowledge_date"   # reused by every bitemporal schema below
```

### 2. Fundamentals fact table (bitemporal, vintage/append-only)

One row per reported fact. Multiple rows may share `(stock_id,
report_period_end, field)` when restatements occur -- each such row is a
distinct vintage, distinguished by `knowledge_date`.

```python
REPORT_PERIOD_END_COL = "report_period_end"
FIELD_COL = "field"
IS_RESTATEMENT_COL = "is_restatement"

FUNDAMENTALS_FACT_SCHEMA = PanelSchema(
    key_columns=(STOCK_COL, REPORT_PERIOD_END_COL, FIELD_COL, KNOWLEDGE_DATE_COL),
    dtypes={
        STOCK_COL: "string",
        REPORT_PERIOD_END_COL: "datetime",
        FIELD_COL: "string",
        KNOWLEDGE_DATE_COL: "datetime",
        VALUE_COL: "float",
        IS_RESTATEMENT_COL: "bool",
    },
)
```

### 3. Corporate actions fact table (bitemporal, vintage/append-only)

```python
EFFECTIVE_DATE_COL = "effective_date"
ACTION_TYPE_COL = "action_type"            # e.g. "split", "dividend", "rights_issue"
ADJUSTMENT_FACTOR_COL = "adjustment_factor"
IS_SUPERSEDED_COL = "is_superseded"

CORPORATE_ACTIONS_SCHEMA = PanelSchema(
    key_columns=(STOCK_COL, EFFECTIVE_DATE_COL, ACTION_TYPE_COL, KNOWLEDGE_DATE_COL),
    dtypes={
        STOCK_COL: "string",
        EFFECTIVE_DATE_COL: "datetime",
        ACTION_TYPE_COL: "string",
        KNOWLEDGE_DATE_COL: "datetime",
        ADJUSTMENT_FACTOR_COL: "float",
        IS_SUPERSEDED_COL: "bool",
    },
)
```

### 4. Raw and adjusted return panels (same shape family, distinct columns -- never conflate them)

```python
RAW_RETURN_COL = "raw_ret"
ADJUSTED_RETURN_COL = "adj_ret"

PIT_RAW_RETURN_PANEL_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={DATE_COL: "datetime", STOCK_COL: "string", RAW_RETURN_COL: "float"},
)
PIT_ADJUSTED_RETURN_PANEL_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={DATE_COL: "datetime", STOCK_COL: "string", ADJUSTED_RETURN_COL: "float"},
)
```

### 5. Market cap: float and total, always both, never one ambiguous column

```python
FLOAT_MARKET_CAP_COL = "float_mcap"
TOTAL_MARKET_CAP_COL = "total_mcap"

PIT_MARKET_CAP_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={
        DATE_COL: "datetime",
        STOCK_COL: "string",
        FLOAT_MARKET_CAP_COL: "float",
        TOTAL_MARKET_CAP_COL: "float",
    },
)
```

### 6. Listing info (not bitemporal -- decision documented above)

```python
LIST_DATE_COL = "list_date"
DELIST_DATE_COL = "delist_date"

PIT_LISTING_INFO_SCHEMA = PanelSchema(
    key_columns=(STOCK_COL,),
    dtypes={STOCK_COL: "string", LIST_DATE_COL: "datetime"},
)
```

(`delist_date` is intentionally not in `dtypes`: it is `NaT` for a still-listed
stock, exactly mirroring how `smart_beta.data.schema.LISTING_INFO_SCHEMA`
already handles this -- look at it for the precedent.)

### 7. Trading status (not bitemporal -- decision documented above)

```python
PIT_TRADING_STATUS_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={DATE_COL: "datetime", STOCK_COL: "string"},
)
```

(Flag columns themselves are open-ended/per-vendor, mirroring how
`smart_beta.data.schema.TRADING_STATUS_SCHEMA` only enforces the key
columns' dtypes and leaves flag columns unconstrained by name.)

### 8. One new `Settings` field

Add to the existing `Settings` frozen dataclass in
`smart_beta/config/settings.py` (additive only -- do not touch any
existing field):

```python
# --- Point-in-time discipline (Phase 3) ---
pit_availability_buffer_days: int = 0
```

Document it (as a comment above the field, matching this file's existing
style) as: additional days beyond a fact's `knowledge_date` before it is
treated as available to a backtest; `0` by default (a fact is available
exactly as of its `knowledge_date`), reserved for later conservatism (e.g.
modeling data-vendor ingestion lag beyond the public announcement itself).
No other Phase 3 Wave 1/2 logic reads this yet -- you are only reserving
the name so a later task doesn't need to touch this shared file again.

### 9. Module docstring

Write a module-level docstring in `pit/schema.py` stating the bitemporal
decision table above (entity -> bitemporal or not, and why) in your own
words -- this is the one place in the codebase a future reader should be
able to find the full rationale without re-reading the plan document.

## Non-goals

- Do not write any resolution/adjustment logic (no "latest known as of
  t" function, no return-adjustment computation) -- that's `pit/fundamentals.py`
  and `pit/corporate_actions.py`, later Wave 2 tasks. This task is shapes
  and constants only, exactly like `smart_beta/data/schema.py` itself
  contains no domain logic.
- Do not add a `knowledge_date`-equivalent column to the listing,
  trading-status, or return/market-cap schemas -- that was a considered
  and rejected generalization (see the table above); don't second-guess it
  here.
- Do not modify any existing schema or constant in
  `smart_beta/data/schema.py` -- only import from it.
- Do not add more than the one specified `Settings` field.

## Required tests

Mirror the existing style in `tests/test_schema.py` (Phase 0) --
read it for the pattern before writing yours.

- Each new schema validates a well-formed hand-built example panel.
- Each new schema rejects a malformed example: missing a required column,
  wrong dtype on at least one column, and a duplicate key row (three
  separate tests per schema is not required -- covering each failure mode
  at least once across the whole file is; don't skip the duplicate-key
  case for the vintage schemas specifically, since two rows sharing every
  key column *except* differing only in a non-key field would otherwise
  be silently valid when it shouldn't be for a bitemporal fact table --
  make sure your key columns actually enforce this).
- A vintage-specific test: build a `FUNDAMENTALS_FACT_SCHEMA`-conforming
  panel with two rows for the same `(stock_id, report_period_end, field)`
  but different `knowledge_date` values (a restatement scenario) and
  confirm `validate_panel` accepts it (proving the schema's key columns
  correctly allow multiple vintages rather than rejecting them as
  duplicates) -- then confirm a *third* row with the same
  `(stock_id, report_period_end, field, knowledge_date)` as an existing
  row *does* get rejected as a duplicate.
- An identity test proving reuse, not redefinition: assert
  `smart_beta.pit.schema.PanelSchema is smart_beta.data.schema.PanelSchema`
  (and likewise for `validate_panel`, `DATE_COL`, `STOCK_COL`, `VALUE_COL`)
  -- the same style as Phase 2's `test_factor_panel_schema_is_shared_with_factors_base`.
- A test confirming `Settings().pit_availability_buffer_days == 0` and that
  it can be overridden via `Settings(pit_availability_buffer_days=5)`.

## Acceptance criteria

- All 143 pre-existing tests continue to pass unchanged.
- All new tests in `tests/test_pit_schema.py` pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/pit/schema.py` (new),
  `smart_beta/config/settings.py` (one additive field), and
  `tests/test_pit_schema.py` (new) -- nothing else.
- `smart_beta/pit/schema.py` imports `PanelSchema`/`validate_panel`/
  `SchemaError`/`DATE_COL`/`STOCK_COL`/`VALUE_COL` from
  `smart_beta.data.schema` rather than redefining any of them.

## When done

Run the full test suite (`.venv/bin/pytest`) from your worktree, commit
your changes on branch `phase3/task-b-pit-schema`, and report back:

(a) the full list of schema/constant names you defined, confirming they
match the spec above (or explain any deviation and why);
(b) confirmation the one new `Settings` field was added additively;
(c) test results (should be 143 pre-existing + your new tests, all
passing);
(d) the `git diff --stat` output.

Do not merge your branch, do not touch `master`, and do not modify files
outside the three listed above.
