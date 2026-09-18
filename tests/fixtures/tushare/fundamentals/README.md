# Tushare fundamentals fixtures (P4DB-6)

Backing data for `smart_beta/vendors/tushare/fundamentals.py` and
`tests/test_tushare_fundamentals.py`.

## Evidence classes (kept distinct on purpose)

| Class | What it is | Where |
| --- | --- | --- |
| **Proxy-observed live evidence** | Recorded responses from the third-party proxy `https://pcd.mobcvb.cn/tushare/pro` reached with an `X-API-Key`. | Every `*.json` in this directory |
| **Upstream Tushare documented semantics** | Tushare's own `report_type` / `ann_date` / `f_ann_date` / `fina_indicator` field definitions and code families. | Cited in `fundamentals.py` docstrings, not re-derived here |
| **Contract-modeled / offline data** | Hand-constructed rows used only to exercise branches for which no real specimen exists (knowledge-date cases B/C/D/E, condition-3 divergence, absent-`fina_indicator`-date). | Inline in the test file, explicitly labelled there |
| **Direct-official-Tushare behavior** | Tushare's paid official API. **Not exercised or certified by this task.** | — |

**Proxy-observed evidence must never be read as certifying direct
official-Tushare behavior.** `access_path` is `proxy:pcd.mobcvb.cn` on
every emitted fact row so a consumer can always tell the difference.

## How the recordings were made

`TUSHARE_PROXY_TOKEN` was present in the implementation environment at
recording time (it was *not* searched for; it was only read from the
process environment). Each request was issued **three times** through
`ProxyTushareClient` and the three non-empty payloads were required to
agree byte-for-byte via `check_canonical_consistency`; the canonical
payload is what is stored here. No credential value is stored anywhere in
this directory.

The `manifest.json` maps each stored payload file to the `api_name` and
parameters that produced it (the same `(api_name, sorted params)` key
`replay_transport` uses). Tests replay these with
`replay_transport`, so `pytest` performs **zero** network calls.

## What the payloads cover

* `income` for `000001.SZ` FY2022 (Q1-Q4, unfiltered and
  `report_type=1`; Q3 `report_type=2`) -- Gate-1 cumulative-vs-single-
  quarter reconciliation evidence.
* `income` / `balancesheet` for `600518.SH` FY2017 and Q1/Q3 2018 -- the
  original-vs-restated vintage pair and the `t1 < t2 < t3` chain raw facts.
* `income` / `balancesheet` for `002450.SZ` FY2015 -- the stale-`ann_date`
  / reprocessed-`f_ann_date` specimen (and a real same-schema-key,
  different-value collision between `report_type=1` and `report_type=4`).
* `income` for `002450.SZ` FY2016/FY2017 -- the blank-out specimens.
* `income` for `002069.SZ` FY2017 -- the single-row
  no-second-vintage-but-`ann_date != f_ann_date` counterexample.
* `fina_indicator` for `000001.SZ` 2022Q2/Q3 and `600518.SH` 2017FY and
  `002450.SZ` 2015FY. The endpoint **does** expose its own `ann_date`; no
  recorded specimen diverged from the anchor `income` row's `ann_date`.
* `disclosure_date` for `600518.SH` and `002450.SZ` FY2017 -- independent
  corroboration of the original vintage `ann_date`.

All values are as returned by the proxy; no number was edited, smoothed,
or back-filled.

## Re-recording

```bash
TUSHARE_PROXY_TOKEN=... .venv/bin/python -m \
    smart_beta.vendors.tushare.proxy_client \
    --out tests/fixtures/tushare/fundamentals \
    "income ts_code=000001.SZ period=20220930" ...
```

`P4DB-8`/`P4DB-9` should reuse these via the loader in
`tests/test_tushare_fundamentals.py` rather than duplicating them.
