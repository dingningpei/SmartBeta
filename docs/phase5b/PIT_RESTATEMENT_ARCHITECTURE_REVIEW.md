# PIT Restatement Architecture Review

**STRICTLY OFFLINE ARCHITECTURE REVIEW.** Live provider calls: **0**.
Credential access: forbidden and not performed. Production-code changes:
**none**. Test changes: **none**. No frozen rule changed, no Stage 3 rerun,
no evidence-branch merge, no push, no tag.

- Master before: `48cc8d25371a978d8cf71a468581117a78173fb6`
- Forensics input: `docs/phase5b/B2_CA2_INCOME_ANCHOR_FORENSICS.md`
  (`48cc8d2…`)

## 1. Question

What should the generic PIT architecture mean when multiple real vintages
of the same logical financial report are preserved? Specifically: whether a
later restatement should make the earlier original vintage retroactively
unusable for pre-restatement formations, or whether the trusted engine
should select among preserved vintages by what was knowable at each
formation date. This is answered from the project's generic PIT principles,
not from the desire to unblock Phase 5B.

## 2. Origin of the `ann_date == f_ann_date` condition

- Introduced/strengthened as the **CH3 join condition (i)** in the Phase 4D-B
  plan (`worker_tasks/phase4d_b/phase4d-b-plan.md`, item 7), and amended by
  the **2026-09-17 independent audit, Patch 2**: a previous "match on
  `(stock_id, report_period_end)` plus absence of reprocessing evidence
  *elsewhere*" test was found insufficient; **positive evidence** was
  required.
