# Phase 4D-B Worker Task — P4DB-6: Fundamentals (Wave 2)

## Background (read this first)

Read `worker_tasks/phase4d_b/phase4d-b-plan.md` in full — this task
implements the most evidence-dense part of the plan: frozen policies 2,
3, 4, 6, 7, 8, 9. Read `smart_beta/pit/schema.py`'s
`FUNDAMENTALS_FACT_SCHEMA` and bitemporal decision table, and
`smart_beta/pit/fundamentals.py`'s `latest_known_value` (read the whole
file — it is short) before writing any code, so you understand exactly
what shape you must emit and, just as importantly, what you must **not**
implement (vintage *selection*/"latest known as of t" is
`latest_known_value`'s job; this task never resolves an as-of query, it
only emits raw, fully-timestamped vintage facts).

This is a Wave 2 task, depends on P4DB-1 and P4DB-2 (merged, Barrier 1
passed). Disjoint from P4DB-4, P4DB-5, P4DB-7 — do not import from them.

**This task must not compute any cumulative-to-discrete subtraction, and
must not call anything resembling `latest_known_value` itself.** If you
find yourself writing "get the most recent X" logic, stop — that is
exactly the boundary violation this whole phase's architecture is
designed to prevent (see plan.md, "Why no `pit/*` task is needed").

