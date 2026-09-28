# Discovery Dataset v1.1 — D7 Return-Evidence and Delisting Clarification (NORMATIVE)

- **Status:** normative clarification of the D7 HOLD items (B1, B2, B3, B4 and
  the terminal-at-formation boundary), decided by the user on 2026-09-28.
- **Documentation only:** it changes no completed stage (D1–D6, D-SC) and no
  sealed phase. D6's existence rule (`listed_start ≤ f−1` and
  `listed_end ≥ f`) is unchanged.
- **It clarifies, without modifying:**
  - `docs/discovery_dataset_v1_contract.md` §§7, 8, 9, 10, 11, 14, 20 —
    SHA-256 `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`;
  - `docs/discovery_dataset_v1_1_d6_clarification.md` (5238303) —
    `e3cb52a892f533c41c5c0d1d927cd54d7e200ef2a28e56e76efdf01227aa17ea`;
  - `worker_tasks/phase7/phase7-plan.md` §§5, 6.1, 8.1 (P7-B ownership and the
    cross-boundary purge) — `8b2009533ee00e8a04e0c65b6853711f01387592e42f60c0b97170d23183a13e`.
- Where this document departs from the literal contract wording, it says so
  explicitly (§5 and §6). Nothing else is broadened.

## 1. Ownership split (B1)

The contract's single D7 task (§20) is split into two tasks:

| Owner | Responsibility |
|---|---|
| **D7a** | Factor-independent, stored **observed** evidence: daily total returns from the §8 path, per-session and per-path QC status, retrospective terminal metadata, calendar endpoint and purge metadata, and provenance. |
| **D7b** | The TrustedInput bridge (§7, §11.1, one-session lag) and the evaluation-time T0/TS scenarios (§9.4, §9.5), affected counts, B, E_S, E_L and the §9.6 classification. |
| **P7-B** (`smart_beta/evaluation/forward_returns.py`, unchanged) | Compounding of forward returns for an evaluation horizon h. |

- D7a stores **no** compounded forward return, T0 value, TS value, τ,
  synthetic proceeds, factor rank, factor leg or predicted-high/low
  assignment.
- Observed evidence, compounded forward returns and delisting scenarios stay
  three separate concepts. None is stored as another.

## 2. Storage of observed evidence under `derived/`

- §14 lists `derived/` as holding "universe and dollar-volume series". This
  clarification also authorizes D7a's **factual observed return evidence**
  (daily total returns reconstructed by the §8 certified path, with status
  and provenance) under `derived/returns/<returns_id>/`.
- It stays outside Git. The §14 commit rule is unchanged.
- It covers development sessions only (§10 below).

## 3. No fixed horizon (B2)

- D7 freezes no research horizon set. h is an `EvaluationSpec` / campaign
  parameter (Phase 7 §6.1), with **1 ≤ h ≤ Hmax = 63 trusted NYSE sessions**
  (§11.2).
- D7a provides evidence sufficient for every h in 1…63.
- **endpoint(f, h)** is the h-th trusted NYSE session strictly after f. The
  realized interval is close(f) → close(endpoint), made of the session
  returns dated f+1 … endpoint (§11.1). No return dated f or earlier
  contributes.

## 4. D7a population (B4)

- D7a covers exactly the D6 development **top-1000** members of each
  development formation (`u-6150bc6ac2866b7f`), with their D6 `rank`,
  `top_200`, `top_500` and `top_1000` carried verbatim.
- Later evaluation selects top-200, top-500 or top-1000 from these fields,
  without rebuilding or reranking the universe or rereading market data.
- The population comes from formation-time membership only. It is never
  intersected with any later surviving-symbol set. Every member stays
  represented whatever its later outcome.

## 5. Terminal authority (B3) — departure from the literal §9.1 wording

- **The terminal authority is the retrospective D4 `listed_end`** of the
  member's security_key.
- §9.1 says "a series ends at its last EOD row". For D7 and D7b, the date on
  which a series ends is **not** established from market-row cessation:
  - not from the last observed row or the last usable row;
  - not from the absence of a later row, including at endpoint+1;
  - not from a temporary trading gap or ordinary missingness.
- **Missing market observations never establish a terminal event.** A
  missing or unusable observation is MISSING/INCOMPLETE unless `listed_end`
  independently establishes a terminal event.
- A last observed or usable row may be kept only as factual evidence. It never
  sets terminal status.
- D4 `listed_end` is retrospective identity/listing evidence. Using it for
  realized-outcome classification does **not** certify historical
  delisting-knowledge timing, issuer-identity knowledge timing or
  continuity, or terminal proceeds.

