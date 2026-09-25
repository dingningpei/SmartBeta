"""Tests for the Phase-10 P10-G assessment and Holm step-down.

Coverage follows the frozen P10-G contract
(``worker_tasks/phase10/phase10-plan.md`` sections 10-11 and the P10-G row of
section 16):

* the Holm step-down against an independent naive reference, including ties,
  adjusted p-values, the frozen ``m`` and ``HolmFamilyMismatchError`` on an
  extra/missing member;
* the inadmissible-member ``p := 1`` rule and the R-3 FWER-preserving
  behaviour;
* the full section 11.2 four-row truth table, ``EFFECT_BELOW_SESOI`` never
  mapping to ``NOT_SUPPORTED``, ``economic_state``, the G3
  ``DECLARATION_DEPENDENT`` flag, the series-identical annotation and
  ``not_supported_scope``;
* the downgrade-only section 11.3 reassessment, including the new DERIVED
  record and the never-upgrade property.

The tests are deterministic and offline: no network, provider, PIT, model or
holdout access.  Evidence roles are computed by the real P10-D rule engine
over synthetic in-memory Knowledge-PIT chains (exactly as P10-D's own tests
do); inference results are deterministic fixtures, not statistical
procedures.  No statistical procedure is certified here.
"""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import date
from typing import Any

import pytest

import phase10_fixtures as fixtures
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.science import assessment as A
from smart_beta.science import knowledge as K
from smart_beta.science import preregistration as P
from smart_beta.science.contracts import (
    NOT_SUPPORTED_SCOPE,
    PRODUCTION_READINESS,
    PROTOCOL_VERSION,
    AssessmentState,
    Channel,
    Direction,
    EffectSizeQualification,
    EstimandKind,
    EvidenceGrade,
    EvidenceRole,
    InformationalFlag,
    MissingnessPolicy,
    ObservationKind,
    PValueType,
    ReasonCode,
    RecordKind,
    content_hash,
)
from smart_beta.science.inference import (
    InferenceResult,
    InferenceStatus,
)

# ---------------------------------------------------------------------------
# deterministic synthetic context
# ---------------------------------------------------------------------------

CLOCK = "2020-01-01T00:00:00Z"
PREREG_DATE = date(2020, 1, 1)
PROGRAM = "prog-1"
HYP = "H-1"
SUBJECT = "SEC:CN:000001"

SESSIONS: tuple[str, ...] = fixtures.synthetic_calendar("2020-01-06", 10)
DAY0 = SESSIONS[0]
DAY5 = SESSIONS[5]
CALENDAR = TradingCalendar([date.fromisoformat(day) for day in SESSIONS])

CONFIRMATION_WINDOW = ("2020-03-01", "2020-03-05")
HOLDOUT = ("2020-02-01", "2020-12-31")

_CONSTRUCTION = {
    "n_groups": 5,
    "cost_bps": 10.0,
    "winsorization": 0.01,
    "factor_missing_policy": "PROPAGATE",
}


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _fp(
    *,
    subject: str = SUBJECT,
    start: str = DAY5,
    end: str | None = None,
    kind: ObservationKind = ObservationKind.PRICE_CHANGE,
) -> dict[str, Any]:
    end = start if end is None else end
    return fixtures.synthetic_footprint_body(
        blocks=[fixtures.synthetic_block(kind, [subject], [[start, end]])]
    )


def _partition_spec() -> dict[str, Any]:
    return {
        "folds": [
            {
                "fold_key": "is",
                "role": "is",
                "index": 0,
                "start": "2019-01-01",
                "end": "2020-01-31",
            },
            {
                "fold_key": "holdout",
                "role": "holdout",
                "index": 1,
                "start": HOLDOUT[0],
                "end": HOLDOUT[1],
            },
        ],
        "holdout_key": "b" * 64,
    }


def _dataset_contract(footprint: dict[str, Any]) -> dict[str, str]:
    return {
        "security_map_hash": footprint["security_map_hash"],
        "market_series_map_hash": footprint["market_series_map_hash"],
        "variable_map_hash": footprint["variable_map_hash"],
        "calendar_hash": footprint["calendar_hash"],
        "derivation_rules_version": footprint["derivation_rules_version"],
    }


def _confirmation(
    *,
    window: tuple[str, str] = CONFIRMATION_WINDOW,
    footprint: dict[str, Any] | None = None,
) -> P.ConfirmationDesign:
    footprint = footprint if footprint is not None else _fp()
    return P.ConfirmationDesign(
        window=window,
        realization_bound_days=5,
        subjects=(SUBJECT,),
        observation_kinds=(ObservationKind.PRICE_CHANGE,),
        declared_footprint=footprint,
        partition_spec=_partition_spec(),
        dataset_contract=_dataset_contract(footprint),
    )


def _member_contract(
    hypothesis_id: str,
    *,
    freeze_record: str | None = None,
    estimand_kind: EstimandKind = EstimandKind.MEAN_NET_LONG_SHORT,
    sesoi: float = 0.05,
    direction: Direction = Direction.POSITIVE,
    bound_alpha: float = 0.05,
) -> P.MemberContract:
    return P.MemberContract(
        hypothesis_id=hypothesis_id,
        hypothesis_freeze_record=freeze_record or _sha(f"freeze-{hypothesis_id}"),
        factor_spec_hash=_sha(f"factor-{hypothesis_id}"),
        estimand_kind=estimand_kind,
        horizon=5,
        construction=dict(_CONSTRUCTION),
        direction=direction,
        sesoi=sesoi,
        estimator_id=P.ESTIMATOR_ID,
        procedure_ref={
            "procedure_id": "fixture-procedure",
            "version": "1.0.0",
            "contract_hash": _sha("contract"),
        },
        admission_record_hash=_sha(f"admission-{hypothesis_id}"),
        params={},
        dependence_justification_hash=_sha(f"dependence-{hypothesis_id}"),
        missingness_policy=MissingnessPolicy.COMPLETE_REQUIRED,
        bound_alpha=bound_alpha,
    )


