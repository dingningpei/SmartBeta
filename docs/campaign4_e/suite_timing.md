# Suite timing finding (planner measurement, 2026-10-14; master `09296ef`)

## Measurement

- **Command:** `scrubrun.sh -m pytest -q -p no:cacheprovider --durations=60`, run solo on
  the laptop with credentials scrubbed and no network.
- **Result:** 6787 passed, 1 skipped, **838 s** wall.

## Where the time goes

`tests/test_phase5a_certification.py::test_item12_full_regression` takes **421 s**, half of
the wall time.
- **What it does:** it re-runs the whole suite, minus its own file, in a subprocess with
  `timeout=900`.
- **Why:** it is the executable authority of the sealed Phase-5A certification item
  [12] (`docs/phase5a_capm_certification.md`). So every full run executes every other
  test **twice**.
- **The next slowest test** takes 20 s. No other test exceeds 16 s.

## Correction to the Campaign-3 closeout §6.6

- **What the bound covers:** the "900 s Phase-5A bound" is item 12's **subprocess
  timeout** over the *inner* suite. That run now takes about 420 s, so the headroom is
  about 2.1×.
- **What was wrong:** the closeout compared the *outer* wall time (805 s) against 900 s.
  That comparison was a planner misreading, and it overstated the problem.

## Disposition

- **E4 needs no code change.**
- `test_phase5a_certification.py` belongs to a sealed phase and is **not modified**.
- **Routine pre-merge checks** may run:

      pytest --deselect tests/test_phase5a_certification.py::test_item12_full_regression

  That takes about 420 s and runs every test once.
- **Barrier and certification runs** keep the full command, with item 12 included.
- **Watch threshold:** if item 12's inner run exceeds **750 s**, stop and plan. Any change
  to the sealed test needs explicit user approval.
