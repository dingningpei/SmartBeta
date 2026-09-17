# Phase 4B Worker Task — P4B-R1: Replay Request Identity

## Background (read this first)

Master is at `f716b1a` (P4B-8 merged, 690/690 tests green). Read
`worker_tasks/phase4b/phase4b-plan.md` for overall context.

**The problem, exactly as found during the independent P4B-8 review.**
`TiingoClient.get_fundamentals_asreported` and `get_fundamentals_normalized`
(P4B-1, frozen) hit the *identical* URL path
(`/tiingo/fundamentals/{ticker}/statements`), differing only in the
`asReported` query parameter. `replay_transport` (P4B-1, frozen) keys its
lookup on request path only, by design (`smart_beta/vendors/tiingo/client.py`),
so it cannot return two different bodies for that one path. P4B-8's own
tests worked around this with a small, local, test-file-only wrapper
(`tests/test_tiingo_source.py`'s `_make_client()`); this is real,
reported, correctly isolated to that one test file, and not itself
broken — but it means every future task that needs both statement bodies
in one fixture-fed client (starting with P4B-9) would otherwise have to
reinvent the same workaround. This task fixes the shared infrastructure
once instead.

**This is a test/replay-infrastructure fix, not a production-semantics
fix.** Tiingo's real, live API already differentiates these two calls
correctly by the query parameter -- nothing about production behavior is
broken. Only the *offline test double* needs to learn to do the same.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-r1-replay-request-identity`
on branch `phase4b/task-p4b-r1-replay-request-identity`, branched from
`master` at `f716b1a`. Run tests with `.venv/bin/pytest`.

## File ownership

**You may modify/create exactly these:**

- `smart_beta/vendors/tiingo/client.py` (amend — `replay_transport`
  only; see scope below)
- `tests/test_tiingo_client.py` (amend)

**You must not modify anything else.** In particular:
- Do not modify `TiingoClient` itself (its five data methods, `_call`,
  `_live_transport`, `_fundamentals_params`) -- this task touches only
  `replay_transport`.
- Do not modify `tests/test_tiingo_source.py` or any other test file that
  already uses `replay_transport` -- every existing call site (dozens,
  across P4B-1/2/4/5/6/7/8's tests) must keep working completely
  unchanged. If P4B-8's own `_make_client()` workaround could now be
  simplified using your new mechanism, that is a future task's decision
  to make, not yours -- do not touch that file.
- Do not modify any `smart_beta/vendors/tiingo/*.py` module other than
  `client.py`.
- Do not modify `smart_beta/pit/*` or `pyproject.toml`.
- Do not add a new dependency.

## The API you amend (read the actual file first)

```python
# smart_beta/vendors/tiingo/client.py, already merged
Transport = Callable[[str, Mapping[str, str]], "tuple[int, object]"]

def replay_transport(
    recordings: Mapping[str, "tuple[int, object]"],
) -> Transport:
    """Deterministic, network-free Transport from recorded
    {request_path: (status_code, parsed_body)} specimens. Keys on path
    only -- CURRENTLY cannot distinguish two recordings sharing one path
    that differ only by query parameter. This is the function you fix."""
```

## What to build

Extend `replay_transport` with a small, general, backward-compatible
mechanism for distinguishing requests that share a path but differ by
one relevant query parameter -- not an AAPL-only or statements-only
special case; any future path/param pair must be able to reuse it
unchanged.

A workable shape (adjust if you find a cleaner one that meets every
requirement below, but keep it this small and this general):

```python
def replay_transport(
    recordings: Mapping[str, "tuple[int, object]"],
    *,
    param_recordings: Mapping[tuple[str, str, str], "tuple[int, object]"] | None = None,
) -> Transport:
    """Deterministic, network-free Transport from recorded specimens.

    `recordings`: {request_path: (status_code, body)} -- unchanged,
    exact-path lookup, for the common case of one recording per path.
    This parameter's meaning and every existing call site using only it
    must keep working identically.

    `param_recordings`: optional {(path, param_name, param_value):
    (status_code, body)} -- for the rarer case where two or more
    recordings share one path and are distinguished only by one query
    parameter's value (e.g. Tiingo's asReported=true vs. its absence on
    the same /statements path). Checked first, as the more specific
    match; falls back to `recordings` by path alone when no
    param_recordings entry matches. `param_name`/`param_value` compare
    against `str(params.get(param_name))`, so both string and
    boolean-ish query values match consistently.

    Raises KeyError naming the unmatched path (and, if param_recordings
    was supplied, the params actually sent) when nothing matches --
    never a silently-empty or fabricated response, exactly as before.
    """
```

The exact parameter name/shape is not sacred -- what's required is:
backward compatibility (every existing `replay_transport({...})` call,
positional or keyword, with no second argument, behaves identically to
today), genuinely general disambiguation (not hardcoded to `asReported`
or to the statements endpoint specifically -- a *different* future
path/param pair must be usable with the same mechanism, unchanged), and
the same fail-loud-on-no-match discipline the function already has.

## Required tests

1. **Backward compatibility, exhaustively.** Every existing
   `replay_transport(...)` call site in `tests/test_tiingo_client.py`
   (there are several -- read them) continues to pass completely
   unmodified. Do not change any existing test's assertions to
   accommodate this task; if one breaks, your change is wrong, not the
   test.
2. **Path + relevant param disambiguates correctly, using the real
   specimen.** Using the real, already-recorded AAPL asReported and
   normalized fixtures (`tests/fixtures/tiingo/client/
   aapl_fundamentals_asreported.json` and `..._normalized.json`), build
   one `TiingoClient` via a single `replay_transport(..., param_recordings=...)`
   call, and confirm `client.get_fundamentals_asreported("AAPL", ...)`
   returns the as-reported body while `client.get_fundamentals_normalized(
   "AAPL", ...)` returns the normalized body -- from the *same*
   transport, the *same* path, correctly disambiguated.
3. **A path with no matching param_recordings entry falls back to
   `recordings`.** Confirms the two mechanisms compose rather than one
   silently shadowing the other.
4. **Still fails loudly on a genuine miss.** A path/param combination
   present in neither `recordings` nor `param_recordings` still raises
   `KeyError`, and the error is still informative (names what was
   requested).
5. **Generality, not a special case.** Construct a *second*,
   unrelated-to-Tiingo-statements hypothetical path/param pair (a small,
   local, in-test example is fine -- it does not need to be a real
   Tiingo endpoint) and confirm the same mechanism disambiguates it too,
   proving this isn't secretly hardcoded to the one known specimen.

## Non-goals

- Do not modify `TiingoClient`'s public methods or their call signatures.
- Do not modify or simplify `test_tiingo_source.py`'s existing
  `_make_client()` workaround -- out of this task's file ownership.
- Do not add retry/caching/rate-limiting behavior.
- Do not change what happens on a live (non-replay) request in any way.

## Acceptance criteria

- All pre-existing tests (690 as of `f716b1a`) continue to pass
  unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/vendors/tiingo/client.py` and
  `tests/test_tiingo_client.py`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-r1-replay-request-identity
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4b/task-p4b-r1-replay-request-identity`, touching only the two
files listed above.

## When done

Report: (a) the exact final `replay_transport` signature; (b)
confirmation every pre-existing call site (list them) still passes
unmodified; (c) the required test results; (d) full suite result; (e)
`git diff --stat`. Do not merge, do not touch `master`, do not modify
files outside the list above.
