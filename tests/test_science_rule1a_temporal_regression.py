"""Phase 10 P10-D-R2 regression: rule-1a temporal amendment end-to-end.

Repairs the frozen rule-1a defect recorded in
``worker_tasks/phase10/phase10-plan.md`` section 5.4 ("Rule 1a temporal
amendment (P10-D-R2)"): the ``CONSUMPTION`` scan in
:func:`smart_beta.science.roles.evidence_role` must consider only records with
``seq(c) < tau_P`` (``tau_P`` = the assessed preregistration's ``seq``). This
file exercises the amendment through the **unchanged** public
:func:`smart_beta.science.assessment.assess` and
:func:`smart_beta.science.assessment.reassess` APIs, with a real
``CONSUMPTION`` record present in ``K``.

Coverage:

* **E** -- assess with the study's own post-``tau_P`` ``CONSUMPTION`` in ``K``
  is not ``ROBUSTNESS``; reassessing against a ``K`` that still holds it does
  not downgrade; reassessing after an *additional* later overlapping
  post-``tau_P`` ``CONSUMPTION`` produces no rule-1a downgrade.
* **F** -- a genuine qualifying pre-``tau_P`` ``CONSUMPTION`` is
  ``ROBUSTNESS`` (and therefore ``NOT_ASSESSED`` with ``ROLE_ROBUSTNESS``),
  and the pre-existing section 11.3 downgrade semantics (a late ``EXPOSED``
  declaration) remain effective.

The roles-level assertions for A-D/F live in
``tests/test_science_roles.py``. Everything here is deterministic and
offline: synthetic in-memory/fixture records only, no provider, network,
PIT-data, model or holdout access. This is an implementation regression, not
a certification of any statistical procedure. The assessment fixes reuse the
frozen P10-G fixtures from ``tests/test_science_assessment.py`` by import
(the only cross-file dependency, and read-only).
"""

from __future__ import annotations

from typing import Any, Mapping

import test_science_assessment as T

from smart_beta.science import assessment as A
from smart_beta.science import knowledge as K
from smart_beta.science.contracts import (
    AssessmentState,
    Channel,
    EvidenceRole,
    ReasonCode,
    RecordKind,
)


# ---------------------------------------------------------------------------
# synthetic builders
# ---------------------------------------------------------------------------


def _append_consumption(
    chain: Any,
    *,
    prereg_record: K.KnowledgeRecord,
    artifact_record: K.KnowledgeRecord,
    footprint: Mapping[str, Any],
    study_id: str = "study-1",
) -> K.KnowledgeRecord:
    """Append one valid ``CONSUMPTION`` to an in-memory P10-G test chain."""
    return chain.append(
        kind=RecordKind.CONSUMPTION,
        program_id=T.PROGRAM,
        payload={
            "study_id": study_id,
            "prereg_record_hash": prereg_record.record_hash,
            "artifact_record_hash": artifact_record.record_hash,
        },
        footprint=footprint,
    )


def _pre_tau_p_consumption_scenario() -> dict[str, Any]:
    """A prospective study with a genuine pre-``tau_P`` ``CONSUMPTION``.

    A prior preregistration that contains ``H`` is frozen before the assessed
    preregistration ``P``; the ``CONSUMPTION`` (whose footprint overlaps
    ``fp(E)`` and whose preregistration contains ``H``) is appended before
    ``P``, so ``seq(c) < tau_P`` and rule 1a fires.
    """
    chain = T._Chain()
    decision = T._decision(chain)
    freeze = T._freeze(chain, T.HYP, [decision.record_hash])
    member = T._member_contract(T.HYP, freeze_record=freeze.record_hash)

    prior_prereg = T._prereg((member,), estimand_policy_record=decision.record_hash)
    prior_prereg_record = T._append_prereg(chain, prior_prereg, [freeze])
    prior_artifact = T._artifact(
        chain,
        footprint=T._fp(start=T.DAY5, end=T.DAY5),
        available_from=T.DAY5,
        packaging="prior-bytes",
    )
    consumption = _append_consumption(
        chain,
        prereg_record=prior_prereg_record,
        artifact_record=prior_artifact,
        footprint=T._fp(start=T.DAY5, end=T.DAY5),
        study_id="prior-study",
    )

    assessed_prereg = T._prereg((member,), estimand_policy_record=decision.record_hash)
    assessed_prereg_record = T._append_prereg(chain, assessed_prereg, [freeze])
    artifact = T._artifact(
        chain,
        footprint=T._fp(start=T.DAY5, end=T.DAY5),
        available_from=T.DAY5,
        packaging="assessed-bytes",
    )
    # The rule-1a source is strictly pre-tau_P.
    assert consumption.seq < assessed_prereg_record.seq
    return {
        "chain": chain,
        "prereg": assessed_prereg,
        "prereg_record": assessed_prereg_record,
        "artifacts": {T.HYP: artifact},
        "freezes": [freeze],
    }


# ---------------------------------------------------------------------------
# E -- own / later post-tau_P CONSUMPTION never downgrades
# ---------------------------------------------------------------------------


