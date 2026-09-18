# Phase 4D-B Tushare (China A-Share) Adapter Certification (P4DB-9)

Final Phase 4D-B gate. This document records what was **actually
re-verified** by exercising the real, assembled
`smart_beta.vendors.tushare.source.TushareAShareSource` against the real
specimens recorded by P4DB-1 through P4DB-7, and what remains **NOT
CERTIFIED**. It is the checked-in, human-readable half of the executable
certification in `tests/test_phase4d_b_certification.py`; every
disposition below corresponds to a real assertion in that file, exercised
against the assembled source (or, where a named leg is not reachable
through the seven `PITDataSource` methods, through the same injected
`TushareClient` and the owning module's public mapper — always stated).

Baseline: `master` at the P4DB-8 merge (`17f65728a58edce89b75160628b2592d850e83f0`),
Barrier 3 passed. This task edits **no production code** and no existing
test file. It re-derives every claim by calling the real merged code
directly; it does **not** import or re-run any prior task's own test file.

Executable authority: `tests/test_phase4d_b_certification.py`. The report
synchronization test at the bottom of that file is a **synchronization
guard only** — it asserts that all 16 line-items are present with an
explicit status and that the four frozen values are exact; it proves
nothing by itself. Each item's real proof is the named executable check.

Every specimen invoked here is **proxy-observed** through the third-party
proxy `pcd.mobcvb.cn/tushare/pro`. Proxy-observed evidence is **not**
direct official-Tushare behavior and certifies nothing about the paid
official API at any point tier. P4DB-1's and P4DB-2's fixtures are
**contract-modeled** (Wave 1 had no live credential); P4DB-4 through
P4DB-7's fixtures are **live-recorded**. The two classes are never
conflated below.

---

## Dispositions (completeness invariant: all 16 considered, none silent)

**[1] `SCHEMA CONFORMANCE = PASS`**
`test_item01_schema_conformance_all_seven_methods` runs Phase 3's
unmodified `pit.compliance.check_schema_conformance` against the assembled
source over the real `000001.SZ` FY2022 specimens
(`2022-06-30..2022-09-30`, field `total_revenue`) and gets exactly seven
results (calendar + six panel methods), all `PASS`. The real end-to-end
`000001.SZ` window is also exercised, so this is not an all-empty
technicality.

**[2] `CUMULATIVE VS SINGLE-QUARTER RECONCILIATION = PASS`**
`test_item02_cumulative_single_quarter_reconciliation` runs the Gate-1
arithmetic through the assembled source: cumulative Q3
`total_revenue = 138,265,000,000` and H1 `= 92,022,000,000`, whose
difference is **46,243,000,000**, matching the independently-tagged
`report_type=2` single-quarter record exactly; `n_income`
`36,659,000,000 − 22,088,000,000 = 14,571,000,000` likewise. The two
cumulative legs run through `TushareAShareSource.get_fundamentals`; the
single-quarter leg is read from the same live-recorded specimen through
P4DB-6's public `map_statement_payload` on the same injected client,
because Tushare's default (`report_type`-omitted) response does not
contain `report_type=2` and P4DB-6 exposes no `report_type` selector (the
named P4DB-8 interface finding). The adapter itself subtracts nothing.

**[3] `CHINA FUNDAMENTALS VINTAGE CAPABILITY = PASS`**
`test_item03_vintage_capability_600518_and_002450` cites both named
specimens through the real assembled source:
* `600518.SH` FY2017 `total_revenue`: `check_fundamentals_vintages_preserved`
  confirms the original (`knowledge_date=2018-04-26`,
  `value=26,476,970,977.57`, `is_restatement=False`) and the restated
  (`knowledge_date=2019-04-30`, `value=17,578,618,640.06`,
  `is_restatement=True`) vintages both survive.
* `002450.SZ` FY2015 `total_revenue`: the assembled source raises
  P4DB-6's loud `TushareConflictingVintageError` on the schema key
  `(002450.SZ, 2015-12-31, total_revenue, 2021-02-28)` — two real,
  distinct-value vintages collapse onto one key and the adapter refuses
  to guess. That refusal is positive vintage-capability evidence, not a
  certification gap.

**[4] `CHINA FUNDAMENTALS VINTAGE COVERAGE COMPLETENESS = NOT CERTIFIED`**
`test_item04_vintage_coverage_counterexample` names **`002069.SZ` FY2017**
as the standing counterexample. Through the assembled source,
`total_revenue` returns exactly **one** row
(`knowledge_date=2020-10-15`, `value=3,205,845,988.9`), even though the
row's own `ann_date=2018-04-28` vs `f_ann_date=2020-10-15` proves an
in-place reprocessing occurred and public record documents a real
restatement. A single retrievable vintage is never read as "no
restatement occurred". This line is deliberately **not** collapsed into
item 3's PASS: capability (multiple vintages *can* be emitted) is not
coverage (every restatement is retrievable).

**[5] `KNOWLEDGE-DATE RULE = PASS` (as an adapter-internal parsing policy)**
`test_item05a_knowledge_date_real_specimens`,
`test_item05b_fallback_case_b_fires`,
`test_item05c_fallback_case_c_refuses`, `test_item05d_case_d_malformed`,
and `test_item05e_case_e_both_missing` exercise the real `002450.SZ`
stale-`ann_date`/`f_ann_date=20210228` specimen and the firing and
non-firing fallback cases through the assembled source. The real
`report_type=1` and `report_type=4` FY2015 income rows resolve to
`knowledge_date=2021-02-28` (the later `f_ann_date`), never the stale
`ann_date=2016-04-22`, while the `report_type=5` original carries the
2016-04-22 date; a `2022-03-31` constructed case-B row (missing
`f_ann_date`, eligible `report_type=1`) resolves to `ann_date`; and cases
C/D/E are dropped and appear in `get_uncertain_observations` with
`ineligible_report_type_for_fallback`, `f_ann_date_malformed`, and
`both_dates_missing_or_invalid` respectively. **Caveat (plan policy 3): this is an adapter-internal parsing policy for
Tushare's specific field semantics as observed through this proxy; it must
not be documented as a general claim about what `f_ann_date` means for any
other vendor or for direct official-Tushare access.**

**[6] `CH3 NI-EX-NONRECURRING = FIELD-LEVEL, VINTAGE-JOIN-DEPENDENT, NOT A SINGLE CANONICAL FIELD`**
`test_item06a_ch3_suppressed_600518`,
`test_item06b_ch3_suppressed_002450`, and
`test_item06c_ch3_successful_join` run through the assembled source. The
`600518.SH` FY2017 and `002450.SZ` FY2015 periods are suppressed (no
`ni_ex_nonrecurring` row; each appears in `get_uncertain_observations`
with `reason="ch3_vintage_join_not_certified"`, for condition 2 — a
restated `report_type` exists). The clean `000001.SZ` 2022-09-30 join
succeeds (`value=36,597,000,000`, `knowledge_date=2022-10-25`,
`reporting_basis=CUMULATIVE_YTD`). There is no single canonical field:
`fina_indicator.profit_dedt` has the right economics but no
`report_type`; `income.net_after_nr_lp_correct` has vintage tracking but
was empty even in the 600518.SH restatement. A matching date is necessary
corroboration, never proof of vintage identity.

**[7] `TOTAL MARKET CAP = PASS (canonical)` / `FLOAT MARKET CAP = DIAGNOSTIC ONLY, NOT INDEPENDENTLY CERTIFIED`**
`test_item07_total_market_cap_canonical` runs the real `000001.SZ`
2013-06-20 specimen through the assembled source: `total_mcap` is the
vendor's `daily_basic.total_mv` exactly, its `total_share × close` sanity
ratio is ~1.0, and `float_mcap` is the genuinely distinct
`daily_basic.circ_mv` (never aliased to total). The module marker
`FLOAT_MARKET_CAP_IS_DIAGNOSTIC_ONLY is True`. The float line is **not** a
PASS: it is a named, diagnostic-only quantity, and no migrated path may
substitute it for `total_mcap`.

**[8] `CH4 TURNOVER FEASIBILITY = PASS (mechanical only)`**
`test_item08_ch4_turnover_feasibility_mechanical_only` reconstructs the
real `000001.SZ` 2013-01..2014-03 window through the assembled source in
two sub-windows inside the proxy's ≤366-day cap: `get_market_cap` yields
**295** rows with `_total_share` present on **251**, and the three real
distinct `_total_share` values are exactly
**512,335 / 819,736 / 952,075**. Joining the same client's real `daily`
`vol` leg gives **251** usable turnover observations, all finite and
positive. **This is mechanical feasibility only; it is explicitly not a
PIT-immutability certification of historical `total_share`** — the field
demonstrably steps within the window.

**[9] `CHANGING ADJ_FACTOR RECONSTRUCTION = PASS`**
`test_item09_changing_adj_factor_2013_06_20` runs the real `000001.SZ`
2013-06-20 ex-date through the assembled source and the owning mappers:
`get_market_cap` shows `_total_share` stepping `512335 -> 819736`
(exactly ×1.6), `get_corporate_actions` emits the `split_dividend` action
with the frozen formula's `adjustment_factor ≈ 1.614263`, and the
vendor's own `adj_factor` series (fetched through the same injected client
and mapped by P4DB-5's public `map_adj_factor_to_corporate_actions`)
steps `36.173 -> 58.387` (ratio ≈ 1.61407). The two agree far inside the
tests' 0.1% tolerance. (The assembled source's `get_corporate_actions`
surfaces the dividend-derived action, not the `adj_factor` series — a
named interface boundary, not a mapping defect; the cross-check uses the
same real client and the owning mapper.)

