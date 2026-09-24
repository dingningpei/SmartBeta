# Pilot 1A — real run `pilot1a-real-deepseek-v1`: evidence snapshot & certification record

Immutable, git-tracked copy of the single authorized Pilot-1A real-model run.
The runtime original lives in the git-ignored `pilot_runs/pilot1a/pilot1a-real-deepseek-v1/`
and was never modified. `run/` is a byte-identical copy: all 24 files were
verified against the hashes captured immediately after termination. `SHA256SUMS`
covers every file in `run/` and `launcher/`. Frozen plan:
`worker_tasks/pilot1/pilot1-plan.md` (§26b TF firewall, §26e governance, §26f
DeepSeek).

## Classification (user review, 2026-09-24)

| Dimension | Disposition |
|---|---|
| **Pilot-1A operational validation** | **PASS** |
| **Scientific alpha certification** | **NOT ESTABLISHED** |
| **Production trading readiness** | **NOT CERTIFIED** |
| **Comprehensive actual-secret-value artifact exclusion** | **NOT CERTIFIED** (see Known gaps) |

Pilot 1A is **terminal**: the run registered empirical experiments, so no
retry, no replacement run_id and no second Pilot-1A run are permitted, and the
holdout is not consumed again.

## Identity

| Item | Value |
|---|---|
| run_id | `pilot1a-real-deepseek-v1` (run_mode `real`; predecessor: none) |
| Code HEAD (bound commit) | `a901c4fec47b8d5be22b5f61a0caa534514195d1` (sealed base `phase9-complete` → `76691c8…`) |
| Config canonical hash | `a9d45b97ed25a1e23239df82a15c66d3fd9fb7c82434a46fc431a57ea46162c1` |
| Config file SHA-256 | `58bcaeac8e45fa52604e159700597abc7de23403b9ff86872cc9ecd5be6376ae` (`run/config.json`; also committed at `pilot_configs/pilot1a-real-deepseek-v1.json`) |
| Scientific prompt template | `smart_beta.pilot.prompt:REAL_PROMPT_TEMPLATE_TEXT`, SHA-256 `948454e9a2e94e35765b860237be72db5d0d47684dc2d99050517283e03e93e6` (= `run/prompt_template.txt`) |
| Transport system message | `"Return json."` (transport only; inside every complete-request hash) |
| Complete request hashes | call 0 `27349fc02a9891e8d3ce4a1d3c855a3c5fa56451ab3f5c269da65d43b3a8d98d`; call 1 `1a807e8c000bd872e38fc98260ea266ddf72b30fdfd9cedc41a55b6a70700871`; call 2 `df71ec439d243398a80c13736139729acb9d9adcfef5c912fe6867c11837e7ef` |
| Provider / model | DeepSeek, `deepseek-v4-pro` (documented DeepSeek-V4-Pro-0813); provider-returned model id on all calls: `deepseek-v4-pro`; SDK `openai` 3.19.2 against `https://api.deepseek.com` |
| Request settings | `response_format=json_object`, `reasoning_effort=high`, `max_tokens=12000`; no tools, sampling parameters or seed; SDK retries 0; timeout 600 s |
| Dataset / fixture | Phase-5A Gate-B US (fixed 26-name dated DJIA snapshot), single input `daily_total_return` (LIVE_RECORDED); fixture tree `84c80f574d90d6cc4567eb5369eb22f450580936` + per-file hashes verified; offline replay only |
| Partition | IS 2025-10-15…2026-03-31 · OOS 2026-04-01…2026-06-30 · final holdout 2026-07-01…2026-09-15 (inclusive) |
| Holdout identity | `e74807afe9d9e41c25487221860aedfe8827697290efa9b42988e42f47ab4bb8` |
| Family identity | `5a3eed7c2f65bfdf8951b6768a59699862f08e6e9239d2580f26a2fbc685bb49` |
| Price table | `deepseek-api/deepseek-v4-pro/peak/2026-09-24` (input $1.32/MTok cache-miss peak, output $3.96/MTok peak; source api-docs.deepseek.com, retrieved 2026-09-24) |
| Budgets | invocations 5 (3 effective); cumulative input 100,000 / output 40,000 tokens; 12,000 max output per call; USD 1.00; wall clock 1,800 s; proposals 3; statistical m = 3 (fixed-m Bonferroni, α = 0.05) |
| Transaction cost | 10 bps ONE_WAY |
| Credential handling | `DEEPSEEK_API_KEY` typed by the user into a no-echo prompt of `launcher/pilot1a_real_launch.py`, running in a dedicated Herdr pane. Never visible to the planner; captured by the runner and deleted from `os.environ` before any subprocess. |

