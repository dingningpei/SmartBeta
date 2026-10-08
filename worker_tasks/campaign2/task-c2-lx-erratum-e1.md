# C2-LX erratum C2-E1 — Campaign-2 SESOI (FROZEN 2026-10-08; user-approved option A)

- **Defect class:** specification defect (planner). The frozen Campaign-2 precommitment
  (`0815259b…0bc5`) and registration plan (`bacca410…a29a`) did not fix the SESOI value
  or its justification text, which the Phase-10 `EstimandPolicy` contract requires
  (`sesoi`, `sesoi_justification_hash`). The C2-LX worker filled the gap in commit
  `1b923c3`; that choice becomes binding only through this erratum.
- **Frozen values:**
  - `sesoi = 0.0025` (net long-short return per 21-session holding period), the same
    value as Campaign-1;
  - justification text (exact UTF-8 bytes, a single line):

    > C2 estimand policy: 0.0025 net long-short return per 21-session holding period (about 3.0% annualized), the smallest net quintile long-short spread on the D6 top-500 equal-weighted universe, after 10 bps Phase-7 transaction cost per unit of membership turnover, that Campaign-2 treats as economically meaningful; the Campaign-1 SESOI, fixed before any Campaign-1 empirical evaluation and unchanged for Campaign-2.

  - `sesoi_justification_hash = 3bba6e1656f94bdde7f94639a823ceae9a4a4c251194bdfb65059cffd55458ea`.
- **Effect:**
  - no code change, and no change to the C2-DEV-SCREEN-v1 screen or the disposition
    (neither uses SESOI);
  - it enters only the `ESTIMAND_POLICY` registration record.
