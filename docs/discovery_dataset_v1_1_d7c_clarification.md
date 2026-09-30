# Discovery Dataset v1.1 — D7c TrustedInput Bridge Clarification (NORMATIVE)

- **Status:** normative clarification of the D7c HOLD items. The user decided
  them on 2026-09-29: Decision 1 = P1c; Decision 2 = the same-segment
  invariant for `total_return_index`, with the suppression rule to be derived
  and proven.
- **Documentation only:** it changes no completed stage and no sealed phase.
  Phase 6 is **not** modified.
- **It clarifies, without modifying:**
  - `docs/discovery_dataset_v1_contract.md` §§3, 5, 7, 8, 10, 11.1, 16
    (gate 13), 20 —
    `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`;
  - `docs/discovery_dataset_v1_1_d7_clarification.md` §1 (8efbd96), which
    assigned the TrustedInput bridge to D7b. The D7b spec (4a88275) did not
    implement it, and D7c now carries that obligation.

## 1. Gate-13 authority and the Phase-6 consumer

- **Contract §7:** the only FactorSpec inputs admitted in v1 are
  `daily_total_return`, `total_return_index` ("the trusted cumulative product
  of `daily_total_return`") and `dollar_volume`. They reach Phase 6 **only**
  through the bridge, with the §11.1 one-session lag.
- **Contract §11.1:** "the row dated d carries the observation from session
  prev(d)".
- **Gate 13:** "an input built from session f's own data cannot reach factor
  row f; every row-f input's source session is ≤ f−1."
- **The Phase-6 consumer** (`smart_beta/spec/engine.py`, `evaluator.py`,
  `transforms.py`, all unchanged):
  - Each input is a `TrustedInput(values, capability, evidence_class,
    vintage_evidence)`.
  - `values` is a wide `float64` frame: trusted observation dates as the
    index, `security_key` strings as the columns, `NaN` = missing.
  - All inputs share **one exact grid**, and Phase 6 performs **no** temporal
    selection or alignment.
  - `lag(x, N)` and rolling `mean/sum/std/min/max(x, N)` operate on grid rows
    (observation dates). Rolling windows require `min_periods == window`, so a
    NaN in the window gives NaN.
  - `rank` (`na_option="keep"`), `winsorize` (quantiles skip NaN; NaN stays
    NaN) and `standardize` (row mean and std skip NaN) ignore NaN cells.
  - `MAX_LAG_PERIODS = 10_000`, rolling windows are similarly bounded, and
    expressions nest. **There is no enforced 252-session lookback cap.** The
    declared `DataRequirement.lookback` is compared only with
    `capability.history`, never with the expression. Contract §3's "maximum
    factor lookback (252 sessions)" is a rationale for the listing-age rule,
    not an enforced bound.

## 2. Timing (one diagram; no double lag)

    close(s−1)          close(s)
         └── primitive r(s) = §8 total return dated s (needs close(s−1), close(s))
    Phase-6 row d = next_trusted_session(s) carries r(s)      [the §11.1 lag, applied once]
    factor row f therefore uses only primitives dated ≤ prev(f) = f−1

- The previous close inside the return formula is part of the primitive. It
  is **not** the §11.1 lag, and the §11.1 lag is applied exactly once.
- Sessions come from the trusted NYSE calendar, never from calendar days.
- **The grid:** trusted sessions from 2015-01-02 to 2023-06-30 inclusive.
  - The first row is NaN (its prev is outside the acquisition range).
  - Row 2023-06-30 carries primitives dated 2023-06-29, so no reserve
    observation is needed.

## 3. Grid population (P1c)

- **Physical columns:** every D4 `INTERVAL_ASSIGNED` `security_key` that
  appears exactly once in `i-a21f1e46621dee94`, sorted. This fixed set exists
  only because Phase 6 requires one exact grid. Future D6 membership (any
  top-N union) is **never** used to choose columns.
- **Economic mask:** cell (d, K) may be non-NaN only if **all** of these hold:
  - the primitive dated `s = prev(d)` is valid for K inside K's D4 interval
    (the usable-row rules below);
  - `class_state_at(class_timeline, K, prev(d))` is `COMMON_STOCK`;
  - `policy_state_at(policy_timeline, K, prev(d))` is
    `NO_REGULATORY_EXCLUSION_OBSERVED`. `EXCLUDED` and
    `ISSUER_STATUS_UNRESOLVED` are masked, as in D6.
  - The frozen D-SC functions are used read-only with cutoff **prev(d)**,
    which is the row's information cutoff (the gate-13-consistent reading of
    "on session d", matching D6's f−1).
  - There is no final-class mask and no union of securities that were ever
    COMMON. A later D-SC transition cannot change an earlier row.
- **Why the mask is safe:** masked cells are NaN before any Phase-6
  operation. Given the NaN semantics in §1, an ineligible column cannot affect
  `rank`, `winsorize` or `standardize` for eligible securities.
- **Nonclaims carried forward:** all D-SC nonclaims, including
  issuer-identity knowledge timing and continuity (NOT CERTIFIED).

## 4. Primitives, QC and identity

- **`daily_total_return` dated s:** the contract §8 path, exactly as in D7a.
  - The row is usable per D6 §5 / H3: on-calendar; not CORRUPT, not
    INTERVAL_MISMATCH, not VENDOR_NULL; `close > 0`. The same holds for
    prev(s), the previous trusted session.
  - `raw = close(s)/close(prev(s)) − 1`, then `compute_adjusted_returns` with
    exact-date matching, `as_of = 2023-06-30`, buffer 0, and the
    corporate-action preconditions (an unmatched or superseded event fails
    closed).
  - It must be byte-equal to D7a's `daily_total_return` on every cell where
    both exist.
- **`dollar_volume` dated s:** `float64(raw close) × float64(raw volume)` on a
  liquidity-valid row (usable, `close > 0`, volume not null).
  - Zero volume gives **0**. Missing or null volume gives NaN.
  - Adjusted fields are never read.
- **QC:**
  - CORRUPT, INTERVAL_MISMATCH, VENDOR_NULL, missing rows, NOT_YET_LISTED,
    ENDED and VENDOR_MISSING → NaN.
  - CORP_ACTION_SUSPECT and TERMINATION_MISMATCH (UNCERTIFIED_SEMANTICS) →
    usable.
  - Nothing is filled. There is no forward fill, zero substitution or
    imputation.
- **Identity:** rows map to K only inside K's assigned interval. There is no
  continuity inference and no ticker stitching.
- **Excluded:** `IDENTITY_AMBIGUOUS`, `HISTORY_UNREACHABLE` and duplicated keys
  have no column.
- **No delisting treatment:** no D7b T0/TS or τ value ever enters an input.

## 5. `total_return_index`: the same-segment invariant and its minimal suppression

**Invariant (user-frozen).** A TRI ratio is valid only when both endpoints lie
in the same uninterrupted valid-return segment. Any non-VALID return breaks
the segment: NO_ROW, ROW_UNUSABLE, PREV_NO_ROW, PREV_ROW_UNUSABLE, or leaving
the identity interval. Forward fill, zero substitution, carrying the level
through a gap, and ratios across rebased segments are all forbidden.

**Construction (per key, unlagged, dated s).**
- **Anchor a:** the first session in K's interval whose row is usable and
  whose next session's return is VALID. `TRI(a) = 1.0`.
- **Recursion:** `TRI(s) = TRI(prev(s)) × (1 + r(s))` for each consecutive
  VALID return from `next(a)` onward.
- **First break b:** the first session after a whose return is not VALID.
  The segment `[a, b−1]` is **segment 1**. Every later segment is computed
  internally, each with a distinct segment id, but is **not** admitted.

**Minimal suppression (derived).**
- The legal Phase-6 lookback is unbounded relative to the grid (§1: lags up
  to 10,000, nesting allowed, grid ≈ 2,200 rows).
- Therefore the only finite, deterministic numeric rule that makes every
  legal expression fail closed across a break is to admit **segment 1 only**.
  Every unlagged TRI dated s ≥ b is NaN in the Phase-6 input, to the end of
  the grid.
- In Phase-6 row terms, row d carries `TRI(prev(d))` if `prev(d) ∈ [a, b−1]`
  (and the §3 mask admits it), otherwise NaN.
- A shorter window (for example 252) is **insufficient**: `lag(TRI, 253)` or
  `lag(lag(TRI, 200), 200)` would reach across the break.

**Proof, covering the off-by-one.** Take any expression whose value at row d
combines finite TRI cells at rows d₁ … dₖ, through arithmetic, lag, rolling
or nesting.
- Each finite cell has `prev(dᵢ) ∈ [a, b−1]`, which is contiguous and
  break-free by definition of b.
- So every pair of endpoints lies in one uninterrupted segment, for any N ≥ 1
  and any nesting.
- If an endpoint would be at or after b, its cell is NaN, and NaN propagates
  through lag, rolling (full window required) and arithmetic.
- Boundary cases:
  - The break session b and the first valid return after the gap are both
    suppressed.
  - The last admitted unlagged session is b−1, which appears at Phase-6 row
    `next(b−1) = b`. Row b therefore carries `TRI(b−1)`, which is admissible:
    it is information as of b−1.
  - Row `next(b)` carries `TRI(b)` = NaN.
- Tests cover N ∈ {1, 2, 251, 252, 253, 1000} and nested lags.

**Coverage cost (planning evidence from development QC metadata, not
hard-coded).** 203 of 45,000 D6 top-500 member-formation rows (0.5%, 37 keys)
have an in-interval break before their formation, so their TRI is NaN.
`daily_total_return` and `dollar_volume` are unaffected.

**A future option (out of scope).** If a later authority enforces a maximum
effective expression lookback L, a restart with suppression of the first L+1
segment sessions could be reconsidered. D7c v1 does not assume it.

## 6. Knowledge-PIT and evidence class

- **Evidence class:** `EvidenceClass.LIVE_RECORDED` (the Pilot-1A precedent;
  never upgraded).
- **Knowledge date:** row d holds the end-of-day observation of prev(d).
  Vendor revision history is not certified: values are as served at C.
- **Knowledge-PIT:** D7c is a derived development artifact. It needs **no**
  Knowledge-PIT record: DERIVED/ACCESS writers are P10-I and P10-H, at
  evaluation ingestion.

## 7. Nonclaims

D7c certifies only the deterministic, development-only construction and
Phase-6 delivery of the three contract inputs, under the one-trusted-session
lag, with gate-13 timing and future-data invariance demonstrated.

It does **not** certify:
- factor efficacy, alpha or predictive validity;
- any Campaign-1 result;
- confirmation eligibility or reserve evidence;
- identity continuity beyond D4, or issuer-identity timing;
- delisting proceeds or survivorship completeness;
- volume semantics (NOT_CERTIFIED);
- production readiness.
