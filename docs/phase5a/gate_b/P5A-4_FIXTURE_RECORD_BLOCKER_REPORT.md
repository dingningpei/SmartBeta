# P5A-4 Gate B — Fixture-Recording Blocker Report (Stage 2)

**Status: BLOCKED at the live fixture-recording boundary. Gate B did NOT complete.**
**P5A-5: NOT STARTED. Nothing committed.**

The entitlement correction (Stage 1) passed pre-live review. The authorized
bounded resume resolved V and WMT, producing a valid 30-candidate freeze, but
the live Tiingo fixture recording then hit the hourly allocation after 68
successful requests. The corrected recorder stopped on the first transient and
wrote no partial fixture set.

---

## 1. ENTITLEMENT RESUME (authorized, bounded)

Run once: `.venv/bin/python scripts/fetch_phase5a_gate_b_fixtures.py --mode probe`

- The 28 already-definitive candidates were **not re-probed** (all logged
  `[resumed]`).
- **V = HTTP 200, `rows_returned=1`** — definitive accessible.
- **WMT = HTTP 200, `rows_returned=1`** — definitive accessible.
- No wait, no retry; exactly one request per unresolved candidate.
- Log preserved: `docs/phase5a/gate_b/evidence/p5a4_entitlement_resume_v_wmt.log`
  sha256 `14327d1faa22d7afa014bcc0f211adf70e8b41e3a758b5460363c0033493ec47`.

## 2. FINAL CANDIDATE STATES

- **accessible = 26**
  `MMM, AXP, AMGN, AAPL, BA, CAT, CVX, CSCO, KO, DIS, GS, HD, HON, IBM, JNJ,
  JPM, MCD, MRK, MSFT, NKE, PG, CRM, TRV, UNH, V, WMT`
- **plan-tier excluded = 4** `GOOGL, AMZN, NVDA, SHW` (HTTP 400, "DOW 30")
- **unknown = 0**

## 3. FINAL FROZEN UNIVERSE

Exact names: `MMM, AXP, AMGN, AAPL, BA, CAT, CVX, CSCO, KO, DIS, GS, HD, HON,
IBM, JNJ, JPM, MCD, MRK, MSFT, NKE, PG, CRM, TRV, UNH, V, WMT` — **count 26**.

## 4. UNIVERSE SNAPSHOT

- Path: `docs/phase5a/gate_b/universe_snapshot.json`
- sha256 `b2fef3989048a4e82f8db6f90296e7d02024e02406951c82c62363875b55156a`
- `retrieved_at_utc` `2026-09-19T18:32:53Z`, probe date `2026-06-15`
- Built as one atomic `os.replace` from all 30 definitive outcomes; internally
  validated (`_snapshot_outcomes_are_definitive == True`).
- Pre-resume non-final ledger preserved (not certification evidence):
  `docs/phase5a/gate_b/evidence/NONFINAL_entitlement_probe_ledger_pre_resume.json`
  sha256 `d9bb0c8a6032680456ba69ebbeef939695797885a30a378cdda6eeedaf677eb8`,
  whose 28 outcomes trace to the preserved blocker log
  `.../evidence/p5a4_recorder_quota_block.log`
  sha256 `35b4d8be4a75b9a418c37e980fc9bf3efeb7f8b46fed2ab568dbb1b1c279230e`.

## 5. LIVE TIINGO FIXTURES — BLOCKED

Run once:
`.venv/bin/python scripts/fetch_phase5a_gate_b_fixtures.py --mode record`

- 69 fixture requests attempted for the frozen 26: **68 returned HTTP 200**,
  then the 69th returned **HTTP 429**.
- **First blocking request:** `GET /tiingo/fundamentals/TRV/daily`
  (`startDate=2025-09-15`, `endDate=2026-09-15`)
  HTTP 429 body:
  `{'detail': 'Error: You have run over your hourly request allocation. Please upgrade at https://api.tiingo.com/pricing to have your limits increased.'}`
- The corrected recorder **stopped immediately**: no sleep, no quota-window
  retry, **no partial fixture set written**.
- Files written under `tests/fixtures/tiingo/phase5a_gate_b/`: **0** (directory
  created empty by `mkdir`; no bodies, no `manifest.json`).
- Log preserved:
  `docs/phase5a/gate_b/evidence/p5a4_fixture_record_quota_block.log`
  sha256 `d4213e4b8faa533af1af53603faf3dfea8454090942bb2ba203c264f01942b25`.

## 6. LIVE FRED FIXTURE

**Not requested.** The run returned 3 in `run_record` before reaching FRED;
`tests/fixtures/risk_free/treasury_gate_b/` is empty (no request, no fixture).

## 7. OTHER REQUIRED FIELDS

| Field | Value |
|---|---|
| **GATE B WINDOW** | `2025-09-15` .. `2026-09-15` (EOD lookback start `2025-09-05`); unchanged |
| **SETTINGS** | `DEFAULT_SETTINGS` (unmodified); Gate A's `bottom_mcap_exclude_pct=0.0` override NOT carried over |
| **TRADABILITY** | NOT DETERMINED (no artifacts produced) |
| **ARTIFACT A** | NOT RUN (blocked before pipeline) |
| **ARTIFACT B** | NOT RUN |
| **ARTIFACT C** | NOT RUN |
| **TARGETED TESTS** | `.venv/bin/python -m pytest tests/test_fetch_phase5a_gate_b_recorder.py -q` → 17 passed |
| **FULL SUITE** | 6 failed, 1173 passed, 1 skipped, 7 errors — all 13 failures/errors are the incomplete empirical `tests/test_capm_pilot_gate_b.py` (no fixtures/artifacts); the 1 skip is the pre-existing key-gated live certification test (`tests/test_tiingo_certification.py:716`) that skipped on `TiingoAPIError`; no regressions |
| **NETWORK BOUNDEDNESS** | PASS — one request = one attempt; stop on first 429; no sleep/retry |
| **PARTIAL-WRITE PROTECTION** | PASS — no fixture body or manifest written |
| **PROVENANCE** | PASS — snapshot valid; 28 pre-existing outcomes traceable to the preserved log; stale snapshot never consumed |
| **CREDENTIAL HYGIENE** | PASS — key only presence-checked; never logged or written |
| **COMMIT** | NONE |
| **P5A-4 STATUS** | BLOCKED |
| **P5A-5 STARTED** | NO |

## 8. Live-request accounting

- Authorized recorder: 2 entitlement probes (V, WMT) + 69 fixture attempts
  (68×200, 1×429) = **71 requests**.
- The requested full-suite invocation additionally triggered the pre-existing
  key-gated live test `tests/test_tiingo_certification.py::test_fundamentals_ranged_query_semantics`,
  which attempted a live call and skipped on `TiingoAPIError` (1 request per
  invocation). This test is outside P5A-4 ownership and is not part of the
  recorder.

## 9. Working tree

```
?? docs/phase5a/gate_b/
?? scripts/fetch_phase5a_gate_b_fixtures.py
?? tests/test_capm_pilot_gate_b.py
?? tests/test_fetch_phase5a_gate_b_recorder.py
```

No recorder processes are running. `universe_snapshot.json` is a valid freeze
(26/30); the Gate B fixture set, FRED fixture, artifacts, and disposition do
not exist yet.

## 10. Corrective path (for a later, separately authorized task)

Once the Tiingo hourly allocation is available, re-run **only**
`--mode record` (never `--mode all`, which would re-probe all 30). The frozen
universe and window are unchanged. No universe/window/settings change is
warranted; the block is purely the vendor hourly allocation.
