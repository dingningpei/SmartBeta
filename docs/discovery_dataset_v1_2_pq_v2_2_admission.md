# Dataset v1.2 — P-Q v2.2 real development-data admission evidence (PQ2-ADM, from ADM-1)

- **Status:** planner admission evidence. Authorized by the user ("两件事都批准",
  2026-10-09).
- **Fresh run:** a full run from ADM-1. No earlier ADM result is reused.
- **Software bound:**
  - master `b150fe8`;
  - the certified P-Q v2.2 software (I1–I15 evidence
    `docs/discovery_dataset_v1_2_pq_v2_2_software_barrier.md`, `498e4723…`, certifying
    tree `93b6a54`). `b150fe8` adds only that evidence document.
- **Admission spec:** the frozen PQ2-ADM (Part 2 of
  `docs/discovery_dataset_v1_2_pq_v2_integration_barrier.md`), plus the v2.2 plan §6
  additions to ADM-4:
  - the perturbed C1-H1 must certify as SIMPLE under v2.2;
  - one random grammar member per class.
- **Authority selection:** v2.2 is evaluated **explicitly** (`authority=AUTHORITY_V2_2`).
  `CURRENT_AUTHORITY` remains v2.1 (PQ22-E1), and Campaign-2 stays bound to v2.1.
- **No empirical execution:** no factor values, L_t, B/L statistics, screens, Campaign
  attempts or slots were computed or consumed.

## Exact identities recorded from the verified run

| Item | Value |
|---|---|
| P-Q authority | P-Q v2.2 — methodology `ab12886cdbac42725bcc283d2e37567e15340b3b80cb603f2a4df8a31a77af76` (base v2.1 `66772bb1…5d1e`), CS-ID `432de097…bdae2`, ED-ID `a67afc6b319721da5a11f419681ca1d489046e627dfe43242ea6eb54f36388ef`, CE-ID `81cafb32030681023be9328c0ceda1d9d78e502071377692b19dc302ce699d5d` |
| Dataset v1.2 identity | `a065493d295883f425d499816a98ac55a03cd67a1086bd7b64249b90fe76b9da` |
| P-Q overlay | `pq-59c3bd7abe08eef5` / `baa920ddfc52805c70445abfb8650e2969d341ae9383e1b1a0c6a788fcc95f2c` |
| D7c trusted inputs | `ti-aa23fabd3db4e595` / `30ee0c51602cf792e8cb7654c2501f52c7d664675dbf78845c903df6683c2ae6` |
| D7a artifact | `r-34565f074d64ccda` |

## Environment

- **Interpreter and credentials:** the canonical interpreter; the eight credentials
  scrubbed per process.
- **Network guard:** `6697069a…8368`, hash-verified on every invocation; the self-test
  blocked 3 outbound paths, with 0 credential names present.
- **Planner scripts** (scratchpad, not committed): `adm22/adm2.py` (adapted from the v2.1
  ADM-2 script: v2.2 authority, explicit selection), `adm22/adm4.py`, and the v2.1
  `adm21/adm5.py`.

## Results

**ADM-1 — Authority and pinned code: PASS**
- `verify_authority(authority=AUTHORITY_V2_2)` passes, including the base document;
- `require_admitted_authority` resolves the v2.2 triple to `AUTHORITY_V2_2`;
- `CURRENT_AUTHORITY` is v2.1;
- 11/11 pinned code hashes match.

**ADM-2 — Real development source set: PASS**
- **Determinism:** two fresh runs (`PYTHONHASHSEED` 1 and 2) are identical except for
  `wall_s` / `peak_rss_mb`. Source-set digest
  `198a9606377fbbb24e6535385b05249456bf4e4844a78227d4f557a42a52cfed`, identical to the
  v2.1 ADM.
- **Flags:** 17,469 = **2,687 RESOLVED + 14,701 UNKNOWN_SOURCE** (13,459 never in the
  masked D7c panel + 1,242 non-finite) **+ 81 UNKNOWN_RESERVE**. An exact partition.
- **RESOLVED m_s range:** [0.0005, 1000.0], an observation only.
- **MISDATED candidates:** 147 (94 split, 53 dividend); 0 flags or candidates outside
  their key's interval.
