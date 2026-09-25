"""Phase 10 P10-E: the pre-registration contract (plan section 7).

This module owns **only** the pre-registration surface frozen by
``worker_tasks/phase10/phase10-plan.md`` section 7 and the P10-E row of
section 16:

* :class:`EstimandPolicy` -- the single primary estimand-per-program policy
  (section 7.1), recorded as a ``HUMAN_DECISION`` with
  ``decision_kind = ESTIMAND_POLICY`` **before** the program's first
  ``HYPOTHESIS_FREEZE``;
* :class:`ConfirmationDesign` -- the ``confirmation`` sub-contract: window,
  realization bound, subjects, observation kinds, the canonical
  source-observation footprint body, the Phase-7 ``PartitionRef`` with
  ``HOLDOUT = window`` and the dataset contract;
* :class:`MemberContract` -- one family member (section 7.2): hypothesis
  identity, the frozen estimand terms, the admitted inference-procedure
  reference and the **exact** ``PROCEDURE_ADMISSION`` record it relies on;
* :class:`PreRegistration` -- the single object that freezes the whole family
  (R-5a): ``family_id = prereg_id``;
* :func:`validate_preregistration` -- the section 7.3 refusal matrix
  (checks 1-5), including the clarified admission-ordering check 4;
* :func:`append_preregistration` -- append the single ``PREREGISTRATION``
  record via :mod:`smart_beta.science.knowledge`, with the members'
  ``HYPOTHESIS_FREEZE`` records plus the ``ESTIMAND_POLICY`` record in
  ``refs.influenced_by`` and every ``admission_record_hash`` in
  ``refs.consulted`` (the mechanical binding that makes retrospective
  admission impossible);
* :func:`estimand_policy_from_record` / :func:`append_estimand_policy` and
  :func:`preregistration_from_record` for deterministic replay.

Temporal authority (plan sections 7.3 check 4 and 9.3)
------------------------------------------------------

Record **sequence and Knowledge-PIT prefix membership are the only authority**
for admission ordering. ``recorded_at`` timestamps are metadata and never
establish admission-before-freeze. The prefix passed to
:func:`validate_preregistration` is the exact K prefix visible when the
``PREREGISTRATION`` record is appended (``seq < tau_P``), so an admission
created later is either absent from the prefix (refused) or a forward
reference the K store rejects (:mod:`smart_beta.science.knowledge` accepts
only refs to smaller-seq records). No retrospective admission can make an
already-frozen preregistration admissible.

Dependencies
------------

The module imports only already-merged Phase-10 modules
(:mod:`smart_beta.science.contracts`, :mod:`smart_beta.science.knowledge`,
:mod:`smart_beta.science.footprint`, :mod:`smart_beta.science.inference`) and
never a sibling Wave-3 module. It performs no I/O beyond the append
:func:`append_preregistration` performs through the caller's
:class:`~smart_beta.science.knowledge.KnowledgeLog`, and it makes no network,
provider, PIT or holdout call.

The private ``_params_match`` helper is imported deliberately: it is the
single frozen implementation of the section 9.2 ``param_schema`` semantics,
so a parameter accepted here is exactly a parameter the P10-F executor will
accept. Duplicating it would risk divergence between preregistration-time and
execution-time validation.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any

from smart_beta.evaluation.spec import FoldRole, PartitionRef
from smart_beta.science.contracts import (
    DERIVATION_RULES_VERSION,
    NULL_HYPOTHESIS,
    PROTOCOL_VERSION,
    Direction,
    EstimandKind,
    MissingnessPolicy,
    ObservationKind,
    ReasonCode,
    RecordKind,
    canonical_json,
    content_hash,
    validate_footprint_shape,
)
from smart_beta.science.footprint import (
    FootprintOverlap,
    footprint_from_body,
    overlap,
)
from smart_beta.science.inference import (
    InferenceProcedureRegistry,
    _params_match,
)
from smart_beta.science.knowledge import (
    GENESIS_PREV_HASH,
    KnowledgeLog,
    KnowledgeRecord,
)

__all__ = [
    # frozen constants
    "ESTIMATOR_ID",
    "DECISION_RULE_VERSION",
    "CONSTRUCTION_KEYS",
    "DATASET_CONTRACT_KEYS",
    "PROCEDURE_ADMISSION",
    "PROCEDURE_REVOCATION",
    "ESTIMAND_POLICY",
    # errors
    "PreregistrationError",
    "PreregistrationRefused",
    # values
    "EstimandPolicy",
    "ConfirmationDesign",
    "MemberContract",
    "PreRegistration",
    # functions
    "estimand_policy_content",
    "estimand_policy_from_record",
    "append_estimand_policy",
    "analysis_plan_id_for",
    "validate_preregistration",
    "append_preregistration",
    "preregistration_from_record",
]


# ---------------------------------------------------------------------------
# frozen constants (plan sections 7.1, 7.2)
# ---------------------------------------------------------------------------

#: The single v1 per-date estimator name (plan section 7.2).
ESTIMATOR_ID = "mean_per_date_v1"

#: The decision-rule version hashed into ``analysis_plan_id`` (section 7.2).
#: No constant is frozen by name in the plan; this is the version tag of the
#: section 11.2 total-mapping decision rule that P10-G implements.
DECISION_RULE_VERSION = "phase10-decision-rule-v1"

#: The Phase-6/7 factor-panel construction keys (section 7.1).
CONSTRUCTION_KEYS: tuple[str, ...] = (
    "n_groups",
    "cost_bps",
    "winsorization",
    "factor_missing_policy",
)

#: The dataset-contract keys (section 7.2).
DATASET_CONTRACT_KEYS: tuple[str, ...] = (
    "security_map_hash",
    "market_series_map_hash",
    "variable_map_hash",
    "calendar_hash",
    "derivation_rules_version",
)

#: ``HUMAN_DECISION`` ``decision_kind`` values owned by this module.
PROCEDURE_ADMISSION = "PROCEDURE_ADMISSION"
PROCEDURE_REVOCATION = "PROCEDURE_REVOCATION"
ESTIMAND_POLICY = "ESTIMAND_POLICY"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class PreregistrationError(ValueError):
    """A preregistration value is structurally malformed (fail closed).

    Raised for construction/contract defects (bad field, duplicate member,
    missing freeze, a policy that does not precede the program's first
    freeze, a window outside the HOLDOUT fold, a non-canonical stored body).
    Scientific refusals that the plan maps to a ``ReasonCode`` are raised as
    :class:`PreregistrationRefused` instead.
    """


class PreregistrationRefused(Exception):
    """The frozen section 7.3 refusal matrix rejects the preregistration.

    No ``PREREGISTRATION`` record is appended when this is raised. ``reason``
    is the closed :class:`~smart_beta.science.contracts.ReasonCode` the plan
    names for the failed check.
    """

    def __init__(self, reason: ReasonCode | str, detail: str = "") -> None:
        if not isinstance(reason, ReasonCode):
            reason = ReasonCode(reason)
        self.reason = reason
        self.detail = detail
        text = f"preregistration refused: {reason.value}"
        if detail:
            text = f"{text}: {detail}"
        super().__init__(text)


# ---------------------------------------------------------------------------
# small validation helpers
# ---------------------------------------------------------------------------


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _expect_non_empty_str(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise PreregistrationError(
            f"{where} must be a non-empty string, got {value!r}"
        )
    return value


def _expect_sha256(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or _SHA256_HEX_RE.match(value) is None:
        raise PreregistrationError(
            f"{where} must be a lowercase 64-hex sha256, got {value!r}"
        )
    return value


def _expect_finite_number(
    value: Any,
    *,
    where: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PreregistrationError(f"{where} must be a finite number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise PreregistrationError(f"{where} must be finite, got {value!r}")
    if minimum is not None and number < minimum:
        raise PreregistrationError(f"{where} must be >= {minimum}, got {value!r}")
    if maximum is not None and number > maximum:
        raise PreregistrationError(f"{where} must be <= {maximum}, got {value!r}")
    return number


def _expect_int(
    value: Any, *, where: str, minimum: int | None = None
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PreregistrationError(f"{where} must be an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise PreregistrationError(f"{where} must be >= {minimum}, got {value!r}")
    return value


def _expect_iso_date(value: Any, *, where: str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        value = value.isoformat()
    if not isinstance(value, str) or _ISO_DATE_RE.match(value) is None:
        raise PreregistrationError(
            f"{where} must be an ISO YYYY-MM-DD date, got {value!r}"
        )
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise PreregistrationError(f"{where} is not a valid date: {exc}") from exc
    return value


def _normalize_construction(value: Any) -> Mapping[str, Any]:
    """Validate and freeze the Phase-6/7 factor-panel construction policy."""
    if not isinstance(value, Mapping):
        raise PreregistrationError(
            f"construction must be a mapping, got {type(value).__name__}"
        )
    if set(value.keys()) != set(CONSTRUCTION_KEYS):
        raise PreregistrationError(
            "construction keys must be exactly "
            f"{sorted(CONSTRUCTION_KEYS)}, got {sorted(value.keys())}"
        )
    normalized = {
        "n_groups": _expect_int(
            value["n_groups"], where="construction.n_groups", minimum=2
        ),
        "cost_bps": _expect_finite_number(
            value["cost_bps"], where="construction.cost_bps", minimum=0.0
        ),
        "winsorization": _expect_finite_number(
            value["winsorization"],
            where="construction.winsorization",
            minimum=0.0,
            maximum=0.5,
        ),
        "factor_missing_policy": _expect_non_empty_str(
            value["factor_missing_policy"],
            where="construction.factor_missing_policy",
        ),
    }
    return _freeze(normalized)


def _coerce_estimand_kind(value: Any, *, where: str) -> EstimandKind:
    if isinstance(value, EstimandKind):
        return value
    try:
        return EstimandKind(value)
    except (TypeError, ValueError) as exc:
        raise PreregistrationError(
            f"{where} must be a closed EstimandKind, got {value!r}"
        ) from exc


def _coerce_direction(value: Any, *, where: str) -> Direction:
    if isinstance(value, Direction):
        return value
    try:
        return Direction(value)
    except (TypeError, ValueError) as exc:
        raise PreregistrationError(
            f"{where} must be a closed Direction, got {value!r}"
        ) from exc


def _coerce_missingness(value: Any, *, where: str) -> MissingnessPolicy:
    if isinstance(value, MissingnessPolicy):
        return value
    try:
        return MissingnessPolicy(value)
    except (TypeError, ValueError) as exc:
        raise PreregistrationError(
            f"{where} must be a closed MissingnessPolicy, got {value!r}"
        ) from exc


def _coerce_observation_kind(value: Any, *, where: str) -> ObservationKind:
    if isinstance(value, ObservationKind):
        return value
    try:
        return ObservationKind(value)
    except (TypeError, ValueError) as exc:
        raise PreregistrationError(
            f"{where} must be a closed ObservationKind, got {value!r}"
        ) from exc


def _normalize_procedure_ref(value: Any) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise PreregistrationError("procedure_ref must be a mapping")
    expected = {"procedure_id", "version", "contract_hash"}
    if set(value.keys()) != expected:
        raise PreregistrationError(
            f"procedure_ref keys must be exactly {sorted(expected)}"
        )
    return _freeze(
        {
            "procedure_id": _expect_non_empty_str(
                value["procedure_id"], where="procedure_ref.procedure_id"
            ),
            "version": _expect_non_empty_str(
                value["version"], where="procedure_ref.version"
            ),
            "contract_hash": _expect_sha256(
                value["contract_hash"], where="procedure_ref.contract_hash"
            ),
        }
    )


def _normalize_params(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PreregistrationError("params must be a mapping")
    try:
        canonical_json(_plain(value))
    except Exception as exc:  # noqa: BLE001 - fail closed on any defect
        raise PreregistrationError(f"params are not canonical-JSON: {exc}") from exc
    return _freeze(_plain(value))


# ---------------------------------------------------------------------------
# EstimandPolicy (plan section 7.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EstimandPolicy:
    """The single primary estimand policy of one program (section 7.1).

    Recorded as a ``HUMAN_DECISION`` with ``decision_kind = ESTIMAND_POLICY``
    before the program's first ``HYPOTHESIS_FREEZE``. The direction is
    declared per member, never here.
    """

    program_id: str
    estimand_kind: EstimandKind
    horizon: int
    construction: Mapping[str, Any]
    sesoi: float
    sesoi_justification_hash: str

    def __post_init__(self) -> None:
        _expect_non_empty_str(self.program_id, where="EstimandPolicy.program_id")
        if not isinstance(self.estimand_kind, EstimandKind):
            object.__setattr__(
                self, "estimand_kind", _coerce_estimand_kind(self.estimand_kind, where="EstimandPolicy.estimand_kind")
            )
        object.__setattr__(
            self,
            "horizon",
            _expect_int(self.horizon, where="EstimandPolicy.horizon", minimum=1),
        )
        object.__setattr__(
            self, "construction", _normalize_construction(self.construction)
        )
        object.__setattr__(
            self,
            "sesoi",
            _expect_finite_number(
                self.sesoi, where="EstimandPolicy.sesoi", minimum=0.0
            ),
        )
        if float(self.sesoi) <= 0.0:
            raise PreregistrationError("EstimandPolicy.sesoi must be > 0")
        _expect_sha256(
            self.sesoi_justification_hash,
            where="EstimandPolicy.sesoi_justification_hash",
        )

    def to_content(self) -> dict[str, Any]:
        """The canonical plan-content (no envelope metadata)."""
        return estimand_policy_content(self)

    @classmethod
    def from_content(cls, mapping: Mapping[str, Any]) -> "EstimandPolicy":
        required = {
            "program_id",
            "estimand_kind",
            "horizon",
            "construction",
            "sesoi",
            "sesoi_justification_hash",
        }
        if not isinstance(mapping, Mapping) or set(mapping.keys()) != required:
            raise PreregistrationError(
                "EstimandPolicy content keys must be exactly "
                f"{sorted(required)}"
            )
        return cls(
            program_id=_expect_non_empty_str(
                mapping["program_id"], where="EstimandPolicy.program_id"
            ),
            estimand_kind=_coerce_estimand_kind(
                mapping["estimand_kind"], where="EstimandPolicy.estimand_kind"
            ),
            horizon=_expect_int(
                mapping["horizon"], where="EstimandPolicy.horizon", minimum=1
            ),
            construction=_normalize_construction(mapping["construction"]),
            sesoi=_expect_finite_number(
                mapping["sesoi"], where="EstimandPolicy.sesoi", minimum=0.0
            ),
            sesoi_justification_hash=_expect_sha256(
                mapping["sesoi_justification_hash"],
                where="EstimandPolicy.sesoi_justification_hash",
            ),
        )


def estimand_policy_content(policy: EstimandPolicy) -> dict[str, Any]:
    """The canonical ``ESTIMAND_POLICY`` content (without the envelope keys)."""
    if not isinstance(policy, EstimandPolicy):
        raise PreregistrationError("policy must be an EstimandPolicy")
    return {
        "program_id": policy.program_id,
        "estimand_kind": policy.estimand_kind.value,
        "horizon": policy.horizon,
        "construction": _plain(policy.construction),
        "sesoi": policy.sesoi,
        "sesoi_justification_hash": policy.sesoi_justification_hash,
    }


def _decision_kind(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("decision_kind")
    if hasattr(value, "value"):
        value = value.value
    return value if isinstance(value, str) else None


def estimand_policy_from_record(record: Any) -> EstimandPolicy:
    """Load and validate the ``ESTIMAND_POLICY`` decision from a K record."""
    if _kind(record) != RecordKind.HUMAN_DECISION:
        raise PreregistrationError(
            "the estimand_policy_record must name a HUMAN_DECISION record"
        )
    payload = _payload(record)
    if _decision_kind(payload) != ESTIMAND_POLICY:
        raise PreregistrationError(
            "the estimand_policy_record must carry decision_kind = "
            "ESTIMAND_POLICY"
        )
    return EstimandPolicy.from_content(
        {key: payload[key] for key in (
            "program_id",
            "estimand_kind",
            "horizon",
            "construction",
            "sesoi",
            "sesoi_justification_hash",
        ) if key in payload}
    )


def append_estimand_policy(
    log: KnowledgeLog,
    policy: EstimandPolicy,
    *,
    actor_role: str = "OPERATOR",
    consulted: Sequence[str] = (),
    consult_all_prior: bool = False,
) -> KnowledgeRecord:
    """Append the ``ESTIMAND_POLICY`` HumanDecision record (section 7.1)."""
    if not isinstance(log, KnowledgeLog):
        raise PreregistrationError("log must be a KnowledgeLog")
    if not isinstance(policy, EstimandPolicy):
        raise PreregistrationError("policy must be an EstimandPolicy")
    _expect_non_empty_str(actor_role, where="actor_role")
    payload: dict[str, Any] = {
        "decision_kind": ESTIMAND_POLICY,
        "actor_role": actor_role,
        **estimand_policy_content(policy),
    }
    if consult_all_prior:
        payload["consulted_all_prior"] = True
    return log.append(
        kind=RecordKind.HUMAN_DECISION,
        payload=payload,
        program_id=policy.program_id,
        refs={"consulted": tuple(consulted)},
    )


# ---------------------------------------------------------------------------
# ConfirmationDesign (plan section 7.2 ``confirmation``)
# ---------------------------------------------------------------------------


def _canonical_partition_spec(
    value: Any, *, window: tuple[str, str]
) -> Mapping[str, Any]:
    """Return the canonical sealed Phase-7 ``PartitionRef`` representation.

    Validation authority is the sealed
    :class:`smart_beta.evaluation.spec.PartitionRef`: the supplied mapping is
    parsed with ``PartitionRef.from_dict`` and must already equal the
    resulting ``PartitionRef.to_dict()`` (compared through the frozen
    section 4.1 canonical JSON, so the persisted JSON representation is
    what is compared). A non-canonical caller representation is **refused**,
    never silently normalized; P10-E reproduces none of the sealed rules.

    The persisted boundary is the half-open ``[start, end)`` interval
    carried verbatim by ``engine._partition_ref``; the \"whole window in the
    HOLDOUT fold\" check uses the direct ``start <= w0 and w1 <= end``
    translation (section 7.3 check 4). ``Partition.partition_id`` is never
    consulted.
    """
    if not isinstance(value, Mapping):
        raise PreregistrationError("partition_spec must be a mapping")
    try:
        partition_ref = PartitionRef.from_dict(value)
    except Exception as exc:  # noqa: BLE001 - sealed authority fails closed
        raise PreregistrationError(
            f"partition_spec is not a valid sealed Phase-7 PartitionRef: {exc}"
        ) from exc
    canonical = partition_ref.to_dict()
    try:
        supplied_json = canonical_json(_plain(value))
    except Exception as exc:  # noqa: BLE001 - fail closed
        raise PreregistrationError(
            f"partition_spec is not canonical JSON: {exc}"
        ) from exc
    if supplied_json != canonical_json(canonical):
        raise PreregistrationError(
            "partition_spec must already be the canonical PartitionRef "
            "representation (PartitionRef.from_dict(...).to_dict()); a "
            "non-canonical representation is refused, never normalized"
        )
    holdouts = [
        fold for fold in partition_ref.folds if fold.role is FoldRole.HOLDOUT
    ]
    if len(holdouts) != 1:
        raise PreregistrationError(
            "partition_spec must define exactly one HOLDOUT fold"
        )
    holdout = holdouts[0]
    w0 = date.fromisoformat(window[0])
    w1 = date.fromisoformat(window[1])
    if not (holdout.start <= w0 and w1 <= holdout.end):
        raise PreregistrationError(
            "partition_spec must place the whole confirmation window in the "
            f"HOLDOUT fold: window=[{window[0]}, {window[1]}] "
            f"holdout=[{holdout.start.isoformat()}, "
            f"{holdout.end.isoformat()}]"
        )
    return _freeze(canonical)


def _normalize_dataset_contract(value: Any, *, footprint: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value.keys()) != set(
        DATASET_CONTRACT_KEYS
    ):
        raise PreregistrationError(
            "dataset_contract keys must be exactly "
            f"{sorted(DATASET_CONTRACT_KEYS)}"
        )
    normalized = {
        "security_map_hash": _expect_sha256(
            value["security_map_hash"], where="dataset_contract.security_map_hash"
        ),
        "market_series_map_hash": _expect_sha256(
            value["market_series_map_hash"],
            where="dataset_contract.market_series_map_hash",
        ),
        "variable_map_hash": _expect_sha256(
            value["variable_map_hash"], where="dataset_contract.variable_map_hash"
        ),
        "calendar_hash": _expect_sha256(
            value["calendar_hash"], where="dataset_contract.calendar_hash"
        ),
        "derivation_rules_version": _expect_non_empty_str(
            value["derivation_rules_version"],
            where="dataset_contract.derivation_rules_version",
        ),
    }
    if normalized["derivation_rules_version"] != DERIVATION_RULES_VERSION:
        raise PreregistrationError(
            "dataset_contract.derivation_rules_version must be "
            f"{DERIVATION_RULES_VERSION!r}"
        )
    if footprint.get("determinable") is True:
        for key in (
            "security_map_hash",
            "market_series_map_hash",
            "variable_map_hash",
            "calendar_hash",
            "derivation_rules_version",
        ):
            if footprint.get(key) != normalized[key]:
                raise PreregistrationError(
                    "dataset_contract must match the declared footprint's "
                    f"{key}"
                )
    return _freeze(normalized)


@dataclass(frozen=True)
class ConfirmationDesign:
    """The frozen ``confirmation`` sub-contract (plan section 7.2)."""

    window: tuple[str, str]
    realization_bound_days: int
    subjects: tuple[str, ...]
    observation_kinds: tuple[ObservationKind, ...]
    declared_footprint: Mapping[str, Any]
    partition_spec: Mapping[str, Any]
    dataset_contract: Mapping[str, Any]

    def __post_init__(self) -> None:
        window = self.window
        if isinstance(window, (str, bytes)) or not isinstance(window, Sequence) or len(window) != 2:
            raise PreregistrationError("confirmation.window must be a [w0, w1] pair")
        w0 = _expect_iso_date(window[0], where="confirmation.window[0]")
        w1 = _expect_iso_date(window[1], where="confirmation.window[1]")
        if w0 > w1:
            raise PreregistrationError("confirmation.window w0 must not exceed w1")
        object.__setattr__(self, "window", (w0, w1))
        object.__setattr__(
            self,
            "realization_bound_days",
            _expect_int(
                self.realization_bound_days,
                where="confirmation.realization_bound_days",
                minimum=0,
            ),
        )
        subjects = self.subjects
        if isinstance(subjects, (str, bytes)) or not isinstance(subjects, Sequence) or not subjects:
            raise PreregistrationError(
                "confirmation.subjects must be a non-empty sequence"
            )
        normalized_subjects = sorted(
            {
                _expect_non_empty_str(item, where="confirmation.subjects entry")
                for item in subjects
            }
        )
        object.__setattr__(self, "subjects", tuple(normalized_subjects))
        kinds = self.observation_kinds
        if isinstance(kinds, (str, bytes)) or not isinstance(kinds, Sequence) or not kinds:
            raise PreregistrationError(
                "confirmation.observation_kinds must be a non-empty sequence"
            )
        normalized_kinds = sorted(
            {
                _coerce_observation_kind(item, where="confirmation.observation_kinds entry")
                for item in kinds
            },
            key=lambda kind: kind.value,
        )
        object.__setattr__(self, "observation_kinds", tuple(normalized_kinds))
        footprint = self.declared_footprint
        if not isinstance(footprint, Mapping):
            raise PreregistrationError("confirmation.declared_footprint must be a mapping")
        try:
            validate_footprint_shape(footprint)
        except Exception as exc:  # noqa: BLE001 - fail closed
            raise PreregistrationError(
                f"confirmation.declared_footprint is not canonical: {exc}"
            ) from exc
        object.__setattr__(self, "declared_footprint", _freeze(_plain(footprint)))
        object.__setattr__(
            self,
            "partition_spec",
            _canonical_partition_spec(self.partition_spec, window=(w0, w1)),
        )
        object.__setattr__(
            self,
            "dataset_contract",
            _normalize_dataset_contract(
                self.dataset_contract, footprint=footprint
            ),
        )

    @property
    def partition_ref_hash(self) -> str:
        """Frozen section 4.1 identity of the canonical ``PartitionRef``.

        ``content_hash(PartitionRef.to_dict())`` -- the same value P10-S
        computes for ``InferentialSeriesBundle.partition_ref_hash``. It is
        never derived from ``Partition.partition_id``.
        """
        return content_hash(PartitionRef.from_dict(self.partition_spec).to_dict())

    def to_content(self) -> dict[str, Any]:
        return {
            "window": list(self.window),
            "realization_bound_days": self.realization_bound_days,
            "subjects": list(self.subjects),
            "observation_kinds": [kind.value for kind in self.observation_kinds],
            "declared_footprint": _plain(self.declared_footprint),
            "partition_spec": _plain(self.partition_spec),
            "dataset_contract": _plain(self.dataset_contract),
        }

    @classmethod
    def from_content(cls, mapping: Mapping[str, Any]) -> "ConfirmationDesign":
        required = {
            "window",
            "realization_bound_days",
            "subjects",
            "observation_kinds",
            "declared_footprint",
            "partition_spec",
            "dataset_contract",
        }
        if not isinstance(mapping, Mapping) or set(mapping.keys()) != required:
            raise PreregistrationError(
                f"confirmation content keys must be exactly {sorted(required)}"
            )
        return cls(
            window=tuple(mapping["window"]),
            realization_bound_days=mapping["realization_bound_days"],
            subjects=tuple(mapping["subjects"]),
            observation_kinds=tuple(mapping["observation_kinds"]),
            declared_footprint=mapping["declared_footprint"],
            partition_spec=mapping["partition_spec"],
            dataset_contract=mapping["dataset_contract"],
        )


# ---------------------------------------------------------------------------
# MemberContract (plan section 7.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberContract:
    """One frozen family member (plan section 7.2)."""

    hypothesis_id: str
    hypothesis_freeze_record: str
    factor_spec_hash: str
    estimand_kind: EstimandKind
    horizon: int
    construction: Mapping[str, Any]
    direction: Direction
    sesoi: float
    estimator_id: str
    procedure_ref: Mapping[str, str]
    admission_record_hash: str
    params: Mapping[str, Any]
    dependence_justification_hash: str
    missingness_policy: MissingnessPolicy
    bound_alpha: float

    def __post_init__(self) -> None:
        _expect_non_empty_str(self.hypothesis_id, where="member.hypothesis_id")
        _expect_sha256(
            self.hypothesis_freeze_record,
            where="member.hypothesis_freeze_record",
        )
        _expect_sha256(self.factor_spec_hash, where="member.factor_spec_hash")
        if not isinstance(self.estimand_kind, EstimandKind):
            object.__setattr__(
                self,
                "estimand_kind",
                _coerce_estimand_kind(
                    self.estimand_kind, where="member.estimand_kind"
                ),
            )
        object.__setattr__(
            self,
            "horizon",
            _expect_int(self.horizon, where="member.horizon", minimum=1),
        )
        object.__setattr__(
            self, "construction", _normalize_construction(self.construction)
        )
        if not isinstance(self.direction, Direction):
            object.__setattr__(
                self,
                "direction",
                _coerce_direction(self.direction, where="member.direction"),
            )
        object.__setattr__(
            self,
            "sesoi",
            _expect_finite_number(self.sesoi, where="member.sesoi", minimum=0.0),
        )
        if float(self.sesoi) <= 0.0:
            raise PreregistrationError("member.sesoi must be > 0")
        if self.estimator_id != ESTIMATOR_ID:
            raise PreregistrationError(
                f"member.estimator_id must be {ESTIMATOR_ID!r}"
            )
        object.__setattr__(
            self, "procedure_ref", _normalize_procedure_ref(self.procedure_ref)
        )
        _expect_sha256(
            self.admission_record_hash, where="member.admission_record_hash"
        )
        object.__setattr__(self, "params", _normalize_params(self.params))
        _expect_sha256(
            self.dependence_justification_hash,
            where="member.dependence_justification_hash",
        )
        if not isinstance(self.missingness_policy, MissingnessPolicy):
            object.__setattr__(
                self,
                "missingness_policy",
                _coerce_missingness(
                    self.missingness_policy, where="member.missingness_policy"
                ),
            )
        object.__setattr__(
            self,
            "bound_alpha",
            _expect_finite_number(
                self.bound_alpha, where="member.bound_alpha", minimum=0.0
            ),
        )
        if not (0.0 < float(self.bound_alpha) < 1.0):
            raise PreregistrationError("member.bound_alpha must be in (0, 1)")

    @property
    def null_hypothesis(self) -> str:
        """The only null (plan section 4.3); not a settable field."""
        return NULL_HYPOTHESIS

    @property
    def dependence_design_id(self) -> str:
        """``content_hash(procedure_ref, params, justification)`` (section 7.2)."""
        return content_hash(
            {
                "procedure_ref": _plain(self.procedure_ref),
                "params": _plain(self.params),
                "dependence_justification_hash": self.dependence_justification_hash,
            }
        )

    def to_content(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "hypothesis_freeze_record": self.hypothesis_freeze_record,
            "factor_spec_hash": self.factor_spec_hash,
            "estimand_kind": self.estimand_kind.value,
            "horizon": self.horizon,
            "construction": _plain(self.construction),
            "direction": self.direction.value,
            "null": NULL_HYPOTHESIS,
            "sesoi": self.sesoi,
            "estimator_id": self.estimator_id,
            "procedure_ref": _plain(self.procedure_ref),
            "admission_record_hash": self.admission_record_hash,
            "params": _plain(self.params),
            "dependence_justification_hash": self.dependence_justification_hash,
            "missingness_policy": self.missingness_policy.value,
            "bound_alpha": self.bound_alpha,
        }

    @classmethod
    def from_content(cls, mapping: Mapping[str, Any]) -> "MemberContract":
        required = {
            "hypothesis_id",
            "hypothesis_freeze_record",
            "factor_spec_hash",
            "estimand_kind",
            "horizon",
            "construction",
            "direction",
            "null",
            "sesoi",
            "estimator_id",
            "procedure_ref",
            "admission_record_hash",
            "params",
            "dependence_justification_hash",
            "missingness_policy",
            "bound_alpha",
        }
        if not isinstance(mapping, Mapping) or set(mapping.keys()) != required:
            raise PreregistrationError(
                f"member content keys must be exactly {sorted(required)}"
            )
        if mapping["null"] != NULL_HYPOTHESIS:
            raise PreregistrationError(
                f"member.null must be {NULL_HYPOTHESIS!r}"
            )
        return cls(
            hypothesis_id=mapping["hypothesis_id"],
            hypothesis_freeze_record=mapping["hypothesis_freeze_record"],
            factor_spec_hash=mapping["factor_spec_hash"],
            estimand_kind=mapping["estimand_kind"],
            horizon=mapping["horizon"],
            construction=mapping["construction"],
            direction=mapping["direction"],
            sesoi=mapping["sesoi"],
            estimator_id=mapping["estimator_id"],
            procedure_ref=mapping["procedure_ref"],
            admission_record_hash=mapping["admission_record_hash"],
            params=mapping["params"],
            dependence_justification_hash=mapping["dependence_justification_hash"],
            missingness_policy=mapping["missingness_policy"],
            bound_alpha=mapping["bound_alpha"],
        )


def _analysis_plan_content(
    members: Sequence[MemberContract],
    *,
    decision_rule_version: str = DECISION_RULE_VERSION,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "decision_rule_version": decision_rule_version,
        "members": [
            {
                "estimator_id": member.estimator_id,
                "procedure_ref": _plain(member.procedure_ref),
                "params": _plain(member.params),
                "missingness_policy": member.missingness_policy.value,
                "bound_alpha": member.bound_alpha,
            }
            for member in members
        ],
    }


def analysis_plan_id_for(
    members: Sequence[MemberContract],
    *,
    decision_rule_version: str = DECISION_RULE_VERSION,
) -> str:
    """The section 7.2 ``analysis_plan_id`` content hash.

    ``PreRegistration.analysis_plan_id`` always calls this with the frozen
    module constant :data:`DECISION_RULE_VERSION`; the keyword exists so a
    test can mechanically show that a different decision-rule version changes
    the id while the frozen v1 string is unchanged.
    """
    return content_hash(
        _analysis_plan_content(
            members, decision_rule_version=decision_rule_version
        )
    )


# ---------------------------------------------------------------------------
# PreRegistration (plan section 7.2)
# ---------------------------------------------------------------------------

_PREREG_BODY_KEYS = frozenset(
    {
        "prereg_id",
        "family_id",
        "estimand_policy_record",
        "members",
        "alpha_study",
        "confirmation",
        "partition_ref_hash",
        "analysis_plan_id",
        "power_disclosure",
    }
)

_POWER_DISCLOSURE_REQUIRED = ("mde", "sigma_lr", "T_conf", "source_record")


def _normalize_power_disclosure(
    value: Any, *, member_ids: Sequence[str]
) -> Mapping[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        raise PreregistrationError("power_disclosure must be a mapping")
    if set(value.keys()) != set(member_ids):
        raise PreregistrationError(
            "power_disclosure must have exactly one entry per member: "
            f"expected {sorted(member_ids)}, got {sorted(value.keys())}"
        )
    normalized: dict[str, Mapping[str, Any]] = {}
    for member_id in member_ids:
        disclosure = value[member_id]
        if not isinstance(disclosure, Mapping):
            raise PreregistrationError(
                f"power_disclosure[{member_id!r}] must be a mapping"
            )
        if "unavailable_reason" in disclosure:
            if set(disclosure.keys()) != {"unavailable_reason"}:
                raise PreregistrationError(
                    f"power_disclosure[{member_id!r}] must be either the "
                    "reported fields or {{'unavailable_reason': ...}}, not both"
                )
            normalized[member_id] = _freeze(
                {
                    "unavailable_reason": _expect_non_empty_str(
                        disclosure["unavailable_reason"],
                        where=f"power_disclosure[{member_id!r}].unavailable_reason",
                    )
                }
            )
            continue
        if set(disclosure.keys()) != set(_POWER_DISCLOSURE_REQUIRED):
            raise PreregistrationError(
                f"power_disclosure[{member_id!r}] must have exactly "
                f"{sorted(_POWER_DISCLOSURE_REQUIRED)} or unavailable_reason"
            )
        normalized[member_id] = _freeze(
            {
                "mde": _expect_finite_number(
                    disclosure["mde"],
                    where=f"power_disclosure[{member_id!r}].mde",
                ),
                "sigma_lr": _expect_finite_number(
                    disclosure["sigma_lr"],
                    where=f"power_disclosure[{member_id!r}].sigma_lr",
                    minimum=0.0,
                ),
                "T_conf": _expect_int(
                    disclosure["T_conf"],
                    where=f"power_disclosure[{member_id!r}].T_conf",
                    minimum=0,
                ),
                "source_record": _expect_non_empty_str(
                    disclosure["source_record"],
                    where=f"power_disclosure[{member_id!r}].source_record",
                ),
            }
        )
    return _freeze(normalized)


@dataclass(frozen=True)
class PreRegistration:
    """The single object that freezes the whole family (plan R-5a).

    ``prereg_id`` and ``family_id`` are derived; the family belongs directly
    here and freezes atomically under one hash at one ``tau_P``.
    """

    estimand_policy_record: str
    members: tuple[MemberContract, ...]
    alpha_study: float
    confirmation: ConfirmationDesign
    power_disclosure: Mapping[str, Mapping[str, Any]]

    def __post_init__(self) -> None:
        _expect_sha256(
            self.estimand_policy_record,
            where="PreRegistration.estimand_policy_record",
        )
        if isinstance(self.members, (str, bytes)) or not isinstance(
            self.members, Sequence
        ):
            raise PreregistrationError("PreRegistration.members must be a sequence")
        members = tuple(self.members)
        if not members:
            raise PreregistrationError(
                "a preregistration must freeze at least one family member"
            )
        for member in members:
            if not isinstance(member, MemberContract):
                raise PreregistrationError(
                    "every member must be a MemberContract"
                )
        hypothesis_ids = [member.hypothesis_id for member in members]
        if len(set(hypothesis_ids)) != len(hypothesis_ids):
            raise PreregistrationError(
                "PreRegistration members must have unique hypothesis_id values"
            )
        # The family is canonically ordered by hypothesis_id.
        object.__setattr__(
            self,
            "members",
            tuple(sorted(members, key=lambda member: member.hypothesis_id)),
        )
        object.__setattr__(
            self,
            "alpha_study",
            _expect_finite_number(
                self.alpha_study, where="PreRegistration.alpha_study", minimum=0.0
            ),
        )
        if not (0.0 < float(self.alpha_study) < 0.5):
            raise PreregistrationError(
                "PreRegistration.alpha_study must be in (0, 0.5)"
            )
        if not isinstance(self.confirmation, ConfirmationDesign):
            raise PreregistrationError(
                "PreRegistration.confirmation must be a ConfirmationDesign"
            )
        object.__setattr__(
            self,
            "power_disclosure",
            _normalize_power_disclosure(
                self.power_disclosure,
                member_ids=[member.hypothesis_id for member in self.members],
            ),
        )

    # -- derived identity --------------------------------------------------

    @property
    def analysis_plan_id(self) -> str:
        """``content_hash`` of the section 7.2 analysis-plan content.

        Always uses the frozen :data:`DECISION_RULE_VERSION` constant.
        """
        return analysis_plan_id_for(self.members)

    @property
    def partition_ref_hash(self) -> str:
        """Frozen section 4.1 identity of the canonical ``partition_spec``.

        Equal to the P10-S ``InferentialSeriesBundle.partition_ref_hash`` for
        the same ``PartitionRef``; never ``Partition.partition_id``.
        """
        return self.confirmation.partition_ref_hash

    @property
    def prereg_id(self) -> str:
        """``content_hash`` of the body below, excluding the derived ids."""
        return content_hash(self._core_body())

    @property
    def family_id(self) -> str:
        """One family per preregistration (plan R-5a)."""
        return self.prereg_id

    def _core_body(self) -> dict[str, Any]:
        return {
            "estimand_policy_record": self.estimand_policy_record,
            "members": [member.to_content() for member in self.members],
            "alpha_study": self.alpha_study,
            "confirmation": self.confirmation.to_content(),
            "partition_ref_hash": self.partition_ref_hash,
            "analysis_plan_id": self.analysis_plan_id,
            "power_disclosure": {
                member.hypothesis_id: _plain(
                    self.power_disclosure[member.hypothesis_id]
                )
                for member in self.members
            },
        }

    def to_content(self) -> dict[str, Any]:
        """The full canonical body stored in the ``PREREGISTRATION`` payload."""
        body = self._core_body()
        return {
            "prereg_id": self.prereg_id,
            "family_id": self.family_id,
            **body,
        }

    # -- reconstruction ----------------------------------------------------

    @classmethod
    def from_content(cls, mapping: Mapping[str, Any]) -> "PreRegistration":
        if not isinstance(mapping, Mapping) or set(mapping.keys()) != _PREREG_BODY_KEYS:
            raise PreregistrationError(
                "preregistration body keys must be exactly "
                f"{sorted(_PREREG_BODY_KEYS)}"
            )
        core = {
            key: mapping[key]
            for key in (
                "estimand_policy_record",
                "members",
                "alpha_study",
                "confirmation",
                "partition_ref_hash",
                "analysis_plan_id",
                "power_disclosure",
            )
        }
        computed = content_hash(core)
        if mapping["prereg_id"] != computed:
            raise PreregistrationError(
                "prereg_id does not match the preregistration content"
            )
        if mapping["family_id"] != mapping["prereg_id"]:
            raise PreregistrationError(
                "family_id must equal prereg_id (one family per preregistration)"
            )
        prereg = cls(
            estimand_policy_record=mapping["estimand_policy_record"],
            members=tuple(
                MemberContract.from_content(member)
                for member in mapping["members"]
            ),
            alpha_study=mapping["alpha_study"],
            confirmation=ConfirmationDesign.from_content(mapping["confirmation"]),
            power_disclosure=mapping["power_disclosure"],
        )
        if prereg.analysis_plan_id != mapping["analysis_plan_id"]:
            raise PreregistrationError(
                "analysis_plan_id does not match the member analysis plan"
            )
        if prereg.partition_ref_hash != mapping["partition_ref_hash"]:
            raise PreregistrationError(
                "partition_ref_hash does not match the canonical PartitionRef"
            )
        if prereg.to_content() != _plain(mapping):
            raise PreregistrationError(
                "preregistration body is not in canonical form"
            )
        return prereg


# ---------------------------------------------------------------------------
# generic K-record accessors (KnowledgeRecord or plain validated mapping)
# ---------------------------------------------------------------------------


def _field(record: Any, key: str) -> Any:
    if isinstance(record, Mapping):
        if key not in record:
            raise PreregistrationError(f"record is missing {key!r}")
        return record[key]
    if not hasattr(record, key):
        raise PreregistrationError(f"record is missing {key!r}")
    return getattr(record, key)


def _kind(record: Any) -> RecordKind | None:
    value = _field(record, "kind")
    if isinstance(value, RecordKind):
        return value
    try:
        return RecordKind(value)
    except (TypeError, ValueError):
        return None


def _seq(record: Any) -> int:
    value = _field(record, "seq")
    if isinstance(value, bool) or not isinstance(value, int):
        raise PreregistrationError("record seq must be an integer")
    return value


def _payload(record: Any) -> Mapping[str, Any]:
    value = _field(record, "payload")
    if not isinstance(value, Mapping):
        raise PreregistrationError("record payload must be a mapping")
    return value


def _record_hash(record: Any) -> str:
    return _expect_sha256(_field(record, "record_hash"), where="record_hash")


def _footprint(record: Any) -> Mapping[str, Any] | None:
    value = _field(record, "footprint")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise PreregistrationError("record footprint must be a mapping or null")
    return value


class _RecordIndex:
    """A hash -> record index over a contiguous genesis-anchored K prefix."""

    def __init__(self, records: Sequence[Any]) -> None:
        self.records = tuple(records)
        self.by_hash: dict[str, Any] = {}
        for expected_seq, record in enumerate(self.records):
            seq = _seq(record)
            if seq != expected_seq:
                raise PreregistrationError(
                    "the prefix must be a contiguous K prefix from seq 0; "
                    f"record {expected_seq} carries seq {seq}"
                )
            record_hash = _record_hash(record)
            if record_hash in self.by_hash:
                raise PreregistrationError(
                    "the prefix contains duplicate record hashes"
                )
            self.by_hash[record_hash] = record
        # tau_P: the seq the next (PREREGISTRATION) append would receive.
        self.tau = len(self.records)

    def get(self, record_hash: str) -> Any | None:
        return self.by_hash.get(record_hash)


# ---------------------------------------------------------------------------
# section 7.3 validation (checks 1-5)
# ---------------------------------------------------------------------------


def _validate_policy_and_freezes(
    prereg: PreRegistration, index: _RecordIndex
) -> EstimandPolicy:
    policy_record = index.get(prereg.estimand_policy_record)
    if policy_record is None:
        raise PreregistrationError(
            "the ESTIMAND_POLICY record is not in the preregistration-freeze "
            "prefix"
        )
    policy = estimand_policy_from_record(policy_record)
    policy_seq = _seq(policy_record)
    # The policy precedes the program's first HYPOTHESIS_FREEZE (section 7.1).
    for record in index.records:
        if _kind(record) != RecordKind.HYPOTHESIS_FREEZE:
            continue
        if _field(record, "program_id") != policy.program_id:
            continue
        if _seq(record) < policy_seq:
            raise PreregistrationError(
                "the ESTIMAND_POLICY must precede the program's first "
                "HYPOTHESIS_FREEZE"
            )
    # Every member's own freeze exists and matches the member identity.
    member_freeze_seqs: list[int] = []
    for member in prereg.members:
        freeze_record = index.get(member.hypothesis_freeze_record)
        if freeze_record is None or _kind(freeze_record) != RecordKind.HYPOTHESIS_FREEZE:
            raise PreregistrationError(
                f"member {member.hypothesis_id!r} has no HYPOTHESIS_FREEZE "
                "record in the prefix"
            )
        payload = _payload(freeze_record)
        if payload.get("hypothesis_id") != member.hypothesis_id:
            raise PreregistrationError(
                f"member {member.hypothesis_id!r} does not match its "
                "HYPOTHESIS_FREEZE hypothesis_id"
            )
        if payload.get("factor_spec_hash") != member.factor_spec_hash:
            raise PreregistrationError(
                f"member {member.hypothesis_id!r} does not match its "
                "HYPOTHESIS_FREEZE factor_spec_hash"
            )
        member_freeze_seqs.append(_seq(freeze_record))
    if member_freeze_seqs and policy_seq >= min(member_freeze_seqs):
        raise PreregistrationError(
            "the ESTIMAND_POLICY must precede every member's HYPOTHESIS_FREEZE"
        )
    return policy


def _validate_no_duplicate_members(prereg: PreRegistration) -> None:
    seen: set[tuple[str, str, str]] = set()
    for member in prereg.members:
        key = (
            member.factor_spec_hash,
            member.estimand_kind.value,
            canonical_json(_plain(member.construction)),
        )
        if key in seen:
            raise PreregistrationError(
                "duplicate member: two members share "
                "(factor_spec_hash, estimand, construction)"
            )
        seen.add(key)


def _validate_estimand_policy_equality(
    prereg: PreRegistration, policy: EstimandPolicy
) -> None:
    policy_construction = canonical_json(_plain(policy.construction))
    for member in prereg.members:
        mismatch: str | None = None
        if member.estimand_kind != policy.estimand_kind:
            mismatch = "estimand_kind"
        elif member.horizon != policy.horizon:
            mismatch = "horizon"
        elif canonical_json(_plain(member.construction)) != policy_construction:
            mismatch = "construction"
        elif float(member.sesoi) != float(policy.sesoi):
            mismatch = "sesoi"
        if mismatch is not None:
            raise PreregistrationRefused(
                ReasonCode.ESTIMAND_POLICY_VIOLATION,
                f"member {member.hypothesis_id!r} differs from the program "
                f"estimand policy in {mismatch}",
            )


def _validate_footprint_disjoint(
    prereg: PreRegistration,
    index: _RecordIndex,
    calendar: Any,
) -> None:
    body = prereg.confirmation.declared_footprint
    if body.get("determinable") is not True:
        raise PreregistrationRefused(
            ReasonCode.FOOTPRINT_OVERLAP_UNDETERMINABLE,
            "the declared confirmation footprint is not determinable",
        )
    declared = footprint_from_body(body, calendar=calendar)
    for record in index.records:
        if _kind(record) != RecordKind.CONSUMPTION:
            continue
        consumed_body = _footprint(record)
        if consumed_body is None:
            raise PreregistrationRefused(
                ReasonCode.FOOTPRINT_OVERLAP_UNDETERMINABLE,
                "a prior CONSUMPTION record carries no footprint",
            )
        consumed = footprint_from_body(consumed_body, calendar=calendar)
        result = overlap(declared, consumed)
        if result is FootprintOverlap.OVERLAP:
            raise PreregistrationRefused(
                ReasonCode.FOOTPRINT_ALREADY_CONSUMED,
                "the declared confirmation footprint overlaps a prior "
                "CONSUMPTION footprint",
            )
        if result is FootprintOverlap.UNDETERMINABLE:
            raise PreregistrationRefused(
                ReasonCode.FOOTPRINT_OVERLAP_UNDETERMINABLE,
                "the declared confirmation footprint overlap with a prior "
                "CONSUMPTION footprint is undeterminable",
            )


def _procedure_contract_for(
    registry: InferenceProcedureRegistry, procedure_ref: Mapping[str, str]
) -> Any:
    procedure = registry.get(
        procedure_ref["procedure_id"], procedure_ref["version"]
    )
    if procedure is None:
        raise PreregistrationRefused(
            ReasonCode.PROCEDURE_NOT_ADMITTED,
            f"procedure {procedure_ref['procedure_id']!r} "
            f"{procedure_ref['version']!r} is not in the registry",
        )
    if registry.production and procedure.contract.test_only:
        raise PreregistrationRefused(
            ReasonCode.PROCEDURE_NOT_ADMITTED,
            "a test_only procedure cannot be admitted in a production registry",
        )
    return procedure.contract


def _validate_member_admission(
    member: MemberContract,
    index: _RecordIndex,
    registry: InferenceProcedureRegistry,
) -> None:
    ref = dict(member.procedure_ref)
    admission_record = index.get(member.admission_record_hash)
    if admission_record is None:
        raise PreregistrationRefused(
            ReasonCode.PROCEDURE_NOT_ADMITTED,
            "the admission_record_hash is not in the preregistration-freeze "
            "prefix",
        )
    if _kind(admission_record) != RecordKind.HUMAN_DECISION:
        raise PreregistrationRefused(
            ReasonCode.PROCEDURE_NOT_ADMITTED,
            "the admission_record_hash does not name a HUMAN_DECISION record",
        )
    payload = _payload(admission_record)
    if _decision_kind(payload) != PROCEDURE_ADMISSION:
        raise PreregistrationRefused(
            ReasonCode.PROCEDURE_NOT_ADMITTED,
            "the admission record does not carry decision_kind = "
            "PROCEDURE_ADMISSION",
        )
    admitted_id = payload.get("procedure_id")
    admitted_version = payload.get("version")
    admitted_contract_hash = payload.get("contract_hash")
    if (
        not isinstance(admitted_id, str)
        or not admitted_id
        or not isinstance(admitted_version, str)
        or not admitted_version
        or not isinstance(admitted_contract_hash, str)
        or _SHA256_HEX_RE.match(admitted_contract_hash) is None
    ):
        raise PreregistrationRefused(
            ReasonCode.PROCEDURE_NOT_ADMITTED,
            "the admission record is unverifiable (missing/ill-typed fields)",
        )
    if (
        admitted_id != ref["procedure_id"]
        or admitted_version != ref["version"]
    ):
        raise PreregistrationRefused(
            ReasonCode.PROCEDURE_NOT_ADMITTED,
            "the admission record does not name the member's procedure "
            "(procedure_id/version)",
        )
    if admitted_contract_hash != ref["contract_hash"]:
        raise PreregistrationRefused(
            ReasonCode.PROCEDURE_IDENTITY_MISMATCH,
            "the admission contract_hash does not match the member's "
            "procedure_ref.contract_hash",
        )
    # No revocation of that exact version may exist in the freeze prefix.
    for record in index.records:
        if _kind(record) != RecordKind.HUMAN_DECISION:
            continue
        revocation = _payload(record)
        if _decision_kind(revocation) != PROCEDURE_REVOCATION:
            continue
        if (
            revocation.get("procedure_id") == ref["procedure_id"]
            and revocation.get("version") == ref["version"]
        ):
            raise PreregistrationRefused(
                ReasonCode.PROCEDURE_REVOKED,
                "the procedure version is revoked within the "
                "preregistration-freeze prefix",
            )
    contract = _procedure_contract_for(registry, ref)
    if (
        contract.contract_hash != ref["contract_hash"]
        or contract.contract_hash != admitted_contract_hash
    ):
        raise PreregistrationRefused(
            ReasonCode.PROCEDURE_IDENTITY_MISMATCH,
            "the registry contract_hash does not match the admission and "
            "procedure_ref",
        )
    if member.estimand_kind not in contract.supported_estimands:
        raise PreregistrationRefused(
            ReasonCode.PROCEDURE_ESTIMAND_UNSUPPORTED,
            "the admitted procedure does not support the member's estimand",
        )
    if member.missingness_policy not in contract.supported_missingness:
        raise PreregistrationRefused(
            ReasonCode.MISSINGNESS_POLICY_UNSUPPORTED,
            "the admitted procedure does not support the member's "
            "missingness policy",
        )
    if not _params_match(member.params, contract.param_schema):
        raise PreregistrationRefused(
            ReasonCode.PROCEDURE_PARAMS_INVALID,
            "the member params do not validate under the procedure "
            "param_schema",
        )


def validate_preregistration(
    prereg: PreRegistration,
    *,
    prefix: Sequence[Any],
    registry: InferenceProcedureRegistry,
    calendar: Any = None,
) -> EstimandPolicy:
    """Validate the section 7.3 refusal matrix (checks 1-5).

    ``prefix`` is the **exact** Knowledge-PIT prefix visible when the
    ``PREREGISTRATION`` record would be appended (``seq < tau_P``). It may be
    a sequence of :class:`~smart_beta.science.knowledge.KnowledgeRecord`
    objects (the ``KnowledgeLog.read()`` result) or of plain validated
    envelope mappings. Record sequence and prefix membership are the only
    admission-ordering authority; timestamps are never consulted.

    Returns the validated :class:`EstimandPolicy` (loaded from the prefix).
    Raises :class:`PreregistrationError` for a structural defect and
    :class:`PreregistrationRefused` (carrying the frozen
    :class:`~smart_beta.science.contracts.ReasonCode`) for a scientific
    refusal.
    """
    if not isinstance(prereg, PreRegistration):
        raise PreregistrationError("prereg must be a PreRegistration")
    if not isinstance(registry, InferenceProcedureRegistry):
        raise PreregistrationError(
            "registry must be an InferenceProcedureRegistry"
        )
    index = _RecordIndex(prefix)

    # Check 1 -- freezes exist and the estimand policy precedes them.
    policy = _validate_policy_and_freezes(prereg, index)
    # Check 2 -- no exact duplicates.
    _validate_no_duplicate_members(prereg)
    # Section 7.1 -- every member matches the frozen estimand policy.
    _validate_estimand_policy_equality(prereg, policy)
    # Check 3 -- declared footprint determinable and disjoint from CONSUMPTION.
    _validate_footprint_disjoint(prereg, index, calendar)
    # Check 4 -- admission ordering and procedure support.
    for member in prereg.members:
        _validate_member_admission(member, index, registry)
    # Check 5 -- no retroactivity. ``prefix`` is a contiguous genesis-anchored
    # K prefix, so every record in ``index`` has seq < tau_P = index.tau, and
    # every referenced record was looked up in ``index`` (checks 1 and 4). No
    # field can reference a record with seq >= tau_P.
    return policy


# ---------------------------------------------------------------------------
# append / replay
# ---------------------------------------------------------------------------


def append_preregistration(
    log: KnowledgeLog,
    prereg: PreRegistration,
    *,
    registry: InferenceProcedureRegistry,
    calendar: Any = None,
) -> KnowledgeRecord:
    """Validate and append the single ``PREREGISTRATION`` record (R-5a).

    ``refs.influenced_by`` = the members' ``HYPOTHESIS_FREEZE`` records plus
    the ``ESTIMAND_POLICY`` record. ``refs.consulted`` = every member's
    ``admission_record_hash`` (the mechanical binding of section 7.3 check 4).
    """
    if not isinstance(log, KnowledgeLog):
        raise PreregistrationError("log must be a KnowledgeLog")
    prefix = log.read()
    policy = validate_preregistration(
        prereg, prefix=prefix, registry=registry, calendar=calendar
    )
    influenced_by = sorted(
        {member.hypothesis_freeze_record for member in prereg.members}
        | {prereg.estimand_policy_record}
    )
    consulted = sorted(
        {member.admission_record_hash for member in prereg.members}
    )
    record = log.append(
        kind=RecordKind.PREREGISTRATION,
        program_id=policy.program_id,
        payload={
            "preregistration": prereg.to_content(),
            "preregistration_hash": prereg.prereg_id,
        },
        refs={
            "influenced_by": tuple(influenced_by),
            "consulted": tuple(consulted),
        },
    )
    expected_head = prefix[-1].record_hash if prefix else GENESIS_PREV_HASH
    if record.seq != len(prefix) or record.prev_hash != expected_head:
        raise PreregistrationError(
            "the Knowledge-PIT log changed during the preregistration append"
        )
    return record


def preregistration_from_record(record: Any) -> PreRegistration:
    """Reconstruct and verify a ``PREREGISTRATION`` record's frozen body."""
    if _kind(record) != RecordKind.PREREGISTRATION:
        raise PreregistrationError(
            "the record must be a PREREGISTRATION record"
        )
    payload = _payload(record)
    body = payload.get("preregistration")
    declared_hash = payload.get("preregistration_hash")
    if not isinstance(body, Mapping):
        raise PreregistrationError(
            "the PREREGISTRATION payload must carry a preregistration mapping"
        )
    prereg = PreRegistration.from_content(body)
    if declared_hash != prereg.prereg_id:
        raise PreregistrationError(
            "the stored preregistration_hash does not match the body"
        )
    return prereg
