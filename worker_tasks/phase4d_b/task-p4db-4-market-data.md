# Phase 4D-B Worker Task — P4DB-4: Market Data (Wave 2)

## Background (read this first)

Read `worker_tasks/phase4d_b/phase4d-b-plan.md` in full before starting,
especially policy 1 (`total_mcap` canonical) and evidence items 6-7 (CH4
depth, changing adj_factor — those are P4DB-5's and your fixtures'
territory respectively, but read them for context). This is a Wave 2
task; it depends on P4DB-1 (client — use `TushareClient`/
`replay_transport` only, never `ProxyTushareClient` directly) and P4DB-2
(identifiers — use `resolve_stock_id`). P4DB-1 and P4DB-2 are merged to
`master` before you start (Barrier 1). P4DB-5, P4DB-6, P4DB-7 are your
Wave 2 siblings — disjoint files, do not import from them, assume they
may not exist yet in your worktree.

**Your job:** implement three of `TushareAShareSource`'s seven methods'
worth of mapping logic: `get_raw_returns`, `get_market_cap`, and
`get_trading_status` — mirroring how Tiingo's P4B-4
(`returns_and_market_cap.py`) delivered the same three from one vendor
endpoint family. Read `smart_beta/pit/source.py`'s docstrings for each
method's exact contract (effective time, knowledge time, schema) before
writing any mapping code.

**Your working directory** will be a git worktree at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-4-market-data`
on branch `phase4d_b/task-p4db-4-market-data`, branched from `master`
after Barrier 1.

## File ownership

**You may create exactly:**

- `smart_beta/vendors/tushare/market_data.py`
- `tests/test_tushare_market_data.py`
- `tests/fixtures/tushare/market_data/`

**You must not modify** `client.py`, `proxy_client.py`,
`identifiers.py`, `calendar_source.py`, `corporate_actions.py`,
`fundamentals.py`, `listing.py`, `source.py`, `smart_beta/pit/*`,
`smart_beta/data/*`, or any existing test file.

## Frozen policy 1: `total_mcap` is canonical (read twice)

`daily_basic.total_mv` (or `total_share × close` cross-checked against
it — pick one as primary and use the other only as a sanity check,
document which) maps to `PIT_MARKET_CAP_SCHEMA.total_mcap`. This is the
value CH3/CH4 factor construction is expected to use, per the frozen
Phase 4D-A.2/A.3 evidence gate 6 (CH4 turnover feasibility) which was
built specifically on `daily_basic.total_share`.

`daily_basic.float_share`/`circ_mv`/`free_share` map to
`PIT_MARKET_CAP_SCHEMA.float_mcap` **as a named, documented,
diagnostic-only approximation** — mirror Tiingo's precedent exactly
(P4B-4/M2: `float_mcap == total_mcap` was an explicit, flagged
approximation when no distinct figure existed; here the situation is the
opposite — a distinct figure *does* exist, but it must still be
explicitly flagged as not independently certified as PIT-safe, since its
own historical accuracy/point-in-time-ness has not been verified any
more than `total_mcap`'s has). Never let a caller mistake `float_mcap`
here for a vendor-certified float-adjusted figure — the docstring on
your mapping function must say so explicitly, and P4DB-9 must carry a
`FLOAT MARKET CAP = DIAGNOSTIC ONLY, NOT INDEPENDENTLY CERTIFIED` line
mirroring Tiingo's own `FLOAT MARKET CAP = NOT CERTIFIED` precedent.

## `get_trading_status`

Unlike Tiingo (volume-derived `is_zero_volume` only), Tushare/China
A-shares have real, named trading-status concepts: suspension
(`suspend_d`/`daily`'s absence for a listed, not-yet-delisted security on
a trading day — investigate which endpoint gives this directly, e.g. the
legacy `suspend` endpoint referenced in the original Phase 4D-A spike),
limit-up/limit-down (derivable from `daily`'s `close` vs. the security's
board-specific limit band — **do not implement a general limit-band
detector**; the original spike found only the ST ±5% band empirically
verified, with ordinary ±10% and STAR/ChiNext ±20% bands explicitly
`NOT CERTIFIED` — carry that status forward unless you produce new,
real, live evidence for a specific band, and if you do, name the
specimen and band explicitly), and ST status (`stock_basic`'s `name`
field carrying an `ST`/`*ST` prefix, or a dedicated flag if one exists —
investigate empirically). `PIT_TRADING_STATUS_SCHEMA`'s flag columns are
open-ended per-vendor (read `smart_beta/pit/schema.py:160-164`) — add
`is_suspended`, `is_limit_up`, `is_limit_down`, `is_st` as your own
adapter-specific extra columns, populating only what you can actually
verify; a column that cannot be certified for a given band/case should
still be emitted (never omitted from the schema), but its value semantics
and certification status must be documented plainly in your report for
P4DB-9 to carry forward accurately (do not silently default an
unverified flag to `False` in a way that reads as "verified not
suspended/limit/ST" — if genuinely undeterminable, this is a real design
tension to flag explicitly rather than paper over; propose your best
resolution and explain the trade-off).

## Fixture inputs (real, confirmed-reachable specimens)

- `daily`, `000001.SZ`, `20230101`-`20230115` — raw returns basic case.
- `daily`, `000001.SZ`, `20130101`-`20131231` and `20140101`-`20140331`
  — the CH4 depth window (237 + 58 rows, 295 total, confirmed reachable;
  used by P4DB-9's CH4 feasibility certification, so your fixture must
  cover this exact range).
- `daily_basic`, same `000001.SZ` ranges above — for market cap and the
  confirmed real `total_share` step changes (512,335 → 819,736 →
  952,075) across this window.
- `daily`, `000001.SZ`, `19910101`-`19911231` — historical depth
  specimen (167 rows confirmed reachable).

## Required tests

- `get_raw_returns` schema-conforms to `PIT_RAW_RETURN_PANEL_SCHEMA`.
- `get_market_cap` schema-conforms to `PIT_MARKET_CAP_SCHEMA`, with
  `total_mcap` and `float_mcap` both populated and distinct where the
  underlying vendor fields are distinct.
- The real `total_share` step change at `000001.SZ` 2013-06-20 (512,335 →
  819,736) is reflected correctly in `total_mcap`'s underlying share
  count across that boundary (a computed-value test, not just schema
  shape).
- `get_trading_status` schema-conforms and includes the four flag
  columns; at least one real specimen per implemented flag (or an
  explicit `NOT CERTIFIED`-documented absence for a flag you could not
  verify).

## Non-goals

No corporate-action adjustment (P4DB-5). No fundamentals. No listing/
delisting logic (P4DB-7) — `get_trading_status`'s suspension flag is a
same-day signal, not a delisting determination.

## Acceptance criteria

- `.venv/bin/pytest` green, full suite unaffected.
- Zero live network calls in `pytest`.
- `total_mcap` vs. `float_mcap` certification status stated explicitly
  in your report, matching the language P4DB-9 needs verbatim.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-4-market-data
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4d_b/task-p4db-4-market-data`, touching only the files listed
above.

## When done

Report: (a) the exact mapping used for `total_mcap`/`float_mcap`,
including which vendor field was primary vs. sanity-check; (b) which
trading-status flags were populated with real evidence vs. left `NOT
CERTIFIED`, per band/case; (c) the total_share step-change verification
result; (d) test results; (e) `git diff --stat`. Do not merge, do not
touch `master`.