- Implemented and commented in
  `smart_beta/vendors/tushare/fundamentals.py::_assemble_ch3_records`
  ("Condition 1: every anchor row must have valid, equal ann/f dates, and
  there must be exactly one distinct (ann, f) vintage").
- Exercised by the CH3 join test suite in `tests/test_tushare_fundamentals.py`
  (clean specimen emits; report_type=4/5 and fina-ann-divergence suppress).

## 3. Intent of the condition (what failure mode it prevents)

The condition is grounded in plan **item 3** — the 002450.SZ (康得新) fraud
specimen: a `report_type=1` row whose `ann_date=2016-04-22` stayed stale
while `f_ann_date=2021-02-28`, and whose *values were silently replaced* by
the 2021 restatement. Plan item 3: **"`ann_date` alone is never safe when
it diverges from `f_ann_date`."**

The rule exists to prevent **look-ahead / attribution error**: attributing a
restated value to the original announcement date. It is a provider-safety
gate, not a generic PIT selection rule.

## 4. Generic PIT architecture (the trusted engine)

`smart_beta/pit/fundamentals.py` (`latest_known_value`, Phase 3 P3-D) is the
project's one trusted temporal-selection rule and states, verbatim:

> "``FUNDAMENTALS_FACT_SCHEMA`` is bitemporal and append-only: the same
> ``(stock_id, report_period_end, field)`` fact may appear many times, once
> per vintage, distinguished by ``knowledge_date`` (**a restatement is a new
> row, never an overwrite**)."

> "for an as-of knowledge date ``t``, no returned fact may have
> ``knowledge_date > t``. … Among the visible vintages … the one with the
> greatest ``knowledge_date`` wins."

**The generic engine already implements vintage-as-of selection.** The
architecture boundary is: the **adapter** maps provider rows to append-only
facts with correct `knowledge_date`; the **engine** selects the latest
knowable vintage per formation date. This is Architecture B, already frozen.

## 5. Why the CH3 join is special (not a generic-architecture property)

Plan **item 5**: `fina_indicator.profit_dedt` has the right economic
definition but **no `report_type` at all** — it cannot preserve
pre-adjustment vintages. Therefore the CH3 value source cannot be
independently vintage-tagged; the only corroboration available is that
`fina_indicator.ann_date` equals the anchor's `ann_date` (condition iii), and
that the anchor itself is positively non-restated (`ann == f_ann`, condition
i). The whole-period suppression is a **conservative gate for the CH3 field
only**, forced by that endpoint limitation — income/balancesheet fields do
**not** go through this gate (they map each row separately with its own
`knowledge_date` and are selected as-of by the engine).

## 6. Critical restatement question

Does `ann_date=20251031, f_ann_date=20260430` support interpreting
`20260430` as the knowledge/publication date of the restated vintage?

**CERTIFIED** by existing frozen semantics: plan item 3 defines
`knowledge_date(row) = f_ann_date if ann_date != f_ann_date else ann_date`,
and `knowledge_date_decision` case A resolves a valid `f_ann_date` to itself.
So the restated row's `knowledge_date = 20260430`, and the original row's
`knowledge_date = 20251031`.

## 7. Formation-time thought experiment (income fields, no code change)

The income anchor itself is preserved as two append-only vintages:

| vintage | knowledge_date | total_revenue | n_income | basic_eps |
|---|---|---|---|---|
| original | 20251031 | 60.9B | 22.2B | 5.542 |
| restatement | 20260430 | 30.6B | 6.79B | 1.668 |

Under the **already-frozen** engine (`latest_known_value`):

- **2025-11-30:** only `knowledge_date=20251031` is visible → original used.
- **2026-03-31:** same → original used (restatement not yet knowable).
- **2026-04-30:** restatement (`20260430 <= 20260430`) becomes visible; both
  vintages present → latest (restatement) wins.
- **2026-05-31:** restatement wins.

**No look-ahead**: the 2026-04-30 restatement's existence/values cannot
affect the 2025-11-30 or 2026-03-31 decision. This is the behavior the
generic engine already gives income/balancesheet fields.

For the **CH3 field** (`ni_ex_nonrecurring`), the same engine cannot be
applied today because the recovered `fina_indicator` row (`ann_date=20251031`,
single row, no `f_ann_date`/`report_type`) provides **no signal** to assign
its `profit_dedt` to the 20251031 vs 20260430 vintage. That is the precise
reason condition (i) fails closed: the CH3 value's vintage is genuinely
ambiguous when the anchor is restated.

## 8. Look-ahead test (conceptual vintage-as-of model)

- At **2025-11-30**: engine can use only `knowledge_date <= 2025-11-30`; the
  2026-04-30 restatement is excluded. Pass.
- At **2026-04-30** onward: the engine must not silently ignore the newer
  vintage; `latest_known_value` selects the restatement. Pass (for fields with
  per-vintage `knowledge_date`).

The CH3 field fails the test **not** because vintage-as-of is wrong, but
because `fina_indicator` supplies no per-vintage signal to feed it.

## 9. Scope / generalization (offline scan)

A full scan of preserved P5B-6 income/balancesheet raw evidence found the
"multiple report_type=1 vintages, divergent `f_ann_date`" pattern in **one
stock/period only**: `000858.SZ::20250930`, in both `income` and
`balancesheet` (the Q3-2025 report was materially restated 2026-04-30:
income revenue 60.9B→30.6B; balancesheet total_assets 181.1B→191.2B,
total_liab 34.9B→60.4B). The same in-place-reprocessing pattern is already
documented in the Phase 4D-B plan via the 002450.SZ / 002069.SZ / 600518.SH
specimens. In the pilot universe it is **isolated** to this one material
restatement event; no recurrence claim is made beyond the preserved evidence.

## 10. Architecture verdict

**`CURRENT-RULE-IS-CONSERVATIVE-GATE`**

The `ann_date == f_ann_date` condition is a provider-safety gate (positive
evidence required against in-place reprocessing), not a fundamental PIT
requirement. The generic trusted engine already embodies vintage-as-of
selection; the gate is an adapter-level conservatism specific to the CH3
field's source (`fina_indicator.profit_dedt`, which lacks vintage-tracking
fields).

## 11. Amendment decision

**`ARCHITECTURE-AMENDMENT-REQUIRES-MORE-EVIDENCE`**

The generic engine needs no amendment (it already selects as-of). Whether
the CH3 gate may be safely refined (e.g. to allow the original vintage to
feed pre-restatement formations) depends on evidence the repository does not
currently hold — namely whether `fina_indicator`'s `ann_date`/`profit_dedt`
reliably tags a specific vintage when the income anchor is restated. Until
that is established, the conservative gate is correct. An amendment being
warranted would not itself authorize implementation.

## 12. B2 independence (anti-overfitting check)

**YES** — the same conclusion follows from `smart_beta/pit/fundamentals.py`,
the Phase 4D-B plan items 3/5/7, and the 002450.SZ / 002069.SZ / 600518.SH
specimens (none of which is 000858.SZ), and it does not depend on whether
relaxing the gate would unblock Phase 5B.

## 13. Status (unchanged)

```
B2:        B2-PARTIALLY-RECOVERED
Stage 3:   STAGE3-RERUN-NOT-ELIGIBLE
Barrier 4a: NOT PASSED
P5B-7:      BLOCKED
```

## 14. Repository action

Documentation only, committed to `master`. No production change, no test
change, no live call, no evidence-branch merge, no push, no tag.
