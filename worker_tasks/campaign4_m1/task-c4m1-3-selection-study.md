# Campaign-4 M1 — task C4M1-3: selection-rule study on the M1-e pools (FROZEN 2026-10-19)

## Authority

- `worker_tasks/campaign4_m1/campaign4-m1-plan.md`: the binding constraints apply in full.
  Constraint 2 (training-window firewall) applies to **selection**. A test-window value may
  be computed only after the rule's selection file is written and hashed.
- `docs/campaign4_m1s_selection_study_draft.md` §1–§4.
- `docs/campaign4_m1e_result.md`.
- **Base:** master at launch. Depends on C4M1-1 and C4M1-2, both merged.

## Content (all new files)

### 1. `smart_beta/campaign4/selection_study.py`

**Pool reconstruction**
- `load_run_pool(run_dir) -> RunPool`: read one M1-e `executor_state.json` (read-only).
- Return the run's arm, origin, window cut, and the ordered eligible proposals: proposal
  id, proposal order, `FactorSpec`, spec hash.
- **Eligible** means the run labelled the proposal EVALUATED: certified, A4-admitted
  (treatment), evaluated, assessed, and with a VALID training rank statistic.
- Document exactly which state fields define it. If the state cannot identify EVALUATED
  unambiguously, STOP and report; do not guess.

**Series cache**
- `SeriesCache(directory)`: for each spec hash, persist the per-period L-TD **net** series
  of every TD scenario (all 54 IS formations) as canonical JSON.
- On a hit, load from disk without re-evaluation. The first evaluation goes through the
  injected evaluator.
- The rank statistic recomputed from the cached series must equal the one from the live
  export, bitwise.

**Statistics.** These are new helpers. Each reuses the EWC procedure exactly as
`campaign4/ranking.py` does, and with a full training window must reduce to
`rank_statistic` bitwise.
- `s0_stat(series, cut)`: equals `rank_statistic(...).value`.
- `s1_stat(series, cut, last_n=24)`: the min over TD scenarios of the one-sided EWC t on
  the last 24 training periods.
- `halves_positive(series, cut) -> bool`: the training periods split into the first half
  and the second half (30 → 15 + 15; 42 → 21 + 21; an odd count is an error). True iff the
  mean net return is > 0 in both halves, in every TD scenario.
- `composite_series(top5)`: for each candidate, its argmin TD scenario (the scenario that
  attains its S0 value; a tie takes the first scenario in export order). The result is the
  equal-weight period-by-period mean of those 5 series. Fewer than 5 eligible candidates
  is an error recorded in the report.

**Rules** `S0`, `S1`, `S2`, `S3` exactly as in the draft §2.
- Ties go to the earlier proposal.
- S2 with an empty filter → `NO_SELECTION`.
- Each rule writes `selection_<rule>_<arm>_<origin>.json` (pick, spec hash(es), training
  value) and hashes it **before** any test-window value is read. A spy test enforces the
  order.

**Test statistics**
- **W1:** the one-sided EWC t of the pick's series on the test periods. S0–S2 take the min
  over TD scenarios; S3 uses the composite series. Report mean, sign, positive share and n.
- **W2:** descriptive only (mean, sign, positive share, n = 12). No t.

**Pool base rate** (after all selections are written)
- For each run, the test value of every eligible candidate (its min-scenario W1 EWC t, or
  its W2 mean).
- Report the median, the share > 0, the max, and the Spearman correlation between the S0
  training value and the test value.

**Flags**, as in the draft §3:
- PROMISING per rule;
- POOL-LIMITED per study.

### 2. `scripts/run_c4_selection_study.py`

- **Arguments:**
  - `--dataset-root`, `--knowledge-log` (the K-PIT copy), `--m1e-run-root`
    (read-only), `--series-cache`, `--output-dir`;
  - `--runs` (default all four);
  - `--jobs N` (default 1).
- Build the assembly and evaluator exactly as `scripts/run_c4_walkforward.py` does, by
  import (`_build_evaluator`, `build_identity_generator`). Do not copy them.
- **Parallelism:**
  - `--jobs > 1` evaluates cache misses in a process pool;
  - each worker writes its own cache file atomically;
  - the result must be independent of `--jobs`: the report is byte-identical for 1 and 2
    on the synthetic fixture.
- **Refusals:**
  - an output or cache directory inside `data_store/` or inside the repository;
  - writing into the M1-e run root.
- **Report:** `c4_selection_study_report.json` (canonical, content-hashed) + `.md`:
  - per rule × run: the pick, the training value and the test result;
  - the base rate;
  - the flags;
  - trial accounting: 16 selection trials, plus M1-e's 400, recorded only.
- **S0 reproduction check:** on real data, S0 must reproduce the M1-e selection and the
  rank-1…5 test statistics recorded in the M1-e report (`--m1e-reports <dir>...`). A
  mismatch makes the run exit non-zero, with the report marked `REPRODUCTION_FAILED`.

## Tests (synthetic only; `tests/test_c4m1_selection_study.py`; no network, no real data)

1. **Pool:** a synthetic executor state with all six statuses gives exactly the EVALUATED
   proposals, in proposal order.
2. **Statistics:**
   - `s0_stat` == `rank_statistic` bitwise on a synthetic export;
   - `s1_stat` with `last_n` = the training length == `s0_stat`;
   - `halves_positive` splits correctly, and an odd count raises;
   - `composite_series` uses the argmin scenario and equal weights.
3. **Rules:** hand-built pools where S0, S1, S2 and S3 each pick a known, different answer;
   S2 gives NO_SELECTION when the filter is empty; ties go to the earlier proposal.
4. **Firewall:**
   - a spy shows every selection file is written before the first test-period read;
   - an injected leak of a test period into a rule raises `C4FirewallError`.
5. **Cache:**
   - a second run makes zero evaluator calls;
   - a cached series reproduces the rank statistic bitwise;
   - the report is identical for `--jobs 1` and `--jobs 2`.
6. **Reproduction:** a mismatched S0 versus a supplied M1-e report gives
   `REPRODUCTION_FAILED` and a non-zero exit.
7. **Safety:** no K-PIT write (file spy); the M1-e run root is untouched (hash its files
   before and after); refusal of in-repo or `data_store/` output paths.

No unit test calls git. The reviewer checks that `git diff` of the protected paths (plan
constraint 3) against the base is empty.

## Stop rules

STOP and report if:
- EVALUATED cannot be reconstructed unambiguously from the M1-e state;
- the C4M1-1 / C4M1-2 modules or the EWC procedure would need modifying;
- `s0_stat` cannot be made bitwise-equal to `rank_statistic`;
- any real data or network access would be needed in tests.
