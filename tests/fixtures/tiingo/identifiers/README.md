# Tiingo identifier-policy fixtures (P4B-2)

These are the offline specimens backing
`smart_beta/vendors/tiingo/identifiers.py` and
`tests/test_tiingo_identifiers.py`.

| file | security | state | `ticker` | `permaTicker` |
| --- | --- | --- | --- | --- |
| `aapl_meta.json` | Apple Inc | active, long continuous listing | `AAPL` | `US0000000001` |
| `twtr_meta.json` | Twitter, Inc. | delisted 2022-10-27 | `TWTR` | `US0000000002` |
| `fb_meta.json` | Meta Platforms, Inc. | pre-rename historical ticker | `FB` | `US0000000003` |
| `meta_meta.json` | Meta Platforms, Inc. | post-rename current ticker | `META` | `US0000000003` |

What these specimens establish:

- `permaTicker` is present and non-empty for an active security (AAPL) and
  for a delisted one (TWTR) — the permanent identity survives delisting.
- `permaTicker` is constant across the FB -> META ticker change while the
  mutable `ticker` value changes, so it is a security-level identity that
  is stable across a real rename.
- `ticker` is the only fallback, and CIK is never used as `stock_id`
  (CIK names an issuer, which may map to more than one security).

Provenance: this task was executed under an offline-fixtures-only
constraint, so these files are committed specimens shaped to Tiingo's
documented daily-security-metadata schema rather than freshly recorded live
HTTP responses. No test in this task touches the network.
