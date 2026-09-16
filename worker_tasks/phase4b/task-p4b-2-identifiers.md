# Phase 4B Worker Task — P4B-2: Identifier Policy (Wave 1)

## Background (read this first)

Read `worker_tasks/phase4b/phase4b-plan.md` in full first — it is the
frozen architecture record for all nine Phase 4B tasks. Master is at
`aae4c72` (tag `phase3-complete`, 383/383 tests green).

**Your task, P4B-2, decides what `stock_id` means for the Tiingo
adapter.** Phase 3's `PITDataSource` interface treats `stock_id` as an
opaque, stable string key — every method's schema keys on it. For China A
shares (the original design target) a stock's numeric code is a reliable,
stable, security-level identifier. For US equities this is genuinely not
obvious: a ticker can change (renames, relistings, ticker reuse after a
delisting), and CIK — the SEC's identifier — names an *issuer*, not
necessarily a single *security* (one issuer can have multiple share
classes/securities under one CIK). This task's job is to determine,
empirically, what Tiingo actually offers as a stable security-level
identifier and freeze the resolution policy.

This is one of three Wave 1 tasks (P4B-1 client, P4B-3 calendar). You do
not depend on either and must not import from them — build your own
minimal, self-contained investigation.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-2-identifiers`
on branch `phase4b/task-p4b-2-identifiers`, branched from `master` at
`aae4c72`. Run tests with `.venv/bin/pytest`.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/vendors/tiingo/identifiers.py`
- `tests/test_tiingo_identifiers.py`
- `tests/fixtures/tiingo/identifiers/` — any fixture files you need

**You must not modify anything else.** In particular:

**Never create, under any circumstance:**
- `smart_beta/vendors/__init__.py`
- `smart_beta/vendors/tiingo/__init__.py`

These two files are unconditionally and solely owned by P4B-1. P4B-1 not
yet being merged when you start from the Wave-1 base commit is the
**expected** situation under the parallel Wave-1 DAG (P4B-1, P4B-2, P4B-3
all start from the same commit) — it is not evidence of an early start,
and it is not something to flag. Do not treat it as an anomaly, and do
not work around it by creating either marker file yourself.

You do not need either file to exist to do your work: write
`smart_beta/vendors/tiingo/identifiers.py` directly. Python's implicit
namespace-package support (PEP 420) means this module imports correctly
as `smart_beta.vendors.tiingo.identifiers` with no `__init__.py` present
anywhere in `vendors/` or `vendors/tiingo/` — you do not need to verify
this claim, just rely on it; if your import genuinely fails for an
unrelated reason, report the actual error rather than adding a marker
file to "fix" it.

Also do not touch `pyproject.toml`, anything under `smart_beta/pit/`,
`smart_beta/data/`, `smart_beta/config/`, any other `vendors/tiingo/*.py`
module, or any fixture subdirectory other than
`tests/fixtures/tiingo/identifiers/`.

## What to build

```python
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedIdentifier:
    """The outcome of resolving one security's stock_id, carrying enough
    provenance for a later certification report to know whether identifier
    continuity is trustworthy."""
    stock_id: str
    is_permanent: bool  # True iff derived from a permanent, security-level
                         # Tiingo field (e.g. permaTicker); False iff a
                         # ticker fallback was used.
    source_field: str    # the literal Tiingo response field name this was
                          # read from, e.g. "permaTicker" or "ticker" --
                          # for auditability.


def resolve_stock_id(meta: dict) -> ResolvedIdentifier:
    """Given a raw Tiingo security-metadata dict (the kind of object
    TiingoClient.get_meta returns -- but this function takes the dict
    directly, not a client, so it stays independent of P4B-1's code this
    wave), determine stock_id:

    1. If a permanent, security-level identifier field is present and
       non-empty (your investigation below determines the real field
       name -- do not assume it is literally "permaTicker" without
       confirming against a real response), use it, is_permanent=True.
    2. Otherwise, fall back to the ticker field, is_permanent=False.

    Never uses CIK, and never uses any issuer-level field, as stock_id --
    an issuer-level identifier can legitimately map to more than one
    security, which would violate every PITDataSource schema's assumption
    that stock_id names exactly one security.
    """
```