def _prereg(
    members: tuple[P.MemberContract, ...],
    *,
    alpha_study: float = 0.05,
    estimand_policy_record: str | None = None,
    confirmation: P.ConfirmationDesign | None = None,
) -> P.PreRegistration:
    members = tuple(members)
    return P.PreRegistration(
        estimand_policy_record=estimand_policy_record or _sha("policy"),
        members=members,
        alpha_study=alpha_study,
        confirmation=confirmation if confirmation is not None else _confirmation(),
        power_disclosure={
            member.hypothesis_id: {"unavailable_reason": "not computed in v1"}
            for member in members
        },
    )


# ---------------------------------------------------------------------------
# in-memory, P10-B-valid chain builder (mirrors P10-D's tests)
# ---------------------------------------------------------------------------


class _Chain:
    """A deterministic in-memory Knowledge-PIT chain."""

    def __init__(self) -> None:
        self.records: list[K.KnowledgeRecord] = []

    @property
    def snapshot(self) -> dict[str, Any]:
        head = self.records[-1].record_hash if self.records else K.GENESIS_PREV_HASH
        return {"length": len(self.records), "head_hash": head}

    def append(self, **kwargs: Any) -> K.KnowledgeRecord:
        seq = len(self.records)
        prev_hash = (
            self.records[-1].record_hash if self.records else K.GENESIS_PREV_HASH
        )
        record = K.KnowledgeRecord.build(
            seq=seq, prev_hash=prev_hash, recorded_at=CLOCK, **kwargs
        )
        self._check_refs(record)
        self.records.append(record)
        return record

    def read(self) -> tuple[K.KnowledgeRecord, ...]:
        return tuple(self.records)

    def _check_refs(self, record: K.KnowledgeRecord) -> None:
        known = {existing.record_hash for existing in self.records}
        for name in K.REF_NAMES:
            for reference in record.refs[name]:
                assert reference in known, (name, reference)


def _decision(chain: _Chain) -> K.KnowledgeRecord:
    return chain.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        program_id=PROGRAM,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "operator",
            "consulted_all_prior": True,
        },
    )


def _freeze(chain: _Chain, hypothesis_id: str, parents: list[str]) -> K.KnowledgeRecord:
    return chain.append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        program_id=PROGRAM,
        payload={
            "hypothesis_id": hypothesis_id,
            "factor_spec_hash": _sha(f"factor-{hypothesis_id}"),
        },
        refs={"influenced_by": list(parents)},
    )


def _artifact(
    chain: _Chain,
    *,
    footprint: dict[str, Any] | None = None,
    sealed: bool = True,
    available_from: str = DAY5,
    packaging: str = "bytes-1",
) -> K.KnowledgeRecord:
    return chain.append(
        kind=RecordKind.ARTIFACT,
        program_id=PROGRAM,
        payload={
            "packaging_hash": _sha(packaging),
            "sealed": sealed,
            "available_from": available_from,
            "source_label": "synthetic-artifact",
        },
        footprint=footprint if footprint is not None else _fp(),
    )


def _append_prereg(
    chain: _Chain,
    prereg: P.PreRegistration,
    freezes: list[K.KnowledgeRecord],
) -> K.KnowledgeRecord:
    return chain.append(
        kind=RecordKind.PREREGISTRATION,
        program_id=PROGRAM,
        payload={
            "preregistration": prereg.to_content(),
            "preregistration_hash": prereg.prereg_id,
            "consulted_all_prior": True,
        },
        refs={"influenced_by": tuple(record.record_hash for record in freezes)},
    )


def _add_declaration(
    chain: _Chain,
    *,
    channel: Channel,
    footprint: dict[str, Any],
    exposed: bool,
    hypothesis_ids: tuple[str, ...] = (HYP,),
    exposure_event_date: str = "2019-01-01",
    extras: dict[str, Any] | None = None,
) -> K.KnowledgeRecord:
    basis = _sha(f"basis-{chain.snapshot['length']}")
    if exposed:
        payload = fixtures.exposure_declaration(
            channel=channel,
            footprint=footprint,
            exposure_event_date=exposure_event_date,
            basis_hash=basis,
            knowledge_snapshot_ref=chain.snapshot,
            program_ids=(PROGRAM,),
            hypothesis_ids=hypothesis_ids,
        )
    else:
        payload = fixtures.not_exposed_declaration(
            channel=channel,
            footprint=footprint,
            basis_hash=basis,
            knowledge_snapshot_ref=chain.snapshot,
            program_ids=(PROGRAM,),
            hypothesis_ids=hypothesis_ids,
        )
    if extras:
        payload.update(extras)
    return chain.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=channel,
        footprint=footprint,
        payload=payload,
    )


def _g1_study(
    hypothesis_ids: tuple[str, ...] = (HYP,),
    *,
    estimand_kind: EstimandKind = EstimandKind.MEAN_NET_LONG_SHORT,
    sesoi: float = 0.05,
    artifact: K.KnowledgeRecord | None = None,
) -> dict[str, Any]:
    """A prospective G1 study: E is recorded after ``tau_P`` with post-``tau_P`` data."""
    chain = _Chain()
    decision = _decision(chain)
    freezes = [_freeze(chain, hid, [decision.record_hash]) for hid in hypothesis_ids]
    members = tuple(
        _member_contract(
            hid,
            freeze_record=freeze.record_hash,
            estimand_kind=estimand_kind,
            sesoi=sesoi,
        )
        for hid, freeze in zip(hypothesis_ids, freezes)
    )
    prereg = _prereg(
        members,
        estimand_policy_record=decision.record_hash,
    )
    prereg_record = _append_prereg(chain, prereg, freezes)
    artifact_record = artifact if artifact is not None else _artifact(chain)
    return {
        "chain": chain,
        "prereg": prereg,
        "prereg_record": prereg_record,
        "artifact": artifact_record,
        "freezes": freezes,
    }


