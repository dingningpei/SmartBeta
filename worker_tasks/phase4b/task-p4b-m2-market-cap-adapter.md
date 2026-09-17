# Phase 4B Worker Task — P4B-M2: Working Market-Cap Adapter (Market Cap Bridge, part 2)

## Background (read this first)

Read `worker_tasks/phase4b/phase4b-plan.md` and
`worker_tasks/phase4b/task-p4b-m1-fundamentals-daily-client.md` first.

**This task starts only after P4B-M1 is reviewed APPROVE and merged to
`master`.** Confirm your worktree's `master` merge-base actually contains
`TiingoClient.get_fundamentals_daily` before starting; if it does not,
stop and report rather than proceeding against a stale base.

**Your task, P4B-M2, replaces `map_eod_to_market_cap`'s current
permanent-failure behavior with the fallback the original, frozen P4B-4
spec already authorized**, now that P4B-M1 has closed the one blocking
gap (no client method existed for
`GET /tiingo/fundamentals/{ticker}/daily`). You are amending an
already-merged file (`smart_beta/vendors/tiingo/returns_and_market_cap.py`)
— this is expected and intentional, not a boundary violation, exactly as
the Planner instructed when creating this task.

**The one thing this task must never do:** present the approximation as
if it were Tiingo's own float-adjusted figure. Tiingo has no distinct
float-adjusted market-cap figure under current access (confirmed by
P4B-4's original investigation and unchanged by P4B-M1). `float_mcap`
becomes numerically equal to `total_mcap` *only* as a named,
documented, testably-flagged approximation.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-m2-market-cap-adapter`
on branch `phase4b/task-p4b-m2-market-cap-adapter`, branched from `master`
after P4B-M1 merges. Run tests with `.venv/bin/pytest`.

## File ownership

**You may modify/create exactly these:**

- `smart_beta/vendors/tiingo/returns_and_market_cap.py` (amend)
- `tests/test_tiingo_returns_market_cap.py` (amend)
- `tests/fixtures/tiingo/returns_market_cap/` — new fixture files only in
  this existing directory; you may also *reuse* the existing
  `aapl_fundamentals_daily_2024-01-02_2024-01-05.json` file already
  captured there by the original P4B-4 investigation — it is real,
  already-verified live data; do not re-capture it, do not modify it.

**You must not modify anything else.** In particular:
- Do not modify `smart_beta/vendors/tiingo/client.py` (P4B-M1's merged,
  frozen deliverable — you only call `get_fundamentals_daily`, never
  change it).
- Do not modify `identifiers.py`, `calendar_source.py`,
  `corporate_actions.py`, `fundamentals.py`, `listing.py`.
- Do not touch `smart_beta/pit/*` or `pyproject.toml`.
- Do not change `map_eod_to_raw_returns` or `map_eod_to_trading_status` in
  any way — this task is scoped to `map_eod_to_market_cap` only.
- Do not change P4B-2's identifier policy or P4B-7's listing/delisting
  policy. This task has nothing to do with either.

## The API you consume (already merged — read the actual file)

```python
# smart_beta.vendors.tiingo.client (P4B-1 + P4B-M1, frozen)
class TiingoClient:
    def get_fundamentals_daily(
        self, ticker: str, start_date: str | None = None, end_date: str | None = None
    ) -> list[dict]:
        """GET /tiingo/fundamentals/{ticker}/daily. Confirmed keys:
        date, marketCap, enterpriseVal, peRatio, pbRatio, trailingPEG1Y."""
```

(Exact signature may have shifted slightly during P4B-M1's actual
implementation — read the real merged `client.py`, this is a summary
written before it existed.)

## What to build

Replace the body of `map_eod_to_market_cap`. It currently always raises
`TiingoMarketCapUnavailableError`; this task changes its inputs and
behavior so it can succeed using daily-fundamentals rows.

```python
#: Named, testable flag: float_mcap in this adapter's output is NOT
#: Tiingo's own float-adjusted figure -- none exists under current
#: access. It is total_mcap, repeated. Any caller (including P4B-9's
#: certification) that treats this as a verified float-adjusted market
#: cap is wrong; check this flag first.
FLOAT_MARKET_CAP_IS_APPROXIMATED = True

def map_eod_to_market_cap(
    daily_fundamentals_rows: list[dict],
    stock_id: str,
    source_endpoint: str = "get_fundamentals_daily",
) -> pd.DataFrame:
    """Map Tiingo daily-fundamentals rows to a market-cap panel.

    Conforms to PIT_MARKET_CAP_SCHEMA, plus provenance columns.

    `total_mcap` = the row's `marketCap` field, as Tiingo reports it,
    unmodified. `float_mcap` = the SAME value, because Tiingo has no
    distinct float-adjusted figure under current access -- this is an
    explicit, named approximation (see FLOAT_MARKET_CAP_IS_APPROXIMATED),
    never presented as a verified float-adjusted figure.

    A row with a missing or null `marketCap` is excluded from the output
    (no fabricated value) -- do not raise for this case, since a
    genuinely absent day is a normal, expected gap in daily-fundamentals
    coverage (P4B-4's own investigation found history begins
    2023-09-18 on this account), not the same problem the old
    TiingoMarketCapUnavailableError guarded against (no data source at
    all). If `daily_fundamentals_rows` is empty, return an empty,
    schema-conformant frame -- do not raise.

    This function's PARAMETER changed from `eod_rows` to
    `daily_fundamentals_rows` because market cap is no longer derived
    from EOD prices at all -- update every caller/test accordingly
    (there are none outside this module and its own test file as of
    this task).
    """
```

`TiingoMarketCapUnavailableError` (the exception class) may be removed
entirely if nothing in the module raises it anymore, or kept and
documented as "no longer raised by this function, retained only if a
future gap reappears" — your call, but do not leave dead, undocumented
code; whichever you choose, update `__all__` and the module docstring's
market-cap section to match reality, not the old investigation's
conclusion (which is now superseded by this task, not deleted from
history — the old docstring section should be updated to say P4B-M1/M2
closed the gap, not left claiming the gap is still open).

## Fixture inputs

Reuse the existing, already-live-captured
`tests/fixtures/tiingo/returns_market_cap/aapl_fundamentals_daily_2024-01-02_2024-01-05.json`
(keys: `date, marketCap, enterpriseVal, peRatio, pbRatio,
trailingPEG1Y`). If you need a second specimen (e.g. one row with a null
`marketCap` to exercise the exclusion path), you may hand-construct a
small, clearly-labeled variant (`_constructed: true`-style marker or an
equivalent note in the fixture/README) rather than needing a fresh live
capture — a null-value gap is a structural case, not a vendor-behavior
claim requiring independent verification.

## Required tests

1. **`total_mcap` matches the real `marketCap` value** from the reused
   AAPL fixture, for each row present.
2. **`float_mcap == total_mcap` exactly**, for the same rows.
3. **The approximation cannot be silently mistaken for a real
   float-adjusted figure — this is the most important test in this
   task, make it concrete:**
   - `FLOAT_MARKET_CAP_IS_APPROXIMATED` is `True` and is exported in
     `__all__`.
   - The module docstring contains an explicit, greppable sentence
     stating the approximation plainly (e.g. asserting a required
     substring is present in `returns_and_market_cap.__doc__`), mirroring
     the existing pattern already used for P4B-6's `is_restatement`
     docstring test and P4B-5's `DIVIDEND_SEMANTICS_CERTIFIED` framing.
   - A test named to make the intent unmistakable, e.g.
     `test_float_market_cap_is_approximation_not_verified_vendor_figure`,
     that fails if a future edit ever sets `FLOAT_MARKET_CAP_IS_APPROXIMATED`
     to `False` without also changing the computation to use a genuinely
     distinct figure (i.e., assert the flag and the equality both hold
     together, so the two cannot silently drift apart).
4. **Missing/null `marketCap` row is excluded, not fabricated** — no
   exception, no zero, no forward-fill.
5. **Empty input returns an empty, schema-conformant frame.**
6. **Schema conformance** via `validate_panel` against `PIT_MARKET_CAP_SCHEMA`.
7. **Provenance columns present**, with `_source_endpoint` reflecting
   `get_fundamentals_daily` (or whatever literal string you choose,
   documented).
8. **No input mutation** of `daily_fundamentals_rows`.
9. Confirm `map_eod_to_raw_returns` and `map_eod_to_trading_status` (both
   untouched by this task) still pass their existing tests unchanged —
   run the full existing `test_tiingo_returns_market_cap.py` file, not
   just your new tests.

## Non-goals

- Do not attempt to obtain a genuinely distinct float-adjusted figure
  from any other endpoint. If you discover one exists, stop and report
  it as a finding rather than expanding this task's scope.
- Do not change `TiingoClient` in any way.
- Do not change identifier resolution, listing/delisting, corporate
  actions, or fundamentals mapping.
- Do not add a new `Settings` field or touch `pyproject.toml`.

## Acceptance criteria

- All pre-existing tests continue to pass unchanged, **except** any test
  in `test_tiingo_returns_market_cap.py` that asserted the old
  always-raises behavior for `map_eod_to_market_cap` — those must be
  replaced with tests of the new behavior, not left contradicting it.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/vendors/tiingo/returns_and_market_cap.py`,
  `tests/test_tiingo_returns_market_cap.py`, and (only if you added one)
  a new file under `tests/fixtures/tiingo/returns_market_cap/`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-m2-market-cap-adapter
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4b/task-p4b-m2-market-cap-adapter`, touching only the files listed
above.

## When done

Report: (a) the exact new `map_eod_to_market_cap` signature; (b) whether
you removed `TiingoMarketCapUnavailableError` or kept it, and why; (c)
confirmation that `FLOAT_MARKET_CAP_IS_APPROXIMATED` and its accompanying
tests exist exactly as required; (d) test results; (e) `git diff --stat`.
Do not merge, do not touch `master`, do not modify files outside the list
above.
