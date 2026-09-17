# Phase 4D-B Frozen Architecture: China A-Share Adapter (Tushare)

Baseline: `master` at commit `4a3b859` (Phase 4C complete — P4C-11
engine-migration certification gate merged). This document is the frozen record of Phase
4D-B's design, produced after the Phase 4D-A / 4D-A.2 / 4D-A.3 evidence
rounds (access characterization, gate refinement, knowledge-date and CH3
semantic investigation — full dossier lives in this session's planning
memory, not in the repo; every fact this plan depends on is restated here
so no worker needs that memory). It supersedes nothing upstream — no
Phase 3 (`smart_beta/pit/*`), Phase 4B (`smart_beta/vendors/tiingo/*`), or
Phase 4C file is modified by any task below. It is the reference every
P4DB-1..9 worker spec points back to instead of re-deriving context.

## Scope

Implement and certify `TushareAShareSource`, a
`smart_beta.pit.source.PITDataSource` implementation for China A-shares,
backed by Tushare data reached through a third-party proxy
(`pcd.mobcvb.cn/tushare/pro`, `X-API-Key` auth, token in
`TUSHARE_PROXY_TOKEN`). Certification means: run Phase 3's existing,
parameterized `pit.compliance.check_*` functions against it using real,
recorded specimens, and produce an explicit PASS / FAIL / **NOT
CERTIFIED** report per item — never silently upgrading an untestable or
partially-evidenced capability to a pass. This mirrors
`docs/phase4b_tiingo_certification.md`'s discipline exactly.

**Deferred, not built here:**
- CH3/CH4 factor implementations or migration (item 13 — this phase is
  vendor-adapter only; the adapter emits raw, correctly-timestamped
  vintage facts, it does not compute characteristics).
- Any cumulative-YTD → discrete-quarter subtraction. That belongs to
  whichever future phase migrates CH3/CH4, and must be built entirely
  out of two `pit.fundamentals.latest_known_value` calls at the *same*
  `as_of` (see "Why no `pit/*` task is needed" below) — Phase 4D-B does
  not need to and must not implement it.
- A paid/official Tushare API integration. This phase only implements
  the proxy transport; a future official-API transport is a drop-in
  `TushareClient` implementation, not a redesign (see subpackage layout).
- Index membership, any cross-vendor compositing, fundamentals point-
  tier upgrades.

## Evidence this plan is built on (inlined, not just referenced)

From the Phase 4D-A/A.2/A.3 investigation (proxy-observed, cross-checked
against public disclosure record):

1. **Cumulative reporting semantics (Gate 1): PASS.** `income`
   `report_type=1` (default) reports cumulative year-to-date figures;
   `report_type=2`/`7` report single-quarter figures. Verified by exact
   arithmetic: `000001.SZ` FY2022 cumulative Q3 total_revenue
   (138,265,000,000) minus cumulative H1 (92,022,000,000) equals
   46,243,000,000 — which is *exactly* the independently-tagged
   `report_type=2` single-quarter record for the same period. `n_income`
   reconciles the same way (36,659,000,000 − 22,088,000,000 =
   14,571,000,000, exact).

2. **Vintage/restatement capability: PASS, coverage: NOT CERTIFIED.**
   `600518.SH` (康美药业) FY2017: `report_type=1` (original,
   ann_date=2018-04-26): total_revenue=26,476,970,977.57,
   money_cap=34,151,434,208.68. `report_type=4` (restated,
   ann_date=2019-04-30): total_revenue=17,578,618,640.06,
   money_cap=4,207,124,387.23. Both externally corroborated to the exact
   digit against the real public correction announcement (money_cap
   341.51亿→42.07亿, revenue overstated 88.98亿). **But** `002069.SZ`
   (獐子岛) FY2017 returns only ONE row despite public record of a real
   2020 restatement — absence of a second vintage row must never be
   read as "no restatement occurred."

