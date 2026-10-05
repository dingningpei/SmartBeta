"""PQ2-B — P-Q v2 Candidate-E label envelope (M§6, M§8, M§9).

This module is the label half of the PQ2-B deliverable of the frozen P-Q v2
implementation plan (``docs/discovery_dataset_v1_2_pq_v2_implementation_plan.md``
§6.2).  Given one D7a label record and the key's flagged transitions, MISDATED
candidates and reserve-unknown flag set, :func:`label_envelope` computes the
frozen Candidate-E return envelope of M§6/M§8:

* ``s == f`` contributes ``Q_s^{-1}`` (the formation endpoint, corrected from
  P-Q v1's false negative);
* ``f < s <= e*`` contributes ``Q_s`` (an interior flag; interior close errors
  cancel);
* ``s == next(e*)`` contributes ``Q_s^{-1}`` (the ending endpoint);
* every MISDATED candidate with exactly one of ``{s, d}`` in ``(f, e*]`` adds
  the discrete alternative ``phi_d^{+1}`` (``s`` inside) or ``phi_d^{-1}``
  (``d`` inside);
* per flag the factor set is the hull of the base factor and its alternatives,
  and the envelope is the product of the per-flag extrema;
* the M§8 firewall turns ``e* == DEVELOPMENT_END`` or an unknown flag in the
  dependency window into ``UNKNOWN_RESERVE``.

Nothing here reads a dataset artifact, the reserve, holdout, ``raw/`` or
``dsc_evidence``.  It is pure interval arithmetic over the frozen core types.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from smart_beta.dataset_v1.pqv2_core import (
    DEVELOPMENT_END,
    LabelStatus,
    MisdatedCandidate,
    NotAssessedReason,
    PQv2InputError,
    PositiveInterval,
    UncertainTransition,
    UNIT,
)

__all__ = [
    "LabelEnvelope",
    "label_envelope",
    "FORMATION_ENDPOINT",
    "INTERIOR",
    "ENDING_ENDPOINT",
    "POSITION_CLASSES",
]

#: Position classes of a dependent flagged transition (plan §7).
FORMATION_ENDPOINT = "FORMATION_ENDPOINT"
INTERIOR = "INTERIOR"
ENDING_ENDPOINT = "ENDING_ENDPOINT"
POSITION_CLASSES = (FORMATION_ENDPOINT, INTERIOR, ENDING_ENDPOINT)

_STATE_EMPTY = "EMPTY_OBSERVED_PATH_IDENTITY"
_STATE_COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class LabelEnvelope:
    """One member's frozen Candidate-E label envelope (M§8)."""

    status: str
    reason: str | None
    lo: float
    hi: float
    t0_lo: float
    t0_hi: float
    ts_lo: float
    ts_hi: float
    formation_endpoint: int
    interior: int
    ending_endpoint: int
    n_misdated: int
    n_unknown: int

    def positions(self) -> dict[str, int]:
        return {
            FORMATION_ENDPOINT: int(self.formation_endpoint),
            INTERIOR: int(self.interior),
            ENDING_ENDPOINT: int(self.ending_endpoint),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "lo": self.lo,
            "hi": self.hi,
            "t0_lo": self.t0_lo,
            "t0_hi": self.t0_hi,
            "ts_lo": self.ts_lo,
            "ts_hi": self.ts_hi,
            "formation_endpoint": int(self.formation_endpoint),
            "interior": int(self.interior),
            "ending_endpoint": int(self.ending_endpoint),
            "n_misdated": int(self.n_misdated),
            "n_unknown": int(self.n_unknown),
        }


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    converter = getattr(value, "date", None)
    if callable(converter):
        try:
            return converter()
        except (TypeError, ValueError):
            return None
    return None


