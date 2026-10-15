# Campaign-4 M0 — task C4M0-13 (M0-1 direction audit + M0-3 common exposure) (DRAFT; not frozen)

## Authority

- `docs/campaign4_method_program_draft.md` §1 (M0-1, M0-3);
- `docs/campaign4_m1a_generator_contract_design_draft.md` decision Q1 (user, 2026-10-15);
- the binding constraints of the frozen `worker_tasks/campaign4_m0/campaign4-m0-plan.md` §2
  (IS only, no K-PIT append, no network, new code in `smart_beta/diagnostics/` only, pinned
  and C3-bound files import-only, reuse instead of re-implementation);
- base: master at freeze.

## Purpose

These are the two diagnostics that calibrate the M1-a contract on evidence:
- **M0-1:** did the Campaign-3 generator build factors whose direction contradicts its own
  stated rationale (the F2 convention gap)?
- **M0-3:** are the 99 candidates a few repeated bets, and how many near-duplicates would
  the A4 gate (|ρ| > 0.90) have removed?

## Inputs (all persisted, all generator-visible)

| Input | Path |
|---|---|
| The 99 candidates (digests, spec hashes, proposal ids) | `data_store/campaign3/d6c34dbc…/generation.json` |
| Proposals (spec, `research_question`, `economic_rationale`, `generation_reason`) | `data_store/campaign3/76a2c212…/generation_state/executor_state.json` |
| IS long-short series | `data_store/campaign3/76a2c212…/screen_cache/<digest>.export.json` |
| Factor values | `ProductionAssembly.fast_panel_for(spec, is_fold)` (the C3-L-R6 production path) |
| Planner rationale coding (§1.2) | `docs/campaign4_m0_1_rationale_coding.json`, SHA-256 passed on the command line and verified |

## 1. M0-1 — direction audit

### 1.1 Reference drivers (raw orientation: high value = high economic variable)

| Id | Expression | Planner pre-check (2026-10-15) |
|---|---|---|
| D-MOM | `lag(tri,21) / lag(tri,252)` (= KF-MOM12) | SIMPLE |
| D-REV | `tri / lag(tri,21)` (= −KF-REV1) | SIMPLE |
| D-VOL | `rolling_std(ret,252)` (= −KF-LVOL) | SIMPLE |
| D-DVT | `rolling_mean(dv,21) / rolling_mean(dv,252)` | SIMPLE |
| D-EAR | `event_ear_k_kp1` (= KF-EAR) | EVENT_SIMPLE |

A driver that fails certification is reported and omitted; it is never altered.

### 1.2 Planner rationale coding (REVIEWER JUDGMENT; blind)

- **Who and when:** the planner, after this spec is frozen and **before** any exposure is
  computed.
- **What the planner reads:** only `research_question`, `economic_rationale`,
  `generation_reason` and the spec `description` / `hypothesis`. It does **not** read the
  expression AST or any per-candidate result.
- **Code per proposal:**
  - `driver` ∈ {MOM, REV, VOL, DVT, EAR, OTHER};
  - `relation` ∈ {+, −, ?}: the sign the rationale predicts between the **raw** economic
    variable (as oriented in §1.1) and future return. Examples: "past winners keep
    winning" is MOM +; "short-term overreaction reverts" is REV −; "low volatility earns
    more" is VOL −.
- **Recording:** the file is committed with its SHA-256 before the run.

### 1.3 Exposure and classification (mechanical)

- **Exposure:** `E[i,d]` = the mean over IS formations of the cross-sectional Spearman
  correlation between candidate *i*'s factor values and driver *d*'s, over common non-NaN
  members. A formation with fewer than 30 common members is skipped.
- **Classification** of each coded candidate (driver ≠ OTHER, relation ≠ ?), with
  `x = E[i, driver]`:

  | Class | Condition |
  |---|---|
  | **CONSISTENT** | \|x\| ≥ 0.20 and sign(x) = relation |
  | **INVERTED** | \|x\| ≥ 0.20 and sign(x) ≠ relation |
  | **WEAK** | \|x\| < 0.20 |

  Uncoded candidates are counted as UNCODED.
