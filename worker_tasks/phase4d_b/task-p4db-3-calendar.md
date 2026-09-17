# Phase 4D-B Worker Task — P4DB-3: China A-Share Trading Calendar (Wave 1)

## Background (read this first)

Read `worker_tasks/phase4d_b/phase4d-b-plan.md` in full before starting.
This is a Wave 1 task, independent of P4DB-1 and P4DB-2 — do not import
from either.

**Frozen policy, carried over from Phase 4B's equivalent decision
(`phase4b-plan.md` policy 4), applied here for the same reason:** the
trading calendar must come from an independent, authoritative source,
**never from Tushare row presence**. A basket of liquid securities'
`daily` rows may be used only as an optional sanity-check oracle, never
as the source of truth. This task has, by design, **zero Tushare/proxy
dependency** — it does not need `TushareClient` at all.

China's SSE (Shanghai) and SZSE (Shenzhen) exchanges share one trading
calendar (this must be verified, not assumed — check whether Tushare's
own `trade_cal` endpoint, if you choose to use it as the sanity-check
oracle only, ever shows a divergence between the two exchanges; if it
does, that is a significant, reportable finding, not something to average
away). The calendar closes for weekends and a fixed set of PRC public
holidays (New Year's Day, Spring Festival — a lunar-calendar holiday with
a multi-day closure that shifts every year, National Day/Golden Week,
Qingming, Labour Day, Dragon Boat Festival, Mid-Autumn Festival), plus
occasional one-off closures (e.g. a state mourning day). **Do not
hand-derive the lunar-calendar dates yourself** — use an authoritative,
versioned holiday data source (a maintained Python package such as
`chinese_calendar`/`workalendar`, or a hand-transcribed table sourced
from the CSRC/exchange's own published holiday notices, whichever is
already available or addable without a `pyproject.toml` change; if a new
dependency is genuinely required, flag it explicitly in your report
rather than silently adding it — Phase 4B's "no new dependency" policy is
a *default*, not an absolute rule for this task, but any addition must be
called out, not silent).

**Your working directory** will be a git worktree at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-3-calendar`
on branch `phase4d_b/task-p4db-3-calendar`, branched from `master` at
`4a3b859`.

## File ownership

**You may create exactly these files:**

- `smart_beta/vendors/tushare/calendar_source.py`
- `tests/test_tushare_calendar.py`
- `tests/fixtures/tushare/calendar/` (only if you use `trade_cal` as an
  optional sanity-check oracle — otherwise this task may have no
  fixtures at all)

**You must not modify** any other `vendors/tushare/*.py` file,
`smart_beta/pit/*`, `smart_beta/data/*`, or any existing test file.

## API shape to implement

```python
def build_china_a_share_calendar(start: date, end: date) -> TradingCalendar: ...
```

Read `smart_beta.pit.calendar.TradingCalendar` and its
`from_weekdays_excluding_holidays` constructor (used by Tiingo's
`calendar_source.py` — read that file for the exact pattern to mirror)
before designing your signature; match it unless you have a documented
reason to diverge.

## Required tests

- A known trading day (e.g. an ordinary Tuesday) is recognized as open.
- A known weekend is closed.
- A known Spring Festival closure window for at least one real year is
  closed (pick a year with unambiguous, independently documented dates).
- A known National Day/Golden Week closure window is closed.
- If you implement the `trade_cal` sanity-check oracle: cross-check your
  authoritative calendar against real `trade_cal` rows for at least one
  full year and report any divergence found — do not silently reconcile
  a divergence by trusting `trade_cal` over your authoritative source;
  report it as a finding for P4DB-9.

## Non-goals

No client, no identifiers, no market data. Do not attempt to model
intraday trading-halt schedules — this is a whole-day open/closed
calendar only, matching `TradingCalendar`'s existing contract.

## Acceptance criteria

- `.venv/bin/pytest` green, full suite unaffected.
- Zero live network calls in `pytest` (if you use `trade_cal` at all, it
  must go through a recorded fixture, never live).
- Any new dependency is explicitly flagged in your final report, not
  silently added to `pyproject.toml` without saying so (and if truly
  unavoidable, you may propose the `pyproject.toml` addition in your
  report for review rather than making it yourself, unless your task
  instructions are later amended to authorize it).

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-3-calendar
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch `phase4d_b/task-p4db-3-calendar`,
touching only the files listed above.

## When done

Report: (a) which holiday-data source you used and why; (b) whether SSE
and SZSE calendars were confirmed identical or found to diverge, with
evidence; (c) any `trade_cal` cross-check divergence found; (d) whether a
new dependency was added or only proposed; (e) test results; (f)
`git diff --stat`. Do not merge, do not touch `master`.
