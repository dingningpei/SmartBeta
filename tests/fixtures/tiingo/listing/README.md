# Tiingo listing / delisting fixtures (P4B-7)

Live-captured Tiingo JSON and labeled constructed sequences backing
`smart_beta/vendors/tiingo/listing.py` and `tests/test_tiingo_listing.py`.
Live bodies were captured 2026-09-16 via `urllib.request` with
`Authorization: Token` from `TIINGO_API_KEY` (timeout 20s, one attempt
per request). The API key is not stored. No test in this task touches
the network.

## Live recordings

| file | endpoint | notes |
| --- | --- | --- |
| `twtr_meta.json` | `GET /tiingo/daily/TWTR` | Delisted Twitter. Keys: `ticker`, `name`, `description`, `startDate` (`2013-11-07`), `endDate` (`2022-10-28`), `exchangeCode`. **No** `isActive`, **no** `delistDate`, **no** `permaTicker`. |
| `twtr_eod_prices_2022-09-15_2022-11-15.json` | `GET /tiingo/daily/TWTR/prices` | 32 rows. Trailing history is more than 10 trading days. Exactly one zero-volume row: terminal `2022-10-28` (`volume == 0`, `close == 53.7`). A follow-up query `2022-10-29..2022-12-31` returned no rows (not saved). |
| `aapl_meta.json` | `GET /tiingo/daily/AAPL` | Active Apple. Same six keys. `startDate` `1980-12-12`, `endDate` `2026-09-16`. No delisting-status field. |
| `aapl_eod_prices_2026-08-17_2026-09-16.json` | `GET /tiingo/daily/AAPL/prices` | 22 rows ending at `endDate`. Zero zero-volume rows. |

Observed daily-meta keys (AAPL and TWTR, identical set): `ticker`,
`name`, `description`, `startDate`, `endDate`, `exchangeCode`.

Observed EOD keys (identical set): `date`, `open`, `high`, `low`,
`close`, `volume`, `adjOpen`, `adjHigh`, `adjLow`, `adjClose`,
`adjVolume`, `divCash`, `splitFactor`.

## Empirical correction vs. the frozen spec's TWTR assumption

The P4B-7 spec asked for TWTR's "real corroborating trailing run" to
assert `delist_date = endDate`. Live EOD under this account has a
trailing zero-volume run of **length 1**. The frozen constant
`_MIN_CORROBORATING_ZERO_VOLUME_DAYS = 5` is not lowered. Real TWTR
therefore maps to `delist_date = NaT` -- a single isolated zero-volume
day at the tail is the exact case the policy forbids treating as
delisting.

`isActive` exists on `GET /tiingo/fundamentals/meta` (already captured
by P4B-2, not owned here) and on `GET /tiingo/utilities/search`, but
not on the get_meta dict this mapper consumes. A live search for
`TWTR` did not return delisted TWTR.

## Constructed (labeled-as-such) adversarial fixtures

| file | purpose |
| --- | --- |
| `constructed_isolated_mid_history_zero_volume_{meta,eod}.json` | Exactly one zero-volume day on 2021-06-03; nonzero-volume trading resumes; `endDate` 2021-06-10 is well after that day. Must not assert delisting. |
| `constructed_short_trailing_zero_volume_{meta,eod}.json` | Trailing run of **4** zero-volume rows ending at `endDate` 2021-07-14 (`MIN - 1`). Must not assert delisting. Distinct from the isolated mid-history case. |
| `constructed_sufficient_trailing_zero_volume_{meta,eod}.json` | Trailing run of **5** zero-volume rows ending at `endDate` 2021-08-16, no later rows. This is the corroboration path live TWTR does not provide. Must assert `delist_date = endDate`. |