## Terminal state

`completed_stop`, typed stop **`proposal_budget_exhausted`**, reached at the
pre-generation budget check after the third proposal. Journal: 74 records,
`run_started` → `run_closed`, SHA-256
`8189309046d1c94ada7155750e913536ac865ad460313b1845ed47ee5e4ad0c3`.

## Certification findings

**1. Real model loop.**
- 3 DeepSeek calls (all `finish_reason=stop`, no provider error).
- 3 GenerationEvents, each with exactly one candidate.
- 3 proposals, 3 Phase-6-admitted FactorSpecs, 3 registered empirical
  experiments.
- Terminal stop: `proposal_budget_exhausted`.
- Reconstruction: **EXACT** (74 records, 0 mismatches; every normalization,
  evaluation and decision re-derivation matched).
- Structural firewall: **PASS** (3 invocations).
- Temporal firewall: **PASS** (216 items, 0 failures, D = [2025-10-15, 2026-07-01)).

| # | Generated FactorSpec | Phase-8 decision | Reason codes | Search slot |
|---|---|---|---|---|
| 1 | `standardize(ret)` | **ACCEPT** | — | 0 → 1 of 3 |
| 2 | `rank(ret)` | DEFER | `holdout_previously_consumed` | 1 → 2 of 3 |
| 3 | `rank(rolling_mean(ret, 5))` | DEFER | `holdout_previously_consumed` | 2 → 3 of 3 |

Evaluation records: `e0c500a7…`, `199934bb…`, `a9b712e1…`. DecisionRecords:
`82e29894…`, `c095a589…`, `efc5d9f9…`. Full values are in
`run/records/evaluation_record.jsonl` and `run/records/orchestration_outcome.jsonl`.
Experiment 1's holdout fold: IC −0.0020, rank-IC −0.0463, net long-short
−0.0014/day, Sharpe −1.80.

**2. Holdout governance.** The real holdout `e74807af…` was consumed exactly
once, by experiment 1 (`92f08135…`). Experiments 2 and 3 were DEFER because of
`holdout_previously_consumed`. Pilot 1A is therefore terminal.

**3. Decision semantics.** Experiment 1's ACCEPT is a **structural/governance
ACCEPT only**: evidence complete, provenance consistent, search admissible,
first exact holdout use. The sealed Phase-8 judge applies no performance
criterion. The ACCEPT is **not** scientific evidence of alpha, **not** economic
validation, and **not** production-trading readiness.

**4. Semantic-redundancy finding (future design issue).** `standardize(ret)` and
`rank(ret)` produced identical rank-based portfolio behavior under the current
evaluation construction: identical rank-IC, long-short, Sharpe and drawdown in
every fold; only Pearson IC differs. They nevertheless consumed separate
statistical slots. This is consistent with the existing semantic-equivalence
nonclaim. The experiments are **not** retroactively merged, and search-family
accounting is **not** altered.

**5. Secret-audit gap.**
- The original run's credential-pattern sweep passed.
- Comprehensive actual-secret-value exclusion across the entire artifact
  package was **not** established: the runner-side value-aware sweep covered
  only 1 file, and the G6 value check does not know `DEEPSEEK_API_KEY`.
- This historical result is **not** rewritten by any later code fix.

## Audit results

