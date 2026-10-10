# Dataset v1.2 — P-Q v2.3 software integration barrier evidence (PQ2-I1 … PQ2-I15, full recertification)

- **Status:** planner barrier evidence for **P-Q v2.3** (EVENT_G). This is Wave 4 of the
  frozen implementation plan `docs/discovery_dataset_v1_2_pq_v2_3_implementation_plan.md`
  (`dc716635…`), authorized by the user's plan approval ("批准", 2026-10-09).
- **Master certified:** `553778c`. This document is the only file committed by this
  barrier.
- **Additive:** the v2, v2.1 and v2.2 evidence is unchanged. **This document certifies
  P-Q v2.3 only.**
- **Authority chain** (hash-verified):

  | Document | Hash |
  |---|---|
  | v2.3 methodology | `d4ec7f6c…0b26aad` |
  | v2.2 amendment | `ab12886c…` |
  | v2.1 base | `66772bb1…` |
  | M1b | `94309a61…` |
  | plan | `dc716635…` |
  | errata PQ22-E1 / PQ22-E2 | applied |

- **Merged v2.3 worker commits:**

  | Task | Commit |
  |---|---|
  | A | `8b1d936` |
  | B | `d256ade` |
  | G | `b52e1c2` |
  | C | `390df2d` |
  | H | `f6ff7ac` |

  P10-P `0142e22` was also merged in this interval, but it is not part of P-Q.

## Identities (P-Q v2.3; selectable — `CURRENT_AUTHORITY` remains v2.1)

| Identity | Value |
|---|---|
| Methodology | `d4ec7f6caaa6cc74a340fdcf28b5d2030b3a650c383518284099ebd5a0b26aad` (chain → v2.2 → v2.1) |
| ED-ID | `b3907c69878342fa9e8b07cdf35e4ddd6b47823fe0388ee9fb1762235e01ea7b` |
| CE-ID | `f3c7c7dfaa7de050b991fd64e7d586e69c787707834116d827301ad332d24a91` |
| CS-ID | `432de097…bdae2` (unchanged) |

## Environment

- **Interpreter and credentials:** the eight credentials scrubbed per process.
- **Network guard:** `6697069a…8368`, hash-verified on every invocation.
- **Planner tools** (scratchpad):

  | Tool | SHA-256 prefix |
  |---|---|
  | `b23/i1.py` | `5f3da053…` |
  | `b23/i11.py` | `02108f2b…` |
  | `b23/i11_event.py` | `4f2c2179…` |
  | `b23/run_rest.sh` | `503c6e6c…` |
  | `c_cross.py` | `16bae4d3…` |
  | `c23_cross.py` | `1f2aff16…` |
  | `pq_mutrun.py` | `1abd4514…` |

## Results

**I1 — Frozen identity (the chain): PASS**
- `CURRENT_AUTHORITY` = v2.1; admitted authorities = (v2.1, v2.2, v2.3);
- the v2.3 document hash equals the constant; its base equals the v2.2 amendment;
- the ED and CE lines extracted from the v2.3 document hash to the constants; CS is
  unchanged;
- `verify_authority` passes for v2.3, v2.2, v2.1 and v2;
- `verify_authority(v2.3)` **rejects** a one-byte tamper of the v2.3 document, of the
  v2.2 amendment and of the v2.1 base (the chain is enforced);
- a non-admitted triple → rejected;
- 11/11 pinned hashes match.

**I2 — Non-regression: PASS**
- 0 changes since `0777a10` to the v1, D7 and Campaign-1 files;
- 0 non-doc changes since the v2.2 certification (`b150fe8`) outside `pqv2_*`,
  `edgar_*`, `science/procedures/` and tests;
- 644 Campaign-1 / Dataset-v1.2 / D7 / Campaign-2 tests pass.

**I3 — Certification: PASS**
- the P-Q module suite: 641 tests, including events and the oracle;
- the **full-tier** grammar and event cross-checks (`PQV2_FULL_CROSSCHECK=1`): **12,015
  passed** in 883 s;
