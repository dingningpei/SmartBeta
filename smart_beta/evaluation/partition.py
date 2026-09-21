"""Deterministic temporal partition authority (Phase 7, task P7-A).

This module owns *exactly* the temporal partition of an evaluation: the
explicit date ranges that make up an in-sample / out-of-sample /
walk-forward / final-holdout split, the deterministic identity of that
split, the evaluation-local single-use holdout token, and the
partition-boundary predicate that the forward-return aligner (P7-B) applies
under the section 8.1 joint contract.

Trust boundary (Phase 7 plan, section 5)
----------------------------------------
This is a *pure temporal contract*. It reasons only about dates and calendar
membership. It must never read, import, derive, or expose:

* return panels (realized or forward), factor panels, prices, market caps;
* provider access, point-in-time selection, or vendor logic;
* metrics, portfolio construction, accept/reject decisions, or registries;
* cross-experiment holdout history (that is Phase 8's registry).

Its only non-stdlib dependency is
:class:`smart_beta.pit.calendar.TradingCalendar`, used *read-only* to
validate that explicit boundaries fall on real trading days (and, when a
calendar is supplied, that a fold actually contains trading days). No other
part of :mod:`smart_beta` is imported, and the module performs no I/O.

Boundary semantics (frozen, explicit)
-------------------------------------
Every fold is a half-open interval ``[start, end)`` over calendar dates:

* ``start`` is **inclusive**: a date equal to ``start`` belongs to the fold;
* ``end`` is **exclusive**: a date equal to ``end`` does *not* belong to the
  fold.

Contiguous folds share the boundary date (``fold[i].end == fold[i+1].start``),
so a date exactly on a shared boundary belongs to exactly one side: the
*later* fold. This is deterministic and documented, never inferred from data.
A date before the first fold's ``start`` or on/after the last fold's ``end``
belongs to no fold.

Section 8.1 joint rule with P7-B (normative)
--------------------------------------------
A factor observation formed at ``t`` whose realized forward return spans the
closed realization interval ``[t, t+h]`` may belong to a fold ``F`` only when
**both** endpoints satisfy ``F``'s boundary semantics -- i.e. ``t`` and
``t+h`` are both in ``[F.start, F.end)``. Because each fold is a contiguous
range, that is exactly the condition "the complete realization interval lies
entirely inside one fold".

An interval whose realization endpoint leaves the formation fold -- because
it reaches the next fold, a gap, or the end of the partitioned range -- is
**purged**: it belongs to no fold. This module never truncates, shortens,
reassigns, or borrows across a boundary; it only *reports* membership
(:meth:`Partition.classify_interval`) or its boolean form
(:meth:`Partition.contains_interval`). In particular, a realization endpoint
equal to the formation fold's exclusive ``end`` is *not* a member of that
fold and therefore purges the label: that is the fail-closed reading of
"entirely inside one partition".

Evaluation-local holdout single-use
-----------------------------------
A :class:`Partition` exposes a deterministic :attr:`Partition.holdout_key`
(a SHA-256 hash of the split-rule identifier and the holdout date range) and
an immutable :class:`Holdout` record carrying a ``consumed`` marker.
:class:`HoldoutRegistry` is the evaluation-local single-use mechanism: the
engine creates one registry per evaluation and refuses a second consumption
of the same holdout key in that context. This is *not* a cross-experiment
freshness guarantee -- persistent proof that a holdout was never previously
consumed belongs to Phase 8's experiment registry.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum
from typing import Any, Iterable, Mapping

import pandas as pd

from smart_beta.pit.calendar import TradingCalendar

__all__ = [
    "Fold",
    "FoldRole",
    "Holdout",
    "HoldoutAlreadyConsumedError",
    "HoldoutRegistry",
    "IntervalMembership",
    "MembershipStatus",
    "Partition",
    "PartitionValidationError",
    "canonical_json",
    "holdout_key",
    "partition_id",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PartitionValidationError(ValueError):
    """A partition, fold, or boundary query is malformed or inconsistent.

    Raised at construction time (and on malformed boundary queries), so a
    partition that violates the frozen temporal contract can never reach a
    downstream evaluation step. Nothing is silently coerced: an overlapping,
    empty, non-calendar-aligned, or ill-ordered split fails closed here.
    """


class HoldoutAlreadyConsumedError(RuntimeError):
    """The same holdout key was consumed twice within one evaluation context."""


# ---------------------------------------------------------------------------
# Frozen vocabularies
# ---------------------------------------------------------------------------


class FoldRole(str, Enum):
    """The frozen fold-role vocabulary (Phase 7 plan, section 6.3)."""

    IS = "is"
    OOS = "oos"
    WALK_FORWARD = "walk_forward"
    HOLDOUT = "holdout"


class MembershipStatus(str, Enum):
    """The partition-membership disposition of one realization interval.

    ``INSIDE``
        Both the formation timestamp and the complete realization interval
        lie inside the *same* fold: the observation belongs to that fold.
    ``CROSSES_BOUNDARY``
        The formation timestamp lies inside a fold, but the realization
        endpoint leaves it (next fold, gap, or past the partitioned range):
        the label is **purged** under section 8.1.
    ``OUTSIDE``
        The formation timestamp lies in no fold, so the observation is not
        part of this partition at all.
    """

    INSIDE = "inside"
    CROSSES_BOUNDARY = "crosses_boundary"
    OUTSIDE = "outside"


_ROLE_ORDER = {
    FoldRole.IS: 0,
    FoldRole.OOS: 1,
    FoldRole.WALK_FORWARD: 2,
    FoldRole.HOLDOUT: 3,
}


# ---------------------------------------------------------------------------
# Small fail-closed validators
# ---------------------------------------------------------------------------


def _as_calendar_date(value: Any, *, field_name: str) -> pd.Timestamp:
    """Normalize an accepted date-like value to a tz-naive midnight Timestamp.

    Fail closed on non-dates, ``NaT``, timezone-aware values, and values with
    a non-midnight time component: this is a *daily calendar* contract, and a
    time-of-day on a boundary would be an ambiguous, silently-coerced
    boundary, which is exactly what the frozen contract forbids.
    """
    if value is None or isinstance(value, bool):
        raise PartitionValidationError(
            f"{field_name} must be a date, got {value!r}"
        )
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise PartitionValidationError(
            f"{field_name} must be a date, got {value!r}"
        ) from exc
    if pd.isna(ts):
        raise PartitionValidationError(f"{field_name} must not be NaT")
    if ts.tzinfo is not None:
        raise PartitionValidationError(
            f"{field_name} must be timezone-naive, got {ts}"
        )
    if ts != ts.normalize():
        raise PartitionValidationError(
            f"{field_name} must be a midnight calendar date with no time "
            f"component, got {ts}"
        )
    return ts.normalize()


def _coerce_role(value: Any) -> FoldRole:
    if isinstance(value, FoldRole):
        return value
    if isinstance(value, str):
        try:
            return FoldRole(value)
        except ValueError:
            pass
    allowed = ", ".join(sorted(member.value for member in FoldRole))
    raise PartitionValidationError(
        f"role must be one of [{allowed}], got {value!r}"
    )


def _validate_split_rule(value: Any) -> str:
    if not isinstance(value, str):
        raise PartitionValidationError(
            f"split_rule must be a string, got {type(value).__name__}"
        )
    if not value or value != value.strip():
        raise PartitionValidationError(
            "split_rule must be a non-empty string with no surrounding "
            f"whitespace, got {value!r}"
        )
    return value


# ---------------------------------------------------------------------------
# A fold: one explicit, half-open date range
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fold:
    """One explicit temporal segment ``[start, end)`` with a frozen role.

    Parameters
    ----------
    role:
        One of :class:`FoldRole`. ``walk_forward`` folds additionally require
        a non-negative ``fold_index``; every other role forbids one.
    start:
        Inclusive boundary date.
    end:
        Exclusive boundary date; ``start < end`` is required.
    fold_index:
        Position of a walk-forward fold. Required (and only meaningful) for
        ``role == FoldRole.WALK_FORWARD``.
    """

    role: FoldRole
    start: pd.Timestamp
    end: pd.Timestamp
    fold_index: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _coerce_role(self.role))
        object.__setattr__(
            self, "start", _as_calendar_date(self.start, field_name="fold start")
        )
        object.__setattr__(
            self, "end", _as_calendar_date(self.end, field_name="fold end")
        )
        if self.start >= self.end:
            raise PartitionValidationError(
                f"fold {self.role.value} has an empty or inverted range: "
                f"start {self.start.date()} must be strictly before end "
                f"{self.end.date()}"
            )
        if self.role is FoldRole.WALK_FORWARD:
            if (
                isinstance(self.fold_index, bool)
                or not isinstance(self.fold_index, int)
                or self.fold_index < 0
            ):
                raise PartitionValidationError(
                    "walk_forward folds require a non-negative integer "
                    f"fold_index, got {self.fold_index!r}"
                )
        elif self.fold_index is not None:
            raise PartitionValidationError(
                f"fold_index is only meaningful for walk_forward folds; "
                f"role {self.role.value} must not carry one"
            )

    # -- temporal semantics ----------------------------------------------
    def contains(self, timestamp: date | pd.Timestamp) -> bool:
        """Whether ``timestamp`` is in this fold under ``[start, end)``."""
        ts = _as_calendar_date(timestamp, field_name="timestamp")
        return bool(self.start <= ts < self.end)

    def contains_interval(
        self,
        formation: date | pd.Timestamp,
        realization_end: date | pd.Timestamp,
    ) -> bool:
        """Whether the closed realization interval ``[formation, realization_end]``
        lies entirely inside this fold (section 8.1).

        Equivalent to requiring both endpoints to be members of the half-open
        fold range; since the interval is contiguous this is exactly
        "entirely inside". A realization endpoint equal to this fold's
        exclusive ``end`` is *not* a member and therefore fails this test.
        """
        t = _as_calendar_date(formation, field_name="formation timestamp")
        u = _as_calendar_date(realization_end, field_name="realization_end")
        if u < t:
            raise PartitionValidationError(
                f"realization_end {u.date()} is before formation {t.date()}; "
                "the realization interval is malformed"
            )
        return bool(self.start <= t and u < self.end)

    def to_dict(self) -> dict[str, Any]:
        """Deterministic JSON-safe record of this fold."""
        return {
            "role": self.role.value,
            "fold_index": self.fold_index,
            "start": self.start.strftime("%Y-%m-%d"),
            "end": self.end.strftime("%Y-%m-%d"),
        }


# ---------------------------------------------------------------------------
# Interval membership (the section 8.1 predicate result)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntervalMembership:
    """The deterministic disposition of one ``[formation, realization_end]``.

    This is the value the section 8.1 predicate returns: it *reports*
    membership or purge and never mutates, truncates, or reassigns anything.
    """

    status: MembershipStatus
    formation_fold: Fold | None = None
    realization_fold: Fold | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, MembershipStatus):
            object.__setattr__(
                self, "status", MembershipStatus(self.status)
            )

    @property
    def included(self) -> bool:
        """Whether the observation belongs to a fold (``INSIDE``)."""
        return self.status is MembershipStatus.INSIDE

    @property
    def purged(self) -> bool:
        """Whether the label crosses a fold boundary and must be excluded."""
        return self.status is MembershipStatus.CROSSES_BOUNDARY

    @property
    def excluded(self) -> bool:
        """Whether the observation belongs to no fold (purged or outside)."""
        return self.status is not MembershipStatus.INSIDE

    @property
    def fold(self) -> Fold | None:
        """The formation fold, when the observation is inside the partition."""
        return self.formation_fold

    @property
    def crossed_boundary(self) -> tuple[Fold, Fold | None] | None:
        """The boundary identity crossed by a purged label, else ``None``.

        Returns ``(formation_fold, realization_fold)`` where
        ``realization_fold`` is ``None`` when the realization endpoint falls
        in a gap or past the end of the partitioned range. Downstream purge
        provenance (per-boundary counts) is derived from this.
        """
        if self.status is not MembershipStatus.CROSSES_BOUNDARY:
            return None
        assert self.formation_fold is not None
        return (self.formation_fold, self.realization_fold)


# ---------------------------------------------------------------------------
# Holdout: key + consumed marker
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Holdout:
    """The evaluation-local final-holdout token.

    ``key`` is the deterministic hash of the split rule and the holdout date
    range; ``consumed`` is the single-use marker. The object is immutable:
    :meth:`mark_consumed` returns a *new* consumed instance, so a frozen
    :class:`Partition` is never mutated in place.
    """

    key: str
    start: pd.Timestamp
    end: pd.Timestamp
    consumed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key:
            raise PartitionValidationError(
                f"holdout key must be a non-empty string, got {self.key!r}"
            )
        object.__setattr__(
            self, "start", _as_calendar_date(self.start, field_name="holdout start")
        )
        object.__setattr__(
            self, "end", _as_calendar_date(self.end, field_name="holdout end")
        )
        if self.start >= self.end:
            raise PartitionValidationError(
                f"holdout range is empty or inverted: {self.start.date()} -> "
                f"{self.end.date()}"
            )
        if not isinstance(self.consumed, bool):
            raise PartitionValidationError(
                f"holdout consumed marker must be a bool, got {self.consumed!r}"
            )

    def mark_consumed(self) -> "Holdout":
        """Return this holdout with the consumed marker set, or fail closed."""
        if self.consumed:
            raise HoldoutAlreadyConsumedError(
                f"holdout {self.key} has already been consumed in this "
                "evaluation context"
            )
        return replace(self, consumed=True)

    def to_dict(self) -> dict[str, Any]:
        """Deterministic JSON-safe record of this holdout token."""
        return {
            "key": self.key,
            "start": self.start.strftime("%Y-%m-%d"),
            "end": self.end.strftime("%Y-%m-%d"),
            "consumed": self.consumed,
        }


class HoldoutRegistry:
    """Evaluation-local single-use holdout consumption.

    One registry belongs to one evaluation context. It records each consumed
    holdout key and refuses a second consumption of the same key. This is the
    *within one evaluation* guarantee only; it makes no cross-experiment or
    cross-process freshness claim (Phase 8 registry scope).
    """

    def __init__(self) -> None:
        self._consumed: dict[str, Holdout] = {}

    @staticmethod
    def _holdout_of(target: "Partition | Holdout") -> Holdout:
        if isinstance(target, Holdout):
            return target
        if isinstance(target, Partition):
            holdout = target.holdout
            if holdout is None:
                raise PartitionValidationError(
                    "partition defines no final-holdout fold, so it has no "
                    "holdout that can be consumed"
                )
            return holdout
        raise PartitionValidationError(
            "holdout consumption requires a Holdout or a Partition, got "
            f"{type(target).__name__}"
        )

    def is_consumed(self, target: "Partition | Holdout | str") -> bool:
        """Whether ``target``'s holdout key has been consumed in this context."""
        if isinstance(target, str):
            return target in self._consumed
        return self._holdout_of(target).key in self._consumed

    def consume(self, target: "Partition | Holdout") -> Holdout:
        """Consume the holdout exactly once, returning the consumed marker.

        Raises :class:`HoldoutAlreadyConsumedError` on a second consumption of
        the same key within this registry.
        """
        holdout = self._holdout_of(target)
        if holdout.key in self._consumed:
            raise HoldoutAlreadyConsumedError(
                f"holdout {holdout.key} has already been consumed in this "
                "evaluation context; a final holdout is single-use within one "
                "evaluation"
            )
        consumed = holdout.mark_consumed()
        self._consumed[holdout.key] = consumed
        return consumed

    @property
    def consumed_keys(self) -> frozenset[str]:
        """The holdout keys consumed so far in this context."""
        return frozenset(self._consumed)

    def __len__(self) -> int:
        return len(self._consumed)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"HoldoutRegistry(consumed={len(self._consumed)})"


