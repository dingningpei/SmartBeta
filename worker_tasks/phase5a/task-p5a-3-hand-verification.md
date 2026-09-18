# Phase 5A Worker Task — P5A-3: Independent Gate A Hand Verification

## Background (read this first)

Read `worker_tasks/phase5a/phase5a-plan.md` in full. This task depends on
P5A-2 (merged, reviewed, barrier passed) — you consume its real,
committed fixtures and real, committed output artifacts. **You own no
production code.** Your entire job is an independent arithmetic check,
not a new feature.

**Why this must be independent, not a re-run:** calling
`compute_market_excess_return` again and comparing its output to itself
would prove nothing — it would only confirm the function is
deterministic, not that it computed the *correct* number. This task
instead reconstructs one real `MKT` observation **from the raw recorded
evidence, using plain arithmetic**, and compares that independent
reconstruction to the pipeline's own output.

**Independence boundary, explicit (reviewed at barrier time, not
mechanically enforced):** your test file may read exactly one value from
P5A-2's *processed* output — Artifact A's final `MKT` for the chosen
date, as the comparison target. Every other input to your reconstruction
(eligible names, lagged market caps, `adj_ret`, DGS3MO source
values/dates) must come from the *raw* fixtures under
`tests/fixtures/tiingo/phase5a_gate_a/` and `tests/fixtures/risk_free/
treasury/` — never from P5A-2's own Artifact B diagnostic evidence (its
excluded-name list, raw lagged-mcap trace, or risk-free diagnostic
columns), even though that evidence covers the same date. Reading
Artifact B's already-computed trace values instead of the raw fixtures
would make this a re-statement of P5A-2's own working, not an
independent check. **The reviewer at this task's barrier must read this
test file's source and confirm it imports/reads only the raw fixture
paths plus the single `MKT` comparison value — this is a stated,
explicit reviewer obligation, not something a production or test-suite
mechanism enforces.**

**Your working directory** will be a git worktree at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p5a-3-hand-verification`
on branch `phase5a/task-p5a-3-hand-verification`, branched from `master`
after P5A-2's barrier passes.

## File ownership

**You may create exactly:**
- `tests/test_phase5a_hand_verification.py`

**You must not modify or create anything else** — no production code
file of any kind, no fixture file (read P5A-1's and P5A-2's committed
fixtures, never alter them), no other test file, no `worker_tasks/*`.

## Required reconstruction (the actual chain, in plain arithmetic)

Pick one real, non-`NaN` `MKT` date from P5A-2's committed output
(prefer a date with all three names eligible, so the reconstruction
exercises the full weighting chain; if no such date exists, pick the
best available and state why in your test's docstring). From the raw
recorded fixtures only (never by importing and calling
`compute_market_excess_return`, `_market_factor`, or any other
production helper), reconstruct by hand, in the test file itself:

1. **Eligible names** for that date (apply the same tradability logic
   `USZeroVolumeTradabilityPolicy` would — read its real, already-
   existing implementation to reproduce its decision by hand for this
   specific date/name set, or, if its decision for this exact
   date/universe is unambiguous from the raw `is_zero_volume` values
   alone, state that explicitly rather than re-deriving the whole
   policy).
2. **Lagged market caps** — the prior trading day's `total_mcap` for
   each eligible name, read directly from the raw Tiingo fixture.
3. **Normalized weights** — each name's lagged market cap divided by
   the sum of lagged market caps across eligible names for that date.
4. **Constituent `adj_ret`** — each name's adjusted return for that
   date, read directly from the raw fixture (or recomputed from raw
   close prices plus any corporate action in the window, if one
   exists — state explicitly which).
5. **`Rm`** — the weighted sum of step 3's weights times step 4's
   returns.
6. **Risk-free reconstruction** — read the DGS3MO source value and date
   P5A-1's fixture actually recorded for this equity date, compute
   `delta_calendar_days` by hand from the two real trading dates
   involved, and apply the frozen formula
   `(y/100) * (delta_calendar_days/365)` yourself, in the test.
7. **`MKT`** — `Rm - Rf`, computed by hand.

## Required numerical tolerance

State an explicit tolerance in the test (proposed default: relative
tolerance `1e-9`, matching the precision this project's prior
certification hand-verifications have used — tighten or loosen only
with a stated reason if floating-point accumulation in the real
computation genuinely requires it). The test must assert the hand-
reconstructed `MKT` matches P5A-2's committed output value within this
tolerance, and must fail loudly (not silently pass) if the two disagree.

## Non-goals

No new production code. No new fixtures. No changes to P5A-1 or P5A-2's
own tests. No statistical claims — this is a single-point arithmetic
check, not a hypothesis test.

## Failure behavior

If the hand reconstruction does **not** match the pipeline's output
within tolerance, this is not something to "fix" by adjusting your
reconstruction until it matches — it is exactly the kind of discrepancy
Phase 5A exists to surface. Report it as a specific, named finding
(exact date, exact values on both sides, exact step where they diverge)
for independent review, and do not merge.

## Exit criteria

- `.venv/bin/pytest` green, full suite unaffected.
- The reconstruction test passes with the stated tolerance, or the
  discrepancy is reported precisely as a blocking finding.
- Zero live network calls, zero new fixtures.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p5a-3-hand-verification
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit on branch `phase5a/task-p5a-3-hand-verification`, touching
only `tests/test_phase5a_hand_verification.py`.

## When done (completion-report format)

Report exactly:
(a) the exact date chosen and why;
(b) every intermediate value in the reconstruction chain (eligible
    names, weights, returns, `Rm`, DGS3MO source value/date,
    `delta_calendar_days`, `Rf`, final `MKT`), side by side with the
    pipeline's own committed value for that date;
(c) the exact tolerance used and whether it was met;
(d) if it was not met, the precise point of divergence, reported as a
    blocking finding, not resolved by you;
(e) test results;
(f) `git diff --stat`.
Do not merge, do not touch `master`.
