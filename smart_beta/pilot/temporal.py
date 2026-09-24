"""Pilot 1A P1A-G6R: the temporal information-flow firewall (TF-1..TF-6).

This module owns **only** the harness-level temporal firewall specified by
``worker_tasks/pilot1/pilot1-plan.md`` section 26b (the P1A-G6R rework row).
It answers one question mechanically: *does every generator-visible empirical
item derive from sealed Phase-7 evidence whose temporal coverage lies wholly
inside the config's authorized development interval*
``D = [is_start, holdout_start)``?

Why a separate firewall
-----------------------

The G3 key/substring firewall (``smart_beta.pilot.firewall``) cannot see
temporal derivation. The H6-v1 evidence proved that a holdout overlap can be
carried by *harmlessly named* tables (``subperiod_stability``,
``parameter_sensitivity``) whose keys are all allowlisted. This module is the
compensating control: it derives coverage from the **sealed**
:class:`~smart_beta.evaluation.spec.EvaluationRecord` content only -- never
from a name, a guess or a denylist -- and fails closed on anything it cannot
prove.

The frozen checks (plan section 26b, TF-1..TF-6)
-----------------------------------------------

* **TF-1 authorized interval.** ``D = [is_start, holdout_start)`` from the
  config. Every source record's partition is cross-checked: the ``IS`` fold
  start must equal ``is_start`` and the ``HOLDOUT`` fold start must equal
  ``holdout_start``.
* **TF-2 coverage from sealed content.** A visible fold item maps through its
  ``fold_key`` to that record's partition ``FoldBoundary [start, end)``. A
  subperiod row maps through its own ``subperiod_start``/``subperiod_end``
  columns (half-open ``[start, end)``).
* **TF-3 containment.** Every covered item satisfies
  ``is_start <= start`` and ``end_exclusive <= holdout_start``.
* **TF-4 unknown coverage fails closed.** ``parameter_sensitivity``,
  ``universe_sensitivity``, ``redundancy`` and any unknown-named robustness
  table with rows fail; a visible item without derived coverage fails.
* **TF-5 closed schema.** The generator-visible experiment/feedback dicts must
  contain exactly the known P9-B keys; an unknown key, or an unknown-named
  table with rows, fails. The check is a positive allowlist, not a denylist.
* **TF-6 enforcement points.** :func:`enforce_temporal_firewall` raises the
  typed :class:`TemporalFirewallViolation` (whose
  :attr:`~TemporalFirewallViolation.stop_reason` is
  :data:`~smart_beta.research.policy.StopReason.HOLDOUT_FIREWALL_VIOLATION`)
  for the runner's pre-call check. :func:`audit_temporal_firewall` returns the
  structured audit (one row per item) for the post-hoc G6 artifact.

The module imports the read-only sealed Phase-7 record contract, the read-only
Phase-9 visible-projection contract and the P1A-C canonical-hash convention.
It performs no I/O, no network access, no model call and no credential access.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from smart_beta.evaluation.spec import EvaluationRecord, FoldRole
from smart_beta.pilot.contracts import PilotContractError, canonical_json
from smart_beta.research.history import (
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
)
from smart_beta.research.policy import StopReason

__all__ = [
    "TEMPORAL_FIREWALL_SCHEMA_VERSION",
    "COVERAGE_DECLARABLE_TABLES",
    "FORBIDDEN_COVERAGE_TABLES",
    "TemporalFirewallError",
    "TemporalFirewallViolation",
    "TemporalVerdict",
    "TemporalCoverageRow",
    "TemporalFirewallAudit",
    "TemporalFirewallReport",
    "audit_temporal_firewall",
    "enforce_temporal_firewall",
]

#: The frozen temporal-firewall audit schema version.
TEMPORAL_FIREWALL_SCHEMA_VERSION = "pilot1a/temporal-firewall/v1"

#: Robustness-table names whose rows carry their own row-level temporal
#: provenance and may therefore be coverage-declared.
COVERAGE_DECLARABLE_TABLES: frozenset[str] = frozenset({"subperiod_stability"})

#: Robustness-table names with no row-level temporal provenance: any row fails.
FORBIDDEN_COVERAGE_TABLES: frozenset[str] = frozenset(
    {"parameter_sensitivity", "universe_sensitivity"}
)

#: The subperiod table identity and its row-level coverage columns.
SUBPERIOD_TABLE_NAME = "subperiod_stability"
SUBPERIOD_KEY_COLUMN = "subperiod_key"
SUBPERIOD_START_COLUMN = "subperiod_start"
SUBPERIOD_END_COLUMN = "subperiod_end"

#: The closed P9-B key vocabularies (positive allowlists, not denylists).
_VISIBLE_HISTORY_KEYS: frozenset[str] = frozenset(
    {"proposals", "experiments", "families", "content_hash"}
)
_VISIBLE_EXPERIMENT_KEYS: frozenset[str] = frozenset(
    {
        "experiment_id",
        "hypothesis_id",
        "family_id",
        "attempt_index",
        "parent_experiment_id",
        "proposal_id",
        "factor_provenance_hash",
        "evaluation_spec_hash",
        "fold_evidence",
        "redundancy",
        "robustness_tables",
        "search_status",
    }
)
_FOLD_EVIDENCE_KEYS: frozenset[str] = frozenset({"fold_key", "role", "metrics"})
_EVIDENCE_TABLE_KEYS: frozenset[str] = frozenset({"name", "columns", "rows"})
_REDUNDANCY_KEYS: frozenset[str] = frozenset(
    {"reference_key", "method", "value", "n_obs"}
)
_FEEDBACK_KEYS: frozenset[str] = frozenset({"experiments", "content_hash"})
_FEEDBACK_EXPERIMENT_KEYS: frozenset[str] = frozenset(
    {
        "experiment_id",
        "hypothesis_id",
        "factor_provenance_hash",
        "evaluation_spec_hash",
        "is_folds",
        "oos_folds",
        "walk_forward_folds",
        "robustness_tables",
        "redundancy",
        "search_status",
        "reason_classes",
    }
)
#: The feedback fold-list fields and their implied (development) roles.
_FEEDBACK_FOLD_FIELDS: tuple[str, ...] = (
    "is_folds",
    "oos_folds",
    "walk_forward_folds",
)


class TemporalFirewallError(ValueError):
    """Base class for temporal-firewall contract/programming violations."""


class TemporalFirewallViolation(TemporalFirewallError):
    """The generator-visible evidence is not temporally firewalled (fail closed).

    The typed :attr:`stop_reason` is the frozen
    :data:`~smart_beta.research.policy.StopReason.HOLDOUT_FIREWALL_VIOLATION`
    path: the runner must stop the loop before any external call.
    """

    #: The frozen typed stop this violation maps to.
    stop_reason = StopReason.HOLDOUT_FIREWALL_VIOLATION

    def __init__(
        self,
        message: str,
        *,
        audit: "TemporalFirewallAudit | None" = None,
        findings: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.audit = audit
        self.findings = tuple(findings)


class TemporalVerdict(str, Enum):
    """The frozen PASS / FAIL disposition of one item or one audit."""

    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class TemporalCoverageRow:
    """One structured temporal-firewall audit row (plan section 26b TF-6).

    The five frozen identity fields are ``experiment_id``,
    ``evaluation_record_hash``, ``category``, ``item_key`` and the two
    coverage pairs; ``carrier`` records which generator-visible object carried
    the item and ``detail`` explains a failure.
    """

    experiment_id: str | None
    evaluation_record_hash: str | None
    category: str
    item_key: str
    source_start: str | None
    source_end_exclusive: str | None
    authorized_start: str
    authorized_end_exclusive: str
    verdict: TemporalVerdict
    carrier: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "evaluation_record_hash": self.evaluation_record_hash,
            "category": self.category,
            "item_key": self.item_key,
            "source_start": self.source_start,
            "source_end_exclusive": self.source_end_exclusive,
            "authorized_start": self.authorized_start,
            "authorized_end_exclusive": self.authorized_end_exclusive,
            "verdict": self.verdict.value,
            "carrier": self.carrier,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class TemporalFirewallAudit:
    """The structured temporal audit of one generator-visible snapshot pair."""

    status: TemporalVerdict
    authorized_start: str | None
    authorized_end_exclusive: str | None
    rows: tuple[TemporalCoverageRow, ...] = ()
    findings: tuple[str, ...] = ()
    evaluated_records: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is TemporalVerdict.PASS

    @property
    def failures(self) -> tuple[TemporalCoverageRow, ...]:
        return tuple(row for row in self.rows if row.verdict is TemporalVerdict.FAIL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TEMPORAL_FIREWALL_SCHEMA_VERSION,
            "status": self.status.value,
            "authorized_start": self.authorized_start,
            "authorized_end_exclusive": self.authorized_end_exclusive,
            "item_count": len(self.rows),
            "failure_count": len(self.failures),
            "evaluated_records": list(self.evaluated_records),
            "findings": list(self.findings),
            "rows": [row.to_dict() for row in self.rows],
        }


@dataclass(frozen=True)
class TemporalFirewallReport:
    """The post-hoc temporal audit of every journaled generator-visible snapshot.

    Aggregates one :class:`TemporalFirewallAudit` per distinct
    ``visible_history`` / ``research_feedback`` authority snapshot. The
    flattened :attr:`rows` list is the fail-closed package evidence.
    """

    status: TemporalVerdict
    authorized_start: str | None
    authorized_end_exclusive: str | None
    audits: tuple[TemporalFirewallAudit, ...] = ()
    findings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is TemporalVerdict.PASS

    @property
    def rows(self) -> tuple[TemporalCoverageRow, ...]:
        return tuple(row for audit in self.audits for row in audit.rows)

    @property
    def failures(self) -> tuple[TemporalCoverageRow, ...]:
        return tuple(
            row for row in self.rows if row.verdict is TemporalVerdict.FAIL
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TEMPORAL_FIREWALL_SCHEMA_VERSION,
            "status": self.status.value,
            "authorized_start": self.authorized_start,
            "authorized_end_exclusive": self.authorized_end_exclusive,
            "audit_count": len(self.audits),
            "item_count": len(self.rows),
            "failure_count": len(self.failures),
            "findings": list(self.findings),
            "rows": [row.to_dict() for row in self.rows],
        }


# ---------------------------------------------------------------------------
# coercion helpers
# ---------------------------------------------------------------------------


def _coerce_date(value: Any, *, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise TemporalFirewallError(
                f"{field_name} must be an ISO date, got {value!r}"
            ) from exc
    raise TemporalFirewallError(
        f"{field_name} must be an ISO date string or datetime.date, got "
        f"{type(value).__name__}"
    )


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _as_payload(
    value: Any,
    typed_cls: type,
    *,
    carrier: str,
    rows: list[TemporalCoverageRow],
    findings: list[str],
    authorized_start: str,
    authorized_end_exclusive: str,
) -> Mapping[str, Any]:
    """Normalize a typed projection or a raw mapping to its ``to_dict`` form."""
    if value is None:
        return typed_cls().to_dict()
    if isinstance(value, typed_cls):
        return value.to_dict()
    if isinstance(value, Mapping):
        return dict(value)
    rows.append(
        TemporalCoverageRow(
            experiment_id=None,
            evaluation_record_hash=None,
            category="schema",
            item_key=carrier,
            source_start=None,
            source_end_exclusive=None,
            authorized_start=authorized_start,
            authorized_end_exclusive=authorized_end_exclusive,
            verdict=TemporalVerdict.FAIL,
            carrier=carrier,
            detail=(
                f"{carrier} must be a {typed_cls.__name__} or its serialized "
                f"mapping form, got {type(value).__name__}"
            ),
        )
    )
    findings.append(
        f"{carrier} is not a {typed_cls.__name__} or a mapping"
    )
    return {}


def _as_items(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


# ---------------------------------------------------------------------------
# closed-schema checks (TF-5)
# ---------------------------------------------------------------------------


def _record_schema_failure(
    *,
    rows: list[TemporalCoverageRow],
    findings: list[str],
    carrier: str,
    context: str,
    key: str,
    experiment_id: str | None,
    record_hash: str | None,
    authorized_start: str,
    authorized_end_exclusive: str,
    detail: str,
) -> None:
    rows.append(
        TemporalCoverageRow(
            experiment_id=experiment_id,
            evaluation_record_hash=record_hash,
            category="schema",
            item_key=f"{context}.{key}",
            source_start=None,
            source_end_exclusive=None,
            authorized_start=authorized_start,
            authorized_end_exclusive=authorized_end_exclusive,
            verdict=TemporalVerdict.FAIL,
            carrier=carrier,
            detail=detail,
        )
    )
    findings.append(f"{context}: {detail}")


def _check_closed_keys(
    payload: Any,
    known: frozenset[str],
    *,
    context: str,
    carrier: str,
    experiment_id: str | None,
    record_hash: str | None,
    rows: list[TemporalCoverageRow],
    findings: list[str],
    authorized_start: str,
    authorized_end_exclusive: str,
) -> None:
    if not isinstance(payload, Mapping):
        _record_schema_failure(
            rows=rows,
            findings=findings,
            carrier=carrier,
            context=context,
            key="<type>",
            experiment_id=experiment_id,
            record_hash=record_hash,
            authorized_start=authorized_start,
            authorized_end_exclusive=authorized_end_exclusive,
            detail=(
                f"{context} must be a mapping, got {type(payload).__name__}"
            ),
        )
        return
    for key in sorted(set(payload) - known):
        _record_schema_failure(
            rows=rows,
            findings=findings,
            carrier=carrier,
            context=context,
            key=str(key),
            experiment_id=experiment_id,
            record_hash=record_hash,
            authorized_start=authorized_start,
            authorized_end_exclusive=authorized_end_exclusive,
            detail=(
                f"unknown P9-B key {key!r}; the generator-visible schema is "
                "closed"
            ),
        )


# ---------------------------------------------------------------------------
# record index / partition cross-check (TF-1)
# ---------------------------------------------------------------------------


def _coerce_evaluation_records(
    evaluation_records: Any,
    *,
    rows: list[TemporalCoverageRow],
    findings: list[str],
    authorized_start: str,
    authorized_end_exclusive: str,
) -> list[EvaluationRecord]:
    if evaluation_records is None:
        return []
    if isinstance(evaluation_records, Mapping):
        values = list(evaluation_records.values())
    elif isinstance(evaluation_records, Sequence) and not isinstance(
        evaluation_records, (str, bytes, bytearray)
    ):
        values = list(evaluation_records)
    elif isinstance(evaluation_records, Iterable):
        values = list(evaluation_records)
    else:
        raise TemporalFirewallError(
            "evaluation_records must be a mapping or an iterable of "
            f"EvaluationRecord, got {type(evaluation_records).__name__}"
        )
    records: list[EvaluationRecord] = []
    for item in values:
        if isinstance(item, EvaluationRecord):
            records.append(item)
        elif isinstance(item, Mapping):
            try:
                records.append(EvaluationRecord.from_dict(item))
            except Exception as exc:  # noqa: BLE001 - reported as a finding
                findings.append(
                    "journaled/derived EvaluationRecord is not rebuildable: "
                    f"{type(exc).__name__}: {exc}"
                )
                rows.append(
                    TemporalCoverageRow(
                        experiment_id=None,
                        evaluation_record_hash=None,
                        category="record",
                        item_key="<unparseable>",
                        source_start=None,
                        source_end_exclusive=None,
                        authorized_start=authorized_start,
                        authorized_end_exclusive=authorized_end_exclusive,
                        verdict=TemporalVerdict.FAIL,
                        carrier="evaluation_record",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )
        else:
            raise TemporalFirewallError(
                "evaluation_records entries must be EvaluationRecord or their "
                f"serialized mappings, got {type(item).__name__}"
            )
    return records


def _index_by_spec_hash(
    records: Sequence[EvaluationRecord],
    *,
    rows: list[TemporalCoverageRow],
    findings: list[str],
    authorized_start: str,
    authorized_end_exclusive: str,
) -> dict[str, EvaluationRecord]:
    index: dict[str, EvaluationRecord] = {}
    for record in records:
        existing = index.get(record.spec_hash)
        if existing is not None and existing.content_hash != record.content_hash:
            findings.append(
                "two distinct EvaluationRecords share spec_hash "
                f"{record.spec_hash!r}; coverage cannot be derived unambiguously"
            )
            rows.append(
                TemporalCoverageRow(
                    experiment_id=None,
                    evaluation_record_hash=None,
                    category="record",
                    item_key=record.spec_hash,
                    source_start=None,
                    source_end_exclusive=None,
                    authorized_start=authorized_start,
                    authorized_end_exclusive=authorized_end_exclusive,
                    verdict=TemporalVerdict.FAIL,
                    carrier="evaluation_record",
                    detail="ambiguous spec_hash",
                )
            )
            continue
        index[record.spec_hash] = record
    return index


def _check_partition(
    record: EvaluationRecord,
    *,
    authorized_start: date,
    holdout_start: date,
    rows: list[TemporalCoverageRow],
    findings: list[str],
) -> None:
    authorized_start_text = authorized_start.isoformat()
    authorized_end_text = holdout_start.isoformat()
    is_folds = [fold for fold in record.partition.folds if fold.role is FoldRole.IS]
    holdout_folds = [
        fold for fold in record.partition.folds if fold.role is FoldRole.HOLDOUT
    ]
    if not is_folds:
        findings.append(
            f"EvaluationRecord {record.content_hash} carries no IS fold"
        )
    if not holdout_folds:
        findings.append(
            f"EvaluationRecord {record.content_hash} carries no HOLDOUT fold"
        )
    for fold in is_folds:
        start = _iso(fold.start)
        verdict = (
            TemporalVerdict.PASS
            if fold.start == authorized_start
            else TemporalVerdict.FAIL
        )
        rows.append(
            TemporalCoverageRow(
                experiment_id=None,
                evaluation_record_hash=record.content_hash,
                category="authorized_interval_is_start",
                item_key=fold.fold_key,
                source_start=start,
                source_end_exclusive=_iso(fold.end),
                authorized_start=authorized_start_text,
                authorized_end_exclusive=authorized_end_text,
                verdict=verdict,
                carrier="evaluation_record",
                detail=(
                    ""
                    if verdict is TemporalVerdict.PASS
                    else (
                        "IS fold start does not equal the authorized is_start "
                        f"({start} != {authorized_start_text})"
                    )
                ),
            )
        )
    for fold in holdout_folds:
        start = _iso(fold.start)
        verdict = (
            TemporalVerdict.PASS
            if fold.start == holdout_start
            else TemporalVerdict.FAIL
        )
        rows.append(
            TemporalCoverageRow(
                experiment_id=None,
                evaluation_record_hash=record.content_hash,
                category="authorized_interval_holdout_start",
                item_key=fold.fold_key,
                source_start=start,
                source_end_exclusive=_iso(fold.end),
                authorized_start=authorized_start_text,
                authorized_end_exclusive=authorized_end_text,
                verdict=verdict,
                carrier="evaluation_record",
                detail=(
                    ""
                    if verdict is TemporalVerdict.PASS
                    else (
                        "HOLDOUT fold start does not equal the authorized "
                        f"holdout_start ({start} != {authorized_end_text})"
                    )
                ),
            )
        )


# ---------------------------------------------------------------------------
# per-item coverage checks (TF-2/TF-3/TF-4)
# ---------------------------------------------------------------------------


def _containment_verdict(
    source_start: date,
    source_end: date,
    *,
    authorized_start: date,
    holdout_start: date,
) -> tuple[TemporalVerdict, str]:
    if source_start >= source_end:
        return (
            TemporalVerdict.FAIL,
            f"coverage interval is empty or inverted [{source_start}, {source_end})",
        )
    if source_start < authorized_start:
        return (
            TemporalVerdict.FAIL,
            (
                f"coverage start {source_start} precedes the authorized "
                f"is_start {authorized_start}"
            ),
        )
    if source_end > holdout_start:
        return (
            TemporalVerdict.FAIL,
            (
                f"coverage end {source_end} exceeds the authorized "
                f"holdout_start {holdout_start}"
            ),
        )
    return TemporalVerdict.PASS, ""


def _coverage_row(
    *,
    experiment_id: str | None,
    record_hash: str | None,
    category: str,
    item_key: str,
    source_start: date | None,
    source_end: date | None,
    verdict: TemporalVerdict,
    carrier: str,
    detail: str,
    authorized_start: date,
    holdout_start: date,
) -> TemporalCoverageRow:
    return TemporalCoverageRow(
        experiment_id=experiment_id,
        evaluation_record_hash=record_hash,
        category=category,
        item_key=item_key,
        source_start=_iso(source_start),
        source_end_exclusive=_iso(source_end),
        authorized_start=authorized_start.isoformat(),
        authorized_end_exclusive=holdout_start.isoformat(),
        verdict=verdict,
        carrier=carrier,
        detail=detail,
    )


def _metric_signature(metric: Any) -> str | None:
    """Canonical JSON signature of one sealed/visible metric mapping."""
    if not isinstance(metric, Mapping):
        return None
    try:
        return canonical_json(metric)
    except PilotContractError:
        return None


def _metrics_signature(metrics: Any) -> tuple[str, ...] | None:
    """Order-insensitive canonical signature of a metric list.

    ``None`` means the value is not a well-formed metric sequence; any such
    value fails closed. The signature is a sorted tuple so the frozen
    constructors' name ordering is not content.
    """
    if not isinstance(metrics, Sequence) or isinstance(
        metrics, (str, bytes, bytearray)
    ):
        return None
    signatures: list[str] = []
    for metric in metrics:
        signature = _metric_signature(metric)
        if signature is None:
            return None
        signatures.append(signature)
    return tuple(sorted(signatures))


def _sealed_fold_metrics(record: EvaluationRecord, fold_key: Any) -> tuple[Any, ...] | None:
    """The matched sealed ``FoldResult`` metrics for ``fold_key``, if any."""
    for fold_result in record.fold_results:
        if fold_result.fold_key == fold_key:
            return tuple(metric.to_dict() for metric in fold_result.metrics)
    return None


def _sealed_fold_role(record: EvaluationRecord, fold_key: Any) -> FoldRole | None:
    for fold_result in record.fold_results:
        if fold_result.fold_key == fold_key:
            return fold_result.role
    return None


def _row_as_mapping(columns: Sequence[Any], row: Any) -> dict[str, Any] | None:
    """Zip one visible table row onto its declared columns, or ``None``."""
    if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)):
        return None
    if len(row) != len(columns):
        return None
    return {str(column): row[index] for index, column in enumerate(columns)}


def _audit_fold_items(
    items: Any,
    *,
    carrier: str,
    experiment_id: str | None,
    record: EvaluationRecord | None,
    rows: list[TemporalCoverageRow],
    findings: list[str],
    authorized_start: date,
    holdout_start: date,
) -> None:
    for index, item in enumerate(_as_items(items)):
        context = f"{carrier}.fold_evidence[{index}]"
        _check_closed_keys(
            item,
            _FOLD_EVIDENCE_KEYS,
            context=context,
            carrier=carrier,
            experiment_id=experiment_id,
            record_hash=(None if record is None else record.content_hash),
            rows=rows,
            findings=findings,
            authorized_start=authorized_start.isoformat(),
            authorized_end_exclusive=holdout_start.isoformat(),
        )
        if not isinstance(item, Mapping):
            continue
        fold_key = item.get("fold_key")
        if record is None:
            rows.append(
                _coverage_row(
                    experiment_id=experiment_id,
                    record_hash=None,
                    category="fold",
                    item_key=str(fold_key),
                    source_start=None,
                    source_end=None,
                    verdict=TemporalVerdict.FAIL,
                    carrier=carrier,
                    detail="no source EvaluationRecord for this fold item",
                    authorized_start=authorized_start,
                    holdout_start=holdout_start,
                )
            )
            continue
        fold = next(
            (
                candidate
                for candidate in record.partition.folds
                if candidate.fold_key == fold_key
            ),
            None,
        )
        if fold is None:
            rows.append(
                _coverage_row(
                    experiment_id=experiment_id,
                    record_hash=record.content_hash,
                    category="fold",
                    item_key=str(fold_key),
                    source_start=None,
                    source_end=None,
                    verdict=TemporalVerdict.FAIL,
                    carrier=carrier,
                    detail=(
                        f"fold_key {fold_key!r} is not a fold of the source "
                        "EvaluationRecord partition"
                    ),
                    authorized_start=authorized_start,
                    holdout_start=holdout_start,
                )
            )
            continue
        verdict, detail = _containment_verdict(
            fold.start,
            fold.end,
            authorized_start=authorized_start,
            holdout_start=holdout_start,
        )
        failure_details: list[str] = []
        sealed_role = _sealed_fold_role(record, fold_key)
        sealed_metrics = _sealed_fold_metrics(record, fold_key)
        if sealed_role is None or sealed_metrics is None:
            verdict = TemporalVerdict.FAIL
            failure_details.append(
                f"fold_key {fold_key!r} has no matching sealed fold_results entry"
            )
        else:
            if sealed_role is FoldRole.HOLDOUT:
                verdict = TemporalVerdict.FAIL
                failure_details.append(
                    "matched sealed fold_results entry is the reserved "
                    "HOLDOUT role"
                )
            visible_signature = _metrics_signature(item.get("metrics"))
            sealed_signature = _metrics_signature(sealed_metrics)
            if (
                visible_signature is None
                or sealed_signature is None
                or visible_signature != sealed_signature
            ):
                verdict = TemporalVerdict.FAIL
                failure_details.append(
                    "visible fold metrics do not exactly equal the matched "
                    "sealed fold_results metrics"
                )
        if failure_details:
            detail = "; ".join(
                part for part in (detail, *failure_details) if part
            )
        rows.append(
            _coverage_row(
                experiment_id=experiment_id,
                record_hash=record.content_hash,
                category="fold",
                item_key=str(fold_key),
                source_start=fold.start,
                source_end=fold.end,
                verdict=verdict,
                carrier=carrier,
                detail=detail,
                authorized_start=authorized_start,
                holdout_start=holdout_start,
            )
        )


def _audit_redundancy(
    items: Any,
    *,
    carrier: str,
    experiment_id: str | None,
    record: EvaluationRecord | None,
    rows: list[TemporalCoverageRow],
    findings: list[str],
    authorized_start: date,
    holdout_start: date,
) -> None:
    record_hash = None if record is None else record.content_hash
    for index, item in enumerate(_as_items(items)):
        context = f"{carrier}.redundancy[{index}]"
        _check_closed_keys(
            item,
            _REDUNDANCY_KEYS,
            context=context,
            carrier=carrier,
            experiment_id=experiment_id,
            record_hash=record_hash,
            rows=rows,
            findings=findings,
            authorized_start=authorized_start.isoformat(),
            authorized_end_exclusive=holdout_start.isoformat(),
        )
        item_key = (
            str(item.get("reference_key"))
            if isinstance(item, Mapping)
            else str(index)
        )
        rows.append(
            _coverage_row(
                experiment_id=experiment_id,
                record_hash=record_hash,
                category="redundancy",
                item_key=item_key,
                source_start=None,
                source_end=None,
                verdict=TemporalVerdict.FAIL,
                carrier=carrier,
                detail=(
                    "redundancy has no row-level temporal provenance and must "
                    "be empty"
                ),
                authorized_start=authorized_start,
                holdout_start=holdout_start,
            )
        )


def _audit_subperiod_table(
    table: Mapping[str, Any],
    *,
    carrier: str,
    experiment_id: str | None,
    record: EvaluationRecord | None,
    rows: list[TemporalCoverageRow],
    findings: list[str],
    authorized_start: date,
    holdout_start: date,
) -> None:
    record_hash = None if record is None else record.content_hash
    if record is None:
        rows.append(
            _coverage_row(
                experiment_id=experiment_id,
                record_hash=None,
                category="subperiod",
                item_key=SUBPERIOD_TABLE_NAME,
                source_start=None,
                source_end=None,
                verdict=TemporalVerdict.FAIL,
                carrier=carrier,
                detail="no source EvaluationRecord to match subperiod rows against",
                authorized_start=authorized_start,
                holdout_start=holdout_start,
            )
        )
        return

    sealed_table = record.subperiod_table
    sealed_columns = tuple(sealed_table.columns)
    if not all(
        column in sealed_columns
        for column in (
            SUBPERIOD_KEY_COLUMN,
            SUBPERIOD_START_COLUMN,
            SUBPERIOD_END_COLUMN,
        )
    ):
        rows.append(
            _coverage_row(
                experiment_id=experiment_id,
                record_hash=record_hash,
                category="subperiod",
                item_key=SUBPERIOD_TABLE_NAME,
                source_start=None,
                source_end=None,
                verdict=TemporalVerdict.FAIL,
                carrier=carrier,
                detail=(
                    "the matched sealed record's subperiod_table lacks one of "
                    f"{SUBPERIOD_KEY_COLUMN}/{SUBPERIOD_START_COLUMN}/"
                    f"{SUBPERIOD_END_COLUMN}; coverage cannot be derived"
                ),
                authorized_start=authorized_start,
                holdout_start=holdout_start,
            )
        )
        findings.append(
            "sealed subperiod_table lacks row-level temporal coverage columns"
        )
        return

    sealed_by_signature: dict[str, Mapping[str, Any]] = {}
    for sealed_row in sealed_table.rows:
        sealed_mapping = _row_as_mapping(sealed_columns, sealed_row)
        if sealed_mapping is None:
            continue
        try:
            signature = canonical_json(sealed_mapping)
        except PilotContractError:  # pragma: no cover - sealed cells are scalars
            continue
        sealed_by_signature.setdefault(signature, sealed_mapping)

    visible_columns = _as_items(table.get("columns"))
    visible_rows = _as_items(table.get("rows"))
    columns_match = tuple(visible_columns) == sealed_columns

    for index, row in enumerate(visible_rows):
        mapping = _row_as_mapping(visible_columns, row)
        if mapping is None or not columns_match:
            item_key = (
                str(mapping.get(SUBPERIOD_KEY_COLUMN))
                if mapping is not None
                and SUBPERIOD_KEY_COLUMN in mapping
                else str(index)
            )
            rows.append(
                _coverage_row(
                    experiment_id=experiment_id,
                    record_hash=record_hash,
                    category="subperiod",
                    item_key=item_key,
                    source_start=None,
                    source_end=None,
                    verdict=TemporalVerdict.FAIL,
                    carrier=carrier,
                    detail=(
                        "visible subperiod row does not exactly match any row "
                        "of the matched sealed record's subperiod_table "
                        "(columns/arity differ)"
                    ),
                    authorized_start=authorized_start,
                    holdout_start=holdout_start,
                )
            )
            continue

        try:
            signature = canonical_json(mapping)
        except PilotContractError:
            signature = None
        item_key = str(mapping.get(SUBPERIOD_KEY_COLUMN, index))
        sealed_match = (
            None if signature is None else sealed_by_signature.get(signature)
        )
        if sealed_match is None:
            rows.append(
                _coverage_row(
                    experiment_id=experiment_id,
                    record_hash=record_hash,
                    category="subperiod",
                    item_key=item_key,
                    source_start=None,
                    source_end=None,
                    verdict=TemporalVerdict.FAIL,
                    carrier=carrier,
                    detail=(
                        "visible subperiod row is absent from the matched "
                        "sealed record's subperiod_table; coverage is never "
                        "taken from the visible row"
                    ),
                    authorized_start=authorized_start,
                    holdout_start=holdout_start,
                )
            )
            continue

        try:
            source_start = _coerce_date(
                sealed_match[SUBPERIOD_START_COLUMN],
                field_name="subperiod_start",
            )
            source_end = _coerce_date(
                sealed_match[SUBPERIOD_END_COLUMN],
                field_name="subperiod_end",
            )
        except TemporalFirewallError as exc:
            rows.append(
                _coverage_row(
                    experiment_id=experiment_id,
                    record_hash=record_hash,
                    category="subperiod",
                    item_key=item_key,
                    source_start=None,
                    source_end=None,
                    verdict=TemporalVerdict.FAIL,
                    carrier=carrier,
                    detail=str(exc),
                    authorized_start=authorized_start,
                    holdout_start=holdout_start,
                )
            )
            continue
        verdict, detail = _containment_verdict(
            source_start,
            source_end,
            authorized_start=authorized_start,
            holdout_start=holdout_start,
        )
        rows.append(
            _coverage_row(
                experiment_id=experiment_id,
                record_hash=record_hash,
                category="subperiod",
                item_key=item_key,
                source_start=source_start,
                source_end=source_end,
                verdict=verdict,
                carrier=carrier,
                detail=detail,
                authorized_start=authorized_start,
                holdout_start=holdout_start,
            )
        )


def _audit_robustness_tables(
    items: Any,
    *,
    carrier: str,
    experiment_id: str | None,
    record: EvaluationRecord | None,
    rows: list[TemporalCoverageRow],
    findings: list[str],
    authorized_start: date,
    holdout_start: date,
) -> None:
    record_hash = None if record is None else record.content_hash
    for index, table in enumerate(_as_items(items)):
        context = f"{carrier}.robustness_tables[{index}]"
        _check_closed_keys(
            table,
            _EVIDENCE_TABLE_KEYS,
            context=context,
            carrier=carrier,
            experiment_id=experiment_id,
            record_hash=record_hash,
            rows=rows,
            findings=findings,
            authorized_start=authorized_start.isoformat(),
            authorized_end_exclusive=holdout_start.isoformat(),
        )
        if not isinstance(table, Mapping):
            continue
        name = table.get("name")
        table_rows = _as_items(table.get("rows"))
        if name == SUBPERIOD_TABLE_NAME:
            _audit_subperiod_table(
                table,
                carrier=carrier,
                experiment_id=experiment_id,
                record=record,
                rows=rows,
                findings=findings,
                authorized_start=authorized_start,
                holdout_start=holdout_start,
            )
        elif name in FORBIDDEN_COVERAGE_TABLES:
            if table_rows:
                rows.append(
                    _coverage_row(
                        experiment_id=experiment_id,
                        record_hash=record_hash,
                        category=str(name),
                        item_key=str(name),
                        source_start=None,
                        source_end=None,
                        verdict=TemporalVerdict.FAIL,
                        carrier=carrier,
                        detail=(
                            f"{name} has {len(table_rows)} row(s) and no "
                            "row-level temporal provenance; it must be empty"
                        ),
                        authorized_start=authorized_start,
                        holdout_start=holdout_start,
                    )
                )
                findings.append(
                    f"{context}: {name} is non-empty and cannot be "
                    "coverage-declared"
                )
        elif name in COVERAGE_DECLARABLE_TABLES:  # pragma: no cover - only subperiod
            _audit_subperiod_table(
                table,
                carrier=carrier,
                experiment_id=experiment_id,
                record=record,
                rows=rows,
                findings=findings,
                authorized_start=authorized_start,
                holdout_start=holdout_start,
            )
        elif table_rows:
            rows.append(
                _coverage_row(
                    experiment_id=experiment_id,
                    record_hash=record_hash,
                    category="unknown_table",
                    item_key=str(name),
                    source_start=None,
                    source_end=None,
                    verdict=TemporalVerdict.FAIL,
                    carrier=carrier,
                    detail=(
                        f"robustness table {name!r} has {len(table_rows)} "
                        "row(s) and is not in the coverage-declarable set "
                        f"{sorted(COVERAGE_DECLARABLE_TABLES)}; it fails closed"
                    ),
                    authorized_start=authorized_start,
                    holdout_start=holdout_start,
                )
            )
            findings.append(
                f"{context}: unknown robustness table {name!r} with rows"
            )


def _resolve_experiment_record(
    experiment: Mapping[str, Any],
    *,
    index: Mapping[str, EvaluationRecord],
    carrier: str,
    rows: list[TemporalCoverageRow],
    findings: list[str],
    authorized_start: date,
    holdout_start: date,
) -> EvaluationRecord | None:
    experiment_id = experiment.get("experiment_id")
    spec_hash = experiment.get("evaluation_spec_hash")
    if not isinstance(spec_hash, str) or not spec_hash:
        rows.append(
            _coverage_row(
                experiment_id=(None if experiment_id is None else str(experiment_id)),
                record_hash=None,
                category="coverage",
                item_key=str(experiment_id),
                source_start=None,
                source_end=None,
                verdict=TemporalVerdict.FAIL,
                carrier=carrier,
                detail=(
                    "visible experiment carries no evaluation_spec_hash, so no "
                    "source EvaluationRecord coverage can be derived"
                ),
                authorized_start=authorized_start,
                holdout_start=holdout_start,
            )
        )
        findings.append(
            f"{carrier}: experiment {experiment_id!r} lacks evaluation_spec_hash"
        )
        return None
    record = index.get(spec_hash)
    if record is None:
        rows.append(
            _coverage_row(
                experiment_id=(None if experiment_id is None else str(experiment_id)),
                record_hash=None,
                category="coverage",
                item_key=str(experiment_id),
                source_start=None,
                source_end=None,
                verdict=TemporalVerdict.FAIL,
                carrier=carrier,
                detail=(
                    f"no source EvaluationRecord with spec_hash {spec_hash} was "
                    "supplied; coverage cannot be derived"
                ),
                authorized_start=authorized_start,
                holdout_start=holdout_start,
            )
        )
        findings.append(
            f"{carrier}: no EvaluationRecord matches experiment "
            f"{experiment_id!r} (spec_hash {spec_hash})"
        )
        return None
    declared_provenance = experiment.get("factor_provenance_hash")
    if (
        isinstance(declared_provenance, str)
        and declared_provenance != record.factor_provenance_hash
    ):
        rows.append(
            _coverage_row(
                experiment_id=(None if experiment_id is None else str(experiment_id)),
                record_hash=record.content_hash,
                category="coverage",
                item_key=str(experiment_id),
                source_start=None,
                source_end=None,
                verdict=TemporalVerdict.FAIL,
                carrier=carrier,
                detail=(
                    "visible factor_provenance_hash does not match the source "
                    f"EvaluationRecord ({declared_provenance} != "
                    f"{record.factor_provenance_hash})"
                ),
                authorized_start=authorized_start,
                holdout_start=holdout_start,
            )
        )
        findings.append(
            f"{carrier}: experiment {experiment_id!r} provenance mismatch"
        )
        return None
    return record


def _audit_experiment(
    experiment: Any,
    *,
    carrier: str,
    known_keys: frozenset[str],
    fold_fields: tuple[str, ...],
    index: Mapping[str, EvaluationRecord],
    rows: list[TemporalCoverageRow],
    findings: list[str],
    authorized_start: date,
    holdout_start: date,
) -> None:
    context = f"{carrier}.experiments"
    _check_closed_keys(
        experiment,
        known_keys,
        context=context,
        carrier=carrier,
        experiment_id=(
            None
            if not isinstance(experiment, Mapping)
            else _optional_text(experiment.get("experiment_id"))
        ),
        record_hash=None,
        rows=rows,
        findings=findings,
        authorized_start=authorized_start.isoformat(),
        authorized_end_exclusive=holdout_start.isoformat(),
    )
    if not isinstance(experiment, Mapping):
        return
    experiment_id = _optional_text(experiment.get("experiment_id"))
    record = _resolve_experiment_record(
        experiment,
        index=index,
        carrier=carrier,
        rows=rows,
        findings=findings,
        authorized_start=authorized_start,
        holdout_start=holdout_start,
    )
    if record is None:
        # Without a source record every item lacks derived coverage. Emit one
        # fail-closed finding per fold/table/redundancy item so the evidence
        # stays item-complete even on the unresolved path.
        for field in fold_fields:
            for item in _as_items(experiment.get(field)):
                item_key = (
                    str(item.get("fold_key"))
                    if isinstance(item, Mapping)
                    else "?"
                )
                rows.append(
                    _coverage_row(
                        experiment_id=experiment_id,
                        record_hash=None,
                        category="fold",
                        item_key=item_key,
                        source_start=None,
                        source_end=None,
                        verdict=TemporalVerdict.FAIL,
                        carrier=carrier,
                        detail="no source EvaluationRecord for this fold item",
                        authorized_start=authorized_start,
                        holdout_start=holdout_start,
                    )
                )
        for item in _as_items(experiment.get("redundancy")):
            rows.append(
                _coverage_row(
                    experiment_id=experiment_id,
                    record_hash=None,
                    category="redundancy",
                    item_key="?",
                    source_start=None,
                    source_end=None,
                    verdict=TemporalVerdict.FAIL,
                    carrier=carrier,
                    detail="no source EvaluationRecord for this redundancy item",
                    authorized_start=authorized_start,
                    holdout_start=holdout_start,
                )
            )
        for item in _as_items(experiment.get("robustness_tables")):
            name = item.get("name") if isinstance(item, Mapping) else None
            rows.append(
                _coverage_row(
                    experiment_id=experiment_id,
                    record_hash=None,
                    category=str(name) if name is not None else "robustness_table",
                    item_key=str(name),
                    source_start=None,
                    source_end=None,
                    verdict=TemporalVerdict.FAIL,
                    carrier=carrier,
                    detail="no source EvaluationRecord for this robustness table",
                    authorized_start=authorized_start,
                    holdout_start=holdout_start,
                )
            )
        return

    for field in fold_fields:
        _audit_fold_items(
            experiment.get(field),
            carrier=carrier,
            experiment_id=experiment_id,
            record=record,
            rows=rows,
            findings=findings,
            authorized_start=authorized_start,
            holdout_start=holdout_start,
        )
    _audit_redundancy(
        experiment.get("redundancy"),
        carrier=carrier,
        experiment_id=experiment_id,
        record=record,
        rows=rows,
        findings=findings,
        authorized_start=authorized_start,
        holdout_start=holdout_start,
    )
    _audit_robustness_tables(
        experiment.get("robustness_tables"),
        carrier=carrier,
        experiment_id=experiment_id,
        record=record,
        rows=rows,
        findings=findings,
        authorized_start=authorized_start,
        holdout_start=holdout_start,
    )


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def audit_temporal_firewall(
    visible: GeneratorVisibleResearchHistory | Mapping[str, Any] | None = None,
    feedback: ResearchFeedback | Mapping[str, Any] | None = None,
    evaluation_records: Any = (),
    *,
    is_start: Any,
    holdout_start: Any,
) -> TemporalFirewallAudit:
    """Audit one generator-visible snapshot pair against ``D = [is_start, holdout_start)``.

    ``visible``/``feedback`` may be the frozen typed projections or their
    serialized ``to_dict()`` mappings (``None`` means the empty projection).
    ``evaluation_records`` is the collection of sealed
    :class:`~smart_beta.evaluation.spec.EvaluationRecord` objects (or their
    serialized mappings) whose content is the **only** temporal-coverage
    authority.

    The function never raises on a content violation: it returns an audit
    whose :attr:`~TemporalFirewallAudit.status` is ``FAIL`` and whose ``rows``
    carry one item per finding. :func:`enforce_temporal_firewall` is the
    raising entry point the runner uses before every model invocation.
    """
    rows: list[TemporalCoverageRow] = []
    findings: list[str] = []

    try:
        authorized_start = _coerce_date(is_start, field_name="is_start")
        holdout_start_date = _coerce_date(holdout_start, field_name="holdout_start")
    except TemporalFirewallError as exc:
        return TemporalFirewallAudit(
            status=TemporalVerdict.FAIL,
            authorized_start=None if is_start is None else str(is_start),
            authorized_end_exclusive=(
                None if holdout_start is None else str(holdout_start)
            ),
            rows=(),
            findings=(str(exc),),
        )

    if authorized_start >= holdout_start_date:
        return TemporalFirewallAudit(
            status=TemporalVerdict.FAIL,
            authorized_start=authorized_start.isoformat(),
            authorized_end_exclusive=holdout_start_date.isoformat(),
            rows=(),
            findings=(
                "authorized development interval is empty: is_start "
                f"{authorized_start} is not before holdout_start "
                f"{holdout_start_date}",
            ),
        )

    authorized_start_text = authorized_start.isoformat()
    authorized_end_text = holdout_start_date.isoformat()

    visible_payload = _as_payload(
        visible,
        GeneratorVisibleResearchHistory,
        carrier="visible_history",
        rows=rows,
        findings=findings,
        authorized_start=authorized_start_text,
        authorized_end_exclusive=authorized_end_text,
    )
    feedback_payload = _as_payload(
        feedback,
        ResearchFeedback,
        carrier="research_feedback",
        rows=rows,
        findings=findings,
        authorized_start=authorized_start_text,
        authorized_end_exclusive=authorized_end_text,
    )

    _check_closed_keys(
        visible_payload,
        _VISIBLE_HISTORY_KEYS,
        context="visible_history",
        carrier="visible_history",
        experiment_id=None,
        record_hash=None,
        rows=rows,
        findings=findings,
        authorized_start=authorized_start_text,
        authorized_end_exclusive=authorized_end_text,
    )
    _check_closed_keys(
        feedback_payload,
        _FEEDBACK_KEYS,
        context="research_feedback",
        carrier="research_feedback",
        experiment_id=None,
        record_hash=None,
        rows=rows,
        findings=findings,
        authorized_start=authorized_start_text,
        authorized_end_exclusive=authorized_end_text,
    )

    records = _coerce_evaluation_records(
        evaluation_records,
        rows=rows,
        findings=findings,
        authorized_start=authorized_start_text,
        authorized_end_exclusive=authorized_end_text,
    )
    index = _index_by_spec_hash(
        records,
        rows=rows,
        findings=findings,
        authorized_start=authorized_start_text,
        authorized_end_exclusive=authorized_end_text,
    )
    for record in records:
        _check_partition(
            record,
            authorized_start=authorized_start,
            holdout_start=holdout_start_date,
            rows=rows,
            findings=findings,
        )

    for experiment in _as_items(visible_payload.get("experiments")):
        _audit_experiment(
            experiment,
            carrier="visible_history",
            known_keys=_VISIBLE_EXPERIMENT_KEYS,
            fold_fields=("fold_evidence",),
            index=index,
            rows=rows,
            findings=findings,
            authorized_start=authorized_start,
            holdout_start=holdout_start_date,
        )
    for experiment in _as_items(feedback_payload.get("experiments")):
        _audit_experiment(
            experiment,
            carrier="research_feedback",
            known_keys=_FEEDBACK_EXPERIMENT_KEYS,
            fold_fields=_FEEDBACK_FOLD_FIELDS,
            index=index,
            rows=rows,
            findings=findings,
            authorized_start=authorized_start,
            holdout_start=holdout_start_date,
        )

    status = (
        TemporalVerdict.FAIL
        if findings or any(row.verdict is TemporalVerdict.FAIL for row in rows)
        else TemporalVerdict.PASS
    )
    return TemporalFirewallAudit(
        status=status,
        authorized_start=authorized_start_text,
        authorized_end_exclusive=authorized_end_text,
        rows=tuple(rows),
        findings=tuple(findings),
        evaluated_records=tuple(
            sorted(record.content_hash for record in records)
        ),
    )


def enforce_temporal_firewall(
    visible: GeneratorVisibleResearchHistory | Mapping[str, Any] | None = None,
    feedback: ResearchFeedback | Mapping[str, Any] | None = None,
    evaluation_records: Any = (),
    *,
    is_start: Any,
    holdout_start: Any,
) -> TemporalFirewallAudit:
    """Audit and raise :class:`TemporalFirewallViolation` unless it is PASS.

    The runner calls this before every model invocation; a violation means the
    call must not be made and the loop must stop with
    ``HOLDOUT_FIREWALL_VIOLATION``.
    """
    audit = audit_temporal_firewall(
        visible,
        feedback,
        evaluation_records,
        is_start=is_start,
        holdout_start=holdout_start,
    )
    if not audit.ok:
        findings = list(audit.findings)
        findings.extend(
            f"{row.category}:{row.item_key}: {row.detail}"
            for row in audit.failures
            if row.detail
        )
        raise TemporalFirewallViolation(
            "generator-visible evidence failed the temporal information-flow "
            "firewall: " + ("; ".join(findings) or "audit status FAIL"),
            audit=audit,
            findings=tuple(findings),
        )
    return audit
