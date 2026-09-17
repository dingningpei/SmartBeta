# Phase 4C Worker Task — P4C-4: Identifier-Continuity Structured Guard

## Background (read this first)

Read `worker_tasks/phase4c/phase4c-plan.md` in full first. Master is at
`634c31c`.

**Why this task exists, and exactly what it must not do.** Phase 4B's
certification found `IDENTIFIER CONTINUITY = NOT CERTIFIED`: Tiingo's
`permaTicker` exists but `TiingoPITSource` never wires it, so every
configured ticker resolves via `smart_beta.vendors.tiingo.identifiers.
resolve_stock_id`'s mutable-ticker fallback (`is_permanent=False`). A
long-horizon backtest crossing a real ticker rename/reuse event (the real,
already-documented FB→META case) could silently misattribute history if
nothing checks for this. This task builds a **structured, declarative**
guard around that already-known fact — it does **not** invent a way to
detect or fix the underlying limitation.

**Read `smart_beta/vendors/tiingo/identifiers.py`'s `ResolvedIdentifier`
dataclass before writing anything.** It already carries exactly the
signal this task needs (`is_permanent: bool`, `source_field: str`) — this
task does not compute anything new about identity; it only turns a
collection of already-resolved identifiers into an explicit policy
decision plus persistent evidence.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-4-identifier-continuity`
on branch `phase4c/task-p4c-4-identifier-continuity`, branched from
`master` at `634c31c`. Run tests with `.venv/bin/pytest`.

This is Phase 4C, Wave 1 — one of four parallel tasks. Assume P4C-1,
P4C-2, P4C-3 do not exist in your worktree; do not import from them.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/research_inputs/identifier_continuity.py`
- `tests/test_research_inputs_identifier_continuity.py`

**Never create** `smart_beta/research_inputs/__init__.py` — unconditionally
P4C-1's file; not needed (PEP 420 namespace packages).

**You must not modify anything else**, including
`smart_beta/vendors/tiingo/identifiers.py` (you read `ResolvedIdentifier`,
you do not change it, and you do not add a `permaTicker` code path
anywhere), `smart_beta/pit/*`, or `pyproject.toml`.

## What to build

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from smart_beta.vendors.tiingo.identifiers import ResolvedIdentifier
```

Wait — read this carefully before importing that: **this module must not
import a concrete vendor module either**, to keep `research_inputs`
vendor-agnostic end to end. Instead, define your own tiny, structural
(duck-typed) protocol for "a resolved identifier," so this module works
with *any* source's identifier-resolution result, not specifically
Tiingo's:

```python
from typing import Protocol


class ResolvedIdentifierLike(Protocol):
    """Structural -- matches smart_beta.vendors.tiingo.identifiers.
    ResolvedIdentifier without importing it, and matches any future
    vendor's equivalent result with the same three fields."""
    stock_id: str
    is_permanent: bool
    source_field: str


@dataclass(frozen=True)
class IdentifierContinuityEvidence:
    """Machine-visible record of exactly what was found and what was
    decided -- this is the object that must survive into a later
    experiment/audit layer; no policy mode may produce a result that
    lacks this."""
    certified: bool                       # True iff every identifier is permanent
    non_permanent_stock_ids: tuple[str, ...]
    policy_applied: Literal["fail", "warn", "allow"]
    proceeded_under_override: bool        # True iff execution continued
                                           # despite certified being False


class IdentifierContinuityError(Exception):
    """Raised under policy='fail' when continuity is not certified."""
    def __init__(self, evidence: IdentifierContinuityEvidence) -> None:
        self.evidence = evidence
        super().__init__(
            f"identifier continuity not certified for: "
            f"{evidence.non_permanent_stock_ids}"
        )


def check_identifier_continuity(
    resolved_identifiers: Sequence["ResolvedIdentifierLike"],
    *,
    policy: Literal["fail", "warn", "allow"] = "fail",
) -> IdentifierContinuityEvidence:
    """See the frozen behavior table below -- write the real docstring
    from it; do not invent additional modes or behavior."""
