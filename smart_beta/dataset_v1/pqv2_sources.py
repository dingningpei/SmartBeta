"""PQ2-B — P-Q v2 real-data source set (flags, m_s, MISDATED, firewall).

This module is the PQ2-B deliverable of the frozen P-Q v2 implementation plan
(``docs/discovery_dataset_v1_2_pq_v2_implementation_plan.md`` §6.2) and
implements the source-set half of the frozen methodology
(``docs/discovery_dataset_v1_2_pq_v2_methodology.md`` M§2, M§3, M§7, M§8):

* the flagged transitions (with their trusted ``prev``/``next`` sessions and the
  observed multiplier ``m_s`` and relative correction set ``Q_s``);
* the M§7 MISDATED candidates (recorded split / cash-distribution events of the
  same D4-bound security inside its D4 interval and within ``MISDATED_WINDOW``
  trusted sessions of the flag, with the combined same-day factor ``phi_d``);
* the M§8 firewall (a flag whose ``+5`` candidate window or ``next`` session runs
  past ``DEVELOPMENT_END`` has an undeterminable candidate set and is UNKNOWN).

Nothing here reads a dataset artifact, the reserve, holdout, ``raw/`` or
``dsc_evidence``: the D3/D4/D7A files are opened only through the frozen
:mod:`returns`/:mod:`delisting_bridge` guarded resolvers, and tests inject
synthetic bindings.  No network call is made and no credential value is read.
"""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from smart_beta.dataset_v1 import delisting_bridge as _d7b
from smart_beta.dataset_v1 import returns as _d7a
from smart_beta.dataset_v1 import quarantine as _pq
from smart_beta.dataset_v1 import trusted_inputs as _d7c
from smart_beta.dataset_v1.pqv2_core import (
    DEVELOPMENT_END,
    MISDATED_ACTION_TYPES,
    MISDATED_WINDOW,
    MisdatedCandidate,
    PQv2BindingError,
    PQv2InputError,
    SourceSet,
    UncertainTransition,
    s2_relative,
)

__all__ = [
    "SourceBindings",
    "load_ca_events",
    "load_identity_assignment",
    "load_source_set",
    "POSITION_BEYOND_DEVELOPMENT_END",
    "POSITION_NEXT_BEYOND_DEVELOPMENT",
]

#: Firewall reason for a flag whose ``+5`` candidate window passes DEV_END.
POSITION_BEYOND_DEVELOPMENT_END = "MISDATED_WINDOW_BEYOND_DEVELOPMENT_END"
#: Firewall reason for a flag whose trusted ``next`` session is in the reserve.
POSITION_NEXT_BEYOND_DEVELOPMENT = "NEXT_BEYOND_DEVELOPMENT"

_CA_COLUMNS = (
    "stock_id",
    "effective_date",
    "action_type",
    "knowledge_date",
    "adjustment_factor",
    "is_superseded",
)
_IDENTITY_COLUMNS = (
    "security_key",
    "ticker",
    "listed_start",
    "listed_end",
)
_DAILY_COLUMNS = (
    "security_key",
    "date",
    "session_status",
    "daily_total_return",
    "ca_event_count",
)


@dataclass(frozen=True)
class SourceBindings:
    """The bindings needed by :func:`load_source_set`.

    ``ca`` is the D7a upstream :class:`returns.Bindings` (it resolves the D3
    ``ca_events.parquet`` and the D4 ``identity_assignment.parquet``);
    ``d7a`` is the :class:`delisting_bridge.Bindings` that resolves the D7A
    ``daily_returns.parquet`` used for the ``ca_event_count`` cross-check.
    """

    ca: Any
    d7a: Any


# ---------------------------------------------------------------------------
# Date / frame helpers
# ---------------------------------------------------------------------------


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, datetime):
        if pd.isna(value):
            return None
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.Timestamp(value)
    if pd.isna(parsed):
        return None
    return parsed.date()


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value)
    return text if text else None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _as_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(value)


def _sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# Binding verification (mirrors the frozen D7a stage verifier)
# ---------------------------------------------------------------------------


