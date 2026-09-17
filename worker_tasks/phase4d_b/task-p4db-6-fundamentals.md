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

**Amended by the 2026-09-17 independent audit (Patch 1/Patch 3) — the
original version of this section specified the precedence/non-
fabrication rule correctly but under-specified the required tests and
the visibility of the "otherwise" branch. Both are corrected below; the
rule itself is unchanged.**

```
IF f_ann_date is present AND parses as a valid date:
    knowledge_date = f_ann_date                                        # case A

ELIF f_ann_date is missing/null (genuinely absent, not malformed)
     AND ann_date is present AND parses as a valid date
     AND report_type in {1, 2, 3, 6, 7, 8}:
    knowledge_date = ann_date                                          # case B (fallback fires)

ELSE:
    row is dropped from get_fundamentals's output; NEVER a fabricated
    knowledge_date. Recorded in the observation-level uncertainty
    side-table (see the new section immediately below) with reason:
      - "ineligible_report_type_for_fallback"   # f_ann_date missing,
                                                 # report_type in
                                                 # {4,5,9,10,11,12}     # case C
      - "f_ann_date_malformed"                  # f_ann_date present
                                                 # but fails to parse   # case D
      - "both_dates_missing_or_invalid"         # neither date usable  # case E
```

`report_type in {4, 5, 9, 10, 11, 12}` (the adjusted/pre-adjustment
families) are **never** eligible for the `ann_date` fallback — these are
exactly the codes the evidence associates with reprocessing.

**Required tests for this policy specifically (all five lettered cases
are mandatory; `ann_date == f_ann_date` covers case A only and must
never be treated as coverage of case B, the fallback actually firing):**

- **Case A** — a real specimen where `f_ann_date` is present and valid
  (may coincide with `ann_date`, the common case) — `knowledge_date`
  resolves to `f_ann_date`.
- **Case A, divergent** — a real specimen where `ann_date != f_ann_date`
  and `f_ann_date` wins (`002450.SZ` FY2015 `report_type=1` row:
  `ann_date=20160422`, `f_ann_date=20210228` — `knowledge_date` must
  resolve to `2021-02-28`, **not** `2016-04-22`).
- **Case B (the fallback actually firing — this was missing from the
  original spec and is not covered by any case-A test)** — a real
  specimen (or, if none is found after a genuine search, an explicitly-
  labeled *constructed* one, documented as constructed in your test's
  docstring) where `f_ann_date` is genuinely null/missing, `ann_date` is
  present and valid, and `report_type` is one of `{1, 2, 3, 6, 7, 8}` —
  assert `knowledge_date` resolves to `ann_date`.
- **Case C** — `f_ann_date` missing on an *ineligible* `report_type`
  (4/5/9/10/11/12) — assert the row is dropped, not defaulted, and
  appears in the uncertainty side-table with
  `reason="ineligible_report_type_for_fallback"`.
- **Case D** — `f_ann_date` present but malformed/unparseable — assert
  the row is dropped (never coerced into a best-effort date) and appears
  in the side-table with `reason="f_ann_date_malformed"`.
- **Case E** — both `ann_date` and `f_ann_date` missing or invalid —
  assert the row is dropped and appears in the side-table with
  `reason="both_dates_missing_or_invalid"`.

## Observation-level uncertainty (Patch 3 — new, required)

Aggregated counters are insufficient evidence for a PIT-safety claim —
knowing "N rows were dropped" does not tell a consumer *which*
`(stock_id, report_period_end, field)` observations are affected.
Implement `get_uncertain_observations(start, end, fields) ->
pd.DataFrame` in this same module (no new file, no `PITDataSource`
signature change — this is adapter-specific extra API surface,
analogous to how `is_blank_out` is an adapter-specific extra column, and
it does **not** touch `smart_beta/pit/*`), returning one row per
uncertain observation with at least: `stock_id`, `report_period_end`,
`field`, `report_type`, `raw_ann_date`, `raw_f_ann_date`, `reason`
(a string status — see the case C/D/E taxonomy above, plus
`"ch3_vintage_join_not_certified"` from the CH3 section below), and
`retrieved_at`.

