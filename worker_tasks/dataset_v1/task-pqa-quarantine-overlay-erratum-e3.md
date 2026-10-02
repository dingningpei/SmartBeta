# PQA — Specification Erratum E3: scope of the post-load reserve assertion (ADOPTED)

- **Status:** ADOPTED. The user authorized it on 2026-10-02, after the P-Q
  Integration Barrier stopped at I5.
- **This is a standalone erratum.** It corrects
  `worker_tasks/dataset_v1/task-pqa-quarantine-overlay.md` (bab2296),
  `92e175f568b7c9c2257418cf0ad294cf21c079dfb9e9628ed3be3cbbf9743f44`, §2
  ("Post-load assertion: every loaded date is ≤ 2023-06-30"), §3 and §11. It
  complements erratum E2
  (`d440ac098f6c4850110e456ea8649117ee25b7f570831c2e0f1298a42762ee9d`).
- **The governing PQA authority is now** PQA + E2 + E3. Where they differ, E3
  governs.
- **Classification:** a planner specification defect.
  - The §2 assertion was over-broad. It applied to listing-interval metadata and
    to calendar-derived endpoints, not only to market and event dates.
  - The worker implemented it literally.
  - It was found when production build 1 raised
    `ReserveFirewallError: listed_start holds a reserve date 2025-04-07`.
  - **No firewall violation occurred.** The build's file-open audit shows only
    allowlisted development artifacts. D8 defines the reserve as the D3
    `reserve_sealed/` package, which was never opened.
- **Methodology impact:** NONE. **Quarantine semantics:** unchanged.

## E3.1 Precedent

The frozen D7c implementation applies its post-load reserve assertion only to
market and event dates: D3 observations, D5 flags and D3 CA events. It reads D4
listing intervals as metadata and clips them with `min(listed_end, 2023-06-30)`
before use. E3 aligns PQA with that precedent.

## E3.2 Corrected rule (replaces the §2 post-load assertion)

The loaded columns fall into three classes:

| Class | Columns (as loaded by `build_overlay`) | Rule |
|---|---|---|
| **S — strict (market / event / schedule-in-development)** | D5 `date`; D7c `row_date`, `source_session`; D7c segments `anchor_session`, `last_session`; D7a labels `formation_date`, `observed_segment_end`; D7a members `formation_date`, `listed_end_dev`; D7a horizons `formation_date`; D6 `formation_date` | Every non-null value ≤ 2023-06-30. Otherwise `ReserveFirewallError` (fail closed) |
| **M — listing-interval metadata** | D4 `listed_start`, `listed_end` | Loading is allowed. **Before use**, each interval is clipped to the development window: `listed_end′ = min(listed_end, 2023-06-30)`. A key whose `listed_start > 2023-06-30` has **no development interval** and is excluded from the interval map. A development flag on such a key still raises `QuarantineInputError` (E2 / §3 fail-closed). No session after 2023-06-30 is ever derived from these values |
| **E — calendar-derived endpoints** | D7a labels `endpoint`; D7a horizons `endpoint` | Loading is allowed. Every value ≥ 2023-07-03 **must** belong to a (formation, h) with `crosses_reserve == true`, otherwise `ReserveFirewallError`. Such labels stay excluded from quarantine computation (`NOT_APPLICABLE`, reason `RESERVE_PURGE`) and their endpoint is never used as a data key |

**Firewall rules (unchanged and restated):**
- No file under `reserve_sealed`, `raw` or `dsc_evidence` is opened.
- No reserve market value is read.
- No computed session after 2023-06-30 is used as a data key. The reserve-boundary
  rule for e′ = 2023-06-30 (`UNDETERMINABLE_RESERVE_BOUNDARY`) is unchanged.
- **Observed production counts** (structural; read-only):
  - class M: `listed_start` 1,211 and `listed_end` 7,800 rows ≥ 2023-07-03;
  - class E: labels `endpoint` 126,000 and horizons `endpoint` 126;
  - class S: 0 in every column.

## E3.3 Required tests (added)

1. A class-S column holding 2023-07-03 or later raises `ReserveFirewallError`. One
   test per class-S column family: flags, D7c rows, segments, labels, members,
   horizons and D6.
2. Class M: an interval with `listed_end` after the boundary is accepted and
   clipped, and the derived prev/next sessions are ≤ 2023-06-30.
   - A key with `listed_start` after the boundary is excluded from the interval map.
   - A development flag on that key raises `QuarantineInputError`.
3. Class E: a post-boundary endpoint on a `crosses_reserve == true` horizon is
   accepted, and the label is excluded.
   - A post-boundary endpoint on a non-crossing horizon raises
     `ReserveFirewallError`.
4. The production column-to-class assignment is exhaustive. Every date column read
   by `build_overlay` is assigned exactly one class. An unassigned date column fails
   the test.

## E3.4 Required mutations (added, numbered after the current set)

- apply the strict assertion to D4 `listed_start` / `listed_end` (the original
  defect);
- skip the strict assertion for D5 `date`;
- skip the strict assertion for D7c `row_date`;
- accept a post-boundary endpoint on a non-crossing horizon;
- use the unclipped D4 `listed_end` when deriving next(s);
- keep a key with a post-boundary `listed_start` in the interval map.

## E3.5 Consequences

- **PQB pin:** PQB's pinned SHA-256 for `quarantine.py` must be updated again, as a
  separate reviewed one-line commit, as under E2.5.
- **Barrier:** the P-Q Integration Barrier restarts from I1 after the repairs merge.
