# Campaign-4 M1 — task C4M1-1: contract core (A2 / A3 / A4 / A6, rank statistic, prompt) (DRAFT; not frozen)

## Authority

`worker_tasks/campaign4_m1/campaign4-m1-plan.md` (its binding constraints apply in full);
`docs/campaign4_m1a_contract_calibration_draft.md` §1–§4.

## Content (all new files)

### 1. `smart_beta/research/prompts/campaign4_v1.txt`

- A byte-identical copy of `worker_tasks/campaign4_m1/campaign4_v1_prompt.txt`.
- Its SHA-256 must equal the hash bound at freeze. Expose it as
  `campaign4.feedback.PROMPT_TEMPLATE_C4_HASH`, verified at load.
- The only placeholder is `<<<C4_FEEDBACK>>>`.

### 2. `smart_beta/campaign4/contract.py` — A2 declarations

- Parse the raw generator JSON (top-level `schema_version` must be
  `campaign4-candidates-v1`).
- For each candidate entry, validate the A2 fields:
  - `predicted_relation`: an object with a non-empty `variable` string, and `direction` in
    {`higher_return`, `lower_return`};
  - `direction_check`: a non-empty string;
  - `family`: in {`momentum`, `reversal`, `volatility_risk`, `liquidity_volume`,
    `earnings_event`, `interaction`}.
- Output:
  - a C3-compatible candidate list, with the three A2 keys removed and `schema_version` set
    to `campaign3-candidates-v1`, so that `research/generator.py` normalizes it unchanged;
  - a sidecar record per **original entry index**: `{index, a2_status: VALID |
    A2_DECLARATION_INVALID, reason, predicted_relation, direction_check, family}`.
- An invalid entry must still be counted against the budget and must not be evaluated.
- The original raw response bytes and their SHA-256 are kept in the sidecar.

### 3. `smart_beta/campaign4/diversity.py` — A4 gate + family cap + A6

- `DiversityGate(threshold=0.90, family_cap=30, window_cut: date)`.
- `check(candidate_key, panel, family) -> Decision`. Decision is one of `ADMIT`,
  `NEAR_DUPLICATE(other_key, rho)`, or `FAMILY_CAP(family)`.
  - Use `direction_exposure.panel_values_by_date` + `mean_spearman` (import, do not
    re-implement), restricted to formation dates `< window_cut`.
  - Compare with **every admitted** candidate, using |ρ|.
  - The family count includes every proposal that declared the family, whether admitted or
    rejected.
- `restatement(panel) -> (driver_id, max_abs_E)`: the max |E| against the five C4M0-13
  reference drivers, on the same window. The driver panels are passed in; the driver
  specs are imported from `direction_exposure`.
- Processing order is proposal order. The gate is order-dependent, like `a4_replay`.

### 4. `smart_beta/campaign4/ranking.py` — the rank statistic

- `window_series(record, window_cut, side) -> (periods, values)`: keep the periods with
  formation date `< window_cut` (training) or `>= window_cut` (test).
- `rank_statistic(td_export, window_cut, *, bound_alpha=0.05)`:
  - the minimum over the event scenarios {K0, K1} (non-event: the single L-TD cell) of the
    one-sided EWC t of `l_series × sign` on the training periods;
  - status `VALID`, `NOT_ASSESSED(reason)` (when the cell is NOT_ASSESSED) or
    `INSUFFICIENT_SAMPLE` (when fewer than 24 periods).
  - Use the EWC procedure the v2.5 equivalence path uses (`known_factors._ewc_statistic`).
    Its import may be private; record that explicitly in a module comment.
- `l_t0_ewc_t(base_export, window_cut)`: the same for the L-T0 cells.
- `test_statistics(td_export, window_cut)`:
  - the test-period EWC t when there are 24 or more periods;
  - always: the mean L-TD net return, its sign, the share of positive months, and n.
- `assert_v25_admitted()`: calls `verify_authority_v2_5()` and checks d == −0.55. Every TD
  path calls it first.

### 5. `smart_beta/campaign4/feedback.py` — renderers

- **Treatment (`render_c4_feedback`):** one JSON line per proposal, in proposal order:
  `{proposal_id, factor_spec_hash, expression, family, predicted_direction, status,
  reason, rank_stat, rank, l_t0_ewc_t, l_td_ewc_t, restatement:{driver, abs_e},
  nearest:{proposal_id, rho}}`.
  - `status` ∈ {EVALUATED, NOT_ASSESSED, NOT_CERTIFIED, NEAR_DUPLICATE, FAMILY_CAP,
    A2_DECLARATION_INVALID, SCHEMA_REJECTED}.
  - `expression` is the canonical compact JSON of the AST.
  - Numbers are rounded to 3 decimals.
  - With no proposals, the block is `No prior proposals.`
- **Control (`render_control_prompt`):**
  - The C3 v2 template, loaded by `llm_generator_deepseek.load_prompt_template_v2()`, with
    its hash asserted equal to `PROMPT_TEMPLATE_V2_HASH`.
  - Rendered through `llm_generator.render_prompt(..., template=...)` with C3 structures
    whose IS metrics (`<cell>_net_long_short_mean`, `<cell>_hac_t` for B/L × T0/TS) are
    **recomputed on the training periods**. Use the same estimator as
    `campaign3_eval.metrics_from_diagnostics` (`evaluation.metrics.long_short_mean_tstat`).
  - If those C3 structures cannot be built without modifying a C3 file: **STOP and
    report.** Do not re-implement the C3 renderer.
- **Firewall:** both renderers take `window_cut` and raise `C4FirewallError` if any value
  passed in carries a period `>= window_cut`.
- `C4_SYSTEM_PROMPT`: the C3 `SYSTEM_PROMPT` text with "Campaign-3" replaced by
  "Campaign-4". It is a new constant; the C3 one is untouched.

## Tests (synthetic only; `tests/test_c4m1_contract_core.py`)

1. **A2:** valid entries pass, and the stripped output is accepted by the unchanged C3
   normalizer. Each malformed field (missing, wrong enum, empty string, wrong type) gives
   `A2_DECLARATION_INVALID` at the right index. The raw-bytes hash is kept.
2. **A4:** an exact copy and a negated copy are rejected as NEAR_DUPLICATE. The first of a
   pair is kept. An independent factor is admitted. The 31st candidate of one family is
   FAMILY_CAP. Formations at or after `window_cut` do not affect ρ.
3. **A6:** a candidate equal to a driver gives abs_e = 1 with the right driver id.
4. **Rank statistic:**
   - K0/K1 minimum;
   - NOT_ASSESSED is propagated;
   - fewer than 24 training periods gives INSUFFICIENT_SAMPLE;
   - truncation keeps only dates `< cut`;
   - it equals the EWC t of a hand-sliced series.
5. **Test statistics:** with 12 periods there is no t, and the mean/sign/share are
   correct; with 24 there is a t.
6. **Firewall:** each renderer raises on a period `>= window_cut`, and the gate raises the
   same way.
7. **Prompt:** the file hash equals the frozen hash. The control template hash equals
   `PROMPT_TEMPLATE_V2_HASH`. The rendered prompts are byte-identical across two runs.
8. **v2.5:** `assert_v25_admitted` is called on the TD path (monkeypatch it to fail →
   the TD path raises).
No unit test calls git. The reviewer checks that `git diff` of the protected paths (plan
constraint 3) against the base is empty.

## Stop rules

STOP and report if:
- a frozen helper is missing;
- the C3 normalizer or renderer cannot be reused without modifying a protected file;
- any data_store path would need to be read. This task reads **no** real data.
