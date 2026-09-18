# Tushare listing/delisting fixtures (P4DB-7)

Backing data for `smart_beta/vendors/tushare/listing.py` and
`tests/test_tushare_listing.py`.

## Provenance categories — do not conflate these

This directory deliberately keeps four different kinds of statement
separate. Read each file's name and the table below before trusting any
number in a test.

| Category | Where it lives | What it certifies |
| --- | --- | --- |
| **Proxy-observed live evidence** | files with **no** `constructed_` prefix, recorded in `manifest.json` with `live_recorded: true` | what `pcd.mobcvb.cn/tushare/pro` actually returned, through `TUSHARE_PROXY_TOKEN` present in the recording process's environment |
| **Contract-modeled / offline data** | `constructed_*.json` | a hand-built adversarial case; not vendor ground truth |
| **Upstream Tushare documented semantics** | prose only (this README, the module docstring) | the vendor's documented `list_status` values (`L`/`D`/`P`) |
| **Direct-official-Tushare behavior** | **not represented here** | nothing in this directory may be read as certifying direct official-Tushare behavior |

Proxy-observed evidence is **not** direct official-Tushare behavior. Every
non-constructed file here is a third-party-proxy observation and must be
re-recorded against the official API before being treated as vendor ground
truth.

## Live recording method

Recorded 2026-09-17 (UTC) with `ProxyTushareClient.record()`; see
`manifest.json` for the exact request parameters, HTTP status, sample
count, and item count of every file. The token was read from the process
environment only and is not present in any file. No credential value is
stored, printed, or committed.

The small `ts_code`-filtered `stock_basic` specimens and the
`002450.SZ` `daily` window were requested with `samples=3`, so the proxy's
canonical-consistency check applies (three non-empty payloads had to agree
byte-for-byte). The large `list_status="D"` sweep and the `000001.SZ`
daily window were requested with a single sample to respect the proxy's
rate limits.

## What the live specimens show

### `list_status` values actually observed

* `stock_basic_list_status_D.json` — 339 records, **all** with
  `list_status="D"` and **all** with a non-empty `delist_date`.
* `stock_basic_list_status_P.json` — **0** records. `"P"` is a valid query
  parameter and upstream Tushare documents it as 暂停上市 (paused
  listing), but the proxy returned no `"P"` row this round. No `"P"`
  *record* was observed; the value is documented, not observed.
* `stock_basic_000001.SZ.json` and `stock_basic_002069.SZ.json` show real
  `"L"` rows. So the **observed** `list_status` values are `"L"` and
  `"D"`; the **documented** set is `{"L", "D", "P"}`.

### Redundant rows from `ts_code` queries

`ts_code`-filtered queries return the authoritative row plus redundant
rows with the same `ts_code`/`name`/`list_date` but null
`list_status`/`delist_date` (and sometimes an all-null row). Examples:

* `stock_basic_002450.SZ.json` — row 1 is authoritative
  (`D` / `20210531`); row 2 has null `list_status`/`delist_date`.
* `stock_basic_000001.SZ.json` — row 1 is authoritative (`L`); rows 2–3
  are null/partly-null duplicates.

`select_stock_basic_row` collapses these and fails closed on genuinely
conflicting records.

### The real delisted specimen

`002450.SZ` (康得退, `list_status="D"`) is the reachable, confirmed-delisted
specimen:

* `stock_basic_002450.SZ.json` — `delist_date="20210531"`.
* `daily_002450.SZ_20210301_20211231.json` — 30 real `daily` rows, the
  last on `2021-05-28` (**before** the claimed `delist_date`), with **no**
  row after it anywhere in the 2021 window (and none in separately probed
  2022/2023 windows).
* Corroborating absence: 146 authoritative SSE/SZSE trading sessions in
  `(2021-05-31, 2021-12-31]`, far above the frozen threshold of 5.

`002069.SZ` (獐子岛) — a company with a well-known fraud history — is
**still listed** (`list_status="L"`, no `delist_date`). It is the
reputational control proving this adapter trusts the vendor row, not a
company's reputation.

### The negative control

`daily_000001.SZ_20260101_20260917.json` — 173 real rows for the
clearly-still-listed `000001.SZ` (平安银行), spanning to the recording
date. Its `stock_basic_000001.SZ.json` row carries no `delist_date`, so
the adapter never fabricates one.

## Constructed specimens (`constructed_*.json`)

Both are **hand-built**, not recorded, and exist only to exercise branches
a real specimen did not naturally provide:

* `constructed_insufficient_gap_stock_basic.json` +
  `constructed_insufficient_gap_daily.json` — a synthetic `000000.SZ`
  with a claimed `delist_date="20240105"` and a `daily` window that stops
  on that date. Observed through `2024-01-09` (only 2 trading sessions
  later), the trailing gap is below the frozen 5, so the claim is **not**
  corroborated.
* `constructed_rows_after_claim_stock_basic.json` +
  `constructed_rows_after_claim_daily.json` — the same claim, but `daily`
  rows continue *after* `20240105`, the direct contradiction case. The
  claim is **not** corroborated.

`tests/test_tushare_listing.py` also exercises the insufficient-gap branch
using the **real** `002450.SZ` claim with a deliberately **truncated
observation window**; that construction is labeled in the test.

## DELISTING CORROBORATION status

`DELISTING CORROBORATION = PASS (proxy-observed specimen `002450.SZ`;
not certified for direct official-Tushare behavior).`

The corroboration machinery is exercised against a real, reachable
delisted specimen through the proxy. Because every specimen here is
proxy-observed and the proxy is explicitly
`PROXY SUFFICIENT FOR PRODUCTION: NO`, this is a POC-grade PASS, not a
certification of the official API.
