# P4B-8 composition fixtures

These specimens exist only to prove correct **composition and delegation** in
`TiingoPITSource`; the Wave 1/2 mapping modules were already tested against
their own fixtures. Each file here is a copy of a real captured Tiingo
response (or a clearly-labelled derivation of one), re-homed under this
task's fixture directory per the frozen per-task fixture-ownership rule.

| File | Provenance |
|---|---|
| `aapl_meta.json` | verbatim copy of `tests/fixtures/tiingo/client/aapl_meta.json` (live `GET /tiingo/daily/AAPL`) |
| `aapl_eod_prices_2020-08-20_2020-09-05.json` | verbatim copy of the P4B-1/P4B-4/P4B-5 AAPL EOD recording (contains the 2020-08-31 4-for-1 split) |
| `aapl_fundamentals_normalized.json` | verbatim copy of the P4B-6 normalized statements recording |
| `aapl_fundamentals_asreported.json` | the real P4B-6 as-reported recording with the **2026 Q1 statement removed** (see below) |
| `aapl_fundamentals_daily_2024-01-02_2024-01-05.json` | verbatim copy of the P4B-M1/M2 daily-fundamentals recording |
| `twtr_meta.json` | verbatim copy of the P4B-1/P4B-7 delisted-TWTR metadata |
| `twtr_eod_prices_2022-10-20_2022-10-28.json` | verbatim copy of the P4B-4 delisted-TWTR EOD recording |

## Why `aapl_fundamentals_asreported.json` is trimmed

The real captured as-reported statement list contains fiscal 2026 Q3, Q2,
**and Q1**, while the real normalized capture contains only Q3 and Q2. P4B-6
documents this as a genuine live coverage gap: the fail-closed mapper rejects
any as-reported statement with no unique normalized match, so passing the
full unbounded pair to `map_asreported_to_fundamentals` raises by design
(asserted in `tests/test_tiingo_fundamentals.py::
test_real_coverage_gap_q1_aborts_whole_asreported_batch`).

In production, `TiingoPITSource.get_fundamentals` passes the requested date
range through to the statements endpoints, so the vendor returns a
range-consistent subset. Offline, `replay_transport` keys on path only and
ignores the date params, so a recording that represents the ranged response
must already be coverage-consistent. This file is therefore the real Q3 + Q2
statements verbatim, with the out-of-coverage Q1 statement removed. No value
is fabricated or altered.

## The `replay_transport` path-collision gap (reported, not patched)

`get_fundamentals` calls both `get_fundamentals_asreported` and
`get_fundamentals_normalized`, which hit the **same** URL path
(`/tiingo/fundamentals/AAPL/statements`) and differ only by the
`asReported=true` query parameter. `replay_transport` deliberately ignores
query params (`smart_beta/vendors/tiingo/client.py`), so it cannot return
two different bodies for this one endpoint. The source tests use a thin
param-aware wrapper around `replay_transport` for the statements path only;
this is a test-harness accommodation for a real Wave 1 fixture-replay
limitation, not a change to the client.
