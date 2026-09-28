# Discovery Dataset v1.1 — D6 Duplicate `security_key` Clarification (NORMATIVE)

- **Status:** normative D6 interface clarification (Option A), decided by the
  user on 2026-09-28.
- **Documentation only:** it changes no completed stage.
- **It clarifies, without modifying:**
  - `worker_tasks/dataset_v1/task-d6-universe.md` (376c260; `25927941…`);
  - `docs/discovery_dataset_v1_1_d6_clarification.md` (5238303).

## 1. Input fact

- The frozen D4 artifact `i-a21f1e46621dee94` **may contain repeated
  `security_key` rows**. In production it has 11,231 rows and 11,220 distinct
  keys: 11 keys appear on 2 rows each (22 rows).
- All 22 are IDENTITY_AMBIGUOUS with reason OVERLAPPING_LISTED_ROWS
  (same-start snapshot rows).
- **D4 remains authoritative and unchanged.**

## 2. Rule

1. **D6 population identity is DISTINCT `security_key`.**
2. **A duplicated key is permitted only if every D4 row for it is
   D6-identity-inadmissible**, i.e. no row in the group has
   `state = INTERVAL_ASSIGNED`.
3. **Any duplicate group containing one or more INTERVAL_ASSIGNED rows fails
   closed** with a typed D6 consistency error. D6 must not select, rank,
   merge or discard rows, resolve overlap, or infer a canonical identity.
4. **An all-inadmissible duplicate group collapses to exactly one D6
   population member:**
   - primary reason `IDENTITY_NOT_ADMISSIBLE`;
   - never eligible;
   - never reaches listing-age, missingness or liquidity admission, ranking,
     or the top-200/500/1000 flags.
5. **The existence rule applies only after identity admission.**
   - For an all-inadmissible group the primary reason is
     `IDENTITY_NOT_ADMISSIBLE`, whatever the underlying rows' intervals.
   - No synthetic listing interval is created.
   - **Population membership** of the collapsed member at f: the member is in
     the population at f if **any** underlying row satisfies
     `listed_start ≤ f−1 ≤ … listed_end ≥ f`. This is a diagnostic
     accounting choice only; it never alters admission or reason.
6. **Provenance stays visible.** For every collapsed member, the output
   records deterministically:
   - `security_key`;
   - the duplicate-group row count;
   - the sorted distinct identity states;
   - the sorted distinct D4 reasons.
7. **`n_population` counts distinct keys**, not D4 rows.
8. **No identity ambiguity is resolved by this collapse.** It only defines
   how D6 represents an already-inadmissible D4 identity group.
   - Issuer-identity knowledge timing/continuity remains **NOT CERTIFIED**.

## 3. Unchanged

- The `n_eligible` reconciliation semantics and the frozen development
  reference (`d6_reference_development_v4.csv`, SHA-256 `7469e171…`) are
  unchanged. No expected `n_eligible` value changes.
- Population and not-listed counts are diagnostics, not reconciliation
  targets.
