# Phase 4C Worker Task — P4C-5: Inject TradabilityPolicy into build_tradable_universe

## Background (read this first)

Read `worker_tasks/phase4c/phase4c-plan.md` in full first. This task
starts only after P4C-2 is merged and Barrier 1 is green. Confirm your
worktree's `master` merge-base contains
`smart_beta/research_inputs/tradability.py` before starting; if it does
not, stop and report rather than proceeding against a stale base.

**Your task, P4C-5, is the single amendment to
`smart_beta/data/universe.py`.** It is the sole owner of this file and of
`tests/test_universe.py` for the entire Phase 4C DAG — no other task
touches either. Read the actual current
`build_tradable_universe` implementation in full before writing anything;
this spec describes the *change*, not a replacement you should write from
scratch.

**The critical requirement, stated plainly: when no policy is supplied,
behavior must remain numerically and structurally identical to today,
for every existing fixture in `tests/test_universe.py`.** You must prove
this, not merely leave the existing tests passing by accident — see
Required Tests below.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-5-universe-policy-injection`
on branch `phase4c/task-p4c-5-universe-policy-injection`, branched from
`master` after P4C-2 merges. Run tests with `.venv/bin/pytest`.

## File ownership

**You may modify/create exactly these:**

- `smart_beta/data/universe.py` (amend)
- `tests/test_universe.py` (amend — additive only; every existing test
  function must remain, unmodified in its assertions, unless you find
  one that was already testing something this change makes obsolete —
  if so, report it rather than silently deleting it)

**You must not modify anything else**, including
`smart_beta/research_inputs/tradability.py` (P4C-2's merged, frozen
deliverable — you consume `TradabilityPolicy`/`ChinaAShareTradabilityPolicy`,
you do not change them), `smart_beta/data/schema.py`,
`smart_beta/pipelines/*`, `smart_beta/benchmarks/*`, or `pyproject.toml`.

## The API you consume (already merged — read the actual file)

```python
# smart_beta.research_inputs.tradability (P4C-2, frozen)
class TradabilityPolicy(abc.ABC):
    def evaluate(self, trading_status, listing_info, market_cap, keys, settings) -> pd.DataFrame: ...

class ChinaAShareTradabilityPolicy(TradabilityPolicy): ...
class USZeroVolumeTradabilityPolicy(TradabilityPolicy): ...
```

(Exact signatures may have shifted slightly during P4C-2's actual
implementation — read the real merged file.)

## What to build

Amend `build_tradable_universe`'s signature to accept an optional policy,
defaulting to the China policy:

```python
def build_tradable_universe(
    returns: pd.DataFrame,
    market_cap: pd.DataFrame,
    trading_status: pd.DataFrame,
    listing_info: pd.DataFrame,
    settings: Settings = DEFAULT_SETTINGS,
    *,
    policy: "TradabilityPolicy | None" = None,
) -> pd.DataFrame:
    """... existing docstring, plus:

    `policy` defaults to `ChinaAShareTradabilityPolicy()` when omitted --
    every existing caller that does not pass `policy` gets numerically
    and structurally identical output to before this change. When
    `policy` is supplied, that policy's `evaluate(...)` result drives
    `is_tradable` instead of this function's own inline logic.
    """
```

Move the current inline tradability logic (listing age, four-flag check,
bottom-cap exclusion) so that, functionally, it is exactly what
`ChinaAShareTradabilityPolicy.evaluate` already does (P4C-2 extracted it
faithfully) — you are wiring the *call*, not re-deriving the logic a
second time in this file. Do not duplicate the logic inline and also call
the policy; pick one (the policy) and delete the duplicate from this
file, so there is exactly one implementation of the China rule in the
codebase after this task, in `research_inputs/tradability.py`.

Keep the existing top-level validation (`validate_panel` calls,
`missing_flags` check) — those apply regardless of which policy is used,
since they check the *shape* of the inputs, not the tradability rule
itself. Only the actual tradability *decision* moves to the policy.

## Required tests

1. **Behavior preservation, exhaustively.** For every existing scenario
   in `tests/test_universe.py` (listing age, delisted stock, each of the
   four flags individually, bottom-cap cutoff recomputed per date,
   missing flag, missing market cap), call `build_tradable_universe`
   with **no `policy` argument** and assert the exact same `is_tradable`
   values the function produces today — do not just re-run the existing
   assertions (which would trivially pass if you accidentally left the
   old inline logic in place *and* added an unused policy parameter);
   additionally assert that calling with
   `policy=ChinaAShareTradabilityPolicy()` explicitly produces the
   identical result to calling with no `policy` at all, proving the
   default really does delegate to that policy and isn't a second,
   independently-maintained code path that merely happens to agree
   today.
2. **Explicit `USZeroVolumeTradabilityPolicy` usage works end to end**
   through `build_tradable_universe` — construct a small
   Tiingo-shaped `trading_status` frame (only `is_zero_volume`, no China
   columns) and confirm `build_tradable_universe(..., policy=
   USZeroVolumeTradabilityPolicy())` succeeds and produces a sensible
   `is_tradable` column, without requiring the four China columns to be
   present at all.
3. **The old four-flag hard requirement no longer blocks a
   non-China policy.** Confirm that calling with the US policy and a
   trading-status frame lacking `is_suspended`/etc. does **not** raise
   the `missing_flags` `ValueError` the current code raises
   unconditionally today — that check must become policy-aware (only
   enforced when the China policy's required columns are actually the
   ones in use), or moved into the policy's own `evaluate` (which P4C-2
   may or may not have already done — check the real merged code and
   adapt accordingly; report which you found).
4. Output schema/columns are unchanged (`date, stock_id, is_tradable`)
   for the default path; if a non-default policy's diagnostic columns
   need to surface, decide (and document) whether
   `build_tradable_universe` passes them through or trims back to the
   three canonical columns — either is acceptable as long as it's
   consistent and documented, but the **default (China) path's output
   must be column-identical to today's**, unconditionally.

## Non-goals

- Do not modify `TradabilityPolicy` or either of its implementations.
- Do not introduce any US-specific column into the default China code
  path.
- Do not change `TRADABLE_COL`'s name or the function's other parameters.

## Acceptance criteria

- All pre-existing tests (including every test in `tests/test_universe.py`
  unmodified in its assertions) continue to pass.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/data/universe.py` and
  `tests/test_universe.py`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-5-universe-policy-injection
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4c/task-p4c-5-universe-policy-injection`, touching only the two
files listed above.

## If you discover a contract contradiction

Stop and report it rather than improvising.

## When done

Report: (a) the exact new `build_tradable_universe` signature; (b) how
you resolved the `missing_flags` hard requirement's interaction with a
non-China policy (item 3 above) and which approach you found already
built into P4C-2's merged code, if any; (c) confirmation of behavior
preservation, with specifics; (d) test results; (e) `git diff --stat`.
Do not merge, do not touch `master`, do not modify files outside the two
files above.