| Audit | Result |
|---|---|
| Reconstruction | RECONSTRUCTION_EXACT |
| Structural (key/schema) firewall | PASS |
| Temporal information-flow firewall | PASS (216 items, 0 failures) |
| G6 credential-pattern sweep (run time) | PASS |
| Runner value-aware credential sweep (run time) | passed over **1 file only**, so not comprehensive |
| Snapshot credential-pattern scan (2026-09-24; offline; all 24 package files + config) | 0 hits for `sk-…`, `sk-ant-…`, Bearer tokens, Phase-5A `key/token = …`, PEM private keys, AWS key ids, and credential-name/long-value pairs |
| Structured field inspection (every JSON/JSONL object) | only credential-like field name: `security.credentials`, holding the policy label `"model-only-runtime"`; no credential value |
| Network | real-mode endpoint allowlist (only `api.deepseek.com:443`); `urllib` blocked; market data offline; 0 Anthropic calls. No per-connection network log is recorded; this rests on in-process enforcement. |
| Usage / cost | prompt 24,619 · completion 8,385 (incl. reasoning 7,451) · cache hit 4,992 / miss 19,627; conservative peak cost **USD 0.0657** (every input token at the cache-miss rate); all ceilings respected |

## Exact dispositions (frozen wording)

- **Operational PASS:** "Pilot 1A demonstrated that the sealed Phase-6/7/8/9
  research architecture, connected through the Pilot-1A harness to a real
  DeepSeek model and to offline, integrity-verified, PIT-certified Gate-B data,
  executed and durably recorded a governed end-to-end research loop — three
  write-ahead generation events, three pre-evidence proposals, three
  Phase-6-admitted FactorSpecs, three Phase-7 evaluations and three Phase-8
  governed decisions — ending in the typed stop `proposal_budget_exhausted`,
  with the final holdout consumed exactly once, the structural and temporal
  firewalls intact, no sealed authority modified, and exact offline
  reconstruction."
- **Scientific nonclaim:** "Pilot 1A establishes no scientific evidence of
  alpha. Experiment 1's ACCEPT is a structural/governance acceptance under a
  DecisionPolicy that applies no performance criterion; its holdout
  performance was negative; the holdout had been inspected during Phase 5A;
  the sample is 26 names over about one year with a survivorship-unsafe
  universe; and no significance test certifies any factor."
- **Production-readiness nonclaim:** "Nothing in Pilot 1A certifies production
  or trading readiness, economic usefulness, capacity, or live execution."
- **Secret-audit limitation:** "Credential-pattern sweeps of the complete
  artifact package passed at run time and again at snapshot time. Comprehensive
  exclusion of the actual secret value across the entire package is NOT
  CERTIFIED: the run-time value-aware sweep covered only one file, and the
  actual value is not available for a retrospective comparison."

## Nonclaims

- Clean scientific discovery.
- An untouched holdout.
- Alpha or statistical significance.
- Economic usefulness.
- Production or trading readiness.
- Autonomous scientific creativity or optimal hypothesis generation.
- Causal discovery.
- Deterministic LLM regeneration.
- Model ignorance of the sample period.
- Semantic-equivalence detection.
- Complete adaptive multiple-testing correction.
- Survivorship-safe membership.
- Provider universality.
- China A-share applicability.
- Total side-channel elimination.

The end-to-end temporal firewall is guaranteed here by the Pilot harness guard,
not by Phase 9 alone (FIX B deferred; see `docs/phase9_research_loop_certification.md` §9).

## Known gaps

1. Actual-secret-value exclusion is not certified (finding 5). A future-run
   fix is proposed separately; this record stays as written.
2. Semantic redundancy consumed statistical slots (finding 4). It is a
   future design issue.
3. Holdout governance is per-run in memory, and `holdout_id` has no run or
   program identity. Any future multi-run or cross-LLM comparison needs a
   persistent cross-run holdout ledger first.
4. No per-connection network log exists. Network isolation rests on the
   in-process allowlist.
5. DeepSeek JSON mode enforces no schema. Validity came from the frozen prompt
   and harness validation, not from provider enforcement.

## Layout

```
run/                      byte-identical copy of pilot_runs/pilot1a/pilot1a-real-deepseek-v1/
  journal.jsonl           the authoritative hash-chained run journal
  manifest.json config.json prompt_template.txt report.md
  firewall_audit.json temporal_firewall_audit.json secret_sweep.json reconstruction_report.json
  records/*.jsonl         per-kind extracts (interrupted.jsonl is present and empty)
launcher/pilot1a_real_launch.py   planner launcher (no secret; getpass prompt only)
SHA256SUMS                hashes of every file under run/ and launcher/
```

Verify with: `cd pilot_evidence/pilot1a-real-deepseek-v1 && shasum -a 256 -c SHA256SUMS`.