3. **Knowledge-date rule (certified via 002450.SZ 康得新/Kangde Xin
   fraud specimen):**
   `knowledge_date(row) = f_ann_date if ann_date != f_ann_date else
   ann_date`. Decisive evidence: `002450.SZ` FY2015 income has a
   `report_type=5` (pre-adjustment original) row with
   ann_date=f_ann_date=2016-04-22, total_revenue=7,459,380,000,
   n_income=+1,404,920,000 (profit) — and a `report_type=1` ("latest")
   row with the *same* ann_date=2016-04-22 but **f_ann_date=2021-02-28**,
   total_revenue=2,377,810,000, n_income=**-1,485,330,000** (loss). The
   `report_type=1` row's values were silently replaced by the 2021
   restatement while its `ann_date` was left stale — using `ann_date`
   here would attribute 2021-known numbers to 2016. **`ann_date` alone is
   never safe when it diverges from `f_ann_date`.** This is a
   vendor/proxy-observed rule specific to Tushare's own reprocessing
   mechanics, not a general "f_ann_date always means X" claim.

4. **New failure mode: blank-out.** `002450.SZ` FY2016/FY2017 income
   (`period=20161231`, `20171231`) return `total_revenue`/`n_income` as
   **empty strings**, not corrected numbers — some reprocessing events
   invalidate a field without supplying a replacement. Must surface as
   an explicit missing/invalid state, never silently absent or defaulted
   to 0/NaN indistinguishable from "never had data."

5. **CH3 (net profit excluding non-recurring items): no single clean
   field.** `fina_indicator.profit_dedt` has the right economic
   definition ("扣除非经常性损益后的净利润（扣非净利润）") but **no
   `report_type` at all** — it cannot preserve pre-adjustment vintages.
   `income.net_after_nr_lp_correct` ("...（更正前）", i.e. pre-correction
   value) has vintage tracking but was empty even in the 600518.SH
   restatement. Neither is a safe canonical field on its own (item 7).

6. **CH4 depth: PASS (feasibility only).** `daily.vol` +
   `daily_basic.total_share` joined for `000001.SZ` 2013-01→2014-03: 295
   rows, 251 usable, 3 distinct real `total_share` values in-window
   (512,335 → 819,736 → 952,075).

7. **Changing adj_factor: PASS.** `000001.SZ` 2013-06-20 ex-date
   (10-for-6 bonus + cash dividend): `total_share` steps 512,335 →
   819,736 (exactly ×1.6); `adj_factor` steps 36.173 → 58.387 (ratio
   1.614, consistent with the combined stock+cash adjustment).

