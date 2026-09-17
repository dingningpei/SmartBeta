# Phase 4C Worker Task — P4C-3: Fail-Closed Fundamentals Coverage Orchestration

## Background (read this first)

Read `worker_tasks/phase4c/phase4c-plan.md` in full first. Master is at
`634c31c`.

**Why this task exists.** P4B-9's certification proved that
`TiingoPITSource.get_fundamentals` can succeed for a narrow date range and
raise for a wider one, because the vendor's `asReported`/normalized
statement coverage is not generally symmetric across arbitrary ranges
(real evidence: `2026-03-01..2026-07-31` reconciles, `2026-01-01..2026-12-31`
does not, for the identical security). Baking Tiingo-specific retry logic
into a generic engine would be wrong. This task builds a small,
vendor-agnostic orchestration that decomposes a wide request into
narrower sub-ranges and enforces **strict completeness by default** — a
vendor coverage failure must never silently shrink the sample a caller
receives.

**Read `smart_beta/pit/source.py` (`PITDataSource.get_fundamentals`'s
abstract signature) before writing anything.** This task calls that
interface method only — it never imports, catches, or type-checks
against `smart_beta.vendors.tiingo.fundamentals.
FiscalPeriodReconciliationError` or any other vendor-specific exception
by name. Any exception from a single sub-range call is treated
identically: that sub-range is unresolved. This works precisely because
each call this module makes is for one narrow, internally-constructed
sub-range with well-formed inputs — a broad catch here is a deliberate,
documented, bounded choice, not a general "swallow errors" anti-pattern.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-3-fundamentals-coverage`
on branch `phase4c/task-p4c-3-fundamentals-coverage`, branched from
`master` at `634c31c`. Run tests with `.venv/bin/pytest`.

This is Phase 4C, Wave 1 — one of four parallel tasks. Assume P4C-1,
P4C-2, P4C-4 do not exist in your worktree; do not import from them.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/research_inputs/fundamentals_coverage.py`
- `tests/test_research_inputs_fundamentals_coverage.py`

**Never create** `smart_beta/research_inputs/__init__.py` — unconditionally
P4C-1's file; not needed (PEP 420 namespace packages; see P4C-2's spec for
the full reasoning if you want it, it applies identically here).

**You must not modify anything else**, including `smart_beta/pit/source.py`,
`smart_beta/pit/fundamentals.py`, any `smart_beta/vendors/*` file (you
consume the abstract `PITDataSource` interface only, never a concrete
vendor class), or `pyproject.toml`.

## What to build

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Sequence

import pandas as pd

from smart_beta.pit.source import PITDataSource


@dataclass(frozen=True)
class FundamentalsCoverageReport:
    """What was actually resolved, and what wasn't, for one retrieval."""
    requested_intervals: tuple[tuple[pd.Timestamp, pd.Timestamp], ...]
    resolved_intervals: tuple[tuple[pd.Timestamp, pd.Timestamp], ...]
    unresolved_intervals: tuple[tuple[pd.Timestamp, pd.Timestamp], ...]
    failure_reasons: Mapping[tuple[pd.Timestamp, pd.Timestamp], str]

    @property
    def is_complete(self) -> bool:
        return not self.unresolved_intervals


class FundamentalsCoverageError(Exception):
    """Raised by default when the requested coverage is incomplete."""
    def __init__(self, report: FundamentalsCoverageReport) -> None:
        self.report = report
        super().__init__(
            f"{len(report.unresolved_intervals)} of "
            f"{len(report.requested_intervals)} requested interval(s) "
            "could not be resolved"
        )


@dataclass(frozen=True)
class FundamentalsRetrievalResult:
    """Always carries coverage alongside data -- there is no code path
    that returns a bare DataFrame indistinguishable from a complete one."""
    data: pd.DataFrame
    coverage: FundamentalsCoverageReport


def retrieve_fundamentals(
    source: PITDataSource,
    start: date | str,
    end: date | str,
    fields: Sequence[str],
    *,
    interval_width_days: int = 92,
    allow_partial: bool = False,
) -> FundamentalsRetrievalResult:
    """See docstring requirements below -- write the real one; this sketch
    fixes the contract, not the prose."""
