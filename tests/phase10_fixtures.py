"""Shared *synthetic* builders for the Phase-10 science test suite (owned by P10-A).

Plan section 4.4: this module holds the shared synthetic builders that later
Phase-10 tasks import and never modify:

* synthetic calendars, security maps, panels and alignments;
* Knowledge-PIT record builders (canonical, hash-chained envelopes);
* exposure-declaration helpers (the section 5.2a payload schema).

Every builder is deterministic and offline: no provider, network, PIT,
clock, filesystem (except the explicit :func:`write_knowledge_log` helper),
UUID or randomness access. Task-specific fixtures stay in the task's own test
file.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from smart_beta.science.contracts import (
    DERIVATION_RULES_VERSION,
    EVIDENCE_FOOTPRINT_SCHEMA,
    EXPOSURE_DECLARATION_SCHEMA,
    Channel,
    DeclarantRole,
    ObservationKind,
    Polarity,
    RecordKind,
    canonical_json,
    content_hash,
)

__all__ = [
    "GENESIS_PREV_HASH",
    "iter_sessions",
    "synthetic_calendar",
    "synthetic_security_map",
    "synthetic_market_series_map",
    "synthetic_variable_map",
    "synthetic_panel",
    "synthetic_alignment",
    "synthetic_block",
    "synthetic_footprint_body",
    "knowledge_record",
    "chain_knowledge_records",
    "write_knowledge_log",
    "exposure_declaration",
    "not_exposed_declaration",
    "pretraining_declaration",
]


#: The seq-0 ``prev_hash`` sentinel (plan section 5.1): 64 zeros.
GENESIS_PREV_HASH = "0" * 64


# ---------------------------------------------------------------------------
# calendars / maps / panels / alignments
# ---------------------------------------------------------------------------


def iter_sessions(start: date, count: int) -> tuple[date, ...]:
    """Return ``count`` consecutive weekday sessions from ``start`` inclusive."""
    if count < 0:
        raise ValueError("count must be non-negative")
    sessions: list[date] = []
    cursor = start
    while len(sessions) < count:
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return tuple(sessions)


def synthetic_calendar(
    start: str = "2020-01-01", count: int = 30
) -> tuple[str, ...]:
    """A deterministic ISO-date weekday calendar (synthetic, no real PIT)."""
    return tuple(d.isoformat() for d in iter_sessions(date.fromisoformat(start), count))


def synthetic_security_map(
    *,
    vendor: str = "tiingo",
    market: str = "CN",
    codes: Sequence[str] = ("000001", "600000"),
) -> dict[str, str]:
    """Map vendor identifiers to canonical ``SEC:<MARKET>:<code>`` subject keys."""
    return {f"{vendor}:{code}": f"SEC:{market}:{code}" for code in codes}


def synthetic_market_series_map(
    *,
    vendor: str = "tiingo",
    series: Sequence[str] = ("rf", "benchmark"),
) -> dict[str, str]:
    """Map vendor series identifiers to canonical ``MKT:<series_id>`` keys."""
    return {f"{vendor}:{name}": f"MKT:{name}" for name in series}


def synthetic_variable_map(
    *,
    columns: Sequence[tuple[str, str, str]] = (
        ("return", "RETURN_1D", "value"),
        ("close", "PRICE_FIELD", "value"),
    ),
) -> dict[str, dict[str, str]]:
    """Map vendor column names to a frozen derived-variable descriptor."""
    return {
        column: {"derived_variable": variable, "value_column": value_column}
        for column, variable, value_column in columns
    }


def synthetic_panel(
    *,
    securities: Sequence[str] = ("000001", "600000"),
    sessions: Sequence[str] | None = None,
    value: float = 1.0,
) -> list[dict[str, Any]]:
    """A synthetic long-form panel of identifier/date/value rows."""
    dates = tuple(sessions) if sessions is not None else synthetic_calendar()
    return [
        {"security_id": security, "date": session, "value": value}
        for session in dates
        for security in securities
    ]


def synthetic_alignment(
    *,
    sessions: Sequence[str] | None = None,
    values: Sequence[float] | None = None,
) -> dict[str, float]:
    """A synthetic date -> value alignment (deterministic identity default)."""
    dates = tuple(sessions) if sessions is not None else synthetic_calendar()
    if values is None:
        return {session: float(index) for index, session in enumerate(dates)}
    if len(values) != len(dates):
        raise ValueError("values and sessions must have equal length")
    return {session: float(value) for session, value in zip(dates, values)}


# ---------------------------------------------------------------------------
# evidence-footprint bodies (plan section 6.4)
# ---------------------------------------------------------------------------


def _obs_kind_value(kind: ObservationKind | str) -> str:
    if isinstance(kind, ObservationKind):
        return kind.value
    if isinstance(kind, str) and kind in {member.value for member in ObservationKind}:
        return kind
    raise ValueError(f"{kind!r} is not a closed ObservationKind")


def synthetic_block(
    kind: ObservationKind | str,
    subject_keys: Sequence[str],
    intervals: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """A section 6.4 canonical footprint block (subjects must share intervals)."""
    return {
        "observation_kind": _obs_kind_value(kind),
        "subject_keys": list(subject_keys),
        "intervals": [list(interval) for interval in intervals],
    }


def synthetic_footprint_body(
    *,
    blocks: Sequence[Mapping[str, Any]],
    determinable: bool = True,
    unresolved: Sequence[str] = (),
    derivation_rules_version: str = DERIVATION_RULES_VERSION,
    security_map_hash: str | None = None,
    market_series_map_hash: str | None = None,
    variable_map_hash: str | None = None,
    calendar_hash: str | None = None,
    ded: Sequence[Any] = (),
) -> dict[str, Any]:
    """Build a deterministic, structural section 6.4 footprint body.

    Audit hashes default to a synthetic digest so an undeterminable body can be
    built by passing ``determinable=False`` plus a non-empty ``unresolved``
    list.
    """
    audit_default = content_hash({"synthetic": "footprint-audit"})
    if not determinable:
        audit_default = None  # type: ignore[assignment]
    return {
        "schema": EVIDENCE_FOOTPRINT_SCHEMA,
        "determinable": determinable,
        "unresolved": list(unresolved),
        "derivation_rules_version": derivation_rules_version,
        "security_map_hash": security_map_hash or audit_default,
        "market_series_map_hash": market_series_map_hash or audit_default,
        "variable_map_hash": variable_map_hash or audit_default,
        "calendar_hash": calendar_hash or audit_default,
        "blocks": [dict(block) for block in blocks],
        "ded": list(ded),
    }


# ---------------------------------------------------------------------------
# Knowledge-PIT record builders (plan section 5.1)
# ---------------------------------------------------------------------------

_EMPTY_REFS: dict[str, list[str]] = {
    "derived_from": [],
    "included": [],
    "influenced_by": [],
    "consulted": [],
}


def knowledge_record(
    *,
    seq: int,
    kind: RecordKind | str,
    payload: Mapping[str, Any],
    prev_hash: str = GENESIS_PREV_HASH,
    channel: Channel | str = Channel.SYSTEM,
    program_id: str | None = None,
    refs: Mapping[str, Sequence[str]] | None = None,
    footprint: Mapping[str, Any] | None = None,
    event_time: str | None = None,
    recorded_at: str,
) -> dict[str, Any]:
    """Build one canonical, hash-chained Knowledge-PIT record envelope.

    ``record_hash`` is the section 4.1 content hash of the envelope fields
    (everything except ``record_hash`` itself); ``recorded_at`` is the
    pre-formatted ISO-8601 ``Z`` string, hashed for tamper-evidence only.
    """
    kind_value = kind.value if isinstance(kind, RecordKind) else str(kind)
    channel_value = channel.value if isinstance(channel, Channel) else str(channel)
    normalized_refs: dict[str, list[str]] = {
        name: list(values) for name, values in _EMPTY_REFS.items()
    }
    if refs is not None:
        for name in _EMPTY_REFS:
            if name in refs:
                normalized_refs[name] = list(refs[name])
    body: dict[str, Any] = {
        "seq": seq,
        "prev_hash": prev_hash,
        "kind": kind_value,
        "channel": channel_value,
        "program_id": program_id,
        "refs": normalized_refs,
        "footprint": dict(footprint) if footprint is not None else None,
        "event_time": event_time,
        "recorded_at": recorded_at,
        "payload": dict(payload),
    }
    record = dict(body)
    record["record_hash"] = content_hash(body)
    return record


def chain_knowledge_records(
    specs: Iterable[Mapping[str, Any]],
    *,
    start_seq: int = 0,
    prev_hash: str = GENESIS_PREV_HASH,
) -> list[dict[str, Any]]:
    """Chain a sequence of :func:`knowledge_record` keyword specs.

    ``seq`` and ``prev_hash`` are derived from the running chain; a spec that
    already supplies them is rejected so the chain is unambiguous.
    """
    records: list[dict[str, Any]] = []
    current_prev = prev_hash
    for offset, spec in enumerate(specs):
        spec = dict(spec)
        if "seq" in spec:
            raise ValueError("chain_knowledge_records derives 'seq' from the chain")
        if "prev_hash" in spec:
            raise ValueError("chain_knowledge_records derives 'prev_hash'")
        record = knowledge_record(seq=start_seq + offset, prev_hash=current_prev, **spec)  # type: ignore[arg-type]
        records.append(record)
        current_prev = record["record_hash"]
    return records


def write_knowledge_log(path: str | Path, records: Iterable[Mapping[str, Any]]) -> Path:
    """Write canonical JSONL for a list of records and return the path.

    Used by tamper/adversarial tests that need a raw on-disk log; the
    production append-only store is owned by P10-B.
    """
    target = Path(path)
    target.write_text(
        "".join(canonical_json(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return target


# ---------------------------------------------------------------------------
# exposure-declaration helpers (plan section 5.2a)
# ---------------------------------------------------------------------------


def _build_declaration(
    *,
    declarant_id: str,
    role: DeclarantRole | str,
    channel: Channel | str,
    program_ids: Sequence[str],
    hypothesis_ids: Sequence[str],
    footprint: Mapping[str, Any],
    claim: Mapping[str, Any],
    knowledge_snapshot_ref: Mapping[str, Any],
    extras: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble a synthetic section 5.2a declaration payload.

    ``declaration_id`` is the envelope ``record_hash`` and is therefore derived
    by P10-B, never supplied here.
    """
    role_value = role.value if isinstance(role, DeclarantRole) else str(role)
    channel_value = channel.value if isinstance(channel, Channel) else str(channel)
    payload: dict[str, Any] = {
        "schema_version": EXPOSURE_DECLARATION_SCHEMA,
        "declarant": {"declarant_id": declarant_id, "role": role_value},
        "channel": channel_value,
        "scope": {
            "program_ids": list(program_ids),
            "hypothesis_ids": list(hypothesis_ids),
        },
        "footprint": dict(footprint),
        "claim": dict(claim),
        "knowledge_snapshot_ref": dict(knowledge_snapshot_ref),
    }
    payload.update(dict(extras))
    return payload


