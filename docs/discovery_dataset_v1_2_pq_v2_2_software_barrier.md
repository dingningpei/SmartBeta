# Dataset v1.2 — P-Q v2.2 software integration barrier evidence (PQ2-I1 … PQ2-I15, full recertification)

- **Status:** planner barrier evidence for **P-Q v2.2**. This is Wave 4 of the frozen
  implementation plan `docs/discovery_dataset_v1_2_pq_v2_2_implementation_plan.md`
  (`212fcd82…e9cd21b8`), authorized by the user's plan approval ("按照你的建议",
  2026-10-08).
- **Master certified:** `93b6a54`. This document is the only file committed by this
  barrier.
- **Additive:** the P-Q v2 and v2.1 evidence (`2281bebd…`, `7b28231f…`) is preserved
  unchanged. **This document certifies P-Q v2.2 only.**
- **Mode:** FULL I1–I15, plus the plan §6 additions:
  - the I3 grammar census;
  - the I9 generic-evaluator independence audit;
  - an I11 rebuild of one random grammar member per class.
- **Authority chain** (hash-verified):

  | Document | Hash |
  |---|---|
  | amendment | `ab12886c…1a77af76` |
  | v2.1 base | `66772bb1…5d1e` |
  | plan | `212fcd82…e9cd21b8` |
  | PQ22-A | `d546e0c7…` |
  | PQ22-G | `81ea49b4…` |
  | PQ22-C | `cadfb600…` |
  | PQ22-H | `d02b9327…` |
  | erratum PQ22-E1 | `c354c4fd…f39f1538` (authority selection; CURRENT stays v2.1) |
  | erratum PQ22-E2 | `cd97b82e…e4a6c50c` (two-tier cross-check) |

- **Merged v2.2 worker commits:**

  | Task | Commit(s) |
  |---|---|
  | A | `b7e0138`, then A-R1 `6811482` |
  | G | `1c68185` |
  | C | `605a6d8` |
  | H | `7b4f4e3`, then H-R1 `4d1e333` (NaN-member ranking, M§2), then H-R2 `34416f1` (two-tier) |

## Identities (P-Q v2.2; selectable — `CURRENT_AUTHORITY` remains v2.1)

| Identity | Value | Result |
|---|---|---|
| Methodology | `ab12886cdbac42725bcc283d2e37567e15340b3b80cb603f2a4df8a31a77af76` (bound with the base `66772bb1…`) | bound |
| CS-ID | `432de09746bbc4c16c332d662c24d6d7369787d55e8b704e3a521494b73bdae2` | unchanged |
| ED-ID | `a67afc6b319721da5a11f419681ca1d489046e627dfe43242ea6eb54f36388ef` | bound |
| CE-ID | `81cafb32030681023be9328c0ceda1d9d78e502071377692b19dc302ce699d5d` | bound |

## Environment

- **Interpreter and credentials:** the canonical interpreter; the eight credentials
  scrubbed per process.
- **Network guard:** `sitecustomize.py` `6697069a…8368`, hash-verified on every
  invocation.
- **Planner tools** (scratchpad, not committed):

  | Tool | SHA-256 prefix |
  |---|---|
  | `i1.py` | `30a9b5b1…` |
  | `i11.py` | `02108f2b…` |
  | `run_rest.sh` | `9b36ba67…` |
  | `c_cross.py` | `16bae4d3…` |
  | `pq_mutrun.py` | `1abd4514…` |

- **Concurrent load:** the E-1L L3 SEC acquisition ran at the same time (a separate
  process, networked, no P-Q code). It does not affect any result below.

## Results

**I1 — Frozen identity: PASS**
- `CURRENT_AUTHORITY is AUTHORITY_V2_1`; admitted authorities = (v2.1, v2.2);
- the amendment and base hashes equal their constants;
- an **independent** recomputation reproduces the constants: the CE line extracted from
  the amendment; the ED line computed as the v2.1 list plus `add_sub_support_union` and
  `pq-v2.2-ed-3`; CS unchanged;
- `verify_authority` passes for v2.2, v2.1 and v2;
- tampering with the amendment, and separately with the base document → rejected;
- a non-admitted identity triple → rejected;
- 11/11 pinned code hashes match.

**I2 — P-Q v1 non-regression: PASS**
- 0 changes since `0777a10` to the v1, D7a/b/c, Campaign-1, `engines/`, `spec/` and
  `evaluation/` files;
- 0 non-doc changes since the v2.1 certification (`13062ce`) outside `pqv2_*`,
  `edgar_*`, Campaign-2 and tests;
- 644 Campaign-1 / Dataset-v1.2 / D7 / Campaign-2 tests pass.

**I3 — Dependency and grammar certification: PASS**
- the P-Q module suite passes: 556 tests;
- the **full-tier** cross-check (`PQV2_FULL_CROSSCHECK=1`, PQ22-E2) passes: **8,007
  tests** in 863 s;
