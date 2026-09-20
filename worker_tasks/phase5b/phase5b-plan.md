# Phase 5B: Real-Data China CH3/CH4 (Wave 0 — FROZEN)

**STATUS: FROZEN (Wave 0 methodology/architecture only).** This document
is the binding record of every Phase 5B Wave 0 methodology/architecture
decision, resolved across several independent-review passes (including a
user-supplied independent check of Liu–Stambaugh–Yuan (2019) against an
earlier, incorrect draft). Freezing this document does **not** authorize a
worktree, a branch, a Pi worker, or Wave 1 — those each require their own
separate, explicit authorization. Items explicitly marked `NOT CERTIFIED`
within this document remain open evidence questions; they do not block the
freeze of the surrounding contract they are attached to, and must not be
silently upgraded without new evidence designed to resolve them.

Baseline: `master` at `phase5a-complete` =
`bae0131260d5e9622e094c13cca249cafe8ce7e1`. Phase 5A is sealed: no file
that was part of the Phase 5A merge set (see that tag's annotation) may be
modified by any Phase 5B task except where explicitly and narrowly
authorized below (see P5B-3's `capm.py`/`ch3.py` amendments, which are
additive-only and must not change any Phase 5A certified value).

## Scope

Migrate CH3/CH4 off the legacy, pre-Phase-3 `_load_panel`/synthetic-only
path onto the trusted PIT/`research_inputs` boundary, using the real
`TushareAShareSource`, and produce a small, hand-verified, POC-grade
real-data pilot — mirroring Phase 5A's Gate A discipline, not a
production/scalability claim.

**Deferred, not built here:** any China market-representative universe,
any statistical-significance or profitability claim, any transaction-cost
analysis, CH4 if its turnover dependency (P5B-2) is not empirically usable
by Wave 4a's barrier, any upgrade of a Phase 4D-B `NOT CERTIFIED` item.

---

## B1 — China domestic risk-free rate (RESOLVED — contract frozen, day-count/transformation explicitly NOT CERTIFIED pending one bounded check)

**Correction (user-supplied, from an independent check of Liu–Stambaugh–
Yuan (2019), *JFE* 134(1), 48–69): the LSY-exact risk-free instrument is
the PBOC (China) benchmark one-year deposit rate.** This supersedes the
earlier SHIBOR-3M-as-primary proposal. SHIBOR 3M is **demoted to a
documented, possible future MODERN-SUBSTITUTE robustness variant only** —
it must never be used, described, or defaulted to as the Phase 5B CH3/CH4
risk-free definition.

**Economic definition (frozen target):** the PBOC-published benchmark
one-year RMB deposit interest rate (人民币一年期存款基准利率), converted
into a daily factor-period rate.

**Recollection, moderate confidence, requires the same bounded check named
below before being treated as fact:** PBOC has not changed this benchmark
rate since its 2015-10-24 adjustment (held at 1.50% throughout the
post-2015 period). If confirmed, this materially *simplifies* — not
complicates — the provider's temporal-alignment burden for any pilot
window chosen in the post-2015 period: the rate is a genuine step function
with very few historical change dates, all individually enumerable and
independently cross-checkable against PBOC's own public announcement
history, rather than a daily-quoted series requiring an ongoing feed. It
must still be modeled as a real, dated, piecewise-constant series (never a
bare hardcoded constant), so a pilot window that happens to span an actual
rate-change date, or a pre-2015 window, is handled correctly rather than by
accident.

**Provider shape (reuses P5A-1's proven pattern, not its data):** same
formula shape as the frozen P5A-1 transformation
(`rf_t = (rate(t)/100) * (delta_calendar_days(t)/365)`), where `rate(t)` is
looked up as the most recent PBOC-announced rate whose `effective_date <=
t` (a piecewise-constant step lookup, not a daily quote lookup) — same
`RiskFreeProvider` contract, same interface-option-(a) explicit
`trading_dates` construction, same first-observation-NaN-before-staleness
precedent, evaluated against the **China SSE/SZSE calendar**, not the US
calendar. "Staleness" in P5A-1's gap-tolerance sense does not apply the
same way to an administratively-set rate; the only fail-closed case needed
is "no PBOC announcement covers this date at all" (before the earliest
recorded effective date) → NaN, mirroring P5A-1's first-observation
precedent rather than inventing a new rule.
**Whether LSY convert the annual deposit rate to a daily/period rate by
simple pro-rating (the formula above) or by compounding is NOT yet
confirmed** — flagged as part of the same bounded check below, since it
changes the exact numeric `rf_t`, not just its provenance.

**Knowledge-date semantics:** PBOC announcements have their own public
announcement date and effective date (sometimes, historically, a few days
apart) — both must be modeled explicitly (`knowledge_date` =
announcement date, the rate applies to equity dates on/after
`effective_date`); this distinction is **unverified this pass** and is
part of the bounded check.

**Source / provenance — the ECONOMIC-DEFINITION vs. EXACT-SOURCE
distinction (binding on this project going forward, see `CLAUDE.md` §8):**
the exact CSMAR/WRDS database row LSY's own paper pulled is not accessible
to this project. The proposed target source is **PBOC's own official
published announcement history** (the actual rate-setting authority's own
record) — this is **ECONOMIC-DEFINITION REPLICATION** (the same real-world
quantity, independently sourced from its origin) and must be reported as
exactly that, never as **EXACT-SOURCE REPLICATION** (the literal
CSMAR/WRDS feed), which is not supported and must not be claimed. Given how
few distinct rate-change events exist, a small, individually-cited table of
`(announcement_date, effective_date, rate)` rows built directly from PBOC's
own public announcements (a `CONSTRUCTED`-category fixture in this
project's existing evidence taxonomy, cross-checked against at least one
independent public secondary source before being trusted) is a viable,
low-complexity capture strategy — much lower-risk than a live daily-feed
integration would have been for SHIBOR.

**Contingency, named now rather than discovered later:** if PBOC's own
site cannot be reliably read/cited, or Tushare's proxy turns out to be the
only practical source for this series, the RF leg would then inherit the
same Tushare-proxy `NOT CERTIFIED` dispositions as the equity leg — must be
stated explicitly if it happens, not silently absorbed.

**Exact bounded check required before freeze (specified, not executed this
pass — no live calls were made):** ONE bounded documentation read
confirming (i) PBOC's benchmark one-year deposit rate change history
(dates, rates, and the announcement-vs-effective-date distinction) from
PBOC's own published record or an authoritative secondary source; (ii)
whether LSY pro-rate or compound the annual rate into their model's return
period; and (iii), bundled here because it is the same source check, LSY's
E/P re-formation cadence and cumulative-YTD-vs-TTM earnings treatment (see
the E/P section below). This is a specification of P5B-1's required first
action, not a report of having performed it.

**Provider contract (RESOLVED — frozen now, not deferred to Wave 1):**

- **Rate applicable to each date:** the piecewise-constant step lookup
  defined above (`rate(t)` = the PBOC-announced rate whose `effective_date`
  is the greatest one `<= t`).
- **Announcement/effective-date semantics:** `knowledge_date` =
  announcement date; the rate is applied to equity dates on/after
  `effective_date`. A well-formed announcement always has
  `effective_date >= announcement_date` (a policy change cannot take
  effect before it is announced); the provider asserts this on load rather
  than assuming it silently.
- **PIT availability rule:** an equity date `t` may use `rate(t)` only when
  some recorded announcement has both `effective_date <= t` **and**
  `announcement_date <= t` (the second condition is implied by the first
  given the well-formedness assertion above, but is stated as an explicit
  contract clause, not left implicit).
- **Transformation (annual quoted rate -> daily factor return):**
  **PROPOSED DEFAULT, explicitly NOT CERTIFIED, not silently assumed —**
  simple pro-rating, `rf_t = (rate(t)/100) * (delta_calendar_days(t)/365)`,
  the same shape as P5A-1's frozen formula. Per instruction: if the bounded
  check below cannot establish whether LSY use simple pro-rating or
  compounding, or the exact domestic day-count convention, **that one item
  is marked NOT CERTIFIED and the pilot proceeds with this stated default**
  — the provider contract as a whole is not held hostage to it.
- **Day-count convention:** Actual/365, matching P5A-1's convention, used
  as the same not-silently-assumed default above — **NOT CERTIFIED**
  against SHIBOR-style money-market Actual/360 or any China-domestic
  deposit-rate-specific convention until the bounded check confirms one way
  or the other.
- **Missing/staleness behavior:** first-observation NaN takes precedence
  (no announcement covers a date before the earliest recorded one -> NaN),
  mirroring P5A-1's frozen rule; no other "staleness" concept applies to a
  step-function administratively-set rate.
- **Provenance:** PBOC's own official announcement history — **ECONOMIC-
  DEFINITION REPLICATION**, explicitly not EXACT-SOURCE (CSMAR/WRDS)
  replication (see the source/provenance paragraph above and `CLAUDE.md`
  §8).

**Remains NOT CERTIFIED (named explicitly, not blocking the contract
above):** the exact pro-rating-vs-compounding transformation LSY use; the
exact domestic day-count convention (Actual/365 assumed as a stated,
flagged default); genuine economic equivalence of an administratively-set
deposit rate to a market riskless rate (never claimed — the same caveat
every `RiskFreeProvider` implementation already carries); PBOC-source-vs-
CSMAR/WRDS-source equivalence beyond the economic-definition level named
above.

**Remaining uncertainty for Wave 1:** the bounded check (PBOC's own
rate-change history, and confirmation or refutation of the day-
count/transformation default above) is still outstanding — but per the
resolution above, it no longer blocks freezing this section's *contract*;
it only determines whether the day-count/transformation item stays flagged
NOT CERTIFIED or gets upgraded to a confirmed convention.

**Exact frozen dispositions, stated plainly:**
`CHINA RF SOURCE = RESOLVED` — PBOC's own official historical benchmark
deposit-rate tables / rate-adjustment announcements are treated as
sufficient authoritative provenance for this project's economic-definition
series (ECONOMIC-DEFINITION REPLICATION; EXACT-SOURCE/CSMAR-WRDS
replication is explicitly not claimed and not required).
`RF DAILY TRANSFORMATION CONVENTION = NOT CERTIFIED` — implemented as
simple pro-rating on an Actual/365 basis (a stated, provider-owned,
configurable convention), pending the bounded check above; this is a
documented implementation convention, never presented as a fact about
LSY's own paper.

---

## B2 — Turnover capability

**Proposed architecture: additive, Tushare-adapter-local extra columns —
no `PITDataSource` ABC change, no Tiingo impact.**

1. `TushareAShareSource.get_raw_returns` gains a new **blessed** (i.e.
   documented, non-underscore, intended for research consumption — unlike
   the existing underscore provenance columns) extra column, `vol`: the
   vendor's own raw `daily.vol`, native units, never adjusted, never
   filled. This is not a `PIT_RAW_RETURN_PANEL_SCHEMA` *required* column
   (so Tiingo's implementation of the same ABC method is untouched and no
   existing conformance test can break), exactly mirroring how
   `total_mcap`/`float_mcap` are blessed-but-not-required extras on
   `PIT_MARKET_CAP_SCHEMA`.
2. `TushareAShareSource.get_market_cap` promotes its existing
   `_total_share` diagnostic to a new blessed `total_share` extra column
   (kept alongside, not replacing, the still-underscore `_float_share`,
   `_close`, `_total_mcap_sanity_ratio`). This is the one diagnostic
   Phase 4D-B item 8 already mechanically proved usable
   ("CH4 TURNOVER FEASIBILITY = PASS (mechanical only)"); promoting only
   this one column, not the others, keeps the blessing narrow and
   evidence-backed rather than a blanket upgrade.
3. Two new, narrowly-named `research_inputs.inputs` functions (mirroring
   `get_realized_returns`/`get_capitalization_weights`'s one-function-one-
   consequential-choice style — **not** folded into
   `get_capitalization_weights`, whose own docstring is explicit that it
   never emits anything beyond `total_mcap`):
   - `get_raw_trading_volume(view, start, end) -> (date, stock_id, vol)`
   - `get_shares_outstanding(view, start, end) -> (date, stock_id, total_share)`,
     with the same "raises if the source doesn't carry this column, never
     silently substitutes `float_share`" discipline
     `get_capitalization_weights` already uses for `total_mcap`.

**Turnover definition (frozen shape, proposed):**

```
turnover_t = vol_t / total_share_t
```

using **total** shares, mirroring the same total-vs-float choice already
frozen for market-cap weighting (policy 1) — not float/circulating shares,
which remain diagnostic-only per that same policy. This is a documented
approximation of "true" free-float turnover, stated as such, never silently
presented as float-turnover.

**Abnormal turnover — RETRACTED AND CORRECTED (user-supplied, independent
check of LSY (2019)).** The previous proposal to reuse
`ch4.py::_add_turnover_proxy`'s shape (lag one period, subtract a trailing
6-**calendar-month** mean) is **wrong** and must not be implemented for
the real PIT-native path; that existing function is **not** called by
`ch4_pit.py`. The published characteristic is a **ratio of two rolling
trading-day means**, not a difference of a lagged value and a trailing
mean:

```
one_month_abnormal_turnover_t =
    mean(daily_turnover over the most recent 20 trading days ending at t)
    / mean(daily_turnover over the most recent 250 trading days ending at t)
```

**Frozen contract (new logic, owned by `ch4_pit.py`, not a generic
trusted-layer primitive — same rationale as the Monthly Formation
Contract's own narrow ownership):**

- **Window unit:** trading days (from the source's own `trading_calendar()`),
  not calendar days or calendar months — a genuine unit change from the
  legacy placeholder's month-based window.
- **Window endpoints:** both windows end **at `F_m` inclusive** — the short
  window is the 20 trading days `[F_m - 19, F_m]`, the long window is the
  250 trading days `[F_m - 249, F_m]`, both in trading-day-count terms.
  Including `F_m` itself is not a knowledge-date violation: turnover
  (`vol`/`total_share`) is same-day-knowable market data, exactly like
  market cap.
- **Lagging:** none beyond the window definition itself — the ratio
  computed as of `F_m` **is** the characteristic used for month `m+1`'s
  formation (no additional one-period shift on top of it), then broadcast
  and held fixed through month `m+1` via the same formation-date-keyed
  merge every other characteristic uses.
- **Minimum-observation requirement:**
  `FULL-250-OBSERVATION REQUIREMENT = PROJECT FAIL-CLOSED SUFFICIENCY
  POLICY, not a claimed LSY rule.` LSY's own paper establishes the 20/250
  characteristic *definition*, not (so far as this pass has determined) an
  explicit missing-data/insufficient-history policy — those are two
  separate things, and this plan freezes only its **own** sufficiency
  policy: a stock without a **full** 250 trading days of turnover history
  as of `F_m` gets `NaN` for this characteristic (excluded from that
  month's turnover-based (PMO) sort only — it remains eligible for CH3's
  own size/E-P sort). No partial-window average is computed. This default
  is configurable at the benchmark level (the two new `Settings` fields
  below double as the window lengths a future minimum-observation rule
  would key off), does not block Wave 0 freeze, and may be revisited if
  the bounded methodology check ever surfaces an explicit published
  minimum — without needing to reopen the 20/250 characteristic definition
  itself.
- **Formation-date cutoff:** exactly `F_m` — no observation with a date
  after `F_m` may enter either rolling window, by construction (the window
  is defined to end at `F_m`).
- **Settings:** two new, additive `Settings` fields —
  `ch4_turnover_short_window_days: int = 20` and
  `ch4_turnover_long_window_days: int = 250` — added to
  `smart_beta/config/settings.py` (new fields only; the existing
  `turnover_abnormal_window_months` stays untouched, still owned by the
  legacy placeholder `ch4.py` alone).

**PMO direction:** unchanged — `low_leg − high_leg` (low abnormal turnover
= pessimistic = long leg). The economics still hold under the ratio
definition: a ratio below 1 means recent turnover is depressed relative to
its trailing year (pessimism), a ratio above 1 means recent turnover is
elevated (optimism) — the same long-low/short-high direction as before.

**Remains NOT CERTIFIED:** `total_share`'s PIT-immutability (Phase 4D-B
item 8 already found it steps mid-window — mechanical feasibility only,
never a PIT-immutability certification); any claim that
`vol/total_share` is a validated free-float turnover measure.

---

## B3 — PIT-native CH3/CH4 architecture

**Proposed design:** additive parameterization of the existing
column-agnostic-capable helpers, plus two new modules — no change to
`smart_beta/pit/*` or `smart_beta/research_inputs/*` beyond B2's additions.

1. `capm.py::_add_mcap_scope` and `capm.py::_add_cross_sectional_groups`
   gain new optional `lag_col`/`scope_col` parameters defaulting to the
   existing legacy constants (`_LAG_COL`, `_SCOPE_COL`) — **zero behavior
   change** for legacy `ch3.py`/`ch4.py` and their existing test file
   (`test_benchmarks_ch.py`), which call them with no new arguments.
2. `ch3.py::_add_ch3_size_groups` gains the same additive
   `lag_col`/`scope_col` pass-through.
3. Two new files, `smart_beta/benchmarks/ch3_pit.py` and
   `smart_beta/benchmarks/ch4_pit.py` (module-per-benchmark, mirroring the
   existing `ch3.py`/`ch4.py` split), each exposing an explicitly
   `_pit`-suffixed public function (`compute_ch3_factors_pit`,
   `compute_ch4_factors_pit`) — the suffix is deliberate so a caller can
   never confuse the legacy-synthetic-only and PIT-native entry points,
   mirroring this codebase's existing "a consequential choice gets its own
   name" discipline (`adj_ret` vs `ret`, `total_mcap` vs `float_mcap`).
4. A new `_load_ch_pit_panel` helper (owned by `ch3_pit.py`, imported by
   `ch4_pit.py`) that implements the **Monthly Formation Contract** below —
   it does **not** reuse `capm.py::_load_pit_panel`/`_fundamentals_panel`'s
   single-global-cutoff, exact-date-match pattern (see that section for why
   this pattern is unsuitable here). It composes `get_realized_returns` +
   `get_capitalization_weights` + `get_tradability` + `get_fundamentals`
   (called once per formation date, not once for the window) + (CH4 only)
   B2's two new functions, resolves each characteristic at each formation
   date `F_m`, and broadcasts the resulting values/group labels onto every
   trading day of the following holding month via a formation-date-keyed
   merge. It then calls the now-parameterized `_add_mcap_scope`/
   `_add_cross_sectional_groups`/`_add_ch3_size_groups` with the PIT column
   names. `_value_weighted_by`/`_market_factor`/`_two_by_three`/`_spread`
   are reused **completely unmodified**, called with explicit
   `ret_col="adj_ret"`, `weight_col="total_mcap_lag"`. **Correction (see
   the Shell Screen section): `_market_factor` must be called on the
   scope-filtered (top-70%, post-shell-screen) subset of the panel, not the
   full eligible-universe panel** — unlike `capm.py::compute_market_excess_
   return`'s own US CAPM call (which correctly uses the full universe,
   since US CAPM has no shell screen at all). This is a genuine, CH3/CH4-
   specific deviation from the CAPM call pattern, not a copy of it. No
   change to `smart_beta/data/align.py` is needed — the earlier proposal to
   add a monthly-resampling primitive there is retracted (see the Monthly
   Formation Contract section).

**Hard constraint, binding on whoever implements this:** `capm.py` is not
file-frozen the way `docs/phase5a_*`/the `phase5a-complete` tag are, but it
is *behaviorally* sealed for Phase 5A's certified call path
(`compute_market_excess_return` is literally Phase 5A's `MKT` authority).
Any edit to `capm.py` under this task must be proven non-breaking by
**re-running the full Phase 5A certification suite
(`tests/test_phase5a_certification.py`,
`tests/test_capm_pilot_gate_a.py`, `tests/test_capm_pilot_gate_b.py`) and
confirming byte-identical numeric output**, not merely "still green."
A green-but-numerically-different result is a hard-stop finding, not a
pass.

**Remaining uncertainty:** the exact new PIT scope-column name and whether
`_load_ch_pit_panel` needs its own module or can live inside `ch3_pit.py`
alone with `ch4_pit.py` importing from it — a naming/layout detail to
settle at task-spec time, not a methodology question.

---

## Published CH3 definition (frozen target)

Liu–Stambaugh–Yuan (2019, *JFE*, "Size and Value in China"):

- **Shell screen:** exclude the bottom 30% of the tradability-eligible
  universe by lagged market cap — **before** MKT, SMB, VMG, or PMO are
  constructed. The resulting top-70% subset is the **CH factor universe**,
  and it is the sole universe every factor is built from (corrected — see
  the shell-vs-tradability resolution below; MKT is **not** built from the
  larger unscreened eligible universe).
- **Size split:** median of the CH factor universe → `small`/`big`.
- **Value characteristic:** earnings-to-price, `E/P = ni_ex_nonrecurring /
  total_mcap` (real E/P, not the current `book_value/mcap` placeholder) —
  see the E/P resolution below for the exact numerator semantics.
- **2×3 sort:** size (2) × E/P tercile (3), value-weighted within cell.
- **SMB:** `small − big`, averaged across the three E/P legs.
- **VMG:** `high E/P − low E/P`, averaged across the two size legs.
- **MKT:** value-weighted return of the **CH factor universe (the same
  shell-screened top 70%, not the larger tradability-eligible universe)**
  minus the **domestic** (China) risk-free rate (B1).
- **Weighting:** lagged `total_mcap` (never `float_mcap`).
- **Lag:** one formation-period lag on every characteristic (market cap,
  E/P) — see formation/rebalance below for what "one period" means.

## Published CH4 definition (frozen target)

CH3 plus:

- **Turnover characteristic:** `daily_turnover_t = vol_t / total_share_t`
  (B2).
- **Abnormal turnover (corrected — see B2's full contract):**
  `mean(daily_turnover, most recent 20 trading days ending at F_m) /
  mean(daily_turnover, most recent 250 trading days ending at F_m)` — a
  ratio of two rolling trading-day means, **not** a lagged-value-minus-
  trailing-mean difference (that shape, inherited unmodified from the
  legacy placeholder, was proposed earlier in this plan and has been
  retracted as incorrect).
- **Sort:** tercile on abnormal turnover, on the same CH factor universe
  (shell-screened top 70%) as SMB/VMG.
- **PMO:** `low − high` abnormal-turnover leg, value-weighted.
- Everything else identical to CH3 (same MKT, SMB, VMG construction).

---

## Monthly Formation Contract (RESOLVED — supersedes the earlier
"monthly-return-compounding primitive" proposal, which was wrong)

**Factor output remains a DAILY series, exactly like Gate A/B.** No
monthly-return compounding of underlying stock returns is performed
anywhere. What changes from CAPM's design is not the return frequency —
it is that portfolio **membership and weights are fixed once per month, at
formation, and held constant across every daily row of the following
month** — the standard Fama-French / Ken-French-daily-factor / LSY
convention. This directly answers the "is a generic monthly-compounding
primitive actually required" question: **no.** The earlier proposal to add
a `resample_daily_to_monthly_returns` primitive in `data/align.py` is
**retracted**; nothing resamples daily returns into monthly returns
anywhere in this design.

**Exact contract:**

- **Daily adjusted-return input:** `get_realized_returns`'s existing daily
  `adj_ret` panel, used as-is, never resampled or compounded.
- **Trading calendar / month membership:** a calendar month `m` is the set
  of trading dates (from the source's own `trading_calendar()`) whose
  calendar month is `m`. The **formation date** `F_m` is the last trading
  date in month `m`.
- **Characteristic observation cutoff:**
  - market cap and turnover (same-day knowable market data): the value
    observed **exactly on `F_m`** — no lag needed at this step, since
    "lagged relative to the return it explains" is already satisfied by
    `F_m` preceding the entire holding month `m+1`.
  - E/P (`ni_ex_nonrecurring`, knowledge-date gated): resolved via the
    **unmodified** `pit.fundamentals.latest_known_value`, called with
    **`as_of = F_m` specifically, once per formation date** — not once
    globally for the whole pilot window. See the hard requirement below;
    this is the one genuinely new piece of logic this contract needs.
- **Knowledge-date cutoff:** exactly `F_m` for every fundamentals value
  used in month `m`'s formation. Because `latest_known_value`'s own
  (unmodified, already-tested) contract guarantees `knowledge_date +
  buffer <= as_of`, calling it with `as_of = F_m` **structurally proves**
  no fact with `knowledge_date > F_m` can enter month `m+1`'s portfolio —
  provided (hard requirement, below) the resolution is genuinely called
  once per `F_m`, never reused across months.
- **Formation date:** `F_m`, as defined above.
- **Lagged-market-cap / portfolio-weight date:** both the sort variable and
  the value-weight are `total_mcap` observed on `F_m`, **held fixed** (not
  re-observed) for every trading day of month `m+1`.
- **Holding period:** every trading day from the first trading day after
  `F_m` through `F_{m+1}` (the last trading day of month `m+1`).
- **Missing daily observations:** unchanged from existing behavior — a
  stock with no `adj_ret` on a given holding-month day contributes nothing
  to that day's cross-section (drop, never zero-fill), via the existing,
  unmodified `_value_weighted_returns`/`_value_weighted_by`/
  `group_return_stats` machinery.
- **Suspensions inside the holding month:** a stock suspended (or otherwise
  failing the non-size tradability check) on one specific holding-month day
  is excluded from **that day's** cross-section only; its formation-date
  group assignment and fixed weight are unaffected for the month's other
  days. Must be verified by an explicit executable test (a mid-month
  single-day suspension removes exactly that day's contribution and no
  other), not assumed from reading the code.
- **Tradable-on-formation-date requirement:** **yes, required.** A stock
  must pass the non-size tradability check **at `F_m` itself** to be
  eligible for month `m+1`'s formation at all — sort membership is frozen
  at formation; a stock that is merely suspended on `F_m` but tradable
  again mid-month is still excluded for the whole month (prevents a
  same-day artifact from corrupting the month's group assignment).
- **Partial first month:** if the pilot window's `start` truncates a
  calendar month such that no full prior month-end exists inside the
  window, that first partial month has **no formation date** and is
  excluded from factor output entirely — no synthetic/boundary formation
  date is invented.
- **Partial last month:** if `end` truncates a holding month whose
  formation already occurred inside the window, the factor output includes
  only the actual trading days inside `[start, end]` for that month — real,
  correct daily values, but an incomplete day count, which must be reported
  as a diagnostic (e.g. `days_in_holding_month` vs. `days_observed`), never
  silently treated as a full month.
- **Listing/delisting inside a month:** a stock listing mid-holding-month
  was not tradable at the preceding `F_m` and is therefore correctly absent
  from that month's formation (no special-casing needed; picked up at the
  next formation date it qualifies for). A stock delisting mid-holding-month
  was a formation-date member and simply stops contributing on/after its
  last tradable day, per the suspensions rule above — must be verified that
  diagnostics attribute this to a listing-status exclusion, not a silent
  zero/NaN.
- **Output shape:** one row per **trading day** (daily `MKT`/`SMB`/`VMG`/
  `PMO`), using formation-fixed group/weight assignments — never one row
  per month.

**Hard requirement (the actual new logic this contract needs):** do
**not** reuse `capm.py::_fundamentals_panel`'s existing pattern as-is for
this purpose. That pattern resolves `latest_known_value` **once, globally,
at the whole window's `end`**, and merges onto the daily panel by **exact
date equality** against `report_period_end` — it does not forward-fill a
characteristic across a holding period. Reused naively, it would leak
later-window knowledge backward into earlier formation dates and would
leave `ep_proxy` non-NaN on at most one day per fiscal period. CH3/CH4's
real loader must instead: (1) compute the set of formation dates `{F_m}`
for the requested window; (2) for each `F_m`, call `latest_known_value`
independently with `as_of = F_m`; (3) take whatever `report_period_end`
that resolves to for each stock (which will usually be several months
**before** `F_m` — that is correct, not a bug); (4) broadcast that
resolved value, plus the `F_m`-observed market cap/turnover and their
resulting group labels, onto every trading day in month `m+1` via a
formation-date-keyed merge (not `lag_panel`, which shifts by row position,
not by calendar month). This mapping/broadcast mechanism is new, but it is
narrow (calendar bucketing + a keyed merge), owned inside `ch3_pit.py`
itself — **not** promoted to a shared trusted-layer primitive, since it is
specific to LSY-style fixed-monthly-formation construction, not a general
PIT need.

**Side finding, out of scope for Phase 5B, flagged for awareness only:**
reading `_fundamentals_panel` for this review surfaced that the
**already-migrated** `ff3.py`/`ff5.py` use exactly the single-global-`end`,
exact-date-match pattern described above. Whether that is an existing gap
in already-shipped Phase 4C code, or is fine for reasons not fully explored
in this pass, was not determined here and is **not** a Phase 5B task —
noted so it is not silently rediscovered later, and so nobody assumes
CH3/CH4 can simply copy that pattern.

---

## Shell screen vs. tradability screen — RESOLVED (corrected: MKT is also shell-screened)

**Frozen architecture (single pipeline, one CH factor universe feeding
every factor):**

```
raw/PIT universe
  -> non-size tradability eligibility   (ChinaAShareTradabilityPolicy,
                                          bottom_mcap_exclude_pct = 0.0)
  -> eligible universe
  -> bottom-30% lagged-market-cap shell exclusion  (_add_mcap_scope,
                                          the ONE AND ONLY size-based cut)
  -> CH factor universe (top 70%)
  -> MKT, SMB, VMG, PMO all constructed from this one universe
```

For the Phase 5B pilot, `ChinaAShareTradabilityPolicy` is configured with
`bottom_mcap_exclude_pct = 0.0` and therefore owns **only** the non-size
checks: suspension, limit-up/limit-down, ST-flag, listing-age,
not-yet-delisted. It performs **no** market-cap-based exclusion. The
bottom-30% shell exclusion is the **sole** market-cap-based cut in the
whole pipeline, applied once, in factor construction, never in
tradability, and never applied twice.

This is the same numeric resolution Gate A's GATE-A-2 already used
(`dataclasses.replace(DEFAULT_SETTINGS, bottom_mcap_exclude_pct=0.0)` at
the orchestration call site, no change to `tradability.py` itself), reused
here for a different, now-explicit reason: preventing an accidental
compounding of the same cut at two layers, not (as in Gate A) working
around a degenerate small-n sample.

**Correction (user-supplied, from an independent check of LSY (2019)):
MKT is built from the SAME shell-screened top-70% CH factor universe as
SMB/VMG/PMO — not from the larger, unscreened tradability-eligible
universe.** The earlier draft of this section (and the existing legacy
`ch3.py` module docstring, which states "MKT is the value-weighted return
of the full universe... the 30% shell screen applies to the SMB/VMG
portfolio sorts") is **incorrect** per this independent check and must
**not** be reproduced in the PIT-native path. `ch3_pit.py`/`ch4_pit.py`
must compute `_market_factor` **on the scope-filtered (top-70%) subset of
the panel**, not on the full eligible-universe panel — a real, mechanical
implementation difference from how today's legacy `ch3.py`/`ch4.py` call
`_market_factor(panel)` unfiltered. (The existing legacy synthetic-only
`ch3.py`/`ch4.py` and their test file are untouched by this correction —
this affects only the new PIT-native modules and this plan's own earlier,
now-superseded description.)

**Required diagnostics (binding on P5B-3/P5B-6):** the constituent
diagnostics frame must report the two exclusion layers **separately**, not
as one merged reason — e.g. `exclusion_reason` values distinguishing
`tradability:<suspended|limit|st|listing_age|delisted>` from
`shell_screen:bottom_30pct`, so a reviewer can always tell which layer
excluded a given `(date, stock_id)` and confirm no double-application
occurred.

---

## E/P + `ni_ex_nonrecurring` vintage semantics

- **Numerator:** `ni_ex_nonrecurring`, exactly as gated by Phase 4D-B
  policy 7's three-condition positive-vintage-identity test — silence
  (`get_uncertain_observations`, `reason="ch3_vintage_join_not_certified"`),
  never a guess, on failure. No change to that gate.
- **Denominator (published definition, restated exactly):**
  `previous-month-end close x total shares outstanding` — i.e. previous-
  month-end (`F_m`) total market capitalization. `total_mcap` (Tushare's
  own `daily_basic.total_mv`) is the **proposed practical implementation**
  of this quantity, but per instruction it may be used **only after** an
  explicit executable check confirms semantic equivalence, not by
  assumption: Phase 4D-B item 7 already established a
  `_total_mcap_sanity_ratio` (`total_mv / (total_share * close)`,
  "normally ~1.0") as a diagnostic, but "normally" is not "always" — P5B-3
  (or P5B-6's pilot) must assert this ratio holds within a stated tolerance
  (proposed: within 1%) on every observation actually used by the pilot,
  and **report, not silently accept**, any observation where it does not.
  If it fails to hold for some observations, the fallback (`close x
  total_share`, computed directly from a blessed `close`/`total_share`)
  is not currently available — `get_market_cap`'s `_close` remains
  underscore/diagnostic-only (B2 blessed only `total_share`, not `_close`)
  — so a genuine divergence would be a new, real blocker to surface, not
  quietly worked around.
- **Period: RETRACTED "annual filings only" — corrected per user-supplied
  independent check of LSY (2019).** LSY state that when financial-
  statement information is used to sort stocks, the sort at each month-end
  `F_m` uses **the firm's financial report having the most recent public
  release (knowledge) date on or before `F_m`** — quarterly since 2002,
  semiannual before. This is **exactly** what the Monthly Formation
  Contract's per-formation-date `latest_known_value(as_of=F_m)` call
  already produces with **no additional filtering by `report_period_end`
  type** — the earlier "restrict to annual `report_period_end`s only"
  proposal is retracted as an unnecessary, convenience-driven restriction
  that would have deviated from the published rule, not implemented it.
  Whatever `report_period_end` the most-recent-known report happens to
  carry (annual, semiannual, or quarterly) is used as-is.
- **Data-semantics issue, exposed rather than silently resolved (per
  explicit instruction — do not change the published economic definition
  to work around this):** Tushare's `ni_ex_nonrecurring`/`profit_dedt`, for
  any interim (non-annual) `report_type=1` filing, is a **cumulative-
  year-to-date** figure (Phase 4D-B policy 8's own `reporting_basis =
  "CUMULATIVE_YTD"` tag for the report_type family 1/4/5/9/10/11/12), not a
  discrete-quarter or trailing-twelve-month (TTM) figure. Whether LSY's own
  published E/P uses this raw cumulative-YTD-as-filed value directly, or
  applies some TTM/annualization adjustment on top of it, is **not
  determined by this pass** and is bundled into the same bounded
  methodology check named in B1. Until that check resolves it, the Phase
  5B pilot implementation must use the raw cumulative-YTD-as-filed value
  **as reported**, invent no TTM adjustment on its own, and the resulting
  certification must explicitly flag the mechanical within-fiscal-year
  seasonality this produces (cumulative NI grows through the year and
  resets each January) as a **named, open data-semantics limitation** —
  never smoothed over or silently presented as a clean TTM E/P.
  All existing positive-vintage-identity, revision, and suppression rules
  (Phase 4D-B policy 7's three conditions) are preserved unchanged and
  already work at any `report_type=1` cadence, not only fiscal year-ends —
  no new gate logic is required for this correction.
- **Vintage/knowledge-date selection:** unchanged, `latest_known_value` at
  the panel's formation cutoff — no new resolution logic.
- **Missing/suppressed observations:** already handled mechanically by
  `_fundamentals_panel`'s existing "field absent → NaN column" fallback and
  the existing NaN-passthrough in `_assign_groups` — no new code required.
  The resulting empirical suppression **rate** must be measured on the
  pilot universe and reported, never assumed low.
- **Never certified merely by field presence:** `profit_dedt`/
  `ni_ex_nonrecurring` existing in a payload is not PIT certification;
  `(stock_id, report_period_end)` alone is insufficient; positive vintage
  identity (Phase 4D-B's three conditions) remains required for every
  period this pilot uses.

---

## Coverage error vs. intentional suppression — kept structurally distinct

- **Vendor retrieval/coverage failure** (`FundamentalsCoverageError` from
  `research_inputs/fundamentals_coverage.py`): a *requested-range
  sub-interval* failed to retrieve at all. Must keep firing exactly as
  today; CH3 must never pass `allow_partial=True` merely to dodge it.
- **Intentional CH3 suppression** (`get_uncertain_observations`,
  `reason="ch3_vintage_join_not_certified"`): a specific field, for a
  specific `(stock_id, report_period_end)`, was retrieved successfully but
  the adapter declined to emit a value. Not a retrieval gap.

**Proposed executable check (owned by the pilot task, P5B-6):** on the real
pilot universe, assert (a) `get_fundamentals(..., allow_partial=False)`
does **not** raise merely because some periods have a suppressed
`ni_ex_nonrecurring` (coverage orchestration operates at retrieval-interval
granularity, never per-field-suppression granularity — must be confirmed
against real suppressed specimens, not just read from source); and (b)
every suppressed `(stock_id, report_period_end)` absent from
`get_fundamentals`'s facts has a corresponding `get_uncertain_observations`
row with `reason="ch3_vintage_join_not_certified"` — absence is never
silent.

The four Phase 4D-B semantic states (`KnownMissing`/blank-out,
`UnknownAsOf`, `Ambiguous/UncertifiedKnowledgeDate`, `Incomplete/
NotCertifiedVintageCoverage`) remain distinct from CH3's own suppression
state and from each other; none may be collapsed into another.

---

## Pilot universe

Proposed procedure (mirrors Gate A):

- **Size:** 3-5 real, currently-listed, large-cap A-shares.
- **Preferred names:** reuse Phase 4D-B's already-characterized specimens
  where possible — `000001.SZ` (平安银行) in particular, already the
  subject of items 1, 3, 7, 8, 9, 11 in `docs/phase4d_b_tushare_
  certification.md`, reducing the chance of a fresh surprise.
- **Exchange coverage:** at least one `.SH` and one `.SZ` name, to exercise
  the SSE/SZSE calendar on both exchanges.
- **History requirement:** listed through the entire pilot window plus
  enough lookback for a full 250-trading-day turnover history (B2's
  abnormal-turnover long window) and at least one full fiscal-report cycle
  for E/P.
- **Exclusion rule:** no name whose *only* fiscal year inside the pilot
  window is known (from Phase 4D-B's own findings) to raise an unresolved
  `TushareConflictingVintageError` — the pilot needs at least one fully
  clean specimen to hand-verify against, even if other names deliberately
  exercise the suppression/restatement paths.
- **Frozen-universe evidence:** one bounded entitlement probe per name,
  covering every endpoint P5B-1/2/3 need (fundamentals, market cap, the new
  `vol`/`total_share` extras); the China risk-free leg (B1) is a single
  national series independent of the pilot universe's names and is probed
  separately, once, against PBOC's own published source.
- **Representativeness:** explicitly not claimed. Canonical sentence,
  proposed: *"The Phase 5B pilot does not demonstrate representative China
  A-share market coverage, scalability, a China market factor, CH3/CH4
  replication, economic significance, or statistical significance."*

---

## Rate-limit-aware capture

Reuse P5A-4's proven pattern (deterministic per-`(ts_code, endpoint,
date-range)` request identities, staging ledger, resume-only-missing,
no retry/sleep loops, canonical-consistency hash/conflict detection,
atomic promote-only-when-complete, raw evidence preserved even when
invalid, credentials never committed) — but **do not modify
`scripts/fetch_phase5a_gate_b_fixtures.py` itself**, since it is part of
Phase 5A's sealed merge set. Proposed: extract the reusable pattern into a
**new**, independent shared module (e.g. `scripts/staged_capture.py`),
written fresh using the Phase 5A script only as a read reference, not by
editing it. A future, separately-authorized task could migrate Phase 5A's
own script onto the shared module, but that is explicitly out of Phase 5B's
scope.

---

## Hand verification (mirrors P5A-3)

At least one real CH3 factor-date reconstructed entirely by hand from raw
committed evidence, never trusting the production factor path: eligible
(tradability) universe, shell screen, lagged market caps, E/P values, size
split, value groups, per-cell value-weighted returns, SMB, VMG, MKT,
domestic RF. CH4/PMO hand verification is added only once P5B-2's turnover
data is confirmed usable at pilot scale (real, distinct, sane values on the
pilot universe) — an explicit go/no-go gate, not an assumption.

---

## Certification boundaries

**Inherited from Phase 4D-B, none upgraded:**
`CHINA FUNDAMENTALS VINTAGE COVERAGE COMPLETENESS = NOT CERTIFIED`,
`IDENTIFIER CONTINUITY = NOT CERTIFIED`, `PROXY DETERMINISM = NOT
CERTIFIED`, `PROXY SUFFICIENT FOR PRODUCTION = NO`, `CH3 NI-EX-
NONRECURRING = FIELD-LEVEL, VINTAGE-JOIN-DEPENDENT, NOT A SINGLE CANONICAL
FIELD`.

**New Phase-5B-specific dispositions to produce:**
`CHINA RISK-FREE RATE PROVENANCE` (PBOC-official-source-as-economic-
definition-replication vs. Tushare proxy — likely a *better* disposition
than the equity leg's, but must be independently stated, not assumed;
must also state ECONOMIC-DEFINITION REPLICATION, not EXACT-SOURCE
REPLICATION, per the B1 resolution); `TURNOVER DATA CERTIFICATION`
(mechanical availability vs. PIT-immutability, mirroring item 8's own
"feasibility only" framing); `SHELL-SCREEN / TRADABILITY-SCREEN
INTERACTION` (the resolution above, stated explicitly so it is never
"discovered" later as a suspicious duplication); `REAL E/P COVERAGE RATE`
(the measured suppression rate); `CH3/CH4 PILOT SCOPE` (POC-grade only,
same disclaimer shape as Gate A's); every applicable Phase 4D-B `PROXY
SUFFICIENT FOR ... POC/PRODUCTION` line, restated for the equity leg.

A successful pilot must never be read as: production-ready; China-market
representative; historical-index representative; survivorship-safe beyond
what the PIT system actually establishes; proof of factor profitability;
proof of statistical significance; transaction-cost robust; or
out-of-sample validated.

---

## External benchmark data (future validation only)

LSY (and the broader China-factor literature following them) are
understood to publish official CH3/CH4 monthly and daily factor series
(this recollection has not been independently verified this pass). This is
recorded here **only** as a possible **future external sanity check**,
never as a substitute for anything this phase actually builds: it must
not replace this project's own PIT construction, raw-data evidence, hand
verification, or vendor-semantics certification. If such a series is ever
used, **numerical equality with our own output must not be required or
expected** unless universe, dates, source data, and construction semantics
are first independently demonstrated equivalent — a mismatch under
different universe/window/vendor conditions is not, by itself, evidence
our construction is wrong, and a match is not, by itself, evidence it is
right. This is a possible future task, not part of Phase 5B's current DAG.

---

## Project objective boundary (explicit Phase 5B principle)

**Phase 5B validates the China CH3/CH4 benchmark capability of the
generic factor-research system it is built on top of. It is not an
exact-paper-replication project.** Liu–Stambaugh–Yuan (2019) defines the
benchmark *economic methodology* this phase targets — the reference for
*what* CH3/CH4 are — but is not a mandate to reproduce every incidental
choice of the original paper's own data pipeline. The engineering objective this whole codebase is
building toward (per this project's own long-term vision) remains a
generic, PIT-safe, vendor-independent, extensible factor-research
architecture. Concretely, this means:

- Paper-specific methodology choices (the shell-screen threshold, the
  E/P earnings definition, the turnover-ratio windows, the risk-free
  instrument) belong in **benchmark-level configuration and contracts**
  (e.g. `Settings` fields, the frozen formulas documented above) — never
  hardcoded into generic trusted-layer primitives (`smart_beta/pit/*`,
  `smart_beta/research_inputs/*`) unless a genuinely cross-benchmark need
  independently justifies promoting something to that layer (as, e.g.,
  `latest_known_value` already is, because every benchmark needs
  knowledge-date resolution, not because CH3 specifically needs it).
- Every new primitive proposed in this plan (the formation-date/holding-
  month broadcast mechanism, the turnover-ratio window logic) was
  deliberately kept narrow and owned inside `ch3_pit.py`/`ch4_pit.py`
  rather than promoted to a shared module, precisely because it is
  LSY-specific construction logic, not a generic PIT need — consistent
  with this principle, not an oversight to revisit later.
- A future benchmark that needs a *different* published methodology (a
  different shell threshold, a different value characteristic, a
  different sentiment proxy) should be able to reuse `research_inputs.*`
  and the generic sort/value-weight helpers in `capm.py` **without**
  inheriting any of CH3/CH4's own paper-specific constants or window
  choices — this plan's additive-parameterization approach (new optional
  kwargs with defaults, new narrowly-scoped modules) is chosen specifically
  to keep that door open.

---

## Proposed task DAG

Revised from the original planning-review hypothesis: split into more,
smaller waves once B1-B3's real scope (new blessed columns + two new
`research_inputs` functions + the Monthly Formation Contract's
formation-date/holding-month mapping and per-formation-date fundamentals
resolution + two new PIT-native benchmark modules) became concrete, and
because
`ch3_pit.py`/`ch4_pit.py` need P5B-1/P5B-2's **merged** interfaces (not
just a name contract) to import against — mirroring how Phase 4D-B's Wave
2 needed Wave 1 merged first, not just designed.

**Wave 1 (parallel, no cross-file dependency):**
- **P5B-1** China domestic risk-free provider. Owns:
  `smart_beta/research_inputs/risk_free_china.py` (new),
  `tests/test_risk_free_china.py` (new),
  `tests/fixtures/phase5b/china_rf/` (new dir),
  a live-fetch script (new, name TBD). Forbidden: any file under
  `smart_beta/vendors/tushare/`, any Phase 5A file.
- **P5B-2** Turnover capability. Owns:
  `smart_beta/vendors/tushare/market_data.py` (modify, additive),
  `smart_beta/research_inputs/inputs.py` (modify, additive),
  `tests/test_tushare_market_data.py` (extend),
  `tests/test_research_inputs.py` (extend). Forbidden: `capm.py`, `ch3.py`,
  `ch4.py`, anything under `smart_beta/pit/`.

*Barrier 1 — both merged, full suite green (including Phase 5A's own
certification suite, to catch any accidental collision), no ambiguity left
in B1/B2's frozen shape.*

**Wave 2 (standalone — needs Wave 1 merged for real imports):**
- **P5B-3** PIT-native CH3/CH4 architecture, implementing the Monthly
  Formation Contract and the abnormal-turnover ratio contract. Owns:
  `smart_beta/benchmarks/capm.py` (modify, additive-only),
  `smart_beta/benchmarks/ch3.py` (modify, additive-only),
  `smart_beta/config/settings.py` (modify, additive-only — new
  `ch4_turnover_short_window_days`/`ch4_turnover_long_window_days` fields,
  existing `turnover_abnormal_window_months` untouched),
  `smart_beta/benchmarks/ch3_pit.py` (new — includes the formation-date/
  holding-month mapping and per-formation-date fundamentals resolution;
  no monthly-return-compounding logic, and no change to
  `smart_beta/data/align.py`, which this task does not touch),
  `smart_beta/benchmarks/ch4_pit.py` (new), corresponding new test files.
  **Hard requirement:** `tests/test_phase5a_certification.py`,
  `test_capm_pilot_gate_a.py`, `test_capm_pilot_gate_b.py` must be re-run
  and shown numerically unchanged, not just green.

*Barrier 2 — merged, full suite green, Phase 5A numeric outputs confirmed
byte-identical.*

**Wave 3 (parallel, needs Wave 2 merged):**
- **P5B-4** Pilot universe selection + bounded entitlement probe. Owns:
  `docs/phase5b/pilot_universe/` (new), probe script (new). No production
  code.
- **P5B-5** Generalized staged/resumable capture tooling. Owns:
  `scripts/staged_capture.py` (new, independent — does **not** modify the
  Phase 5A script). Forbidden: any file under Phase 5A's merged commit
  set.

*Barrier 3 — universe frozen with evidence, capture tooling exists and is
tested (against synthetic/replay fixtures, not yet live).*

**Wave 4a (standalone, needs Wave 3 merged):**
- **P5B-6** Real CH3 pilot + hand verification. Owns:
  `smart_beta/pipelines/ch3_pilot.py` (new),
  `docs/phase5b/ch3_gate_a/` (new), corresponding tests. Uses P5B-5's
  tooling to record fixtures against P5B-4's frozen universe.

*Barrier 4a — hand-verified, E/P suppression rate measured and reported,
CH4-readiness go/no-go decided from this pilot's own turnover diagnostics.*

**Wave 4b (standalone, conditional on Barrier 4a's go decision):**
- **P5B-7** Real CH4 pilot + hand verification (PMO). Owns:
  `smart_beta/pipelines/ch4_pilot.py` (new),
  `docs/phase5b/ch4_gate_a/` (new).

*Barrier 4b (or explicit "CH4 deferred" disposition if the go/no-go was
no).*

**Wave 5 (standalone, needs everything before it):**
- **P5B-8** Certification. Owns: `docs/phase5b_ch3_ch4_certification.md`
  (new), `tests/test_phase5b_certification.py` (new).

*Final barrier — Phase 5B certification report complete, every inherited
and new NOT CERTIFIED item stated, no upgrade attempted.*

No file-ownership collision was found across Waves 1-5 above other than
the explicitly-flagged, deliberately-additive touches to
`capm.py`/`ch3.py`/`config/settings.py` (all in Wave 2, single task, single
owner — `smart_beta/data/align.py` is untouched by this plan) and the
deliberate exclusion of any edit to Phase 5A's own script (Wave 3).