**[10] `IDENTIFIER CONTINUITY = NOT CERTIFIED`** (carried forward from P4DB-2, unchanged)
`test_item10_identifier_continuity_not_certified` drives the real
resolution path: `000001.SZ` resolves with `is_permanent=True`; the real
terminal `002450.SZ` row resolves with `is_permanent=False`; and the
`000024.SZ`/`001914.SZ` restructuring pair resolves to two **different**
`stock_id`s with no vendor-asserted old-code → new-code join (P4DB-2's
bounded `stock_basic`/`namechange` investigation found no such field, and
the `constructed_namechange` fixture is keyed by the same `ts_code` it
describes). No heuristic rename mapping was implemented, and P4DB-2 had no
live credential to re-confirm against the vendor — so the status is
unchanged `NOT CERTIFIED`.

**[11] `DELISTING CORROBORATION = PASS`** (from P4DB-7's report; proxy-observed)
`test_item11_delisting_corroboration_002450` runs `get_listing_info`
through the assembled source: the real delisted specimen `002450.SZ`
(`stock_basic.delist_date=2021-05-31`, `list_status="D"`) is corroborated
by a sustained absence of authoritative sessions after the claim
(`_delist_corroboration == "corroborated"`; 146 sessions, far above the
frozen threshold of 5). The still-listed `000001.SZ` control reports
`delist_date=NaT` and `no_delist_claim`. This is a POC-grade PASS on a
proxy-observed specimen, **not** a certification of direct
official-Tushare behavior and **not** a production delisting feed.

**[12] `PROXY DETERMINISM = NOT CERTIFIED`**
`test_item12_proxy_determinism_not_certified` executes P4DB-1's
canonical-consistency safeguard against real recorded payloads (identical
→ returns the payload; a differing pair → raises the named
`TushareNonDeterministicResponseError`) and audits every fixture set's
recording history. The live-recorded P4DB-4/5/6/7 sets were recorded
through the proxy and the P4DB-5/P4DB-6 (and the small P4DB-7) requests
declare byte-for-byte three-sample canonical agreement; **no**
`TushareNonDeterministicResponseError` fired in any task's recording (so
this is not `FAIL`). But coverage is incomplete: P4DB-4's manifest
declares no canonical-consistency check, P4DB-7's large
`list_status="D"` sweep and `000001.SZ` daily window were recorded with a
**single** sample to respect rate limits, and P4DB-1/P4DB-2's Wave 1
fixtures are contract-modeled with no live recording at all. Under the
anti-upgrade rule (absence of a counterexample is not certification;
constructed is not live), the cross-task determinism claim is therefore
`NOT CERTIFIED`, with the positively checked live subset named — not
smoothed into a PASS.

