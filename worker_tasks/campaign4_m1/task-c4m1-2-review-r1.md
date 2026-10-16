# C4M1-2 — review return R1 (planner, 2026-10-16)

Review of worker commit `1805604` on base `a6a0a91`. The 60 tests pass when re-run.

**Accepted:**
- **Executor fallback.** A thin subclass that overrides only the schema and the id
  domains, as the spec's fallback clause allows.
- **DeepSeek transport.** The request dict is identical to C3's `generate`: model,
  messages, max_tokens, reasoning_effort and stream.
- The harness, the cache, the selection-before-test ordering and the CLI.

Three implementation defects are returned. They change no frozen rule.

## R1 — treatment `TaEvaluation.metrics` must be training-window, with no swallowed errors

`_ta_from_evaluation` stores the **full-IS** `metrics_from_diagnostics` in the executor
history, and it silently uses `()` on any exception. The treatment generator does not render
that history, but full-IS numbers then sit in the run state next to the training-window
ones.

Required:
- both arms store `control_metrics(base_export, not_assessed=..., window_cut=cut)`;
- remove the `try/except Exception`;
- test: the persisted treatment history contains no metric whose `n_obs` exceeds the
  training period count.

## R2 — a malformed treatment response is rejected and counted, not a run stop

C3 turns an unparseable or ill-shaped raw artifact into a `MALFORMED` event, counts it, and
continues (`generator.py` ~1585; `_plan_for_event` counts a zero-entry event as 1). The
treatment generator raises `C4ContractError` from `parse_campaign4_response`. The executor
wraps it as a generator failure and the run stops. The two arms would then follow different
failure rules.

Required:
- when the C4 parse fails (invalid JSON, wrong `schema_version`, no `candidates` list),
  pass the **raw response unchanged** to the C3 boundary, so it is normalized as
  MALFORMED, counted and shown in the feedback as `SCHEMA_REJECTED`;
- keep the raw bytes and the SHA-256 in the sidecar;
- transport and length failures still stop the run, as in C3;
- tests:
  - invalid JSON;
  - a `campaign3-candidates-v1` schema in the treatment arm;
  - for both: counted, not evaluated, and the run continues to the next call.

## R3 — NOT_CERTIFIED must come from the certification outcome, not be inferred

`finalize_event` labels every admitted candidate that the evaluator did not see as
NOT_CERTIFIED.
- Required: take the label from the executor's actual certification result for that
  proposal. Any other "admitted but not evaluated" case gets its own explicit reason.
- Test: a NOT_CERTIFIED fixture is labelled from the certification result.

## Delivery

One new commit on top of `1805604`. Do not amend. Report the SHA and the test count.
