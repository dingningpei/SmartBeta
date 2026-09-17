# P4C-6 research-inputs fixtures

These files exist only to feed `tests/test_research_inputs.py`'s real,
fixture-replayed `TiingoPITSource` end-to-end paths (task P4C-6). Per the
frozen per-task fixture-ownership rule, they are **verbatim copies**, not
imports, of already-committed Phase 4B fixtures. The API key is never
stored, and no test here touches the network.

## Provenance (exact source path for each copied file)

| File here | Copied from | What it is |
| --- | --- | --- |
| `aapl_meta.json` | `tests/fixtures/tiingo/source/aapl_meta.json` | live `GET /tiingo/daily/AAPL` (itself a copy of `tests/fixtures/tiingo/client/aapl_meta.json`). |
| `aapl_eod_prices_2020-08-20_2020-09-05.json` | `tests/fixtures/tiingo/source/aapl_eod_prices_2020-08-20_2020-09-05.json` | live AAPL EOD containing the 2020-08-31 4-for-1 split (`splitFactor=4.0`); source for raw returns, trading status, corporate actions, and listing history. |
| `aapl_fundamentals_daily_2024-01-02_2024-01-05.json` | `tests/fixtures/tiingo/source/aapl_fundamentals_daily_2024-01-02_2024-01-05.json` | live AAPL daily metrics (`marketCap`); the market-cap source that yields **both** `float_mcap` and `total_mcap`. |
| `aapl_fundamentals_asreported.json` | `tests/fixtures/tiingo/source/aapl_fundamentals_asreported.json` | real AAPL as-reported statements (fiscal 2026 Q3 + Q2) with the out-of-coverage Q1 statement removed, so the as-reported/normalized pair reconciles under a ranged query. Used for the `get_fundamentals` success path. |
| `aapl_fundamentals_normalized.json` | `tests/fixtures/tiingo/source/aapl_fundamentals_normalized.json` | real AAPL normalized statements (fiscal 2026 Q3 + Q2), coverage-consistent with the file above. |
| `aapl_statements_asreported_2026-01-01_2026-12-31.json` | `tests/fixtures/tiingo/certification/aapl_statements_asreported_2026-01-01_2026-12-31.json` | live, real AAPL as-reported ranged response that **includes** fiscal 2026 Q1. This is the real coverage gap: Q1 has no normalized match, so the fail-closed mapper raises. |
| `aapl_statements_normalized_2026-01-01_2026-12-31.json` | `tests/fixtures/tiingo/certification/aapl_statements_normalized_2026-01-01_2026-12-31.json` | live normalized ranged response carrying only 2026 Q3 + Q2 (no Q1). |

Every file is byte-for-byte the source file; no value is fabricated,
altered, or re-serialized. The `{client,source,certification}` fixture sets'
own READMEs document their live-capture provenance and their deliberate
derivations (the trimmed as-reported Q1 removal is that source set's
documented, labeled derivation).

## Fixture-replay note

`get_fundamentals` hits `GET /tiingo/fundamentals/AAPL/statements` for both
the as-reported and normalized variants; the two differ only by the
`asReported=true` query parameter. `tests/test_research_inputs.py` wraps
`replay_transport` with a thin param-aware branch for that one path, exactly
as `tests/test_tiingo_source.py` documents. This is a test-harness
accommodation for the Wave 1 replay limitation, not a change to the client.
