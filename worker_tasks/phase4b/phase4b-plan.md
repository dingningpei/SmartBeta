# Phase 4B Frozen Architecture: First Real US Adapter (Tiingo)

Baseline: `master` at commit `aae4c72` (Phase 3 complete, tagged
`phase3-complete`, 383/383 tests green).

This document is the frozen record of Phase 4B's design, after three
review rounds. It supersedes nothing upstream — no Phase 3 file
(`smart_beta/pit/*`) is modified by any task below. It is the reference
every P4B-1..9 worker spec points back to instead of re-deriving context.

## Scope

Implement and certify `TiingoPITSource`, a `smart_beta.pit.source.PITDataSource`
implementation backed by the Tiingo API, covering US equities. Certification
means: run Phase 3's existing, parameterized `pit.compliance.check_*`
functions against it using real, recorded Tiingo specimens, and produce an
explicit PASS / FAIL / **NOT CERTIFIED** report — never silently upgrading an
untestable capability to a pass.

**Deferred, not built here:** SEC/XBRL ingestion, any cross-vendor
compositing/merge class, index membership, Phase 0-2 engine migration
(Phase 4C), the China A-share adapter (Phase 4D).

## The composite-source architecture

```
Tiingo client            SEC/XBRL client (future, NOT this phase)
      \                        /
     canonical raw records (per-vendor mapping modules)
              |
     TiingoPITSource (implements PITDataSource fully, Tiingo-only)
              |
     Phase-3 compliance boundary (existing check_* functions, called
     with Tiingo's own real facts as parameters)
              |
        PointInTimeView  ->  research engines (not touched)
```

This works without any Phase 3 change because `PanelSchema.validate()`
(`smart_beta/data/schema.py`) only checks that required columns are present
with the right dtype — extra columns (provenance, `is_zero_volume`) pass
through untouched — and because `PITDataSource` is a plain ABC: which
concrete class implements it is a call-site decision, not a Phase 3 concern.

## Five frozen policies (binding on every task below)