def _field(record: Mapping[str, Any] | Any, name: str) -> Any:
    if isinstance(record, Mapping):
        if name not in record:
            raise PQv2InputError(f"label record lacks {name!r}")
        return record[name]
    if hasattr(record, name):
        return getattr(record, name)
    try:
        return record[name]
    except (TypeError, KeyError, IndexError) as exc:
        raise PQv2InputError(
            f"label record must expose {name!r}"
        ) from exc


def _unknown_sessions(
    unknown: Any,
) -> dict[Any, Any]:
    """Normalise the key's unknown set to ``{flag_session: prev_session_or_None}``.

    Accepts a mapping (as stored by the loader) or a sequence whose entries are
    a session, ``(session, prev_session)`` or ``(session, prev_session, reason)``.
    """

    if unknown is None:
        return {}
    if isinstance(unknown, Mapping):
        raw = list(unknown.values())
    else:
        raw = list(unknown)
    out: dict[Any, Any] = {}
    for entry in raw:
        if isinstance(entry, (tuple, list)):
            if len(entry) == 0:
                continue
            session = entry[0]
            prev_s = entry[1] if len(entry) > 1 else None
        else:
            session, prev_s = entry, None
        out[session] = prev_s
    return out


def _as_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    try:
        import pandas as pd

        if pd.isna(value):
            return False
    except (ImportError, TypeError, ValueError):  # pragma: no cover
        pass
    return bool(value)


