# Phase 4D-B Worker Task — P4DB-7: Listing/Delisting (Wave 2)

## Background (read this first)

Read `worker_tasks/phase4d_b/phase4d-b-plan.md` in full. This is a Wave 2
task, depends on P4DB-1 and P4DB-2 (merged, Barrier 1 passed). Disjoint
from P4DB-4, P4DB-5, P4DB-6 — do not import from them.

Read Tiingo's `smart_beta/vendors/tiingo/listing.py` and
`phase4b-plan.md` policy 5 first: **row existence never implies
delisting on its own** — Tiingo's policy required a *sustained* trailing
zero-volume run (not one day) before asserting `delist_date`, because raw
`endDate` metadata alone was insufficient evidence, and the real TWTR
specimen's terminal zero-volume run (length 1) was *shorter* than the
frozen threshold (5), correctly producing `delist_date = NaT` — an
honest specimen limitation, not a bug, and it was never "fixed" by
lowering the threshold. **The same discipline applies here, adapted to
what China A-share data actually looks like.**

Unlike Tiingo, a delisted China A-share security does not keep emitting
zero-volume `daily` rows — trading simply stops, and rows stop appearing
entirely. Your corroboration signal is therefore different in shape but
the same in spirit: `stock_basic`'s own `delist_date`/`list_status`
field is the *primary* claim; it must be corroborated by a *sustained*
absence of `daily` rows after that date (checked against the
authoritative calendar from P4DB-3, not against Tushare's own trading-
day-implied calendar) before your adapter treats the delisting as
corroborated. Investigate and freeze an explicit minimum-gap threshold
(trading days, not calendar days) the same way Tiingo froze
`_MIN_CORROBORATING_ZERO_VOLUME_DAYS = 5` — pick a number, justify it
with a real specimen, and do not tune it merely to make a specific test
pass.

**Your working directory** will be a git worktree at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-7-listing`
on branch `phase4d_b/task-p4db-7-listing`, branched from `master` after
Barrier 1.

## File ownership

**You may create exactly:**

- `smart_beta/vendors/tushare/listing.py`
- `tests/test_tushare_listing.py`
- `tests/fixtures/tushare/listing/`

**You must not modify** any other `vendors/tushare/*.py` file,
`smart_beta/pit/*`, `smart_beta/data/*`, or any existing test file.

## Required investigation

1. Fetch `stock_basic` (with `list_status='L'`, `'D'`, and `'P'` if that
   third status exists — verify empirically what values `list_status`
   actually takes) for a real, confirmed-delisted A-share security.
   `002069.SZ` (獐子岛) and `002450.SZ` (康得新) are both known
   real-world distressed/restructured companies from this phase's
   fundamentals evidence — check whether either is actually delisted per
   `stock_basic`, or whether they remain listed despite their fraud
   history (verify, do not assume from general knowledge — the original
   Phase 4D-A spike explicitly warned against name/reputation-based
   assumptions substituting for direct evidence).
2. For whichever real specimen you find with `list_status='D'` and a
   `delist_date`, fetch `daily` for a window straddling that
   `delist_date` and confirm rows genuinely stop.
3. Freeze your minimum-trading-day-gap threshold using this real
   specimen, and test both that it corroborates correctly for this
   specimen and that a security still listed (`list_status='L'`, `daily`
   rows continuing) never has `delist_date` fabricated.

## Required output

`get_listing_info() -> pd.DataFrame` conforming to
`PIT_LISTING_INFO_SCHEMA` (`stock_id`, `list_date`; `delist_date`
present as `NaT` for still-listed securities). Per
`smart_beta.pit.source.PITDataSource.get_listing_info`'s contract: **must
not omit a delisted security or truncate its history** — a delisted
security's full pre-delisting history must remain queryable through
every other method. Do not implement any truncation logic here (that is
each other method's own concern if any, not this one's) — this method
only reports the list_date/delist_date facts themselves.

## Fixture inputs

- `stock_basic` with `list_status` covering all real values found.
- `daily` for your chosen delisted-specimen window (before/around/after
  the real or claimed `delist_date`).
- `daily` for at least one clearly-still-listed security's recent window
  (e.g. `000001.SZ`), as the negative control.

## Required tests

- Schema conformance.
- The real delisted specimen resolves to a corroborated `delist_date`
  (not `NaT`) if your evidence supports it — or, if your chosen
  candidates turn out to still be listed, find and use whatever real
  delisted specimen you can actually confirm; if none is reachable under
  current proxy access, report that explicitly (`DELISTING
  CORROBORATION = NOT CERTIFIED — no reachable delisted specimen`)
  rather than fabricating one, mirroring Tiingo's honest TWTR outcome.
- A clearly-still-listed security never gets a fabricated `delist_date`.
- A `stock_basic`-claimed `delist_date` with an insufficient trailing gap
  (below your frozen threshold) does **not** get corroborated —
  construct this case if no real specimen naturally has it, and document
  that it's constructed.

## Non-goals

No market data, no fundamentals, no corporate actions.

## Acceptance criteria

- `.venv/bin/pytest` green, full suite unaffected.
- Zero live network calls in `pytest`.
- Your frozen minimum-gap threshold and its justification are stated
  explicitly in a module docstring, not left implicit.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-7-listing
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch `phase4d_b/task-p4db-7-listing`,
touching only the files listed above.

## When done

Report: (a) what `list_status` values were actually found and what they
mean; (b) whether a real, reachable delisted specimen was found, and if
so which one; (c) your frozen minimum-gap threshold and its
justification; (d) the `DELISTING CORROBORATION` status to carry into
P4DB-9, stated explicitly; (e) test results; (f) `git diff --stat`. Do
not merge, do not touch `master`.
