# Tiingo raw-returns / market-cap / trading-status fixtures (P4B-4)

Live-captured Tiingo JSON (and one hand-constructed EOD sequence) backing
`smart_beta/vendors/tiingo/returns_and_market_cap.py` and
`tests/test_tiingo_returns_market_cap.py`. Live bodies were captured
2026-09-16. The API key is not stored. No test in this task touches the
network.

## EOD prices (`GET /tiingo/daily/{ticker}/prices`)

| file | ticker | window | notes |
| --- | --- | --- | --- |
| `aapl_eod_prices_2020-08-20_2020-09-05.json` | AAPL | 2020-08-20..2020-09-05 | spans the 2020-08-31 4-for-1 split (`splitFactor == 4.0` on that row). Raw `close` drops 499.23 → 129.04; `adjClose` does not. |
| `twtr_eod_prices_2022-10-20_2022-10-28.json` | TWTR | 2022-10-20..2022-10-28 | delisted Twitter. Terminal row 2022-10-28 has `volume == 0` and `close == 53.7` (unchanged from 2022-10-27). |
| `isolated_zero_volume_eod.json` | (synthetic) | 2021-06-01..2021-06-07 | hand-constructed. One isolated `volume == 0` day on 2021-06-03 in the *middle* of otherwise-normal trading. Not a delisting. |

Observed EOD keys (AAPL and TWTR live bodies, identical set): `date`,
`open`, `high`, `low`, `close`, `volume`, `adjOpen`, `adjHigh`,
`adjLow`, `adjClose`, `adjVolume`, `divCash`, `splitFactor`. **No**
`marketCap`, **no** share-count, **no** float-share field.

## Market-cap investigation (`GET /tiingo/fundamentals/{ticker}/daily`)

| file | ticker | window | notes |
| --- | --- | --- | --- |
| `aapl_fundamentals_daily_2024-01-02_2024-01-05.json` | AAPL | 2024-01-02..2024-01-05 | live body. Keys: `date`, `marketCap`, `enterpriseVal`, `peRatio`, `pbRatio`, `trailingPEG1Y`. `marketCap` is present. There is no distinct float-adjusted figure. |

This endpoint is **not** wrapped by `TiingoClient` (P4B-1 is frozen for
this wave). History under this account begins 2023-09-18; a 2020-08-20
query (the AAPL split window) returned an empty list. The mapper does
not call this endpoint; `map_eod_to_market_cap` fails closed rather than
fabricating a number from EOD prices or reaching around the client.

No test in this task touches the network.
