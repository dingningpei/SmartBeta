# C4M1-1 — erratum E1 and review return (planner, 2026-10-16)

Review of worker commit `2eec225` on base `49a19e2`. The 32 tests pass when re-run.
Three items are returned. R1 is an implementation defect. R2 and R3 clarify spec text that
was ambiguous or wrong. They change no frozen methodology (rank statistic, thresholds,
windows, prompt hash).

## R1 — the treatment firewall must bind to the statistics (implementation defect)

`FeedbackRecord.period` is a single date per record, so the renderer's check does not
constrain the numbers it renders. `rank_stat`, `l_t0_ewc_t` and `l_td_ewc_t` are bare floats
with no record of the periods they came from.

Required:
- `RankStatistic` (ranking.py) carries `window_cut` and `last_period`. `last_period` is the
  latest period actually used, or `None` when none was used. `rank_statistic` and
  `l_t0_ewc_t` set both.
- `render_c4_feedback` takes the three statistics as `RankStatistic` objects, not floats.
  It raises `C4FirewallError` unless:
  - each object's `window_cut` equals the renderer's cut; and
  - `last_period` is `None` or `< cut`.
- The restatement and nearest-neighbour values come from a `DiversityGate`. The record
  carries that gate's `window_cut`, and the renderer asserts it equals the cut.
- Remove the `period` field.
- Tests:
  - a statistic computed with a later cut is rejected;
  - a statistic whose `last_period >= cut` is rejected, built by hand;
  - a gate with another cut is rejected.

## R2 — control feedback is per proposal and mirrors `metrics_from_diagnostics` (spec clarification)

The spec asked for C3 structures with recomputed metrics. Read correctly, that means
**C3's own per-experiment feedback**, not one synthetic experiment holding every cell.

Replace the single fabricated experiment with:
- `control_metrics(series_export, *, not_assessed: bool, window_cut) -> tuple[MetricValue, ...]`.
  - It has the same names, order and structure as
    `campaign3_eval.metrics_from_diagnostics`: for every cell in export record order,
    `<cell>_net_long_short_mean` then `<cell>_hac_t`, then `is_assessable`.
  - Values are recomputed on periods `< window_cut` by `long_short_mean_tstat`. B cells use
    `b_series`; L cells use `l_series`.
  - A NOT_ASSESSED cell gives value `None`, mirroring what the frozen function emits for
    such a cell. Document `n_obs`.
  - `is_assessable` follows `not_assessed`, exactly as the frozen function does.
  - The function slices the export itself and asserts every period it used is `< cut`.
- `render_control_prompt(visible_history, feedback, *, window_cut)`: the verified v2
  template through `llm_generator.render_prompt`.
  - The `ResearchFeedback` comes from the caller. C4M1-2 builds it the C3 way: the
    executor's history, whose `TaEvaluation.metrics` are `control_metrics`.
  - No experiment or hypothesis id is fabricated here. Remove `_CONTROL_*` ids.
- Tests:
  - with the cut after the last period, `control_metrics` equals `metrics_from_diagnostics`
    on a synthetic diagnostics object whose cells carry the full-series mean and HAC t;
  - the names and order are exact;
  - truncation is correct;
  - a NOT_ASSESSED cell is handled.
- Planner check after merge: with the cut at 2020-07-31 (full IS), the metrics must match
  the persisted C3 history metrics for the 99 C3 candidates.

## R3 — event test statistics are per cell, with a conservative headline (spec clarification)

`test_statistics` currently takes the descriptive statistics from the first assessed TD
cell. Required:
- descriptive statistics (mean of `l_series × sign`, sign, positive share, n) for **every**
  assessed TD cell, in `cells`;
- a headline that uses the cell with the **lower** mean, mirroring the minimum in the rank
  statistic; ties go to K0;
- `ewc_t` stays the minimum over cells with 24 or more test periods.
- Tests: a K0/K1 pair whose means differ in sign → the headline is the negative cell.

## Delivery

One new commit on the same branch, on top of `2eec225`. Do not amend. Report the SHA and
the test count.

## Second review (commit `18c6215`): R1–R3 accepted; R4 returned

**Planner real-data check:**
- Inputs: the 99 persisted C3 exports and C3's own persisted history metrics
  (generator-visible, read-only).
- `control_metrics` with the cut at 2020-07-31 reproduces every metric **value bitwise**:
  99 of 99 experiments, max difference 0.

**R4 (implementation defect):** for a NOT_ASSESSED cell, `n_obs` is 0, but the frozen
function emits the cell's B finite-period count (54 in C3, e.g. `l-t0_hac_t`
`{value: null, n_obs: 54}`).
- Required: a NOT_ASSESSED cell's `n_obs` = the number of training periods (`< cut`) of
  the B cell of the same scenario and event scenario (`L-T0` → `B-T0`, `L-TS-K1` →
  `B-TS-K1`).
- Test: a NOT_ASSESSED L cell next to an assessed B cell with 30 training periods →
  `n_obs == 30`.
