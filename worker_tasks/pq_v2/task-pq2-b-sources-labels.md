# PQ2-B — P-Q v2 source set (flags, m_s, MISDATED, firewall) + Candidate-E label envelope (FROZEN)

- **Authority:**
  - methodology M§2, M§3, M§6, M§7, M§8, M§9;
  - plan §6.2, §8, §10.
- **Wave:** 2. **Dependencies:** PQ2-A merged (types, S2), PQ2-G merged (oracle).
  **Runs in parallel with:** PQ2-C.
- **Owned:**
  - `smart_beta/dataset_v1/pqv2_sources.py`, `smart_beta/dataset_v1/pqv2_labels.py`
    (new);
  - `tests/test_pqv2_sources.py`, `tests/test_pqv2_labels.py` (new);
  - `tests/fixtures/pqv2/labels/**` (new, including `mutations.json`).
- **Forbidden:** every other file. Read-only imports are allowed from `pqv2_core`,
  `quarantine` (`Overlay`), `returns` (bindings/constants), `delisting_bridge`
  (`_Resolver`, bindings, `canonical_json`) and `trusted_inputs` (`TrustedInput`
  accessors).
- **Tests use synthetic fixtures only.** No real `data_store` reads.

## 1. `pqv2_sources.load_source_set` (plan §6.2)

- **Inputs:** `overlay` (P-Q v1 `Overlay`; uses `.flags` columns `security_key`,
  `flag_session`, `prev_session`, `next_session`, `next_beyond_development`); the
  D7c `daily_total_return` TrustedInput; the trusted sessions; and a CA-event frame
  (the D7a-bound D3 `ca_events.parquet` via `delisting_bridge`/`returns` bindings: the
  loader function `load_ca_events(root, bindings)` reads through the bound resolver
  with hash verification) plus the D4 intervals (`overlay.identity` /
  identity-assignment frame).
- **m_s:** 1 + the D7c `daily_total_return` whose `source_session` == s. If it is
  missing or not finite, or m_s ≤ 0, raise `PQv2InputError`.
- **Candidates:** see the plan §6.2 bullets, exactly:
  - action type ∈ `MISDATED_ACTION_TYPES`;
  - non-superseded events with knowledge_date == effective_date (the D7a rule);
  - effective session = the trusted session (exact match required, else raise);
  - the D4 interval of the flag's key;
  - |pos(d) − pos(s)| ≤ 5, d ≠ s;
  - **only sessions carrying a recorded event**;
  - φ_d = the product of factors over all the key's events on d;
  - cross-check: the count against D7a `ca_event_count` when supplied.
- **Firewall:**
  - if pos(s) + 5 > pos(DEVELOPMENT_END), or `next_beyond_development`, s goes into
    `unknown_by_key` (with reason);
  - any input row dated > DEVELOPMENT_END raises `PQv2InputError`;
  - the loader never constructs a path under `reserve_sealed/`, `raw/` or
    `dsc_evidence`.
- **Output:** a deterministic `SourceSet`, sorted by (key, s).

## 2. `pqv2_labels.label_envelope` (plan §6.2; M§6, M§8, M§9)

- **Input:** one D7a label record (formation f, endpoint e, `label_state`,
  `observed_segment_end` e′, `terminal_affected`); the key's transitions, candidates
  and unknown set; and the D7b record values `t0_value`, `ts_value`.
- **Status:**
  - `EMPTY_OBSERVED_PATH_IDENTITY` → `EMPTY_NO_DEPENDENCY` (multiplier [1, 1]; no flag
    is consulted, even if one lies in the structural window);
  - not admissible or INCOMPLETE → `FACTUAL_MISSING` (values NaN; never bounded);
  - COMPLETE: let e* = e′ if terminal, else e. If e* == DEVELOPMENT_END, or any flag in
    [f, next(e*)] or any relevant candidate pair is in `unknown`, the status is
    `UNKNOWN_RESERVE`. Otherwise it is `ENVELOPE`.
- **Factor per flag s** with dependency:
  - s == f → Q_s⁻¹; f < s ≤ e* → Q_s; s == next(e*) → Q_s⁻¹;
  - **MISDATED:** each candidate (s, d) with exactly one of {s, d} in (f, e*] adds the
    alternative φ_d (s in) or φ_d⁻¹ (d in). This includes flags s **outside**
    [f, next(e*)] whose d is inside (f, e*];
  - per flag, the set is hull({Λ_s if dependent, else 1} ∪ alternatives);
  - envelope [lo, hi] = the product of the per-flag mins and maxes.
- **Values:** v^X_lo/hi = (1 + v^X_obs)·lo/hi − 1, for X ∈ {T0, TS}. NaN passes
  through. 1 + v = 0 stays −1.
- **Exposure counts** returned: flags by position class {FORMATION_ENDPOINT, INTERIOR,
  ENDING_ENDPOINT}, number of MISDATED alternatives, unknown.

## 3. Required tests

- **Oracle equality** (`pqv2_oracle.label_extremes` vs `label_envelope`) on the M§16
  label cases: single suspect, multiple suspects, formation endpoint, ending endpoint,
  interior close cancellation, MISDATED paired CA, EMPTY, INCOMPLETE (MISSING), reserve
  UNKNOWN, terminal COMPLETE. Use oracle grids that include the endpoints of every Q_s.
- **Equality is required** whenever each flag has at most one MISDATED candidate and the
  oracle runs per-flag Cartesian states. Otherwise containment is required, and the gap
  is reported.
- **Sources:**
  - ±5 boundary (5 included, 6 excluded); a session with no recorded event is never a
    candidate;
  - action-type filter; D4 interval filter;
  - firewall cases (pos(s) + 5 beyond the end → unknown; a post-firewall row → raise);
  - `ca_event_count` mismatch → raise;
  - determinism (the same `SourceSet` twice).
- **T0/TS:** a TS long terminal (1 + v = 0) stays −1.
- **File-open audit test:** monkeypatched `open` and `pd.read_parquet` record paths; no
  forbidden path segment appears.

## 4. Mutations (`tests/fixtures/pqv2/labels/mutations.json`)

| ID | Mutation |
|---|---|
| V2-M01 | omit s == f |
| V2-M02 | an interior flag also contributes Q⁻¹ at next(s) |
| V2-M03 | candidates dropped |
| V2-M04 | every ±5 session is a candidate (φ = 1 sessions included as uncertain/alternatives) |
| V2-M06 | firewall filter removed (post-end rows accepted) |
| V2-M07 | e′ == DEVELOPMENT_END treated as ENVELOPE |
| V2-M18 | INCOMPLETE given an envelope instead of NaN |
| B-M1 | EMPTY consults flags |
| B-M2 | next(e*) given Q instead of Q⁻¹ |
| B-M3 | a MISDATED alternative with the wrong exponent sign |
| B-M4 | φ_d of the first event only (same-day events not combined) |
| B-M5 | ±5 measured in calendar days instead of trusted sessions |

## 5. Stop conditions

Plan §13. In addition:
- STOP if the D7a bindings do not expose the D3 `ca_events` path without modifying
  `returns.py` or `delisting_bridge.py`;
- STOP if the oracle and the analytic envelope disagree in an equality case (a possible
  specification defect).