def _assess(
    scenario: dict[str, Any],
    inputs: list[A.MemberAssessmentInput],
    *,
    study_id: str = "study-1",
) -> A.AssessmentFamily:
    return A.assess(
        scenario["prereg"],
        inputs,
        study_id=study_id,
        knowledge=scenario["chain"].records,
        prereg_record_hash=scenario["prereg_record"].record_hash,
        calendar=CALENDAR,
    )


def _input(
    hypothesis_id: str,
    artifact_record_hash: str,
    *,
    inference: InferenceResult | None = None,
    **kwargs: Any,
) -> A.MemberAssessmentInput:
    return A.MemberAssessmentInput(
        hypothesis_id=hypothesis_id,
        artifact_record_hash=artifact_record_hash,
        inference=inference,
        **kwargs,
    )


def _valid_result(
    *,
    p: float,
    ub: float,
    estimate: float = 0.10,
    bound_alpha: float = 0.05,
) -> InferenceResult:
    return InferenceResult(
        status=InferenceStatus.VALID,
        reason=None,
        procedure_ref={
            "procedure_id": "fixture-procedure",
            "version": "1.0.0",
            "contract_hash": _sha("contract"),
        },
        implementation_source_sha256=_sha("source"),
        admission_record_hash=_sha("admission"),
        params={},
        missingness_policy=MissingnessPolicy.COMPLETE_REQUIRED,
        n=10,
        estimate_theta_prime=estimate,
        se=0.05,
        statistic=2.0,
        p_one_sided=p,
        p_value_semantics_type=PValueType.ASYMPTOTIC,
        bound_alpha=bound_alpha,
        upper_bound_theta_prime=ub,
        input_series_hash=_sha("series"),
    )


def _invalid_result(reason: ReasonCode = ReasonCode.SERIES_MISSING) -> InferenceResult:
    return InferenceResult(
        status=InferenceStatus.INVALID,
        reason=reason,
        procedure_ref={
            "procedure_id": "fixture-procedure",
            "version": "1.0.0",
            "contract_hash": _sha("contract"),
        },
        implementation_source_sha256=_sha("source"),
        admission_record_hash=_sha("admission"),
        params={},
        missingness_policy=MissingnessPolicy.COMPLETE_REQUIRED,
        n=None,
        estimate_theta_prime=None,
        se=None,
        statistic=None,
        p_one_sided=None,
        p_value_semantics_type=None,
        bound_alpha=0.05,
        upper_bound_theta_prime=None,
        input_series_hash=_sha("series"),
    )


def _naive_holm_rejections(p_values: dict[str, float], alpha: float) -> set[str]:
    """An independent step-down reference (threshold form, section 10)."""
    ordered = sorted(p_values, key=lambda hid: (p_values[hid], hid))
    m = len(ordered)
    rejected: set[str] = set()
    for position, hid in enumerate(ordered, start=1):
        if p_values[hid] <= alpha / (m - position + 1):
            rejected.add(hid)
        else:
            break
    return rejected