1. **Fundamentals period-end reconciliation is fail-closed.** `asReported`
   values + `date` -> knowledge_date. Default/normalized `date` -> a
   report_period_end lookup key, matched to the asReported statement by
   fiscal identity (year/quarter), never by date, never by calendar-quarter
   arithmetic (AAPL's fiscal Q3 2026 ends 2026-06-27, not 2026-06-30).
   Exactly one match required (`count(period_end_matches) == 1`); 0 or >1
   matches raises `FiscalPeriodReconciliationError` — never silently
   dropped, never inferred, never forward/back-filled, never an arbitrary
   duplicate pick, and `knowledge_date` is never substituted for
   `report_period_end`. Owned by P4B-6.

2. **Restatement status is a schema-constrained placeholder, not a claim.**
   `is_restatement=False` is emitted because `FUNDAMENTALS_FACT_SCHEMA`
   requires a bool and Tiingo currently returns at most one vintage per
   fact. `False != verified non-restated.` P4B-9's certification report
   must contain the literal line `RESTATEMENT/VINTAGE RECONSTRUCTION = NOT
   CERTIFIED`, and must not invoke `check_fundamentals_vintages_preserved`
   or `check_restatement_not_backfilled` against `TiingoPITSource` (no real
   multi-vintage specimen exists under current access).

3. **Identifiers: `permaTicker`, investigated empirically, never CIK.**
   P4B-2 must test whether Tiingo's `permaTicker` is present, stable, and
   security-level (not issuer-level like CIK) for both an active security
   (AAPL) and a delisted one (TWTR). Preferred: `permaTicker -> stock_id`.
   Ticker fallback is permitted only if `permaTicker` is unobtainable under
   current access, and if used, P4B-9's report must contain the literal
   line `IDENTIFIER CONTINUITY = NOT CERTIFIED`.

4. **The trading calendar comes from an independent, authoritative source,
   never from Tiingo row presence.** P4B-3 builds NYSE's real holiday
   calendar via `TradingCalendar.from_weekdays_excluding_holidays`, including
   9/11 (2001-09-11..09-14) and Hurricane Sandy (2012-10-29..10-30) as
   named exceptional closures. A basket of liquid securities' Tiingo rows
   may be used only as an optional sanity-check oracle, never as the source
   of truth — and P4B-3 has zero Tiingo dependency regardless.

5. **Row existence never implies tradability.** A Tiingo EOD row with
   `volume == 0` is preserved (not dropped) and flagged via an
   adapter-specific `is_zero_volume` column on `get_trading_status`'s
   output (`PIT_TRADING_STATUS_SCHEMA`'s flag columns are open-ended by
   design — `pit/schema.py:160-164`). The same signal, corroborated by a
   *sustained* trailing run (not one day) with no further rows, is required
   before `get_listing_info` asserts a `delist_date` — Tiingo's raw
   `endDate` metadata field alone is insufficient evidence (P4B-7).

## Additional decisions made while writing these specs

- **No new dependency, no `pyproject.toml` change, anywhere in Phase 4B.**
  `smart_beta/vendors/tiingo/client.py` uses only `urllib.request`/`json`/
  stdlib for its live transport. This avoids the one shared-file collision
  risk that required a dedicated reconciliation task in Phase 2.
- **All automated tests run against recorded fixtures — zero live network
  calls in `pytest`.** Each task owns its own fixture subdirectory under
  `tests/fixtures/tiingo/<task>/` and records its own copies of the real
  specimens it needs (some duplication across tasks is accepted, exactly as
  Phase 1-3 accepted duplicated *scenarios* — never duplicated *files* one
  task doesn't own).
- **`get_trading_status` is owned by P4B-4, not a separate task.** For
  Tiingo/US equities the only trading-status signal available is
  volume-derived (`is_zero_volume`); there is no China-A-share-style
  suspension/limit-up/limit-down/ST data source in scope. P4B-4 therefore
  delivers three of `TiingoPITSource`'s seven methods worth of mapping
  logic (`get_raw_returns`, `get_market_cap`, `get_trading_status`), all
  derived from the same EOD row.
- **Float vs. total market cap:** Tiingo is not expected to expose a
  float-adjusted figure distinct from total market cap for US equities.
  P4B-4 must investigate this empirically; if only one figure exists,
  `float_mcap` is set equal to `total_mcap` as a named, documented
  approximation (never silently), and P4B-9's report must contain the
  literal line `FLOAT MARKET CAP = APPROXIMATED (no distinct float-adjusted
  figure available from Tiingo)` if this is the outcome.
- **Corporate-action adjustment-factor formulas are frozen, not left to the
  worker to derive under time pressure** (see P4B-5): split factor is used
  directly as Tiingo reports it; dividend factor is
  `1 + divCash / close_on_effective_date`, which is the unique factor that
  makes `compute_adjusted_returns`'s `(1+raw_ret)*factor-1` formula recover
  the correct total return from Tiingo's ex-dividend raw close.

## Subpackage layout

```
smart_beta/vendors/
    __init__.py                                                [P4B-1]
    tiingo/
        __init__.py                                             [P4B-1]
        client.py            # TiingoClient, TiingoAPIError, replay_transport [P4B-1]
        identifiers.py       # resolve_stock_id, ResolvedIdentifier          [P4B-2]
        calendar_source.py   # build_nyse_calendar                           [P4B-3]
        returns_and_market_cap.py  # raw returns, market cap, trading status [P4B-4]
        corporate_actions.py       # split/dividend -> CORPORATE_ACTIONS_SCHEMA [P4B-5]
        fundamentals.py             # asReported reconciliation (fail-closed)  [P4B-6]
        listing.py                   # listing/delisting w/ corroboration     [P4B-7]
        source.py                     # TiingoPITSource(PITDataSource)        [P4B-8]
tests/
    fixtures/tiingo/<task-slug>/     # each task owns exactly one subdirectory
    test_tiingo_{client,identifiers,calendar,returns_market_cap,
                 corporate_actions,fundamentals,listing,source,
                 certification}.py
docs/
    phase4b_tiingo_certification.md                              [P4B-9]
```

## Task table

| Task | Objective | Files | Depends on | Wave |
|---|---|---|---|---|
| P4B-1 | Tiingo HTTP client, fixture-replay transport | `vendors/tiingo/{__init__,client}.py`, `vendors/__init__.py`, tests | none | 1 |
| P4B-2 | `stock_id` policy via `permaTicker` investigation | `vendors/tiingo/identifiers.py`, tests | none | 1 |
| P4B-3 | Authoritative NYSE trading calendar | `vendors/tiingo/calendar_source.py`, tests | none | 1 |
| P4B-4 | Raw returns + market cap + trading status (zero-volume) | `vendors/tiingo/returns_and_market_cap.py`, tests | P4B-1, P4B-2 | 2 |
| P4B-5 | Corporate actions (split/dividend, raw only) | `vendors/tiingo/corporate_actions.py`, tests | P4B-1, P4B-2 | 2 |
| P4B-6 | Fundamentals, fail-closed period-end reconciliation | `vendors/tiingo/fundamentals.py`, tests | P4B-1, P4B-2 | 2 |
| P4B-7 | Listing/delisting with corroboration | `vendors/tiingo/listing.py`, tests | P4B-1, P4B-2 | 2 |
| P4B-8 | `TiingoPITSource` assembly | `vendors/tiingo/source.py`, tests | P4B-4, P4B-5, P4B-6, P4B-7 | 3 |
| P4B-9 | Certification (PASS/FAIL/NOT CERTIFIED) | `docs/phase4b_tiingo_certification.md`, tests | P4B-8 | 4 |

## Dependency DAG

```
Wave 1 (parallel)   P4B-1 (client)   P4B-2 (identifiers)   P4B-3 (calendar)
Barrier 1
Wave 2 (parallel)   P4B-4 (returns/mcap/status)   P4B-5 (corp actions)   P4B-6 (fundamentals)   P4B-7 (listing)
                    (needs 1, 2)                  (needs 1, 2)           (needs 1, 2)           (needs 1, 2)
Barrier 2
Wave 3 (standalone) P4B-8 (assembly)   (needs 4, 5, 6, 7)
Barrier 3
Wave 4 (standalone) P4B-9 (certification)   (needs 8)
```

## Status

Barrier 1, Barrier 2, the Market Cap Bridge (P4B-M1/M2), and P4B-8 are
all merged to `master` (`f716b1a`, 690/690 tests green). The Determinism/
Replay Barrier (P4B-D1, P4B-R1, below) runs next, in parallel, before
P4B-9. P4B-9 has not started.

## Market Cap Bridge (P4B-M1 / P4B-M2) — pre-P4B-8 follow-up

P4B-4's real investigation found `map_eod_to_market_cap` had no viable
data path under the client surface that existed at the time, so it
correctly fails closed (`TiingoMarketCapUnavailableError`) rather than
fabricating a figure. Left as-is, this would produce an avoidable
`schema_conformance_get_market_cap` FAIL in P4B-9's certification (Phase
3's `check_schema_conformance` calls all six panel methods and turns a
raised exception into a genuine FAIL, not a skip). Two small, sequential,
independently reviewed follow-up tasks close this gap before P4B-8:

| Task | Objective | Files | Depends on |
|---|---|---|---|
| P4B-M1 | Add `TiingoClient.get_fundamentals_daily` (`GET /tiingo/fundamentals/{ticker}/daily`) | `vendors/tiingo/client.py`, its test file, `tests/fixtures/tiingo/client/` | none (branches from `18e8308`) |
| P4B-M2 | Replace `map_eod_to_market_cap`'s fail-closed placeholder with `total_mcap` from `marketCap`, `float_mcap = total_mcap` as a named, flag-and-docstring-enforced approximation (`FLOAT_MARKET_CAP_IS_APPROXIMATED`) | `vendors/tiingo/returns_and_market_cap.py`, its test file, `tests/fixtures/tiingo/returns_market_cap/` | P4B-M1 merged |

Specs: `task-p4b-m1-fundamentals-daily-client.md`,
`task-p4b-m2-market-cap-adapter.md`. Neither touches Phase 3, identifiers,
listing, or corporate actions. P4B-8 is not created until both are merged.

## Determinism/Replay Barrier (P4B-D1 / P4B-R1) — pre-P4B-9 follow-up

Independent P4B-8 review reproduced Phase 3's `check_deterministic_results`
(already-merged, unmodified) directly against the real, assembled
`TiingoPITSource` and found it genuinely fails whenever a queried method's
output is non-empty — not from a data bug, but because every panel method
carries a real wall-clock `_ingested_at` provenance timestamp (frozen
Phase 4B policy, correctly implemented identically across every Wave 2
module). `SyntheticPITSource`, this check's only prior reference
implementation, carries no provenance columns and could never have
exposed this. Separately, the same review found `replay_transport`
(P4B-1) cannot distinguish `get_fundamentals_asreported` from
`get_fundamentals_normalized` in an offline fixture-fed client, since
both hit one path differing only by a query parameter `replay_transport`
discards by design; P4B-8 worked around this with an isolated,
test-file-local wrapper. Two small, independently reviewed, **parallel**
(no file overlap) follow-up tasks close both gaps before P4B-9:

| Task | Objective | Files | Depends on |
|---|---|---|---|
| P4B-D1 | Amend `check_deterministic_results` (Phase 3, deliberately narrow reopening) to compare only each schema's own canonical columns, never provenance columns, using the schema `_panel_method_specs` already provides | `pit/compliance.py`, its test file | none (branches from `f716b1a`) |
| P4B-R1 | Extend `replay_transport` with a small, general, backward-compatible path+param disambiguation mechanism | `vendors/tiingo/client.py`, its test file | none (branches from `f716b1a`) |

Specs: `task-p4b-d1-canonical-determinism.md`,
`task-p4b-r1-replay-request-identity.md`. D1 is the only Phase 4B task
permitted to touch `smart_beta/pit/*`, and only `compliance.py`'s
`check_deterministic_results`, narrowly. R1 touches only `client.py`'s
`replay_transport`. File sets are fully disjoint from each other and from
every other Phase 4B module — both run in the same wave. P4B-9 is not
created until both are merged and the full suite is green.

## Recorded, not-yet-applied finding: TWTR delisting corroboration

Real TWTR EOD evidence has a trailing zero-volume run of length 1. The
frozen `_MIN_CORROBORATING_ZERO_VOLUME_DAYS = 5` (P4B-7, correct and
unchanged) means real TWTR's `delist_date` is `NaT` under the current
policy — P4B-7's code is correct; this is a specimen limitation, not a
bug. Independently reconfirmed through the fully assembled
`TiingoPITSource` during P4B-8 review: TWTR still resolves to
`delist_date = NaT`, unmodified by assembly. Phase 3's
`check_delisted_security_history_present` will therefore produce a
genuine, honest FAIL for its `delisted_listing_info` sub-check when run
against TWTR (the row-presence sub-checks — raw returns, market cap,
trading status — are independently real and should pass). This must
never be smoothed into a pass.

## Recorded status: identifier continuity

`permaTicker` is real and live-confirmed, but is absent from the
`GET /tiingo/daily/{ticker}` shape `resolve_stock_id` consumes (it lives
on `GET /tiingo/fundamentals/meta` instead). Under the current, merged
code, AAPL/TWTR/FB/META all resolve via the ticker fallback
(`is_permanent=False`). **`IDENTIFIER CONTINUITY = NOT CERTIFIED`** and
must not be silently upgraded. P4B-8's assembly (`TiingoPITSource._resolve_
stock_id`) confirmed it wires only `get_meta` → `resolve_stock_id`, never
`fundamentals/meta` — the frozen P4B-8 spec never authorized otherwise,
so this status carries forward unchanged into P4B-9.

## Recorded, not-yet-applied: required P4B-9 spec amendments

`task-p4b-9-certification.md` is not edited until this list is explicitly
reviewed and approved, after the P4B-D1/P4B-R1 barrier passes:

1. **TWTR.** Remove the stale assumption of "the real corroborated
   delist_date from P4B-7's policy." Real terminal zero-volume run = 1,
   frozen threshold = 5, therefore `delist_date = NaT`. Certification
   must distinguish historical row presence (independently real, should
   pass) from listing-info corroboration (will not corroborate for
   TWTR — an honest, expected FAIL, never fabricated into a pass). Carry
   `TWTR DELISTING CORROBORATION = NOT CERTIFIED`. Never lower the
   threshold merely to obtain PASS.
2. **Identifiers.** Carry `IDENTIFIER CONTINUITY = NOT CERTIFIED` — the
   resolver remains `get_meta`-based; `permaTicker` exists elsewhere but
   is not wired.
3. **Float market cap.** `get_market_cap` is now operational (P4B-M1/M2),
   but `float_mcap == total_mcap` is explicitly an approximation, never
   an independently observed or vendor-certified float-adjusted figure.
   Carry `FLOAT MARKET CAP = NOT CERTIFIED as independently observed
   vendor float-adjusted market capitalization`. Schema conformance is
   not semantic certification.
4. **Restatements.** `is_restatement=False` remains a schema-constrained
   placeholder. Carry `RESTATEMENT/VINTAGE RECONSTRUCTION = NOT
   CERTIFIED`, unchanged.
5. **Fundamentals ranged-query semantics.** Add an explicit certification
   step for the assumption identified during P4B-8 review: whether
   Tiingo's real, live, date-bounded statements endpoints actually return
   a mutually reconcilable fiscal-identity subset (the offline PoC only
   assumes this). Attempt a real call over a period known to have
   coverage on both endpoints; if access/network/vendor behavior prevents
   verification, carry `FUNDAMENTALS RANGED-QUERY SEMANTICS = NOT
   CERTIFIED`. Never weaken P4B-6's fail-closed reconciliation regardless
   of the result.
6. **Dividend semantics.** Preserve P4B-5's already independently
   supported result (real Nasdaq + SEC 8-K cross-check) unchanged; do not
   upgrade or downgrade without new contradictory evidence.

The existing P4B-9 completeness invariant (every considered item gets an
explicit PASS/FAIL/NOT CERTIFIED/`NOT RUN — reason`, no silent omission)
already covers all six; these are factual updates to stale assumptions,
not new architecture.

