# Phase 5B Barrier 4a — Terminal Disposition (P5B-6)

This is a narrow, documentation-only record. It commits no code, adds
no `smart_beta/pipelines/ch3_pilot.py`, copies no raw evidence into any
production module, and does not modify any Phase 5A file. The full
empirical evidence lives on the preserved, unmerged branch
`phase5b/task-p5b-6-ch3-pilot`, whose terminal commit is named below.

## What P5B-6 was

Per the frozen plan (`worker_tasks/phase5b/phase5b-plan.md`, Wave 4a):
the real CH3 pilot against the frozen four-name universe
(`000001.SZ`, `600519.SH`, `601318.SH`, `000858.SZ`), using P5B-5's
staged-capture tooling to record fixtures, gated through freeze ->
capture -> empirical semantic checks -> factor computation + hand
verification, with Barrier 4a requiring a hand-verified factor date,
a measured E/P suppression rate, and a CH4-readiness go/no-go from
real turnover diagnostics.

## Frozen capture contract

- Universe: the 4 frozen names above, unchanged throughout.
- Corrected capture contract: **84** identities (originally 80; 4
  identities requesting a 366-calendar-day `daily_basic` span were
  replaced -- not retried -- by 8 sub-chunks respecting a **project
  convention of <=360-calendar-day transport chunks**, proven
  gap-free and non-overlapping over the original interval; this
  convention is explicitly not a certified proxy maximum).
- Acquisition governance: **Policy C** (pre-frozen bounded acquisition
  budget), adopted mid-task and applied to P5B-6 by grandfathering its
  own already-completed rounds into the budget:
  - **Maximum invocations: 3** (this project's own historical
    precedent from two independent capture sessions, not a
    probability-model derivation -- see
    `phase5b/task-p5b-6-ch3-pilot`'s own `ACQUISITION_POLICY.md`,
    commit `b15d3d74e93372d73e66ddce0d8ceb14d86f9d19`, for the full
    reasoning).
  - Each invocation: resume-only-missing, exactly one live attempt per
    identity, no intra-invocation retry/backoff/sleep, immutable
    evidence preservation, append-only.

## Run-by-run outcome (from immutable evidence)

| Invocation | Attempted | Success | Entitlement denial | Transport failure | Request-spec failure |
|---|---|---|---|---|---|
| Run 1 | 80 | 48 | 0 | 28 | 4 |
| Resume-2 | 36 | 18 | 0 | 18 | 0 |
| Resume-3 | 18 | 14 | 0 | 4 | 0 |

**Final combined coverage: 80 / 84.** **4 identities remain
unresolved, all `UPSTREAM/TRANSPORT FAILURE` (`HTTP 503
upstream_pool_exhausted`), zero entitlement denials observed anywhere
across all 134 live calls in this task:**

- `601318.SH::fina_indicator::20260630`
- `000858.SZ::fina_indicator::20250930`
- `000858.SZ::income::20251231`
- `000858.SZ::fina_indicator::20260331`

**Acquisition budget exhausted: 3 of 3 invocations consumed. No
Resume-4 is permitted under this capture contract.**

## Scientific interpretation (stated precisely, not glossed over)

**P5B-6 did not fail a CH3 methodology test.** It never reached CH3
methodology at all. Capture-set freezing and the bounded acquisition
attempts are the only stages that ran. The following were **NOT
REACHED / NOT TESTED**, not passed and not failed:

- the `total_mcap` semantic-equivalence check;
- the E/P positive-vintage survival/suppression measurement;
- empirical PIT formation verification;
- `compute_ch3_factors_pit` factor computation;
- independent hand verification;
- the CH4-readiness go/no-go decision.

## Entitlement disposition (bounded language, not generalized)

**No explicit entitlement denial was observed in the bounded P5B-6
capture** (0/134 live calls returned 400/401/403 across all three
invocations). This is not a claim about global endpoint or account
accessibility beyond this task's own bounded sample.

## Eventual bounded acquisition feasibility for this pilot: NOT ESTABLISHED

Real progress was made under the frozen budget (66 -> 80 of 84 across
Resume-2 and Resume-3), but complete required acquisition was not
achieved within the pre-frozen three-invocation budget. This finding
does not support inferring a success probability for any future
attempt, and does not support a claim that the remaining 4 identities
could never eventually be acquired -- only that they were not acquired
within *this* pilot's frozen budget.

## Certification boundaries (unchanged, restated verbatim)

`PROXY DETERMINISM = NOT CERTIFIED`. `SINGLE-PASS COMPLETE-CAPTURE
RELIABILITY = NOT CERTIFIED`. `PROXY SUFFICIENT FOR PRODUCTION = NO`.
None of these is upgraded or downgraded by this outcome.

## Barrier 4a: NOT PASSED

## Evidence lineage