**Your working directory** will be a git worktree at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-6-fundamentals`
on branch `phase4d_b/task-p4db-6-fundamentals`, branched from `master`
after Barrier 1.

## File ownership

**You may create exactly:**

- `smart_beta/vendors/tushare/fundamentals.py`
- `tests/test_tushare_fundamentals.py`
- `tests/fixtures/tushare/fundamentals/`

**You must not modify** any other `vendors/tushare/*.py` file,
`smart_beta/pit/*` (including `fundamentals.py` and `schema.py` — see
plan.md's explicit reasoning for why no schema change is needed; if your
own investigation makes you believe a schema change genuinely is
required, stop and report that as a blocking finding rather than making
the change yourself), `smart_beta/data/*`, or any existing test file.

## Required output shape

`get_fundamentals(start, end, fields) -> pd.DataFrame`, conforming to
`FUNDAMENTALS_FACT_SCHEMA` (`stock_id`, `report_period_end`, `field`,
`knowledge_date`, `value`, `is_restatement` — all required, exactly as
the existing schema defines them, no changes) **plus** these adapter-
owned extra columns, which pass through `PanelSchema.validate()`
untouched exactly as Tiingo's `is_zero_volume` does on
`get_trading_status`:

| Column | Type | Meaning |
|---|---|---|
| `reporting_basis` | string | `"CUMULATIVE_YTD"` \| `"SINGLE_QUARTER"` \| `"INSTANT"` — see policy 8 |
| `report_type` | string/int | raw Tushare `report_type` code, audit-only |
| `vintage_id` | string | opaque id disambiguating rows sharing the schema's own key when `update_flag` duplicates are observed (see below) |
| `is_blank_out` | bool | `True` when the vendor returned an empty-string value for a field that is normally numeric (see policy 6) |
| `source_vendor` | string | always `"Tushare"` |
| `access_path` | string | always `"proxy:pcd.mobcvb.cn"` for this phase |
| `retrieved_at` | datetime | when your adapter fetched this row |

## Frozen policy 3: knowledge-date rule (implement exactly this)

```
knowledge_date = f_ann_date                         if f_ann_date is present and valid
knowledge_date = ann_date                           if f_ann_date is missing/null
                                                     AND ann_date is present
                                                     AND report_type in {1, 2, 3, 6, 7, 8}
<row dropped, counted in dropped_ambiguous_date_count>  otherwise
```

`report_type in {4, 5, 9, 10, 11, 12}` (the adjusted/pre-adjustment
families) are **never** eligible for the `ann_date` fallback — these are
exactly the codes the evidence associates with reprocessing. Expose
`dropped_ambiguous_date_count` as an attribute or return value your tests
can assert on (e.g. a module-level counter reset per call, or a second
return value/attribute on a small result object — your call, document
it).

**Required tests for this policy specifically:**
- A real specimen where `ann_date == f_ann_date` (the common case) —
  `knowledge_date` equals both.
- A real specimen where they diverge and `f_ann_date` is used (`002450.SZ`
  FY2015 `report_type=1` row: `ann_date=20160422`, `f_ann_date=20210228`
  — `knowledge_date` must resolve to `2021-02-28`, **not**
  `2016-04-22`).
- A real specimen proving the fallback correctly refuses to fire: the
  same `002450.SZ` FY2015 `report_type=5` row has `ann_date ==
  f_ann_date` already (no fallback needed there — find or construct,
  from real fixture data, a case with `f_ann_date` genuinely missing on
  an ineligible `report_type` (4/5/9/10/11/12) and assert the row is
  dropped, not defaulted).

## Frozen policy 6: blank-out handling

`002450.SZ` FY2016 (`period=20161231`) and FY2017 (`period=20171231`)
`income` return `total_revenue`/`n_income` as **empty strings**, not
numbers, on the `report_type=1` row (`f_ann_date=20210228`). Detect this
(empty string where a float is expected) and emit `is_blank_out=True`,
`value=NaN` for that row — never silently coerce the empty string to
`0.0`, and never drop the row entirely (its presence, tagged
`is_blank_out=True`, is itself the signal a consumer needs — dropping it
would look identical to "this field was never reported for this
period," which is a different, false claim).

## Frozen policy 2: vintage capability, `is_restatement`

Set `is_restatement=True` when a row's vintage relationship to an
earlier one is directly evidenced: either (a) it shares
`(stock_id, report_period_end, field)` with another row at a different,
larger `knowledge_date` (the `report_type=1`/`4` pairing, e.g.
`600518.SH` FY2017: `report_type=1` ann_date=2018-04-26 vs.
`report_type=4` ann_date=2019-04-30, both real, both retrievable), or
(b) the row itself shows `ann_date != f_ann_date` (the `002450.SZ`
in-place-reprocessing signal). `is_restatement=False` when neither
signal is present. **Never let `False` be read or documented as "verified
original" — it means "no restatement evidence found for this row,"** the
same distinction Tiingo already established for its own placeholder.

Handle the observed `update_flag` duplicate pattern: every restatement
specimen this phase's evidence covers (`600518.SH`, `002450.SZ`,
`002069.SZ`) showed pairs of rows sharing the same `report_type` and the
same `ann_date`/`f_ann_date` but different `update_flag` (0 vs. 1), with
byte-identical values in every observed case. Since
`FUNDAMENTALS_FACT_SCHEMA`'s key is `(stock_id, report_period_end,
field, knowledge_date)` and these duplicate rows share all four, you
must de-duplicate them before emitting (schema validation rejects
duplicate keys — `PanelSchema.validate` raises on this). Pick the higher
`update_flag` value deterministically (document why: it is the vendor's
own "more final" marker even though observed values never differed) and
record both raw `update_flag` values in `vintage_id` for audit (e.g.
`f"{report_type}:{ann_date}:{f_ann_date}"` is probably sufficient since
`update_flag` carried no additional information in every observed
specimen — but if you find a real case where `update_flag` duplicates
*do* differ in value, that is a significant, reportable finding
requiring a different resolution; do not silently pick one in that case,
report it as a blocker).

## Frozen policy 7 + 8: CH3 join and reporting_basis (the hard part)

`reporting_basis`: `income`/`cashflow` rows get `"CUMULATIVE_YTD"` for
`report_type in {1, 4, 5, 9, 10, 11, 12}` and `"SINGLE_QUARTER"` for
`report_type in {2, 3, 7, 8}`. `balancesheet` rows always get
`"INSTANT"` regardless of `report_type` (balance-sheet fields are stock
quantities, not flow quantities). `fina_indicator` rows (see below) —
determine empirically: does `profit_dedt` behave as cumulative YTD like
`income`'s own fields, or as something else? (The original investigation
found `fina_indicator` has no `report_type` field at all — you must
still decide and document what `reporting_basis` value to emit for it;
the evidence suggests `"CUMULATIVE_YTD"` by analogy to `income`, since
`profit_dedt` is a companion of `income`'s own cumulative net-income
fields, but verify this doesn't contradict any real specimen before
freezing it.)

**CH3 (`field="ni_ex_nonrecurring"`) emission rule — implement exactly:**
For a given `(stock_id, report_period_end)`, fetch both the `income`/
`balancesheet` vintages for that period AND the `fina_indicator`
`profit_dedt` value for the same period. Emit a `ni_ex_nonrecurring` fact
**only if**:
1. The `income` `report_type=1` row for that `(stock_id,
   report_period_end)` has `ann_date == f_ann_date` (no reprocessing
   evidence on the anchor vintage), AND
2. No `report_type in {4, 5, 9, 10, 11, 12}` row exists for the same
   `(stock_id, report_period_end)` in `income` or `balancesheet` (no
   restated-comparative vintage exists that would indicate this period
   was ever touched by a later correction).

If both hold, emit `ni_ex_nonrecurring` with `value=profit_dedt`,
`knowledge_date` = the `income` `report_type=1` row's own
`knowledge_date` (i.e., borrowed from the anchor vintage per plan.md
policy 9 — **not** `fina_indicator`'s own dates, since `fina_indicator`
carries no vintage information of its own), `is_restatement=False`,
`reporting_basis="CUMULATIVE_YTD"`. If either condition fails, **emit no
row at all** for that `(stock_id, report_period_end, "ni_ex_nonrecurring")`
— this is a silence, not a guess, and it must be tested explicitly (a
real specimen where the join is correctly suppressed, not just one where
it succeeds).

**Required CH3 tests:**
- A clean specimen where the join succeeds (find one from your own
  fixture recording — any period with no reprocessing evidence on either
  side).
- The `600518.SH` FY2017 specimen: the join must be suppressed (a
  restated `report_type=4` row exists for this period) — assert no
  `ni_ex_nonrecurring` row is emitted for `(600518.SH, 2017-12-31)`.
- The `002450.SZ` FY2015 specimen: suppressed for the same reason
  (`report_type=5` exists).

## Fixture inputs (all real, confirmed-reachable specimens — record all of these)

- `income`, `000001.SZ`, `period=20220331,20220630,20220930,20221231`,
  each with `report_type=1` — for the Gate-1 cumulative reconciliation
  fixture P4DB-9 needs (`46,243,000,000` / `14,571,000,000` exact-match
  evidence).
- `income`, `000001.SZ`, `period=20220930`, `report_type=2` — the
  independently-tagged single-quarter record used in that reconciliation.
- `fina_indicator`, `000001.SZ`, `period=20220930` and `20220630`.
- `income`, `balancesheet`, `600518.SH`, `period=20171231` (no
  `report_type` filter — get all rows) — the original-vs-restated
  vintage pair (`report_type=1` ann_date=2018-04-26 vs. `report_type=4`
  ann_date=2019-04-30).
- `disclosure_date`, `600518.SH`, `end_date=20171231` — corroborates the
  original vintage's `ann_date`.
- `income`, `600518.SH`, `period=20180331` and `20180930` — the
  `t1 < t2 < t3` adversarial chain raw facts (Q1 original
  `ann_date=20180428`; Q3 original `ann_date=20181027` and restated
  `report_type=4` `ann_date=20191030`).
- `income`, `600518.SH`, `period=20180630` — H1 2018 (only one row
  confirmed to exist; this is deliberate evidence, not a fixture gap —
  record it as-is).
- `income`, `balancesheet`, `002450.SZ`, `period=20151231` (no filter) —
  the 5-row stale-`ann_date`/reprocessed-`f_ann_date` specimen.
- `income`, `002450.SZ`, `period=20161231` and `20171231` — the
  blank-out specimens.
- `disclosure_date`, `002450.SZ`, `end_date=20171231`.
- `income`, `002069.SZ`, `period=20171231` — the single-row
  no-second-vintage counterexample (`ann_date=20180428`,
  `f_ann_date=20201015`).

## Required tests (beyond the policy-specific ones above)

- Schema conformance to `FUNDAMENTALS_FACT_SCHEMA` for the full extra-
  column set.
- No duplicate-key row ever reaches the output (the `update_flag`
  de-duplication is exercised against a real specimen).
- `dropped_ambiguous_date_count` is nonzero on a fixture set that
  contains at least one genuinely-ineligible-fallback row (construct
  this if no real specimen naturally has one — document that it's a
  constructed edge case, distinct from your other, all-real tests).

## Non-goals

No cumulative-to-discrete subtraction (see Background — this is the one
rule this whole task must not violate). No market data, no corporate
actions, no listing logic.

## Acceptance criteria

- `.venv/bin/pytest` green, full suite unaffected.
- Zero live network calls in `pytest`.
- Every frozen policy above (2, 3, 6, 7, 8, 9) has at least one real-
  specimen test, named clearly enough that P4DB-9 can point to it in the
  certification report.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-6-fundamentals
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4d_b/task-p4db-6-fundamentals`, touching only the files listed
above.

## When done

Report: (a) exact function signatures implemented; (b) confirmation that
the knowledge-date rule, blank-out handling, `update_flag`
de-duplication, and CH3 join-or-suppress logic all pass against the real
specimens listed above, with any deviation explained; (c) the
`reporting_basis` decision for `fina_indicator`, with evidence; (d) any
case where the `update_flag` duplicate-value assumption did not hold
(report as a blocker if found); (e) test results; (f) `git diff --stat`.
Do not merge, do not touch `master`.