## 6. Terminal-affected rule — departure from the literal "(f, f+h]" wording

For a D7a member at formation f and horizon h:

    terminal_affected(f, h)  ⇔  f ≤ listed_end ≤ endpoint(f, h)

- `listed_end == f` ⇒ affected. `f < listed_end ≤ endpoint` ⇒ affected.
  `listed_end > endpoint` ⇒ not affected for that horizon.
- `listed_end < f` cannot occur for a D6 member (D6 H2). If met, it fails
  closed.
- **Why:** D6 H2 admits `listed_end == f`. In production, 29 top-1000
  formation rows (29 keys, 25 formations; 10 in the top-500, 3 in the top-200)
  have `listed_end == f`, a row at f and no row after f. Without this rule
  they would silently leave every statistic, contrary to §9.2.
- This supersedes the open lower bound of §9.2's "(f, f+h]" **only** for the
  `listed_end == f` boundary case. It is not broadened further.
- **No endpoint+1 dependence:** classification never looks at any market
  observation after endpoint(f, h).

## 7. Empty observed path at `listed_end == f`

- The observed segment (f, listed_end] ∩ trusted sessions is **empty**.
- Its compounded value is the empty-product identity: **R_obs = 0**.
- This is **not** an observed 0% market return. No post-formation return was
  observed. Its stored semantics are `EMPTY_OBSERVED_PATH_IDENTITY`, never
  `OBSERVED_ZERO_RETURN`. It is never treated as missing.

## 8. T0 and TS stay evaluation-time scenarios

The frozen §9.2 formula is unchanged:

    R_scenario = (1 + R_obs) · (1 + τ) − 1

- R_obs is the observed return from close(f) to close(listed_end), or the
  empty identity 0 (§7).
- τ is the **incremental** return from the last close to the unknown
  proceeds. τ = 0 means no incremental terminal return. It does **not** mean
  a zero terminal value, which would be τ = −100%.
- T0: τ = 0 for every affected observation, so R_scenario = R_obs.
- TS: long leg τ = −100%, short leg τ = +100% (a declared stress value, not a
  bound), middle quantiles τ = 0.
- So when `listed_end == f`: R_scenario = τ, which gives 0 under T0, −100% on
  the TS long leg, +100% on the TS short leg and 0 in the TS middle.
- These are scenario results, not observed proceeds or observed returns.
  They are computed only in D7b at evaluation time, after leg assignment, and
  are never stored in D7a.

## 9. Path states

- Sessions after `listed_end` in an affected horizon are **POST_TERMINAL**.
  They are not MISSING, and no return is synthesized for them.
- Missing or unusable sessions before `listed_end` stay invalid. The terminal
  event does not erase them.
- `terminal_affected` and `observed_path_complete` are separate facts, and
  both may hold (terminal and incomplete).
- How an incomplete pre-terminal path enters T0/TS statistics is a D7b
  decision. D7a imputes nothing.

## 10. Reserve boundary

- The frozen purge applies (contract §11.2; Phase 7 §8.1): a label (f, h)
  whose realized interval contains any session ≥ 2023-07-03 is **PURGED**. It
  is never shortened, partly compounded or read from the reserve.
- D7a reads no market observation on or after 2023-07-03.
- D7a publishes a calendar-only table of (formation, h, endpoint,
  crosses_reserve) for h = 1…63. This is calendar metadata, not reserve
  market data.
- D7b must remove labels with `crosses_reserve` **before** calling P7-B.
  Otherwise P7-B, given a development-only panel, would report them as
  `insufficient_forward_window`. That distinction is mechanical and tested.

## 11. No exact future listing dates in D7a

- D7a exposes the exact `listed_end` only when it is ≤ 2023-06-30.
- Otherwise it stores only the structural indicator
  `CONTINUES_BEYOND_DEVELOPMENT_PERIOD`, with provenance "retrospective D4
  evidence".
- This needs no reserve market-data access.

## 12. Nonclaims kept

- **Identifier continuity and issuer-identity knowledge timing/continuity:**
  NOT CERTIFIED.
- **Historical delisting-knowledge timing:** not PIT-certified.
- **Delisting returns / terminal proceeds:** NOT CERTIFIED
  (`DELISTING_RETURN_NOT_CERTIFIED`). T0 is a reference and TS a stress
  scenario; neither estimates proceeds. No finite short-side or Pearson-IC
  bound exists or is claimed.
- **Survivorship completeness:** NOT CERTIFIED.
- **Complex corporate actions:** bounded by the existing specimen-level
  certification.
- **Reserve D6 / reserve evidence:** DEFERRED / NOT ASSESSED.
