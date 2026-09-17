# Phase 4D-B Worker Task — P4DB-2: Identifiers (Wave 1)

## Background (read this first)

Read `worker_tasks/phase4d_b/phase4d-b-plan.md` in full before starting.
This is a Wave 1 task, independent of P4DB-1 (client) and P4DB-3
(calendar) — do not import from either; assume neither exists in your
worktree, and use only recorded fixtures you build yourself (via a small
`fetch()`-shaped stub in your own test file, not the real client).

**Your job:** decide and implement the `ts_code -> stock_id` mapping
policy, and **empirically re-investigate identifier continuity** — the
original Phase 4D-A live spike found a real corporate-restructuring
specimen (`000024.SZ`, superficially resembling `001914.SZ`) that turned
out to be an *unrelated entity* on inspection, evidence that naive
brand/name-based continuity assumptions are actively wrong. The frozen
instruction from that spike was: return `NOT CERTIFIED` rather than build
a heuristic mapping. Your job is not to overturn that finding by
assumption — it is to check, with a bounded, real investigation, whether
`stock_basic`'s own fields (`ts_code`, `name`, `list_date`,
`delist_date`, and any `list_status`/change-history field it exposes)
give a *direct, vendor-asserted* continuity signal for a merger/
restructuring case, as opposed to the heuristic (name-similarity)
approach that was already shown to be wrong.

**Your working directory** will be a git worktree at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-2-identifiers`
on branch `phase4d_b/task-p4db-2-identifiers`, branched from `master` at
`4a3b859`.

## File ownership

**You may create exactly these files:**

- `smart_beta/vendors/tushare/identifiers.py`
- `tests/test_tushare_identifiers.py`
- `tests/fixtures/tushare/identifiers/`

**You must not modify** `client.py`, `proxy_client.py`,
`calendar_source.py`, `market_data.py`, `corporate_actions.py`,
`fundamentals.py`, `listing.py`, `source.py`, `smart_beta/pit/*`,
`smart_beta/data/*`, or any existing test file.

## API shape to implement

```python
@dataclass(frozen=True)
class ResolvedIdentifier:
    stock_id: str
    is_permanent: bool     # mirrors Tiingo's flag: True only if backed by
                            # a vendor-asserted stable identifier, never a
                            # ticker-symbol guess
    raw_ts_code: str

def resolve_stock_id(ts_code: str, stock_basic_row: dict | None = None) -> ResolvedIdentifier: ...
```

`ts_code` (e.g. `"000001.SZ"`) is Tushare's own namespaced identifier
(exchange-suffixed). The baseline policy (unless your investigation finds
a reason to change it) is `stock_id = ts_code`, `is_permanent = True` for
the common case — `ts_code` is exchange+code, stable under ordinary
circumstances, unlike Tiingo's ticker-only fallback. **The open question
your investigation must resolve empirically is what happens under
restructuring/relisting**, not the common case.

## Required investigation (bounded — do not crawl the full market)

1. Fetch `stock_basic` (with `fields` covering at minimum `ts_code, name,
   list_date, delist_date, list_status`) for `000024.SZ` and `001914.SZ`
   specifically — these are the two tickers from the original spike's
   name-similarity false positive. Confirm independently (do not assume
   from memory) whether Tushare's own data treats them as
   related/successor entities or as fully unrelated, using only fields
   `stock_basic` actually returns — not name-string comparison.
2. Check whether `stock_basic` (or any other reachable endpoint,
   `namechange` in particular — confirmed reachable in the earlier
   investigation) exposes an explicit prior-identifier/renaming record
   that would let a *vendor-asserted* (not heuristic) continuity mapping
   be built for a genuine ticker-code change (as opposed to an unrelated
   new listing reusing a similar-looking code). `namechange` was
   confirmed to return `ts_code, name, start_date, end_date, ann_date,
   change_reason` in the original spike (not yet field-verified this
   phase — verify).
3. If `namechange` (or another field) gives a direct, vendor-asserted
   old-code → new-code mapping for at least one real specimen, implement
   it and set `is_permanent=True` for that mapped case, with a passing
   test using the real specimen. **If no such direct signal exists, or
   only name-similarity heuristics are available, do not implement a
   heuristic mapping — leave `resolve_stock_id` as the direct `ts_code`
   passthrough and carry `is_permanent=False` semantics for any case
   that would otherwise require guessing.**

## Fixture inputs

- `stock_basic` for `000024.SZ`, `001914.SZ`, and at least 3 arbitrary
  active large-caps (e.g. `000001.SZ`, `600519.SH`, `601318.SH`) for the
  common-case tests.
- `namechange` for `000024.SZ` and `001914.SZ` (and any ticker your
  investigation surfaces as a genuine rename case, if one is found).

## Required tests

- Common case: `resolve_stock_id("000001.SZ")` → `stock_id="000001.SZ"`,
  `is_permanent=True`.
- The `000024.SZ`/`001914.SZ` specimen: assert the actual, empirically-
  confirmed relationship (unrelated, or vendor-asserted-related — whatever
  your investigation finds), not an assumed one.
- If a genuine rename mapping is implemented: a real before/after
  specimen resolving to the same `stock_id`.
- If no rename mapping is implemented: a test asserting the passthrough
  behavior and that `is_permanent` is `False` for a case lacking direct
  vendor evidence (do not skip this test — an empty result is still a
  result to assert on).

## Non-goals

No calendar, no client, no fundamentals, no market data. Do not build a
general Chinese-market corporate-action-driven identifier graph — that is
explicitly out of scope; a single, narrow, evidence-driven policy is all
this task authorizes.

## Acceptance criteria

- `.venv/bin/pytest` green, full suite unaffected.
- The certification-relevant finding (whether `IDENTIFIER CONTINUITY`
  status changes from `NOT CERTIFIED`, or is carried forward unchanged)
  is stated explicitly in your final report — this feeds P4DB-9 directly
  and must not be left implicit in code comments alone.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-2-identifiers
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4d_b/task-p4db-2-identifiers`, touching only the files listed
above.

## When done

Report: (a) the exact `ResolvedIdentifier`/`resolve_stock_id` signature
implemented; (b) the `000024.SZ`/`001914.SZ` finding, verbatim data, and
whether it changes `IDENTIFIER CONTINUITY`'s status from `NOT CERTIFIED`
carried forward in the plan — state explicitly `IDENTIFIER CONTINUITY =
<status>` with your evidence; (c) whether a `namechange`-based mapping
was implemented, and why or why not; (d) test results; (e)
`git diff --stat`. Do not merge, do not touch `master`.
