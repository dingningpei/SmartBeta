"""Phase 8 P8-E: the deterministic skeptical judge.

This module owns **only** the skeptical judgment described by
``worker_tasks/phase8/phase8-plan.md`` sections 7.6 (``DecisionPolicy``), 7.7
(``DecisionRecord``), 9 (decision outcomes / reason codes), 12 (adversarial
suite) and the P8-E row of section 13's task table. The judge **consumes**
immutable frozen evidence and contracts and deterministically produces a
:class:`~smart_beta.experiment.policy.DecisionRecord`. It **adjudicates**
evidence; it never creates, optimizes or recomputes it.

Authoritative inputs (all frozen, all read-only)
-----------------------------------------------

* the Phase-7 :class:`~smart_beta.evaluation.spec.EvaluationRecord` (opaque,
  immutable evidence);
* the frozen P8-D :class:`~smart_beta.experiment.policy.DecisionPolicy` and
  :class:`~smart_beta.experiment.policy.SearchPolicy` contracts;
* a deterministic P8-A
  :class:`~smart_beta.experiment.registry.RegistrySnapshot`;
* the P8-C search-governance verdict
  (:class:`~smart_beta.experiment.search.SearchGovernanceDecision`);
* the P8-B holdout-governance evidence
  (:class:`~smart_beta.experiment.policy.HoldoutGovernanceEvidence`).

Authority delegation (consume, never re-implement)
--------------------------------------------------

* **Search governance is P8-C's.** The judge consumes the
  ``SearchGovernanceDecision`` verdict; it never recounts attempts from
  registry rows and never substitutes ``attempts_so_far`` for the frozen
  ``family_alpha`` / ``family_budget_m``. ``BUDGET_EXHAUSTED``,
  ``LOCK_VIOLATION``, ``CONFLICT``, ``EVALUATION_MUTATION`` and
  ``FAMILY_LINEAGE_MIGRATION`` are non-admissible and fail closed (DEFER by
  default, or the :class:`DecisionPolicy`-mapped outcome).
* **Holdout governance is P8-B's.** The judge consumes the
  ``HoldoutGovernanceEvidence`` (``holdout_id``, ``prior_consumption``,
  ``prior_consumed_by``). It implements no second holdout-identity algorithm.
  A consumed exact required holdout fails closed per the frozen policy;
  overlapping-but-nonidentical holdouts are *not* treated as exact reuse.

The judge never recomputes factor values, ``EvaluationRecord`` metrics,
portfolio construction or forward returns; never chooses a better
spec/parameter/universe after seeing results; never rewrites registry history
or evaluation evidence; never changes ``family_id`` / ``family_alpha`` /
``family_budget_m`` / ``procedure`` / ``trial_unit``; never manufactures a
search slot; never resets family history; never redefines holdout identity;
never silently reuses a consumed exact final holdout; and never performs
automatic semantic-family inference. Orchestration / state machines
(``PROPOSED -> ... -> DEFERRED``) are explicit non-goals (P8-F / post-Phase-8).

Decision outcomes (frozen; section 9)
-------------------------------------

``ACCEPT`` / ``REJECT`` / ``DEFER``. ``DEFER`` is a first-class outcome and is
**not** ``REJECT``:

* ``ACCEPT`` -- all mandatory evidence requirements satisfied **and** search
  governance admissible;
* ``REJECT`` -- evidence is complete/adjudicable **but** a mandatory frozen
  acceptance requirement definitively fails;
* ``DEFER`` -- evidence/governance/provenance is insufficient, conflicting,
  unavailable, uncertified or otherwise non-adjudicable. Known failure may
  justify ``REJECT``; unknown/missing/uncertified is never upgraded to a known
  failure, so the fail-closed default is ``DEFER``.

Policy authority
----------------

The :class:`DecisionPolicy` is predeclared authority. The judge applies
``required_evidence``, ``minimum_n_obs``, ``require_is_oos``,
``require_holdout``, ``holdout_reuse``, ``required_search_policy``,
``redundancy_threshold`` (only when set -- an unset optional criterion never
becomes mandatory), the ``decision_outcomes`` reason->outcome mapping and
``fail_closed`` exactly. It invents no criterion and removes none.

Scope decision (documented, not hidden)
----------------------------------------

The frozen plan does not put a candidate p-value on the ``EvaluationRecord``,
and the judge is forbidden to recompute one. The frozen statistical criterion
the judge can validly apply is therefore the P8-C search-governance verdict:
the fixed-m Bonferroni attempt accounting is admissible (``ADMISSIBLE`` or a
deterministic ``REPLAY``) and the frozen threshold ``family_alpha /
family_budget_m`` is recorded. ``MULTIPLE_TESTING_HURDLE_NOT_MET`` remains part
of the frozen reason-code vocabulary but is only emitted when the policy maps
it; the judge never invents an observed statistic. Similarly, ``minimum_n_obs``
is applied to the smallest ``n_obs`` over the record's per-fold metric
evidence -- a conservative, deterministic reading of "minimum sample
requirements" -- and a record whose sample size cannot be determined DEFERs.

Trust boundary (plan sections 4-6, 13)
--------------------------------------

This module imports the standard library plus the read-only Phase-8 contracts
(``policy`` / ``registry`` / ``search``) and, for the immutable evidence type,
``smart_beta.evaluation.spec``. It never imports ``smart_beta.pit``,
``smart_beta.vendors``, ``smart_beta.engines``, ``smart_beta.data`` or the
Phase-7 evaluation machinery, performs no I/O, no network/provider call, no
dynamic execution, and reads no clock, UUID or randomness. Every produced
``DecisionRecord`` is a pure deterministic function of its frozen inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

from smart_beta.evaluation.spec import EvaluationRecord, FoldRole
from smart_beta.experiment.policy import (
    BudgetExhaustion,
    DecisionOutcome,
    DecisionPolicy,
    DecisionRecord,
    EvidenceSection,
    HoldoutConsumptionResult,
    HoldoutGovernanceEvidence,
    HoldoutReuse,
    ReasonCode,
    SearchGovernanceEvidence,
    SearchPolicy,
)
from smart_beta.experiment.registry import (
    RegistrySnapshot,
    experiment_id_for,
)
from smart_beta.experiment.search import (
    SearchGovernanceDecision,
    SearchVerdict,
)

__all__ = [
    "JUDGE_VERSION",
    "JudgeError",
    "JudgeInputError",
    "judge_experiment",
]

#: Frozen judge version; part of every :class:`DecisionRecord` content hash.
JUDGE_VERSION = "p8-e-judge/1.0.0"

_HEX_DIGITS = frozenset("0123456789abcdef")
_SHA256_LENGTH = 64


class JudgeError(ValueError):
    """Base class for malformed judge inputs."""


class JudgeInputError(JudgeError):
    """A judge input is malformed (wrong type or not a SHA-256 identity).

    A *semantic* provenance mismatch (a valid identity that disagrees with the
    frozen evidence) is **not** an error: it is reported as a fail-closed
    ``DEFER`` ``DecisionRecord``. Only malformed inputs -- from which no valid
    ``DecisionRecord`` could be built -- raise.
    """


@dataclass(frozen=True)
class _Finding:
    """One adjudication finding.

    ``unadjudicable`` marks evidence/governance/provenance that is
    insufficient, conflicting, unavailable or uncertified, as opposed to a
    known, definitively failed mandatory requirement.
    """

    reason: ReasonCode
    unadjudicable: bool


# ---------------------------------------------------------------------------
# Fail-closed validators
# ---------------------------------------------------------------------------


def _require_sha256(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise JudgeInputError(
            f"{field_name} must be a 64-char lowercase hex SHA-256 string, got "
            f"{type(value).__name__}"
        )
    if len(value) != _SHA256_LENGTH or any(ch not in _HEX_DIGITS for ch in value):
        raise JudgeInputError(
            f"{field_name} must be a 64-char lowercase hex SHA-256 string, got "
            f"{value!r}"
        )
    return value


def _require_type(value: object, expected: type, *, field_name: str) -> object:
    if not isinstance(value, expected):
        raise JudgeInputError(
            f"{field_name} must be a {expected.__name__}, got "
            f"{type(value).__name__}"
        )
    return value


def _require_optional_type(
    value: object, expected: type, *, field_name: str
) -> object | None:
    if value is None:
        return None
    return _require_type(value, expected, field_name=field_name)


# ---------------------------------------------------------------------------
# Structural evidence inspection (presence only -- no metric is computed)
# ---------------------------------------------------------------------------


def _evidence_present(record: EvaluationRecord, section: EvidenceSection) -> bool:
    """Whether the declared ``EvaluationRecord`` section is present.

    Presence is a structural test only: no metric, return, portfolio or
    partition is recomputed. A ``None``/empty section is missing evidence.
    """
    if section is EvidenceSection.PARTITION:
        return len(record.partition.folds) > 0
    if section is EvidenceSection.FOLD_RESULTS:
        return len(record.fold_results) > 0
    if section is EvidenceSection.METRIC_TABLES:
        return len(record.metric_tables) > 0
    if section is EvidenceSection.COST_ADJUSTED_SERIES:
        return len(record.cost_adjusted_series.index) > 0
    if section is EvidenceSection.SUBPERIOD_TABLE:
        return len(record.subperiod_table.rows) > 0
    if section is EvidenceSection.PARAMETER_SENSITIVITY_TABLE:
        return len(record.parameter_sensitivity_table.rows) > 0
    if section is EvidenceSection.UNIVERSE_SENSITIVITY_TABLE:
        return len(record.universe_sensitivity_table.rows) > 0
    if section is EvidenceSection.REDUNDANCY_MEASUREMENTS:
        return len(record.redundancy_measurements) > 0
    if section is EvidenceSection.PURGE_COUNTS:
        return len(record.purge_counts) > 0
    if section is EvidenceSection.HOLDOUT:
        return _has_holdout_fold(record)
    raise JudgeInputError(f"unknown evidence section {section!r}")


def _has_holdout_fold(record: EvaluationRecord) -> bool:
    for fold in record.partition.folds:
        if fold.role is FoldRole.HOLDOUT:
            return True
    return False


def _has_is_and_oos(record: EvaluationRecord) -> bool:
    has_is = False
    has_oos = False
    for fold in record.partition.folds:
        if fold.role is FoldRole.IS:
            has_is = True
        elif fold.role is FoldRole.OOS:
            has_oos = True
    return has_is and has_oos


def _observed_sample_size(record: EvaluationRecord) -> int | None:
    """The smallest recorded ``n_obs`` across the record's fold metrics.

    A conservative structural reading of ``minimum_n_obs``: the weakest fold's
    evidence governs. Returns ``None`` when no per-fold sample size can be
    observed (the sample size is then *unknown* -> DEFER, never a REJECT).
    """
    smallest: int | None = None
    for fold in record.fold_results:
        for metric in fold.metrics:
            n_obs = metric.n_obs
            if smallest is None or n_obs < smallest:
                smallest = n_obs
    return smallest


def _max_absolute_redundancy(record: EvaluationRecord) -> tuple[float | None, bool]:
    """``(largest |redundancy|, all_defined)`` over the record's measurements.

    ``all_defined`` is ``False`` when any measurement value is undefined
    (``None``) -- an undefined redundancy cannot be adjudicated against a
    threshold, so the judge DEFERs rather than treating it as passing.
    """
    largest: float | None = None
    all_defined = True
    for measurement in record.redundancy_measurements:
        value = measurement.value
        if value is None:
            all_defined = False
            continue
        magnitude = value if value >= 0.0 else -value
        if largest is None or magnitude > largest:
            largest = magnitude
    return largest, all_defined


# ---------------------------------------------------------------------------
# Outcome resolution (policy is the predeclared authority)
# ---------------------------------------------------------------------------


def _resolve_reason_outcome(
    finding: _Finding,
    *,
    policy: DecisionPolicy,
    search_policy: SearchPolicy,
) -> DecisionOutcome:
    """Resolve one finding to an outcome using the frozen policy.

    Precedence: an explicit ``decision_outcomes`` mapping wins; otherwise the
    frozen ``holdout_reuse`` / ``budget_exhaustion`` disposition applies;
    otherwise the policy's ``fail_closed`` disposition applies. A finding can
    never resolve to ``ACCEPT`` (a failure reason is never a pass).
    """
    mapped: DecisionOutcome | None = None
    for rule in policy.decision_outcomes:
        if finding.reason in rule.reason_codes:
            mapped = rule.outcome
            break
    if mapped is not None:
        return mapped if mapped is not DecisionOutcome.ACCEPT else policy.fail_closed
    if finding.reason is ReasonCode.HOLDOUT_PREVIOUSLY_CONSUMED:
        return (
            DecisionOutcome.REJECT
            if policy.holdout_reuse is HoldoutReuse.PROHIBITED
            else DecisionOutcome.DEFER
        )
    if finding.reason is ReasonCode.SEARCH_BUDGET_EXHAUSTED:
        return (
            DecisionOutcome.DEFER
            if search_policy.budget_exhaustion is BudgetExhaustion.DEFER
            else DecisionOutcome.REJECT
        )
    return policy.fail_closed


def _combine_outcomes(outcomes: object, policy: DecisionPolicy) -> DecisionOutcome:
    """Combine resolved outcomes fail-closed: ``DEFER`` > ``REJECT`` > ``ACCEPT``."""
    seen_defer = False
    seen_reject = False
    for outcome in outcomes:  # type: ignore[union-attr]
        if outcome is DecisionOutcome.DEFER:
            seen_defer = True
        elif outcome is DecisionOutcome.REJECT:
            seen_reject = True
    if seen_defer:
        return DecisionOutcome.DEFER
    if seen_reject:
        return DecisionOutcome.REJECT
    return DecisionOutcome.ACCEPT


# ---------------------------------------------------------------------------
# Provenance validation
# ---------------------------------------------------------------------------


def _validate_registry(
    findings: list[_Finding],
    *,
    experiment_id: str,
    hypothesis_id: str,
    record: EvaluationRecord,
    search_policy: SearchPolicy,
    snapshot: RegistrySnapshot,
) -> None:
    entry = None
    for candidate in snapshot.experiments:
        if candidate.experiment_id == experiment_id:
            entry = candidate
            break
    if entry is None:
        findings.append(_Finding(ReasonCode.PROVENANCE_MISSING, True))
    else:
        if entry.hypothesis_id != hypothesis_id:
            findings.append(_Finding(ReasonCode.PROVENANCE_MISSING, True))
        if entry.evaluation_record_hash != record.content_hash:
            findings.append(_Finding(ReasonCode.PROVENANCE_MISSING, True))
        if entry.family_id != search_policy.family_id:
            findings.append(_Finding(ReasonCode.PROVENANCE_MISSING, True))
    # The supplied experiment identity must be exactly the frozen
    # hypothesis + EvaluationSpec identity (P8-A owns the computation).
    if experiment_id_for(hypothesis_id, record.spec_hash) != experiment_id:
        findings.append(_Finding(ReasonCode.PROVENANCE_MISSING, True))


def _validate_search_governance(
    findings: list[_Finding],
    *,
    experiment_id: str,
    record: EvaluationRecord,
    search_policy: SearchPolicy,
    search_decision: SearchGovernanceDecision | None,
) -> None:
    if search_decision is None:
        # Search-governance state is incomplete -> non-adjudicable.
        findings.append(_Finding(ReasonCode.INSUFFICIENT_EVIDENCE, True))
        return
    if search_decision.family_id != search_policy.family_id:
        findings.append(_Finding(ReasonCode.PROVENANCE_MISSING, True))
    if search_decision.experiment_id != experiment_id:
        findings.append(_Finding(ReasonCode.PROVENANCE_MISSING, True))
    if search_decision.evaluation_record_hash != record.content_hash:
        findings.append(_Finding(ReasonCode.PROVENANCE_MISSING, True))

    verdict = search_decision.verdict
    if verdict is SearchVerdict.ADMISSIBLE or verdict is SearchVerdict.REPLAY:
        # The frozen fixed-m Bonferroni threshold must be the predeclared one.
        if search_decision.threshold_applied != search_policy.alpha_per_test:
            findings.append(_Finding(ReasonCode.PROVENANCE_MISSING, True))
        return
    if verdict is SearchVerdict.BUDGET_EXHAUSTED:
        findings.append(_Finding(ReasonCode.SEARCH_BUDGET_EXHAUSTED, True))
    elif verdict is SearchVerdict.CONFLICT:
        # Same experiment, changed evaluation evidence -> fail closed.
        findings.append(_Finding(ReasonCode.PROVENANCE_MISSING, True))
    elif verdict is SearchVerdict.LOCK_VIOLATION or verdict is SearchVerdict.DEFER:
        findings.append(_Finding(ReasonCode.SEARCH_FAMILY_UNKNOWN, True))
    else:  # pragma: no cover - the frozen enum has no other member
        findings.append(_Finding(ReasonCode.SEARCH_FAMILY_UNKNOWN, True))


def _validate_holdout_governance(
    findings: list[_Finding],
    *,
    record: EvaluationRecord,
    policy: DecisionPolicy,
    holdout_evidence: HoldoutGovernanceEvidence | None,
    snapshot: RegistrySnapshot,
) -> None:
    if policy.require_holdout and holdout_evidence is None:
        # A required cross-experiment holdout verdict is unavailable.
        findings.append(_Finding(ReasonCode.INSUFFICIENT_EVIDENCE, True))
    elif policy.require_holdout and holdout_evidence.holdout_id is None:
        findings.append(_Finding(ReasonCode.INSUFFICIENT_EVIDENCE, True))

    if holdout_evidence is None:
        return

    if holdout_evidence.prior_consumed_by is not None:
        registered = False
        for entry in snapshot.experiments:
            if entry.experiment_id == holdout_evidence.prior_consumed_by:
                registered = True
                break
        if not registered:
            # The cited prior consumption is not registered evidence.
            findings.append(_Finding(ReasonCode.PROVENANCE_MISSING, True))

    if (
        holdout_evidence.prior_consumption
        is HoldoutConsumptionResult.PREVIOUSLY_CONSUMED
    ):
        # Exact persistent-holdout reuse -> fail closed per the frozen policy.
        findings.append(_Finding(ReasonCode.HOLDOUT_PREVIOUSLY_CONSUMED, False))

    if policy.require_holdout and not record.holdout_consumed:
        # The final holdout fold exists in the design but was not consumed.
        findings.append(_Finding(ReasonCode.POLICY_UNSATISFIED, False))


# ---------------------------------------------------------------------------
# Mandatory requirement evaluation
# ---------------------------------------------------------------------------


def _evaluate_required_evidence(
    findings: list[_Finding],
    *,
    record: EvaluationRecord,
    policy: DecisionPolicy,
) -> None:
    for section in policy.required_evidence:
        if not _evidence_present(record, section):
            findings.append(_Finding(ReasonCode.INSUFFICIENT_EVIDENCE, True))


def _evaluate_minimum_n_obs(
    findings: list[_Finding],
    *,
    record: EvaluationRecord,
    policy: DecisionPolicy,
) -> None:
    if policy.minimum_n_obs is None:
        return
    observed = _observed_sample_size(record)
    if observed is None:
        findings.append(_Finding(ReasonCode.INSUFFICIENT_EVIDENCE, True))
    elif observed < policy.minimum_n_obs:
        findings.append(_Finding(ReasonCode.POLICY_UNSATISFIED, False))


def _evaluate_partitions(
    findings: list[_Finding],
    *,
    record: EvaluationRecord,
    policy: DecisionPolicy,
) -> None:
    if policy.require_is_oos and not _has_is_and_oos(record):
        findings.append(_Finding(ReasonCode.POLICY_UNSATISFIED, False))


def _evaluate_redundancy(
    findings: list[_Finding],
    *,
    record: EvaluationRecord,
    policy: DecisionPolicy,
) -> None:
    if policy.redundancy_threshold is None:
        # Optional criterion unset: redundancy is recorded, never a verdict.
        return
    if not record.redundancy_measurements:
        findings.append(_Finding(ReasonCode.INSUFFICIENT_EVIDENCE, True))
        return
    largest, all_defined = _max_absolute_redundancy(record)
    if not all_defined:
        findings.append(_Finding(ReasonCode.INSUFFICIENT_EVIDENCE, True))
    elif largest is not None and largest > policy.redundancy_threshold:
        findings.append(_Finding(ReasonCode.REDUNDANCY_EXCEEDS_THRESHOLD, False))


# ---------------------------------------------------------------------------
# The judge
# ---------------------------------------------------------------------------


def judge_experiment(
    *,
    experiment_id: str,
    hypothesis_id: str,
    record: EvaluationRecord,
    policy: DecisionPolicy,
    search_policy: SearchPolicy,
    registry_snapshot: RegistrySnapshot,
    search_decision: SearchGovernanceDecision | None = None,
    holdout_evidence: HoldoutGovernanceEvidence | None = None,
    human_explanation: str | None = None,
) -> DecisionRecord:
    """Adjudicate one frozen candidate and return a replayable ``DecisionRecord``.

    The result is a pure deterministic function of the frozen inputs: the same
    ``EvaluationRecord``, ``DecisionPolicy``, ``SearchPolicy``, registry
    snapshot and governance verdicts always produce the same semantic
    ``DecisionRecord`` (``human_explanation`` is excluded from the semantic
    content hash).

    Provenance is validated fail-closed: every supplied identity/hash must
    agree with the frozen evidence. A mismatch is reported as a ``DEFER``
    ``DecisionRecord`` (it never raises, and it never mutates its inputs).

    Raises :class:`JudgeInputError` only for malformed inputs from which no
    valid ``DecisionRecord`` could be built.
    """
    _require_sha256(experiment_id, field_name="experiment_id")
    _require_sha256(hypothesis_id, field_name="hypothesis_id")
    _require_type(record, EvaluationRecord, field_name="record")
    _require_type(policy, DecisionPolicy, field_name="policy")
    _require_type(search_policy, SearchPolicy, field_name="search_policy")
    _require_type(registry_snapshot, RegistrySnapshot, field_name="registry_snapshot")
    _require_optional_type(
        search_decision,
        SearchGovernanceDecision,
        field_name="search_decision",
    )
    _require_optional_type(
        holdout_evidence,
        HoldoutGovernanceEvidence,
        field_name="holdout_evidence",
    )
    if human_explanation is not None and not isinstance(human_explanation, str):
        raise JudgeInputError("human_explanation must be a string or None")

    findings: list[_Finding] = []

    # (1) Provenance: every supplied identity/hash must agree.
    _validate_registry(
        findings,
        experiment_id=experiment_id,
        hypothesis_id=hypothesis_id,
        record=record,
        search_policy=search_policy,
        snapshot=registry_snapshot,
    )
    if policy.required_search_policy != search_policy.content_hash:
        # The DecisionPolicy references a different SearchPolicy identity.
        findings.append(_Finding(ReasonCode.PROVENANCE_MISSING, True))

    # (2) Search governance (P8-C authoritative; consume the verdict).
    _validate_search_governance(
        findings,
        experiment_id=experiment_id,
        record=record,
        search_policy=search_policy,
        search_decision=search_decision,
    )

    # (3) Holdout governance (P8-B authoritative; consume the evidence).
    _validate_holdout_governance(
        findings,
        record=record,
        policy=policy,
        holdout_evidence=holdout_evidence,
        snapshot=registry_snapshot,
    )

    # (4) Mandatory frozen acceptance requirements.
    _evaluate_required_evidence(findings, record=record, policy=policy)
    _evaluate_minimum_n_obs(findings, record=record, policy=policy)
    _evaluate_partitions(findings, record=record, policy=policy)
    _evaluate_redundancy(findings, record=record, policy=policy)

    # (5) Resolve outcomes fail-closed and combine (DEFER > REJECT > ACCEPT).
    resolved = [
        _resolve_reason_outcome(
            finding, policy=policy, search_policy=search_policy
        )
        for finding in findings
    ]
    decision = _combine_outcomes(resolved, policy)
    if decision is DecisionOutcome.ACCEPT and (
        DecisionOutcome.ACCEPT not in policy.allowed_outcomes
    ):
        # The frozen policy does not permit ACCEPT.
        decision = policy.fail_closed
        findings.append(_Finding(ReasonCode.POLICY_UNSATISFIED, True))

    search_governance = (
        SearchGovernanceEvidence()
        if search_decision is None
        else search_decision.to_search_governance_evidence()
    )
    holdout_governance = (
        HoldoutGovernanceEvidence()
        if holdout_evidence is None
        else holdout_evidence
    )

    return DecisionRecord(
        experiment_id=experiment_id,
        hypothesis_id=hypothesis_id,
        evaluation_record_hash=record.content_hash,
        decision_policy_hash=policy.content_hash,
        search_policy_hash=search_policy.content_hash,
        registry_snapshot_hash=registry_snapshot.snapshot_hash,
        search_governance=search_governance,
        holdout_governance=holdout_governance,
        decision=decision,
        reason_codes=tuple(finding.reason for finding in findings),
        judge_version=JUDGE_VERSION,
        human_explanation=human_explanation,
    )
