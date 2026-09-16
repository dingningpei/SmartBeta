# Phase 4B Worker Task — P4B-9: Certification (Wave 4, final gate)

## Background (read this first)

Read `worker_tasks/phase4b/phase4b-plan.md` in full first. This task
starts only after P4B-8 is merged and Barrier 3 is green. Confirm your
worktree's `master` merge-base contains `smart_beta/vendors/tiingo/source.py`
before starting.

**Your task, P4B-9, is the final Phase 4B gate — not another ordinary
unit-test task.** Its job is to run Phase 3's already-merged, already-
parameterized `smart_beta.pit.compliance.check_*` functions against the
real `TiingoPITSource`, using real Tiingo specimens, and produce an
explicit, honest report: **PASS / FAIL / NOT CERTIFIED**, with the third
outcome never collapsed into either of the other two.

**This is the task where several separate "do not overclaim" decisions
from architecture review all converge into one deliverable.** You are not
deciding these policies — they are frozen — you are the one who writes
them down as literal, checked-in report lines:

```
RESTATEMENT/VINTAGE RECONSTRUCTION = NOT CERTIFIED
IDENTIFIER CONTINUITY = NOT CERTIFIED          (only if P4B-2 used ticker fallback)
FLOAT MARKET CAP = APPROXIMATED (no distinct float-adjusted figure available from Tiingo)   (only if P4B-4's investigation found this)
DIVIDEND ADJUSTMENT SEMANTICS = NOT CERTIFIED  (only if P4B-5's DIVIDEND_SEMANTICS_CERTIFIED finding was False, or its fail-closed path was taken)
```

Read P4B-2's, P4B-4's, and P4B-5's actual merged module docstrings/reports
before writing your report — do not guess whether the identifier,
float-market-cap, or dividend-semantics caveats apply; check what each
actually found.

**A further, separate completeness requirement, added after review: no
check that was considered relevant/applicable in principle may simply
disappear from the report.** The four named lines above cover specific,
known caveats; they do not exhaust every way a check could end up not
run. See "Completeness invariant" below — it is a distinct requirement
from the four named lines, not satisfied by them alone.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-9-certification`
on branch `phase4b/task-p4b-9-certification`, branched from `master`
after P4B-8 merges. Run tests with `.venv/bin/pytest`.

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
```

## What to build

Not a new production module — a test file that runs the real checks
against a real, fixture-fed `TiingoPITSource`, plus a checked-in report
document.

```python
# tests/test_tiingo_certification.py

def _build_source() -> TiingoPITSource:
    """Constructs TiingoPITSource(["AAPL", "TWTR"], client=<replay-transport
    TiingoClient fed your certification fixtures>)."""

def test_layer_a_schema_and_determinism(): ...
    # check_schema_conformance, check_no_shared_mutable_state,
    # check_deterministic_results against the real source

def test_layer_a_survivorship_twtr(): ...
    # check_delisted_security_history_present, stock_id resolved for TWTR,
    # expected_last_date = the real corroborated delist_date from P4B-7's
    # policy applied to your TWTR fixture

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
3. The literal line `IDENTIFIER CONTINUITY = NOT CERTIFIED` **if and only
   if** P4B-2's merged module docstring shows a ticker fallback was used
   (`ResolvedIdentifier.is_permanent == False` for either AAPL or TWTR) —
   read the real merged code to check; omit this line entirely if
   `permaTicker` was successfully used for both.
4. The literal line `FLOAT MARKET CAP = APPROXIMATED (no distinct
   float-adjusted figure available from Tiingo)` **if and only if**
   P4B-4's merged module docstring shows `float_mcap` was set equal to
   `total_mcap` — read the real merged code to check; omit this line
   entirely if a genuinely distinct figure was found.
5. The literal line `DIVIDEND ADJUSTMENT SEMANTICS = NOT CERTIFIED` **if
   and only if** P4B-5's merged module docstring shows
   `DIVIDEND_SEMANTICS_CERTIFIED == False`, or shows that its fail-closed
   exception path was taken for dividend rows — read the real merged code
   to check; omit this line entirely if the vendor-semantics evidence was
   successfully established.
6. A short "what remains deferred" list, copied from
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
than imported from theirs.

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

## Acceptance criteria

- All pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `docs/phase4b_tiingo_certification.md` exists, contains the mandatory
  `RESTATEMENT/VINTAGE RECONSTRUCTION = NOT CERTIFIED` line unconditionally,
  contains the three conditional lines (`IDENTIFIER CONTINUITY`,
  `FLOAT MARKET CAP`, `DIVIDEND ADJUSTMENT SEMANTICS`) correctly included
  or correctly omitted based on what P4B-2/P4B-4/P4B-5 actually found,
  and satisfies the completeness invariant: every check named in "APIs
  you consume" above has an explicit disposition line (PASS/FAIL/
  NOT CERTIFIED/`NOT RUN — <reason>`), with none simply absent.
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
considered check with no omissions; (b) which of the three conditional
NOT CERTIFIED lines you included or omitted, and why (quoting what
P4B-2/P4B-4/P4B-5's real merged code showed); (c) the disposition and
reason for `check_future_announcement_not_visible` specifically; (d) test
results; (e) `git diff --stat`; (f) the full text of
`docs/phase4b_tiingo_certification.md`. Do not touch `master`, do not
modify files outside the list above.
