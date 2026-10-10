# Campaign-3 P7 — planner development-equivalence barrier evidence

- **Status:** planner barrier evidence for `docs/campaign3_p7_implementation_spec.md` §5,
  which was frozen at `5b29314` and authorized as part of P7.
- **Code certified:** master `d274b35`, which contains:
  - R7-U `e6c4031`;
  - R7-T `b061f00` + `5c1274b`, and R7-T-R2 `89dbbf2`;
  - R7-R `f57b6c8`;
  - R7-X `1b7d3b3` + R1 `757c1d8`.
- **Data:** **real development data only**, and **0 reserve opens**.

## Method

- **Clone root.** The runs used a copy-on-write (APFS `clonefile`) root at
  `scratchpad/p7b/root`. It contains only:
  - `manifest/`, `normalized/` and `production_v1/`;
  - `derived/{classification, identity, qc, qc_audit, quarantine}`.
  **Excluded:**
  - `reserve_sealed/`, so reserve opens were physically impossible;
  - `raw/`, `dsc_evidence/` and `dsc_probe/`;
  - the frozen D6, D7a and D7c outputs, so every layer was rebuilt by `reserve_chain`.
  The real `data_store` was never written.
- **Comparison.** Each script compared its outputs read-only against the frozen artifacts
  in the real `data_store`.
- **Audit and environment.** The file-open audit (`PQ_AUDIT_LOG`) ran on every step.
  Credentials were scrubbed, and the network guard `6697069a…8368` was verified.
- **Chain.** D6 → D7a (consuming the rebuilt D6) → D7c → the P-Q v2.4 source set (default
  development fold), computed from the rebuilt D7a and D7c.
- **Planner tools** (SHA-256 prefixes):

  | Tool | SHA-256 prefix |
  |---|---|
  | `d6.py` | `c466e8f1…` |
  | `d7a.py` | `e96f6250…` |
  | `d7c.py` | `a3aa8d58…` |
  | `pqdigest.py` | `fff4efe4…` |

  `pqdigest.py` is the v2.3 ADM-2 digest code with the authority set to v2.4.

## Results (§1 criterion: `assert_frame_equal(check_exact, check_dtype)` after a canonical sort, plus identity hashes recomputed with the frozen functions)

| Target | Result | Audit |
|---|---|---|
| **D6** `u-6150bc6ac2866b7f` | `run_universe_fold(FoldWindow.development())`: `universe_id` equal; `universe_hash` equal (`6e8539b7…`); formations (128 rows), universe_development (90,000) and audit_development (531,389) **EQUAL**; `universe.json` differing fields: **none** | 501 opens; 0 reserve; real-store opens = 4, all comparator reads of the frozen D6 |
| **D7a** `r-34565f074d64ccda` | `build_returns_fold(DEVELOPMENT_FOLD)`: members (90,000), horizons (5,670), daily_returns (1,961,150) and labels (5,670,000) **EQUAL**. The new tables written through the frozen `returns._write_outputs` give `files_sha256` equal and `returns_hash` equal (`4c487e47…`); `returns.json` is byte-identical. Counts, purge, member and terminal fields are equal | 513 opens; 0 reserve; 0 non-comparator real-store opens |
| **D7c** `ti-aa23fabd3db4e595` | `build_trusted_fold(development_fold())`: total_return_index (7,287,589), daily_total_return (7,456,944), dollar_volume (7,460,930) and segments (24,445) **EQUAL**. `files_sha256` keyed at the frozen paths is equal; `ti_hash` recomputed with the frozen function is **`30ee0c51…` = frozen** | 566 opens; 0 reserve; 0 non-comparator real-store opens |
| **P-Q source digest** | Computed under P-Q v2.4 (default development fold) on the rebuilt D7a/D7c: **`198a9606377fbbb24e6535385b05249456bf4e4844a78227d4f557a42a52cfed`** = the ADM digest. Flags 17,469 = 2,687 RESOLVED + 14,701 UNKNOWN_SOURCE + 81 UNKNOWN_RESERVE; 147 MISDATED candidates; 312 reused tickers (identical to the v2.1/v2.2/v2.3 ADMs) | 1,813 opens; 0 reserve; **0 real-store opens** |

## Defect found and repaired during the barrier (disclosed)

- **First D7c run: FAIL.** R7-T's fold code raised `TrustedFoldInputError` on the real D5
  day flags dated 2023-06-30. It had applied an invented post-load cap,
  `date <= prev(last grid session)` (2023-06-29).
- **What frozen D7c does:**
  - it requires only `date < RESERVE_START`;
  - `LAST_SOURCE_DATE = 2023-06-29` is a **read bound** for observations, corporate
    actions and D-SC, not a rejection cap.
- **Why the synthetic tests missed it:** none of them had rows on the last development
  session.
- **Repair:** returned as **R7-T-R2** (`89dbbf2`).
  - The post-load cap is removed; the read bound is kept.
  - A new last-session synthetic test compares against the frozen builder.
  - The new mutation `R7T-M_srcend` is killed; the whole trusted manifest (9) is killed in
    the planner re-run.
- **D7c re-run:** PASS, and the audit confirms the repaired module was loaded.
- **D6 and D7a** were unaffected; their results above are from the first runs.

## Also established at this barrier

- **The reserve gate (R7-X-R1).** The planner probed it adversarially. Each of these is
  **denied**:
  - a hand-built `fold.AccessGrant`;
  - a hand-built `access.AccessGrant`;
  - `dataclasses.replace` of a verified grant;
  - a pickle round-trip.
  Only `access.AccessGrant.from_kpit` grants open a reserve `FoldWindow`.
  **Residual limitation:** the gate guards against in-process misuse. It is not a
  sandbox against deliberately malicious in-process code.
- **K-PIT** `d1429eee…` was unchanged. The P-Q dataset-identity check read a **copy** in
  the scratchpad.

## Regression

- Full suite at `d274b35`: **6,513 passed, 1 skipped, 0 failed** (761 s). Planner run.
- At `eb2c534` (after the R7-X merge): 6,512 passed.

## Non-claims

- **Development fold only.** Reserve-fold behaviour on real data is not exercised, and it
  is not certified. It happens only in an authorized P6 confirmation.
- **Writer metadata.** D7c parquet files written under the fold's `ti_id` carry a
  different manifest identity (`ti-e078e37d7919dfe2`) by design, because it is the
  fold-module identity. The equivalence is content and frozen-path hash equality, per §1.
- **Content fields.** The D7a fold content records fold-relative `constants`; this differs
  from the frozen `returns.json` by design. It is not an identity field.

## Result

**P7 DEVELOPMENT-EQUIVALENCE BARRIER: PASS** (D6, D7a, D7c and the P-Q source digest are
all equal; 0 reserve opens). P7 is complete. The next step, the **Campaign-3 P5
precommitment**, requires its own design and authorization.
