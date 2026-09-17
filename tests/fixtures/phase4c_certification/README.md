# P4C-11 certification fixtures

These files exist only to feed `tests/test_phase4c_certification.py`'s real,
fixture-replayed `TiingoPITSource` end-to-end paths (task P4C-11). Per the
frozen per-task fixture-ownership rule, they are **verbatim copies**, not
imports, of already-committed Phase 4B / Phase 4C fixtures. The API key is
never stored, and no test here touches the network.

## Provenance (exact source path for each copied file)

| File here | Copied from | What it is |
| --- | --- | --- |
| `aapl_meta.json` | `tests/fixtures/tiingo/certification/aapl_meta.json` | live `GET /tiingo/daily/AAPL`; identifier/listing source. |
| `twtr_meta.json` | `tests/fixtures/tiingo/certification/twtr_meta.json` | live `GET /tiingo/daily/TWTR`; delisted-security identifier/listing source. |
| `aapl_eod_prices_2020-08-20_2020-09-05.json` | `tests/fixtures/tiingo/certification/aapl_eod_prices_2020-08-20_2020-09-05.json` | live AAPL EOD containing the 2020-08-31 4-for-1 split. |
| `aapl_fundamentals_daily_2020-08-20_2020-09-05.json` | `tests/fixtures/phase4c/benchmarks/aapl_fundamentals_daily_2020-08-20_2020-09-05.json` | locally authored market-cap stand-in over the split window (Tiingo daily history on this account begins 2023); used only so the pipeline has a weight for the real return series. |
| `aapl_fundamentals_daily_2024-01-02_2024-01-05.json` | `tests/fixtures/tiingo/certification/aapl_fundamentals_daily_2024-01-02_2024-01-05.json` | live AAPL daily metrics (`marketCap`). |
| `aapl_statements_asreported_2026-03-01_2026-07-31.json` | `tests/fixtures/tiingo/certification/aapl_statements_asreported_2026-03-01_2026-07-31.json` | as-reported fiscal 2026 Q3 + Q2 (coverage-consistent). |
| `aapl_statements_normalized_2026-03-01_2026-07-31.json` | `tests/fixtures/tiingo/certification/aapl_statements_normalized_2026-03-01_2026-07-31.json` | normalized fiscal 2026 Q3 + Q2. |
| `aapl_statements_asreported_2026-01-01_2026-12-31.json` | `tests/fixtures/tiingo/certification/aapl_statements_asreported_2026-01-01_2026-12-31.json` | as-reported fiscal 2026 Q3 + Q2 + **Q1** (the real coverage gap). |
| `aapl_statements_normalized_2026-01-01_2026-12-31.json` | `tests/fixtures/tiingo/certification/aapl_statements_normalized_2026-01-01_2026-12-31.json` | normalized fiscal 2026 Q3 + Q2 only (no Q1). |
| `twtr_eod_prices_2022-10-20_2022-10-28.json` | `tests/fixtures/tiingo/certification/twtr_eod_prices_2022-10-20_2022-10-28.json` | live TWTR EOD; the terminal 2022-10-28 row has `volume == 0`. |
| `twtr_statements_asreported.json` | `tests/fixtures/tiingo/certification/twtr_statements_asreported.json` | real TWTR plan-tier HTTP 400 body. |
| `twtr_statements_normalized.json` | `tests/fixtures/tiingo/certification/twtr_statements_normalized.json` | real TWTR plan-tier HTTP 400 body. |
| `twtr_fundamentals_daily.json` | `tests/fixtures/tiingo/certification/twtr_fundamentals_daily.json` | real TWTR plan-tier HTTP 400 body. |
| `rgen_fundamentals_asreported_error.json` | `tests/fixtures/tiingo/client/rgen_fundamentals_asreported_error.json` | real RGEN plan-tier HTTP 400 body (restatement-vintage evidence). |

Every file is byte-for-byte the source file; no value is fabricated,
altered, or re-serialized. The `{client,source,certification}` fixture sets'
own READMEs document their live-capture provenance and their deliberate,
labeled derivations.
