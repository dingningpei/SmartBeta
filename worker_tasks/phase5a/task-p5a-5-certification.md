# Phase 5A Worker Task — P5A-5: Certification / Findings

## Background (read this first)

**This file is the single normative source for the required Phase 5A
certification line-item list**, mirroring the discipline
`worker_tasks/phase4d_b/task-p4db-9-certification.md` established (that
file's own doctrine, quoted for this task too): *"a markdown-presence
check exists only as a synchronization guard alongside the executable
checks, never as the sole proof of a claim."* Every disposition below
must correspond to a real, executable assertion in
`tests/test_phase5a_certification.py` against the real artifacts P5A-1
through P5A-4 actually produced — never a re-statement of what the plan
*expected* without re-checking it.

This is the final task in the sequential chain — depends on P5A-1
through P5A-4 all merged and barrier-passed. You are the only task in
this final step.

**Your working directory** will be a git worktree at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p5a-5-certification`
on branch `phase5a/task-p5a-5-certification`, branched from `master`
after P5A-4's barrier passes.

## File ownership

**You may create exactly:**
- `docs/phase5a_capm_certification.md`
- `tests/test_phase5a_certification.py`

**You must not modify:**
- any `smart_beta/*` production file
- any P5A-1 through P5A-4 test file or fixture
- `worker_tasks/phase5a/*`

## Required certification report content (every line below, backed by a real executable check)

1. `GATE A DISPOSITION = <PASS/FAIL/NOT RUN — reason>` — verified by
   re-loading P5A-2's real committed artifacts and asserting the chain
   actually produced a non-trivial `MKT` series (not an all-`NaN` or
   empty result) without any recorded upstream-defect finding blocking
   it.
2. `GATE A CLAIM = REAL-DATA END-TO-END EXECUTION` — and, verbatim, the
   full **not-demonstrated** list: *"Gate A does not demonstrate
   representative market coverage, scalability, a US market factor,
   CAPM replication, economic significance, or statistical
   significance."* This exact sentence (or a whitespace-normalized
   match of it) must appear in the report.
3. `GATE B DISPOSITION = <PASS/FAIL/DESIGN BARRIER — reason>` — verified
   against P5A-4's actual completion report and committed artifacts,
   including whichever of the two honest outcomes actually occurred.
4. `GATE B CLAIM = BOUNDED FIXED-UNIVERSE SCALABILITY` (only if Gate B
   passed; if it hit a design barrier, this line instead states that
   explicitly) — **and a required negative check**: the whole report,
   and P5A-4's own artifacts, must **never** contain any of: "the DJIA",
   "a historical DJIA universe", "a representative US market portfolio",
   "a survivorship-safe index universe" (case-insensitive substring
   check) — a literal, executable grep-style assertion, not a prose
   promise.
5. `HAND VERIFICATION = <PASS/FAIL>` — re-run P5A-3's actual test and
   cite the exact date and tolerance it verified.
6. `RISK-FREE TRANSFORMATION = <formula, stated exactly>` — the frozen
   formula from `phase5a-plan.md`, plus the evidence-limitation sentence
   preserved **verbatim**: *"the short-maturity/simple-interest
   interpretation of DGS3MO is supported by mutually consistent
   Treasury.gov, FRED, and academic documentation, but the primary
   Treasury Yield Curve Methodology technical publication has not been
   directly read in full."* Required verbatim (whitespace-normalized)
   match, checked the same way P4DB-9 checked its own frozen caveat
   sentences.
7. `RISK-FREE STALENESS/NO-FUTURE-LEAKAGE = <PASS/FAIL>` — re-cite
   P5A-1's real staleness and no-future-leakage test results.
8. `NEWEY-WEST CONVENTION = lag 6 (DEFAULT_SETTINGS.newey_west_lags)` —
   plus the required verbatim caveat: *"this is a frozen Phase 5A
   diagnostic convention inherited from existing machinery, not a
   certification that lag 6 is the statistically optimal HAC bandwidth
   for daily observations."* Required verbatim match.
9. `UPSTREAM DEFECT AUDIT = <NONE FOUND / list each named finding>` —
   a real cross-task audit of every P5A-1 through P5A-4 completion
   report for a recorded upstream-defect finding; if any exists, it
   must be listed here, not omitted because it makes the phase look
   less clean.
10. `EVIDENCE TAXONOMY AUDIT = <PASS/FAIL>` — confirm every artifact
    across P5A-1 through P5A-4 correctly labels itself as one of
    `LIVE-RECORDED` / `FIXTURE-REPLAYED` / `CONTRACT-MODELED` /
    `CONSTRUCTED` / `HAND-VERIFIED` / `NOT CERTIFIED`, and that no
    artifact's own claim exceeds the gate-level claim boundary it
    belongs to (e.g., nothing in Gate A's artifacts describes a "market
    factor").
11. `CREDENTIAL LEAKAGE = <PASS/FAIL>` — a real regex sweep (mirroring
    P4DB-9's token-pattern sweep exactly) across every file P5A-1
    through P5A-4 added, confirming no credential-shaped string is
    committed anywhere.
12. `FULL REGRESSION = <PASS/FAIL>` — the complete existing test suite,
    run fresh, on the fully integrated Phase 5A branch state.
13. `RF FORMULA INDEPENDENCE = <PASS/FAIL>` (resolves false-PASS
    scenario G — closes the B2 tautology gap) — this certification's own
    test file independently constructs `TreasuryBillRiskFreeProvider`
    against P5A-1's committed fixture and asserts its output against
    frozen literal expected values **computed here, in this file, with
    plain Python arithmetic on literals** (`0.05 * 1 / 365`,
    `0.05 * 3 / 365`, etc.) — never copied from, or re-derived by
    calling, P5A-1's own test file or the provider under test. Include
    the `/252`-compounding negative guard here too, independently of
    whether P5A-1's own test included one. This gives a second,
    differently-authored check that would fail even if P5A-1's own test
    were tautological.
14. `CAPM SINGLE-AUTHORITY AUDIT = <PASS/FAIL>` (resolves false-PASS
    scenario I — closes the B1 duplication gap) — a literal source-grep
    of `smart_beta/pipelines/capm_pilot.py`, confirming it contains no
    reference to any `smart_beta/benchmarks/capm.py` private symbol
    (`_market_factor`, `_value_weighted_returns`, `_value_weighted_by`,
    `_load_pit_panel`), and a re-run of the
    `derived_market_return - risk_free_return == MKT` invariant test
    against the real committed Gate A (and, if passing, Gate B) output.

## The completeness invariant test

Mirror `test_phase4d_b_certification.py`'s pattern exactly: parse
`docs/phase5a_capm_certification.md` and assert (a) all 14 items are
present with an explicit disposition, and (b) items 2, 6, and 8's
required verbatim sentences are present exactly (whitespace-normalized),
and (c) item 4's forbidden-terminology sweep finds nothing. State
explicitly in this test file's own docstring, exactly as P4DB-9's does,
that this markdown-parsing test is a synchronization guard only — the
real proof for each item is its own named executable check elsewhere in
this file.

## Explicit reviewer obligations (D and E remain reviewer-enforced, not
mechanically enforced — state this plainly in the report, do not imply
otherwise)

- **Item 5 (hand verification)** must also record that the reviewer
  read `tests/test_phase5a_hand_verification.py`'s own source and
  confirmed it reads only raw fixture paths plus the single `MKT`
  comparison value from P5A-2's Artifact A — never P5A-2's Artifact B
  diagnostic evidence (per `task-p5a-3...md`'s independence boundary).
  This is a stated review-time judgment, not a grep result.
- **Item 3 (Gate B disposition)** must also record that the reviewer
  inspected P5A-4's commit/diff structure and completion-report
  narrative for consistency with a genuine freeze-before-computation
  ordering (per `task-p5a-4...md`'s explicit reviewer obligation) —
  again a stated review-time judgment, not a mechanical proof.

## Non-goals

No new empirical claims beyond what P5A-1 through P5A-4 already
produced. No new production code. No fixing of any finding those tasks
recorded — report it here, do not resolve it.

## Failure behavior

If any of P5A-1 through P5A-4's own claims cannot be independently
re-verified against their actual committed artifacts (not just trusted
from their completion reports), the corresponding certification item
must read `NOT RUN — reason`, never a copied-forward `PASS` that wasn't
actually re-checked.

## Exit criteria

- `.venv/bin/pytest` green, full suite unaffected.
- All 14 items present, each backed by a real executable check.
- The three required verbatim/negative checks (items 2, 4, 6, 8) pass,
  plus item 13's independent `/252` negative guard and item 14's
  source-grep.
- The Phase 5A final barrier (per `phase5a-plan.md`'s 17-point list, see
  point 17 added for the B1/B4 corrections) is explicitly evaluated
  point by point in this report, not just implied by the 14 items above.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p5a-5-certification
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit on branch `phase5a/task-p5a-5-certification`, touching only
the two files listed above.

## When done (completion-report format)

Report exactly: (a) the full 14-item certification document verbatim;
(b) explicit evaluation of all 17 final-barrier points from
`phase5a-plan.md`; (c) any status that required re-checking rather than
trusting a prior task's own report, with what you found; (d) the full
list of upstream-defect findings (if any) across the whole phase; (e)
test results; (f) `git diff --stat`. Do not merge, do not touch
`master`.