## Required investigation (this is the actual point of the task)

Using a real Tiingo API key (`TIINGO_API_KEY` environment variable) and
plain `urllib.request`/`json` calls — **do not import
`smart_beta.vendors.tiingo.client`**, since that is P4B-1's sibling
Wave-1 deliverable and this task must not depend on it — fetch and save
raw metadata JSON under `tests/fixtures/tiingo/identifiers/` for:

1. **AAPL** (active, long continuous listing).
2. **TWTR** (delisted — confirms whether the permanent-identity field
   still resolves for a security no longer trading).
3. **A real historical ticker-rename case, if you can find and confirm
   one under current access** (candidates: Facebook/Meta, FB -> META, or
   an equivalent well-documented rename). Try to determine empirically
   whether Tiingo's permanent-identity field stayed constant across the
   rename while the ticker itself changed. **If Tiingo's data model does
   not actually let you query pre-rename history under the post-rename
   ticker (a real, plausible limitation), document that plainly as a
   finding — do not force a positive result, and do not spend excessive
   time on this if the access model makes it genuinely unresolvable; one
   clear attempt and a clear negative finding is an acceptable outcome.**

Write your findings as a short, explicit paragraph at the top of
`identifiers.py`'s module docstring: which field you used, whether it
was present/stable/security-level for both AAPL and TWTR, and the outcome
of the rename investigation (positive, negative, or inconclusive-and-why).

## Required tests

1. Given a real (or realistically-shaped, derived from your captured
   fixture) AAPL meta dict with the permanent-identity field present,
   `resolve_stock_id` returns it with `is_permanent=True` and the correct
   `source_field`.
2. Given the real TWTR meta dict (delisted), same assertion — proving the
   policy also resolves correctly for a security that no longer trades.
3. Given a meta dict with the permanent-identity field absent or empty
   (constructed by you, simulating the fallback case even if you never
   observed it for real), `resolve_stock_id` returns the ticker with
   `is_permanent=False`.
4. A dedicated test (or a clear assertion inside an existing one) that
   `resolve_stock_id` never reads a CIK-like field, by constructing a meta
   dict where a CIK-shaped field is present but the permanent-identity
   field is absent, and confirming the CIK value is NOT returned as
   `stock_id` (the fallback to ticker must fire instead).

## Non-goals

- Do not implement any HTTP client — read/construct dicts directly.
- Do not implement any PIT schema mapping, EOD/fundamentals fetching, or
  anything beyond `stock_id` resolution.
- Do not decide here whether "IDENTIFIER CONTINUITY = NOT CERTIFIED" gets
  written anywhere — that literal line belongs to P4B-9's certification
  report, informed by your `is_permanent` finding, not authored by you.

## Acceptance criteria

- All 383 pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/vendors/tiingo/identifiers.py`,
  `tests/test_tiingo_identifiers.py`, and files under
  `tests/fixtures/tiingo/identifiers/` — no `__init__.py` file anywhere in
  the diff.
- The module docstring contains an explicit, readable statement of your
  empirical finding (present/stable/security-level or not) for AAPL and
  TWTR, and the outcome of the rename investigation.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-2-identifiers
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch `phase4b/task-p4b-2-identifiers`,
touching only the files listed above.

## When done

Report: (a) the exact field name you found for Tiingo's permanent
identity concept (or that none exists, if that's the real finding); (b)
whether it held up for both AAPL and TWTR; (c) the outcome of the rename
investigation; (d) test results; (e) `git diff --stat`. Do not merge, do
not touch `master`, do not modify files outside the list above.