**[13] `PROXY SUFFICIENT FOR PHASE 4D-B POC = YES`**
Frozen. `test_item13_proxy_sufficient_for_poc` asserts the literal line
and exercises the POC-grade end-to-end path (schema conformance plus the
real Gate-1 reconciliation) that the proxy makes possible. The proxy
delivered every specimen this POC needs; POC sufficiency is scoped to
this phase's read-only, fixed-universe adapter work and is not a claim
about the official API.

**[14] `PROXY SUFFICIENT FOR PRODUCTION = NO`**
Frozen. `test_item14_proxy_not_sufficient_for_production` asserts the
literal line and re-cites the reasons: (a) provenance/ToS/dependency risk
— every fact carries `access_path="proxy:pcd.mobcvb.cn"`, a third-party
proxy's availability/terms are not a production dependency contract, and
`float_mcap` is only the vendor `circ_mv`, not an independently certified
float-adjusted figure; and (b) the failure modes this phase's own
evidence found — the `002450.SZ` FY2016/17 blank-out (a field
invalidated without replacement) and the stale-`ann_date` in-place
reprocessing, plus the un-chunked ≤366-day range cap. None of this blocks
Phase 4D-B itself, which is scoped to POC-grade access.

**[15] `TUSHARE LICENSING/ATTRIBUTION = NOT CERTIFIED`**
`test_item15_licensing_attribution_cross_task_audit` audits all eight
prior tasks' committed fixtures. The **attribution** half holds: every
fixture set is attributed to the Tushare proxy and no credential value is
committed; the assembled fundamentals output carries
`source_vendor="Tushare"` and `access_path="proxy:pcd.mobcvb.cn"` on
every row, and the committed specimens are small bounded artifacts, not a
bulk market redistribution. The **retrieval-provenance** half does not
hold universally: P4DB-1's `client/manifest.json` declares
`live_recorded: false` (no token at implementation time) and P4DB-2's
fixtures are all `constructed_`/hand-built, while P4DB-4 through P4DB-7
are live-recorded with `recorded_at`/`access_path`. Because the frozen
requirement is *every* committed fixture, and the Wave 1 sets carry
documented-but-not-live provenance, the cross-task item is `NOT
CERTIFIED` rather than a clean PASS — the constructed/live distinction is
preserved, not conflated. No bulk redistribution was committed; the
maximal single specimen is the bounded 339-record `list_status="D"`
audit sweep, far below the full A-share universe.

