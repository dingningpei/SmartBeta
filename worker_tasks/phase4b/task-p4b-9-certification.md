# Phase 4B Worker Task — P4B-9: Certification (Wave 4, final gate)

## Background (read this first)

Read `worker_tasks/phase4b/phase4b-plan.md` in full first. This task
starts only after P4B-8, the Market Cap Bridge (P4B-M1/M2), and the
Determinism/Replay Barrier (P4B-D1/P4B-R1) are all merged and green.
Confirm your worktree's `master` merge-base contains
`smart_beta/vendors/tiingo/source.py`, `TiingoClient.get_fundamentals_daily`
(P4B-M1), `map_eod_to_market_cap`'s real (non-fail-closed) implementation
(P4B-M2), `check_deterministic_results`'s canonical-column comparison
(P4B-D1), and `replay_transport`'s `param_recordings` parameter (P4B-R1)
before starting.

**This amendment (explicitly reviewed, not silent) updates the spec with
empirical findings accumulated through P4B-8 review and the
Determinism/Replay Barrier — read the whole Background section again even
if you read an earlier version of this file.**

**Your task, P4B-9, is the final Phase 4B gate — not another ordinary
unit-test task.** Its job is to run Phase 3's already-merged, already-
parameterized `smart_beta.pit.compliance.check_*` functions against the
real `TiingoPITSource`, using real Tiingo specimens, and produce an
explicit, honest report: **PASS / FAIL / NOT CERTIFIED**, with the third
outcome never collapsed into either of the other two.

**This is the task where several separate "do not overclaim" decisions
from architecture review all converge into one deliverable.** You are not
deciding these policies — they are frozen — you are the one who writes
them down as literal, checked-in report lines. Two of these are now
**empirically confirmed facts** (re-verify against the real merged code
at execution time — do not trust this document blindly if the code has
since changed, but do not assume it changed without checking either), one
is a **new required check** this amendment adds, and the rest were
already frozen:

```
RESTATEMENT/VINTAGE RECONSTRUCTION = NOT CERTIFIED
IDENTIFIER CONTINUITY = NOT CERTIFIED
FLOAT MARKET CAP = NOT CERTIFIED (approximated: float_mcap == total_mcap, no distinct vendor float-adjusted figure observed)
DIVIDEND ADJUSTMENT SEMANTICS = NOT CERTIFIED  (only if P4B-5's DIVIDEND_SEMANTICS_CERTIFIED finding was False, or its fail-closed path was taken -- re-verify, it was True as of P4B-5's merge)
TWTR DELISTING CORROBORATION = NOT CERTIFIED (real trailing zero-volume run = 1 day; frozen minimum = 5 days)
FUNDAMENTALS RANGED-QUERY SEMANTICS = NOT CERTIFIED  (unless you can actually verify it live -- see the dedicated section below; do not assume the negative without attempting verification)
```

`IDENTIFIER CONTINUITY` and `TWTR DELISTING CORROBORATION` are no longer
conditional lines you decide whether to include -- both are confirmed,
reproduced facts as of this amendment (identifier: `resolve_stock_id`
resolves AAPL/TWTR/FB/META all via ticker fallback, `is_permanent=False`;
TWTR: real trailing zero-volume run length 1 against the frozen minimum
of 5, `delist_date=NaT`, independently reproduced through the fully
assembled `TiingoPITSource`). `FLOAT MARKET CAP` is likewise no longer
conditional: P4B-M2 always sets `float_mcap = total_mcap`, unconditionally,
by design. Re-verify each against the actual merged code before writing
your report -- these facts are current as of this amendment, not
guaranteed to be current forever -- but do not omit any of the six lines
above without checking first.

**A further, separate completeness requirement, added after review: no
check that was considered relevant/applicable in principle may simply
disappear from the report.** The lines above cover specific, known
caveats; they do not exhaust every way a check could end up not run. See
"Completeness invariant" below — it is a distinct requirement from the
named lines, not satisfied by them alone.

