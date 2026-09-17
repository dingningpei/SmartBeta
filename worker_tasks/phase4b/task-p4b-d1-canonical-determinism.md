# Phase 4B Worker Task — P4B-D1: Canonical Determinism Compliance

## Background (read this first)

Master is at `f716b1a` (P4B-8 merged, 690/690 tests green). This is a
**Phase 3 reopening** — the only one in Phase 4B, and it is deliberately
narrow. Read this whole section before touching anything.

**Why Phase 3 is being reopened.** `smart_beta/pit/compliance.py` is
frozen, tagged `phase3-complete`, and every later Phase 4B task has been
explicitly forbidden from touching it. During the independent P4B-8
review, I reproduced `check_deterministic_results` (already-merged,
unmodified) directly against the real, fully assembled `TiingoPITSource`
and found it genuinely fails — not because of a data bug, but because
every one of the six panel-returning methods carries a real wall-clock
`_ingested_at` provenance timestamp (frozen Phase 4B policy, correctly
implemented identically by every Wave 2 module since before Wave 1
started). Two back-to-back calls to any method whose output is non-empty
will almost always carry two different `_ingested_at` values, and
`check_deterministic_results`'s `pd.testing.assert_frame_equal(first,
second)` compares the *entire* frame, provenance included, so it reports
a failure. I independently confirmed this is not a fixed set of "broken"
methods: at one date range, four of six methods fail and two trivially
pass (empty output); at another range, the other two fail instead. The
common thread is non-empty output, not any particular method.

**This was never caught before now because it could not have been.**
`SyntheticPITSource` (Phase 3's own reference fixture, Wave 3) carries no
provenance columns at all — it was never a case that could exercise this.
The mismatch is real and only became visible once a real, provenance-
bearing adapter existed. This satisfies the project's standing bar for
reopening Phase 3 ("only if an implementation-blocking contract defect is
demonstrated") — it is demonstrated, not hypothetical, and you must say
so explicitly in your own diff (see "What to document" below).

**Frozen semantic requirement for this task** — read it twice, it is the
whole point:

> Identical source inputs must produce identical CANONICAL PIT data.
> Operational provenance such as `_ingested_at` may legitimately differ
> between invocations and must not cause canonical-data determinism to
> fail.

Both halves matter equally. A fix that makes the check pass by loosening
canonical-column comparison is as wrong as the current bug.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-d1-canonical-determinism`
on branch `phase4b/task-p4b-d1-canonical-determinism`, branched from
`master` at `f716b1a`. Run tests with `.venv/bin/pytest`.

## File ownership

**You may modify/create exactly these:**

- `smart_beta/pit/compliance.py` (amend — `check_deterministic_results`
  only; see scope below)
- `tests/test_pit_compliance_suite.py` (amend)

**You must not modify anything else.** In particular:
- Do not modify `check_no_shared_mutable_state`, `check_schema_conformance`,
  or any other `check_*` function.
- Do not modify `_panel_method_specs`'s signature or return shape — it
  already returns `(method_name, call, schema)` as a 3-tuple;
  `check_deterministic_results` currently discards the third element as
  `_schema`. Stop discarding it; do not change what it returns.
- Do not modify `smart_beta/data/schema.py` (`PanelSchema`) — read its
  `validate()` method (already merged) for the exact definition of a
  schema's "required columns" to reuse, do not redefine it separately.
- Do not modify anything under `smart_beta/vendors/tiingo/` — this task
  has nothing to do with the Tiingo adapter; it fixes a generic,
  vendor-independent compliance function.
- Do not modify `smart_beta/pit/synthetic.py`, `smart_beta/pit/source.py`,
  `smart_beta/pit/view.py`.
- Do not touch `pyproject.toml`.
- Do not touch or move the `phase3-complete` git tag.

## The API you consume/amend (read the actual files first)

```python
# smart_beta/pit/compliance.py, already merged
def _panel_method_specs(
    source: PITDataSource, start, end, fields: Sequence[str],
) -> list[tuple[str, object, "PanelSchema"]]:
    """(method_name, callable, schema) for the six panel-returning methods."""

def check_deterministic_results(source, start, end, fields) -> list[ComplianceCheckResult]:
    """Per panel method: two identical calls produce identical results.
    CURRENTLY compares the full frame -- this is the function you fix."""

# smart_beta/data/schema.py, already merged, DO NOT MODIFY
class PanelSchema:
    key_columns: Sequence[str]
    dtypes: Mapping[str, str]

    def validate(self, df, *, name="panel") -> None:
        required = list(dict.fromkeys((*self.key_columns, *self.dtypes.keys())))
        # ... this IS the canonical definition of "required columns" --
        # reuse it, do not write a second one.
```

## What to build

Amend `check_deterministic_results` to compare only each schema's own
canonical (required) columns between the two calls, using the schema
object `_panel_method_specs` already hands you:

