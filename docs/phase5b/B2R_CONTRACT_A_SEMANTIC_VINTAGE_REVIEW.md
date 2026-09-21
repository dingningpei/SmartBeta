# B2R Contract-A Semantic / Vintage Review

**STRICTLY OFFLINE REVIEW.** Live provider calls: **0**. Credential access:
forbidden and not performed. Production-code changes: **none**. Stage 3 was
not rerun. Evidence branches were reviewed directly, not merged.

- Master before: `1ef3a4bb85ab7c05966691cf3534f397555c907f`
- Evidence reviewed: `2566fecc88b8cd491e1d7d05e82ee7fff55f465f`
  (branch `phase5b/task-p5b-b2r-contract-a-recovery`)
- D4A governance: `D4A-CA-ELIGIBLE` (`cbc36ca766cf7884ac0604f6883839939f24d84a`)

## 1. Question

Whether each recovered Contract-A result can be admitted into the frozen
P5B-6 fundamental reconstruction under the project's existing PIT /
positive-vintage rules. Retrieval success alone is insufficient; the review
distinguishes (1) exact logical record retrieved, (2) positive vintage
identity, (3) row multiplicity resolved deterministically, (4) required
value available, (5) PIT usability, and (6) admissibility.

## 2. Frozen semantics recovered from the codebase (not newly invented)

From `smart_beta/vendors/tushare/fundamentals.py` (frozen Phase 4D-B):

- **Knowledge date (policy 3):** `f_ann_date` wins when valid (case A);
  `ann_date` falls back only when `f_ann_date` is missing and `report_type
  in {1,2,3,6,7,8}` (case B); everything else is dropped (cases C/D/E).
- **CH3 join (`_assemble_ch3_records`)** — the `ni_ex_nonrecurring` fact is
  emitted only when all three frozen conditions hold:
  1. the `income` anchor `report_type=1` has `ann_date == f_ann_date`
     (valid, and exactly one distinct `(ann, f)` vintage across anchor rows);
  2. no `income`/`balancesheet` row for the period has
     `report_type in {4,5,9,10,11,12}`;
  3. `fina_indicator` exposes its own `ann_date` equal to the anchor's
     `ann_date`, and its `profit_dedt` is **present and single-valued**
     (null/empty `profit_dedt` rows are skipped; conflicting non-null
     values fail closed).
- The CH3 fact's `knowledge_date` is the **anchor's** `ann_date`, never the
  `fina_indicator` row's own date.
- `fina_indicator` returns **no** `f_ann_date`, `report_type`, or
  `update_flag`; its positive-vintage identity is established only by its
  `ann_date` matching the anchor under condition 3.
- Stock + period alone is **not** positive-vintage identity; existence of a
  `profit_dedt` field does **not** certify its semantics.

## 3. Evidence reviewed

1. `smart_beta/vendors/tushare/fundamentals.py` (frozen CH3 join + policy 3).
2. `docs/phase5b/BARRIER_4A_DISPOSITION.md` (D4A-HOLD + B2R history).
3. P5B-6 capture evidence (`phase5b/task-p5b-6-ch3-pilot`, terminal
   `24b7d08…`) for the pre-existing income/`fina_indicator` anchors.
4. B2R-CA raw bodies + `attempts.jsonl` + `vintage_fields.json` at
   `2566fecc…`.

## 4. Raw field-level findings (independent of the worker summary)

Field vectors were reconstructed from `data.fields` × `data.items` for every
returned row.

- **B2-CA-1** `601318.SH`/`fina_indicator`/`20260630` — 2 rows,
  `ann_date=20260821`, `end_date=20260630`, `profit_dedt=87712000000`
  (equal in both). The rows are **not byte-identical**: 22 non-CH3 fields
  differ (`total_revenue_ps` 31.7622 vs 31.7615, `op_income` 12906000000 vs
  11897000000, `tangible_asset` null vs 850632000000, and 19 more). The
  CH3-relevant fields (`ts_code`, `end_date`, `ann_date`, `profit_dedt`)
  are identical across both rows.
- **B2-CA-2** `000858.SZ`/`fina_indicator`/`20250930` — 1 row,
  `ann_date=20251031`, `profit_dedt=6458238128.8`.
- **B2-CA-3** `000858.SZ`/`income`/`20251231` — 1 row,
  `ann_date=20260430`, `f_ann_date=20260430`, `report_type=1`,
  `update_flag=1`. (`income` has no `profit_dedt`.)
- **B2-CA-4** `000858.SZ`/`fina_indicator`/`20260331` — 2 rows, both
  `ann_date=20260430`, `end_date=20260331`. Row 0 is a strict null-subset of
  row 1: exactly 8 fields differ, all null in row 0 and non-null in row 1
  (`eps`, `dt_eps`, `extra_item`, `profit_dedt=8036134136.04`, `roe_waa`,
  `roe_dt`, `q_dt_roe`, `dt_netprofit_yoy`). No field carries two
  conflicting non-null values.

Pre-existing anchors (P5B-6, already captured):

- `601318.SH`/`income`/`20260630` — 3 rows, all `ann_date=20260821`,
  `f_ann_date=20260821`, `report_type=1` → single `(ann,f)` vintage
  `(20260821, 20260821)`, so condition 1 holds; `anchor_date=20260821`.
- `000858.SZ`/`income`/`20250930` — 2 rows: row 0 `ann=20251031,
  f=20251031`; row 1 `ann=20251031, f=20260430` (a restatement with
  `ann != f`). This **breaks** condition 1 (`ann != f` ⇒ suppress).
- `000858.SZ`/`income`/`20260331` — 1 row, `ann=20260430, f=20260430,
  report_type=1` → `anchor_date=20260430`.