def _scenario(value: Any, factor: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        return float("nan")
    return (1.0 + number) * factor - 1.0


def _dependent_position(
    s: Any, f: date, e_star: date, transition: UncertainTransition
) -> tuple[PositiveInterval, str | None]:
    if s == f:
        return transition.rel.inv(), FORMATION_ENDPOINT
    if f < s <= e_star:
        return transition.rel, INTERIOR
    if s > e_star and transition.prev_s == e_star:
        return transition.rel.inv(), ENDING_ENDPOINT
    return UNIT, None


def label_envelope(
    label_record: Mapping[str, Any] | Any,
    transitions: Sequence[UncertainTransition],
    candidates: Sequence[MisdatedCandidate],
    unknown: Any,
    *,
    t0_value: float | None = None,
    ts_value: float | None = None,
) -> LabelEnvelope:
    """The frozen Candidate-E label envelope of M§6/M§8/M§9."""

    formation = _as_date(_field(label_record, "formation_date"))
    endpoint = _as_date(_field(label_record, "endpoint"))
    state = str(_field(label_record, "label_state"))
    observed_segment_end = _as_date(_field(label_record, "observed_segment_end"))
    terminal = _as_bool(_field(label_record, "terminal_affected"))

    record_t0 = t0_value
    record_ts = ts_value
    if record_t0 is None:
        try:
            record_t0 = _field(label_record, "t0_value")
        except PQv2InputError:
            record_t0 = float("nan")
    if record_ts is None:
        try:
            record_ts = _field(label_record, "ts_value")
        except PQv2InputError:
            record_ts = float("nan")

    unknown_sessions = _unknown_sessions(unknown)

    if state == _STATE_EMPTY:
        return _blank(
            LabelStatus.EMPTY_NO_DEPENDENCY,
            None,
            1.0,
            1.0,
            record_t0,
            record_ts,
        )

    if state != _STATE_COMPLETE or formation is None:
        return _blank(
            LabelStatus.FACTUAL_MISSING,
            None,
            float("nan"),
            float("nan"),
            float("nan"),
            float("nan"),
        )

    e_star = observed_segment_end if terminal else endpoint
    if e_star is None:
        return _blank(
            LabelStatus.FACTUAL_MISSING,
            None,
            float("nan"),
            float("nan"),
            float("nan"),
            float("nan"),
        )

    if e_star == DEVELOPMENT_END:
        return _blank(
            LabelStatus.UNKNOWN_RESERVE,
            NotAssessedReason.RESERVE_UNKNOWN,
            float("nan"),
            float("nan"),
            float("nan"),
            float("nan"),
            unknown=len(unknown_sessions),
        )

    # Per-flag factor sets, seeded with the dependent base interval.
    factors: dict[Any, list[PositiveInterval]] = {}
    positions: dict[Any, str | None] = {}
    for transition in transitions:
        base, position = _dependent_position(
            transition.s, formation, e_star, transition
        )
        factors[transition.s] = [base]
        positions[transition.s] = position

    n_misdated = 0
    for candidate in candidates:
        s = candidate.s
        d = candidate.d
        s_inside = formation < s <= e_star
        d_inside = formation < d <= e_star
        if s_inside and not d_inside:
            alternative = PositiveInterval(candidate.phi_d, candidate.phi_d)
        elif d_inside and not s_inside:
            inverse = 1.0 / candidate.phi_d
            alternative = PositiveInterval(inverse, inverse)
        else:
            continue
        factors.setdefault(s, [UNIT]).append(alternative)
        positions.setdefault(s, None)
        n_misdated += 1

    # Reserve firewall: an unknown flag inside [f, next(e*)] or a relevant
    # candidate whose flag is unknown turns the label UNKNOWN.
    n_unknown = 0
    for session, prev_s in unknown_sessions.items():
        in_window = formation <= session <= e_star or (
            session > e_star and prev_s == e_star
        )
        if in_window:
            n_unknown += 1
    for candidate in candidates:
        s = candidate.s
        d = candidate.d
        s_inside = formation < s <= e_star
        d_inside = formation < d <= e_star
        if (s_inside != d_inside) and s in unknown_sessions:
            n_unknown = max(n_unknown, 1)
    if n_unknown:
        return _blank(
            LabelStatus.UNKNOWN_RESERVE,
            NotAssessedReason.RESERVE_UNKNOWN,
            float("nan"),
            float("nan"),
            float("nan"),
            float("nan"),
            unknown=len(unknown_sessions),
        )

    lo = 1.0
    hi = 1.0
    for session in sorted(factors, key=lambda value: (str(type(value)), value)):
        intervals = factors[session]
        flag_lo = min(interval.lo for interval in intervals)
        flag_hi = max(interval.hi for interval in intervals)
        lo *= flag_lo
        hi *= flag_hi

    counts: dict[str, int] = {FORMATION_ENDPOINT: 0, INTERIOR: 0, ENDING_ENDPOINT: 0}
    for position in positions.values():
        if position is not None:
            counts[position] += 1

    t0_lo = _scenario(record_t0, lo)
    t0_hi = _scenario(record_t0, hi)
    ts_lo = _scenario(record_ts, lo)
    ts_hi = _scenario(record_ts, hi)
    return LabelEnvelope(
        status=LabelStatus.ENVELOPE,
        reason=None,
        lo=lo,
        hi=hi,
        t0_lo=t0_lo,
        t0_hi=t0_hi,
        ts_lo=ts_lo,
        ts_hi=ts_hi,
        formation_endpoint=counts[FORMATION_ENDPOINT],
        interior=counts[INTERIOR],
        ending_endpoint=counts[ENDING_ENDPOINT],
        n_misdated=n_misdated,
        n_unknown=0,
    )


def _blank(
    status: str,
    reason: str | None,
    lo: float,
    hi: float,
    t0: Any,
    ts: Any,
    *,
    unknown: int = 0,
) -> LabelEnvelope:
    return LabelEnvelope(
        status=status,
        reason=reason,
        lo=lo,
        hi=hi,
        t0_lo=_scenario(t0, lo) if math.isfinite(lo) else float("nan"),
        t0_hi=_scenario(t0, hi) if math.isfinite(hi) else float("nan"),
        ts_lo=_scenario(ts, lo) if math.isfinite(lo) else float("nan"),
        ts_hi=_scenario(ts, hi) if math.isfinite(hi) else float("nan"),
        formation_endpoint=0,
        interior=0,
        ending_endpoint=0,
        n_misdated=0,
        n_unknown=unknown,
    )
