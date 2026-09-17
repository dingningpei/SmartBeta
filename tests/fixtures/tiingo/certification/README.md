# P4B-9 certification fixtures

Real, live Tiingo captures backing `tests/test_tiingo_certification.py` and
`docs/phase4b_tiingo_certification.md`. Captured 2026-09-16 via
`TiingoClient` (`Authorization: Token` from `TIINGO_API_KEY`). Every file is
the verbatim Tiingo JSON body; the key is never stored. See `manifest.json`
for each file's `url_path` and the real HTTP status that produced it.

## Source-fixture files (replay-fed `TiingoPITSource`)

| File | What it is |
| --- | --- |
| `aapl_meta.json` | live `GET /tiingo/daily/AAPL`. |
| `aapl_eod_prices_2020-08-20_2020-09-05.json` | live AAPL EOD containing the 2020-08-31 4-for-1 split (`splitFactor=4.0`). |
| `aapl_statements_asreported_2026-03-01_2026-07-31.json` | live AAPL as-reported statements, fiscal 2026 Q3 + Q2. |
| `aapl_statements_normalized_2026-03-01_2026-07-31.json` | live AAPL normalized statements, fiscal 2026 Q3 + Q2. Coverage-consistent with the as-reported file. |
| `aapl_fundamentals_daily_2024-01-02_2024-01-05.json` | live AAPL daily metrics (`marketCap`), market-cap source. |
| `aapl_statements_asreported_2023-12-01_2024-02-29.json` | live AAPL as-reported, fiscal 2024 Q1 (period end 2023-12-30). |
| `aapl_statements_normalized_2023-12-01_2024-02-29.json` | live AAPL normalized, fiscal 2024 Q1. Used by the `build_panel` month-end test because December 2023's real trading month-end (2023-12-29) differs from the naive 2023-12-31. |
| `twtr_meta.json` | live delisted-TWTR metadata (endDate 2022-10-28). |
| `twtr_eod_prices_2022-10-20_2022-10-28.json` | live TWTR EOD ending in a single terminal zero-volume row. |
| `twtr_statements_asreported.json`, `twtr_statements_normalized.json` | **real HTTP 400** plan-tier error bodies (`Free and Power plans are limited to the DOW 30`). |
| `twtr_fundamentals_daily.json` | **real HTTP 400** plan-tier error body. |

## Ranged-query evidence files

| File | What it shows |
| --- | --- |
| `aapl_statements_asreported_2026-01-01_2026-12-31.json` | as-reported fiscal 2026 Q3 + Q2 + **Q1**. |
| `aapl_statements_normalized_2026-01-01_2026-12-31.json` | normalized fiscal 2026 Q3 + Q2 only (no Q1). |

`map_asreported_to_fundamentals` reconciles the Q3 + Q2 pair but correctly
raises `FiscalPeriodReconciliationError` for the wide file because Q1 has no
normalized match. This is the live evidence behind
`FUNDAMENTALS RANGED-QUERY SEMANTICS = NOT CERTIFIED` in the report.

## Plan-tier note

Under the current plan tier, Tiingo's fundamentals endpoints return HTTP 400
for every non-DOW-30 ticker. TWTR (delisted, non-DOW-30) therefore has no
fundamentals/daily coverage; the 400 bodies are recorded verbatim rather
than replaced with empty responses. This is why the certification record
reports `delisted_history_get_market_cap` (TWTR) as a FAIL and never
fabricates TWTR market-cap rows.
