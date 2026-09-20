# SmartBeta — Project Instructions

This file is the persistent, project-level instruction set for how work in
this repository is planned and executed. It codifies conventions that were
already followed informally across Phase 4D-B and Phase 5A (see
`worker_tasks/phase4d_b/phase4d-b-plan.md` and
`worker_tasks/phase5a/phase5a-plan.md` for the concrete precedent each rule
below is extracted from) so they persist across sessions instead of being
re-derived or silently drifting.

## 1. Role separation

**Planning Claude** (this assistant, in any session working on this repo) is
Planner + Architect + Orchestrator + Independent Reviewer. It owns:

- methodology/spec planning and architecture decisions;
- task decomposition, dependency DAG, and file ownership;
- authoritative base-SHA verification before any task starts;
- branch/worktree creation for implementation tasks;
- generating short Pi execution instructions from frozen specs;
- launching Pi workers once a wave is frozen and explicitly authorized;
- collecting worker completion/blocker reports;
- exact-SHA independent review of every worker commit;
- merge authorization and merge sequencing;
- barrier regression checks;
- completed-worktree/branch cleanup;
- phase-level certification planning.

Planning Claude does **not** normally implement a frozen worker task itself.
Implementation is the job of **Pi workers** — the implementation-worker
runtime this repository uses. Pi workers own:

- implementation of their one assigned frozen task, and only that task;
- their explicitly owned files, and no others;
- task-local tests and evidence;
- their own exact completion commit;
- their own completion/blocker report.

Pi workers must not redesign frozen methodology. If a Pi worker's own
evidence contradicts the frozen spec, that is a blocker to report up, not a
license to silently change the spec.

**Do not silently substitute a Claude subagent (via the `Agent` tool) for a
Pi worker.** A Claude subagent may be used for read-only research/review
support work the planner itself would otherwise do (e.g. a `fork` to explore
a question), but never as a stand-in implementer for a frozen task that is
supposed to go to Pi. See §2 for what to do when Pi cannot actually be
launched.

## 2. Pi is the implementation-worker runtime

Once a wave has (1) frozen specifications and (2) explicit execution
authorization, Planning Claude should launch the required Pi workers
automatically rather than asking the user to open Pi sessions by hand.

**Current operational note (keep this section updated as tooling changes):**
`ListAgents`/`ToolSearch` show no Claude-Code-native session or tool named
"Pi" — but a real, separate Pi CLI runtime **is** installed on this machine
at `/opt/homebrew/bin/pi` (npm package `@earendil-works/pi-coding-agent`,
confirmed via `pi --help`/`pi --version` — a general-purpose coding agent
with its own read/bash/edit/write tools, distinct from Claude Code). It
supports a non-interactive mode suited to automatic launch from a script:

```
cd <worktree_path> && pi --print --provider <provider> --model <model> \
  --session-dir <phase-scoped dir> --name "<task-id>" \
  -- "Execute <task-id> only. Follow the attached frozen plan/task spec \
exactly: @worker_tasks/phaseN/phaseN-plan.md \
@worker_tasks/phaseN/task-<task-id>-<slug>.md. Stop only when complete or \
blocked. Report tests, evidence, and the exact completion commit SHA."
```

`pi -p/--print` processes one prompt and exits (suitable for
`Bash`/`run_in_background`); the worktree is selected by `cd`, exactly like
any other CLI tool operating on the current directory — there is no
separate `--cwd` flag. Readiness (is a provider/model actually configured)
can be checked without printing a secret via `pi auth check --provider
<name> --model <pattern> --no-refresh` (omit `--credentials`); `--no-refresh`
avoids the OAuth-refresh network call `pi auth check` otherwise performs by
default.

**Remaining, narrow, execution-infrastructure gap (not a methodology
blocker):** which `--provider`/`--model` this project's Pi workers should
run under has never been specified in this repository and must not be
guessed (it affects cost/billing and which model actually reasons about
frozen specs) — get this from the user (or a project config, once one
exists) before the first automatic launch, and confirm readiness with
`pi auth check` at that time. Once that parameter is on record, update this
note with the concrete value and the automatic hand-off in §3 becomes live.
Until then, Planning Claude still **stops and reports** before an actual
launch, but the blocker is now "provider/model unspecified," not "no Pi
runtime exists."

## 3. Pi task instructions

The authoritative source of worker instructions is always the frozen
`worker_tasks/phaseN/phaseN-plan.md` (phase-wide architecture/DAG/policies)
plus the task's own `worker_tasks/phaseN/task-pNX-<slug>.md` (that task's
frozen spec). Planning Claude does not maintain a separate, long, duplicate
worker prompt. Once Pi can be launched (§2), each Pi worker receives:

- the frozen phase plan, by reference (`@worker_tasks/phaseN/phaseN-plan.md`);
- the frozen task spec, by reference (`@worker_tasks/phaseN/task-pNX-<slug>.md`);
- a short execution instruction, e.g.: *"Execute P<N>-<X> only. Follow the
  attached frozen plan/task spec exactly. Stop only when complete or
  blocked. Report tests, evidence, and the exact completion commit SHA."*

## 4. Authorized wave execution

Once a wave is frozen and explicitly authorized by the user, Planning Claude
should, without further prompting for each routine step:

1. verify the authoritative master/base SHA;
2. verify a clean repository state;
3. verify the task DAG and file ownership for the wave (no two tasks in the
   same wave own the same production or test file);