- planner census (independent of H's test):

  | Class | Expressions | Result |
  |---|---|---|
  | SIMPLE_G | 2,000 | all certified SIMPLE |
  | STD_X_RANK_G | 2,000 | all certified STD_X_RANK |
  | OUTSIDE near-misses | 13 | NOT_CERTIFIED |
  | (one near-miss) | 1 | rejected earlier, at FactorSpec construction |

  **0 disagreements** between production `certify` and the oracle classifier.
- Under the v2.1 authority only the exact C1 forms certify (2 generator hits); all
  others are NOT_CERTIFIED.

**I4–I8 — Correction set, labels, simple, sandwich, turnover: PASS**
- the unchanged B/D/E/F suites pass inside the 556 above;
- full-tier membership ⊆, L ≥ min and turnover ≤ hold for every grammar member (part of
  the 8,007).

**I9 — Oracle independence: PASS**
- 0 `smart_beta` imports under `tests/pqv2_oracle/`;
- 0 `smart_beta` modules loaded on import of `oracle`, `ast` and `grammar`;
- 24 named oracle cases.

**I10 — Mutations: PASS**

| Family | Killed |
|---|---|
| core | 23 |
| dependency | 29 |
| envelope | 28 |
| evaluation | 32 |
| sandwich | 16 |
| simple | 16 |
| labels/sources | 25 of 25, by the union of the two test files (the frozen rule) |
| oracle grammar | 4 |
| oracle self | 7 |

- Every control passes.
- **H22-M3** survives against `test_pqv2_evaluation.py` but is **killed** by its target,
  `test_pqv2_grammar_crosscheck.py`: it is listed in the evaluation manifest but targets
  the cross-check file.
- **Carried forward unchanged:** H-M13 (an equivalent mutation, approved earlier) and
  H-M14 (the fixture-tooling ambiguity).

**I11 — Deterministic rebuild: PASS**
- two fresh processes, with `PYTHONHASHSEED` 1 and 987 and different working
  directories, produce identical output;
- the cases are C1-H1 and C1-H4 under v2.1 and under v2.2, plus one random SIMPLE_G and
  one STD_X_RANK_G member under v2.2;
- persisted bytes equal the diagnostics hash; no path leak;
- v2.2 diagnostics carry `methodology_version` / `base_methodology_sha256`; v2.1
  diagnostics do not (byte-identical v2.1 shape).
- **Barrier finding (repaired before this evidence):** the first I11 run on `debee06`
  crashed on the random SIMPLE_G member. H passed NaN-factor universe members to D,
  against M§2 ("grouping over the n non-NaN members"). H-R1 `4d1e333` ranks exactly B's
  finite-factor grouped members and asserts equality with B's groups. This was a latent
  v2.1 defect, unreachable on the C1 fixture.
- **C1 golden:** the v2.1 C1 golden was **independently regenerated** by the planner on
  the pre-H master `73e6687`, and it is byte-identical to H's committed golden.

**I12 — Reserve firewall: PASS**
- 21 firewall / UNKNOWN boundary tests pass;
- the P-Q suite under a **fresh** `--basetemp` with the file-open audit: 763 tests,
  19,223 opens, **0** forbidden-pattern opens (`reserve_sealed`, `/raw/`,
  `dsc_evidence`, `final_holdout`), and 0 opens under the real `data_store`.

**I13 — K-PIT: PASS**
- `190d53f0…f6a2` is byte-identical before and after.

**I14 — Campaign-1: PASS**
- the ledger `85bde1e7…`, `report.json` `37082653…` and `report.md` `2079ec6f…` are
  unchanged;
- there is no `diagnostics/` directory, and no Campaign-2 output directory.

**I15 — Full suite: PASS**
- at `93b6a54`, scrubbed and network-guarded: **6,031 passed, 1 skipped, 0 failed**. The
  default cross-check tier is included.
- The sealed Phase-5A nested regression (`test_item12_full_regression`, 900 s) passes.
- **Before H-R2,** the single-tier cross-check made it fail deterministically: 1 failed
  / 13,825 passed at `debee06`. That was repaired by PQ22-E2 / H-R2; the sealed test was
  not modified.

## Non-claims

- **Scope:** this certifies that the integrated P-Q v2.2 software implements the frozen
  v2.2 amendment (on its v2.1 base) correctly, conservatively and fail-closed **on
  synthetic evidence**.
- **Not certified:**
  - real-data admission under v2.2 (PQ2-ADM not run);
  - empirical usefulness, data quality or alpha;
  - Campaign advancement;
  - reserve use;
  - the `[A-STD]` assumption, which is an assumption and not a proof.
- **No authority switch:** `CURRENT_AUTHORITY` stays v2.1 (PQ22-E1). Campaign-2 remains
  bound to v2.1.
- **Carried forward:** Gate-6 INCONCLUSIVE; issuer-identity knowledge timing NOT
  CERTIFIED; the H-M14 tooling ambiguity.

## Result

**P-Q v2.2 SOFTWARE IMPLEMENTED — PQ2-I1…I15 PASS**
