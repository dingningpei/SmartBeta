# Phase 5A Worker Task — P5A-4: Gate B Fixed-Universe Scalability Pilot

## Background (read this first)

Read `worker_tasks/phase5a/phase5a-plan.md` in full — this task's
universe-construction rule, terminology, and claim boundary are frozen
there and must be followed exactly, not reinterpreted. This task depends
on P5A-1, P5A-2, and P5A-3 all being merged and barrier-passed — you
reuse P5A-2's `run_capm_pilot` **unmodified**, at a different scale.

**Your job:** freeze a fixed ~30-name universe by the exact procedure
below, confirm entitlement for each name, run the same Gate A chain over
~12 months, and produce the same class of artifacts at this larger
scale — nothing more.

**Your working directory** will be a git worktree at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p5a-4-gate-b-scalability`
on branch `phase5a/task-p5a-4-gate-b-scalability`, branched from
`master` after P5A-3's barrier passes.

## File ownership

**You may create exactly:**
- `tests/test_capm_pilot_gate_b.py`
- `tests/fixtures/tiingo/phase5a_gate_b/`
- `tests/fixtures/risk_free/treasury_gate_b/` (a new, separate directory
  — you extend risk-free coverage to Gate B's window by recording a new
  fixture set through P5A-1's already-existing provider class, not by
  editing P5A-1's own `tests/fixtures/risk_free/treasury/` directory)
- `docs/phase5a/gate_b/` (the frozen universe snapshot/provenance record
  and the machine-readable outputs)
- `scripts/fetch_phase5a_gate_b_fixtures.py` (optional; a one-time,
  non-shipped, non-test recording script, same precedent as P5A-2's own
  `scripts/fetch_phase5a_gate_a_fixtures.py` — never imported by
  `smart_beta`, never collected by pytest — used to run the entitlement
  probes and record the live fetches below)

**You must not modify:**
- `smart_beta/pipelines/capm_pilot.py` (reuse `run_capm_pilot` exactly
  as P5A-2 built it — if it does not support what Gate B needs, that is
  a reported finding for a follow-up task, not something you patch)
- `smart_beta/research_inputs/risk_free_treasury.py`
- `tests/fixtures/risk_free/treasury/` or
  `tests/fixtures/tiingo/phase5a_gate_a/` (P5A-1/P5A-2's own fixtures —
  read-only)
- any other existing file
- `worker_tasks/phase5a/*`

## Frozen universe-construction procedure (do not deviate)

1. **Freeze the snapshot.** Record, with a stated date, the DJIA's
   publicly documented constituent list as of that date. The date and
   the list itself are both part of your committed provenance —
   independently verifiable by anyone, not sourced from this
   repository.
2. **Probe entitlement.** For each of the ~30 names, issue one bounded,
   real Tiingo request against the exact endpoint `total_mcap` depends
   on (`/tiingo/fundamentals/{ticker}/daily`) for a single date inside
   the intended Gate B window. Record the real HTTP outcome for every
   name.
3. **Record exclusions explicitly.** Any name whose probe fails (plan-
   tier block, delisting, ticker change, or any other real error) is
   **excluded and named, with its exact reason** — never silently
   dropped, never substituted with a different name to keep the count
   near 30.
4. **Freeze the surviving list before any return/factor computation.**
   Write it down, with its provenance, in `docs/phase5a/gate_b/` before
   calling `run_capm_pilot`.
5. **Hold it constant** for the entire Gate B window — no
   reconstitution, no substitution, even if a name is later found to
   have an issue inside the window (report that as a finding instead).

## Required terminology (binding, checked in the certification's
completeness test later — use it verbatim everywhere you write about
this universe)

**Always:** *"a fixed universe selected from a dated DJIA constituent
snapshot."*

**Never, anywhere in code, comments, artifacts, or your report:** "the
DJIA," "a historical DJIA universe," "a representative US market
portfolio," "a survivorship-safe index universe."

## If the frozen rule proves unworkable

If, at step 2, entitlement exclusions are so extensive that fewer than
roughly 15–20 names survive (making "approximately 30" not meaningfully
achievable), or if any other part of this procedure cannot be completed
without inventing new infrastructure (e.g., a dynamic index-membership
query), **stop and report this as an explicit design barrier** in your
completion report — do not silently substitute a different, unreviewed
universe-selection rule to force a "pass."

## Required artifacts (under `docs/phase5a/gate_b/`)

Same shape as P5A-2's Gate A artifacts (A: machine-readable `date,
universe_count, market_return, risk_free_return, MKT`; B: diagnostic
evidence including the frozen universe list with every exclusion and
reason; C: statistical summary via the same frozen `newey_west_ols`
convention), scaled to ~30 names / ~12 months, plus the universe-freeze
provenance record from step 4 above.

## Required tests

- Offline, fixture-replayed run of `run_capm_pilot` (unmodified) over
  the Gate B universe/window produces schema-conformant artifacts.
- An assertion that the committed universe list matches exactly what
  the provenance record says was frozen (no silent drift between the
  "frozen" list and what was actually run).
- An assertion that every excluded name from the entitlement probe
  appears, named, with its reason, somewhere in the diagnostic evidence.
- Zero live network calls in `pytest`.

## Live evidence requirement

New, real, live-recorded Tiingo entitlement probes (one call per
candidate name) plus real EOD/market-cap fetches for the surviving
universe over the Gate B window; a new, real DGS3MO fetch covering the
12-month window (via P5A-1's existing provider, recorded as a new
fixture set under your own directory).

## Failure behavior

Same upstream-defect rule as every other Phase 5A task: if running at
this larger scale exposes a defect in `run_capm_pilot`,
`compute_market_excess_return`, the Tiingo adapter, or any other
already-trusted module that P5A-2/P5A-3 did not surface at the smaller
scale, **do not patch it here.** Stop, report it as a specific, named,
scale-dependent finding, and leave Gate B's disposition as an honest
`NOT RUN — <defect>` rather than a silently-worked-around pass.

## Non-goals

No changes to `run_capm_pilot`'s implementation. No dynamic index-
membership infrastructure. No claim beyond "bounded fixed-universe
scalability." No transaction costs, no benchmark comparison.

## Exit criteria

- `.venv/bin/pytest` green, full suite unaffected.
- Zero live network calls in `pytest`.
- Either a complete Gate B artifact set with the frozen universe
  provenance and correct terminology throughout, or an explicit,
  clearly-reasoned design-barrier report — never a silent middle ground.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p5a-4-gate-b-scalability
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase5a/task-p5a-4-gate-b-scalability`, touching only the files listed
above.

## When done (completion-report format)

Report exactly:
(a) the frozen snapshot date and the full candidate list;
(b) every entitlement-probe result, one line per name, with the exact
    outcome (accessible / excluded + reason);
(c) the final frozen, surviving universe;
(d) whether the run completed or hit a design barrier, with full
    reasoning either way;
(e) any upstream defect found at this scale, named specifically, never
    fixed inline;
(f) confirmation of artifact paths;
(g) test results;
(h) `git diff --stat`.
Do not merge, do not touch `master`.
