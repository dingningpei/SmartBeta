# Phase 4B Worker Task — P4B-M1: Fundamentals-Daily Client Endpoint (Market Cap Bridge, part 1)

## Background (read this first)

Master is at `18e8308` (Barrier 2 complete: P4B-1 through P4B-7 all merged,
659/659 tests green). Read `worker_tasks/phase4b/phase4b-plan.md` for the
overall architecture before starting.

**Why this task exists.** P4B-4 (already merged,
`smart_beta/vendors/tiingo/returns_and_market_cap.py`) investigated market
cap and found: Tiingo's EOD price endpoint has no market-cap or
share-count field, but `GET /tiingo/fundamentals/{ticker}/daily` **does**
return a real `marketCap` figure under current account access (confirmed
live, AAPL, keys: `date, marketCap, enterpriseVal, peRatio, pbRatio,
trailingPEG1Y`; no distinct float-adjusted figure). P4B-4 correctly
refused to reach around `TiingoClient`'s boundary to call that endpoint
itself — `map_eod_to_market_cap` always raises
`TiingoMarketCapUnavailableError` today, exactly as its own spec required
when a needed client method doesn't exist.

**Your task, P4B-M1, closes exactly that one gap: add the missing
`TiingoClient` method.** This is a narrow amendment to an already-merged
file, not a redesign — one new method, following the exact shape of the
four that already exist. **You do not touch market-cap semantics at
all** — `float_mcap`/`total_mcap`/the approximation policy are P4B-M2's
job, in a separate, later task, after this one merges.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-m1-fundamentals-daily-client`
on branch `phase4b/task-p4b-m1-fundamentals-daily-client`, branched from
`master` at `18e8308`. Run tests with `.venv/bin/pytest`.

## File ownership

**You may modify/create exactly these:**

- `smart_beta/vendors/tiingo/client.py` (amend — add one method, nothing
  else)
- `tests/test_tiingo_client.py` (amend — add tests for the new method)
- `tests/fixtures/tiingo/client/` — new fixture files only, in this
  existing directory (do not delete or modify any existing file in it)

**You must not modify anything else.** In particular:
- Do not modify `scripts/fetch_tiingo_fixtures.py`. It is P4B-1's file.
  Its own docstring invites later tasks to "reuse this same technique,"
  but no Wave-2 task edited that file directly (verified: P4B-4/5/6/7 each
  captured their own fixtures independently) — follow the same precedent.
  Capture your fixture with a small, throwaway, uncommitted one-off script
  or an interactive `python -c` call using `TiingoClient` directly; do not
  commit a second fixture-capture script.
- Do not touch `smart_beta/vendors/tiingo/returns_and_market_cap.py`,
  `corporate_actions.py`, `fundamentals.py`, `listing.py`, `identifiers.py`,
  `calendar_source.py`, `smart_beta/vendors/__init__.py`,
  `smart_beta/vendors/tiingo/__init__.py`.
- Do not touch `smart_beta/pit/*` or `pyproject.toml`.
- Do not change `map_eod_to_market_cap`'s current fail-closed behavior —
  that is P4B-M2's job.
- Do not add a new fixture subdirectory. Everything you capture lives
  under the existing `tests/fixtures/tiingo/client/`.

## What to build

Add exactly one method to `TiingoClient`, in the same style and using the
same internal helpers as the four existing methods:

```python
def get_fundamentals_daily(
    self,
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """Daily fundamentals metrics for ``ticker``.

    Endpoint: ``GET /tiingo/fundamentals/{ticker}/daily``. Returns the
    parsed JSON array of per-day dicts verbatim -- confirmed live keys:
    ``date, marketCap, enterpriseVal, peRatio, pbRatio, trailingPEG1Y``.
    No field is renamed, reshaped, or dropped. This method has no
    opinion about market-cap semantics -- it only fetches; P4B-M2 decides
    what to do with the result.
    """
    path = f"/tiingo/fundamentals/{urllib.parse.quote(ticker)}/daily"
    params = self._fundamentals_params(start_date, end_date)
    return self._call(path, params)
```

Reuse the existing `_fundamentals_params` helper (already private in
`client.py`) exactly as `get_fundamentals_asreported`/
`get_fundamentals_normalized` do — do not duplicate its logic. `start_date`
and `end_date` are the only parameters this adapter actually needs (P4B-4's
own investigation already confirmed this endpoint takes the same
`startDate`/`endDate` convention as the other fundamentals endpoints); do
not add parameters beyond what P4B-4's design requires.

Add the method to `client.py`'s module docstring's endpoint inventory if
one exists, following whatever documentation convention the file already
uses for its other three data methods — read the actual file first.

## Fixture inputs

Capture your own real, recorded specimen(s) under the existing
`tests/fixtures/tiingo/client/` directory, using a real key from
`TIINGO_API_KEY` (never committed) and a one-off script or interactive
call — not `scripts/fetch_tiingo_fixtures.py` (see File ownership above).
At minimum:

- AAPL `GET /tiingo/fundamentals/{ticker}/daily`, a short window (P4B-4's
  own investigation used `2024-01-02..2024-01-05` for AAPL; reusing that
  exact window keeps this consistent with the already-documented finding,
  but capture it yourself independently rather than copying P4B-4's
  fixture file across the task-ownership boundary).
- Update `tests/fixtures/tiingo/client/manifest.json` (already exists,
  owned collectively by this shared directory) to add your new
  recording's entry in the same `{filename: {url_path, status_code}}`
  shape the existing entries use. Do not alter any existing entry.

## Required tests

1. **Passthrough correctness.** Given a `replay_transport` fixture built
   from your recorded body, `get_fundamentals_daily` returns exactly that
   parsed data verbatim (field names, values, ordering unchanged) —
   mirroring the existing `test_get_fundamentals_asreported_returns_
   recorded_body_verbatim`-style test.
2. **Date params are optional and correctly passed**, mirroring the
   existing `test_fundamentals_date_params_are_optional` test's structure
   for this new method.
3. **No live network call.** The existing autouse `urlopen` tripwire
   fixture in `test_tiingo_client.py` already covers this for every test
   in the file; confirm your new tests pass under it (they will, as long
   as you inject `transport=` like every other test in the file).
4. **Manifest integrity.** Extend the existing
   `test_every_manifest_entry_has_a_readable_json_body` /
   `test_error_specimen_is_the_only_non_200_recording`-style checks (or
   confirm they still pass unmodified) so your new manifest entry is
   covered by whatever meta-tests already validate the manifest's shape.

## Non-goals

- Do not implement, call, or reference `map_eod_to_market_cap` or any
  other Wave-2 module. This task is client-layer only.
- Do not decide or document the `float_mcap`/`total_mcap` approximation
  policy — that text belongs in P4B-M2's module, not here.
- Do not add retries, caching, or rate-limiting.
- Do not add a new dependency to `pyproject.toml`.

## Acceptance criteria

- All pre-existing tests (659 as of `18e8308`) continue to pass
  unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/vendors/tiingo/client.py`,
  `tests/test_tiingo_client.py`, and new files (plus the amended
  `manifest.json`) under `tests/fixtures/tiingo/client/` — no new
  top-level script, no changes to `scripts/fetch_tiingo_fixtures.py`.
- No credential value appears anywhere in the diff.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-m1-fundamentals-daily-client
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4b/task-p4b-m1-fundamentals-daily-client`, touching only the files
listed above.

## When done

Report: (a) the exact `get_fundamentals_daily` signature implemented; (b)
the real response keys you captured, confirming or correcting P4B-4's
already-documented finding (`date, marketCap, enterpriseVal, peRatio,
pbRatio, trailingPEG1Y`); (c) test results; (d) `git diff --stat`. Do not
merge, do not touch `master`, do not modify files outside the list above.