- **Also reported:**
  - the full `E` table;
  - each candidate's L-T0-K0 EWC t (descriptive), grouped by class.

### 1.4 Reading (frozen with this spec)

Let `r = INVERTED / (CONSISTENT + INVERTED)`.

| Reading | Condition | Consequence for M1-a |
|---|---|---|
| **DIRECTION_DEFECT** | r ≥ 0.25 | the F2 prompt gap is confirmed as a material cause; A1/A2 are core |
| **DIRECTION_OK** | r ≤ 0.10 | direction is not the main cause; A1/A2 are kept as hygiene |
| **MIXED** | otherwise | reported for judgment |
| **UNDETERMINED** | CONSISTENT + INVERTED < 20 | too few coded candidates to read |

## 2. M0-3 — common exposure

### 2.1 Return co-movement

- **Data:** the 99 L-T0-K0 `net_series` (54 IS periods) from the exports. For a non-event
  candidate, use its record with `event_scenario` K0 (or none).
- **Statistics:**
  - the 99 × 99 Pearson correlation matrix over common assessed periods;
  - the eigenvalues of that matrix: the PC1 share, and the number of PCs needed for 80%.

### 2.2 Exposure similarity (no return information)

- **The matrix:** `S[i,j]` = the mean cross-sectional Spearman of factor values (the §1.3
  method) for all 99 × 99 pairs.
- **Reported:**
  - the pair counts at |S| > 0.90 and at |S| > 0.70;
  - the connected components at |S| > 0.90;
  - **the A4 replay:** walking the proposals in their original order, how many would the
    frozen A4 rule (|S| > 0.90 with an earlier admitted candidate) have rejected;
  - each candidate's max |E| over the drivers, i.e. how many are textbook restatements
    (max |E| ≥ 0.70).

### 2.3 Reading (frozen with this spec)

| Reading | Condition |
|---|---|
| **CONCENTRATED** | PC1 share ≥ 0.50, **or** the A4 replay rejects ≥ 30 of 99 |
| **DIVERSE** | PC1 share < 0.25 **and** the A4 replay rejects < 10 |
| **MODERATE** | otherwise |

These readings are descriptive inputs to M1-a. They change no frozen rule.

## Outputs

`m0_13_direction_exposure.json` (canonical, content-hashed) and `.md`, written to a
planner-supplied output directory outside the repository data tiers.

## Content (owned, all new)

- `smart_beta/diagnostics/direction_exposure.py`
- `scripts/run_m0_direction_exposure.py`, with these arguments:
  - dataset root, K-PIT copy, run root, output dir;
  - the generation / executor-state / screen-cache paths;
  - the coding file and its SHA-256.

  Its identity path reuses `run_m0_known_factors.build_generator`.
- `tests/test_m0_direction_exposure.py`
- `tests/fixtures/m0/direction_exposure/**`

## Tests (synthetic only)

1. **Spearman exposure:** a candidate equal to a driver gives E = 1; its negation gives
   −1; an independent random factor gives about 0. Formations with < 30 common members are
   skipped.
2. **Classification:** CONSISTENT / INVERTED / WEAK / UNCODED, including the
   threshold-boundary cases.
3. **The A4 replay is order-dependent:** the first of a duplicate pair is kept and the
   second rejected; a negated copy is rejected.
4. **PCA:** on a synthetic one-factor return panel, PC1 share > 0.9; on independent
   series, it is small.
5. **Coding-file hash:** a mismatch aborts before any data access.
6. **Firewall:** a non-IS fold raises before any panel is built; no `reserve_sealed/`
   path is opened.
7. **Determinism:** two runs produce byte-identical JSON.

## Execution (the planner's real run)

1. The planner writes and commits the blind coding file (§1.2).
2. The planner runs on the laptop, under the same protections as M0-2/M0-4: a K-PIT copy
   plus a scratch run root, the production K-PIT hash checked before and after, IS only,
   credentials scrubbed.
3. The result goes in `docs/campaign4_m0_13_result.md`.

## Launch

- One Pi worker via Herdr, worktree `worktrees/task-c4m0-13-direction-exposure`.
- **Stop rule:** a missing frozen helper, an unreadable persisted input, or a driver that
  needs grammar changes → STOP and report.
