# Dataset v1.2 — P-Q v2.1 software integration barrier evidence (PQ2-I1 … PQ2-I15, full recertification)

- **Status:** planner barrier evidence for **P-Q v2.1**. Authorized by the user's approval
  of the Waves 3–5 batch plan (`worker_tasks/pq_v2_1/pq21-wave3-5-batch-plan.md`), which
  includes the full I1–I15 recertification.
- **Master certified:** `94859fe9b78906076bb8568af2b2fcc80ac39d2c`. This document is the
  only file committed by this barrier.
- **Additive:** the historical P-Q v2 evidence
  `docs/discovery_dataset_v1_2_pq_v2_software_barrier.md` (`2281bebd…3f49`, master
  `11319e0`) is preserved unchanged. It certifies P-Q v2 only. **This document certifies
  P-Q v2.1 only.**
- **Mode:** **FULL I1–I15.** The frozen barrier spec defines no delta recertification.
- **Authority chain** (all 13 documents hash-verified in I1):
  - methodology v2.1 `66772bb1…5d1e`;
  - plan v2.1 `007ec880…77f9`;
  - specs PQ21-A `09197100…`, B `7462ae2a…`, C `6065e3a4…`, batch plan `25ce55cd…`,
    D `5d14dab9…`, E `df8f09c1…`, F `7ce81769…`, H `f45f6f4c…`;
  - errata G-E1 `cf841768…`, D-E1 `3a11e46c…`;
  - historical v2 methodology `a31ed9c4…` and its evidence `2281bebd…`.
- **Merged v2.1 worker commits:**

  | Task | Commit(s) |
  |---|---|
  | A | `69ce79e` |
  | B-R1 | `4f758d0` |
  | B | `7aa8616` |
  | C | `3db7b4d` |
  | D | `afe989c` |
  | E | `6935364` |
  | F | `baa1d9f` |
  | H | `92d0032` and `ea6ef79` (temporary integration test doubles removed) |

## Identities

| Identity | Value | Result |
|---|---|---|
| Methodology (v2.1) | `66772bb1b12a134ca35a3ff5e3d475e00c998b95d3cdcb1e3223fd5f012d5d1e` | bound |
| CS-ID | `432de09746bbc4c16c332d662c24d6d7369787d55e8b704e3a521494b73bdae2` | unchanged |
| ED-ID (v2.1) | `65bd7d1341e70674132bc7729870876adf5457fe9ce4c277d0f1148df343a4d6` | bound |
| CE-ID | `605fec93812d4165b6bf455a9fb453cd7c0c6c4a9f5a2a0fc418b53b0d0dbaaf` | unchanged |

## Environment

- **Interpreter and credentials:** the canonical interpreter; the eight provider
  credentials scrubbed per process.
- **Network guard:** `sitecustomize.py` SHA-256
  `6697069a5d5b5cba1c9da59d236efcde428e9b680e9dba1f76073d96fac38368`. Its hash is
  verified by the wrapper before **every** invocation; the self-test blocked 3 outbound
  paths with 0 credential names present.
- **Mutation runner:** `pq_mutrun.py` `1abd4514…bbfb3` (recreated earlier; see the v2
  evidence).
- **History preserved:** the earlier disclosure that the Wave 1–5 planner re-runs of
  P-Q v2 were not mechanically network-isolated stands unchanged.

## Results

**I1 — Frozen identity: PASS**
- the current authority is P-Q v2.1; the v2.1 document hash equals the constant;
- an independent CS/ED/CE recomputation equals the v2.1 constants and the frozen
  literals;
- `verify_authority()` passes for v2.1, and `verify_authority(authority=AUTHORITY_V2)`
  passes on the preserved v2 document;
- the stale v2 methodology hash and the stale v2 ED-ID are rejected by
  `require_current_authority`;
- 11/11 pinned code hashes match;
- tampering with the v2.1 methodology, and separately with a pinned file, is rejected;
- all 13 authority-chain documents match;
- 184 authority/identity tests pass.

**I2 — P-Q v1 non-regression: PASS**
- 0 changes since `0777a10` to the P-Q v1 modules, `engines/`, D5/D7a/D7b/D7c,
  `campaign/`, `spec/` and `evaluation/`; every non-doc change is a `pqv2_*` file;
- 594 P-Q v1 / D7 / Campaign-1 tests pass;
- `verify_overlay` passes with the rule hash matching; 0 forbidden opens.

**I3 — Dependency: PASS**
- 43 dependency tests pass (frozen table, certification, v2.1 cause propagation);
- `certify`: H1–H3 and H6–H7 → SIMPLE; H4–H5 → STD_X_RANK.

**I4 — Correction set: PASS**
- the S2 / Q_s regimes are as frozen;
- 684 oracle flag-states, 41 of them misdated, **0** misdated states combined with S2;
- G-M7 and C-M8 killed.

**I5 — Labels and sources: PASS**
- 69 tests: Candidate-E, v2.1 SOURCE/RESERVE classification and label causes, B-R1
  ticker reuse.