**[16] `KNOWN-MISSING (BLANK-OUT) HANDLING = PASS`**
`test_item16a_blank_out_real_specimen_assembled` and
`test_item16b_blank_out_not_silent_zero_absence_or_restatement` run the
real `002450.SZ` FY2016 (`period=20161231`) and FY2017
(`period=20171231`) blank-out specimens **through the fully assembled
`TushareAShareSource`**, not merely re-cited from P4DB-6's unit test.
Separate assertions confirm:
* the vendor-blanked `total_revenue`/`n_income` values are machine-visible
  as `is_blank_out=True` at the assembled-source level;
* (a) the row is **not** dropped — it is present with
  `knowledge_date=2021-02-28`;
* (b) the value is **not** `0.0` — it is `NaN`;
* (c) it is **not** an ordinary NaN indistinguishable from routine
  unavailability — in this adapter genuine non-reporting is represented as
  row *absence* (the real `002450.SZ` FY2015 `int_income` row is `None`
  and emits no fact), while every emitted NaN row is tagged
  `is_blank_out=True`, so a value-only reader cannot mistake the two;
* (d) `is_blank_out` and `is_restatement` are **independent** facts — the
  blanked rows are `is_restatement=True` because of the separate
  `ann_date != f_ann_date` date signal on their raw rows (e.g. FY2017's
  `ann_date=20180420 != f_ann_date=20210228`), not because they are
  blanked; and a non-blank row (`002069.SZ` FY2017) is also
  `is_restatement=True` while a clean row (`000001.SZ` 2022-06-30) is
  `False`; blankness says nothing about restatement.

---

## Named findings and divergences (no production change made)

* **P4DB-4 manifest records no canonical-consistency check.** The
  `market_data` fixtures are live-recorded but their manifest declares
  only `fetch(retry_on_empty=True)`; it does not state three-sample
  canonical agreement, unlike P4DB-5/P4DB-6. This (with P4DB-7's
  single-sample large sweeps and Wave 1's contract-modeled fixtures) is
  why item 12 is `NOT CERTIFIED`. Proposed follow-up shape: a
  fixture-re-recording task that re-captures every live specimen with the
  canonical-consistency safeguard and records the sample counts, mirroring
  P4DB-6's manifest.
* **P4DB-8 assembly does not surface the `adj_factor` series or the
  `report_type=2` single-quarter row.** Both are reachable only through
  the same injected client plus P4DB-5/P4DB-6's public mappers (as items
  2 and 9 state). Proposed follow-up shape: a consumer-side
  `report_type`/`adj_factor` accessor task, not an adapter mapping change.
* **The proxy's ≤366-day cap is not transparently chunked** (P4DB-8's own
  finding): item 8 works around it by issuing two in-cap sub-windows.
  Fix belongs in a P4DB-1 transport follow-up.
* **Cross-task retrieval-provenance gap** (item 15): P4DB-1/P4DB-2
  fixtures are contract-modeled with no live credential. Proposed
  follow-up shape: a Wave-1 re-recording task once `TUSHARE_PROXY_TOKEN`
  is available, as their own READMEs request.

No Wave 1/2/3 defect was found that requires a production-code fix; the
findings above are all documented interface/provenance boundaries.

---

## Completeness statement

No item considered relevant in principle is silently absent. All 16
required line-items carry an explicit disposition and a named executable
check in `tests/test_phase4d_b_certification.py`; the four frozen values
(`CHINA FUNDAMENTALS VINTAGE CAPABILITY = PASS`, `CHINA FUNDAMENTALS
VINTAGE COVERAGE COMPLETENESS = NOT CERTIFIED`, `PROXY SUFFICIENT FOR
PHASE 4D-B POC = YES`, `PROXY SUFFICIENT FOR PRODUCTION = NO`) are
asserted exactly, so no future clean run can upgrade them by editing this
document. Every reference to a proxy-observed or contract-modeled
specimen above is labeled as such; nothing here certifies direct
official-Tushare behavior at any point tier, and no constructed fixture
is presented as live evidence.