The complete, independently-reviewed empirical record -- Stage 1
freeze, Run 1, the chunking correction, Resume-2, the acquisition-policy
analysis, and Resume-3 -- is preserved unmerged on branch
`phase5b/task-p5b-6-ch3-pilot`, terminal commit
`2aac75682869a9e490a40e29da633c3615b431b1`. That branch and commit are
not deleted, not rewritten, and not merged as a completed
implementation; this document exists so the evidence's location and
final disposition are discoverable from `master` without requiring the
branch to be merged.

## Policy C -- adopted as provisional Phase 5B empirical-acquisition governance

Recorded here for future Phase 5B empirical tasks, as a governance
principle, not a universal numeric constant:

- the capture contract (universe, endpoints, date ranges, chunking) is
  frozen *before* any live call;
- the invocation budget is frozen *before* capture execution begins
  (not chosen or adjusted after seeing partial results);
- each invocation attempts only currently-missing identities, exactly
  once each, with no intra-invocation retry/backoff;
- all evidence is immutable and append-only across invocations;
- a deterministic request-specification failure (e.g. a proxy
  date-range cap) is corrected only by coverage-equivalent transport
  repartitioning, proven gap-free/non-overlapping *before* any
  corrected live call -- never by simply retrying the same request;
- an entitlement failure is classified and reported as its own
  distinct category, never merged with a transport failure;
- budget exhaustion with required identities still missing is a
  **fail-closed** terminal state, not a trigger for an unauthorized
  additional invocation.

**This governance principle is frozen; the specific number "3" is not
silently generalized as a universal constant.** A future empirical
task's own numerical invocation budget must be specified before that
task's own capture begins, based on that task's own engineering
context, exactly as this document's own budget was derived from this
project's specific capture history rather than assumed.

## Next-step classification