```

### Frozen chunk-boundary semantics (write these down verbatim in the real docstring)

`[start, end]` is inclusive on both ends. Decompose it into consecutive,
non-overlapping, contiguous sub-intervals: the first sub-interval starts
exactly at `start`; each subsequent sub-interval starts the day
immediately after the previous one's end (`previous_end + 1 day`); each
sub-interval spans `interval_width_days` calendar days **except possibly
the last**, which ends exactly at `end` (never padded past it, even if
narrower than `interval_width_days`). This guarantees: the union of
sub-intervals equals `[start, end]` exactly (no gap, no overlap), and
every calendar day in `[start, end]` — including both boundary dates —
belongs to exactly one sub-interval.

**This decomposition is a retrieval-granularity detail only.** Never
document, name, or treat a sub-interval as a fiscal period. The docstring
must say this explicitly, close to the words the plan document uses.

### Resolution semantics per sub-interval

Call `source.get_fundamentals(sub_start, sub_end, fields)` once. If it
returns (including returning an empty, valid DataFrame with no rows —
that is a normal, expected outcome for a period with genuinely no
fact, e.g. before a security's real reporting history begins, and must
NOT be treated as unresolved), the sub-interval is **resolved**, and its
rows are added to the accumulated result. If the call raises **any**
exception, the sub-interval is **unresolved**; record
`f"{type(exc).__name__}: {exc}"` as its `failure_reasons` entry — never
import or `isinstance`-check the exception's class.

### Deduplication rule (frozen)

After accumulating rows from every resolved sub-interval, deduplicate on
`FUNDAMENTALS_FACT_SCHEMA`'s own key: `(stock_id, report_period_end,
field, knowledge_date)`. If a key appears more than once: if every
duplicate row's `value` is identical, keep exactly one (deterministically
— e.g. the first occurrence in sub-interval order); if any duplicate
row's `value` genuinely differs across duplicates, this is an internal
contradiction, not something to silently resolve one way — raise a
clearly-named exception (e.g. `FundamentalsInternalInconsistencyError`)
rather than picking a value. Never silently double-count a fact by
leaving true duplicates in the output.

### Completeness enforcement (frozen default)

If `allow_partial=False` (the default) and `coverage.is_complete` is
`False`: raise `FundamentalsCoverageError(coverage)` **before** returning
anything — the caller never receives a truncated `DataFrame` in this
path, only the exception, which carries the full report. If
`allow_partial=True`: always return a `FundamentalsRetrievalResult`
(never a bare `DataFrame`) regardless of whether coverage is complete,
so a caller must explicitly write `.data` to get a frame at all, and
`.coverage` is sitting right next to it in the same object — there is no
way to consume the data without the coverage evidence being reachable
from the same reference.

## Required adversarial tests (all required, per the frozen chunking clarification)

1. **Exact coverage, no gaps, no overlaps.** For a real multi-chunk range,
   assert the union of `requested_intervals` (or the sub-intervals your
   implementation actually issues — expose them for testing) covers
   `[start, end]` exactly, with each calendar day owned by exactly one
   sub-interval (check adjacent sub-intervals: `interval[i].end + 1 day
   == interval[i+1].start`).
2. **Start/end boundaries handled exactly once.** `start` is the first
   sub-interval's start; `end` is the last sub-interval's end; neither
   appears in two sub-intervals.
3. **Short ranges work.** A range narrower than `interval_width_days`
   produces exactly one sub-interval, spanning the full requested range.
4. **Ranges crossing month/year boundaries work.** A range that crosses
   at least one calendar-month and one calendar-year boundary decomposes
   correctly (no special-casing breaks at those boundaries).
5. **Legitimately empty vs. unresolved are distinguishable.** A test
   double `PITDataSource` whose `get_fundamentals` returns an empty
   DataFrame (no exception) for one sub-interval and raises for another:
   assert the empty one appears in `resolved_intervals` (contributing zero
   rows, not an error) and the raising one appears in
   `unresolved_intervals` with its `failure_reasons` entry — and that
   `is_complete` is `False` because of the second one only.
6. **Duplicate identical facts across adjacent sub-intervals are
   deduplicated, never double-counted.** Construct a test double whose
   two adjacent sub-interval calls both happen to return the same
   canonical fact (same key, same value) — assert the final `data` has
   exactly one row for that key.
7. **Duplicate conflicting facts raise, never silently pick one.** Same
   setup, but the two adjacent calls return the same key with *different*
   values — assert this raises your named inconsistency exception rather
   than returning either value.
8. **Strict-by-default.** Any unresolved sub-interval, with
   `allow_partial` omitted (default), raises `FundamentalsCoverageError`
   carrying a report whose `unresolved_intervals`/`failure_reasons` you
   assert on directly.
9. **`allow_partial=True` never silently discards coverage evidence.** Same
   failing scenario, `allow_partial=True`: assert the return type is
   `FundamentalsRetrievalResult` (not a bare `DataFrame`), `.data`
   contains only the resolved rows, and `.coverage` still shows the exact
   same unresolved intervals/reasons as the strict case would have raised
   with.
10. **Never imports or references the real vendor exception.** A static
    check of this module's own import list (or just: do not import
    `smart_beta.vendors.tiingo.fundamentals` at all) — confirm via a test
    that inspects the module's imports, or simply by the module genuinely
    not importing it (reviewer will verify this by reading the file).
11. **No input mutation**, determinism (two identical calls produce
    identical results, aside from a fresh object identity).

## Non-goals

- Do not import any `smart_beta.vendors.*` module.
- Do not invent fiscal-quarter semantics from `interval_width_days`.
- Do not weaken, catch-and-suppress-permanently, or reimplement P4B-6's
  own reconciliation logic — this module never touches
  `map_asreported_to_fundamentals` directly; it only calls the abstract
  `PITDataSource.get_fundamentals`.
- Do not build a caching layer or retry-with-backoff — this is pure
  decomposition + accumulation + completeness enforcement.

## Acceptance criteria

- All pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/research_inputs/fundamentals_coverage.py`
  and `tests/test_research_inputs_fundamentals_coverage.py`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-3-fundamentals-coverage
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4c/task-p4c-3-fundamentals-coverage`, touching only the two files
listed above.

## If you discover a contract contradiction

Stop and report it rather than improvising.

## When done

Report: (a) the exact chunk-decomposition algorithm and confirmation it
satisfies the frozen boundary semantics; (b) the deduplication logic and
how you tested the conflicting-duplicate case; (c) confirmation this
module never imports any `smart_beta.vendors.*` module; (d) test results;
(e) `git diff --stat`. Do not merge, do not touch `master`, do not modify
files outside the list above.
