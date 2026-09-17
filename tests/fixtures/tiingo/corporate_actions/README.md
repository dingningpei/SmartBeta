# Tiingo corporate-action fixtures (P4B-5)

Live-captured Tiingo EOD JSON backing
`smart_beta/vendors/tiingo/corporate_actions.py` and
`tests/test_tiingo_corporate_actions.py`. Captured 2026-09-17 via
`TiingoClient.get_eod_prices` with `Authorization: Token` from
`TIINGO_API_KEY`. The key is not stored in these files.

## Live recordings

| file | ticker | window | why |
| --- | --- | --- | --- |
| `aapl_eod_split_2020-08-20_2020-09-05.json` | AAPL | 2020-08-20 .. 2020-09-05 | 4-for-1 split on 2020-08-31 (`splitFactor` = 4.0 that day, 1.0 otherwise) |
| `aapl_eod_dividend_2020-07-28_2020-08-14.json` | AAPL | 2020-07-28 .. 2020-08-14 | Cash dividend specimen; window covers declaration, ex-date, record, and payment |

Observed EOD keys (every live row): `date`, `close`, `high`, `low`, `open`,
`volume`, `adjClose`, `adjHigh`, `adjLow`, `adjOpen`, `adjVolume`,
`divCash`, `splitFactor`. No announcement, declaration, record, or payment
date field.

## Vendor-semantics specimen (AAPL $0.82 cash dividend)

Tiingo's EOD row for **2020-08-07** has `divCash = 0.82` and `close = 444.45`.
Every other day in the window has `divCash = 0.0`, including:

- 2020-07-30 (declaration date)
- 2020-08-10 (record date)
- 2020-08-13 (payment date)

Independent sources, neither derived from Tiingo:

1. **Nasdaq Dividend History API** (`GET https://api.nasdaq.com/api/quote/AAPL/dividends?assetclass=stocks`,
   captured 2026-09-17; saved as `independent_nasdaq_aapl_dividend_2020-08-07.json`):

   - Ex/EFF Date: `08/07/2020`
   - Cash Amount: `$0.82`
   - Declaration Date: `07/30/2020`
   - Record Date: `08/10/2020`
   - Payment Date: `08/13/2020`

2. **Apple Inc. Form 8-K exhibit 99.1**, filed 2020-07-30, accession
   `0000320193-20-000060`
   (`https://www.sec.gov/Archives/edgar/data/320193/000032019320000060/a8-kexhibit991q3202062.htm`):

   > Apple's Board of Directors has declared a cash dividend of $0.82 per
   > share of the Company's common stock. The dividend is payable on
   > August 13, 2020 to shareholders of record as of the close of business
   > on August 10, 2020.

The 8-K certifies the per-share amount (and the record/payment dates) from
the issuer. Nasdaq certifies that the exchange ex-dividend date is
2020-08-07, distinct from declaration, record, and payment. Tiingo's
`divCash` is attached to that ex-date, with that amount.

Finding: `DIVIDEND_SEMANTICS_CERTIFIED = True`.

## Constructed fixture

`synthetic_same_date_split_and_dividend.json` is **not** a live recording.
It is a hand-built pair of Tiingo-shaped rows used only for the same-date
split+dividend arithmetic test (`_constructed: true` in the file itself).
