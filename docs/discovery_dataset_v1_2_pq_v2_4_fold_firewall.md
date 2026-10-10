# P-Q v2.4 — fold-parameterized temporal firewall (FROZEN 2026-10-09; document-only; NOT implemented)

- **Status:** FROZEN by user approval ("1. 冻结 2 (i)", 2026-10-09). Option (α′) with **implementation path (i)**.
- **Methodology identity:** P-Q v2.4 = the SHA-256 of this document as committed, bound to the chain v2.3 `d4ec7f6c…` →
  v2.2 `ab12886c…` → v2.1 `66772bb1…`.
- **What it amends:** P-Q v2.3 (`d4ec7f6c…`, chain v2.2 → v2.1) **only** in the temporal
  firewall rules of M§8A.1 / M§8, as applied to labels, MISDATED candidates and events.
- **Unchanged:** every grammar, S2/CS, the causes and precedence, the certification
  propositions.

## 1. Problem

- **The firewall is pinned to one date.** P-Q v2.1–v2.3 fix the firewall at
  `DEVELOPMENT_END` = 2023-06-30:
  - a flag with next(s) > `DEVELOPMENT_END` → UNKNOWN_RESERVE;
  - a label with e* == `DEVELOPMENT_END` → UNKNOWN;
  - a MISDATED candidate set within 5 sessions of `DEVELOPMENT_END` → UNKNOWN;
  - an event with K ≥ 2023-07-03 → a firewall incident.
- **Consequence:** P-Q is *undefined* on the reserve, so the L envelope cannot be
  computed for a reserve confirmation.

## 2. Amendment (methodology)

1. **The fold end E.** Each evaluation runs on a declared fold with an end session E.

   | Fold | E | Evaluated under |
   |---|---|---|
   | Development | 2023-06-30 | exactly the v2.3 rules, with E = `DEVELOPMENT_END` |
   | **Authorized reserve fold** | the data cutoff **C**, the last NYSE session of the last complete month of the reserve batch | only under a K-PIT `ACCESS` grant (P7 §4) |

2. **Rules restated with E** (the development fold is identical to v2.3):
   - a flag with next(s) > E → UNKNOWN, with cause `PQV2_RESERVE_UNKNOWN` (dev) or the
     new `PQV2_FOLD_END_UNKNOWN` (reserve fold);
   - a label with e* == E → UNKNOWN, the same pair of causes;
   - a MISDATED candidate window crossing E (within 5 sessions) → UNKNOWN;
   - in the reserve fold, events with K(e) ≤ E are admissible; K(e) > E → a firewall
     incident.
3. **The cross-boundary lookback** (reserve fold): factor inputs and flags dated before
   2023-07-03 are classified by the **development** sources (D7c dev m_s); those dated
   ≥ 2023-07-03 by the **reserve** sources (the R7-T reserve m_s). The S2 and §8A rules
   are otherwise unchanged.
4. **The precedence** inserts `PQV2_FOLD_END_UNKNOWN` at the same rank as
   `PQV2_RESERVE_UNKNOWN`. The two never co-occur, because each belongs to a different
   fold.
5. **Identity:**
   - ED v2.4 appends `fold_parameterized_temporal_firewall` (version `pq-v2.4-ed-5`);
   - CE unchanged in classes; the version token becomes `pq-v2.4-ce-4`;
   - `AUTHORITY_V2_4` is selectable; CURRENT stays v2.1.

**ED-ID v2.4** = `f8370828ae292f9e5bfc0a5a4e7054ba79c33fe88c1646793ad0f1d0dcc88d93`:

```
{"rules":["tri_log_linear_prefix_cancellation","lag_shift","rolling_window_union_numerical","arithmetic_conservative_union","cross_sectional_whole_row","label_candidate_e_[f,next(e)]","dtr_{prev,s,next}","dv_{prev,s}","misdated_recorded_pm5","reserve_unknown_not_assessed","source_unresolved_unknown_not_assessed","add_sub_support_union","event_anchored_tri_window","event_primitive_price_independent","event_identity_unmapped_not_assessed","event_identity_contradicted_not_assessed","event_reserve_not_assessed","event_time_unavailable_not_assessed","event_source_excluded_not_assessed","event_timing_dual_scenario_k0_k1_preclose_15m","fold_parameterized_temporal_firewall"],"version":"pq-v2.4-ed-5"}
```

**CE-ID v2.4** = `c40d8724ff6b6ee7862a07e39eb008da0e667a3826599e3850dff4e4993fa4c2`:

```
{"classes":{"simple_exact":"GRAMMAR_SIMPLE_G_v1","std_x_rank_sandwich":"GRAMMAR_STD_X_RANK_G_v1","event_simple":"GRAMMAR_EVENT_G_v1"},"bounds":{"lookback_max":504,"window_min":2,"window_max":504},"denominators":["tri_ratio","rolling_mean_dv","rolling_std_ret_assumption_A_STD"],"event_data":{"acq_id":"f0d0ee1b4f5875b1d8ad970e041ebe73d8278cc283477378af4bb1bf9fa04f54","ev_id":"ff5e83ec253f007493bfc7341fd4dcc90606ee932855efe3313686d1afabcca3"},"event_window":"K_and_K_plus_1","other":"NOT_ASSESSED_UNLESS_SEPARATELY_FROZEN","version":"pq-v2.4-ce-4"}
```


## 3. Implementation path — DECIDED: (i) (the planner correction is disclosed)

- **What the planner said earlier:** option (α′) would need **no change** to the
  certified v2.3 modules; only new code would be written.
- **What inspection shows:** that is **not feasible for the evaluation layer**.
  `pqv2_evaluation.build_pqv2_diagnostics` has no injection point for a source set or a
  labels function. It calls `pqv2_sources.load_source_set` and `pqv2_labels.label_envelope`
  internally, and both hard-code `DEVELOPMENT_END`.

| Path | What changes | Proof | Cost |
|---|---|---|---|
| **(i) Recommended: minimal fold parameter in the v2.3 modules** | `pqv2_sources`, `pqv2_labels`, `pqv2_events` and `pqv2_evaluation` gain a `fold` argument; **the default is the development fold** | the default path is byte-identical to v2.3: all goldens, the C1 equivalence, the PQ22/PQ23 cross-checks, then a **full I1–I15 recertification** for v2.4 | about 4 worker tasks plus a full barrier (like v2.2) |
| (ii) A new parallel fold evaluator (fork H) | new code only | it must reproduce v2.3 diagnostics byte-for-byte on development | duplicates about 2,000 lines of H; drift risk; harder to keep in sync |

- **Frozen consumers of the four modules** (planner grep):
  - Campaign-2 binds v2.1 through `pqv2_evaluation`;
  - path (i) keeps every default path byte-identical, and the Campaign-2 executor tests
    must pass unchanged. That is a required I-check.

## 4. Non-claims

- **Not a statement about reserve data quality.** The reserve fold's D5 flags are
  classified by the same rules, and Gate-6 INCONCLUSIVE applies to reserve data too.
- **Single use only.** Reserve evaluation happens only inside an authorized
  confirmation.