def _polarity_value(polarity: Polarity | str) -> str:
    return polarity.value if isinstance(polarity, Polarity) else str(polarity)


def exposure_declaration(
    *,
    declarant_id: str = "researcher-1",
    role: DeclarantRole | str = DeclarantRole.RESEARCHER,
    channel: Channel | str = Channel.HUMAN,
    program_ids: Sequence[str] = ("program-synthetic",),
    hypothesis_ids: Sequence[str] = (),
    footprint: Mapping[str, Any],
    exposure_event_date: str,
    basis_hash: str,
    knowledge_snapshot_ref: Mapping[str, Any],
    basis_reference: str | None = None,
) -> dict[str, Any]:
    """A section 5.2a ``EXPOSED`` declaration payload."""
    claim: dict[str, Any] = {
        "polarity": Polarity.EXPOSED.value,
        "exposure_event_date": exposure_event_date,
        "basis_hash": basis_hash,
    }
    if basis_reference is not None:
        claim["basis_reference"] = basis_reference
    return _build_declaration(
        declarant_id=declarant_id,
        role=role,
        channel=channel,
        program_ids=program_ids,
        hypothesis_ids=hypothesis_ids,
        footprint=footprint,
        claim=claim,
        knowledge_snapshot_ref=knowledge_snapshot_ref,
        extras={},
    )


