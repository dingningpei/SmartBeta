# Tushare identifier-policy fixtures (P4DB-2)

Backing data for `smart_beta/vendors/tushare/identifiers.py` and
`tests/test_tushare_identifiers.py`.

## Credential limitation — read first

`TUSHARE_PROXY_TOKEN` was **not present** in this worktree's environment
at implementation time, and the proxy
(`https://pcd.mobcvb.cn/tushare/pro`) rejects every data endpoint without
a valid `X-API-Key` (`/health` and `/ready` are the only unauthenticated
endpoints reached). Per the task's credential-safety instruction, no
attempt was made to find the token anywhere outside the environment, and
no other authentication path was used.

Consequently these JSON files are **not fresh live captures**. They are
hand-constructed specimens that use only field names the endpoints are
documented to return, and the specimen-level continuity conclusion
(`000024.SZ` and `001914.SZ` are unrelated entities) is **carried
forward** from the Phase 4D-A investigation's frozen dossier as recorded
in `worker_tasks/phase4d_b/phase4d-b-plan.md` (evidence item 9) — it was
**not** independently re-confirmed against the live vendor this round.
Every file is prefixed `constructed_` to make that unmistakable. Do not
treat these as vendor ground truth; re-record them from the proxy once
`TUSHARE_PROXY_TOKEN` is available.

## `constructed_stock_basic.json`

Tushare `stock_basic` response shape `{"fields": [...], "items": [...]}`,
requested with `fields=ts_code,name,list_date,delist_date,list_status`
for five codes:

| `ts_code` | `name` | `list_status` | `delist_date` |
| --- | --- | --- | --- |
| `000001.SZ` | 平安银行 | `L` | (empty) |
| `600519.SH` | 贵州茅台 | `L` | (empty) |
| `601318.SH` | 中国平安 | `L` | (empty) |
| `000024.SZ` | 招商地产 | `D` | `20151230` |
| `001914.SZ` | 招商积余 | `L` | (empty) |

The key point the fixture supports is structural, not the exact strings:
`stock_basic` exposes **no** successor/predecessor `ts_code` field. Two
records are either the same code or two independent securities. There is
no vendor-asserted old-code -> new-code join key to build a continuity
mapping from.

## `constructed_namechange.json`

Tushare `namechange` response shape, requested for `000024.SZ` and
`001914.SZ`. Documented fields: `ts_code`, `name`, `start_date`,
`end_date`, `ann_date`, `change_reason`.

Every row is keyed by the **same** `ts_code` it describes. The endpoint
records *name* changes for one code; it cannot express a code change at
all. `000024.SZ` and `001914.SZ` never appear on a shared row, so
`namechange` provides no old-code -> new-code mapping.

## Investigation outcome

1. `stock_basic` — no direct continuity signal; only per-code listing
   status/dates.
2. `namechange` — reachable and field shape confirmed (`ts_code, name,
   start_date, end_date, ann_date, change_reason`), but keyed by the same
   `ts_code`; no code remapping.
3. No direct vendor-asserted old-code -> new-code mapping was found (and
   none could be live-verified this round), so **no heuristic rename
   mapping is implemented**. `resolve_stock_id` is a direct `ts_code`
   passthrough, and securities whose own `stock_basic` row shows a
   terminal listing resolve with `is_permanent=False`.

`IDENTIFIER CONTINUITY = NOT CERTIFIED` (carried forward; live
re-confirmation blocked by the missing credential).
