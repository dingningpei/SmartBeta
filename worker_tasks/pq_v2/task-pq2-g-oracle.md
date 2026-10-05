# PQ2-G — Independent brute-force P-Q v2 oracle (test-only) (FROZEN)

- **Authority:**
  - methodology `docs/discovery_dataset_v1_2_pq_v2_methodology.md` (M§2, M§3, M§6–M§13,
    **M§16**);
  - plan §3, §8, §13.
- **Wave:** 1. **Dependencies:** none. **Runs in parallel with:** PQ2-A.
- **Owned:**
  - `tests/pqv2_oracle/__init__.py`, `tests/pqv2_oracle/oracle.py` (new);
  - `tests/test_pqv2_oracle_self.py` (new);
  - `tests/fixtures/pqv2/oracle/**` (new, including `mutations.json`).
- **Forbidden:** every production file. **`tests/pqv2_oracle` must import nothing from
  `smart_beta.dataset_v1.pqv2_*`** (enforced by an AST test in this task, and again at
  barrier I9). The oracle may import numpy, pandas and itertools only. The **self-test
  file** may import production `smart_beta.spec.transforms`,
  `smart_beta.engines.portfolio_sort` and `smart_beta.evaluation.portfolio`, solely to
  prove semantic equality.

## 1. Principle

The oracle **enumerates correction states and recomputes everything from primitive
synthetic data**: closes, CA events, volumes. It never uses intervals, sandwiches or
envelopes. It must be written from the methodology document and the frozen production
semantics, **without reading** any `pqv2_*` module (none exists at wave 1).

## 2. Synthetic world (`oracle.World`)

- trusted sessions 0..T−1, with integer positions as dates;
- stocks with: a raw close per session (NaN = no row); volume; recorded CA events
  {(session, type ∈ {split, dividend}, factor)}; a universe membership per formation;
- a flag set (security, s);
- parameters: formations, h, G, q (winsorization), c (cost), sign, expression form;
- **expression forms:**
  - `ratio(k1, k2)` = TRI(f−1−k1)/TRI(f−1−k2);
  - `neg_short(k)` = −TRI(f−1)/TRI(f−1−k);
  - `volscaled(k1, k2, w)` = (ratio − 1)/lag(std_{ddof=1}(ret, w), k1);
  - `std_x_rank(k1, k2, w_s, w_l)` = standardize_{ddof=0}(ratio(k1, k2)) ×
    rank_avg(mean(dv, w_s)/mean(dv, w_l));
  - with a full cross-section larger than the universe;
- the bridge lag: row f uses primitives dated ≤ f−1.

## 3. Correction states (M§3, M§7)

For each flag s with observed m_s, a correction picks:
- **a branch** ∈ {NONE, CA (φ_s ← φ_s·λ), CLOSE_PREV (close_{prev s} ← close_{prev s}/λ),
  CLOSE_S (close_s ← close_s·λ)};
- **λ** from a finite grid over Q_s (both endpoints, 1, and interior points);
- **a MISDATED alternative:** none, or the move of one recorded event (split/dividend
  within ±5 sessions, d ≠ s) from d to s.

Grids are configurable. The oracle enumerates the **Cartesian product** over flags (the
global one-misdating reading is available as a filter). Each state recomputes:
- raw returns: m_d = close_d/close_{prev d}·φ_d (D7a/D7c semantics: valid only if both
  closes exist);
- the TRI segment-1 product; ret; dv = close × volume;
- the expression, using the oracle's **own** numpy implementations (lag, rolling
  mean/std, standardize ddof = 0 over non-NaN row values, average rank keeping NaN);
- universe restriction; linear-quantile winsorization at q / 1−q; `rank(first)`
  bucketing;
- labels: the product of m over (f, e′] with D7a terminal/EMPTY/INCOMPLETE semantics;
  T0/TS values (1 + R)(1 + τ) − 1;
- leg means over finite values; net LS = long − short − c·turnover, with turnover
  computed per `evaluation/portfolio` (first period 0).

## 4. Oracle API (plain Python return values)

- `label_extremes(world, security, f, e) -> (min, max) | "EMPTY" | "MISSING" | "UNKNOWN"`;
- `reachable_assignments(world, formation) -> set[(frozenset top, frozenset bottom)]`;
- `ls_min(world, formation, scenario) -> float | None` and
  `turnover_max(world, formation) -> float`;
- `factor_values(world, state, formation) -> dict`, for debugging.

## 5. Self-tests (`tests/test_pqv2_oracle_self.py`)

- **Semantic cross-checks on random synthetic panels:** oracle standardize, rank,
  winsorize and bucketing equal production `transforms` / `portfolio_sort` exactly; the
  oracle turnover equals `evaluation/portfolio._turnover_series`.
- **Sanity:** the NONE state reproduces the observed values; TRI ratio telescoping
  (M§10 Prop. 2) holds numerically; interior close corrections cancel in labels (Prop. 1).
- **AST independence test:** no `pqv2_` import anywhere under `tests/pqv2_oracle`.
- **Case library:** `oracle.CASES` exposes the 18 named M§16 worlds, for use by later
  tasks' tests:
  - Candidate-E single suspect; multiple suspects;
  - formation endpoint; ending endpoint; interior close cancellation;
  - MISDATED paired CA; EMPTY label; TRI prefix cancellation;
  - simple assignment movement;
  - H4/H5 clean-clean crossing; suspect group-boundary crossing; volume-rank movement;
    coupled M/V uncertainty;
  - ties; σ = 0; NaN;
  - winsorization block-condition failure;
  - turnover upper bound.
  - At least one simple and one `std_x_rank` world use **full Campaign windows** (252 /
    126 / 21 / 231 / 105) over at least 300 sessions.

## 6. Mutations (`tests/fixtures/pqv2/oracle/mutations.json`)

| ID | Mutation |
|---|---|
| G-M1 | oracle standardize with ddof = 1 (killed by the transforms cross-check; this is the oracle half of V2-M10) |
| G-M2 | oracle rank `first` instead of `average` |
| G-M3 | oracle winsorization using `nearest` interpolation |
| G-M4 | oracle turnover with a first period ≠ 0 |
| G-M5 | NONE state not equal to the observed values (a λ applied twice) |

## 7. Stop conditions

Plan §13. In addition:
- STOP if `tests/pqv2_oracle` cannot be imported as `pqv2_oracle` from tests (rootdir
  `tests/conftest.py`) without a configuration change;
- STOP if a self-test shows the frozen production semantics differ from the methodology
  text (that is a specification defect).