**A further, separate distinction, added in this amendment: a compliance
check result is not a vendor semantic certification.** `check_schema_
conformance` passing for `get_market_cap` (a structural, mechanical check)
does not and must never imply `FLOAT MARKET CAP` is certified (a semantic
claim about what the vendor field actually represents) -- these are
answering different questions and must never be conflated in your report.
The same applies to every other NOT CERTIFIED line above: a method being
operationally functional (schema-conformant, callable, returns data) is
orthogonal to whether the semantic assumption underneath it has been
independently verified.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-9-certification`
on branch `phase4b/task-p4b-9-certification`, branched from `master`
after the Determinism/Replay Barrier (P4B-D1/P4B-R1) merges. Run tests
with `.venv/bin/pytest`.

## File ownership

**You may create exactly these files, and no others:**

- `tests/test_tiingo_certification.py`
- `tests/fixtures/tiingo/certification/` — any fixture files you need
- `docs/phase4b_tiingo_certification.md` (new file; `docs/` does not
  currently exist in this repo — creating it is fine, this is the only
  task that touches it)

**You must not modify anything else**, including any
`smart_beta/vendors/tiingo/*.py` module (all frozen, already merged —
this task never edits production code, only exercises it),
`smart_beta/pit/compliance.py` (Phase 3, finished — you call its
existing `check_*` functions directly with Tiingo's own real facts as
parameters; you never modify it, and you never call
`run_reference_compliance_suite`, which is `SyntheticPITSource`-specific
and meaningless here), `pyproject.toml`, or any fixture subdirectory
other than your own.

## The APIs you consume (all already merged — read the actual files)

```python
# smart_beta.pit.compliance -- call these directly, NOT
# run_reference_compliance_suite
def check_schema_conformance(source, start, end, fields) -> list[ComplianceCheckResult]: ...
def check_no_shared_mutable_state(source, start, end, fields) -> list[ComplianceCheckResult]: ...
def check_deterministic_results(source, start, end, fields) -> list[ComplianceCheckResult]: ...
    # P4B-D1 (merged): compares each panel's CANONICAL (schema-required)
    # columns only -- _source_vendor/_source_endpoint/_ingested_at are
    # excluded by schema membership, not by name. A real, non-empty query
    # range is required to exercise this meaningfully (an empty result
    # trivially "passes" regardless of provenance behavior -- do not rely
    # on an empty-output range to claim this check ran).
def check_delisted_security_history_present(source, *, stock_id, expected_last_date, query_start, query_end, fields=()) -> list[ComplianceCheckResult]: ...
def check_exchange_calendar_recognizes_holiday(source, *, holiday) -> list[ComplianceCheckResult]: ...
def check_exchange_calendar_month_end(source, *, year, month, expected_trading_month_end) -> list[ComplianceCheckResult]: ...
def check_corporate_action_raw_facts(source, *, stock_id, effective_date, expected_adjustment_factor, raw_return_date, expected_raw_return) -> list[ComplianceCheckResult]: ...
def check_survivorship_through_view(view, *, stock_id, as_of, query_start, query_end) -> list[ComplianceCheckResult]: ...
def check_build_panel_uses_exchange_calendar(view, *, year, month, expected_trading_month_end, fundamental_fields=...) -> list[ComplianceCheckResult]: ...
def check_corporate_action_adjustment_correct(view, *, stock_id, as_of, query_start, query_end, action_date, expected_true_return) -> list[ComplianceCheckResult]: ...

# DO NOT CALL against TiingoPITSource, ever, in this task:
# check_fundamentals_vintages_preserved, check_restatement_not_backfilled
# (both require multi-vintage evidence this adapter cannot provide --
# see the RESTATEMENT/VINTAGE RECONSTRUCTION policy above)

# CONSIDERED BUT MAY NOT BE RUNNABLE -- see "Completeness invariant" below:
# check_future_announcement_not_visible (requires a documented
#   future-knowledge-date specimen; only run it if P4B-6's real AAPL
#   fixture genuinely gives you one you can honestly use -- check before
#   assuming. If it does not, this check's disposition in the report is
#   NOT RUN with a stated reason, not silence.)

@dataclass(frozen=True)
class ComplianceCheckResult:
    name: str
    layer: str
    passed: bool
    message: str
    context: Mapping[str, object]

@dataclass(frozen=True)
class ComplianceReport:
    checks: tuple[ComplianceCheckResult, ...]
    @property
    def all_passed(self) -> bool: ...
    @property
    def failures(self) -> tuple[ComplianceCheckResult, ...]: ...
    def summary(self) -> str: ...

# smart_beta.pit.view
class PointInTimeView:
    def __init__(self, source: PITDataSource, settings=DEFAULT_SETTINGS): ...
    def as_of(self, as_of) -> AsOfSnapshot: ...
    def build_panel(self, start, end, fundamental_fields) -> pd.DataFrame: ...

# smart_beta.vendors.tiingo.source
class TiingoPITSource(PITDataSource): ...

# smart_beta.vendors.tiingo.client (P4B-R1, merged)
def replay_transport(
    recordings: Mapping[str, tuple[int, object]],
    *,
    param_recordings: Mapping[tuple[str, str, str], tuple[int, object]] | None = None,
) -> Transport: ...
    # Use param_recordings to disambiguate get_fundamentals_asreported vs.
    # get_fundamentals_normalized (same path, differ only by the
    # asReported query param) when building your certification client.
    # Do not write a new local param-aware transport wrapper -- that
    # workaround existed only because P4B-8 was built before this
    # mechanism existed; it is integrated now, use it.
```

## What to build

Not a new production module — a test file that runs the real checks
against a real, fixture-fed `TiingoPITSource`, plus a checked-in report
document.

```python
# tests/test_tiingo_certification.py

def _build_source() -> TiingoPITSource:
    """Constructs TiingoPITSource(["AAPL", "TWTR"], client=<TiingoClient
    over replay_transport(recordings, param_recordings=...) fed your
    certification fixtures -- use the integrated P4B-R1 mechanism to
    disambiguate the asReported/normalized statements pair; do not
    reinvent a local workaround>)."""

def test_layer_a_schema_and_determinism(): ...
    # check_schema_conformance, check_no_shared_mutable_state against the
    # real source; check_deterministic_results called at a real,
    # NON-EMPTY query range for at least one method whose real fixture
    # data actually falls inside it (an empty-output range trivially
    # "passes" and proves nothing -- do not use one here). Assert every
    # method PASSes. This is the concrete proof that P4B-D1's canonical-
    # column fix works end-to-end against the real adapter, not just
    # against P4B-D1's own synthetic test doubles.

def test_layer_a_survivorship_twtr(): ...
    # check_delisted_security_history_present with stock_id resolved for
    # TWTR and expected_last_date = TWTR's REAL last observed EOD date
    # (from your fixture, e.g. 2022-10-28) -- NOT "the corroborated
    # delist_date," which does not exist under the frozen policy. This
    # produces a MIXED, EXPECTED result: the raw-returns/market-cap/
    # trading-status row-presence sub-checks should PASS (rows genuinely
    # persist through that date); the delisted_listing_info sub-check
    # should FAIL (get_listing_info() reports delist_date=NaT, not that
    # date, because the real trailing zero-volume run is length 1 against
    # the frozen minimum of 5). Assert BOTH outcomes explicitly -- do not
    # assert the whole check "passes," and do not treat the
    # delisted_listing_info FAIL as a bug to route around. Separately,
    # assert directly that source.get_listing_info() reports TWTR's
    # delist_date as NaT (pd.isna) -- the concrete, unambiguous evidence
    # for the TWTR DELISTING CORROBORATION report line.

def test_layer_a_exchange_calendar(): ...
    # check_exchange_calendar_recognizes_holiday (use one of P4B-3's real
    # NYSE holidays, e.g. a specific New Year's Day), and
    # check_exchange_calendar_month_end (a month where NYSE's real trading
    # month-end differs from the naive calendar month-end)

def test_layer_a_corporate_action_raw_facts_aapl_split(): ...
    # check_corporate_action_raw_facts against the real AAPL 2020-08-31 split

def test_layer_b_survivorship_through_view(): ...
def test_layer_b_build_panel_uses_exchange_calendar(): ...
def test_layer_b_corporate_action_adjustment_correct_aapl_split(): ...
    # PointInTimeView(source) wrapping the real TiingoPITSource; the
    # expected_true_return must be the ONE externally documented true
    # return for the AAPL split date, not re-derived by calling
    # compute_adjusted_returns yourself (anti-tautology, same discipline
    # Phase 3's own P3-H used)

def test_restatement_vintage_reconstruction_not_invoked(): ...
    # a deliberate, explicit test (not just an omission) asserting that
    # this test file does not call check_fundamentals_vintages_preserved
    # or check_restatement_not_backfilled against TiingoPITSource -- e.g.
    # by asserting on the report's check names and confirming neither
    # appears, so a future edit that quietly adds one back is caught

def test_certification_report_covers_every_considered_check(): ...
    # a general completeness test, not specific to any one caveat: build
    # the explicit list of every check this task considered relevant in
    # principle (the ones actually run above, PLUS
    # check_fundamentals_vintages_preserved, check_restatement_not_backfilled,
    # and check_future_announcement_not_visible), parse or reference
    # docs/phase4b_tiingo_certification.md's content, and assert every
    # name in that list appears in the document with an explicit
    # disposition (PASS/FAIL/NOT CERTIFIED/NOT RUN) -- so a future edit
    # that quietly drops a line from the report, for any of the four
    # checks or any other reason, is caught here, not just for the two
    # restatement-specific checks test_restatement_vintage_reconstruction_
    # not_invoked already covers.

def test_fundamentals_ranged_query_semantics(): ...
    # NEW in this amendment. The open question P4B-8 review surfaced:
    # TiingoPITSource.get_fundamentals assumes the vendor's own
    # startDate/endDate range filtering on BOTH statement endpoints
    # yields a mutually reconcilable fiscal-identity subset -- this was
    # never verified against a live call, only assumed for the offline
    # PoC. If TIINGO_API_KEY is available in the environment: make one
    # real, date-bounded call to get_fundamentals_asreported AND
    # get_fundamentals_normalized (a real TiingoClient, not a replay
    # transport, for this one test only) over a fiscal period you can
    # independently confirm has coverage on both endpoints (e.g. AAPL's
    # most recent two fully-published quarters), and call
    # reconcile_report_period_end (or map_asreported_to_fundamentals)
    # against the real response. NEVER print, log, or persist the key
    # itself -- only the response bodies, exactly like every other
    # fixture-capture step in Phase 4B. If the key is unavailable, or the
    # live call fails, or the result is ambiguous: do not guess. Record
    # the attempt and its outcome (or the reason it could not be
    # attempted) for the FUNDAMENTALS RANGED-QUERY SEMANTICS report line
    # -- this test may legitimately be skipped/xfail'd with a clear
    # reason when no key is available, but the report's disposition must
    # still be NOT RUN with that reason stated, never silently absent.
    # Whatever the outcome, this test must NEVER weaken or bypass
    # map_asreported_to_fundamentals's fail-closed reconciliation -- if a
    # real ranged call still produces an unreconcilable pair, that is
    # itself the finding (report it), not a bug to work around.
```

## The certification report document

Write `docs/phase4b_tiingo_certification.md` by hand (not
auto-generated) after your test run, containing at minimum:

1. A disposition line for every check this task considered
   relevant/applicable in principle — not only the ones you ran. Each
   line is one of exactly:
   ```
   PASS
   FAIL
   NOT CERTIFIED
   NOT RUN — <one-line reason>
   ```
   The "ran" checks (schema/determinism/survivorship/calendar/corporate-
   action, both layers) get PASS or FAIL, matching your test suite's real
   output — do not write a report that claims something your tests
   didn't actually verify. `check_fundamentals_vintages_preserved` and
   `check_restatement_not_backfilled` get `NOT CERTIFIED` (see line 2
   below). `check_future_announcement_not_visible` gets either a real
   PASS/FAIL (if you found a genuine specimen and ran it) or
   `NOT RUN — <reason>` (if you did not) — it may never simply be absent
   from the document. This is the **completeness invariant**: every check
   named anywhere in this spec's "APIs you consume" section above must
   have a line in the document, with no exceptions and no silent
   omissions, regardless of which of the reasons below apply.
2. The literal line `RESTATEMENT/VINTAGE RECONSTRUCTION = NOT CERTIFIED`,
   with one sentence explaining why (RGEN 2024 Q2 access blocked under
   current plan tier — cite the real HTTP 400 specimen).
3. The literal line `IDENTIFIER CONTINUITY = NOT CERTIFIED`, unconditionally
   — this is a confirmed fact as of this amendment (`resolve_stock_id`
   resolves via ticker fallback, `is_permanent=False`, for every
   configured ticker under the `get_meta`-based resolver `TiingoPITSource`
   actually wires). Re-verify against the real merged
   `smart_beta/vendors/tiingo/identifiers.py` and `source.py` before
   writing this line — if it has genuinely changed (e.g. a later,
   separately reviewed task wires `fundamentals/meta`), report the real
   current state instead, but do not assume it changed without checking.
4. The literal line `FLOAT MARKET CAP = NOT CERTIFIED (approximated:
   float_mcap == total_mcap, no distinct vendor float-adjusted figure
   observed)`, unconditionally — `map_eod_to_market_cap` (P4B-M2) always
   sets `float_mcap = total_mcap` by design; there is no conditional case
   where it doesn't. Never let `check_schema_conformance` passing for
   `get_market_cap` be read as certifying this line — they answer
   different questions (see the compliance-vs-certification distinction
   in Background).
5. The literal line `DIVIDEND ADJUSTMENT SEMANTICS = NOT CERTIFIED` **if
   and only if** P4B-5's merged module docstring shows
   `DIVIDEND_SEMANTICS_CERTIFIED == False`, or shows that its fail-closed
   exception path was taken for dividend rows — read the real merged code
   to check; omit this line entirely if the vendor-semantics evidence was
   successfully established (it was `True`, independently confirmed
   against a real SEC 8-K filing, as of P4B-5's merge — re-verify, don't
   assume).
6. The literal line `TWTR DELISTING CORROBORATION = NOT CERTIFIED (real
   trailing zero-volume run = 1 day; frozen minimum = 5 days)`,
   unconditionally, alongside — not instead of — the honest FAIL your
   `delisted_listing_info` sub-check produces (see the required test
   above). State plainly in the surrounding prose that this FAIL is the
   expected, correct consequence of the frozen corroboration policy
   applied to real evidence, not a code defect — a reader must not
   mistake it for one.
7. The literal line `FUNDAMENTALS RANGED-QUERY SEMANTICS = NOT CERTIFIED`
   unless your real live verification attempt (see the required test
   above) genuinely succeeded, in which case report the actual outcome
   (reconcilable or not) instead, with the evidence. If no
   `TIINGO_API_KEY` was available to attempt it at all, say so explicitly
   as the reason — do not conflate "not attempted" with "attempted and
   failed"; both are legitimate but distinct reasons for `NOT CERTIFIED`.
8. A short "what remains deferred" list, copied from
   `worker_tasks/phase4b/phase4b-plan.md`'s scope section (SEC/XBRL,
   compositing, Phase 4C engine migration, Phase 4D China adapter) — so
   the document is a complete, standalone record of Phase 4B's actual
   final state without requiring a reader to reconstruct it from nine
   separate task specs.

## Fixture inputs

Capture, under `tests/fixtures/tiingo/certification/`, whatever raw
Tiingo responses your test suite needs to construct
`TiingoPITSource(["AAPL", "TWTR"], client=...)` and exercise the checks
above — likely a superset of what P4B-1/4/5/7 already captured for AAPL's
split and TWTR's delisting, re-recorded into your own subdirectory rather
than imported from theirs. Build your fixture-fed client with
`replay_transport(recordings, param_recordings={...})` (P4B-R1) for the
asReported/normalized statements pair, exactly as R1's own tests
demonstrate — do not write a new local workaround.

For the fundamentals ranged-query semantics test specifically: if you
have real `TIINGO_API_KEY` access, capture whatever live response(s) that
one verification attempt produces (never the key itself) as its own
clearly-labeled fixture; if you do not have access, no fixture is needed
for that test — its NOT RUN disposition and stated reason are the
required output instead.

## Non-goals

- Do not modify `smart_beta/pit/compliance.py` or any production
  `vendors/tiingo/*.py` module.
- Do not call `run_reference_compliance_suite` against `TiingoPITSource`
  — it is meaningful only against `SyntheticPITSource`'s exact reference
  scenario.
- Do not invoke `check_fundamentals_vintages_preserved` or
  `check_restatement_not_backfilled` against `TiingoPITSource` under any
  circumstance in this task.
- Do not write a report line implying a capability is certified unless a
  passing test in this same commit actually demonstrates it.
- Do not let any considered check (see "APIs you consume" above) be
  absent from `docs/phase4b_tiingo_certification.md` — an unavailable
  specimen or capability is a reason to write `NOT RUN — <reason>`, never
  a reason to omit the line entirely.
- Do not write a new local param-aware `replay_transport` wrapper for the
  asReported/normalized statements pair — use P4B-R1's integrated
  `param_recordings` mechanism.
- Do not lower `_MIN_CORROBORATING_ZERO_VOLUME_DAYS` (P4B-7), or otherwise
  alter the delisting-corroboration policy, to make TWTR's
  `delisted_listing_info` sub-check pass. It is supposed to fail here;
  report why, do not fix it.
- Do not weaken, catch, or bypass `map_asreported_to_fundamentals`'s
  fail-closed reconciliation for any reason, including a real ranged-query
  verification attempt that itself produces an unreconcilable result --
  that outcome is a finding to report, never a defect to route around.
- Never print, log, or persist a live `TIINGO_API_KEY` anywhere, including
  in test output, fixture files, or the certification report itself.
- Do not let a compliance check's PASS (e.g. schema conformance) be
  written or implied anywhere in the report as satisfying a NOT CERTIFIED
  semantic line -- they are answered independently.

## Acceptance criteria

- All pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `docs/phase4b_tiingo_certification.md` exists and contains: the four
  unconditional lines (`RESTATEMENT/VINTAGE RECONSTRUCTION`,
  `IDENTIFIER CONTINUITY`, `FLOAT MARKET CAP`, `TWTR DELISTING
  CORROBORATION`, each `= NOT CERTIFIED` with its stated reason); the one
  conditional line (`DIVIDEND ADJUSTMENT SEMANTICS`) correctly included or
  correctly omitted based on what P4B-5 actually found; the
  `FUNDAMENTALS RANGED-QUERY SEMANTICS` line with its actual outcome
  (verified, or `NOT CERTIFIED` with the real reason it couldn't be); the
  TWTR survivorship section explicitly distinguishing the passing
  row-presence sub-checks from the expected, explained
  `delisted_listing_info` FAIL; and satisfies the completeness invariant:
  every check named in "APIs you consume" above has an explicit
  disposition line (PASS/FAIL/NOT CERTIFIED/`NOT RUN — <reason>`), with
  none simply absent.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `tests/test_tiingo_certification.py`,
  `docs/phase4b_tiingo_certification.md`, and files under
  `tests/fixtures/tiingo/certification/`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-9-certification
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4b/task-p4b-9-certification`, touching only the files listed above.
**Do not merge this branch to master** — report back to the Planner for
final review of the certification report's content before any merge,
exactly as every prior Phase 3/4B task did.

## When done

Report: (a) the full PASS/FAIL/NOT CERTIFIED/NOT RUN tally, covering every
considered check with no omissions; (b) confirmation that the four
unconditional lines (RESTATEMENT/VINTAGE RECONSTRUCTION, IDENTIFIER
CONTINUITY, FLOAT MARKET CAP, TWTR DELISTING CORROBORATION) all still
matched the real merged code as you found it, or the real discrepancy if
one exists; (c) whether the conditional DIVIDEND ADJUSTMENT SEMANTICS
line was included or omitted, and why (quoting P4B-5's real merged code);
(d) the fundamentals ranged-query semantics attempt's outcome (verified
reconcilable, verified unreconcilable, or not attempted and why); (e) the
TWTR survivorship test's exact row-presence-PASS-vs-listing-info-FAIL
breakdown; (f) the disposition and reason for
`check_future_announcement_not_visible` specifically; (g) test results;
(h) `git diff --stat`; (i) the full text of
`docs/phase4b_tiingo_certification.md`. Do not touch `master`, do not
modify files outside the list above.