def _naive_adjusted(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(p_values, key=lambda hid: (p_values[hid], hid))
    m = len(ordered)
    running = 0.0
    adjusted: dict[str, float] = {}
    for position, hid in enumerate(ordered, start=1):
        running = max(running, min(1.0, (m - position + 1) * p_values[hid]))
        adjusted[hid] = running
    return adjusted


# ===========================================================================
# Holm (section 10)
# ===========================================================================


def test_holm_matches_naive_reference_with_ties():
    members = tuple(_member_contract(hid) for hid in ("H-1", "H-2", "H-3", "H-4"))
    prereg = _prereg(members)
    p_values = {"H-1": 0.01, "H-2": 0.01, "H-3": 0.04, "H-4": 0.9}

    result = A.compute_holm(prereg, p_values)

    assert result.m == 4
    assert result.family_id == prereg.prereg_id
    assert result.alpha_study == 0.05
    rejected = {m.hypothesis_id for m in result.members if m.holm_rejected}
    assert rejected == _naive_holm_rejections(p_values, 0.05)
    adjusted = {m.hypothesis_id: m.holm_adjusted_p for m in result.members}
    assert adjusted == pytest.approx(_naive_adjusted(p_values))


def test_holm_adjusted_p_values_are_monotone_and_capped():
    members = tuple(_member_contract(hid) for hid in ("H-1", "H-2", "H-3"))
    prereg = _prereg(members)
    p_values = {"H-1": 0.02, "H-2": 0.02, "H-3": 0.02}

    result = A.compute_holm(prereg, p_values)
    by_rank = sorted(result.members, key=lambda m: m.holm_rank)

    assert [m.holm_rank for m in by_rank] == [1, 2, 3]
    adjusted = [m.holm_adjusted_p for m in by_rank]
    assert adjusted == sorted(adjusted)
    assert all(value <= 1.0 for value in adjusted)
    # m=3, p=0.02: 3*.02=.06 -> .06, 2*.02=.04 -> .06, 1*.02=.02 -> .06
    assert adjusted == pytest.approx([0.06, 0.06, 0.06])


def test_holm_extra_member_raises_and_produces_no_assessment():
    members = tuple(_member_contract(hid) for hid in ("H-1", "H-2"))
    prereg = _prereg(members)

    with pytest.raises(A.HolmFamilyMismatchError):
        A.compute_holm(prereg, {"H-1": 0.01, "H-2": 0.02, "H-3": 0.03})
    with pytest.raises(A.HolmFamilyMismatchError):
        A.compute_holm(prereg, {"H-1": 0.01})


def test_assess_extra_or_missing_member_raises():
    scenario = _g1_study(("H-1", "H-2"))
    artifact_hash = scenario["artifact"].record_hash
    good = [
        _input("H-1", artifact_hash, inference=_valid_result(p=0.01, ub=0.1)),
        _input("H-2", artifact_hash, inference=_valid_result(p=0.02, ub=0.1)),
    ]
    with pytest.raises(A.HolmFamilyMismatchError):
        _assess(scenario, good + [_input("H-3", artifact_hash)])
    with pytest.raises(A.HolmFamilyMismatchError):
        _assess(scenario, [good[0]])
    # duplicate member input
    with pytest.raises(A.HolmFamilyMismatchError):
        _assess(scenario, [good[0], good[0]])


def test_holm_inadmissible_member_uses_p_one_and_never_rejects():
    members = tuple(_member_contract(hid) for hid in ("H-1", "H-2"))
    prereg = _prereg(members)

    result = A.compute_holm(
        prereg,
        {"H-1": 0.01, "H-2": None},
        admissible={"H-1": True, "H-2": False},
    )
    bad = result.for_hypothesis("H-2")
    good = result.for_hypothesis("H-1")

    assert bad.admissible is False
    assert bad.effective_p == 1.0
    assert bad.p_value is None
    assert bad.holm_rejected is False
    # m stays the frozen size (2), so H-1's adjusted p is 2 * 0.01 = 0.02.
    assert good.holm_adjusted_p == pytest.approx(0.02)
    assert good.holm_rejected is True
    assert result.m == 2


def test_holm_rejections_are_a_prefix_and_rejected_iff_adjusted_within_alpha():
    members = tuple(_member_contract(hid) for hid in ("H-1", "H-2", "H-3"))
    prereg = _prereg(members)
    p_values = {"H-1": 0.01, "H-2": 0.03, "H-3": 0.20}

    result = A.compute_holm(prereg, p_values)
    by_rank = sorted(result.members, key=lambda m: m.holm_rank)
    rejected_by_rank = [m.holm_rejected for m in by_rank]
    # The rejection set is a prefix of the (p, hypothesis_id) order.
    assert rejected_by_rank == sorted(rejected_by_rank, reverse=True)
    for member in result.members:
        assert member.holm_rejected is (
            member.holm_adjusted_p <= prereg.alpha_study
        )


# ===========================================================================
# the four-row truth table (section 11.2)
# ===========================================================================


@pytest.mark.parametrize(
    "p_value, upper_bound, expected_state, expected_qualification",
    [
        (0.01, 0.20, AssessmentState.SUPPORTED, EffectSizeQualification.SESOI_NOT_EXCLUDED),
        (0.01, 0.02, AssessmentState.SUPPORTED, EffectSizeQualification.EFFECT_BELOW_SESOI),
        (0.50, 0.02, AssessmentState.NOT_SUPPORTED, EffectSizeQualification.NOT_APPLICABLE),
        (0.50, 0.20, AssessmentState.INCONCLUSIVE, EffectSizeQualification.NOT_APPLICABLE),
    ],
)
def test_four_row_truth_table(p_value, upper_bound, expected_state, expected_qualification):
    scenario = _g1_study((HYP,), sesoi=0.05)
    family = _assess(
        scenario,
        [
            _input(
                HYP,
                scenario["artifact"].record_hash,
                inference=_valid_result(p=p_value, ub=upper_bound),
            )
        ],
    )
    assessment = family.for_hypothesis(HYP)

    assert assessment.state is expected_state
    assert assessment.effect_size_qualification is expected_qualification
    assert assessment.primary_null_rejected is not None
    assert assessment.sesoi_excluded_by_upper_bound is not None
    # SUPPORTED without a Holm-adjusted rejection is impossible (section 18).
    if expected_state is AssessmentState.SUPPORTED:
        assert assessment.primary_null_rejected is True
        assert assessment.multiplicity["holm_rejected"] is True


def test_assess_invalid_member_uses_p_one_in_the_step_down():
    scenario = _g1_study(("H-1", "H-2"))
    artifact_hash = scenario["artifact"].record_hash
    family = _assess(
        scenario,
        [
            _input("H-1", artifact_hash, inference=_valid_result(p=0.01, ub=0.2)),
            _input("H-2", artifact_hash, inference=_invalid_result()),
        ],
    )
    valid = family.for_hypothesis("H-1")
    invalid = family.for_hypothesis("H-2")

    assert invalid.state is AssessmentState.NOT_ASSESSED
    assert valid.state is AssessmentState.SUPPORTED
    assert family.holm.m == 2
    # H-1 must clear the m=2 rank-1 threshold (alpha/2 = 0.025) via the
    # adjusted p-value 2 * 0.01 = 0.02; the invalid member enters with p := 1.
    assert family.holm.for_hypothesis("H-1").holm_adjusted_p == pytest.approx(0.02)
    assert family.holm.for_hypothesis("H-2").effective_p == 1.0
    assert valid.multiplicity["holm_rejected"] is True


def test_effect_below_sesoi_is_supported_not_not_supported():
    scenario = _g1_study((HYP,), sesoi=0.05)
    family = _assess(
        scenario,
        [
            _input(
                HYP,
                scenario["artifact"].record_hash,
                inference=_valid_result(p=0.001, ub=0.001),
            )
        ],
    )
    assessment = family.for_hypothesis(HYP)
    assert assessment.state is AssessmentState.SUPPORTED
    assert assessment.effect_size_qualification is (
        EffectSizeQualification.EFFECT_BELOW_SESOI
    )
    assert assessment.state is not AssessmentState.NOT_SUPPORTED


def test_not_supported_is_hypothesis_local_and_carries_no_family_wise_claim():
    scenario = _g1_study((HYP,), sesoi=0.05)
    family = _assess(
        scenario,
        [
            _input(
                HYP,
                scenario["artifact"].record_hash,
                inference=_valid_result(p=0.9, ub=0.01),
            )
        ],
    )
    assessment = family.for_hypothesis(HYP)
    assert assessment.state is AssessmentState.NOT_SUPPORTED
    assert assessment.not_supported_scope == NOT_SUPPORTED_SCOPE
    # The recorded Holm result does not claim a rejection for the member.
    assert assessment.multiplicity["holm_rejected"] is False


def test_not_assessed_member_has_null_booleans_and_not_applicable():
    scenario = _g1_study((HYP,))
    family = _assess(
        scenario,
        [
            _input(
                HYP,
                scenario["artifact"].record_hash,
                inference=_invalid_result(ReasonCode.SERIES_MISSING),
            )
        ],
    )
    assessment = family.for_hypothesis(HYP)
    assert assessment.state is AssessmentState.NOT_ASSESSED
    assert assessment.primary_null_rejected is None
    assert assessment.sesoi_excluded_by_upper_bound is None
    assert assessment.effect_size_qualification is (
        EffectSizeQualification.NOT_APPLICABLE
    )
    assert ReasonCode.SERIES_MISSING in assessment.reason_codes
    assert assessment.multiplicity["holm_rejected"] is False


def test_economic_state_follows_the_estimand_kind():
    net = _g1_study((HYP,), estimand_kind=EstimandKind.MEAN_NET_LONG_SHORT)
    net_assessment = _assess(
        net,
        [
            _input(
                HYP,
                net["artifact"].record_hash,
                inference=_valid_result(p=0.01, ub=0.2),
            )
        ],
    ).for_hypothesis(HYP)
    assert net_assessment.state is AssessmentState.SUPPORTED
    assert net_assessment.economic_state is AssessmentState.SUPPORTED

    ic = _g1_study((HYP,), estimand_kind=EstimandKind.MEAN_RANK_IC)
    ic_assessment = _assess(
        ic,
        [
            _input(
                HYP,
                ic["artifact"].record_hash,
                inference=_valid_result(p=0.01, ub=0.2),
            )
        ],
    ).for_hypothesis(HYP)
    assert ic_assessment.state is AssessmentState.SUPPORTED
    assert ic_assessment.economic_state is AssessmentState.NOT_ASSESSED


def test_g3_assessment_carries_declaration_dependent_flag():
    # A historical study with covering HUMAN NOT_EXPOSED + PUBLIC declarations
    # and a public class-match: the frozen rule order yields G3.
    chain = _Chain()
    decision = _decision(chain)
    freeze = _freeze(chain, HYP, [decision.record_hash])
    footprint = _fp(start=DAY0, end=DAY0)
    artifact = _artifact(chain, footprint=footprint, available_from=DAY0)
    _add_declaration(chain, channel=Channel.HUMAN, footprint=footprint, exposed=False)
    _add_declaration(
        chain,
        channel=Channel.PUBLIC,
        footprint=footprint,
        exposed=True,
        extras={"reference": "public-record", "class_match": True},
    )
    member = _member_contract(HYP, freeze_record=freeze.record_hash)
    prereg = _prereg((member,), estimand_policy_record=decision.record_hash)
    prereg_record = _append_prereg(chain, prereg, [freeze])

    family = A.assess(
        prereg,
        [_input(HYP, artifact.record_hash, inference=_valid_result(p=0.01, ub=0.2))],
        study_id="study-1",
        knowledge=chain.records,
        prereg_record_hash=prereg_record.record_hash,
        calendar=CALENDAR,
    )
    assessment = family.for_hypothesis(HYP)
    assert assessment.evidence_role is EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED
    assert assessment.evidence_grade is EvidenceGrade.G3
    assert InformationalFlag.DECLARATION_DEPENDENT in assessment.flags


def test_series_identical_annotation_is_an_informational_flag():
    scenario = _g1_study(("H-1", "H-2"))
    artifact_hash = scenario["artifact"].record_hash
    family = _assess(
        scenario,
        [
            _input(
                "H-1",
                artifact_hash,
                inference=_valid_result(p=0.01, ub=0.2),
                series_identity=_sha("same-series"),
            ),
            _input(
                "H-2",
                artifact_hash,
                inference=_valid_result(p=0.02, ub=0.2),
                series_identity=_sha("same-series"),
            ),
        ],
    )
    for hid in ("H-1", "H-2"):
        assessment = family.for_hypothesis(hid)
        assert assessment.series_identical_group == ("H-1", "H-2")
        assert InformationalFlag.SERIES_IDENTICAL_GROUP in assessment.flags


def test_governance_missing_and_invalid_are_not_assessed():
    scenario = _g1_study((HYP,))
    artifact_hash = scenario["artifact"].record_hash

    missing = _assess(
        scenario,
        [
            _input(
                HYP,
                artifact_hash,
                inference=_valid_result(p=0.01, ub=0.2),
                governance_validity=A.GovernanceValidity.MISSING,
            )
        ],
    ).for_hypothesis(HYP)
    assert missing.state is AssessmentState.NOT_ASSESSED
    assert ReasonCode.GOVERNANCE_PROVENANCE_MISSING in missing.reason_codes

    invalid = _assess(
        scenario,
        [
            _input(
                HYP,
                artifact_hash,
                inference=_valid_result(p=0.01, ub=0.2),
                governance_validity="INVALID",
            )
        ],
    ).for_hypothesis(HYP)
    assert invalid.state is AssessmentState.NOT_ASSESSED
    assert ReasonCode.GOVERNANCE_INVALID in invalid.reason_codes


def test_all_applicable_reasons_are_collected():
    scenario = _g1_study((HYP,))
    family = _assess(
        scenario,
        [
            _input(
                HYP,
                scenario["artifact"].record_hash,
                inference=_valid_result(p=0.01, ub=0.2),
                governance_validity=A.GovernanceValidity.INVALID,
                inadmissibility_reasons=(ReasonCode.FIREWALL_VIOLATION,),
            )
        ],
    )
    assessment = family.for_hypothesis(HYP)
    assert assessment.state is AssessmentState.NOT_ASSESSED
    assert set(assessment.reason_codes) == {
        ReasonCode.GOVERNANCE_INVALID,
        ReasonCode.FIREWALL_VIOLATION,
    }


def test_inadmissible_role_is_not_assessed():
    # An ARTIFACT recorded at the same seq ordering but with a footprint that
    # is not prospective and no declarations -> UNKNOWN_EXPOSURE (G5).
    chain = _Chain()
    decision = _decision(chain)
    freeze = _freeze(chain, HYP, [decision.record_hash])
    footprint = _fp(start=DAY0, end=DAY0)
    artifact = _artifact(chain, footprint=footprint, available_from=DAY0)
    member = _member_contract(HYP, freeze_record=freeze.record_hash)
    prereg = _prereg((member,), estimand_policy_record=decision.record_hash)
    prereg_record = _append_prereg(chain, prereg, [freeze])

    family = A.assess(
        prereg,
        [_input(HYP, artifact.record_hash, inference=_valid_result(p=0.01, ub=0.2))],
        study_id="study-1",
        knowledge=chain.records,
        prereg_record_hash=prereg_record.record_hash,
        calendar=CALENDAR,
    )
    assessment = family.for_hypothesis(HYP)
    assert assessment.evidence_role is EvidenceRole.UNKNOWN_EXPOSURE
    assert assessment.evidence_grade is EvidenceGrade.G5
    assert assessment.state is AssessmentState.NOT_ASSESSED
    assert ReasonCode.ROLE_UNKNOWN_EXPOSURE in assessment.reason_codes


def test_assessment_content_hash_is_stable_and_content_sensitive():
    scenario = _g1_study((HYP,))
    artifact_hash = scenario["artifact"].record_hash
    first = _assess(
        scenario,
        [_input(HYP, artifact_hash, inference=_valid_result(p=0.01, ub=0.2))],
    ).for_hypothesis(HYP)
    second = _assess(
        scenario,
        [_input(HYP, artifact_hash, inference=_valid_result(p=0.01, ub=0.2))],
    ).for_hypothesis(HYP)
    assert first.assessment_id == second.assessment_id
    assert first.to_content()["protocol_version"] == PROTOCOL_VERSION
    assert first.to_content()["production_readiness"] == PRODUCTION_READINESS

    different = _assess(
        scenario,
        [_input(HYP, artifact_hash, inference=_valid_result(p=0.01, ub=0.19))],
    ).for_hypothesis(HYP)
    assert different.assessment_id != first.assessment_id


# ===========================================================================
# family-wide reassessment (section 11.3)
# ===========================================================================


DAY6 = SESSIONS[6]


def _g1_family(
    entries: list[tuple[str, str]],
    *,
    estimand_kind: EstimandKind = EstimandKind.MEAN_NET_LONG_SHORT,
    sesoi: float = 0.05,
) -> dict[str, Any]:
    """A prospective G1 study with one artifact per member.

    ``entries`` is a list of ``(hypothesis_id, artifact_start)`` pairs; each
    artifact is recorded after ``tau_P`` with a post-``tau_P`` footprint, so
    every member is CONFIRMATION_PROSPECTIVE.  Distinct artifact dates let a
    late declaration downgrade one member without touching another.
    """
    chain = _Chain()
    decision = _decision(chain)
    freezes = [_freeze(chain, hid, [decision.record_hash]) for hid, _ in entries]
    members = tuple(
        _member_contract(
            hid,
            freeze_record=freeze.record_hash,
            estimand_kind=estimand_kind,
            sesoi=sesoi,
        )
        for (hid, _), freeze in zip(entries, freezes)
    )
    prereg = _prereg(members, estimand_policy_record=decision.record_hash)
    prereg_record = _append_prereg(chain, prereg, freezes)
    artifacts = {
        hid: _artifact(
            chain,
            footprint=_fp(start=start, end=start),
            available_from=start,
        )
        for hid, start in entries
    }
    return {
        "chain": chain,
        "prereg": prereg,
        "prereg_record": prereg_record,
        "artifacts": artifacts,
        "freezes": freezes,
    }


def _assess_family(
    scenario: dict[str, Any],
    results: dict[str, tuple[float, float]],
) -> A.AssessmentFamily:
    inputs = [
        _input(
            hid,
            scenario["artifacts"][hid].record_hash,
            inference=_valid_result(p=p, ub=ub),
        )
        for hid, (p, ub) in results.items()
    ]
    return A.assess(
        scenario["prereg"],
        inputs,
        study_id="study-1",
        knowledge=scenario["chain"].records,
        prereg_record_hash=scenario["prereg_record"].record_hash,
        calendar=CALENDAR,
    )


def _persist(chain: _Chain, tmp_path) -> K.KnowledgeLog:
    path = tmp_path / "knowledge.jsonl"
    fixtures.write_knowledge_log(path, [record.to_dict() for record in chain.records])
    return K.KnowledgeLog(path)


def _wrap_assessment(
    chain: _Chain,
    assessment: A.ScientificAssessment,
    artifact_record_hash: str,
) -> K.KnowledgeRecord:
    return chain.append(
        kind=RecordKind.DERIVED,
        channel=Channel.SYSTEM,
        program_id=PROGRAM,
        payload={
            "derivation_kind": "confirmation_assessment",
            "content_hash": assessment.assessment_id,
        },
        refs={"derived_from": (artifact_record_hash,)},
        footprint=_fp(),
    )


def _wrap_family(
    scenario: dict[str, Any], family: A.AssessmentFamily
) -> dict[str, K.KnowledgeRecord]:
    return {
        assessment.hypothesis_id: _wrap_assessment(
            scenario["chain"],
            assessment,
            scenario["artifacts"][assessment.hypothesis_id].record_hash,
        )
        for assessment in family.assessments
    }


def test_reassess_downgrade_appends_a_new_derived_record(tmp_path):
    # m=1: a late exposure declaration downgrades the only member.
    scenario = _g1_family([(HYP, DAY5)])
    family = _assess_family(scenario, {HYP: (0.01, 0.2)})
    original = family.for_hypothesis(HYP)
    assert original.state is AssessmentState.SUPPORTED
    wrapped = _wrap_family(scenario, family)

    _add_declaration(
        scenario["chain"],
        channel=Channel.HUMAN,
        footprint=_fp(start=DAY5),
        exposed=True,
        exposure_event_date="2019-01-01",
        hypothesis_ids=(HYP,),
    )
    log = _persist(scenario["chain"], tmp_path)
    before = len(log.read())
    new_family = A.reassess(family, log, calendar=CALENDAR)

    assert new_family is not None
    new = new_family.for_hypothesis(HYP)
    assert new.state is AssessmentState.NOT_ASSESSED
    assert new.evidence_role is EvidenceRole.DEVELOPMENT
    assert new.evidence_grade is EvidenceGrade.G4
    assert ReasonCode.ROLE_DEVELOPMENT in new.reason_codes
    assert new.primary_null_rejected is None
    assert new.sesoi_excluded_by_upper_bound is None
    assert new.assessment_id != original.assessment_id
    records = log.read()
    assert len(records) == before + 1
    appended = records[-1]
    assert appended.kind is RecordKind.DERIVED
    assert appended.payload["content_hash"] == new.assessment_id
    assert appended.refs["derived_from"] == (wrapped[HYP].record_hash,)
    # The original is never mutated.
    assert original.state is AssessmentState.SUPPORTED


def test_reassess_no_change_returns_none(tmp_path):
    scenario = _g1_family([(HYP, DAY5)])
    family = _assess_family(scenario, {HYP: (0.01, 0.2)})
    _wrap_family(scenario, family)
    log = _persist(scenario["chain"], tmp_path)
    before = len(log.read())

    assert A.reassess(family, log, calendar=CALENDAR) is None
    assert len(log.read()) == before


def test_family_wide_reassessment_uses_r3_placeholder_and_downgrades_siblings(tmp_path):
    scenario = _g1_family([("H-1", DAY5), ("H-2", DAY6)])
    family = _assess_family(scenario, {"H-1": (0.01, 0.2), "H-2": (0.03, 0.2)})
    # Both members are Holm-rejected and SUPPORTED.
    assert family.for_hypothesis("H-1").state is AssessmentState.SUPPORTED
    assert family.for_hypothesis("H-2").state is AssessmentState.SUPPORTED
    assert family.holm.for_hypothesis("H-2").holm_rejected is True
    _wrap_family(scenario, family)

    # H-1 becomes inadmissible; H-2 does not.
    _add_declaration(
        scenario["chain"],
        channel=Channel.HUMAN,
        footprint=_fp(start=DAY5),
        exposed=True,
        exposure_event_date="2019-01-01",
        hypothesis_ids=("H-1",),
    )
    log = _persist(scenario["chain"], tmp_path)
    before = len(log.read())
    new_family = A.reassess(family, log, calendar=CALENDAR)

    assert new_family is not None
    # Membership and m are unchanged.
    assert new_family.holm.m == 2
    assert {a.hypothesis_id for a in new_family.assessments} == {"H-1", "H-2"}
    assert new_family.holm.alpha_study == 0.05

    h1 = new_family.for_hypothesis("H-1")
    h2 = new_family.for_hypothesis("H-2")
    assert h1.state is AssessmentState.NOT_ASSESSED
    assert h1.evidence_role is EvidenceRole.DEVELOPMENT
    # H-2 was SUPPORTED (Holm-rejected).  With H-1 entering at the R-3
    # placeholder 1, the frozen m=2 Holm no longer rejects H-2 (adjusted p
    # 2 * 0.03 = 0.06 > 0.05), so H-2 is re-derived as INCONCLUSIVE.
    assert h2.primary_null_rejected is False
    assert h2.state is AssessmentState.INCONCLUSIVE
    assert new_family.holm.for_hypothesis("H-2").holm_adjusted_p > 0.05
    assert new_family.holm.for_hypothesis("H-2").holm_rejected is False
    # The placeholder is an internal multiplicity input, never an empirical
    # p-value: it is not persisted, and H-1 keeps its original empirical p.
    assert new_family.holm.for_hypothesis("H-1").effective_p == 1.0
    assert new_family.holm.for_hypothesis("H-1").p_value is None
    assert h1.inference["p_one_sided"] == 0.01
    assert h1.inference["p_one_sided"] != 1.0
    # Both members materially changed -> exactly two reassessment records.
    records = log.read()
    assert len(records) == before + 2
    appended = [
        record
        for record in records
        if record.payload.get("derivation_kind")
        == "confirmation_assessment_reassessment"
    ]
    assert {record.payload["content_hash"] for record in appended} == {
        h1.assessment_id,
        h2.assessment_id,
    }
    for record in appended:
        # No empirical p-value -- and no placeholder represented as one -- is
        # written into K; the record carries only the content hash.
        assert "p_one_sided" not in record.payload
        assert "p_value" not in record.payload


def test_family_wide_reassessment_unaffected_member_gets_no_record(tmp_path):
    # H-2 has the smallest p and its rank/adjusted p do not change when H-1
    # becomes inadmissible, so only H-1 gets a reassessment record.
    scenario = _g1_family([("H-1", DAY5), ("H-2", DAY6)])
    family = _assess_family(scenario, {"H-1": (0.5, 0.2), "H-2": (0.03, 0.2)})
    h2_before = family.for_hypothesis("H-2")
    assert h2_before.state is AssessmentState.INCONCLUSIVE
    _wrap_family(scenario, family)

    _add_declaration(
        scenario["chain"],
        channel=Channel.HUMAN,
        footprint=_fp(start=DAY5),
        exposed=True,
        exposure_event_date="2019-01-01",
        hypothesis_ids=("H-1",),
    )
    log = _persist(scenario["chain"], tmp_path)
    before = len(log.read())
    new_family = A.reassess(family, log, calendar=CALENDAR)

    assert new_family is not None
    h1 = new_family.for_hypothesis("H-1")
    h2 = new_family.for_hypothesis("H-2")
    assert h1.state is AssessmentState.NOT_ASSESSED
    # The unaffected member is carried forward unchanged and gets no record.
    assert h2.assessment_id == h2_before.assessment_id
    records = log.read()
    assert len(records) == before + 1
    assert records[-1].payload["content_hash"] == h1.assessment_id


def test_family_wide_reassessment_is_idempotent(tmp_path):
    scenario = _g1_family([("H-1", DAY5), ("H-2", DAY6)])
    family = _assess_family(scenario, {"H-1": (0.01, 0.2), "H-2": (0.03, 0.2)})
    _wrap_family(scenario, family)
    _add_declaration(
        scenario["chain"],
        channel=Channel.HUMAN,
        footprint=_fp(start=DAY5),
        exposed=True,
        exposure_event_date="2019-01-01",
        hypothesis_ids=("H-1",),
    )
    log = _persist(scenario["chain"], tmp_path)

    first = A.reassess(family, log, calendar=CALENDAR)
    assert first is not None
    after_first = len(log.read())
    # Re-running against the same K_now appends no further records.
    second = A.reassess(family, log, calendar=CALENDAR)
    assert second is not None
    assert len(log.read()) == after_first
    # Re-running on the already-reassessed family is a no-op.
    assert A.reassess(second, log, calendar=CALENDAR) is None
    assert len(log.read()) == after_first
    # Deterministic: the two recomputations agree on every semantic field
    # (the snapshot/provenance advance with K, but the meaning is stable).
    for hid in ("H-1", "H-2"):
        first_member = first.for_hypothesis(hid)
        second_member = second.for_hypothesis(hid)
        assert first_member.state is second_member.state
        assert first_member.evidence_role is second_member.evidence_role
        assert first_member.evidence_grade is second_member.evidence_grade
        assert (
            first_member.primary_null_rejected
            == second_member.primary_null_rejected
        )
        assert dict(first_member.multiplicity) == dict(
            second_member.multiplicity
        )


def test_reassess_refuses_an_upgrade_and_appends_nothing(tmp_path):
    scenario = _g1_family([(HYP, DAY5)])
    family = _assess_family(scenario, {HYP: (0.01, 0.2)})
    original = family.for_hypothesis(HYP)
    _wrap_family(scenario, family)
    log = _persist(scenario["chain"], tmp_path)
    before = len(log.read())

    # A stored record weaker than the role K_now recomputes would upgrade it.
    weaker = dataclasses.replace(
        original,
        evidence_role=EvidenceRole.DEVELOPMENT,
        evidence_grade=EvidenceGrade.G4,
        state=AssessmentState.NOT_ASSESSED,
        primary_null_rejected=None,
        sesoi_excluded_by_upper_bound=None,
        effect_size_qualification=EffectSizeQualification.NOT_APPLICABLE,
    )
    weaker_family = A.AssessmentFamily(holm=family.holm, assessments=(weaker,))
    with pytest.raises(A.ReassessmentUpgradeError):
        A.reassess(weaker_family, log, calendar=CALENDAR)
    assert len(log.read()) == before


def test_reassess_requires_a_knowledge_log_and_a_family():
    scenario = _g1_family([(HYP, DAY5)])
    family = _assess_family(scenario, {HYP: (0.01, 0.2)})
    with pytest.raises(A.AssessmentContractError):
        A.reassess(family, list(scenario["chain"].records))
    with pytest.raises(A.AssessmentContractError):
        A.reassess(family.for_hypothesis(HYP), scenario["chain"])  # type: ignore[arg-type]


# ===========================================================================
# frozen-family / dependency guards
# ===========================================================================


def test_assess_does_not_accept_a_role_as_input():
    # Section 21 clause 2: the role is derived, never assigned. MemberAssessmentInput
    # must not carry a role field.
    assert "evidence_role" not in {f.name for f in dataclasses.fields(A.MemberAssessmentInput)}


def test_multiplicity_records_the_frozen_family_size():
    scenario = _g1_study(("H-1", "H-2"))
    artifact_hash = scenario["artifact"].record_hash
    family = _assess(
        scenario,
        [
            _input("H-1", artifact_hash, inference=_valid_result(p=0.01, ub=0.2)),
            _input("H-2", artifact_hash, inference=_valid_result(p=0.9, ub=0.2)),
        ],
    )
    assert family.holm.m == 2
    for assessment in family.assessments:
        assert assessment.multiplicity["m"] == 2
        assert assessment.multiplicity["family_id"] == scenario["prereg"].family_id
