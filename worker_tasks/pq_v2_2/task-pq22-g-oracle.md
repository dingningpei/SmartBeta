# PQ22-G — independent generic oracle + random grammar generator (FROZEN 2026-10-08)

- **Authority:** plan §3–§4; amendment A§15A and A§16; base task
  `worker_tasks/pq_v2/task-pq2-g-oracle.md` and erratum G-E1. **Independence is
  binding:** no `smart_beta` import anywhere under `tests/pqv2_oracle/`.
- **Branch:** `pq2/task-pq22-g` from the spec-freeze master (named in the launch instruction).
- **Owned:**
  - `tests/pqv2_oracle/**`;
  - `tests/test_pqv2_oracle_self.py`;
  - new `tests/test_pqv2_oracle_grammar.py`.

## Work

1. **Generic AST** (oracle-local dataclasses; **not** `smart_beta.spec`), with these node
   types:
   - `Field(tri|ret|dv)`, `Const`;
   - `Lag(k)`;
   - `Rolling(mean|std, w)`;
   - `Add`, `Sub`, `Mul`, `Div`;
   - `Standardize`, `Rank`.

   A converter from the plain-dict expression format (`to_dict()` JSON) is the only
   bridge to production specs. It parses JSON only.
2. **Generic evaluator.** Evaluate any AST on oracle worlds (per-state TRI from m, ret,
   dv) with the existing kernels: `lag_frame`, `rolling_*`, `standardize` (ddof = 0),
   `rank_average`.
   - **Required semantics:**
     - the D7c bridge-lag indexing (M§10);
     - NaN propagation;
     - division by zero → NaN, never ±inf in output.
   - The existing four `Expr` forms must produce **identical** values through the generic
     evaluator (self-test).
3. **Grammar classifier.** An oracle-side, independent implementation of the A§15A
   grammar membership: SIMPLE_G, STD_X_RANK_G or OUTSIDE, with the bounds and the
   denominator rules.
4. **Random generator.**
   - A seeded generator (`seed=20261008`, `numpy.random.Generator(PCG64)`) producing
     grammar members of depth ≤ 4.
   - **Coverage:** every production rule and both parameter extremes (a = 0; w = 2; w =
     504; lookback = 504) must each appear at least 20 times per 2,000 draws.
   - Also generate OUTSIDE near-misses (one rule violated each).
5. **Brute-force checks.** Extend `reachable_assignments` / `ls_min` / `turnover_max` to
   generic expressions by state enumeration over the existing finite correction grids.
6. **Adversarial worlds** (A§16):
   - Add/Sub exponent-cancellation;
   - a near-zero divisor for each permitted denominator;
   - a forbidden divisor;
   - `standardize(ratio − c)`;
   - a lookback of 505 and a window of 1;
   - the 7 C1 specs (given as dict JSON).

## Tests (`test_pqv2_oracle_grammar.py`; self-tests only, no production)

- generic == legacy forms;
- classifier verdicts for every adversarial case;
- generator determinism (same seed → identical expression list hash);
- the coverage counts;
- the evaluator NaN and division rules;
- a world where `[A-STD]` would bind (an all-equal returns window): the oracle **reports**
  std = 0. It does not assert, because the assumption concerns production.

## Mutations (`tests/pqv2_oracle/mutations.json`, killed by the oracle self-tests)

| ID | Mutation |
|---|---|
| G22-M1 | the generic evaluator drops the bridge lag |
| G22-M2 | the classifier admits a cross-sectional node in SIMPLE_G |
| G22-M3 | the generator ignores the seed |
| G22-M4 | the classifier admits `rolling_mean(ret)` as a divisor |

**V22-M11** (an independence break) is a planner audit: an AST import scan plus a
`sys.modules` check.

## Stop

- the generic evaluator cannot reproduce a legacy form exactly;
- the enumeration cost per expression exceeds 2 s median on the fixture worlds; report
  it, and do not shrink N.
