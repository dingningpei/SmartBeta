# Tiingo fundamentals fixtures (P4B-6)

Live-captured Tiingo JSON backing `smart_beta/vendors/tiingo/fundamentals.py`
and `tests/test_tiingo_fundamentals.py`. The AAPL and RGEN bodies are copies
of the P4B-1 live captures (same verbatim Tiingo JSON). Constructed variants
are labeled as such in the filename.

| file | origin | role |
| --- | --- | --- |
| `aapl_fundamentals_asreported.json` | live `GET /tiingo/fundamentals/AAPL/statements?asReported=true` | fact values + `knowledge_date` (`date` = 2026-07-31 for fiscal Q3 2026) |
| `aapl_fundamentals_normalized.json` | live `GET /tiingo/fundamentals/AAPL/statements` | `report_period_end` lookup (`date` = 2026-06-27 for fiscal Q3 2026) |
| `constructed_normalized_q3_removed.json` | **constructed** from the normalized capture with fiscal `(year=2026, quarter=3)` removed | 0-match adversarial case |
| `constructed_normalized_q3_duplicated.json` | **constructed** from the normalized capture with fiscal `(year=2026, quarter=3)` duplicated | >1-match adversarial case |
| `rgen_fundamentals_asreported_error.json` | live `GET /tiingo/fundamentals/RGEN/statements?asReported=true` (HTTP 400) | uncertifiable specimen; caller sees `TiingoAPIError` |

Fiscal identity fields on every statement: `year`, `quarter` (not
`fiscalYear` / `fiscalQuarter`). Coverage of the two live AAPL responses is
not 1:1 (asReported has 2026 Q1–Q3; normalized has 2026 Q2–Q3).

No test in this task touches the network.
