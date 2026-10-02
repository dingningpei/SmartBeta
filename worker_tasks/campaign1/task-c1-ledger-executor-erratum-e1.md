# C1-LX-E1 — Erratum to the C1-LX task spec: Phase-8 `family_id` identity (frozen; standalone)

- **Status:** FROZEN. Approved by the user ("C1-LX-E1 APPROVED"), on master `79a704f`.
- **Amends:** `worker_tasks/campaign1/task-c1-ledger-executor.md` (frozen at
  `d7e87a8`, SHA-256
  `118bcf414fa7015f66d2ec5ffdec3580e7e97123694314d56ad6a585c4f636fd`). That document is
  **not modified**.
- **Effective implementation authority:** the original C1-LX task **+** this erratum.
- **Phase-8 authority:**
  - `worker_tasks/phase8/phase8-plan.md` §7.4: the family identity is a deterministic
    hash of the frozen family declaration plus its recorded lineage, registered before
    results; the display label is cosmetic metadata.
  - `smart_beta/experiment/policy.py` `SearchPolicy.family_id`: an opaque,
    validated 64-character lowercase hex SHA-256.

## Defect (frozen-task specification defect)

C1-LX §1 specified `SearchPolicy(family_id="campaign1-family-1", …)`. Phase-8
rejects this with
`SearchPolicyError: family_id must be a 64-character lowercase hex SHA-256 string`.

This is **not** a Phase-8 or `SearchPolicy` defect, **not** a Campaign methodology
change, and **not** an empirical adaptation. `SearchPolicy` validation is **not
relaxed**, and `SearchPolicy` is **not copied or bypassed**.

## Correction

**`family_id`** = `smart_beta.experiment.registry.content_hash(declaration)`. That is
SHA-256 over the repository's canonical identity JSON: `sort_keys=True`, separators
`(",", ":")`, `ensure_ascii=True`, `allow_nan=False`, UTF-8. The declaration is:

| Field | Value (full, read from repository authority) |
|---|---|
| `domain` | `"campaign1-search-family"` |
| `precommitment_sha256` | `15f700606929889d4b9b64799e49603f0f5e84081df5557519d815cc556619d7` (SHA-256 of `docs/campaign1_precommitment.md`) |
| `factorspecs_sha256` | `f93a218d1e8a5bc07a7e64b52e1ce66be09b6708479d5c76a5f9a2973d6d532c` (SHA-256 of `docs/campaign1_factorspecs.json`) |
| `members` (frozen order C1-H1 … C1-H7; each `factor_spec_hash` of the loaded spec) | `9b27eb881a7b31bfe123866f82996638315be6257c333fcf04cfcae3a02f037c`, `85a7166bf2953fd9240037dc5189cb63dd3ae93690b2574c2f6d0567136d54fc`, `78a6d4547aecee3615841408be1df040fdb33037fe465b790bcd75c5d76fe99f`, `54b7edf3c0f00e5c53c8d87eee7a9c9bfd26287e15b04dc8ac38f57d3e4f1a6f`, `ee5b8ea8f57262945828d545910584b6b49546af344c9a2bfbd2164380564f26`, `42605a13a41cf0c9a04b614424b4bee1643bd565b1ec5b67c58a4b6d6ed816a1`, `32678dfda88647a666994e62697742955f7903b141431a628b3df98895be66b0` |
| `lineage` | `null` (no frozen authority establishes any lineage) |

Canonical declaration (exact):

```
{"domain":"campaign1-search-family","factorspecs_sha256":"f93a218d1e8a5bc07a7e64b52e1ce66be09b6708479d5c76a5f9a2973d6d532c","lineage":null,"members":["9b27eb881a7b31bfe123866f82996638315be6257c333fcf04cfcae3a02f037c","85a7166bf2953fd9240037dc5189cb63dd3ae93690b2574c2f6d0567136d54fc","78a6d4547aecee3615841408be1df040fdb33037fe465b790bcd75c5d76fe99f","54b7edf3c0f00e5c53c8d87eee7a9c9bfd26287e15b04dc8ac38f57d3e4f1a6f","ee5b8ea8f57262945828d545910584b6b49546af344c9a2bfbd2164380564f26","42605a13a41cf0c9a04b614424b4bee1643bd565b1ec5b67c58a4b6d6ed816a1","32678dfda88647a666994e62697742955f7903b141431a628b3df98895be66b0"],"precommitment_sha256":"15f700606929889d4b9b64799e49603f0f5e84081df5557519d815cc556619d7"}
```

