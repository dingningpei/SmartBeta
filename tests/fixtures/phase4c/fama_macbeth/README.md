# P4C-9 Fama-MacBeth fixtures

These files exist only to feed `tests/test_fama_macbeth_pipeline.py`'s
real, fixture-replayed `TiingoPITSource` end-to-end paths (task P4C-9).
Per the frozen per-task fixture-ownership rule, they are **verbatim
copies**, not imports, of already-committed Phase 4B fixtures. The API key
is never stored, and no test here touches the network.

## Provenance (exact source path for each copied file)

All files were copied byte-for-byte from
`tests/fixtures/tiingo/certification/` (the P4B-9 live-capture set).

| File here | What it is |
| --- | --- |
| `aapl_meta.json` | live `GET /tiingo/daily/AAPL`; listing/identifier source. |
| `aapl_eod_prices_2020-08-20_2020-09-05.json` | live AAPL EOD containing the 2020-08-31 4-for-1 split; raw returns / trading status / corporate actions. |
| `aapl_fundamentals_daily_2024-01-02_2024-01-05.json` | live AAPL daily metrics (`marketCap`); the market-cap source. |
| `aapl_statements_asreported_2026-03-01_2026-07-31.json` | as-reported fiscal 2026 Q3 + Q2 (coverage-consistent). |
| `aapl_statements_normalized_2026-03-01_2026-07-31.json` | normalized fiscal 2026 Q3 + Q2. |
| `aapl_statements_asreported_2026-01-01_2026-12-31.json` | as-reported fiscal 2026 Q3 + Q2 + **Q1**. |
| `aapl_statements_normalized_2026-01-01_2026-12-31.json` | normalized fiscal 2026 Q3 + Q2 only (no Q1). |

The two `*_2026-01-01_2026-12-31.json` files are the real ranged-query
evidence behind `FUNDAMENTALS RANGED-QUERY SEMANTICS = NOT CERTIFIED` in
`docs/phase4b_tiingo_certification.md`: Q1 has no normalized match, so the
fail-closed mapper raises `FiscalPeriodReconciliationError` for the whole
batch. The `*_2026-03-01_2026-07-31.json` pair reconciles.