def test_assess_own_post_tau_p_consumption_is_not_robustness(tmp_path) -> None:
    scenario = T._g1_family([(T.HYP, T.DAY5)])
    artifact = scenario["artifacts"][T.HYP]
    own = _append_consumption(
        scenario["chain"],
        prereg_record=scenario["prereg_record"],
        artifact_record=artifact,
        footprint=T._fp(start=T.DAY5, end=T.DAY5),
    )
    # The study's own write-ahead CONSUMPTION references the assessed P (which
    # contains H), overlaps fp(E), and is post-tau_P.
    assert own.seq > scenario["prereg_record"].seq

    family = T._assess_family(scenario, {T.HYP: (0.01, 0.2)})
    member = family.for_hypothesis(T.HYP)
    assert member.evidence_role is not EvidenceRole.ROBUSTNESS
    assert member.evidence_role is EvidenceRole.CONFIRMATION_PROSPECTIVE
    assert member.state is AssessmentState.SUPPORTED

    # Reassessing against the same K (which holds the own CONSUMPTION) must not
    # downgrade: the role is unchanged, so the same family is returned and no
    # reassessment record is appended.
    T._wrap_family(scenario, family)
    log = T._persist(scenario["chain"], tmp_path)
    before = len(log.read())
    unchanged = A.reassess(family, log, calendar=T.CALENDAR)
    assert unchanged is family
    unchanged_member = unchanged.for_hypothesis(T.HYP)
    assert unchanged_member.evidence_role is EvidenceRole.CONFIRMATION_PROSPECTIVE
    assert unchanged_member.state is AssessmentState.SUPPORTED
    assert len(log.read()) == before

    # An additional later overlapping post-tau_P CONSUMPTION also never fires
    # rule 1a, so the role and assessment still do not change.
    log.append(
        kind=RecordKind.CONSUMPTION,
        program_id=T.PROGRAM,
        payload={
            "study_id": "study-1",
            "prereg_record_hash": scenario["prereg_record"].record_hash,
            "artifact_record_hash": artifact.record_hash,
        },
        footprint=T._fp(start=T.DAY5, end=T.DAY5),
    )
    after_append = len(log.read())
    still = A.reassess(unchanged, log, calendar=T.CALENDAR)
    still_member = still.for_hypothesis(T.HYP)
    assert still_member.evidence_role is EvidenceRole.CONFIRMATION_PROSPECTIVE
    assert still_member.state is AssessmentState.SUPPORTED
    assert len(log.read()) == after_append


# ---------------------------------------------------------------------------
# F -- genuine pre-tau_P CONSUMPTION, and downgrade semantics still effective
# ---------------------------------------------------------------------------


def test_assess_genuine_pre_tau_p_consumption_is_robustness(tmp_path) -> None:
    scenario = _pre_tau_p_consumption_scenario()
    family = T._assess_family(scenario, {T.HYP: (0.01, 0.2)})
    member = family.for_hypothesis(T.HYP)
    assert member.evidence_role is EvidenceRole.ROBUSTNESS
    assert member.state is AssessmentState.NOT_ASSESSED
    assert ReasonCode.ROLE_ROBUSTNESS in member.reason_codes

    # Downgrade-only reassessment is a fixpoint for an already-inadmissible
    # ROBUSTNESS member: no upgrade, no new record.
    T._wrap_family(scenario, family)
    log = T._persist(scenario["chain"], tmp_path)
    before = len(log.read())
    same = A.reassess(family, log, calendar=T.CALENDAR)
    assert same is family
    assert same.for_hypothesis(T.HYP).evidence_role is EvidenceRole.ROBUSTNESS
    assert len(log.read()) == before


def test_reassess_genuine_late_exposure_downgrade_still_applies(tmp_path) -> None:
    # The temporal amendment touches only rule 1a. The pre-existing section
    # 11.3 downgrade path (a late HUMAN EXPOSED declaration -> DEVELOPMENT)
    # must remain effective.
    scenario = T._g1_family([(T.HYP, T.DAY5)])
    family = T._assess_family(scenario, {T.HYP: (0.01, 0.2)})
    assert family.for_hypothesis(T.HYP).state is AssessmentState.SUPPORTED
    assert (
        family.for_hypothesis(T.HYP).evidence_role
        is EvidenceRole.CONFIRMATION_PROSPECTIVE
    )
    T._wrap_family(scenario, family)
    T._add_declaration(
        scenario["chain"],
        channel=Channel.HUMAN,
        footprint=T._fp(start=T.DAY5),
        exposed=True,
        exposure_event_date="2019-01-01",
        hypothesis_ids=(T.HYP,),
    )
    log = T._persist(scenario["chain"], tmp_path)
    before = len(log.read())
    new_family = A.reassess(family, log, calendar=T.CALENDAR)
    new = new_family.for_hypothesis(T.HYP)
    assert new.evidence_role is EvidenceRole.DEVELOPMENT
    assert new.state is AssessmentState.NOT_ASSESSED
    assert ReasonCode.ROLE_DEVELOPMENT in new.reason_codes
    assert len(log.read()) == before + 1