**Resulting `family_id`:**
`2d2f16f56fe34ee5fecfb4613a6a96ecdc621f2836e5c2d6ea4fba5c77f8d1d0`. The planner
recomputed it independently with `registry.content_hash` from the full repository
inputs, and it matches.

**Implementation rules:**
1. Build the declaration **at run time** from the full frozen inputs:
   - the file hashes read from disk;
   - the members computed with `factor_spec_hash` on the specs loaded per C1-LX §1, in
     C1-H1 … H7 order.
   Then call `registry.content_hash`, and **assert** the result equals the value above.
   On a mismatch, raise `C1BindingError` (Campaign stop
   `INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE`). The literal is the check, not the
   source of the value.
2. Construct the real Phase-8 `SearchPolicy` with this `family_id`. Every other field
   is unchanged (`family_budget_m=7`, `family_alpha=0.05`, `EXPERIMENT_ID`,
   `FIXED_M_BONFERRONI`, `GOVERNANCE_FAILURE`, `DETERMINISTIC_REPLAY`).
3. **αᵢ comes from `SEARCH_POLICY.alpha_per_test`.** Campaign code must not compute
   `family_alpha / family_budget_m` itself. `CRITICAL_VALUE =
   float(scipy.stats.norm.ppf(1 - SEARCH_POLICY.alpha_per_test))`, still asserted to be
   within 1e-12 of `2.44999766060273`. This replaces the C1-LX §1 formula's operand
   with the identical policy-provided value.
4. **Display name:** `"campaign1-family-1"` may appear only as a cosmetic display
   label, for example in the ledger `CAMPAIGN_OPEN` record as `family_display_label`.
   It is never passed as `family_id` and never enters any identity hash.
5. **No hash cycle:** `family_id` depends only on the precommitment hash, the
   FactorSpecs hash, the member spec hashes and the lineage. It does **not** depend on
   `campaign1_id`. `campaign1_id` includes `SEARCH_POLICY` (and therefore `family_id`),
   so the dependency runs one way only.

## Additional required tests (added to C1-LX §11; nothing removed)

1. The declaration rebuilt from the full frozen inputs hashes exactly to
   `2d2f16f5…d1d0` (full value asserted).
2. `SearchPolicy` accepts this `family_id`.
3. `SearchPolicy` rejects `"campaign1-family-1"` as `family_id`.
4. αᵢ is taken from `SEARCH_POLICY.alpha_per_test`. Checked with a spy or patched
   policy, plus a static check that the Campaign module contains no
   `family_alpha /` division.
5. The critical value stays consistent with the precommitment (within 1e-12).
6. Changing any identity-bearing component changes `family_id`: one member hash,
   member order, the precommitment hash, the FactorSpecs hash, and lineage. These use
   fixtures; the real frozen declaration is never mutated.
7. `family_id` does not depend on `campaign1_id`: changing any `campaign1_id`-only
   input leaves `family_id` unchanged.

**Additional mutations** (the original 17 remain in force):
- LX-E1-M1 the display name used as the identity input / family_id;
- LX-E1-M2 members reordered or sorted;
- LX-E1-M3 `campaign1_id` included in the declaration;
- LX-E1-M4 αᵢ computed locally as `family_alpha / 6` (or any local division);
- LX-E1-M5 the equality assertion against the expected `family_id` removed;
- LX-E1-M6 `lineage` omitted or altered.

## Scope statement

| Item | Change |
|---|---|
| Methodology | NONE |
| Hypothesis-family membership | NONE |
| m | NONE (7) |
| α | NONE (0.05) |
| Multiplicity procedure | NONE |
| Empirical information used | NONE |
