# Dataset v1.2 — P-Q v2.4 software integration barrier evidence (PQ2-I1 … PQ2-I15, full recertification)

- **Status:** planner barrier evidence for **P-Q v2.4** (the fold-parameterized temporal
  firewall). This is Wave 4 of `docs/discovery_dataset_v1_2_pq_v2_4_implementation_plan.md`
  (`0b4dc6e8…`), plan approval "1. 批准" (2026-10-09).
- **Master certified:** `d6e9e68` (P-Q v2.4 complete). The later merges R7-T, R7-R and
  R7-X are P7 code in `reserve_chain/` and are not part of P-Q.
- **Additive:** the v2–v2.3 evidence is unchanged. **This document certifies v2.4 only.**
- **Authority chain:**

  | Document | Hash |
  |---|---|
  | v2.4 methodology | `695973b9…aebed0` |
  | v2.3 | `d4ec7f6c…` |
  | v2.2 | `ab12886c…` |
  | v2.1 | `66772bb1…` |

- **Merged v2.4 worker commits:**

  | Task | Commit |
  |---|---|
  | A | `ee74e41` |
  | G | `db112c0` |
  | B | `8789ace` |
  | H | `28eddd8` |

## Identities (v2.4; selectable — CURRENT stays v2.1)

| Identity | Value |
|---|---|
| Methodology | `695973b9f1c7fcbc4979527a7b20ee0150fc5831da34722df5a7a37416aebed0` |
| ED-ID | `f8370828ae292f9e5bfc0a5a4e7054ba79c33fe88c1646793ad0f1d0dcc88d93` |
| CE-ID | `c40d8724ff6b6ee7862a07e39eb008da0e667a3826599e3850dff4e4993fa4c2` |
| CS-ID | unchanged |

## Environment

- **Credentials and network:** credentials scrubbed; guard `6697069a…8368` verified on
  every invocation.
- **Planner tools** (scratchpad `b24/`):

  | Tool | SHA-256 prefix |
  |---|---|
  | `i1.py` | `4f9d5186…` |
  | `i11_fold.py` | `9e6b7713…` |
  | `run_rest.sh` | `9d614a65…` |
  | `c23_cross.py` | `1f2aff16…` |
  | `pq_mutrun.py` | `1abd4514…` |

## Results

**I1 — Identity chain: PASS**
- CURRENT = v2.1; admitted = (v2.1, v2.2, v2.3, v2.4);
- the v2.4 document hash equals the constant; its base equals the v2.3 document;
- the ED and CE lines extracted from the v2.4 document hash to the constants; CS is
  unchanged;
- `verify_authority` passes for v2.4, v2.3, v2.2, v2.1 and v2;
- `verify_authority(v2.4)` **rejects a one-byte tamper of each** of the v2.4, v2.3,
  v2.2 and v2.1 documents;
- a non-admitted triple → rejected;
- 11/11 pinned hashes match.
- **Disclosure:** the first I1 attempt omitted the v2.3 document from the tamper list (a
  planner sed error). It was corrected and re-run; the re-run is the evidence.

**I2 — Non-regression: PASS**
- 0 changes since `0777a10` to the v1, D7 and Campaign-1 files;
- 0 non-doc changes since the v2.3 certification (`443156b`) outside the expected new or
  amended areas (`pqv2_*`, `edgar_*`, `science/procedures`, `campaign3_*`,
  `research/llm_generator`, `reserve_chain/`, tests);
- 644 Campaign-1 / Dataset-v1.2 / D7 / Campaign-2 tests pass.
- **The default-fold byte identity** is established by PQ24-B/H review: 587 and 487
  regression tests unchanged, including every v2.1–v2.3 golden, the cross-checks,
  Campaign-2 and C3-X.

**I3 — Certification: PASS**
- the P-Q module suite: 665 tests;
- **full-tier** grammar and event cross-checks: **12,015 passed** (882 s);
- planner census:
  - SIMPLE_G / STD_X_RANK_G / near-misses: 0 disagreements;
  - **EVENT_G** 2,000 + 20 adversarial: **0 disagreements**.
- **Disclosure:** the EVENT_G census line was missing from the batch script. It was run
  separately; the separate run is the evidence.

**I4–I8: PASS** (included in the suites above; reserve-fold synthetic brute-force cases
from PQ24-G).

**I9 — Oracle independence: PASS**
- 0 `smart_beta` imports; 0 modules loaded; 24 named cases.

**I10 — Mutations: PASS**

| Family | Killed |
|---|---|
| events | 7 (incl. B24-M4) |
| core | 34 |
| dependency | 33 |
| envelope | 28 |
| evaluation | 37 (incl. H24-M1/M2) |
| sandwich | 16 |
| simple | 16 |
| labels/sources | **28 of 28 by union** (incl. B24-M1…M3) |
| oracle | 13 |
| oracle self | 7 |

- H22-M3 is killed by its target file.
- Carried forward: H-M13 (equivalent) and H-M14 (tooling).

**I11 — Deterministic rebuild: PASS**
- byte-identical across fresh processes for:
  - C1-H1/H4 under v2.1 and v2.2, plus random SIMPLE_G and STD_X_RANK_G members;
  - EVENT_SIMPLE (morning K1 ≡ K0; pre-close K1 ≠ K0);
  - a **synthetic stitched reserve fold** (C1-H1, fold {reserve, 2023-07-03,
    2023-07-21}, a beyond-E flag → `PQV2_FOLD_END_UNKNOWN`).
- No path leaks; persisted bytes equal the diagnostics hash.

**I12 — Firewall: PASS**
- 31 firewall/UNKNOWN tests pass;
- the fresh-basetemp audited P-Q suite: 996 tests, 23,887 opens, **0 opens under the real
  `data_store`**.
- **17 forbidden-pattern opens:** every one is a **synthetic** fixture path under the
  test basetemp (`…/reserve_sealed/edgar-aaaaaaaaaaaaaaaa/…`).
  - 15 come from the reserve-fold tests, which intentionally read the synthetic reserve
    package.
  - 2 occur in the default-fold test `test_default_fold_reads_development_layer_not_reserve`.
    A mode-aware re-audit shows both are **writes** (`w`) by fixture setup. **The loader
    made 0 reserve reads under the default fold**; the test asserts that the reserve
    events are absent.

**I13 / I14 — K-PIT and Campaign-1: PASS**
- K-PIT `d1429eee…` (after the EWC admission) and the Campaign-1 artifacts are
  byte-identical before and after;
- no Campaign-2 output directory.

**I15 — Full suite: PASS**
- at `d6e9e68`: **6,468 passed, 1 skipped, 0 failed** (764 s);
- the sealed Phase-5A nested regression passes.
- **Margin warning:** the suite is now about 12.7 minutes against the 15-minute nested
  bound. Further growth must use default-tier sizing.

## Non-claims

- **Synthetic evidence only.** No real reserve data was read by this barrier or by any
  v2.4 task.
- **Not certified:** reserve-fold real-data behaviour (which happens only in an
  authorized P6 confirmation), any alpha, Campaign advancement.
- **Assumptions:** [A-STD] and [A-EVT]. Early closes are not adjusted. Identity knowledge
  timing is NOT CERTIFIED.

## Result

**P-Q v2.4 SOFTWARE IMPLEMENTED — PQ2-I1…I15 PASS**