- **Structural census** (v2.2 authority, C1 specs; identical to v2.1):

  | Item | Result |
  |---|---|
  | Unresolved flags reached by ≥ 1 dependency | **203** |
  | Never reached | **14,579** |
  | Planner-vs-C reach mismatches | 0 |

  | Hypothesis | Cells | SOURCE causes |
  |---|---|---|
  | C1-H1 | 36,000 | 23 |
  | C1-H2 | 36,000 | 8 |
  | C1-H3 | 36,000 | 1 |
  | C1-H4 | 242,520 | 898 |
  | C1-H5 | 242,520 | 892 |
  | C1-H6 | 36,000 | 23 |
  | C1-H7 | 36,000 | 8 |
  | Labels | 36,000 | 0 |

- **Ticker reuse:** 312 reused tickers, 117 of them with overlapping intervals.
- **Why it is identical to v2.1:** B (sources/labels) and the C1 dependency windows are
  unchanged by v2.2. The census measures structural reach only, **not** factor
  performance.

**ADM-3 — Firewall read audit: PASS**
- 1,751 opens per run;
- **0** under `reserve_sealed/`, `raw/` (including `raw/edgar-*`), `dsc_evidence` or a
  final holdout;
- 1 read-only open of `knowledge_pit/knowledge.jsonl`, by the frozen dataset identity
  check.

**ADM-4 — Certification table: PASS**

| Case | v2.2 | v2.1 |
|---|---|---|
| C1-H1/H2/H3/H6/H7 | SIMPLE | SIMPLE |
| C1-H4/H5 | STD_X_RANK | STD_X_RANK |
| **Perturbed C1-H1 (lag 252 → 251)** | **SIMPLE** | `PQV2_EXPRESSION_NOT_CERTIFIED` |
| Random SIMPLE_G member (`generate_population(seed=20261008)[SIMPLE_G][7]`) | SIMPLE | — |
| Random STD_X_RANK_G member | STD_X_RANK | — |
| A near-miss (`cross_sectional_in_simple`) | `PQV2_EXPRESSION_NOT_CERTIFIED` | — |

- The perturbed C1-H1 is the motivating case: it was NOT_CERTIFIED under v2.1 and must be
  SIMPLE under v2.2.

**ADM-5 — Resource bound: PASS (recorded)**
- **The real ADM-2 run:** about 87–89 s wall, peak RSS about 2.3 GB.
- **Synthetic full-size sub-expression realization** (2138 × 10353): 2.9 s, peak RSS
  1.65 GB.
- **Bound:** no numeric bound is frozen; these are within a single-machine envelope.

**ADM-6 — Immutability: PASS**
- The following are byte-identical before and after:
  - K-PIT `190d53f0…f6a2`;
  - the Campaign-1 ledger, `report.json` and `report.md`;
  - the v2.1 admission evidence;
  - the v2.2 software evidence.
- No Campaign-2 output directory exists.
- 0 K-PIT writes, 0 Campaign attempts, 0 slots.

## Non-claims (preserved)

- **Gate-6:** INCONCLUSIVE; data quality is not certified.
- **Identity timing:** issuer-identity knowledge timing is NOT CERTIFIED.
- **The model:** P-Q v2.2 is a declared correction-sensitivity model, not a truth bound.
  UNKNOWN causes carry no numeric bound. `[A-STD]` is an assumption.
- **Scope:** only expressions in the A§15A grammars are certified; sign +1 only.
- **What admission does not mean:** no empirical success, alpha, Campaign advancement,
  confirmation, or reserve or final-holdout use.
- **No authority switch:** `CURRENT_AUTHORITY` stays v2.1.

## Disposition

**PASS** means exactly this: the certified P-Q v2.2 software, selected explicitly, is
admitted to consume the frozen real Dataset v1.2 **development** inputs for a
**separately precommitted and authorized** empirical execution.

**P-Q v2.2 PQ2-ADM PASS — ADMITTED FOR PROSPECTIVE DEVELOPMENT USE (explicit authority selection)**