**I6 — Simple assignment: PASS**
- 67 tests, including the cause-preserving fail-closed behaviour;
- D-E1 domain: 5 qualifying worlds, 5 winsorization-block exclusions.
- **Recorded limitation:** the G simple census has no world inside Prop. 4's domain;
  coverage comes from the D-built worlds; no H6/H7 `volscaled` world is used at D's
  layer.

**I7 — Sandwich: PASS**
- 45 tests: full-cross-section causes, UNKNOWN short-circuiting before σ, suspect
  coverage.

**I8 — Turnover and cost: PASS**
- 10 turnover/cost tests.

**I9 — Oracle census and independence: PASS**
- 18/18 required cases (24 total); 27 self-tests pass;
- 0 forbidden oracle imports; 0 production references to the oracle; 0 `smart_beta`
  modules loaded when the oracle is imported.

**I10 — Mutations: PASS**

| Family | Killed | Notes |
|---|---|---|
| core | 20 | |
| dependency | 21 | |
| envelope | 28 | |
| evaluation | 27 | plus H-M13 and H-M14 below |
| labels/sources | 25 of 25 | union over the two test files; label mutations are killed by the labels tests, source mutations by the sources tests |
| oracle | 7 | |
| sandwich | 16 | |
| simple | 16 | |

- Every control passes.
- **H-M13:** remains an equivalent mutation, revalidated earlier (the identity check
  rejects duplicates).
- **H-M14:** remains a mutation-fixture ambiguity; it was killed in all three manual
  variants. No repair was authorized.

**I11 — Deterministic rebuild: PASS**
- two fresh processes (different `PYTHONHASHSEED` values and working directories) give
  byte-identical output: C1-H1 `12a64896…9cfe`, C1-H4 `bc75ad8c…baab`;
- the persisted bytes' hash equals the diagnostics hash;
- the four v2.1 identities are bound; no environment leak.

**I12 — Reserve firewall: PASS**
- 48 firewall/UNKNOWN boundary tests pass;
- the P-Q v2 suite under a **fresh** `--basetemp` with the file-open audit: 15,372 opens,
  **0** forbidden-pattern opens, 0 opens under the real `data_store`.
- **Audit note:** a first audited run under the default pytest temp root showed 321
  pattern hits. All of them were bare relative names (`reserve_sealed`, `dsc_evidence`,
  …) opened by pytest's `rmtree` rotation of old synthetic temp directories left by
  earlier full-suite runs. There were 0 opens under the real `data_store`, and the
  fresh-basetemp re-run is the evidence of record.

**I13 — K-PIT: PASS**
- `190d53f0…f6a2` is byte-identical before and after; 0 writes, 0 attempts, 0 slots.

**I14 — Campaign-1: PASS**
- the ledger, `report.json` and `report.md` are byte-identical; there is no `diagnostics/`
  directory; still 7 NOT_ASSESSED, not recomputed.

**I15 — Full suite: PASS**
- the P-Q v2 suite: 435 passed;
- the repository suite: **5521 passed, 1 skipped, 0 failed**.

## v2.1 cross-cutting verification

- **Cross-layer cause test** (B → C → D/E → F → H → persisted diagnostics; synthetic):

  | Case | Result |
  |---|---|
  | H4 (STD_X_RANK) SOURCE-only | {SOURCE}, L-T0 NOT_ASSESSED, `cause_union` (SOURCE) |
  | H4 unrelated flag (outside all H4 windows) | no period affected |
  | H1 SOURCE-only | {SOURCE} |
  | H1 both causes | {RESERVE, SOURCE}, primary RESERVE |

  - Under UNKNOWN, winsorization is never evaluated and no numeric L is produced.
  - 0 forbidden opens.
  - H's own end-to-end tests additionally cover RESERVE-only, a SOURCE label, and an
    unrelated flag for H1.
- **Vocabulary:**
  - no `PQV2_SOURCE_UNRESOLVED` → RESERVE alias anywhere;
  - no legacy `SIMPLE` rule tag;
  - the class vocabulary (`SIMPLE` / `STD_X_RANK`) and the rule vocabulary
    (`SIMPLE_EXACT` / `SANDWICH_CONSERVATIVE_SUPERSET`) stay separate.
- **Same-period invariant:** no cross-period `nanmean`, `dropna`, `fillna`, `ffill`,
  `bfill` or `gap_hac` in F/H. L on exactly B's periods. Per-period causes and their union
  are persisted.
- **T0/TS:** separate per-scenario cause lists; never combined.

## Non-claims

- **Scope:** this certifies that the integrated P-Q v2.1 A–H software implements the
  frozen v2.1 methodology correctly, conservatively and fail-closed **on synthetic
  evidence**.
- **Not certified:**
  - real-data admission (PQ2-ADM not run);
  - empirical usefulness, data quality or alpha;
  - Campaign advancement, confirmation, reserve use or production readiness.
- **Model limits:** UNKNOWN_SOURCE and UNKNOWN_RESERVE carry no numerical bound. P-Q v2.1
  is a declared correction-sensitivity model.
- **Carried forward:** Gate-6 INCONCLUSIVE; issuer-identity knowledge timing NOT
  CERTIFIED; the D evidence limitation; the H-M14 tooling ambiguity.

## Result

**P-Q v2.1 SOFTWARE IMPLEMENTED — PQ2-I1…I15 PASS**