```python
def _canonical_columns(schema: "PanelSchema") -> list[str]:
    """The exact same column set PanelSchema.validate() itself requires --
    key_columns plus every column named in dtypes, in that order, with
    duplicates removed. This is a POSITIVE selection (what IS canonical),
    never a list of provenance column names to exclude -- a new
    non-canonical column added to any future mapper is automatically
    excluded without this function needing to know its name.
    """
    return list(dict.fromkeys((*schema.key_columns, *schema.dtypes.keys())))


def check_deterministic_results(source, start, end, fields) -> list[ComplianceCheckResult]:
    """Per panel method: two identical calls produce identical CANONICAL
    data. Non-canonical columns (e.g. vendor-adapter provenance such as
    _source_vendor/_source_endpoint/_ingested_at) are explicitly excluded
    from this comparison -- they are allowed to vary between invocations
    per the Phase 4B provenance policy, and doing so must never be
    reported as a determinism failure. Canonical values (dates, stock
    ids, and every value the schema actually requires) must still match
    exactly; any real difference there is still reported as a failure.

    Reopened from Phase 3 (P4B-D1): this mismatch was discovered
    empirically only after a real, provenance-bearing vendor adapter
    (Phase 4B / Tiingo) was integrated and checked against this function
    for the first time -- SyntheticPITSource, this function's only prior
    reference implementation, carries no provenance columns and could
    never have exposed it.
    """
    results: list[ComplianceCheckResult] = []
    for method_name, call, schema in _panel_method_specs(source, start, end, fields):
        try:
            first = call()
            second = call()
            canonical = _canonical_columns(schema)
            pd.testing.assert_frame_equal(first[canonical], second[canonical])
        except Exception as exc:
            # ... unchanged failure-result construction
        else:
            # ... unchanged success-result construction
    return results
```

Adjust exactly, and only, what's needed to make this real — read the
actual current function body (it has real `_result(...)` calls you must
preserve verbatim except for the comparison line itself) and existing
failure/success message text; do not rewrite messages that don't need to
change.

## What to document

In `check_deterministic_results`'s own docstring (shown above) and, if
the module docstring's own design-constraints section references
determinism, update it there too: state plainly that comparison is
restricted to canonical (schema-required) columns, why, and that this is
a deliberate, reviewed Phase 3 amendment made after an empirical
discovery via Phase 4B's Tiingo adapter — not a silent behavior change.

## Required tests (adversarial, in `tests/test_pit_compliance_suite.py`)

Phase 3's established meta-testing discipline (deliberately broken
reference `PITDataSource` doubles proving a check fails for the *right*
reason) is the model here — read `test_pit_compliance_suite.py`'s
existing style before writing these.

1. **Differing `_ingested_at` alone does not fail canonical determinism.**
   Construct a minimal test-double `PITDataSource` (or reuse an existing
   fixture in the file if one already fits) whose panel methods return
   frames that are canonical-column-identical across two calls but carry
   a genuinely different extra, non-canonical column value each call
   (e.g. a fresh timestamp, mirroring real `_ingested_at` behavior).
   Assert `check_deterministic_results` reports **PASS** for every method.
2. **Differing canonical data DOES fail.** A second double whose panel
   methods return a genuinely different canonical value (e.g. a
   different `raw_ret`) on the second call. Assert `check_deterministic_
   results` still reports **FAIL** for the affected method(s), with a
   message that still names the method precisely (existing message
   format preserved).
3. **An extra non-canonical column cannot accidentally redefine the
   canonical comparison contract.** A third double whose extra column
   does *not* look like an obvious "provenance" name (no underscore
   prefix, an ordinary-looking name) and differs between calls. Assert
   this is *still* correctly excluded from the comparison and the check
   still PASSes — proving the mechanism keys on actual schema
   membership, never on a naming convention (e.g. "starts with `_`").
   This is the test that would catch a wrong implementation that
   excluded columns by pattern-matching a name instead of using the
   schema.
4. **Real-source regression.** Run `check_deterministic_results` against
   `SyntheticPITSource` (already merged, zero provenance columns) and
   confirm it still reports PASS for every method, unchanged from before
   this task — this is the existing behavior this task must not disturb.
5. Confirm `check_no_shared_mutable_state` and `check_schema_conformance`
   are untouched: run their existing tests unmodified and confirm they
   still pass (you should not need to change a single line in either
   function).

## Non-goals

- Do not touch determinism semantics for `trading_calendar()` — it is
  not a DataFrame-returning method and is not part of
  `_panel_method_specs`; nothing about it changes.
- Do not add a general "ignore these column names" mechanism — the fix
  is schema-driven (positive selection), not name-driven (negative
  exclusion list). If you find yourself writing a list of column names
  to skip, stop and reconsider.
- Do not touch `run_reference_compliance_suite` unless its own tests
  demonstrate it's now broken by this change (it shouldn't be — it
  exercises `SyntheticPITSource`, which has no provenance columns to
  begin with, so canonical-only comparison and full-frame comparison
  should be identical for it).

## Acceptance criteria

- All pre-existing tests (690 as of `f716b1a`) continue to pass, **except**
  any pre-existing test that specifically asserted the OLD (full-frame)
  comparison behavior in a way that's now superseded — if you find one,
  update it deliberately and say so in your report; do not leave a test
  contradicting the new, correct behavior.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/pit/compliance.py` and
  `tests/test_pit_compliance_suite.py`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-d1-canonical-determinism
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4b/task-p4b-d1-canonical-determinism`, touching only the two files
listed above.

## When done

Report: (a) the exact diff to `check_deterministic_results` and any new
helper; (b) confirmation that `check_no_shared_mutable_state`/
`check_schema_conformance` were not touched and their tests still pass
unmodified; (c) the three adversarial test results plus the
`SyntheticPITSource` regression test result; (d) full suite result; (e)
`git diff --stat`. Do not merge, do not touch `master`, do not modify
files outside the list above.
