# Phase 4D-B Worker Task — P4DB-5: Corporate Actions (Wave 2)

## Background (read this first)

Read `worker_tasks/phase4d_b/phase4d-b-plan.md` in full, especially
evidence item 7 (changing adj_factor) and requirement 10's adversarial
fixture list. This is a Wave 2 task, depends on P4DB-1 and P4DB-2
(merged, Barrier 1 passed). Disjoint from P4DB-4, P4DB-6, P4DB-7 — do
not import from them.

**Your job:** map Tushare's `dividend` and `adj_factor` data to
`CORPORATE_ACTIONS_SCHEMA`. Read `smart_beta/pit/schema.py`'s
`CORPORATE_ACTIONS_SCHEMA` and its bitemporal decision table entry for
corporate actions (`effective_date`, `knowledge_date`, `is_superseded`)
before writing any mapping.

**Your working directory** will be a git worktree at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-5-corporate-actions`
on branch `phase4d_b/task-p4db-5-corporate-actions`, branched from
`master` after Barrier 1.

## File ownership

**You may create exactly:**

- `smart_beta/vendors/tushare/corporate_actions.py`
- `tests/test_tushare_corporate_actions.py`
- `tests/fixtures/tushare/corporate_actions/`

**You must not modify** any other `vendors/tushare/*.py` file,
`smart_beta/pit/*`, `smart_beta/data/*`, or any existing test file.

## The proven real specimen (build your certification fixture from this)

`000001.SZ`, `dividend` record for `end_date=20121231`: `stk_div=0.6`
(10-for-6 bonus shares), `cash_div=0.1315`, `ex_date=20130620`,
`ann_date=20130524` (and a second row `ann_date=20130308` for the same
event — the board-proposal announcement vs. the shareholder-meeting-
approved announcement; investigate which one is the correct
`knowledge_date` for this fact — likely the later, confirmed one, but
verify against `effective_date`/`ex_date` ordering: `knowledge_date` must
never exceed `effective_date` in a way that would make the action
"knowable after it already happened" nonsensically, and in practice
corporate actions are typically announced well before their ex-date, so
both candidate ann_dates here legitimately precede `ex_date=20130620` —
pick the policy and document it, e.g. "use the earliest ann_date at which
the exact terms (`stk_div`, `cash_div`) shown in this row were fixed," and
verify empirically whether the two ann_date rows differ in terms or only
in process stage).

Confirmed, numerically reconciled evidence for this exact event:
- `daily_basic.total_share` for `000001.SZ`: `512,335.0` through
  2013-06-18, `819,736.0` from 2013-06-20 — ratio exactly `1.6`, matching
  `1 + stk_div = 1.6`.
- `adj_factor` for `000001.SZ`: `36.173` through 2013-06-19, `58.387`
  from 2013-06-20 — ratio `1.614`, consistent with the combined
  stock+cash adjustment (`(prev_close - cash_div) / (1 + stk_div)` as the
  ex-rights reference price formula — verify this reconciliation
  numerically in your own test using the real `daily.pre_close` value on
  2013-06-20, which was confirmed as `11.92`, against
  `daily.close`=`19.24` on 2013-06-17/18).

## Required mapping

```python
def map_dividend_to_corporate_actions(rows: ...) -> pd.DataFrame: ...
def map_adj_factor_to_corporate_actions(rows: ...) -> pd.DataFrame: ...
# or a single combined function -- your call, document the choice
```

`action_type` should distinguish stock dividend/bonus (`stk_div > 0`)
from pure cash dividend (`cash_div > 0`, `stk_div == 0`) from combined —
pick a small, closed enum and document it (mirror Tiingo's
`split`/`dividend` `action_type` values if `corporate_actions.py`'s
existing constants suggest a compatible naming convention — check
`smart_beta/vendors/tiingo/corporate_actions.py` for precedent before
inventing new strings). `adjustment_factor` is derived per your
documented formula (frozen, not left ambiguous — mirror Phase 4B's
practice of freezing the exact formula in the spec, which policy 11 in
`phase4b-plan.md` did for Tiingo's split/dividend factors).

## Fixture inputs

- `dividend`, `000001.SZ`, full history (116 records confirmed
  reachable, no date params needed) — use the 2012/2013 bonus-share
  event as your primary specimen, plus at least 2 more distinct events
  from the same real history (e.g. the 2015-06-16 or 2014-06-12 bonus
  events) for breadth.
- `adj_factor`, `000001.SZ`, `20130101`-`20131231` — the confirmed real
  step change.
- `daily`, `000001.SZ`, `20130601`-`20130701` — for the raw-price
  reconciliation test (narrow window around the event).

## Required tests

- Schema conformance to `CORPORATE_ACTIONS_SCHEMA`.
- The real 2013-06-20 event: correct `effective_date` (`ex_date`),
  correct `knowledge_date` per your documented policy,
  `adjustment_factor` matching your frozen formula to a documented
  tolerance, cross-checked against the real `adj_factor` ratio
  (`58.387/36.173 ≈ 1.614`).
- At least one additional real bonus-share event from the same
  `000001.SZ` history, independently mapped and checked for internal
  consistency (share-count ratio vs. `stk_div`).
- A pure cash-dividend-only row (`stk_div=0`, e.g. the `20230824`
  `cash_div=0.0`/`None` rows or a real nonzero-cash-only row from the
  history) maps without a spurious stock-split factor.

## Non-goals

No fundamentals, no market data, no listing logic. Do not attempt to
adjust `get_raw_returns`'s output yourself — raw facts only, adjustment
application is a separate, already-existing concern
(`smart_beta.pit.corporate_actions`, unmodified, consumes this schema
downstream — read it, do not duplicate its logic here).

## Acceptance criteria

- `.venv/bin/pytest` green, full suite unaffected.
- Zero live network calls in `pytest`.
- The frozen `adjustment_factor` formula is stated in a module docstring
  in `corporate_actions.py`, not left implicit in code.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-5-corporate-actions
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4d_b/task-p4db-5-corporate-actions`, touching only the files
listed above.

## When done

Report: (a) the exact `action_type` enum and `adjustment_factor` formula
chosen, with rationale; (b) the `ann_date` (knowledge_date) policy
decision for the dual-announcement-date case, with evidence of whether
the two ann_date rows differed in terms; (c) the numerical reconciliation
result against the real `adj_factor`/`total_share`/raw-price evidence;
(d) test results; (e) `git diff --stat`. Do not merge, do not touch
`master`.
