# Tiingo identifier-policy fixtures (P4B-2)

Live-captured Tiingo JSON backing `smart_beta/vendors/tiingo/identifiers.py`
and `tests/test_tiingo_identifiers.py`. Captured 2026-09-16 via
`urllib.request` with `Authorization: Token` from `TIINGO_API_KEY`. The
key is not stored in these files.

## Daily metadata (`GET /tiingo/daily/{ticker}`) — get_meta shape

| file | ticker | name | `permaTicker` |
| --- | --- | --- | --- |
| `aapl_meta.json` | `AAPL` | Apple Inc (active) | **absent** |
| `twtr_meta.json` | `TWTR` | Twitter Inc (delisted; `endDate` 2022-10-28) | **absent** |
| `fb_meta.json` | `FB` | ProShares S&P 500 Dynamic Daily Buffer ETF (BATS, listed 2025-06-26) | **absent** |
| `meta_meta.json` | `META` | Meta Platforms Inc - Class A | **absent** |

Keys observed on every daily-meta body: `ticker`, `name`, `description`,
`startDate`, `endDate`, `exchangeCode`. No `permaTicker`, no CIK.

`FB` is **not** pre-rename Meta: the ticker was reused by an unrelated ETF.
This is why ticker fallback cannot certify identifier continuity.

## Fundamentals metadata (`GET /tiingo/fundamentals/meta`)

| file | ticker | `permaTicker` | `isActive` |
| --- | --- | --- | --- |
| `aapl_fundamentals_meta.json` | `aapl` | `US000000000038` | true |
| `twtr_fundamentals_meta.json` | `twtr` | `US000000000041` | false |
| `meta_fundamentals_meta.json` | `meta` | `US000000000059` | true |

A request that also asked for `FB` returned only the three rows above, so
FB -> META continuity cannot be shown on this endpoint either.

`permaTicker` is a real, security-level field on this endpoint (and on
`GET /tiingo/utilities/search`), including for delisted TWTR. It is not on
the get_meta dict `resolve_stock_id` is specified to consume.

No test in this task touches the network.