```

### Frozen behavior (write this table into the real docstring)

| `policy` | `certified=True` (all permanent) | `certified=False` (any non-permanent) |
|---|---|---|
| `"fail"` (default) | returns evidence, execution proceeds | **raises** `IdentifierContinuityError(evidence)` |
| `"warn"` | returns evidence, execution proceeds | returns evidence with `proceeded_under_override=True`; caller is expected to surface a visible warning using the returned evidence (this function does not itself print/log — that's the caller's presentation choice; see non-goals) |
| `"allow"` | returns evidence, execution proceeds | returns evidence with `proceeded_under_override=True` — silent only in the sense that nothing is *raised*, but the evidence object itself is exactly as complete and explicit as every other mode; nothing about this mode omits or weakens the evidence |

**No mode ever returns evidence with a missing or falsified `non_permanent_
stock_ids` list.** The function's whole point is that `certified=False`
information is never harder to find under `"allow"` than under `"fail"`
— only whether an exception is raised changes.

## Required tests

1. All-permanent input, every policy value: returns evidence with
   `certified=True`, `non_permanent_stock_ids=()`, `proceeded_under_
   override=False`, no exception.
2. Mixed input (some permanent, some not), `policy="fail"` (default and
   explicit): raises `IdentifierContinuityError`; the raised exception's
   `.evidence.non_permanent_stock_ids` names exactly the non-permanent
   ones.
3. Same mixed input, `policy="warn"`: does not raise; returned evidence
   has `certified=False`, the correct `non_permanent_stock_ids`,
   `proceeded_under_override=True`.
4. Same mixed input, `policy="allow"`: does not raise; returned evidence
   is **identical in content** to the `"warn"` case (same
   `non_permanent_stock_ids`, same `certified`, same
   `proceeded_under_override=True`) — the only behavioral difference
   between `"warn"` and `"allow"` is documented as "who is expected to
   surface a message," not a difference in the evidence object itself.
   Assert this equality directly, so a future edit that starts stripping
   information under `"allow"` breaks this test immediately.
5. This function never computes `is_permanent` itself, never reads a
   `list_date`/`startDate`, and never infers anything from listing
   history — confirm by construction: your `ResolvedIdentifierLike`
   protocol has exactly three fields and the function's logic only reads
   `is_permanent`/`stock_id` from its input; grep your own module for the
   word `list_date` and assert it is absent (or just don't write it, and
   note in your report that you deliberately didn't).
6. Uses only duck-typed input — a plain, hand-built object (not
   `smart_beta.vendors.tiingo.identifiers.ResolvedIdentifier`) satisfying
   the protocol's three fields works identically, proving no vendor
   import is needed for this function to work.

## Non-goals

- Do not import `smart_beta.vendors.tiingo.identifiers` or any other
  vendor module.
- Do not wire `permaTicker`.
- Do not attempt to infer or reconstruct identity from `list_date`/
  `startDate`/any listing history.
- Do not have this function print or log anything itself — it returns a
  structured object; presentation (printing a warning under `"warn"`) is
  the caller's job, in a later task, not this one's.
- Do not add a fourth policy mode.

## Acceptance criteria

- All pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly
  `smart_beta/research_inputs/identifier_continuity.py` and
  `tests/test_research_inputs_identifier_continuity.py`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-4-identifier-continuity
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4c/task-p4c-4-identifier-continuity`, touching only the two files
listed above.

## If you discover a contract contradiction

Stop and report it rather than improvising.

## When done

Report: (a) the exact `IdentifierContinuityEvidence`/
`check_identifier_continuity` signatures; (b) confirmation the `"warn"`
and `"allow"` evidence objects are content-identical, per required test
4; (c) test results; (d) `git diff --stat`. Do not merge, do not touch
`master`, do not modify files outside the list above.