This side-table is the mechanism that keeps four distinct states
machine-visible and non-collapsible into `NaN`/absence-of-row or a false
"no restatement" reading:

- **KnownMissing** — a row IS emitted in `get_fundamentals`'s normal
  output with `is_blank_out=True` (policy 6, unchanged) — this state is
  row-level and schema-visible, not side-table-only, though it may also
  appear in the side-table for a unified audit trail.
- **UnknownAsOf** — correctly left to `pit.fundamentals.
  latest_known_value`'s existing, unmodified "absent group = no row"
  behavior (this task never resolves an as-of query — see Non-goals).
- **Ambiguous/UncertifiedKnowledgeDate** — cases C/D/E above: the row is
  absent from `get_fundamentals`'s output (a fabricated knowledge_date
  would be worse than absence) but present, with a reason, in
  `get_uncertain_observations`.
- **Incomplete/NotCertifiedVintageCoverage** — this one is necessarily a
  *dataset-level* disposition, not a per-row adapter tag: the adapter
  has no way to detect a restatement it was never given evidence of
  (the `002069.SZ` counterexample). This state is carried at the
  certification-report level (P4DB-9, policy 2), not invented here as a
  per-row flag — do not attempt to synthesize a per-row "might have been
  restated" signal; that would itself be a fabrication.

**Required test for this section:** running `get_uncertain_observations`
over a fixture set containing at least one of each of cases C, D, and E
(construct D and E if no real specimen has them — cases C and the CH3
suppression case below should both have real specimens) returns exactly
one row per case, each keyed correctly and carrying the right `reason`.

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

**CH3 (`field="ni_ex_nonrecurring"`) emission rule — AMENDED by the
2026-09-17 independent audit (Patch 2). The original two-condition rule
is retained as necessary but is NO LONGER SUFFICIENT on its own — it
proved a fact only by *absence of counter-evidence in other tables*,
which is not positive evidence that `fina_indicator`'s own `profit_dedt`
value belongs to the anchor vintage. A required third condition is
added below.**

For a given `(stock_id, report_period_end)`, fetch the `income`/
`balancesheet` vintages for that period AND the `fina_indicator` row
(including all fields it returns, not just `profit_dedt`) for the same
period. **First, as a blocking sub-investigation (do this before writing
the join logic, and report the result explicitly): does `fina_indicator`
return its own `ann_date` field?** (It was observed doing so in earlier
investigation output, e.g. `{'ann_date': '20230309', 'end_date':
'20221231', 'profit_dedt': ...}`, but this was never verified as a
required, always-present field — confirm empirically.)

Emit a `ni_ex_nonrecurring` fact **only if all three hold**:
1. The `income` `report_type=1` row for that `(stock_id,
   report_period_end)` has `ann_date == f_ann_date` (no reprocessing
   evidence on the anchor vintage).
2. No `report_type in {4, 5, 9, 10, 11, 12}` row exists for the same
   `(stock_id, report_period_end)` in `income` or `balancesheet` (no
   restated-comparative vintage exists that would indicate this period
   was ever touched by a later correction).
3. **(New, required.)** `fina_indicator`'s own `ann_date` for this
   `(ts_code, end_date)` is present and equals the anchor `income` row's
   `ann_date`. A matching date is *necessary* corroborating evidence,
   not proof by itself, of vintage identity — do not claim date equality
   "universally proves" the association; it is the best positive
   evidence available in this dataset, used precisely because
   `fina_indicator` carries no `report_type` of its own to check
   directly. **If `fina_indicator` is found to expose no comparable date
   field at all (condition 3 cannot be evaluated), CH3 must not be
   emitted for ANY period this phase** — do not fall back to the
   original two-condition rule; report this as a specific, named finding
   for the certification stage rather than silently degrading the
   safety bar.

