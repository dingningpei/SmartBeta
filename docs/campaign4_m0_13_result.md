# Campaign-4 M0-13 — direction audit (M0-1) + common exposure (M0-3): result

- **Authority:** `worker_tasks/campaign4_m0/task-c4m0-13-direction-exposure.md` (frozen
  `ef4a090`), erratum E1 (`cc85578`), barrier record `task-c4m0-13-barrier.md` (PASS).
- **Code:** master `9644c20` (C4M0-13 merged as `1cdd750`).
- **Run (planner, laptop, 2026-10-16 04:57–05:47 PDT, 3,025 s):** IS fold
  `[2016-01-29, 2020-07-31)`, universe TOP_1000, K-PIT **copy** + scratch run root,
  credentials scrubbed. Exit 0.
- **Production K-PIT SHA-256** before and after: `29f0cabc…3a35fe` — **unchanged**.
- **Coding file:** `docs/campaign4_m0_1_rationale_coding.json`, SHA-256 `f3d138ab…881972`
  (REVIEWER JUDGMENT, blind, committed before the run; verified by the script).
- **Report:** `m0_13_direction_exposure.json`, content SHA-256
  `a3488a58ea68c7f5ad7018c2c998941c5b89073bf72231b9d304430acd9d9f60` (scratch, not committed).
- All five reference drivers certified (D-MOM, D-REV, D-VOL, D-DVT SIMPLE; D-EAR EVENT_SIMPLE).

## 1. M0-1 — direction audit: **DIRECTION_OK**

| Class | n |
|---|---|
| CONSISTENT | 71 |
| INVERTED | 2 |
| WEAK | 10 |
| UNCODED | 16 |

`r = 2 / 73 = 0.027` ≤ 0.10 → **DIRECTION_OK** (frozen §1.4). Coded = 73 ≥ 20.

The Campaign-3 expressions do implement the direction their own rationales state. The F2
prompt gap (the long side is never named) did **not** turn into sign errors in practice.
The two INVERTED candidates are both REV with relation −; their E vs D-REV is +0.43 and
+0.26.

## 2. Descriptive: the stated economics, not the sign, is what failed

L-T0-K0 one-sided EWC t (descriptive only; T0, not TD; no significance claim). 73 of 99
candidates have a VALID L-T0 cell; see §4 for the other 26.

| Driver, stated relation (coded, CONSISTENT unless noted) | n coded | n with t | mean t | t > 0 |
|---|---|---|---|---|
| MOM + (winners keep winning) | 30 | 15 | −0.39 | 4 |
| MOM − (losers rebound) | 9 | 8 | +0.19 | 7 |
| REV + (short-term continuation) | 9 | 6 | −2.18 | 0 |
| REV − (short-term reversal) | 6 | 6 | +0.30 | 5 |
| EAR + (post-announcement drift) | 9 | 9 | −0.64 | 0 |
| DVT + (rising dollar volume) | 7 | 5 | −1.01 | 0 |

- Across all classes, 17 of 73 candidates have t > 0; 2 have t > 1.0.
- The generator's dominant choices (MOM +, REV +, EAR +, DVT +) were faithfully
  implemented, and on this universe and IS period they earned negative long-side t. The
  smaller contrarian groups (MOM −, REV −) were the only ones with mostly positive t.
- **Reading (REVIEWER JUDGMENT, descriptive):** the C3 failure is in the economic prior
  the generator chose and kept choosing, and in the feedback not steering it off that prior
  (G3/G4 of the M1-a draft), not in sign mechanics.
- **Leakage caution:** these are IS outcomes. They must **not** be written into the
  contract prompt as a hint ("contrarian works"); that would turn IS results into a prior
  and defeat the M1-e walk-forward. They inform only *which mechanism* to fix.

## 3. M0-3 — common exposure: **CONCENTRATED**

| Statistic | Value |
|---|---|
| A4 replay (\|S\| > 0.90 vs an earlier admitted candidate), rejected | **51 of 99** |
| Pairs \|S\| > 0.90 / > 0.70 | 195 / 341 |
| Connected components at \|S\| > 0.90 | 45 (largest: 13, 10, 9, 5, 5) |
| Textbook restatements (max \|E\| ≥ 0.70) | 38 |
| Return co-movement PC1 share (73 series) | 0.314 |
| PCs for 80% (73 series) | 21 |

The A4 replay rejects 51 ≥ 30 → **CONCENTRATED** (frozen §2.3). The PC1 criterion
(≥ 0.50) is not met. The reading rests on the factor-value criterion alone.

Half the 99 proposals were near-copies of an earlier one. 38 were close restatements of
one of five textbook drivers. Several pairs are exact duplicates in factor values under
different spec hashes (e.g. ranks 21/41, 65/83, 40/85).

## 4. Disclosed deviation: 26 candidates lack an L-T0 series

26 of 99 exports carry the L-T0 cell as `NOT_ASSESSED`, reason
`PQV2_L_PERIOD_NOT_ASSESSED` (persisted Campaign-3 data, by driver: MOM 17, DVT 5, REV 3,
EAR 1). The frozen §2.1 assumed 99 series. The implementation excluded the 26 from the
return-correlation matrix (73 series) and reported their t as n/a.

- **Effect on readings:** none. M0-1 does not use returns. The M0-3 reading is decided by
  the A4 replay, which uses factor values for all 99; the PC1 share would have to rise from
  0.31 to ≥ 0.50 to matter, and either way the reading is CONCENTRATED.
- This was not caught at spec freeze. It is recorded here and does not change a frozen rule.

## 5. Consequences for the M1-a contract (input to Q2 calibration; not decided here)

| Contract item | What M0-13 says |
|---|---|
| A1 / A2 (direction convention, declarations) | Direction was not the main cause (DIRECTION_OK). Keep as hygiene, per the frozen §1.4 consequence. A2's `predicted_relation` stays useful for auditing |
| A3 (joined, rank-relevant feedback) | Becomes the **core** fix: the generator persisted with priors that its own results contradicted |
| A4 (diversity gate \|ρ\| > 0.90, family cap 30%) | Strongly supported: it would have removed 51 of 99. Threshold stays fixed (Q4) |
| New candidate item (for the planner's calibration proposal) | A restatement check (max \|E\| vs the five drivers ≥ 0.70) reported to the generator, without return information |