- planner census:

  | Population | Result |
  |---|---|
  | SIMPLE_G 2,000 / STD_X_RANK_G 2,000 / near-misses | certified per the oracle |
  | EVENT_G 2,000 + 20 adversarial (13 members, 7 outside: divisor and cross-section cases) | certified EVENT_SIMPLE / NOT_CERTIFIED per the oracle |

  **0 disagreements.** Under the v2.2 authority every event expression is rejected.
- **Harness note:** the planner's older v2.2 census script reported 2,000
  "SPEC_BUILD_FAIL" for the event class. That script cannot build event roles; the
  dedicated `c23_cross.py` is the evidence for EVENT_G.

**I4–I8: PASS**
- the unchanged B/D/E/F suites pass;
- full-tier membership ⊆, L ≥ min and turnover ≤ hold for every grammar and EVENT_G
  member, per scenario.

**I9 — Oracle independence: PASS**
- 0 `smart_beta` imports under `tests/pqv2_oracle/` (including `events.py`);
- 0 `smart_beta` modules loaded on import.

**I10 — Mutations: PASS**

| Family | Killed |
|---|---|
| events | 6 |
| core | 26 |
| dependency | 33 |
| envelope | 28 |
| evaluation | 35 |
| sandwich | 16 |
| simple | 16 |
| labels/sources | 25 of 25, by the union of the two test files |
| oracle (grammar + events) | 8 |
| oracle self | 7 |

- **H22-M3:** killed by its target cross-check file.
- **Carried forward unchanged:** H-M13 (equivalent) and H-M14 (tooling ambiguity).

**I11 — Deterministic rebuild: PASS**
- **Cases and runs:**
  - C1-H1 and C1-H4 under v2.1 and v2.2, plus one random SIMPLE_G and one STD_X_RANK_G
    member, are byte-identical across fresh processes (`PYTHONHASHSEED` 1 and 987);
  - the EVENT_SIMPLE positive-control shape (`ear`) is byte-identical across fresh
    processes (`PYTHONHASHSEED` 5 and 55);
  - with a morning event, K1 ≡ K0 (recorded `identical=True`);
  - with a **pre-close** event, K1 ≠ K0 (recorded `identical=False`).
- **Integrity:** persisted bytes equal the diagnostics hash; no path leak.
- **Planner tooling error, disclosed:** the first I11 invocation inside the batch script
  failed for both runs. `i11.py` had not been copied, and an unexpanded `$SP` caused a
  path failure, so its "identical" line was vacuous. I11 was re-run correctly, and the
  re-run is the evidence of record.

**I12 — Reserve firewall: PASS**
- 21 firewall / UNKNOWN tests pass;
- the P-Q suite under a fresh `--basetemp` with the file-open audit: 956 tests, 21,877
  opens, **0** forbidden-pattern opens, 0 opens under the real `data_store`.

**I13 / I14 — K-PIT and Campaign-1: PASS**
- byte-identical before and after;
- no Campaign-2 output directory.

**I15 — Full suite: PASS**
- at `553778c`, scrubbed and guarded: **6,288 passed, 1 skipped, 0 failed** (718 s);
- the sealed Phase-5A nested regression (900 s) passes.
- **Margin note:** the nested run is now about 11–12 minutes. Further test growth must
  keep the default tiers small.

## Non-claims

- **Synthetic evidence only:** this certifies the v2.3 software on synthetic evidence.
- **Not certified:**
  - real-data admission under v2.3 (PQ2-ADM v2.3 not run);
  - any empirical usefulness or alpha;
  - Campaign advancement;
  - reserve use.
- **Assumptions carried:** [A-STD] and [A-EVT]. Early closes are not boundary-adjusted.
  Identity knowledge timing is NOT CERTIFIED.
- **No authority switch:** `CURRENT_AUTHORITY` stays v2.1.

## Result

**P-Q v2.3 SOFTWARE IMPLEMENTED — PQ2-I1…I15 PASS**