# ---------------------------------------------------------------------------
# The partition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Partition:
    """A frozen temporal partition with a deterministic identity.

    Parameters
    ----------
    folds:
        The explicit fold ranges. They are canonicalized into deterministic
        order (ascending ``start``), must be non-empty, must not overlap, and
        must satisfy the structural rules below. Gaps between folds are
        permitted (a gap is simply a range belonging to no fold).
    split_rule:
        Deterministic identifier of the split rule that produced these
        boundaries (e.g. ``"is-oos-holdout-v1"``). It is part of the
        partition identity and of the holdout key, so two partitions can never
        claim the same identity from different rules.
    calendar:
        Optional :class:`~smart_beta.pit.calendar.TradingCalendar` used
        **read-only** to validate/align the explicit boundaries. When
        supplied, every fold boundary inside the calendar's span must be a
        trading day and every fold must contain at least one trading day.
        When omitted, boundaries are plain ordered dates. The calendar is a
        validation input only and is excluded from equality/identity, because
        the identity is over the explicit boundaries themselves.

    Structural rules (fail-closed)
    ------------------------------
    * at most one ``is``, one ``oos`` and one ``holdout`` fold;
    * if both ``is`` and ``oos`` exist, the ``is`` range precedes the ``oos``
      range (IS before OOS, by definition);
    * ``walk_forward`` folds carry unique, contiguous, time-ascending
      ``fold_index`` values ``0..n-1``;
    * a ``holdout`` fold, when present, is the chronologically last fold
      (the *final* holdout);
    * folds never overlap.
    """

    folds: tuple[Fold, ...]
    split_rule: str
    calendar: TradingCalendar | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "folds", _coerce_folds(self.folds))
        object.__setattr__(self, "split_rule", _validate_split_rule(self.split_rule))
        self._validate_structure()
        if self.calendar is not None:
            self._validate_calendar()

    # -- validation --------------------------------------------------------
    def _validate_structure(self) -> None:
        folds = self.folds
        by_role: dict[FoldRole, list[Fold]] = {role: [] for role in FoldRole}
        for fold in folds:
            by_role[fold.role].append(fold)

        for role in (FoldRole.IS, FoldRole.OOS, FoldRole.HOLDOUT):
            if len(by_role[role]) > 1:
                raise PartitionValidationError(
                    f"a partition may define at most one {role.value!r} fold, "
                    f"got {len(by_role[role])}"
                )

        if by_role[FoldRole.IS] and by_role[FoldRole.OOS]:
            is_fold = by_role[FoldRole.IS][0]
            oos_fold = by_role[FoldRole.OOS][0]
            if is_fold.end > oos_fold.start:
                raise PartitionValidationError(
                    "the in-sample fold must precede the out-of-sample fold; "
                    f"IS ends {is_fold.end.date()}, OOS starts "
                    f"{oos_fold.start.date()}"
                )

        walk_forward = by_role[FoldRole.WALK_FORWARD]
        indices = [fold.fold_index for fold in walk_forward]
        if indices != list(range(len(walk_forward))):
            raise PartitionValidationError(
                "walk_forward fold indices must be unique, contiguous from 0 "
                f"and ascending in time, got {indices}"
            )

        if by_role[FoldRole.HOLDOUT] and folds[-1].role is not FoldRole.HOLDOUT:
            raise PartitionValidationError(
                "the holdout fold must be the chronologically final fold; got "
                f"last fold {folds[-1].role.value!r}"
            )

    def _validate_calendar(self) -> None:
        calendar = self.calendar
        if not isinstance(calendar, TradingCalendar):  # pragma: no cover - defensive
            raise PartitionValidationError(
                "calendar must be a TradingCalendar, got "
                f"{type(calendar).__name__}"
            )
        if len(calendar) == 0:
            raise PartitionValidationError(
                "calendar must contain at least one trading date"
            )
        last_trading_date = calendar.dates[-1]

        for fold in self.folds:
            if not calendar.is_trading_day(fold.start):
                raise PartitionValidationError(
                    f"fold {fold.role.value} start {fold.start.date()} is not a "
                    "trading day in the supplied calendar"
                )
            if fold.end <= last_trading_date and not calendar.is_trading_day(
                fold.end
            ):
                raise PartitionValidationError(
                    f"fold {fold.role.value} end {fold.end.date()} is inside the "
                    "calendar but is not a trading day; boundaries must be "
                    "calendar-aligned"
                )
            lo = int(calendar.dates.searchsorted(fold.start, side="left"))
            hi = int(calendar.dates.searchsorted(fold.end, side="left"))
            if lo >= hi:
                raise PartitionValidationError(
                    f"fold {fold.role.value} "
                    f"[{fold.start.date()}, {fold.end.date()}) contains no "
                    "trading day in the supplied calendar"
                )

    # -- introspection -----------------------------------------------------
    @property
    def is_folds(self) -> tuple[Fold, ...]:
        return tuple(f for f in self.folds if f.role is FoldRole.IS)

    @property
    def oos_folds(self) -> tuple[Fold, ...]:
        return tuple(f for f in self.folds if f.role is FoldRole.OOS)

    @property
    def walk_forward_folds(self) -> tuple[Fold, ...]:
        return tuple(f for f in self.folds if f.role is FoldRole.WALK_FORWARD)

    @property
    def holdout_fold(self) -> Fold | None:
        """The final-holdout fold, or ``None`` when this split has none."""
        for fold in self.folds:
            if fold.role is FoldRole.HOLDOUT:
                return fold
        return None

    @property
    def partition_id(self) -> str:
        """Deterministic identity: SHA-256 of the split rule + date ranges."""
        return partition_id(self)

    # ``content_hash`` is an alias kept for the Phase-7 record vocabulary.
    @property
    def content_hash(self) -> str:
        """Alias of :attr:`partition_id`."""
        return self.partition_id

    @property
    def holdout_key(self) -> str | None:
        """Deterministic key of the final holdout, or ``None`` if absent."""
        return holdout_key(self)

    @property
    def holdout(self) -> Holdout | None:
        """The final-holdout token (unconsumed), or ``None`` if absent."""
        fold = self.holdout_fold
        if fold is None:
            return None
        return Holdout(
            key=holdout_key(self), start=fold.start, end=fold.end, consumed=False
        )

    # -- the section 8.1 boundary predicate --------------------------------
    def fold_containing(self, timestamp: date | pd.Timestamp) -> Fold | None:
        """The unique fold containing ``timestamp``, or ``None``.

        A date exactly on a shared boundary belongs to the *later* fold
        (``start`` inclusive, ``end`` exclusive).
        """
        ts = _as_calendar_date(timestamp, field_name="timestamp")
        for fold in self.folds:
            if fold.start <= ts < fold.end:
                return fold
        return None

    def classify_interval(
        self,
        formation: date | pd.Timestamp,
        realization_end: date | pd.Timestamp,
    ) -> IntervalMembership:
        """Classify ``[formation, realization_end]`` against this partition.

        This is the section 8.1 partition-boundary predicate. It takes only
        interval endpoints and the partition -- it never inspects, imports, or
        infers any return data -- and answers whether the observation is
        entirely inside one fold (``INSIDE``), must be purged because its
        realization interval crosses a fold boundary (``CROSSES_BOUNDARY``),
        or is not part of this partition at all (``OUTSIDE``).

        ``realization_end`` is the *last date of the realization interval*
        (``t + h``); passing a value before ``formation`` fails closed.
        """
        t = _as_calendar_date(formation, field_name="formation timestamp")
        u = _as_calendar_date(realization_end, field_name="realization_end")
        if u < t:
            raise PartitionValidationError(
                f"realization_end {u.date()} is before formation {t.date()}; "
                "the realization interval is malformed"
            )
        formation_fold = self.fold_containing(t)
        realization_fold = self.fold_containing(u)
        if formation_fold is None:
            return IntervalMembership(
                MembershipStatus.OUTSIDE, None, realization_fold
            )
        if realization_fold is formation_fold:
            return IntervalMembership(
                MembershipStatus.INSIDE, formation_fold, realization_fold
            )
        return IntervalMembership(
            MembershipStatus.CROSSES_BOUNDARY, formation_fold, realization_fold
        )

    def contains_interval(
        self,
        formation: date | pd.Timestamp,
        realization_end: date | pd.Timestamp,
    ) -> bool:
        """Boolean form of :meth:`classify_interval`.

        ``True`` only when the formation timestamp and the complete
        realization interval lie entirely inside one fold.
        """
        return self.classify_interval(formation, realization_end).included

    # -- serialization -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Deterministic JSON-safe record (folds + split rule + identity)."""
        return {
            "split_rule": self.split_rule,
            "folds": [fold.to_dict() for fold in self.folds],
            "partition_id": self.partition_id,
            "holdout_key": self.holdout_key,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Partition":
        """Rebuild a partition from its canonical serialized form.

        A supplied ``partition_id``/``holdout_key`` must match the recomputed
        values, so a tampered or stale provenance record fails closed instead
        of being silently accepted. The (validation-only) calendar is not
        serialized and is not reconstructed.
        """
        if not isinstance(payload, Mapping):
            raise PartitionValidationError(
                f"serialized Partition must be a mapping, got "
                f"{type(payload).__name__}"
            )
        keys = set(payload)
        missing = sorted({"split_rule", "folds"} - keys)
        extra = sorted(keys - {"split_rule", "folds", "partition_id", "holdout_key"})
        if missing:
            raise PartitionValidationError(
                f"serialized Partition is missing required keys {missing}"
            )
        if extra:
            raise PartitionValidationError(
                f"serialized Partition has unsupported keys {extra}"
            )
        raw_folds = payload["folds"]
        if isinstance(raw_folds, (str, bytes)) or not isinstance(raw_folds, Iterable):
            raise PartitionValidationError(
                "serialized 'folds' must be a sequence of fold records, got "
                f"{type(raw_folds).__name__}"
            )
        partition = cls(
            folds=tuple(_fold_from_dict(item) for item in raw_folds),
            split_rule=payload["split_rule"],
        )
        if "partition_id" in payload and payload["partition_id"] != partition.partition_id:
            raise PartitionValidationError(
                "serialized 'partition_id' does not match the canonical "
                f"identity (declared {payload['partition_id']!r}, computed "
                f"{partition.partition_id!r})"
            )
        if "holdout_key" in payload and payload["holdout_key"] != partition.holdout_key:
            raise PartitionValidationError(
                "serialized 'holdout_key' does not match the canonical "
                f"holdout key (declared {payload['holdout_key']!r}, computed "
                f"{partition.holdout_key!r})"
            )
        return partition


# ---------------------------------------------------------------------------
# Canonicalization / identity
# ---------------------------------------------------------------------------


def _coerce_folds(folds: Any) -> tuple[Fold, ...]:
    """Validate, canonicalize, and de-overlap the fold sequence, fail closed."""
    if isinstance(folds, Fold):
        raise PartitionValidationError(
            "folds must be an iterable of Fold instances, not a single Fold"
        )
    if isinstance(folds, (str, bytes)) or not isinstance(folds, Iterable):
        raise PartitionValidationError(
            f"folds must be an iterable of Fold instances, got "
            f"{type(folds).__name__}"
        )
    collected: list[Fold] = []
    for item in folds:
        if not isinstance(item, Fold):
            raise PartitionValidationError(
                f"every fold must be a Fold, got {type(item).__name__}"
            )
        collected.append(item)
    if not collected:
        raise PartitionValidationError(
            "a partition must define at least one fold; an empty partition is "
            "malformed"
        )

    ordered = sorted(
        collected,
        key=lambda f: (
            f.start,
            f.end,
            _ROLE_ORDER[f.role],
            -1 if f.fold_index is None else f.fold_index,
        ),
    )
    for previous, following in zip(ordered, ordered[1:]):
        if previous.end > following.start:
            raise PartitionValidationError(
                "partition folds must not overlap: "
                f"{previous.role.value} [{previous.start.date()}, "
                f"{previous.end.date()}) overlaps {following.role.value} "
                f"[{following.start.date()}, {following.end.date()})"
            )
    return tuple(ordered)


_FOLD_KEYS = frozenset({"role", "fold_index", "start", "end"})


def _fold_from_dict(value: Any) -> Fold:
    if not isinstance(value, Mapping):
        raise PartitionValidationError(
            f"serialized fold must be a mapping, got {type(value).__name__}"
        )
    keys = set(value)
    if keys != _FOLD_KEYS:
        raise PartitionValidationError(
            f"serialized fold must have exactly keys {sorted(_FOLD_KEYS)}, "
            f"got {sorted(keys)}"
        )
    return Fold(
        role=value["role"],
        start=value["start"],
        end=value["end"],
        fold_index=value["fold_index"],
    )


def _partition_content(partition: Partition) -> dict[str, Any]:
    """The exact content the partition identity hashes over."""
    return {
        "split_rule": partition.split_rule,
        "folds": [fold.to_dict() for fold in partition.folds],
    }


def canonical_json(partition: Partition) -> str:
    """Deterministic canonical JSON of a partition's temporal content.

    Sorted keys, no insignificant whitespace, ASCII-only. Contains only the
    split-rule identifier and the explicit fold boundaries -- never any
    return, factor, metric, or provider content.
    """
    if not isinstance(partition, Partition):
        raise PartitionValidationError(
            f"canonical_json requires a Partition, got {type(partition).__name__}"
        )
    return json.dumps(
        _partition_content(partition),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def partition_id(partition: Partition) -> str:
    """Deterministic SHA-256 identity of a partition (rule + date ranges)."""
    return hashlib.sha256(canonical_json(partition).encode("utf-8")).hexdigest()


def holdout_key(partition: Partition) -> str | None:
    """Deterministic holdout key: SHA-256 of the split rule + holdout range.

    ``None`` when the partition defines no final-holdout fold. The key covers
    only the split rule and the holdout's own date range, so an unrelated
    change to the IS/OOS boundaries leaves the holdout key unchanged.
    """
    if not isinstance(partition, Partition):
        raise PartitionValidationError(
            f"holdout_key requires a Partition, got {type(partition).__name__}"
        )
    fold = partition.holdout_fold
    if fold is None:
        return None
    payload = {"split_rule": partition.split_rule, "holdout": fold.to_dict()}
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