8. **Proxy transport quirks (not Tushare's):** proxy enforces its own
   ≤366-day date-range cap (`date_range_too_large`, HTTP 400); transient
   `upstream_pool_exhausted`/`rate_limited` (503/429) on cold calls that
   succeed on retry with ~10-15s backoff; `adj_factor` specifically shows
   intermittent `code:0`-with-empty-`items` responses that succeed with
   real data on retry (retry-until-non-empty, not a data-quality
   distrust signal). A 3× immediate repeat of an identical `income`
   request returned byte-identical payloads — no conflicting non-empty
   payload was observed this investigation, but this must be actively
   checked, not assumed, wherever it matters (item 11).

9. **Identifier continuity (carried forward from the original Phase
   4D-A live spike, unchanged, NOT re-verified this round):** a real
   merger/restructuring specimen (`000024.SZ` → superficially resembling
   `001914.SZ`) turned out to be an unrelated entity on inspection.
   `IDENTIFIER CONTINUITY = NOT CERTIFIED` carries forward as the default
   status unless P4DB-2 produces new empirical evidence.

## Why no `pit/*` task is needed (frozen decision, read before objecting)

Phase 4B needed two narrow, explicitly-flagged reopenings of
`smart_beta/pit/*` (P4B-D1, P4B-R1) for reasons specific to that phase
(a determinism-check bug, a replay-transport ambiguity). Phase 4D-B does
**not** need any `pit/*` change, for a specific, verified reason:

- `PanelSchema.validate()` (`smart_beta/data/schema.py:70-88`) only
  requires that a DataFrame contain its declared key/dtype columns —
  extra columns pass through untouched. This is the exact mechanism
  Tiingo already relies on for its adapter-specific `is_zero_volume`
  column on `get_trading_status`'s output. The Tushare adapter uses the
  same mechanism: `reporting_basis`, `report_type`, `vintage_id`,
  `provenance_*`, and a coverage-certification flag are emitted as
  **extra columns** on `FUNDAMENTALS_FACT_SCHEMA`-conforming output —
  never a change to `FUNDAMENTALS_FACT_SCHEMA` itself (which would be a
  *required*-column change, breaking every existing Phase 3/4B/4C caller
  that already constructs schema-conforming rows without them).
- The cumulative-to-discrete subtraction that requirement 5 is protecting
  against is **not implemented in this phase at all** (deferred, see
  Scope). When it eventually is (a later phase, out of scope here), it
  is built entirely from two unmodified `pit.fundamentals.latest_known_
  value` calls at the *same* `as_of` — one for the field at
  `report_period_end=Q_n end`, one at `report_period_end=Q_(n-1) end` —
  because `latest_known_value` already groups by `(stock_id,
  report_period_end, field)` and already enforces `knowledge_date <=
  as_of` per group, independently, with no cross-group leakage possible.
  Tushare's own `period` parameter maps directly onto
  `report_period_end`, so nothing new is required in the shared PIT
  layer for this guarantee to hold — it already holds, by construction,
  given correctly-timestamped raw facts. **The adapter's only job is to
  emit those facts correctly; it must never itself subtract two periods
  or otherwise resolve "latest" anything.**

This is the concrete architectural instance of requirement 5's "vendor-
specific parsing in the adapter, temporal selection in the trusted PIT
layer" split, verified against the actual code rather than asserted.

## The composite-source architecture

```
proxy transport (TushareClient protocol; ProxyTushareClient impl)
        |
canonical raw-fact mapping (report_type/ann_date/f_ann_date parsing,
knowledge-date rule, reporting_basis tagging, blank-out normalization,
coverage-certification flagging -- all vendor-specific, all in the adapter)
        |
TushareAShareSource (implements PITDataSource fully, Tushare-only)
        |
Phase-3 compliance boundary (existing check_* functions, called with
Tushare's own real facts as parameters -- unmodified)
        |
PointInTimeView -> latest_known_value (unmodified) -> future CH3/CH4
factor code (NOT this phase)
```

## Frozen policies (binding on every task below)

1. **`total_mcap` is canonical for CH3/CH4 market-cap semantics.**
   `daily_basic.total_share × close` (or the vendor's own `total_mv`
   field, cross-checked against that product) maps to `total_mcap`.
   `float_share`/`circ_mv`/`free_share` map to `float_mcap` **as a named,
   documented, diagnostic-only quantity** — never treated as
   interchangeable with `total_mcap`, never silently substituted for it.
   Owned by P4DB-4.

2. **Vintage capability vs. coverage — never conflated.** The adapter
   MAY emit multiple vintages of a fact (real restatement specimens
   exist and are certifiable — `600518.SH`, `002450.SZ`). It must NEVER
   claim or imply, by omission or by a "not restated" default, that a
   fact with only one retrievable vintage was never restated.
   `is_restatement` is set `True` for the certifiable specimens where a
   vintage relationship is directly evidenced (via `report_type=4/5`
   pairing or an `ann_date != f_ann_date` marker on the same row) and
   `False` only where no such evidence exists — `False` here means "no
   evidence of restatement," never "verified original," exactly
   mirroring Tiingo's already-established distinction. P4DB-9's
   certification report must contain the literal lines `CHINA
   FUNDAMENTALS VINTAGE CAPABILITY = PASS` and `CHINA FUNDAMENTALS
   VINTAGE COVERAGE COMPLETENESS = NOT CERTIFIED`, and must name
   `002069.SZ` FY2017 as the standing counterexample. Owned by P4DB-6.

3. **Knowledge-date rule, Tushare-adapter-scoped, fail-closed on
   ambiguity.** `knowledge_date = f_ann_date` when `f_ann_date` is
   present and parses as a valid date; `knowledge_date = ann_date` only
   under the explicit, tested fallback below; otherwise the row is
   dropped from `get_fundamentals`'s output and counted in a
   `dropped_ambiguous_date_count` the adapter must expose for
   certification to report (never silently included with a guessed
   date). **Fallback policy (the only one authorized):**
   `f_ann_date` may be substituted with `ann_date` iff `f_ann_date` is
   missing/null AND `ann_date` is present AND
   `report_type in {1, 2, 3, 6, 7, 8}` (the "current-period, not-a-
   restated-comparative" codes per Tushare's own documented table —
   `report_type in {4, 5, 9, 10, 11, 12}`, the adjusted/pre-adjustment
   families, are never eligible for the fallback, since those are
   exactly the codes associated with reprocessing in the evidence
   above). This fallback must be unit-tested against a real specimen
   where it fires and a real specimen where it correctly refuses to
   fire (ambiguous/ineligible report_type). **This rule is an adapter-
   internal parsing policy for Tushare's specific field semantics as
   observed through this proxy — it must not be written or documented as
   a general claim about what `f_ann_date` means for any other vendor or
   for direct official-Tushare access.** Owned by P4DB-6.

4. **Vendor-specific parsing belongs in the adapter (this whole
   subpackage):** `report_type` interpretation, `ann_date`/`f_ann_date`
   parsing and the knowledge-date rule, empty-string/null
   normalization, restatement/vintage evidence extraction, raw
   provenance preservation. None of this leaks into `smart_beta/pit/*`.

5. **Temporal selection belongs in the trusted PIT layer, untouched by
   this phase.** `latest_known_value`, vintage selection, and any future
   cumulative-to-discrete transformation are explicitly out of scope
   here (see "Why no `pit/*` task is needed"). No adapter module may
   compute a discrete quarter value, and no adapter module may query
   "today's" latest row and treat it as a specific historical `as_of`
   answer.

6. **Distinct semantic states, never collapsed.** The adapter's
   fundamentals output distinguishes, as real, inspectable state (extra
   columns / a small result object, not silent `NaN`/absent-row
   conflation): a genuinely blanked-out value (`002450.SZ` FY2016/17
   pattern — `is_blank_out=True`, `value=NaN` is acceptable *only* when
   tagged this way, since `FUNDAMENTALS_FACT_SCHEMA.value` is typed
   `float` and cannot itself carry a sentinel); a row dropped for
   ambiguous knowledge-date (counted, not silently included — see
   policy 3); and incomplete vintage coverage (policy 2). `pit.
   fundamentals.latest_known_value`'s existing "absent group = no row"
   behavior already correctly represents "nothing knowable as of t" —
   that part needs no adapter-side change; the adapter's job is only to
   never manufacture a row that hides one of the three states above
   inside an ordinary-looking `False`/`0.0`/absent value.

7. **CH3 economic target: net profit excluding non-recurring gains/
   losses.** `profit_dedt` (`fina_indicator`) is the value source — it
   is the only place this exact fact is directly reported — but it is
   emitted as `field="ni_ex_nonrecurring"` **only** when it can be
   unambiguously joined to a single `income`/`balancesheet` vintage for
   the same `(stock_id, report_period_end)` (i.e., that period shows no
   evidence of reprocessing: no `report_type=4/5/9/10/11/12` row exists
   for it, and its own `income` `report_type=1` row has `ann_date ==
   f_ann_date`). When that join is ambiguous (reprocessing evidence
   exists for the period), the adapter must **not** emit a
   `ni_ex_nonrecurring` fact for that `(stock_id, report_period_end)` at
   all — silence, not a guessed vintage, exactly matching policy 6's
   "never manufacture a row." P4DB-9's certification report must contain
   the literal line `CH3 NI-EX-NONRECURRING = FIELD-LEVEL, VINTAGE-
   JOIN-DEPENDENT, NOT A SINGLE CANONICAL FIELD`. Owned by P4DB-6.

8. **Reporting basis, explicit, adapter-owned column.** Every
   fundamentals fact carries an extra `reporting_basis` column valued
   `"CUMULATIVE_YTD"` (income/cashflow `report_type` 1/4/5/9/10/11/12 —
   the cumulative family), `"SINGLE_QUARTER"` (`report_type` 2/3/7/8 —
   the single-quarter family), or `"INSTANT"` (balance-sheet fields,
   which are point-in-time stock quantities, not flow quantities, by
   accounting nature — confirmed by `balancesheet`'s own field
   semantics, e.g. `total_assets`). This is an adapter-owned extra
   column, not a `FUNDAMENTALS_FACT_SCHEMA` change (see "Why no `pit/*`
   task is needed"). Owned by P4DB-6.

9. **`FinancialFact` shape — realized as `FUNDAMENTALS_FACT_SCHEMA` +
   extra columns, not a new type.** Required (already-existing) columns:
   `stock_id`, `report_period_end`, `field`, `knowledge_date`, `value`,
   `is_restatement`. Adapter-owned extra columns: `reporting_basis`
   (policy 8), `report_type` (raw vendor code, audit-only), `vintage_id`
   (opaque, distinguishes rows sharing the same key when the schema's
   own key — `(stock_id, report_period_end, field, knowledge_date)` —
   is insufficient to disambiguate observed duplicate `update_flag`
   rows with identical `knowledge_date`; see evidence item 8's proxy-
   determinism note and the update_flag-duplicate pattern seen in every
   restatement specimen), `is_blank_out` (policy 6), `source_vendor`
   (`"Tushare"`, always, per the existing licensing-disposition
   attribution requirement), `access_path` (`"proxy:pcd.mobcvb.cn"` —
   never omitted, so a downstream consumer can always tell this was not
   direct official-API access), `retrieved_at`. Owned by P4DB-6, used by
   P4DB-8's assembly and P4DB-9's certification.

10. **Executable adversarial fixtures, built from the proven real
    specimens, not synthesized:**
    - `000001.SZ` FY2022 cumulative-vs-single-quarter reconciliation
      (evidence item 1) — owned by P4DB-6's fixture set, exercised by
      P4DB-9.
    - `600518.SH` FY2017 original-vs-restated vintage pair (evidence
      item 2) — P4DB-6, P4DB-9.
    - `002450.SZ` FY2015 stale-`ann_date`/later-`f_ann_date` in-place
      reprocessing (evidence item 3) — P4DB-6, P4DB-9.
    - `002450.SZ` FY2016/17 blank-out specimen (evidence item 4) —
      P4DB-6, P4DB-9.
    - The real `t1 < t2 < t3` cumulative-to-discrete look-ahead trap
      (600518.SH Q1/H1 2018, `t1`=2018-04-28, `t2`=2018-08-29,
      `t3`=2019-04-30) — this is a **consumer-side** trap
      (cumulative-to-discrete subtraction is out of scope for this
      phase per Scope), so P4DB-6 only needs to prove the three raw
      facts are each retrievable with correct `knowledge_date`s;
      P4DB-9's certification report documents the trap and confirms the
      adapter's raw facts alone (not a subtraction it performs) are
      sufficient for a *future* consumer to avoid it — this is a
      documentation/evidence item in P4DB-9, not new adapter code.
    - `000001.SZ` ≥250-day CH4 turnover feasibility (evidence item 6) —
      P4DB-4's raw-returns/market-cap fixtures, exercised by P4DB-9.
    - `000001.SZ` 2013-06-20 changing adj_factor corporate-action
      specimen (evidence item 7) — P4DB-5, P4DB-9.

11. **Proxy behavior is transport-specific, isolated to `proxy_client.
    py`.** Bounded retry policy (max attempts, exponential backoff,
    ceiling — mirrors the ~10-15s backoff that worked empirically),
    explicit empty-success handling (retry when `code:0` and `data.
    items` is empty, up to the retry ceiling, then surface as a
    distinguishable `TushareEmptyResponseError` rather than silently
    returning "no data"), explicit `429`/`503`/
    `upstream_pool_exhausted` handling, and a **canonical-consistency
    check**: `replay_transport`'s recorded-fixture mode must store
    every distinct response seen for a given identical request during
    fixture recording, and if two non-identical non-empty payloads were
    ever recorded for the same request, that must be surfaced as a
    loud, named error at fixture-build time (`TushareNonDeterministic
    ResponseError`), never silently resolved by picking one. No
    proxy-specific class or exception name may appear outside
    `proxy_client.py` — every other module talks only to the
    `TushareClient` protocol. Owned by P4DB-1.

12. **Proxy disposition, carried into the certification report
    verbatim:** `PROXY SUFFICIENT FOR PHASE 4D-B POC: YES`. `PROXY
    SUFFICIENT FOR PRODUCTION: NO` — P4DB-9's report must state this
    explicitly and must not be read as blocking Phase 4D-B itself
    (POC-grade access is exactly what this phase is scoped to use).

13. **Phase 4D-B is vendor-adapter-focused only.** No CH3/CH4 factor
    code, no engine/pipeline migration, no `smart_beta/factors/*` or
    `smart_beta/engines/*` change of any kind.

14. **No two tasks in the same wave own the same production or test
    file.** Verified per-task in the File ownership section of each
    `task-p4db-*.md` — see the Dependency DAG below for the wave
    assignment this constraint was checked against.

15. **Later-wave worktrees are not created before the preceding
    barrier's tasks are merged to `master` and the full suite is
    green.** This is a process rule for whoever runs the waves, not
    adapter code; stated here so it travels with the frozen plan.

## Subpackage layout

```
smart_beta/vendors/
    tushare/
        __init__.py                                                [P4DB-1]
        client.py            # TushareClient protocol, TushareAPIError    [P4DB-1]
        proxy_client.py      # ProxyTushareClient (all proxy-specific     [P4DB-1]
                              #   retry/backoff/empty-handling; replay_transport)
        identifiers.py       # resolve_stock_id (ts_code policy)          [P4DB-2]
        calendar_source.py   # build_china_a_share_calendar (SSE/SZSE)    [P4DB-3]
        market_data.py       # raw returns, market cap, trading status    [P4DB-4]
        corporate_actions.py # dividend/bonus -> CORPORATE_ACTIONS_SCHEMA [P4DB-5]
        fundamentals.py      # knowledge-date rule, vintage/report_type,  [P4DB-6]
                              #   reporting_basis, CH3 join, blank-out
        listing.py            # listing/delisting with corroboration      [P4DB-7]
        source.py              # TushareAShareSource(PITDataSource)         [P4DB-8]
tests/
    fixtures/tushare/<task-slug>/    # each task owns exactly one subdirectory
    test_tushare_{client,identifiers,calendar,market_data,
                  corporate_actions,fundamentals,listing,source,
                  certification}.py
docs/
    phase4d_b_tushare_certification.md                                [P4DB-9]
```

`smart_beta/vendors/__init__.py` is not touched — it is already a stable
marker file (Phase 4B), and this phase adds a sibling subpackage the same
way Phase 4B added `tiingo/` beside a hypothetical future SEC/EDGAR
adapter.

## Task table

| Task | Objective | Files | Depends on | Wave |
|---|---|---|---|---|
| P4DB-1 | `TushareClient` protocol + `ProxyTushareClient` transport, retry/backoff/empty-handling, fixture-replay | `vendors/tushare/{__init__,client,proxy_client}.py`, tests | none | 1 |
| P4DB-2 | `stock_id` policy via `ts_code`; identifier-continuity re-investigation | `vendors/tushare/identifiers.py`, tests | none | 1 |
| P4DB-3 | Authoritative SSE/SZSE trading calendar | `vendors/tushare/calendar_source.py`, tests | none | 1 |
| P4DB-4 | Raw returns + market cap (`total_mcap` canonical) + trading status | `vendors/tushare/market_data.py`, tests | P4DB-1, P4DB-2 | 2 |
| P4DB-5 | Corporate actions (dividend/bonus adjustment factors) | `vendors/tushare/corporate_actions.py`, tests | P4DB-1, P4DB-2 | 2 |
| P4DB-6 | Fundamentals: knowledge-date rule, vintage/report_type parsing, reporting_basis, CH3 join, blank-out | `vendors/tushare/fundamentals.py`, tests | P4DB-1, P4DB-2 | 2 |
| P4DB-7 | Listing/delisting with corroboration | `vendors/tushare/listing.py`, tests | P4DB-1, P4DB-2 | 2 |
| P4DB-8 | `TushareAShareSource` assembly | `vendors/tushare/source.py`, tests | P4DB-4, P4DB-5, P4DB-6, P4DB-7 | 3 |
| P4DB-9 | Certification (PASS/FAIL/NOT CERTIFIED) | `docs/phase4d_b_tushare_certification.md`, tests | P4DB-8 | 4 |

## Dependency DAG

```
Wave 1 (parallel)   P4DB-1 (client)   P4DB-2 (identifiers)   P4DB-3 (calendar)
Barrier 1 -- all of Wave 1 merged to master, full suite green
Wave 2 (parallel)   P4DB-4 (market data)   P4DB-5 (corp actions)   P4DB-6 (fundamentals)   P4DB-7 (listing)
                    (needs 1, 2)            (needs 1, 2)            (needs 1, 2)             (needs 1, 2)
Barrier 2 -- all of Wave 2 merged to master, full suite green
Wave 3 (standalone) P4DB-8 (assembly)   (needs 4, 5, 6, 7)
Barrier 3 -- P4DB-8 merged, full suite green
Wave 4 (standalone) P4DB-9 (certification)   (needs 8)
```

File-ownership check for requirement 14: within Wave 1, the three tasks
own `vendors/tushare/{__init__,client,proxy_client}.py` (P4DB-1),
`vendors/tushare/identifiers.py` (P4DB-2), and
`vendors/tushare/calendar_source.py` (P4DB-3) respectively — disjoint.
Within Wave 2, `market_data.py` (P4DB-4), `corporate_actions.py`
(P4DB-5), `fundamentals.py` (P4DB-6), and `listing.py` (P4DB-7) —
disjoint. Each task's test file and fixture subdirectory are named after
its own module and touched by no other task. No task in this phase
touches a file owned by Phase 3, Phase 4B, or Phase 4C.

## Certification criteria (binding on P4DB-9)

`docs/phase4d_b_tushare_certification.md` must, for each of the items
below, contain an explicit `PASS`, `FAIL`, or `NOT CERTIFIED` line (never
a silent omission), run against the real, recorded specimens in
requirement 10:

1. Schema conformance (`check_schema_conformance` against all seven
   `PITDataSource` methods).
2. `CUMULATIVE VS SINGLE-QUARTER RECONCILIATION` — using the `000001.SZ`
   FY2022 specimen, confirmed exactly as in evidence item 1.
3. `CHINA FUNDAMENTALS VINTAGE CAPABILITY` — PASS, using `600518.SH` and
   `002450.SZ`.
4. `CHINA FUNDAMENTALS VINTAGE COVERAGE COMPLETENESS` — NOT CERTIFIED,
   naming `002069.SZ` FY2017 explicitly as the counterexample.
5. `KNOWLEDGE-DATE RULE` — PASS as an adapter-internal parsing policy
   (policy 3), with both a firing and a non-firing fallback specimen
   tested.
6. `CH3 NI-EX-NONRECURRING` — the literal line from policy 7.
7. `MARKET CAP` — `total_mcap` PASS as canonical; float/circ/free
   explicitly documented as diagnostic-only, never certified as an
   independent float-adjusted figure (mirrors Tiingo's
   `FLOAT MARKET CAP = NOT CERTIFIED` precedent, adapted).
8. `CH4 TURNOVER FEASIBILITY` — PASS, mechanical only, explicitly not a
   PIT-immutability certification of historical `total_share`.
9. `CHANGING ADJ_FACTOR RECONSTRUCTION` — PASS, using the `000001.SZ`
   2013-06-20 specimen.
10. `IDENTIFIER CONTINUITY` — carries forward `NOT CERTIFIED` unless
    P4DB-2 produces new empirical evidence changing it; if unchanged,
    the certification report must say so explicitly, not omit it.
11. `PROXY DETERMINISM` — PASS/FAIL per the canonical-consistency check
    in policy 11; if `TushareNonDeterministicResponseError` ever fired
    during fixture recording for any certification specimen, the report
    must say `PROXY DETERMINISM = FAIL` and name the offending request.
12. `PROXY SUFFICIENT FOR PHASE 4D-B POC` / `PROXY SUFFICIENT FOR
    PRODUCTION` — the literal lines from policy 12.
13. `TUSHARE LICENSING/ATTRIBUTION` — every committed fixture carries
    `source_vendor=Tushare` and its retrieval provenance, per the
    existing licensing disposition (fixture reproducibility, no bulk
    redistribution) — P4DB-9 verifies this was followed by every prior
    task, not just its own fixtures.

## Deferred items (explicit, not silently dropped)

- CH3/CH4 factor implementation and migration.
- Cumulative-to-discrete subtraction (consumer-side, future phase).
- Official (non-proxy) Tushare API transport.
- Point-tier upgrade evaluation for the official account (still 120
  points, unchanged, per the original Phase 4D-A live spike).
- Index membership, cross-vendor compositing.
- Any change to `smart_beta/pit/*`, `smart_beta/data/*`,
  `smart_beta/vendors/tiingo/*`, or Phase 4C engine/pipeline code.