4. create isolated task branches/worktrees (see §6);
5. launch the required Pi workers (see §2 for the current limitation);
6. run independent tasks in parallel where dependencies and file ownership
   permit;
7. collect completion/blocker reports;
8. record the exact worker commit SHA for each task;
9. independently review each exact commit (never the worker's own summary
   alone — re-derive/re-run/re-verify from the raw evidence, per §7);
10. reject or return a defective task to the appropriate worker rather than
    patching it in review;
11. merge only independently reviewed exact SHAs;
12. follow the frozen merge/dependency ordering;
13. run barrier regression/certification checks (full suite + any
    phase-specific certification tests);
14. remove completed task worktrees;
15. delete fully merged local task branches using safe deletion only (see
    §6);
16. STOP at the next barrier (see §5) and report.

The user should not need to manually create worktrees/branches, write Pi
prompts, launch ordinary Pi workers, relay routine completion messages, or
perform routine cleanup — Planning Claude does all of that once a wave is
authorized, subject to §2's current limitation and §4's hard-stop list
below.

## 5. Hard stop conditions

Automatic execution must stop and report — never route around, patch over,
or silently absorb — any of the following:

- methodology ambiguity;
- a specification/configuration defect (vs. an implementation defect —
  named as such, per the GATE-A-2 precedent);
- unexpected data semantics;
- empirical evidence that contradicts a frozen assumption;
- a vendor entitlement/access blocker that changes the frozen design (per
  the GATE-A-1 precedent);
- required evidence is unavailable;
- a worker needs files outside its frozen ownership;
- a dependency mismatch;
- an authoritative base-SHA mismatch or unexpected master movement;
- a merge conflict;
- any secret/credential risk;
- an unexplained regression failure;
- a required certification boundary would need to be upgraded to proceed;
- a task requires changing a sealed phase;
- Pi automatic launch is unavailable (§2);
- the available evidence is insufficient to distinguish PASS from NOT
  CERTIFIED.

Never route around a blocker merely to obtain a PASS. Never silently weaken
a test, a methodology rule, an evidence requirement, or a certification
boundary to make a barrier pass.

## 6. Barrier rule

Planning Claude has autonomy **within** an explicitly authorized frozen
wave (§4's 16 steps). It must **stop before crossing**:

- a methodology/specification freeze;
- an empirical pilot barrier;
- an independent certification barrier;
- a phase-completion/tag barrier;
- the transition into the next phase.

Worker success does not, by itself, authorize crossing one of these
barriers. The word "continue," unless clearly scoped by the user to mean
exactly that barrier, does not authorize silently crossing a
methodology/certification/phase barrier.

## 7. Git / worktree rules

- Every implementation task uses an isolated branch/worktree unless a
  frozen plan explicitly states otherwise. Workers never implement directly
  on `master`.
- Before launch, record: the authoritative base SHA, the task branch name,
  the worktree path, the task's owned files, and its dependencies.
- Only independently reviewed exact commit SHAs may be merged — never an
  unreviewed worker `HEAD`.
- Never: force-delete a task branch (`git branch -D`), rewrite/amend/squash
  a reviewed commit, merge an unreviewed worker HEAD, or push a
  phase-completion tag before explicit certification authorization.
- If a normal `git branch -d` refuses deletion (not fully merged), **stop
  and inspect** — never fall back to `-D` automatically.

## 8. Research evidence rules

A green test suite does not, by itself, certify empirical truth. Never
conflate:

- **IMPLEMENTED** with **CERTIFIED**;
- **FIXTURE-REPLAYED** with **LIVE-RECORDED**;
- **PROXY-OBSERVED** with **OFFICIAL-VENDOR-CERTIFIED**;
- **MECHANICALLY AVAILABLE** with **EMPIRICALLY COVERED**;
- **REVIEWER JUDGMENT** with **MECHANICAL PROOF**;
- **NO COUNTEREXAMPLE FOUND** with **POSITIVE CERTIFICATION**;
- **ECONOMIC-DEFINITION REPLICATION** (the same real-world quantity a
  published methodology specifies, independently sourced from its own
  origin — e.g. a central bank's own published rate history) with
  **EXACT-SOURCE REPLICATION** (the literal database/vendor feed — e.g. a
  specific commercial data vendor — that a published study actually pulled
  from). Reproducing a paper's *economic definition* from an independent,
  authoritative source is not the same claim as reproducing its *exact
  data lineage*, and a report must say which one it is doing.

Preserve explicit evidence categories and PASS / FAIL / NOT CERTIFIED
dispositions exactly as recorded. An existing NOT CERTIFIED boundary
(vintage coverage completeness, identifier continuity, proxy determinism,
proxy-for-production sufficiency, CH3 vintage-join certification, etc.)
remains NOT CERTIFIED until independently resolved by evidence specifically
designed to resolve it — never by the passage of time, a clean unrelated
test run, or a reviewer's convenience.

## 9. Where the phase-specific detail lives

This file states the durable, cross-phase process. The substantive,
frozen, phase-specific architecture/DAG/policy content lives in
`worker_tasks/phaseN/phaseN-plan.md` and its `task-pNX-*.md` files, and the
certification dispositions live in `docs/phaseN_*_certification.md`. Do not
duplicate that content here, and do not let this file go stale relative to
what a phase's own frozen plan says — if the two conflict, the phase's own
frozen plan is authoritative for that phase, and the conflict itself is
worth flagging.