- `000858.SZ`/`fina_indicator`/`20251231` — already captured, 1 row,
  `ann_date=20260430`.

## 5. Per-identity adjudication

### B2-CA-1 — `601318.SH::fina_indicator::20260630` — **ADMISSIBLE**

- Exact record: yes (2 rows, `ts_code=601318.SH`, `end_date=20260630`).
- Positive vintage: yes — `ann_date=20260821` in both rows; the anchor's
  `ann_date=20260821` equals it (condition 3 satisfied); anchor condition 1
  holds (single `(20260821, 20260821)` vintage).
- Multiplicity: resolved for the CH3 record — `profit_dedt` is
  single-valued (`87712000000` in both rows), and `ann_date` is
  single-valued. The 22 differing fields are **outside** the CH3 join's
  scope (the join reads only `ann_date` + `profit_dedt` from
  `fina_indicator`), so they neither change the value nor the vintage.
  **Recorded as an observed provider anomaly**, not as a CH3 ambiguity.
- Required value: `profit_dedt=87712000000` available.
- PIT usability: `knowledge_date = anchor_date = 20260821`.
- Admissible.

### B2-CA-2 — `000858.SZ::fina_indicator::20250930` — **NOT-POSITIVE-VINTAGE**

- Exact record: yes (1 row, `profit_dedt=6458238128.8`, `ann_date=20251031`).
- The **anchor** `000858.SZ::income::20250930` contains a restatement row
  (`ann_date=20251031 != f_ann_date=20260430`), which fails frozen
  condition 1 (`ann != f` ⇒ suppress). The CH3 join for period `20250930` is
  suppressed **at the anchor**, before the `fina_indicator` value is even
  consulted. The recovered `fina_indicator` therefore cannot be admitted
  into the reconstruction for this period.
- This is not a defect of the recovered record; it is a pre-existing
  anchor-vintage defect that the recovered value cannot repair.

### B2-CA-3 — `000858.SZ::income::20251231` — **ADMISSIBLE**

- Exact record: yes (1 row, `ts_code=000858.SZ`, `end_date=20251231`).
- Positive vintage: yes — `report_type=1`, `ann_date=20260430 ==
  f_ann_date=20260430`, `update_flag=1` (single clean anchor).
- Multiplicity: single row, resolved.
- Required value: N/A for the anchor (it supplies the knowledge date and
  vintage gate, not `profit_dedt`); the matching `fina_indicator` for
  `20251231` was already captured (`ann_date=20260430`), so condition 3 can
  be satisfied.
- PIT usability: `knowledge_date = f_ann_date = 20260430`.
- Admissible.

### B2-CA-4 — `000858.SZ::fina_indicator::20260331` — **ADMISSIBLE**

- Exact record: yes (2 rows, `ts_code=000858.SZ`, `end_date=20260331`).
- Positive vintage: yes — both rows `ann_date=20260430`, equal to the
  anchor `000858.SZ::income::20260331` `ann_date=20260430` (condition 3
  satisfied).
- Multiplicity: resolved deterministically — row 0 is a null-subset of
  row 1; the frozen rule skips null `profit_dedt`, leaving a single non-null
  value `8036134136.04`. This is the frozen single-valued rule, not an
  arbitrary "choose non-null."
- Required value: `profit_dedt=8036134136.04` available.
- PIT usability: `knowledge_date = anchor_date = 20260430`.
- Admissible.

## 6. Formation-date relevance

Formation dates are month-ends in `2025-09-30 .. 2026-08-31`.

- **B2-CA-3** (`20251231`, knowledge date `20260430`): usable for formations
  on/after `2026-04-30`.
- **B2-CA-4** (`20260331`, knowledge date `20260430`): usable for formations
  on/after `2026-04-30`.
- **B2-CA-1** (`20260630`, knowledge date `20260821`): usable only for the
  `2026-08-31` formation (the last in the window).
- **B2-CA-2**: no formations enabled (period `20250930` remains suppressed).

## 7. Overall B2 verdict

**`B2-PARTIALLY-RECOVERED`**

Three of the four blockers are semantically resolved and admissible
(B2-CA-1, B2-CA-3, B2-CA-4). The fourth (B2-CA-2) is retrieved but **not**
admissible: its period's `income` anchor carries a restatement
(`ann != f_ann`), so the frozen CH3 join suppresses it independently of the
recovered `fina_indicator` value. Transport success alone cannot produce
`B2-RECOVERED`, and here one identity is blocked by an anchor-vintage
defect, not by missing transport.

## 8. Stage 3 decision

**`STAGE3-RERUN-NOT-ELIGIBLE`**

The offline reconstruction is **not** complete: period `000858.SZ::
20250930` remains blocked by its anchor restatement, and the `000858.SZ`
name still loses formations because of it. Sufficient semantically
admissible evidence to conclude the B2 blocker has been fully removed is
therefore not present. Eligibility is not Barrier-4a passage; a Stage 3
rerun is simply not yet justified.

## 9. Barrier status (unchanged by this review)

```
Barrier 4a:  NOT PASSED
P5B-7:       BLOCKED
```

This review does not declare Barrier 4a passed and does not authorize
P5B-7.

## 10. Provenance note (not a semantic blocker)

The frozen module hard-codes `ACCESS_PATH = "proxy:pcd.mobcvb.cn"`. The
recovered rows are from `datahubco.com`. Any downstream admission must keep
Contract-A evidence distinguishable by path (as the frozen B2R-CA spec
requires); this review does not relabel provenance and does not feed
Contract-A rows through the frozen code as if they were Contract-B rows.

## 11. Repository action

This review is committed to `master`. The B2R-CA evidence branch
(`2566fecc…`) and both CA1 evidence branches are **not** merged. No
production-code change, no live call, no push, no tag.
