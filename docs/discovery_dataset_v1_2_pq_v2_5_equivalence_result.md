# P-Q v2.5 — real-data equivalence result (PQ25-X, IS only)

**Disposition:** P-Q v2.5 (terminal-delisting scenario TD) is **IMPLEMENTED** and
**EQUIVALENCE-PROVEN on IS**. This is **not** an admission of v2.5 for confirmation use;
admission remains a separate, user-authorized step (erratum E1.3).

## Authority and run identity

| Item | Value |
|---|---|
| Spec | `worker_tasks/pq_v2_5/task-pq25-x-equivalence.md` (frozen 2026-10-14, incl. PASS/FAIL rule) |
| Code | PQ25-X `0029e31`, merged as `2d9604a` |
| Barrier at `2d9604a` | full suite 6865 passed, 1 skipped, 882 s (exit 0); `verify_authority_v2_5()` OK |
| Fold | IS `[2016-01-29, 2020-07-31)`, 54 months; reserve and DEVELOPMENT_SEALED untouched |
| d (TD_D) | −0.55 (M1-d, B1) |
| Isolation | K-PIT copy + scratch run root; credentials scrubbed; network guard |
| Production K-PIT SHA-256 | `29f0cabc…` before and after (unchanged) |
| Report `pqv25_equivalence.json` | content_sha256 `4e087d3e854fa7967308cf4a5748c4b400c5a3e4c56b32b306713f3f5ef2a4e5` (not committed; scratch output) |
| Wall time | 15 374 s |

## Gating result: PASS

E1 (TD at d = −1 ≡ TS, bitwise), E2 (TD at d = 0 ≡ T0, bitwise) and E3 (carried `base`
byte-identical to the frozen run) hold for all 11 factors: 52 TD cells compared, first
mismatch: none. No factor was NOT_ASSESSED. E4 (descriptive, per period
L-TS ≤ L-TD ≤ L-T0 at d = −0.55): 0 violations.

| Factor | E1 | E2 | E3 | E4 violations | TD cells |
|---|---|---|---|---|---|
| KF-MOM12 | PASS | PASS | PASS | 0 | 4 |
| KF-MOM6 | PASS | PASS | PASS | 0 | 4 |
| KF-REV1 | PASS | PASS | PASS | 0 | 4 |
| KF-VSMOM | PASS | PASS | PASS | 0 | 4 |
| KF-LVOL | PASS | PASS | PASS | 0 | 4 |
| KF-EAR (event) | PASS | PASS | PASS | 0 | 8 |
| C3-IS-1 (event) | PASS | PASS | PASS | 0 | 8 |
| C3-IS-2 | PASS | PASS | PASS | 0 | 4 |
| C3-IS-3 | PASS | PASS | PASS | 0 | 4 |
| C3-IS-4 | PASS | PASS | PASS | 0 | 4 |
| C3-IS-5 | PASS | PASS | PASS | 0 | 4 |

C3-IS-1…5 are the Campaign-3 TB_SELECTION candidates in IS-rank order (digests `b7857381…`,
`43c12762…`, `b0cf0f6c…`, `b870b9f3…`, `2bbb8e58…`).

## Descriptive: EWC t at d = −0.55 (C4M0-2 statistic, n = 54, all VALID)

For event factors, K0 is shown; K1 differs by < 0.02 in every cell.

| Factor | B-T0 | B-TS | **B-TD** | L-T0 | L-TS | **L-TD** |
|---|---|---|---|---|---|---|
| KF-MOM12 | 0.04 | −1.09 | **−0.59** | −0.26 | −1.35 | **−0.86** |
| KF-MOM6 | −0.23 | −1.95 | **−1.22** | −0.59 | −2.17 | **−1.50** |
| KF-REV1 | 1.43 | 0.14 | **0.72** | 1.04 | −0.16 | **0.37** |
| KF-VSMOM | −0.03 | −1.07 | **−0.60** | −0.27 | −1.28 | **−0.82** |
| KF-LVOL | −1.42 | −2.52 | **−2.02** | −1.75 | −2.83 | **−2.35** |
| KF-EAR | −0.27 | −1.23 | **−0.81** | −1.70 | −2.63 | **−2.22** |
| C3-IS-1 | 1.67 | 0.41 | **0.97** | 1.41 | 0.19 | **0.74** |
| C3-IS-2 | 1.19 | −0.12 | **0.45** | 1.00 | −0.35 | **0.24** |
| C3-IS-3 | 1.25 | −0.19 | **0.46** | 0.84 | −0.74 | **−0.02** |
| C3-IS-4 | 1.08 | −0.15 | **0.42** | 0.59 | −0.79 | **−0.15** |
| C3-IS-5 | 0.45 | −0.57 | **−0.10** | 0.20 | −0.82 | **−0.35** |

Cross-check: the B-T0 column reproduces the M0-2 result (`docs/campaign4_m0_2_result.md`).

## Reading (descriptive only; no decision is taken here)

- TD sits between T0 and TS for every factor and cell, as designed; at d = −0.55 it removes
  roughly half of the TS drag measured in M0-4.
- Under TD no factor (M0 six or C3 five) shows a positive EWC t above 1.0 on IS; the best is
  C3-IS-1 (B-TD 0.97). These are IS diagnostics, not confirmation evidence.