def not_exposed_declaration(
    *,
    declarant_id: str = "researcher-1",
    role: DeclarantRole | str = DeclarantRole.RESEARCHER,
    channel: Channel | str = Channel.HUMAN,
    program_ids: Sequence[str] = ("program-synthetic",),
    hypothesis_ids: Sequence[str] = (),
    footprint: Mapping[str, Any],
    basis_hash: str,
    knowledge_snapshot_ref: Mapping[str, Any],
    basis_reference: str | None = None,
) -> dict[str, Any]:
    """A section 5.2a ``NOT_EXPOSED`` declaration payload."""
    claim: dict[str, Any] = {
        "polarity": Polarity.NOT_EXPOSED.value,
        "basis_hash": basis_hash,
    }
    if basis_reference is not None:
        claim["basis_reference"] = basis_reference
    return _build_declaration(
        declarant_id=declarant_id,
        role=role,
        channel=channel,
        program_ids=program_ids,
        hypothesis_ids=hypothesis_ids,
        footprint=footprint,
        claim=claim,
        knowledge_snapshot_ref=knowledge_snapshot_ref,
        extras={},
    )


def pretraining_declaration(
    *,
    declarant_id: str = "operator-1",
    role: DeclarantRole | str = DeclarantRole.OPERATOR,
    program_ids: Sequence[str] = ("program-synthetic",),
    hypothesis_ids: Sequence[str] = (),
    footprint: Mapping[str, Any],
    basis_hash: str,
    knowledge_snapshot_ref: Mapping[str, Any],
    model_id: str = "deepseek-v4-pro",
    documented_cutoff: str = "UNDOCUMENTED",
    source_reference: str = "synthetic-provider-doc",
) -> dict[str, Any]:
    """A section 5.2a PRETRAINING declaration payload."""
    claim: dict[str, Any] = {
        "polarity": Polarity.EXPOSED.value,
        "exposure_event_date": documented_cutoff
        if documented_cutoff != "UNDOCUMENTED"
        else "1970-01-01",
        "basis_hash": basis_hash,
    }
    return _build_declaration(
        declarant_id=declarant_id,
        role=role,
        channel=Channel.PRETRAINING,
        program_ids=program_ids,
        hypothesis_ids=hypothesis_ids,
        footprint=footprint,
        claim=claim,
        knowledge_snapshot_ref=knowledge_snapshot_ref,
        extras={
            "model_id": model_id,
            "documented_cutoff": documented_cutoff,
            "source_reference": source_reference,
        },
    )