def _verify_stage(resolver: Any, stage: Any) -> None:
    """Verify one frozen stage manifest and every bound file hash by SHA-256."""

    blob = resolver.read_bytes(stage.manifest_relative_path)
    if _sha256_bytes(blob) != stage.manifest_sha256:
        raise PQv2BindingError(
            f"{stage.stage} manifest {stage.manifest_relative_path} hash mismatch"
        )
    try:
        manifest = json.loads(blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PQv2BindingError(
            f"{stage.stage} manifest is not valid JSON"
        ) from exc
    if not isinstance(manifest, dict):
        raise PQv2BindingError(f"{stage.stage} manifest is not a JSON object")
    if manifest.get(stage.manifest_hash_field) != stage.manifest_hash:
        raise PQv2BindingError(
            f"{stage.stage} {stage.manifest_hash_field} does not match the binding"
        )
    files = manifest.get("files_sha256")
    if not isinstance(files, dict):
        raise PQv2BindingError(f"{stage.stage} manifest lacks files_sha256")
    for binding in stage.files:
        if files.get(binding.relative_path) != binding.sha256:
            raise PQv2BindingError(
                f"{stage.stage} manifest binds {binding.relative_path} to the "
                "wrong hash"
            )
        if _sha256_bytes(resolver.read_bytes(binding.relative_path)) != binding.sha256:
            raise PQv2BindingError(
                f"{stage.stage} file {binding.relative_path} hash mismatch"
            )


def load_ca_events(root: Path | str, bindings: Any) -> pd.DataFrame:
    """Load the D7a-bound D3 ``ca_events.parquet`` with hash verification."""

    resolver = _d7a.Resolver(root, bindings)
    _verify_stage(resolver, bindings.d3)
    try:
        return resolver.read_parquet(
            bindings.d3.files[1].relative_path, columns=list(_CA_COLUMNS)
        )
    except _d7a.D7Error as exc:
        raise PQv2InputError(str(exc)) from exc


def load_identity_assignment(root: Path | str, bindings: Any) -> pd.DataFrame:
    """Load the D4 ``identity_assignment.parquet`` with hash verification."""

    resolver = _d7a.Resolver(root, bindings)
    _verify_stage(resolver, bindings.d4)
    try:
        return resolver.read_parquet(
            bindings.d4.files[0].relative_path, columns=list(_IDENTITY_COLUMNS)
        )
    except _d7a.D7Error as exc:
        raise PQv2InputError(str(exc)) from exc


def _verify_d7a(resolver: Any, bindings: Any) -> None:
    """Verify the D7A manifest and every bound D7A file hash by SHA-256."""

    d7a = bindings.d7a
    blob = resolver.read_bytes(d7a.manifest_relative_path)
    if _sha256_bytes(blob) != d7a.manifest_sha256:
        raise PQv2BindingError("D7A manifest hash mismatch")
    try:
        manifest = json.loads(blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PQv2BindingError("D7A manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise PQv2BindingError("D7A manifest is not a JSON object")
    if manifest.get("returns_id") not in (None, d7a.artifact_id):
        raise PQv2BindingError("D7A returns_id does not match the binding")
    if manifest.get("returns_hash") != d7a.returns_hash:
        raise PQv2BindingError("D7A returns_hash does not match the binding")
    files = manifest.get("files_sha256")
    if not isinstance(files, dict):
        raise PQv2BindingError("D7A manifest lacks files_sha256")
    for binding in d7a.files:
        if files.get(binding.relative_path) != binding.sha256:
            raise PQv2BindingError(
                f"D7A manifest binds {binding.relative_path} to the wrong hash"
            )
        if _sha256_bytes(resolver.read_bytes(binding.relative_path)) != binding.sha256:
            raise PQv2BindingError(
                f"D7A file {binding.relative_path} hash mismatch"
            )


def _load_d7a_daily(root: Path | str, bindings: Any) -> pd.DataFrame:
    resolver = _d7b._Resolver(root, bindings)
    _verify_d7a(resolver, bindings)
    return resolver.read_parquet(
        f"derived/returns/{bindings.d7a.artifact_id}/daily_returns.parquet",
        columns=list(_DAILY_COLUMNS),
        filters=[("date", "<=", pd.Timestamp(DEVELOPMENT_END))],
    )


# ---------------------------------------------------------------------------
# Input normalisation
# ---------------------------------------------------------------------------


def _resolve_bindings(root: Path | str, d7a_bindings: Any) -> tuple[Any, pd.DataFrame]:
    """Return ``(returns_bindings, daily_returns_frame)`` for the loader.

    ``d7a_bindings`` may be a :class:`SourceBindings` (both explicit), a
    :class:`returns.Bindings` (D3/D4; the D7A cross-check uses the frozen
    default D7A bindings), or a :class:`delisting_bridge.Bindings` (D7A; the
    D3/D4 bindings are the frozen :func:`returns.default_bindings`).
    """

    if isinstance(d7a_bindings, SourceBindings):
        return d7a_bindings.ca, _load_d7a_daily(root, d7a_bindings.d7a)
    if isinstance(d7a_bindings, _d7a.Bindings):
        return d7a_bindings, _load_d7a_daily(root, _d7b.DEFAULT_BINDINGS)
    if isinstance(d7a_bindings, _d7b.Bindings):
        return _d7a.default_bindings(), _load_d7a_daily(root, d7a_bindings)
    raise PQv2InputError(
        "d7a_bindings must be a SourceBindings, returns.Bindings or "
        "delisting_bridge.Bindings"
    )


def _daily_lookup(ti_inputs: Any) -> tuple[pd.DataFrame, dict[date, Any]]:
    """Return the D7c daily frame and a ``row_date -> index label`` map.

    The wide frame is kept as-is (no O(rows x columns) materialisation); only
    the row index is normalised to calendar dates, so a per-flag lookup is
    O(1).
    """

    trusted = ti_inputs
    if isinstance(ti_inputs, Mapping):
        if _d7c.INPUT_DAILY_TOTAL_RETURN not in ti_inputs:
            raise PQv2InputError(
                "ti_inputs does not carry the daily_total_return TrustedInput"
            )
        trusted = ti_inputs[_d7c.INPUT_DAILY_TOTAL_RETURN]
    values = getattr(trusted, "values", None)
    if not isinstance(values, pd.DataFrame):
        raise PQv2InputError(
            "the daily_total_return TrustedInput must expose a DataFrame"
        )
    positions: dict[date, Any] = {}
    for label in values.index:
        parsed = _as_date(label)
        if parsed is not None:
            positions[parsed] = label
    return values, positions


def _observed_m(
    lookup: tuple[pd.DataFrame, Mapping[date, Any]], key: str, row_date: date
) -> float:
    values, positions = lookup
    label = positions.get(row_date)
    if label is None or key not in values.columns:
        raise PQv2InputError(
            f"flagged transition source session for {key!r} carries no VALID "
            "daily_total_return"
        )
    value = float(values.at[label, key])
    if not math.isfinite(value):
        raise PQv2InputError(
            f"flagged transition source session for {key!r} is not a VALID return"
        )
    m_obs = 1.0 + value
    if not math.isfinite(m_obs) or m_obs <= 0.0:
        raise PQv2InputError(
            f"observed multiplier for {key!r} is not finite and positive"
        )
    return m_obs


# ---------------------------------------------------------------------------
# Identity / event indexes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _IdentityIndex:
    ticker_by_key: Mapping[str, str]
    intervals: Mapping[str, tuple[tuple[date | None, date | None], ...]]


def _identity_index(identity: pd.DataFrame) -> _IdentityIndex:
    ticker_by_key: dict[str, str] = {}
    intervals: dict[str, list[tuple[date | None, date | None]]] = {}
    for row in identity.itertuples(index=False):
        key = _as_str(row.security_key)
        ticker = _as_str(row.ticker)
        if key is None:
            continue
        if ticker is not None:
            if key in ticker_by_key and ticker_by_key[key] != ticker:
                raise PQv2InputError(f"security {key!r} carries multiple tickers")
            ticker_by_key[key] = ticker
        start = _as_date(row.listed_start)
        end = _as_date(row.listed_end)
        if start is not None and start > DEVELOPMENT_END:
            continue
        clipped_end = DEVELOPMENT_END if end is None else min(end, DEVELOPMENT_END)
        intervals.setdefault(key, []).append((start, clipped_end))
    ordered = {
        key: tuple(
            sorted(values, key=lambda item: (item[0] or date.min, item[1] or date.max))
        )
        for key, values in intervals.items()
    }
    return _IdentityIndex(ticker_by_key, ordered)


def _within_interval(
    intervals: Sequence[tuple[date | None, date | None]], session: date
) -> bool:
    for start, end in intervals:
        if start is not None and session < start:
            continue
        if end is not None and session > end:
            continue
        return True
    return False


def _event_index(
    ca_events: pd.DataFrame,
    sessions: Sequence[date],
    index: _IdentityIndex,
) -> dict[str, dict[date, tuple[tuple[str, bool, float], ...]]]:
    """Per security key: session -> the *selected* ``(action, candidate, factor)``.

    Selected means: non-superseded, ``knowledge_date == effective_date``, a
    positive finite factor, inside the key's D4 interval and no later than
    ``DEVELOPMENT_END``.  ``candidate`` is true only for the M§7 action types.
    A reserve-dated row or a non-trusted effective session fails closed.
    """

    trusted = set(sessions)
    ticker_to_key: dict[str, str] = {}
    for key, ticker in index.ticker_by_key.items():
        if ticker in ticker_to_key and ticker_to_key[ticker] != key:
            raise PQv2InputError(
                f"ticker {ticker!r} maps to multiple security keys"
            )
        ticker_to_key[ticker] = key

    out: dict[str, dict[date, list[tuple[str, bool, float]]]] = {}
    for record in ca_events.to_dict("records"):
        ticker = _as_str(record["stock_id"])
        if ticker is None:
            continue
        effective = _as_date(record["effective_date"])
        knowledge = _as_date(record["knowledge_date"])
        if effective is None or knowledge is None:
            continue
        if effective > DEVELOPMENT_END or knowledge > DEVELOPMENT_END:
            raise PQv2InputError(
                f"corporate action for {ticker!r} is dated after DEVELOPMENT_END"
            )
        if effective not in trusted:
            raise PQv2InputError(
                f"corporate action for {ticker!r} on {effective} is not a trusted "
                "session"
            )
        if _as_bool(record["is_superseded"]):
            raise PQv2InputError(
                f"corporate action for {ticker!r} on {effective} is superseded"
            )
        if knowledge != effective:
            raise PQv2InputError(
                f"corporate action for {ticker!r} on {effective} has "
                "knowledge_date != effective_date"
            )
        factor = _as_float(record["adjustment_factor"])
        if factor is None or factor <= 0.0:
            raise PQv2InputError(
                f"corporate action for {ticker!r} on {effective} has an invalid "
                "adjustment_factor"
            )
        key = ticker_to_key.get(ticker)
        if key is None:
            continue
        if not _within_interval(index.intervals.get(key, ()), effective):
            continue
        action = _as_str(record["action_type"]) or ""
        out.setdefault(key, {}).setdefault(effective, []).append(
            (action, action in MISDATED_ACTION_TYPES, factor)
        )
    return {
        key: {session: tuple(values) for session, values in by_session.items()}
        for key, by_session in out.items()
    }


def _daily_count_index(daily: pd.DataFrame) -> dict[tuple[str, date], int]:
    out: dict[tuple[str, date], int] = {}
    for row in daily.itertuples(index=False):
        key = _as_str(row.security_key)
        session = _as_date(row.date)
        count = row.ca_event_count
        if key is None or session is None or count is None:
            continue
        if session > DEVELOPMENT_END:
            raise PQv2InputError(
                f"D7a daily_returns holds a session {session} after DEVELOPMENT_END"
            )
        out[(key, session)] = int(count)
    return out


# ---------------------------------------------------------------------------
# The loader
# ---------------------------------------------------------------------------


def load_source_set(
    root: Path | str,
    *,
    overlay: "_pq.Overlay",
    d7a_bindings: Any,
    ti_inputs: Any,
    sessions: Sequence[date],
) -> SourceSet:
    """Build the deterministic P-Q v2 :class:`SourceSet` (plan §6.2, M§7/M§8).

    * flags come from ``overlay.flags`` only;
    * ``m_s`` is ``1 + daily_total_return`` at the flag's source session;
    * MISDATED candidates are recorded split/dividend events of the same D4
      security, inside its D4 interval, at a trusted session ``d != s`` within
      ``MISDATED_WINDOW``, with the combined same-day factor;
    * the firewall marks a flag UNKNOWN when its ``+5`` window or its ``next``
      session runs past ``DEVELOPMENT_END``.
    """

    parsed_sessions = [_as_date(session) for session in sessions]
    if any(session is None for session in parsed_sessions):
        raise PQv2InputError("sessions must be a sequence of calendar dates")
    session_list = sorted(session for session in parsed_sessions if session is not None)
    position = {session: index for index, session in enumerate(session_list)}
    dev_end_position = bisect_right(session_list, DEVELOPMENT_END) - 1

    ca_bindings, daily = _resolve_bindings(root, d7a_bindings)
    ca_events = load_ca_events(root, ca_bindings)
    identity = load_identity_assignment(root, ca_bindings)
    lookup = _daily_lookup(ti_inputs)
    index = _identity_index(identity)
    events = _event_index(ca_events, session_list, index)
    daily_count = _daily_count_index(daily)

    flags = overlay.flags
    required = {
        "security_key",
        "flag_session",
        "prev_session",
        "next_session",
        "next_beyond_development",
    }
    missing = required - set(flags.columns)
    if missing:
        raise PQv2InputError(f"overlay.flags lacks columns {sorted(missing)}")

    transitions: dict[str, list[UncertainTransition]] = {}
    candidates: dict[str, list[MisdatedCandidate]] = {}
    unknown: dict[str, list[tuple[Any, Any, str]]] = {}

    ordered = sorted(
        flags.to_dict("records"),
        key=lambda row: (str(row["security_key"]), _as_date(row["flag_session"])),
    )
    for row in ordered:
        key = str(row["security_key"])
        s = _as_date(row["flag_session"])
        if s is None:
            raise PQv2InputError(f"flag for {key!r} carries a null session")
        if s not in position:
            raise PQv2InputError(
                f"flag session {s} for {key!r} is not a trusted session"
            )
        flag_position = position[s]
        next_s = (
            session_list[flag_position + 1]
            if flag_position + 1 < len(session_list)
            else None
        )
        prev_s = _as_date(row["prev_session"])
        flag_next = _as_date(row["next_session"])
        beyond = _as_bool(row["next_beyond_development"])

        reasons: list[str] = []
        if flag_position + MISDATED_WINDOW > dev_end_position:
            reasons.append(POSITION_BEYOND_DEVELOPMENT_END)
        if beyond:
            reasons.append(POSITION_NEXT_BEYOND_DEVELOPMENT)
        if reasons:
            unknown.setdefault(key, []).extend(
                (s, prev_s, reason) for reason in reasons
            )
            continue

        if next_s is None:
            raise PQv2InputError(
                f"flag for {key!r} has no trusted next session for m_s"
            )
        m_obs = _observed_m(lookup, key, next_s)
        transitions.setdefault(key, []).append(
            UncertainTransition(
                security_key=key,
                s=s,
                prev_s=prev_s,
                next_s=flag_next,
                m_obs=m_obs,
                rel=s2_relative(m_obs),
            )
        )

        key_events = events.get(key)
        if not key_events:
            continue
        for d in sorted(key_events):
            if d == s:
                continue
            d_position = position.get(d)
            if d_position is None:
                continue
            if abs(d_position - flag_position) > MISDATED_WINDOW:
                continue
            day_events = key_events[d]
            if not any(is_candidate for _action, is_candidate, _factor in day_events):
                continue
            phi_d = math.prod(factor for _action, _candidate, factor in day_events)
            cross_check = daily_count.get((key, d))
            if cross_check is not None and len(day_events) != cross_check:
                raise PQv2InputError(
                    f"recorded CA event count on {d} for {key!r} is "
                    f"{len(day_events)}, D7a ca_event_count is {cross_check}"
                )
            candidates.setdefault(key, []).append(
                MisdatedCandidate(security_key=key, s=s, d=d, phi_d=phi_d)
            )

    return SourceSet(
        transitions_by_key={key: tuple(value) for key, value in transitions.items()},
        candidates_by_key={key: tuple(value) for key, value in candidates.items()},
        unknown_by_key={
            key: tuple(sorted(set(value), key=lambda item: (item[0], item[2])))
            for key, value in unknown.items()
        },
    )