Per the frozen plan's own Wave 4b entry (`worker_tasks/phase5b/
phase5b-plan.md`): *"Wave 4b (standalone, conditional on Barrier 4a's
go decision) -- P5B-7 ... Barrier 4b (or explicit 'CH4 deferred'
disposition if the go/no-go was no)."* Wave 4b's only stated
precondition is Barrier 4a's *go decision* -- a decision that was never
produced, because P5B-6 never reached the stage that would produce it
(Stage 4's CH4-readiness go/no-go). Wave 5 in turn "needs everything
before it," including a resolved Wave 4a/4b. The frozen plan contains
no explicit provision for a Barrier 4a outcome of "budget-exhausted,
incomplete capture, no go/no-go ever produced" -- it anticipated a
completed pilot yielding either a go or a no-go, not a pilot that never
reaches that decision point at all.

**Classification: Phase 5B is blocked pending a different empirical
data path** (option A of three considered) -- not because the frozen
DAG explicitly permits another task to proceed regardless (it does
not; option B), and not because a plan amendment is being made now
(none is; option C is deferred, not decided, by this document). The
currently-authorized data path (this Tushare proxy, under Policy C's
3-invocation budget) has been fully and correctly exhausted without
achieving complete required capture, and the frozen plan defines no
alternate route past this specific barrier. Resolving this blocked
state requires an explicit future decision -- a new, separately
authorized bounded capture attempt (a fresh Policy C budget, not an
automatic continuation), a different empirical data source, or a
plan amendment redefining Barrier 4a's completeness criterion -- none
of which is decided by this document.

---

## Post-Barrier-4a work and final disposition (D4A-HOLD)

This section records everything done **after** the terminal disposition
above, none of which reopens P5B-6's own historical result (still
exactly as recorded above: `80/84`, Policy C `3/3` exhausted, Stage 4
not reached, Barrier 4a not passed -- **never rewritten**). Each item
below lives on its own branch/commit; none is merged into P5B-6's own
evidence branch, and none authorizes live capture, Resume-4, a new
pilot, Stage 4, or P5B-7.

### P5B-ST1 -- historical ST integration repair (B1)

Merge commit `77b66726b909086507429391d00b10e9fd59b09d` (branch
`phase5b/task-p5b-st1-historical-st-integration`, reviewed commit
`23d29cbc336fe3a6ed09f4e2fd62701e42804431`). Wired the already-certified
`bak_basic`/`is_st_name`/`map_to_trading_status(name_by_date=...)` path
into the assembled `TushareAShareSource.get_trading_status()`, which had
never supplied historical name evidence at all (a pre-existing,
documented, deliberate omission -- not a P5B-6-specific gap). Zero live
calls; Phase 5A and Phase 4D-B certifications unchanged (`market_data.py`
and `tradability.py` byte-identical to pre-merge master).
**B1: REPAIRED** (software only -- see ST3/B2R below for why this alone
changes nothing about the real pilot's numbers).

### P5B-ST2 -- `bak_basic` acquisition-capability probe

Evidence branch `phase5b/task-p5b-st2-bak-basic-probe`, commit
`32df213d160e05de98fe86f124651b13a39f6aed` (preserved, **not merged**).
Exactly 2 authorized live calls, no retry: Shape D (`trade_date` only,
`ts_code` omitted) returned a full cross-sectional snapshot (4,294
distinct stocks for one date, including an exact match to the existing
certified `002450.SZ` specimen) -- **D-CERTIFIED-FOR-PROBED-DATE**.
Shape B (`ts_code` + `start_date`/`end_date` over ~1,461 days) was
rejected with the same `date_range_too_large` cap already documented for
`daily_basic` -- **B-NOT-CERTIFIED** for the probed range (a positive but
inconclusive signal, not a certification).

### P5B-ST3 -- historical ST snapshot acquisition design

Pure offline design, no live calls, no code changed. Derived the exact
real requirement for the pilot window: **221 trading dates**
(`2025-09-30`..`2026-08-31`, matching `884 = 221 x 4` in Stage 3's own
evidence exactly) -- correcting an earlier ~470-date planning estimate,
which belonged to CH4's separate turnover lookback window, not to the ST
requirement. Confirmed `scripts/staged_capture.py`'s existing
`RequestSpec`/`StagedCaptureSet` architecture already supports a
`(bak_basic, {trade_date})` identity with **zero framework changes**.
**Classification: ST3-A.** No capture was executed.

### P5B-B2R -- fundamentals blocker (B2) resolution investigation

Pure offline investigation, no live calls, no code changed. Traced the
four unresolved P5B-6 raw identities
(`601318.SH::fina_indicator::20260630`,
`000858.SZ::fina_indicator::20250930`, `000858.SZ::income::20251231`,
`000858.SZ::fina_indicator::20260331`) through the real join logic in
`smart_beta/vendors/tushare/fundamentals.py::_assemble_ch3_records`:
`fina_indicator` is the sole source of the `ni_ex_nonrecurring` value and
`income` is the sole source of its anchor vintage -- **no already-captured
endpoint can substitute for either**. Also found that the actual
production path (`retrieve_fundamentals`, `allow_partial=False`, 92-day
sub-interval decomposition) fails the *entire* window for *all four*
stocks, not just the four raw gaps in isolation, because every required
fiscal period falls inside one of the three sub-intervals a gap touches.
At the narrower per-observation level, `000858.SZ` alone loses 11 of 12
formations because its three gaps land on three consecutive fiscal
periods with no surviving interim report. **Classification: B2R-C** --
genuinely load-bearing, no offline fix, no currently-actionable
independent source; the only identified path is retrying the exact
exhausted identities, which is explicitly **not authorized**.

### CH3 / CH4 status after all of the above

- **CH3:** software complete; empirically blocked (tradability
  unresolved for the real window since no `bak_basic` evidence exists for
  it; fundamentals load-bearing-blocked for `000858.SZ`).
- **CH4:** software complete; turnover leg **data-ready** (coverage 1.0,
  all 4 stocks, real evidence, unchanged); empirically blocked for the
  *same* reasons as CH3 (CH4 reuses `ch3_pit`'s panel loader and the same
  tradability policy, and needs the full CH3 factor set including E/P for
  VMG) -- not an independent or additional blocker, and not a lesser one
  either. **No CH3 or CH4 empirical certification is issued.**

### Final disposition: D4A-HOLD

Phase 5B empirical execution is **suspended pending a material exogenous
change**. This is not a new barrier classification beyond the one
recorded above -- it is this document's final, current answer to "what
happens next," synthesizing P5B-ST1/ST2/ST3/B2R.

**Qualifying material exogenous changes** (any one, independently
verified *before* any new attempt, never inferred):
a materially different data provider; an independently confirmed
upgraded official Tushare entitlement; a newly available qualifying
institutional/licensed source with explicit semantic equivalence
established first (field, report period, vintage identity, knowledge
date, PIT/revision behavior); an independently observable change to the
proxy/provider's own infrastructure; a newly certified batch/range
acquisition contract that changes the failed identity's request shape
itself; an authoritative filing source with independently established
PIT semantics.

**Explicitly non-qualifying:** elapsed time; desire to continue; "try
again"; the same proxy; the same endpoint; the same parameters; the same
missing identity; simply resetting the Policy C invocation counter.

**Safe offline maintenance** (may happen at any time without affecting
this disposition, and does not by itself move Barrier 4a even
partially): an ST2 recorder regression test; an offline Shape-D
snapshot parser/validator built and tested against synthetic fixtures;
a deterministic completeness checker for a future capture, never run
against live data; documentation and diagnostics on already-existing
evidence.

**Barrier 4a: NOT PASSED. P5B-7: BLOCKED. Next wave: NOT AUTHORIZED.**
