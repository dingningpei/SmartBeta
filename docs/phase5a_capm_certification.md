# Phase 5A CAPM Pilot Certification (P5A-5)

Final Phase 5A gate. This document records what was **actually re-verified**
by re-loading the real artifacts and re-running the real checks that
P5A-1 through P5A-4 produced, and what remains **NOT CERTIFIED**. It is
the checked-in, human-readable half of the executable certification in
`tests/test_phase5a_certification.py`; every disposition below corresponds
to a real assertion in that file against the committed artifacts and
fixtures. The markdown-presence test at the bottom of that file is a
**synchronization guard only** — it asserts all 14 line-items are present
with an explicit disposition and that the frozen verbatim sentences and
the forbidden-terminology negative check hold; it proves nothing by
itself.

Baseline: `master` at P5A-4's completion commit
(`57cc411c57010d50b926ee5465c430f4d72aca22`, "P5A-4: complete bounded
Gate B CAPM scalability pilot"). This task edits **no production code**
and no P5A-1 through P5A-4 test file or fixture. It re-derives every
claim by reading the committed artifacts directly; it does **not** import
or re-run any prior task's own disposition as proof.

Executable authority: `tests/test_phase5a_certification.py`.

Evidence taxonomy (preserved, never conflated): `LIVE-RECORDED` /
`FIXTURE-REPLAYED` / `CONTRACT-MODELED` / `CONSTRUCTED` / `HAND-VERIFIED`
/ `NOT CERTIFIED`. A passing offline test is not live empirical
execution. Gate A PASS is not market-factor certification. Gate B PASS is
not representative-US-market certification. A successful risk-free
transformation is not exact Treasury-holding-return certification.

---

## Dispositions (completeness invariant: all 14 considered, none silent)

**[1] `GATE A DISPOSITION = PASS`**
`test_item01_gate_a_disposition_reverified` re-loads the real committed
Artifact A at `docs/phase5a/gate_a/artifact_a_market_factor.json` and
asserts the chain genuinely produced a non-trivial series: **64** trading
dates (2026-06-15 … 2026-09-15), **63** non-NaN `MKT` observations,
`universe_count = 3` on every date, and a strictly positive sample
standard deviation (`0.015077343400323668`) — not an all-`NaN` or empty
result. It also re-reads `GATE_A_DISPOSITION.json` and confirms
`disposition = RUN`, `gate_a_pass = true`, `gate_a_claim = REAL-DATA
END-TO-END EXECUTION`, artifacts `A/B/C = RUN`, and that no recorded
finding blocks the run (both findings are `retained_as_history = true`
and resolved). The real live-recorded Tiingo fixtures
(`tests/fixtures/tiingo/phase5a_gate_a/`, nine HTTP 200 bodies) and the
live-recorded FRED `DGS3MO` fixture (`tests/fixtures/risk_free/
treasury/`) back the run. **PASS**.

**[2] `GATE A CLAIM = REAL-DATA END-TO-END EXECUTION`**
`test_item02_gate_a_claim_verbatim` asserts the report contains the
canonical claim and the full, verbatim not-demonstrated list. The allowed
claim is exactly that the real-data chain executes end to end and
produces reproducible, inspectable `MKT` observations without silently
compensating for an upstream defect. This is explicitly **not** a
representative market factor. The frozen sentence, reproduced
byte-for-byte: *"Gate A does not demonstrate representative market
coverage, scalability, a US market factor, CAPM replication, economic
significance, or statistical significance."*

**[3] `GATE B DISPOSITION = PASS`**
`test_item03_gate_b_disposition_reverified` re-loads
`docs/phase5a/gate_b/GATE_B_DISPOSITION.json` and the real committed
Artifact A, confirming `disposition = RUN`, `gate_b_pass = true`,
`gate_b_claim = BOUNDED FIXED-UNIVERSE SCALABILITY`, a **26-name** frozen
universe (from a **30-candidate** dated snapshot, with the four real
plan-tier exclusions `GOOGL`, `AMZN`, `NVDA`, `SHW` named), **252**
trading dates (2025-09-15 … 2026-09-15), **251** non-NaN `MKT`
observations, and `universe_count = 18` on every date. The two earlier
P5A-4 live-access blocker reports (`P5A-4_BLOCKER_REPORT.md`,
`P5A-4_FIXTURE_RECORD_BLOCKER_REPORT.md`) are preserved as history, not
deleted.

*Reviewer obligation (stated plainly, not mechanically enforced).* The
reviewer inspected P5A-4's commit/diff structure for a genuine
freeze-before-computation ordering. **Finding:** P5A-4 landed as a single
squashed commit (`57cc411`) that contains the universe snapshot, the
fixture set, the FRED fixture, the artifacts, and the preserved blocker
reports together, so the *commit structure alone cannot mechanically
prove* that the freeze preceded computation. The ordering is instead
corroborated by the preserved, independently checkable evidence
sequence — `p5a4_entitlement_resume_v_wmt.log` (probe → 26/30 freeze),
`universe_snapshot.json` (written after the probes, `retrieved_at_utc
2026-09-19T18:32:53Z`), `p5a4_stage_capture_78.log` (78 HTTP-200 bodies
staged from the frozen list), the atomic promotion into
`tests/fixtures/tiingo/phase5a_gate_b/`, `p5a4_fred_acquire.log`, then
`p5a4_artifacts_run.log` — and by the recorder's own stage separation
(`--mode probe` / `--mode record` / `--mode artifacts`). This is a
review-time judgment, recorded here as such, not a grep result.
**PASS.**

**[4] `GATE B CLAIM = BOUNDED FIXED-UNIVERSE SCALABILITY`**
`test_item04_gate_b_claim_and_forbidden_terminology` asserts the claim is
exactly the frozen wording **and** runs a literal, executable
case-insensitive substring sweep over the whole report, all of P5A-4's
committed artifacts under `docs/phase5a/gate_b/`, the Gate B recorder,
and the Gate B test file. The sweep targets the four forbidden universe
descriptions frozen in `worker_tasks/phase5a/phase5a-plan.md` and
`task-p5a-4-gate-b-scalability.md` (they are deliberately **not**
reproduced here, so that this report cannot itself trip the negative
check) and must find none of them. The required terminology — *"a fixed
universe selected from a dated DJIA constituent snapshot"* — is present
in the universe snapshot, the disposition, and the recorder. This is not
a survivorship-safe historical index; no index-membership infrastructure
is built.

**[5] `HAND VERIFICATION = PASS`**
`test_item05_hand_verification_rerun` re-runs P5A-3's actual independent
reconstruction test (`tests/test_phase5a_hand_verification.py`) by
executing its test function against the real committed raw fixtures and
the single Artifact-A comparison value. It independently reconstructs the
**2026-06-16** `MKT` observation (all three Gate A names eligible; the
preceding trading date 2026-06-15; DGS3MO source date 2026-06-16, raw
yield 3.79, `delta_calendar_days = 1`) from the raw Tiingo and FRED
fixtures and matches P5A-2's committed value to a stated **relative
tolerance of `1e-9`**. The observed relative residual is ~`2.3e-14`.

*Reviewer obligation (stated plainly, not mechanically enforced).* The
reviewer read `tests/test_phase5a_hand_verification.py`'s source and
confirmed it reads only the raw fixture paths (`tests/fixtures/
tiingo/phase5a_gate_a/`, `tests/fixtures/risk_free/treasury/`) plus the
single `MKT` comparison value from P5A-2's Artifact A; it never imports
or calls `compute_market_excess_return` or any `smart_beta` production
module, and never reads P5A-2's Artifact B diagnostic evidence. This is a
stated review-time judgment, not a grep result. **PASS.**

**[6] `RISK-FREE TRANSFORMATION = Rf_t = (DGS3MO_source(t) / 100) * (delta_calendar_days(t) / 365)`**
`test_item06_risk_free_transformation_formula` asserts the report states
the frozen formula exactly and carries the canonical evidence-limitation
sentence verbatim, whitespace-normalized: *"the
short-maturity/simple-interest interpretation of DGS3MO is supported by
mutually consistent Treasury.gov, FRED, and academic documentation, but
the primary Treasury Yield Curve Methodology technical publication has
not been directly read in full."* `delta_calendar_days(t)` is the actual
calendar-day gap between equity trading date `t` and the immediately
preceding trading date in the same return panel (Monday→Tuesday = 1;
Friday→Monday = 3). `/252` compounding is explicitly rejected and is
never substituted. The staleness rule's scope is likewise stated
verbatim: it is *"a Phase 5A data-freshness convention, not a
Treasury-calendar certification."* This is a named Phase 5A
approximation, not exact Treasury-holding-return certification.

**[7] `RISK-FREE STALENESS/NO-FUTURE-LEAKAGE = PASS`**
`test_item07_risk_free_staleness_and_no_future_leakage_rerun` re-runs
P5A-1's own executable boundary tests from
`tests/test_risk_free_treasury.py`: exactly 3 business days stale is
accepted; 4 business days stale raises the named
`RiskFreeStalenessError`; a queried date with no published DGS3MO
observation at or before it raises the same named exception (an unbounded
gap is trivially beyond the threshold); the first date of any call is
unconditionally `NaN` with the staleness check never evaluated for it;
and the strictly-backward lookup never reads a future-dated observation
(structurally, via `searchsorted(..., side="right") - 1`). The staleness
count uses the existing, dependency-free
`TradingCalendar.from_weekdays_excluding_holidays` with no holiday
table, i.e. a plain Monday–Friday count, which does **not** claim to
equal the true count of Treasury-market closures. **PASS.**

**[8] `NEWEY-WEST CONVENTION = lag 6 (DEFAULT_SETTINGS.newey_west_lags)`**
`test_item08_newey_west_convention` asserts
`DEFAULT_SETTINGS.newey_west_lags == 6` and that the report carries the
required verbatim caveat: *"this is a frozen Phase 5A diagnostic
convention inherited from existing machinery, not a certification that
lag 6 is the statistically optimal HAC bandwidth for daily
observations."* The statistic is produced only by the existing,
unmodified `newey_west_ols` on non-NaN `MKT` rows, with a minimum of 20
observations (Gate A `n = 63`; Gate B `n = 251`); below 20 the summary
literally says `NOT RUN — insufficient observations`. **PASS.**

**[9] `UPSTREAM DEFECT AUDIT = NONE FOUND`**
`test_item09_upstream_defect_audit` performs a real cross-task audit of
every P5A-1 through P5A-4 report and machine-readable disposition for a
recorded **upstream defect in an already-trusted module**. The audit
finds **none**. The named findings that do exist are retained here, not
omitted:

* **GATE-A-1** — a persistent Tiingo entitlement mismatch (`GET
  /tiingo/fundamentals/GOOGL/daily` → HTTP 400 "DOW 30" plan-tier
  restriction), resolved by the reviewed GOOGL → JPM universe amendment.
  Classification: vendor-access finding, **not** a defect in a trusted
  module. Its original raw 400 body was later lost and is **not**
  reconstructed; it remains a *reported*, not a re-certifiable
  LIVE-RECORDED, finding.
* **GATE-A-2** — the bottom-market-cap screen degenerate at a 2–3-name
  universe, classified in `GATE_A_DISPOSITION.json` as a
  **`spec_configuration_defect`, not an implementation defect**;
  `_above_cap_cutoff` correctly implements a relative percentile screen
  for a broad cross-section. Resolved by the Gate-A-only settings
  override (`bottom_mcap_exclude_pct = 0.0`) with no change to any trusted
  module.
* **P5A-4 Gate B live-access blockers** — two preserved operational
  Tiingo hourly-allocation (`HTTP 429`) blocks, resolved on a bounded
  resume. Not defects of any kind.

No trusted module was modified to reach any Gate A/B result, and no
finding was silently compensated for. **NONE FOUND.**

**[10] `EVIDENCE TAXONOMY AUDIT = PASS`**
`test_item10_evidence_taxonomy_audit` confirms the report carries all six
taxonomy labels and that every Phase 5A fixture manifest declares its
real provenance: the Gate A and Gate B Tiingo manifests and the Gate A
and Gate B FRED manifests are all `live_recorded = true`
(`LIVE-RECORDED`); the reconstructed offline artifacts are
`FIXTURE-REPLAYED`; the P5A-3 arithmetic check is `HAND-VERIFIED`; the
`<20`-observation and exclusion-naming branches are labeled `CONSTRUCTED`
in the P5A-2 test file. It also confirms no artifact's own claim exceeds
its gate boundary: the two preserved non-Gate-A runs are explicitly
labeled (`partial_run_entitlement_limited_not_gate_a/README.md` →
"NOT Gate A evidence"; `gate_a_2_compromised_default_settings/README.md`
→ "NOT certified Gate A output"), and the Gate A README/PROVENANCE carry
the canonical not-demonstrated sentence. No Gate A/B artifact content
claims a representative market factor.

**[11] `CREDENTIAL LEAKAGE = PASS`**
`test_item11_credential_leakage_sweep` runs a real regex sweep, mirroring
P4DB-9's token-pattern sweep exactly —
`(?i)(api[_-]?key|token)\s*[:=]\s*[A-Za-z0-9]{16,}` — across every file
P5A-1 through P5A-4 added (the full `0d15655..HEAD` diff: 150 files),
plus this report and this test file. It finds **zero** credential-shaped
strings. As a stronger guard it also asserts that none of the current
`TIINGO_API_KEY` / `TUSHARE_API_TOKEN` / `TUSHARE_PROXY_TOKEN` values
appears anywhere in that diff (the values are never logged or printed).
Keys are read from the environment only, never written into any fixture
or manifest. **PASS.**

**[12] `FULL REGRESSION = PASS`**
`test_item12_full_regression` runs the complete existing test suite
**fresh** in a subprocess on the fully integrated Phase 5A branch state,
excluding only this certification file (which the outer invocation is
already running), with the live-key environment variables removed so the
one pre-existing key-gated live certification test skips rather than
touching the network. Result: **1203 passed, 1 skipped, 0 failed**. A
separate full run with the key present also passed (**1204 passed**) but
would have exercised the pre-existing key-gated live test, so the
deterministic offline figure is the one certified here. No Phase 0–4D-B
test regresses. **PASS.**

**[13] `RF FORMULA INDEPENDENCE = PASS`**
`test_item13_rf_formula_independence` independently constructs
`TreasuryBillRiskFreeProvider` (and loads P5A-1's committed FRED fixture)
and compares production output against expected values computed **here,
in this file, with plain Python arithmetic on literals** — never copied
from, or re-derived by calling, P5A-1's own test file or the provider
under test:

* Specimen A: annualized yield 5.00%, `delta_calendar_days = 1` →
  `0.05 * 1 / 365 = 0.0001369863013698630…`
* Specimen B: annualized yield 5.00%, `delta_calendar_days = 3` →
  `0.05 * 3 / 365 = 0.0004109589041095890…`
* Real committed-fixture specimen: equity 2026-06-15 → 2026-06-16,
  source 2026-06-16, raw yield 3.79, `delta_calendar_days = 1` →
  `3.79 / 100 * 1 / 365`.

The required negative guard asserts production output does **not** match
the `/252`-compounded value for both specimens (`(1.05)**(1/252) - 1`
etc.) by more than `1e-6`, so a substituted `/252` convention would fail
loudly. This gives a second, differently-authored check that would fail
even if P5A-1's own test were tautological. **PASS.**

**[14] `CAPM SINGLE-AUTHORITY AUDIT = PASS`**
`test_item14_capm_single_authority_audit` runs a literal source-grep of
`smart_beta/pipelines/capm_pilot.py` confirming it contains **no**
reference to any `smart_beta.benchmarks.capm` private symbol
(`_market_factor`, `_value_weighted_returns`, `_value_weighted_by`,
`_load_pit_panel`) and imports only the public
`compute_market_excess_return` from that module. It then re-runs the
`derived_market_return - risk_free_return == MKT` invariant against the
**real committed** Gate A and Gate B Artifact A outputs (the sanctioned
decomposition `market_return = MKT + risk_free_return`) within the frozen
`1e-9` relative tolerance. The invariant is true by construction; the
check exists to catch a future refactor that breaks the
single-authority decomposition, not to prove it. **PASS.**

---

## Final Phase 5A barrier (17 points, evaluated explicitly)

**[B1] Real Tiingo observations, live-recorded with provenance = PASS** —
`tests/fixtures/tiingo/phase5a_gate_a/` and `.../phase5a_gate_b/` manifests
carry `live_recorded: true`, retrieval timestamps, endpoints/params, and
`api_key_stored: false`.

**[B2] Real DGS3MO observations, live-recorded with provenance = PASS** —
`tests/fixtures/risk_free/treasury/` and `.../treasury_gate_b/` manifests
carry the FRED URL, `series_id = DGS3MO`, retrieval timestamps, and the
raw-CSV SHA-256.

**[B3] Gate A executes end to end = PASS** — item [1]; 63 non-NaN `MKT`.

**[B4] At least one Gate A `MKT` observation independently hand-verified
within a stated tolerance = PASS** — item [5]; 2026-06-16, rel tol `1e-9`.

**[B5] Deterministic offline fixture replay (zero live network calls in
pytest) = PASS** — the P5A test modules install autouse tripwires on
`urllib.request.urlopen` (and the Gate B recorder tests additionally
tripwire `time.sleep`); item [12]'s fresh run is deterministic offline.

**[B6] No PIT/data-ownership violation found anywhere in the new code =
PASS** — `smart_beta/research_inputs/risk_free_treasury.py` is statically
independent of the PIT bitemporal machinery (`pit.view`, `pit.schema`,
`pit.fundamentals`) and of `vendors/tiingo/*`; it imports only the
dependency-free `pit.calendar.TradingCalendar`;
`smart_beta/pipelines/capm_pilot.py` derives the RF date grid only through
the trusted, pure `get_realized_returns` wrapper and fails closed on
mismatch.

**[B7] No upstream defect silently compensated for = PASS** — item [9];
findings are named and retained, none patched around.

**[B8] Gate B has an explicit disposition = PASS** — item [3]; `RUN`.

**[B9] Gate B's claim remains exactly "bounded fixed-universe
scalability" = PASS** — item [4].

**[B10] Machine-readable outputs exist for every passing gate = PASS** —
Gate A `docs/phase5a/gate_a/artifact_a_market_factor.json`,
`artifact_b_*_diagnostics.json`, `artifact_c_statistical_summary.json`,
`GATE_A_DISPOSITION.json`; Gate B the same set plus `.csv` and
`universe_snapshot.json`. (Note, re-checked rather than trusted: the Gate
A disposition/README also list `.csv` Artifact A/B files, but only the
JSON variants are committed for Gate A; the repository `.gitignore`
excludes `*.csv`, and Gate B's `.csv` files were force-added. The
machine-readable requirement is met by the committed JSON. Recorded here
as an artifact-existence inconsistency, not a methodology defect.)

**[B11] Diagnostic evidence is sufficient to reconstruct every
calculation step by hand = PASS** — items [5] and [13]; Artifact B
carries per-name `adj_ret`, raw lagged `total_mcap`, observation/lag
source dates and named tradability exclusions, plus the RF provider's
retained per-observation provenance.

**[B12] Statistical summaries follow the frozen diagnostic convention =
PASS** — item [8]; lag 6, ≥20 observations, explicit non-optimality
caveat.

**[B13] The report preserves every evidence distinction without
conflation = PASS** — item [10]; the six taxonomy labels are used
explicitly and the preserved non-Gate-A runs are labeled.

**[B14] Full existing regression suite passes = PASS** — item [12];
1203 passed, 1 skipped (key-gated), 0 failed.

**[B15] No credentials or secrets committed anywhere in the diff = PASS**
— item [11].

**[B16] Every NOT CERTIFIED boundary remains explicit = PASS** — see the
"NOT CERTIFIED" section below; nothing in this report upgrades a claim.

**[B17] `capm_pilot.py` source contains no `capm.py` private-helper call;
`authoritative_MKT` is unmodified; the `derived_market_return` invariant
passes; the RF-provider date grid is proven (not assumed) identical to
the panel grid; the staleness rule is the frozen convention = PASS** —
item [14], the P5A-2 spy test, the fail-closed
`RiskFreeDateGridMismatchError`, and item [7].

---

## Reviewer obligations (D and E remain reviewer-enforced, not
mechanically enforced)

Stated plainly — these are **not** implied to be mechanically proven:

* **Item [5]:** the reviewer read `tests/test_phase5a_hand_verification.py`'s
  own source and confirmed it reads only raw fixture paths plus the single
  `MKT` comparison value from P5A-2's Artifact A — never P5A-2's Artifact B
  diagnostic evidence. A stated review-time judgment, not a grep result.
* **Item [3]:** the reviewer inspected P5A-4's commit/diff structure and
  completion-report narrative for consistency with a genuine
  freeze-before-computation ordering. As recorded in item [3], the single
  squashed commit cannot mechanically prove the ordering; it is
  corroborated by the preserved evidence logs and the recorder's stage
  separation. A stated review-time judgment, not a mechanical proof.

## Explicitly NOT CERTIFIED (preserved, never silently dropped)

* Gate A does **not** demonstrate representative market coverage,
  scalability, a US market factor, CAPM replication, economic
  significance, or statistical significance.
* Gate B does **not** demonstrate representative US market coverage,
  survivorship-safe historical membership, publication-grade CAPM
  replication, or investment performance. It is bounded fixed-universe
  scalability only, over *"a fixed universe selected from a dated DJIA
  constituent snapshot."*
* The `DGS3MO` transformation is a named Phase 5A approximation, **not**
  exact Treasury-holding-return certification.
* The staleness rule is *"a Phase 5A data-freshness convention, not a
  Treasury-calendar certification"*; it does not exclude Treasury-only
  weekday holidays.
* The Newey-West lag-6 convention is a diagnostic convention inherited
  from existing machinery, **not** a statistically optimal HAC bandwidth.
* GATE-A-1 remains a *reported*, not a re-certifiable LIVE-RECORDED,
  finding (its original raw HTTP 400 body was lost).
* Gate B's freeze-before-computation ordering is not mechanically proven
  from commit structure (single squashed commit); it is corroborated by
  preserved evidence logs.
* The Gate A `.csv` artifacts listed in the Gate A disposition/README are
  not committed (only the JSON variants are); the machine-readable
  requirement is met by the JSON.

## Status summary

* Gate A: `RUN` — claim `REAL-DATA END-TO-END EXECUTION`.
* Gate B: `RUN` — claim `BOUNDED FIXED-UNIVERSE SCALABILITY`.
* Hand verification: `PASS` (2026-06-16, rel tol `1e-9`).
* RF formula independence: `PASS`; `/252` negative guard `PASS`.
* CAPM single-authority audit: `PASS`.
* Full regression: `PASS` (1203 passed, 1 skipped, 0 failed offline).
* Credential leakage: `PASS` (zero hits).
* Upstream defects: **NONE FOUND** (findings named, none patched around).
* No production code modified by P5A-5.