If all three hold, emit `ni_ex_nonrecurring` with `value=profit_dedt`,
`knowledge_date` = the `income` `report_type=1` row's own
`knowledge_date` (borrowed from the anchor vintage per plan.md policy 9
— never `fina_indicator`'s own dates as the *knowledge_date*, since
`fina_indicator` carries no vintage/restatement information of its own
even when its `ann_date` is used as corroborating evidence for the join
itself), `is_restatement=False`, `reporting_basis="CUMULATIVE_YTD"`.

**If any of the three conditions fails, emit no
`ni_ex_nonrecurring` row at all** for that `(stock_id, report_period_end)`
— this is a silence, not a guess — **and record an entry in
`get_uncertain_observations` (see above) with
`field="ni_ex_nonrecurring"`, `reason="ch3_vintage_join_not_certified"`.**
This is the executable form of `PIT CH3 VALUE = NOT CERTIFIED /
UNAVAILABLE`: never increase apparent coverage by guessing the
association.

**Required CH3 tests:**
- A clean specimen where all three conditions hold and the join succeeds
  (find one from your own fixture recording — any period with no
  reprocessing evidence on either side and a matching `fina_indicator`
  `ann_date`).
- The `600518.SH` FY2017 specimen: suppressed on condition 2 (a restated
  `report_type=4` row exists) — assert no `ni_ex_nonrecurring` row is
  emitted for `(600518.SH, 2017-12-31)`, and an entry with
  `reason="ch3_vintage_join_not_certified"` appears in
  `get_uncertain_observations`.
- The `002450.SZ` FY2015 specimen: suppressed for the same reason
  (`report_type=5` exists), same side-table assertion.
- **(New, required.)** A specimen (real if found; otherwise explicitly
  constructed and labeled as such) where conditions 1-2 hold but
  condition 3 fails — `fina_indicator`'s own `ann_date` diverges from the
  anchor `income` row's `ann_date` — asserting the join is still
  suppressed. If your investigation finds `fina_indicator` never
  diverges from `income`'s `ann_date` in any real specimen, this test
  may be constructed, but it must exist and must be documented as
  constructed.
- **(New, required.)** If your investigation finds `fina_indicator`
  exposes no comparable date field at all: a test asserting that
  `ni_ex_nonrecurring` is never emitted for any fixture, and every
  candidate period appears in `get_uncertain_observations` with
  `reason="ch3_vintage_join_not_certified"`.

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
- `get_uncertain_observations` schema/shape conformance and the case
  C/D/E coverage required above (an aggregate count alone no longer
  satisfies this task — see "Observation-level uncertainty").
- **The `002450.SZ` FY2016/17 blank-out specimen (policy 6) must be
  exercised by a test in this task's own suite AND is required to be
  re-exercised through the fully assembled `TushareAShareSource` at
  certification time (P4DB-9) — do not treat your own unit-level test as
  sufficient on its own; note this cross-reference in your final report
  so P4DB-9's author can find it.**

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
- All five lettered knowledge-date cases (A-E) and all CH3 join
  conditions (1-3, including the new condition 3's failure mode) have
  explicit, individually identifiable tests — a single combined test
  covering multiple cases is not acceptable, since P4DB-9's certification
  report must be able to cite each one individually.

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

Report: (a) exact function signatures implemented, including
`get_uncertain_observations`; (b) confirmation that the knowledge-date
rule (all five lettered cases A-E), blank-out handling, `update_flag`
de-duplication, and CH3 join-or-suppress logic (all three conditions)
all pass against the real specimens listed above, with any deviation
explained; (c) the `reporting_basis` decision for `fina_indicator`, with
evidence; (d) whether `fina_indicator` exposes its own `ann_date` field
and, if so, whether it was ever found to diverge from the anchor
`income` row's `ann_date` in any real specimen — this directly
determines CH3's coverage and must be stated explicitly, not left
implicit in test names; (e) any case where the `update_flag`
duplicate-value assumption did not hold (report as a blocker if found);
(f) test results; (g) `git diff --stat`. Do not merge, do not touch
`master`.
