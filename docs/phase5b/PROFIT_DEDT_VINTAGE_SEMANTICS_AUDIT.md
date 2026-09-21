# profit_dedt Vintage Semantics Audit

**STRICTLY OFFLINE DATA-SEMANTICS AUDIT.** Live provider calls: **0**.
Credential access: forbidden and not performed. Production-code changes:
**none**. Test changes: **none**. The CH3 gate was not modified; Stage 3 was
not rerun; evidence branches were not merged.

- Master before: `09d7935d2e14d919a3705f3abe219f2b2cb808ad`
- Architecture review input:
  `docs/phase5b/PIT_RESTATEMENT_ARCHITECTURE_REVIEW.md` (`09d7935…`)

## 1. Question

Does existing preserved `fina_indicator` evidence contain a defensible,
generic, provider-supported way to assign a positive vintage identity /
knowledge date to `profit_dedt`? This is judged without regard to making
`000858.SZ` pass.

## 2. Field-level schema audit

Across all preserved `fina_indicator` evidence (58 files: P5B-6, Contract-A
CA1-T1, Contract-A B2R, P5B-4 pilot universe, Phase 4D-B fixtures), three
schema variants exist:

| schema | field count | `report_type` | `f_ann_date` | `update_flag` | `rd_exp` |
|---|---|---|---|---|---|
| A (P5B-6 / Contract-A B2R / CA1-T1) | 108 | absent | absent | absent | absent |
| B (P5B-4 pilot, 600518 fixture) | 110 | absent | absent | absent | absent |
| C (000001.SZ 20220630 fixture) | 167 | absent | absent | **present, empty** | present, empty |

Identity/provenance-relevant fields present in **any** schema are only:
`ts_code`, `ann_date`, `end_date`, and (in schema C only) `update_flag`
whose value is the **empty string** in every preserved row.

## 3. Multi-row groups (same `ts_code` + `end_date`)

| group | rows | `ann_date` | `profit_dedt` | distinguishing fields |
|---|---|---|---|---|
| 601318.SH 20260630 (Contract-A) | 2 | 20260821 = | 87712000000 = | **22 fields differ** (non-null values) |
| 000858.SZ 20260331 (Contract-A) | 2 | 20260430 = | null vs 8036134136.04 | 8 fields (all null-vs-nonnull) |
| 000858.SZ 20260630 (P5B-6) | 2 | 20260829 = | 8483289707.34 = | 1 field (`dt_netprofit_yoy`) |
| 000001.SZ 20220630 (Phase 4D-B fixture) | 2 | 20220818 = | 22042000000.0 = | 1 field (`fcff`); `update_flag=""` |

In every group `ann_date` is **identical** across rows; there is **no stable
version/revision field** and no certified row-order → no deterministic
provider-supported vintage ordering exists in the evidence.

## 4. Special cases

1. **601318.SH 20260630** — two rows share `ann_date` and `profit_dedt` but
   differ in 22 non-value fields (`total_revenue_ps`, `op_income`,
   `tangible_asset`, …). Nothing identifies a version; the difference is
   implicit in differing metric values, not an explicit identity field.
2. **000858.SZ 20260331** — two rows share `ann_date`; row 0 is a null-subset
   of row 1 (8 fields null vs non-null). This is a sparse/full duplication,
   not a version marker.
3. **000858.SZ 20250930** — one `fina_indicator` row (`ann_date=20251031`,
   `profit_dedt=6458238128.8`). The income anchor independently proves two
   vintages (original `knowledge_date=20251031`; restatement
   `knowledge_date=20260430`) that **both carry `ann_date=20251031`**. No
   preserved `fina_indicator` field ties this `profit_dedt` to either
   vintage; any association would be inferred from convenience, not evidence.

## 5. Cross-endpoint relationship (tested, not assumed)

- **Matching `ann_date`:** fails — both income vintages share
  `ann_date=20251031`; `ann_date` cannot distinguish them.
- **Matching `end_date`:** fails — both vintages share `end_date=20250930`.
- **Value derivation:** `profit_dedt` (扣非净利润) and `income.n_income`
  (净利润) are different economic quantities; neither derives the other
  without the non-recurring adjustment, and even if derived it would not
  select a vintage.
- **Row-multiplicity correspondence:** none — income has 2 vintages,
  `fina_indicator` has 1 row.
- **Documented provider identity link:** none found in the repository.

Cross-endpoint vintage linking is therefore **not certified**.

## 6. `profit_dedt` value vs vintage semantics

- **A (value semantics):** `profit_dedt` = 扣除非经常性损益后的净利润, the
  intended CH3 candidate — supported by the Phase 4D-B plan (item 5/7).
- **B (vintage semantics):** when that particular value became knowable —
  **NOT established** by any preserved field. Evidence for A does not
  establish B, and B is the audit's primary blocker.

## 7. Genericity test

No candidate vintage rule survives generically. Any rule of the form
"prefer non-null", "prefer a particular row order", "match `ann_date`", or
"prefer a convenient value" fails: `ann_date` is identical across multi-row
groups and across the income vintages, row order is uncertified, and no
explicit version field exists. A rule that works only for `000858.SZ` or
only to unblock Phase 5B is rejected.

## 8. Audit verdict

**`PROFIT-DEDT-VINTAGE-IDENTITY-NOT-CERTIFIED`**

## 9. Next evidence decision

**`STRUCTURAL-LIMITATION`** — the `fina_indicator` endpoint, as evidenced,
structurally lacks the income-style vintage fields (`report_type`,
`f_ann_date`), its `update_flag` is present-but-empty in the one schema that
carries it, and `ann_date` is proven insufficient to distinguish vintages.

**Exact missing semantic fact:** whether `fina_indicator` carries any
vintage/revision identity at all — specifically (a) whether
`fina_indicator.ann_date` is the original announcement date and stays stale
on restatement, or updates to the restatement date; and (b) whether
`fina_indicator.update_flag` is ever non-empty and, if so, whether it
distinguishes vintages. No preserved evidence answers either.

## 10. Architecture amendment

**`AMENDMENT-NOT-JUSTIFIED`** — existing evidence does not support a generic,
deterministic, PIT-safe vintage rule for `profit_dedt`, so relaxing the CH3
gate is not justified by existing evidence. (It could be revisited only if
the missing semantic fact in §9 is established by future evidence.)

## 11. Status (unchanged)

```
B2:        B2-PARTIALLY-RECOVERED
Stage 3:   STAGE3-RERUN-NOT-ELIGIBLE
Barrier 4a: NOT PASSED
P5B-7:      BLOCKED
```

## 12. Repository action

Documentation only, committed to `master`. No production code, no tests, no
live calls, no evidence-branch merge, no push, no tag.
