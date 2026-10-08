# Dataset v1.2 — P-Q v2.1 real development-data admission evidence (PQ2-ADM, restarted from ADM-1)

- **Status:** planner admission evidence. Authorized by the user ("从 ADM-1 重新开始做 PQ2-ADM
  准入", 2026-10-08).
- **Fresh run:** this is a full restart from ADM-1. No result from the earlier failed
  P-Q v2 ADM runs is reused.
- **Software bound:** master `13062cead34eab867d0d59187d064a89ecebbfae`. This is the
  certified v2.1 software (I1–I15 evidence
  `docs/discovery_dataset_v1_2_pq_v2_1_software_barrier.md`, `7b28231f…2d15`, certifying
  tree `94859fe`); `13062ce` adds only that evidence document.
- **Admission spec:** frozen PQ2-ADM, Part 2 of
  `docs/discovery_dataset_v1_2_pq_v2_integration_barrier.md`, ADM-1…ADM-6, plus the
  v2.1 plan §6 additions (the structural census and the B-R1 interval probe).
- **No empirical execution:** no factor values, L_t, B/L statistics, screens, Campaign
  attempts or slots were computed or consumed.

## Exact identities recorded from the verified run

| Item | Value |
|---|---|
| P-Q authority | P-Q v2.1 — methodology `66772bb1b12a134ca35a3ff5e3d475e00c998b95d3cdcb1e3223fd5f012d5d1e`, CS-ID `432de09746bbc4c16c332d662c24d6d7369787d55e8b704e3a521494b73bdae2`, ED-ID `65bd7d1341e70674132bc7729870876adf5457fe9ce4c277d0f1148df343a4d6`, CE-ID `605fec93812d4165b6bf455a9fb453cd7c0c6c4a9f5a2a0fc418b53b0d0dbaaf` |
| Dataset v1.2 identity (`verify_dataset_v1_2_identity`) | `a065493d295883f425d499816a98ac55a03cd67a1086bd7b64249b90fe76b9da` |
| P-Q overlay | pq_id `pq-59c3bd7abe08eef5`, pq_hash `baa920ddfc52805c70445abfb8650e2969d341ae9383e1b1a0c6a788fcc95f2c` |
| D7c trusted inputs | ti_id `ti-aa23fabd3db4e595`, ti_hash `30ee0c51602cf792e8cb7654c2501f52c7d664675dbf78845c903df6683c2ae6` (manifest `files_sha256` verified by `load_trusted_inputs`) |
| D7a artifact | `r-34565f074d64ccda` |

## Environment

- **Interpreter and credentials:** the canonical interpreter; the eight provider
  credentials scrubbed per process (0 credential names present).
- **Network guard:** `sitecustomize.py` `6697069a5d5b5cba1c9da59d236efcde428e9b680e9dba1f76073d96fac38368`.
  Its hash is verified before every invocation; the self-test blocked 3 outbound paths.

## Results

**ADM-1 — Authority and pinned code: PASS**
- `verify_authority()` (v2.1) and `require_current_authority` pass; 11/11 pinned code
  hashes match.

**ADM-2 — Real development source set: PASS** (counts only; deterministic across two
fresh runs; source-set digest
`198a9606377fbbb24e6535385b05249456bf4e4844a78227d4f557a42a52cfed`)
- **Flags:** 17,469 D5 flags = **2,687 RESOLVED** + **14,701 UNKNOWN_SOURCE** + **81
  UNKNOWN_RESERVE**, an exact partition.
  - UNKNOWN_SOURCE: 13,459 never in the masked D7c panel + 1,242 present but non-finite
    at row next(s).
  - UNKNOWN_RESERVE: 81 by the M§8A.1 temporal/firewall rule.
- **RESOLVED m_s range:** [0.0005, 1000.0]. Extreme suspect transitions exist; S2 covers
  them through Q_s. This is an observation, **not** a data-quality claim.
- **MISDATED candidates:** 147 (94 split, 53 dividend), 0 outside their key's D4
  interval. The D7a `ca_event_count` cross-check, run inside the loader, passed.
- **No global abort.** Every input-validation check passed: overlay, D4 intervals, CA
  validation, trusted sessions.

**ADM-2 (v2.1 additions) — Structural census and B-R1 probe: PASS**

| Item | Result |
|---|---|
| Unresolved flags reached by at least one frozen dependency | **203** |
| Unresolved flags never reached | **14,579** |
| Planner-recomputed vs C reach mismatches | 0 |

Structural cells receiving a cause (IS/OOS formations, 72):

| Hypothesis | Cells | SOURCE | RESERVE | Both |
|---|---|---|---|---|
| C1-H1 | 36,000 | 23 | 0 | 0 |
| C1-H2 | 36,000 | 8 | 0 | 0 |
| C1-H3 | 36,000 | 1 | 0 | 0 |
| C1-H4 | 242,520 (full cross-section) | 898 | 0 | 0 |
| C1-H5 | 242,520 (full cross-section) | 892 | 0 | 0 |
| C1-H6 | 36,000 | 23 | 0 | 0 |
| C1-H7 | 36,000 | 8 | 0 | 0 |
| Labels (universe members, h = 21) | 36,000 | 0 | 0 | 0 |

- **B-R1:** 312 reused tickers (117 with overlapping D4 intervals); 0 flag or candidate
  assignments outside the key's development-clipped interval; deterministic.
- **What the census measures:** cells *structurally reached by an unresolved flag*. It
  says **nothing** about factor performance.

**ADM-3 — Firewall read audit: PASS**
- **Opens:** 1,750 file opens per run; **0** under `reserve_sealed/`, `raw/` or
  `dsc_evidence`; 0 final-holdout reads; 0 network calls.
- **Allowlist classification:**
  - 39 opens under `data_store/discovery_dataset_v1/derived` (D7a, D7c, overlay);
  - 9 under `.../normalized` (D3 CA events, D4 identity);
  - 1 **read-only** open of `knowledge_pit/knowledge.jsonl`, by the frozen dataset
    identity check;
  - the rest are repository code and the venv.
- **Firewall rows:** no row dated ≥ `RESERVE_START` is read (the loaders fail closed on
  post-firewall rows).

**ADM-4 — Certification table: PASS**
- `certify`: H1–H3 and H6–H7 → SIMPLE; H4–H5 → STD_X_RANK.
- A perturbed H1 (lag 252 → 251) → `PQV2_EXPRESSION_NOT_CERTIFIED`.

**ADM-5 — Resource bound: PASS (recorded)**
- **Real ADM-2 run:** about 84 s wall, peak RSS about 2.8 GB.
- **Synthetic full-size sub-expression realization** (2138 × 10353; M, V, standardize,
  rank, multiply; no real factor realization): about 2.5 s, peak RSS about 1.6 GB.
- **Bound:** no numeric bound was frozen. These measurements establish it (plan §11),
  and they are well within a single-machine envelope.

**ADM-6 — Immutability: PASS**
- The K-PIT log (`190d53f0…f6a2`), Campaign-1 ledger / `report.json` / `report.md`, the
  P-Q v2 evidence and the P-Q v2.1 evidence are byte-identical before and after.
- No Campaign-1 `diagnostics/` directory exists.
- 0 K-PIT writes, 0 Campaign attempts, 0 slots.

## Non-claims (preserved)

- **Gate-6:** INCONCLUSIVE; data quality is **not** certified.
- **Identity timing:** historical issuer-identity knowledge timing remains NOT CERTIFIED.
- **The model:** P-Q v2.1 is a declared correction-sensitivity model, not a truth bound.
  UNKNOWN_SOURCE and UNKNOWN_RESERVE carry no numerical bound.
- **Carried forward:** the PQ2-D theorem-evidence limitation remains; H-M14 remains a
  mutation-fixture ambiguity.
- **Network history:** the earlier P-Q v2 Wave 1–5 planner re-runs were not mechanically
  network-isolated (disclosed in the v2 evidence; unchanged).
- **What admission does not mean:** it does not imply empirical success, alpha,
  Campaign advancement, confirmation, or any reserve or final-holdout use.

## Disposition

**PASS** means exactly this: the certified P-Q v2.1 software is admitted to consume the
frozen real Dataset v1.2 **development** inputs for a **separately authorized** empirical
execution.

**P-Q v2 PQ2-ADM PASS — REAL DEVELOPMENT EXECUTION MAY BE SEPARATELY AUTHORIZED**
