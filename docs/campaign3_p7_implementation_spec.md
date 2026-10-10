# Campaign-3 P7 — reserve-fold chain: implementation spec (FROZEN 2026-10-09; R7-U/R7-T/R7-R authorized; R7-X after P-Q v2.4 merges)

- **Authority:**
  - design `docs/campaign3_p7_reserve_path_design.md` (frozen `59fdd21`);
  - **amendment A1** `docs/campaign3_p7_amendment_a1.md` (frozen `3f4ed39`): new
    fold-generic code, proven by development byte-equivalence;
  - the pinned D-chain builders stay untouched.
- **Planner self-check:**
  - **Literals:** every artifact id below.
  - **Pinned code:** `universe.py`, `returns.py`, `trusted_inputs.py`,
    `delisting_bridge.py` and every `pqv2_*` module are **import-only**. New code lives in
    a new package.
  - **Frozen consumers:** none; this adds new modules only.
  - **K-PIT:** tests use disposable logs. The real reserve run (P6) writes the `ACCESS`
    record under separate authorization.
- **No real reserve read in any task, test or planner step before P6.**

## 1. Equivalence targets (frozen development artifacts)

| Layer | Frozen dev artifact | Files |
|---|---|---|
| D4 identity (input) | `i-a21f1e46621dee94` | `identity_assignment.parquet` |
| D6 universe | `u-6150bc6ac2866b7f` | `formations.parquet`, `universe_development.parquet`, `audit_development.parquet`, `universe.json` |
| D7a returns/labels | `r-34565f074d64ccda` | `daily_returns.parquet`, `labels.parquet`, `horizons.parquet`, `members.parquet`, `returns.json` |
| D7c trusted inputs | `ti-aa23fabd3db4e595` (`ti_hash 30ee0c51…`) | `total_return_index`, `daily_total_return`, `dollar_volume`, `segments` (.parquet), `trusted_inputs.json` |
| P-Q source set (v2.4, default development fold) | the ADM digest `198a9606377fbbb24e6535385b05249456bf4e4844a78227d4f557a42a52cfed` | computed by P-Q v2.4 on the R7 development-fold outputs |

**The equivalence criterion** (it binds every task):
- for each output table, `pandas.testing.assert_frame_equal(new, frozen, check_exact=True,
  check_dtype=True)` after a canonical sort by the frozen table's key columns;
- **and** the artifact's own identity hash (the `*.json` hash fields, `ti_hash`, the
  universe/returns ids) is recomputed **identically** from the new outputs, using the
  frozen hashing functions imported read-only.
- Parquet byte equality is **not** required, because writer metadata may differ. Content
  plus identity-hash equality is.

## 2. Fold parameter

`FoldWindow(start, end, source)`:
- `source` selects the D3/D5/v1.3 packages:

  | Fold | Packages |
  |---|---|
  | development | `normalized/n-f874d3ecb61374d4/`, `derived/qc/q-5d1028655f8c82e1/`, `derived/edgar-f0d0ee1b…/` |
  | reserve | `reserve_sealed/n-f874d3ecb61374d4/`, `reserve_sealed/q-5d1028655f8c82e1/`, `reserve_sealed/edgar-f0d0ee1b…/` |

- **Lookback rows** before `start` always come from the development packages.
- **The reserve fold:** start 2023-07-03; end = C. The last reserve formation is
  ≤ C − 21 trusted sessions (design decision 1).
- **Gate:** constructing a reserve `FoldWindow` requires an `AccessGrant` (§4). Without
  it → `ReserveAccessDenied`, before any path is opened.

## 3. Tasks (new package `smart_beta/dataset_v1/reserve_chain/`; disjoint files)

| Wave | Task | Owned (new) | Depends on | Content |
|---|---|---|---|---|
| 1 | **R7-U** universe | `reserve_chain/universe_fold.py`, `tests/test_reserve_chain_universe.py`, `tests/fixtures/reserve_chain/universe/**` | — | the D6 logic (schedule, eligibility incl. 252-session listing age, liquidity, ranks → TOP_200/500/1000) on a `FoldWindow` |
| 1 | **R7-T** trusted inputs | `reserve_chain/trusted_fold.py`, tests/fixtures as above | — | the D7c logic (TRI segments, daily total return, dollar volume, the one-session bridge lag) on a `FoldWindow` |
| 2 | **R7-R** returns/labels | `reserve_chain/returns_fold.py`, … | R7-U | the D7a logic (daily returns, h = 21 labels, horizons, members incl. delisting/terminal evidence) on a `FoldWindow` |
| 3 | ~~R7-Q~~ **moved** | — | — | P-Q reserve-fold sources, labels and events are implemented by **P-Q v2.4 path (i)** (`pqv2_*` gain a `fold` parameter; see `docs/discovery_dataset_v1_2_pq_v2_4_implementation_plan_draft.md`). P7 supplies the stitched dev/reserve inputs (R7-T) and the reserve `Fold` from the `AccessGrant` (R7-X) |
| 4 | **R7-X** access gate + confirmation executor | `reserve_chain/access.py`, `reserve_chain/confirm.py`, … | R7-U, R7-T, R7-R, **P-Q v2.4 merged** | the `AccessGrant` from a K-PIT `ACCESS` record (§4); the executor: derive → P-Q **v2.4** diagnostics on the reserve `Fold` for **one** hypothesis → `run_inference` (admitted `EWC_T23_ONE_SIDED`) → assessment; write-ahead persistence |

- **Tests per task (synthetic):** fold-window handling, lookback stitching, the gate
  refusal, and the edge cases of the original module's own tests.
- **Mutations per task:**
  - the dev/reserve package swapped;
  - the lookback taken from the wrong fold;
  - the bridge lag dropped;
  - the gate bypassed;
  - plus the module-specific ones.

## 4. ACCESS gate (Phase-10 §5.2)

- **`AccessGrant.from_kpit(log, record_hash)`** requires a K-PIT `ACCESS` record whose
  payload names:
  - the hypothesis id;
  - the procedure (`EWC_T23_ONE_SIDED` 1.0.0, contract `ac14e033…`);
  - the exact `reserve_chain` module source SHA-256s;
  - the fold window.
- **Mismatch** → denial.
- **Single use:** a second reserve derivation for the same hypothesis is refused (the
  grant is consumed; K-PIT is the ledger).

## 5. Planner equivalence barrier (after the Wave 1–3 merges; real development data only)

- **The run:** the planner runs `reserve_chain` on the **development** fold over the
  real development packages, and checks §1 equality for D6, D7a, D7c and the P-Q source
  digest. It runs under the network guard, with the file-open audit **proving 0 reserve
  opens**.
- **Pass criterion:** every target is equal. Any mismatch → STOP; the cause is reported
  and returned to the owning worker.
- **Ordering:** this barrier must pass **before** the Campaign-3 P5 freeze.

## 6. Out of scope

- No D7b-observed reserve run is needed beyond what P-Q's evaluation uses, through the
  existing `build_pqv2_diagnostics` with fold inputs supplied.
- If P-Q evaluation needs a pinned function to accept reserve inputs it refuses → STOP
  and report. That would be a further amendment.
