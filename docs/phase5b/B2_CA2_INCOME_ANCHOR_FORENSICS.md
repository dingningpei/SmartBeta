# B2-CA-2 Income Anchor Vintage Forensics

**STRICTLY OFFLINE FORENSIC REVIEW.** Live provider calls: **0**. Credential
access: forbidden and not performed. Production-code changes: **none**.
Stage 3 not rerun. Evidence branches reviewed directly, not merged.

- Master before: `c30bcdac28fdb435d31b39f95f77aec94bdac166`
- Semantic/vintage review: `c30bcdac28fdb435d31b39f95f77aec94bdac166`
- B2R-CA evidence: `2566fecc88b8cd491e1d7d05e82ee7fff55f465f`

## 1. Anchor under examination

`000858.SZ::income::20250930` — the income anchor whose vintage status
blocked admission of the recovered Contract-A `fina_indicator` value.

## 2. Raw source of the anchor

The anchor lives in the preserved P5B-6 capture evidence on branch
`phase5b/task-p5b-6-ch3-pilot`, in two locations with identical content:

- `docs/phase5b/ch3_gate_a/raw/000858.SZ__income__20250930.json` (Run-1 raw)
- `docs/phase5b/ch3_gate_a/staging/income__1efec4c26d2f8f30d42aed59373f5fd9.json`
  (Run-1 staged copy)

A full search of every income-bearing file in the P5B-6 branch (raw, resume
staging, and pilot-universe probe artifacts) found **no other** row for
`ts_code=000858.SZ` + `end_date=20250930`.

## 3. Preserved rows and vintage identities

Four row-instances, two distinct vintage identities:

| identity | ann_date | f_ann_date | report_type | update_flag | comp_type | end_type |
|---|---|---|---|---|---|---|
| **original** | 20251031 | **20251031** | 1 | 1 | 1 | 3 |
| **restatement** | 20251031 | **20260430** | 1 | 1 | 1 | 3 |

The two rows differ in 18 value fields, decisively:

| field | original (ann=f=20251031) | restatement (ann=20251031, f=20260430) |
|---|---|---|
| `total_revenue` | 60,945,321,083.57 | 30,637,727,654.97 |
| `n_income` | 22,211,853,938.77 | 6,786,973,050.17 |
| `basic_eps` | 5.542 | 1.668 |

## 4. Answers to the forensic questions

1. **Exact raw source:** P5B-6 Run-1 capture (`ch3_gate_a/raw/…__income__20250930.json`
   and its staged copy), Contract B / ProMax provenance.
2. **Row count for this stock/period:** 4 row-instances (2 raw + 2 staged),
   representing **2 distinct vintages**.
3. **Complete identity:** reconstructed above (both `report_type=1`,
   `update_flag=1`, differing only in `f_ann_date` and 17 value columns).
4. **Original row with `ann==f==20251031`:** **YES** — present in both the
   raw and the staged capture, with the *original* Q3-2025 values
   (`total_revenue≈60.9B`, `n_income≈22.2B`).
5. **Classification of `ann=20251031, f=20260430`:** **(A) one restated
   provider record** — the documented "in-place reprocessing" pattern
   (Phase 4D-B plan item 3): a `report_type=1` row whose *values were
   silently replaced* by a later restatement (2026-04-30) while `ann_date`
   stayed stale (2025-10-31). It is **not** a capture artifact; the
   original and the restatement are both preserved verbatim.
6. **Does capture preserve multiple rows or collapse them?** The capture
   preserves both rows (`raw/` and `staging/` each contain two rows). The
   reconstruction (`_assemble_ch3_records`) does not collapse them — it
   **fails closed** on the co-present restatement.
7. **Is `ann != f` intentionally fail-closed?** **YES.** Frozen condition 1
   requires *every* `report_type=1` anchor row to have `ann_date ==
   f_ann_date` and exactly one distinct `(ann, f)` vintage; any divergence
   suppresses the whole period.
8. **Is that rule a frozen architectural decision?** **YES.** Phase 4D-B
   plan item 3 ("`ann_date` alone is never safe when it diverges from
   `f_ann_date`", grounded in the 002450.SZ fraud specimen) and item 7
   condition (i); implemented and commented in
   `smart_beta/vendors/tushare/fundamentals.py::_assemble_ch3_records`;
   exercised by the CH3 join test suite.
9. **Can the blocker be resolved with evidence already captured, without
   changing the frozen rule?** **NO.** The original clean vintage is already
   captured, but the frozen condition-1 rule suppresses the period because
   the restatement row is co-present. Admitting the original would require
   selecting one `report_type=1` row over another — a semantic-rule change
   (e.g. "use `ann==f` row only", or "choose earlier/later date"), which is
   explicitly out of scope and not performed.

## 5. Forensic verdict

**`ORIGINAL-VINTAGE-FOUND`** — preserved existing evidence positively
establishes the original clean 2025-10-31 vintage (`ann==f==20251031`,
`report_type=1`, `update_flag=1`, original reported values).

This finding does **not** by itself resolve the blocker: the frozen
condition-1 rule still fails closed because the restatement
(`ann=20251031, f_ann_date=20260430`) is co-present in the same period.

## 6. Next-step classification

**`STRUCTURAL-FAIL-CLOSED`** — the blocker is a structural property of the
frozen semantic rule (condition 1 fails closed on any `ann != f_ann_date`
anchor row), not a data gap (both vintages are captured), not a capture
defect (both rows preserved), and not an implementation bug (the behavior is
the documented, tested intent). Resolution would require a
frozen-semantic-rule change via a separate governance decision, not new
acquisition and not an architecture patch within current semantics.

## 7. Stage 3

**`STAGE3-RERUN-NOT-ELIGIBLE`** — the remaining blocker is not resolved by
the forensic evidence under already-frozen semantics.

## 8. Status (unchanged)

```
B2:        B2-PARTIALLY-RECOVERED
Barrier 4a: NOT PASSED
P5B-7:      BLOCKED
```

## 9. Repository action

This forensic review is committed to `master`. No evidence branch merged, no
production-code change, no live call, no push, no tag.
