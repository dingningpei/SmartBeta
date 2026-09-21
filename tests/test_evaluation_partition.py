"""Tests for :mod:`smart_beta.evaluation.partition` (Phase 7, task P7-A).

Covers the frozen contract in ``worker_tasks/phase7/phase7-plan.md`` sections
6.3 and 8.1:

- deterministic partition identity (split rule + date range);
- explicit, documented boundary inclusivity/exclusivity (``[start, end)``);
- fail-closed rejection of malformed / overlapping / empty partitions;
- walk-forward fold semantics (indexed, contiguous, never reassigned);
- the section 8.1 membership predicate for in-partition vs cross-boundary
  intervals, exercised at the IS->OOS boundary, the OOS->final-holdout
  boundary, and walk-forward fold boundaries;
- evaluation-local single-use holdout key + consumed marker;
- no future-return / factor / provider authority leak (structural check on
  the module itself).
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pandas as pd
import pytest

from smart_beta.evaluation import partition as partition_module
from smart_beta.evaluation.partition import (
    Fold,
    FoldRole,
    Holdout,
    HoldoutAlreadyConsumedError,
    HoldoutRegistry,
    IntervalMembership,
    MembershipStatus,
    Partition,
    PartitionValidationError,
    canonical_json,
    holdout_key,
    partition_id,
)
from smart_beta.pit.calendar import TradingCalendar

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

TRIPLE_FOLDS = (
    Fold(FoldRole.IS, "2024-01-02", "2024-01-10"),
    Fold(FoldRole.OOS, "2024-01-10", "2024-01-22"),
    Fold(FoldRole.HOLDOUT, "2024-01-22", "2024-02-01"),
)
SPLIT_RULE = "is-oos-holdout-v1"


@pytest.fixture()
def calendar() -> TradingCalendar:
    """Every weekday of January 2024 (no holidays)."""
    return TradingCalendar.from_weekdays_excluding_holidays(
        "2024-01-01", "2024-01-31"
    )


@pytest.fixture()
def partition(calendar: TradingCalendar) -> Partition:
    return Partition(
        folds=TRIPLE_FOLDS, split_rule=SPLIT_RULE, calendar=calendar
    )


def _partition(
    folds: tuple[Fold, ...] = TRIPLE_FOLDS,
    split_rule: str = SPLIT_RULE,
    calendar: TradingCalendar | None = None,
) -> Partition:
    return Partition(folds=folds, split_rule=split_rule, calendar=calendar)


def _walk_forward_partition(calendar: TradingCalendar | None = None) -> Partition:
    return Partition(
        folds=(
            Fold(FoldRole.WALK_FORWARD, "2024-01-02", "2024-01-10", 0),
            Fold(FoldRole.WALK_FORWARD, "2024-01-10", "2024-01-22", 1),
            Fold(FoldRole.WALK_FORWARD, "2024-01-22", "2024-02-01", 2),
        ),
        split_rule="walk-forward-3",
        calendar=calendar,
    )


# ---------------------------------------------------------------------------
# Fold construction and the frozen role vocabulary
# ---------------------------------------------------------------------------


def test_fold_roles_are_the_frozen_vocabulary() -> None:
    assert [role.value for role in FoldRole] == ["is", "oos", "walk_forward", "holdout"]


def test_fold_role_accepts_str_and_enum() -> None:
    from_string = Fold("is", "2024-01-02", "2024-01-10")
    from_enum = Fold(FoldRole.IS, "2024-01-02", "2024-01-10")
    assert from_string == from_enum


def test_fold_index_required_for_walk_forward() -> None:
    with pytest.raises(PartitionValidationError, match="fold_index"):
        Fold(FoldRole.WALK_FORWARD, "2024-01-02", "2024-01-10")


def test_fold_index_forbidden_for_other_roles() -> None:
    with pytest.raises(PartitionValidationError, match="fold_index"):
        Fold(FoldRole.IS, "2024-01-02", "2024-01-10", 0)
    with pytest.raises(PartitionValidationError, match="fold_index"):
        Fold(FoldRole.HOLDOUT, "2024-01-02", "2024-01-10", 0)


def test_fold_index_rejects_bool_and_negative() -> None:
    with pytest.raises(PartitionValidationError, match="fold_index"):
        Fold(FoldRole.WALK_FORWARD, "2024-01-02", "2024-01-10", True)
    with pytest.raises(PartitionValidationError, match="fold_index"):
        Fold(FoldRole.WALK_FORWARD, "2024-01-02", "2024-01-10", -1)


def test_fold_rejects_empty_or_inverted_range() -> None:
    with pytest.raises(PartitionValidationError, match="empty or inverted"):
        Fold(FoldRole.IS, "2024-01-10", "2024-01-10")
    with pytest.raises(PartitionValidationError, match="empty or inverted"):
        Fold(FoldRole.IS, "2024-01-10", "2024-01-02")


@pytest.mark.parametrize(
    "bad_boundary",
    [
        None,
        True,
        "not-a-date",
        pd.NaT,
        pd.Timestamp("2024-01-10 09:30:00"),
        pd.Timestamp("2024-01-10", tz="UTC"),
    ],
)
def test_fold_rejects_malformed_boundaries(bad_boundary: object) -> None:
    with pytest.raises(PartitionValidationError):
        Fold(FoldRole.IS, bad_boundary, "2024-01-10")  # type: ignore[arg-type]


def test_fold_accepts_plain_dates() -> None:
    from datetime import date

    fold = Fold(FoldRole.IS, date(2024, 1, 2), date(2024, 1, 10))
    assert fold.start == pd.Timestamp("2024-01-02")
    assert fold.end == pd.Timestamp("2024-01-10")


# ---------------------------------------------------------------------------
# Boundary inclusivity / exclusivity (frozen: [start, end))
# ---------------------------------------------------------------------------


def test_fold_start_inclusive_end_exclusive() -> None:
    fold = Fold(FoldRole.IS, "2024-01-02", "2024-01-10")
    assert fold.contains("2024-01-02")
    assert fold.contains("2024-01-09")
    assert not fold.contains("2024-01-10")
    assert not fold.contains("2024-01-01")


def test_shared_boundary_belongs_to_exactly_one_side(partition: Partition) -> None:
    # IS is [2024-01-02, 2024-01-10); OOS starts exactly on 2024-01-10.
    on_boundary = pd.Timestamp("2024-01-10")
    assert partition.fold_containing(on_boundary) is not None
    assert partition.fold_containing(on_boundary).role is FoldRole.OOS

    owners = [f for f in partition.folds if f.contains(on_boundary)]
    assert len(owners) == 1
    assert owners[0].role is FoldRole.OOS


def test_fold_containing_outside_range_is_none(partition: Partition) -> None:
    assert partition.fold_containing("2024-01-01") is None
    assert partition.fold_containing("2024-02-01") is None
    assert partition.fold_containing("2024-03-15") is None


def test_contiguous_folds_share_the_boundary_date(partition: Partition) -> None:
    assert partition.folds[0].end == partition.folds[1].start
    assert partition.folds[1].end == partition.folds[2].start


# ---------------------------------------------------------------------------
# Deterministic partition identity
# ---------------------------------------------------------------------------


def test_partition_id_is_deterministic_and_stable(partition: Partition) -> None:
    assert partition.partition_id == partition_id(partition)
    assert partition.partition_id == partition.content_hash
    assert partition.partition_id == _partition(calendar=None).partition_id
    assert len(partition.partition_id) == 64
    int(partition.partition_id, 16)  # hex


def test_partition_identity_is_input_order_independent() -> None:
    ordered = _partition()
    shuffled = Partition(
        folds=(TRIPLE_FOLDS[2], TRIPLE_FOLDS[0], TRIPLE_FOLDS[1]),
        split_rule=SPLIT_RULE,
    )
    assert ordered.partition_id == shuffled.partition_id
    assert canonical_json(ordered) == canonical_json(shuffled)
    assert [f.role for f in shuffled.folds] == [
        FoldRole.IS,
        FoldRole.OOS,
        FoldRole.HOLDOUT,
    ]


def test_partition_identity_changes_with_split_rule() -> None:
    a = _partition(split_rule="rule-a")
    b = _partition(split_rule="rule-b")
    assert a.partition_id != b.partition_id


def test_partition_identity_changes_with_a_single_boundary_date() -> None:
    base = _partition()
    # Move exactly one boundary date: the shared IS/OOS boundary.
    shifted = _partition(
        folds=(
            Fold(FoldRole.IS, "2024-01-02", "2024-01-11"),
            Fold(FoldRole.OOS, "2024-01-11", "2024-01-22"),
            TRIPLE_FOLDS[2],
        )
    )
    assert base.partition_id != shifted.partition_id


def test_canonical_json_is_sorted_compact_ascii(partition: Partition) -> None:
    payload = canonical_json(partition)
    assert payload == canonical_json(_partition())
    assert payload.startswith('{"folds":[')
    assert ", " not in payload
    assert payload.isascii()


def test_partition_round_trips_through_dict(partition: Partition) -> None:
    payload = partition.to_dict()
    assert payload["partition_id"] == partition.partition_id
    assert payload["holdout_key"] == partition.holdout_key
    rebuilt = Partition.from_dict(payload)
    assert rebuilt.partition_id == partition.partition_id
    assert rebuilt.holdout_key == partition.holdout_key
    assert rebuilt.folds == partition.folds
    # The validation-only calendar is not serialized.
    assert rebuilt.calendar is None


def test_partition_from_dict_rejects_missing_or_extra_keys(partition: Partition) -> None:
    payload = partition.to_dict()
    del payload["split_rule"]
    with pytest.raises(PartitionValidationError, match="missing required keys"):
        Partition.from_dict(payload)

    payload = partition.to_dict()
    payload["returns"] = [1.0, 2.0]
    with pytest.raises(PartitionValidationError, match="unsupported keys"):
        Partition.from_dict(payload)


def test_partition_from_dict_rejects_tampered_identity(partition: Partition) -> None:
    payload = partition.to_dict()
    payload["partition_id"] = "0" * 64
    with pytest.raises(PartitionValidationError, match="partition_id"):
        Partition.from_dict(payload)

    payload = partition.to_dict()
    payload["holdout_key"] = "0" * 64
    with pytest.raises(PartitionValidationError, match="holdout_key"):
        Partition.from_dict(payload)


# ---------------------------------------------------------------------------
# Malformed / overlapping / empty partition rejection
# ---------------------------------------------------------------------------


def test_empty_partition_is_rejected() -> None:
    with pytest.raises(PartitionValidationError, match="at least one fold"):
        Partition(folds=(), split_rule=SPLIT_RULE)
    with pytest.raises(PartitionValidationError, match="at least one fold"):
        Partition(folds=[], split_rule=SPLIT_RULE)


def test_overlapping_folds_are_rejected() -> None:
    with pytest.raises(PartitionValidationError, match="must not overlap"):
        _partition(
            folds=(
                Fold(FoldRole.IS, "2024-01-02", "2024-01-12"),
                Fold(FoldRole.OOS, "2024-01-10", "2024-01-22"),
            )
        )


def test_nested_fold_is_rejected_as_overlap() -> None:
    with pytest.raises(PartitionValidationError, match="must not overlap"):
        _partition(
            folds=(
                Fold(FoldRole.IS, "2024-01-02", "2024-01-22"),
                Fold(FoldRole.OOS, "2024-01-10", "2024-01-12"),
            )
        )


def test_duplicate_single_role_is_rejected() -> None:
    for role in (FoldRole.IS, FoldRole.OOS, FoldRole.HOLDOUT):
        with pytest.raises(PartitionValidationError, match="at most one"):
            _partition(
                folds=(
                    Fold(role, "2024-01-02", "2024-01-10"),
                    Fold(role, "2024-01-10", "2024-01-22"),
                )
            )


def test_is_must_precede_oos() -> None:
    with pytest.raises(PartitionValidationError, match="must precede"):
        _partition(
            folds=(
                Fold(FoldRole.OOS, "2024-01-02", "2024-01-10"),
                Fold(FoldRole.IS, "2024-01-10", "2024-01-22"),
            )
        )


def test_holdout_must_be_final() -> None:
    with pytest.raises(PartitionValidationError, match="chronologically final"):
        _partition(
            folds=(
                Fold(FoldRole.IS, "2024-01-02", "2024-01-10"),
                Fold(FoldRole.HOLDOUT, "2024-01-10", "2024-01-22"),
                Fold(FoldRole.OOS, "2024-01-22", "2024-02-01"),
            )
        )


def test_walk_forward_indices_must_be_contiguous_from_zero() -> None:
    with pytest.raises(PartitionValidationError, match="contiguous"):
        _partition(
            folds=(
                Fold(FoldRole.WALK_FORWARD, "2024-01-02", "2024-01-10", 0),
                Fold(FoldRole.WALK_FORWARD, "2024-01-10", "2024-01-22", 2),
            )
        )


def test_walk_forward_indices_must_be_unique() -> None:
    with pytest.raises(PartitionValidationError, match="contiguous"):
        _partition(
            folds=(
                Fold(FoldRole.WALK_FORWARD, "2024-01-02", "2024-01-10", 0),
                Fold(FoldRole.WALK_FORWARD, "2024-01-10", "2024-01-22", 0),
            )
        )


def test_walk_forward_indices_must_ascend_in_time() -> None:
    with pytest.raises(PartitionValidationError, match="contiguous"):
        _partition(
            folds=(
                Fold(FoldRole.WALK_FORWARD, "2024-01-02", "2024-01-10", 1),
                Fold(FoldRole.WALK_FORWARD, "2024-01-10", "2024-01-22", 0),
            )
        )


def test_non_fold_entries_are_rejected() -> None:
    with pytest.raises(PartitionValidationError, match="must be a Fold"):
        Partition(folds=("2024-01-02",), split_rule=SPLIT_RULE)  # type: ignore[arg-type]
    with pytest.raises(PartitionValidationError, match="not a single Fold"):
        Partition(folds=TRIPLE_FOLDS[0], split_rule=SPLIT_RULE)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_rule", ["", "  ", " rule ", 7, None])
def test_split_rule_must_be_non_empty_clean_text(bad_rule: object) -> None:
    with pytest.raises(PartitionValidationError, match="split_rule"):
        Partition(folds=TRIPLE_FOLDS, split_rule=bad_rule)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Calendar alignment (TradingCalendar used read-only)
# ---------------------------------------------------------------------------


def test_calendar_aligned_partition_is_accepted(calendar: TradingCalendar) -> None:
    partition = _partition(calendar=calendar)
    assert len(partition.folds) == 3


def test_weekend_boundary_is_rejected_with_a_calendar(
    calendar: TradingCalendar,
) -> None:
    # 2024-01-06 is a Saturday.
    with pytest.raises(PartitionValidationError, match="not a trading day"):
        _partition(
            folds=(
                Fold(FoldRole.IS, "2024-01-02", "2024-01-06"),
                Fold(FoldRole.OOS, "2024-01-06", "2024-01-22"),
            ),
            calendar=calendar,
        )


def test_interior_non_trading_end_is_rejected(
    calendar: TradingCalendar,
) -> None:
    # 2024-01-13 is a Saturday, inside the calendar's span.
    with pytest.raises(PartitionValidationError, match="not a trading day"):
        _partition(
            folds=(
                Fold(FoldRole.IS, "2024-01-08", "2024-01-13"),
                Fold(FoldRole.OOS, "2024-01-13", "2024-01-22"),
            ),
            calendar=calendar,
        )


def test_exclusive_tail_end_past_the_calendar_is_allowed(
    calendar: TradingCalendar,
) -> None:
    # The holdout's exclusive end may run past the supplied calendar's last
    # trading date, so a holdout can include the calendar's final day.
    partition = Partition(
        folds=(Fold(FoldRole.HOLDOUT, "2024-01-30", "2024-02-01"),),
        split_rule="holdout-only",
        calendar=calendar,
    )
    assert partition.folds[0].end == pd.Timestamp("2024-02-01")
    assert partition.fold_containing("2024-01-31") is not None


def test_non_trading_start_is_rejected(calendar: TradingCalendar) -> None:
    with pytest.raises(PartitionValidationError, match="not a trading day"):
        _partition(
            folds=(Fold(FoldRole.IS, "2024-01-06", "2024-01-10"),),
            calendar=calendar,
        )


def test_empty_calendar_is_rejected() -> None:
    with pytest.raises(PartitionValidationError, match="at least one trading date"):
        _partition(calendar=TradingCalendar([]))


def test_partition_without_calendar_treats_dates_as_plain_ordered_ranges() -> None:
    # No calendar supplied: a weekend boundary is a plain ordered date and is
    # accepted (the caller owns alignment), but malformed ranges still fail.
    partition = _partition(
        folds=(
            Fold(FoldRole.IS, "2024-01-02", "2024-01-06"),
            Fold(FoldRole.OOS, "2024-01-06", "2024-01-22"),
        )
    )
    assert partition.partition_id


# ---------------------------------------------------------------------------
# Walk-forward fold semantics
# ---------------------------------------------------------------------------


def test_walk_forward_folds_are_exposed_in_index_order(
    calendar: TradingCalendar,
) -> None:
    partition = _walk_forward_partition(calendar)
    assert [f.fold_index for f in partition.walk_forward_folds] == [0, 1, 2]
    assert partition.holdout_fold is None
    assert partition.holdout_key is None
    assert partition.holdout is None


def test_walk_forward_fold_membership_is_disjoint() -> None:
    partition = _walk_forward_partition()
    for fold in partition.walk_forward_folds:
        for other in partition.walk_forward_folds:
            if other is fold:
                continue
            assert not other.contains(fold.start)


# ---------------------------------------------------------------------------
# Section 8.1 membership predicate
# ---------------------------------------------------------------------------


def test_contains_interval_inside_each_fold(partition: Partition) -> None:
    cases = [
        ("2024-01-02", "2024-01-02", FoldRole.IS),
        ("2024-01-04", "2024-01-08", FoldRole.IS),
        ("2024-01-09", "2024-01-09", FoldRole.IS),
        ("2024-01-10", "2024-01-10", FoldRole.OOS),
        ("2024-01-10", "2024-01-19", FoldRole.OOS),
        ("2024-01-22", "2024-01-31", FoldRole.HOLDOUT),
    ]
    for formation, realization, role in cases:
        membership = partition.classify_interval(formation, realization)
        assert membership.status is MembershipStatus.INSIDE, (formation, realization)
        assert membership.included
        assert not membership.purged
        assert membership.fold is not None
        assert membership.fold.role is role
        assert partition.contains_interval(formation, realization)


def test_is_to_oos_crossing_is_purged(partition: Partition) -> None:
    # Formation on the last IS day; the realization interval reaches into OOS.
    membership = partition.classify_interval("2024-01-09", "2024-01-10")
    assert membership.status is MembershipStatus.CROSSES_BOUNDARY
    assert membership.purged
    assert not membership.included
    assert membership.formation_fold.role is FoldRole.IS
    assert membership.realization_fold.role is FoldRole.OOS
    assert not partition.contains_interval("2024-01-09", "2024-01-10")

    # The label is purged from BOTH sides: the OOS fold does not claim it
    # either (no reassignment to retain the observation).
    is_fold, oos_fold, _ = partition.folds
    assert not is_fold.contains_interval("2024-01-09", "2024-01-10")
    assert not oos_fold.contains_interval("2024-01-09", "2024-01-10")


def test_oos_to_final_holdout_crossing_is_purged(partition: Partition) -> None:
    membership = partition.classify_interval("2024-01-19", "2024-01-22")
    assert membership.status is MembershipStatus.CROSSES_BOUNDARY
    assert membership.formation_fold.role is FoldRole.OOS
    assert membership.realization_fold.role is FoldRole.HOLDOUT
    assert not membership.included
    assert not partition.contains_interval("2024-01-19", "2024-01-22")

    # A holdout return never labels an OOS observation, and vice versa.
    is_fold, oos_fold, holdout_fold = partition.folds
    assert not oos_fold.contains_interval("2024-01-19", "2024-01-22")
    assert not holdout_fold.contains_interval("2024-01-19", "2024-01-22")


def test_walk_forward_fold_crossing_is_purged_not_reassigned() -> None:
    partition = _walk_forward_partition()
    membership = partition.classify_interval("2024-01-09", "2024-01-10")
    assert membership.status is MembershipStatus.CROSSES_BOUNDARY
    assert membership.formation_fold.fold_index == 0
    assert membership.realization_fold.fold_index == 1
    assert not membership.included
    assert not partition.contains_interval("2024-01-09", "2024-01-10")

    # Purged from both folds -- never reassigned to fold 1.
    fold_0, fold_1, _ = partition.folds
    assert not fold_0.contains_interval("2024-01-09", "2024-01-10")
    assert not fold_1.contains_interval("2024-01-09", "2024-01-10")

    # Fold-internal labels remain members.
    assert partition.contains_interval("2024-01-03", "2024-01-09")
    assert partition.contains_interval("2024-01-10", "2024-01-19")


def test_realization_at_fold_end_is_purged(partition: Partition) -> None:
    # The exclusive end is not a member of the formation fold, so a label
    # whose realization ends exactly on it crosses (fail-closed).
    membership = partition.classify_interval("2024-01-09", "2024-01-10")
    assert membership.purged

    single = Partition(
        folds=(Fold(FoldRole.IS, "2024-01-02", "2024-01-10"),),
        split_rule="single-fold",
    )
    end_membership = single.classify_interval("2024-01-09", "2024-01-10")
    assert end_membership.status is MembershipStatus.CROSSES_BOUNDARY
    assert end_membership.realization_fold is None  # past the partition range


def test_realization_past_the_partition_range_is_purged(partition: Partition) -> None:
    membership = partition.classify_interval("2024-01-31", "2024-02-05")
    assert membership.status is MembershipStatus.CROSSES_BOUNDARY
    assert membership.formation_fold.role is FoldRole.HOLDOUT
    assert membership.realization_fold is None


def test_gap_realization_is_purged() -> None:
    gapped = _partition(
        folds=(
            Fold(FoldRole.IS, "2024-01-02", "2024-01-10"),
            # 2024-01-10 .. 2024-01-15 belongs to no fold.
            Fold(FoldRole.OOS, "2024-01-15", "2024-01-22"),
        )
    )
    membership = gapped.classify_interval("2024-01-09", "2024-01-16")
    assert membership.status is MembershipStatus.CROSSES_BOUNDARY
    assert membership.realization_fold.role is FoldRole.OOS
    assert not gapped.contains_interval("2024-01-09", "2024-01-16")


def test_formation_outside_partition_is_outside_not_purged() -> None:
    partition = _partition()
    for formation in ("2024-01-01", "2024-02-01", "2025-01-01"):
        membership = partition.classify_interval(formation, formation)
        assert membership.status is MembershipStatus.OUTSIDE
        assert not membership.included
        assert not membership.purged
        assert membership.formation_fold is None
        assert membership.crossed_boundary is None


def test_point_interval_is_inside_when_the_date_is_a_member() -> None:
    partition = _partition()
    membership = partition.classify_interval("2024-01-10", "2024-01-10")
    assert membership.status is MembershipStatus.INSIDE
    assert membership.fold.role is FoldRole.OOS


def test_crossed_boundary_identity_is_reported(partition: Partition) -> None:
    membership = partition.classify_interval("2024-01-09", "2024-01-10")
    crossed = membership.crossed_boundary
    assert crossed is not None
    formation_fold, realization_fold = crossed
    assert formation_fold.role is FoldRole.IS
    assert realization_fold.role is FoldRole.OOS
    assert partition.classify_interval("2024-01-04", "2024-01-04").crossed_boundary is None


def test_realization_before_formation_is_rejected(partition: Partition) -> None:
    with pytest.raises(PartitionValidationError, match="before formation"):
        partition.classify_interval("2024-01-10", "2024-01-04")
    with pytest.raises(PartitionValidationError, match="before formation"):
        partition.contains_interval("2024-01-10", "2024-01-04")


def test_predicate_rejects_non_midnight_and_malformed_dates(
    partition: Partition,
) -> None:
    with pytest.raises(PartitionValidationError, match="midnight"):
        partition.classify_interval(
            pd.Timestamp("2024-01-04 16:00:00"), "2024-01-04"
        )
    with pytest.raises(PartitionValidationError, match="midnight"):
        partition.classify_interval("2024-01-04", pd.Timestamp("2024-01-04 16:00"))
    with pytest.raises(PartitionValidationError):
        partition.classify_interval(pd.NaT, "2024-01-04")


def test_fold_level_predicate_matches_partition_level(partition: Partition) -> None:
    single = Partition(
        folds=(Fold(FoldRole.IS, "2024-01-02", "2024-01-10"),),
        split_rule="single-fold",
    )
    for formation, realization in (
        ("2024-01-02", "2024-01-09"),
        ("2024-01-09", "2024-01-09"),
        ("2024-01-09", "2024-01-10"),
        ("2024-01-01", "2024-01-05"),
    ):
        assert single.folds[0].contains_interval(
            formation, realization
        ) == single.contains_interval(formation, realization)


def test_interval_membership_is_immutable_and_typed() -> None:
    membership = IntervalMembership(
        status="inside",
        formation_fold=TRIPLE_FOLDS[0],
        realization_fold=TRIPLE_FOLDS[0],
    )
    assert membership.status is MembershipStatus.INSIDE
    with pytest.raises(dataclasses.FrozenInstanceError):
        membership.status = MembershipStatus.OUTSIDE  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Holdout key + evaluation-local single-use
# ---------------------------------------------------------------------------


def test_holdout_key_is_deterministic_and_rule_scoped(partition: Partition) -> None:
    assert partition.holdout_key == holdout_key(partition)
    assert partition.holdout.key == partition.holdout_key
    assert partition.holdout.consumed is False
    assert partition.holdout.start == pd.Timestamp("2024-01-22")
    assert partition.holdout.end == pd.Timestamp("2024-02-01")
    assert len(partition.holdout_key) == 64

    same_holdout_other_rule = _partition(split_rule="rule-b")
    assert same_holdout_other_rule.holdout_key != partition.holdout_key


def test_holdout_key_covers_only_rule_and_holdout_range() -> None:
    base = _partition()
    # Only the IS/OOS boundaries move; the holdout range is untouched.
    moved_is = _partition(
        folds=(
            Fold(FoldRole.IS, "2024-01-02", "2024-01-11"),
            Fold(FoldRole.OOS, "2024-01-11", "2024-01-22"),
            TRIPLE_FOLDS[2],
        )
    )
    assert moved_is.partition_id != base.partition_id
    assert moved_is.holdout_key == base.holdout_key


def test_partition_without_holdout_has_no_key() -> None:
    partition = Partition(
        folds=(Fold(FoldRole.IS, "2024-01-02", "2024-01-10"),),
        split_rule="is-only",
    )
    assert partition.holdout_key is None
    assert partition.holdout is None
    assert partition.holdout_fold is None


def test_holdout_can_be_consumed_exactly_once_per_context(
    partition: Partition,
) -> None:
    registry = HoldoutRegistry()
    assert not registry.is_consumed(partition)
    consumed = registry.consume(partition)
    assert consumed.consumed is True
    assert consumed.key == partition.holdout_key
    assert registry.is_consumed(partition)
    assert registry.is_consumed(partition.holdout_key)
    assert registry.consumed_keys == frozenset({partition.holdout_key})
    assert len(registry) == 1

    with pytest.raises(HoldoutAlreadyConsumedError, match="already been consumed"):
        registry.consume(partition)


def test_consuming_does_not_mutate_the_frozen_partition(
    partition: Partition,
) -> None:
    registry = HoldoutRegistry()
    registry.consume(partition)
    assert partition.holdout.consumed is False


def test_holdout_single_use_is_evaluation_local(partition: Partition) -> None:
    # A fresh registry is a fresh evaluation context: this is explicitly NOT a
    # cross-experiment freshness claim (that is Phase 8 registry scope).
    first = HoldoutRegistry()
    second = HoldoutRegistry()
    first.consume(partition)
    assert second.is_consumed(partition) is False
    assert second.consume(partition).consumed is True


def test_holdout_token_marks_consumed_immutably() -> None:
    token = Holdout("key-1", "2024-01-22", "2024-02-01")
    consumed = token.mark_consumed()
    assert consumed is not token
    assert consumed.consumed is True
    assert token.consumed is False
    with pytest.raises(HoldoutAlreadyConsumedError):
        consumed.mark_consumed()


def test_consuming_a_partition_without_holdout_fails_closed() -> None:
    partition = Partition(
        folds=(Fold(FoldRole.IS, "2024-01-02", "2024-01-10"),),
        split_rule="is-only",
    )
    with pytest.raises(PartitionValidationError, match="no final-holdout"):
        HoldoutRegistry().consume(partition)


def test_holdout_registry_rejects_non_partition_targets() -> None:
    with pytest.raises(PartitionValidationError, match="Holdout or a Partition"):
        HoldoutRegistry().consume("not-a-partition")  # type: ignore[arg-type]


def test_holdout_validation_fail_closed() -> None:
    with pytest.raises(PartitionValidationError, match="non-empty string"):
        Holdout("", "2024-01-22", "2024-02-01")
    with pytest.raises(PartitionValidationError, match="empty or inverted"):
        Holdout("k", "2024-02-01", "2024-01-22")
    with pytest.raises(PartitionValidationError, match="consumed marker"):
        Holdout("k", "2024-01-22", "2024-02-01", consumed="yes")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# No future-return / factor / provider authority leak (structural)
# ---------------------------------------------------------------------------

_ALLOWED_IMPORTS = {
    "__future__",
    "hashlib",
    "json",
    "dataclasses",
    "datetime",
    "enum",
    "typing",
    "pandas",
    "smart_beta.pit.calendar",
}

# Identifiers that would indicate this module had taken over return, factor,
# provider, metric, or portfolio authority that belongs to another task.
_FORBIDDEN_IDENTIFIERS = {
    "get_realized_returns",
    "realized_returns",
    "forward_returns",
    "forward_return",
    "factor_panel",
    "factor_values",
    "read_csv",
    "read_parquet",
    "read_json",
    "load_panel",
    "market_cap",
    "PointInTimeView",
    "as_of",
    "regress",
    "compute_metrics",
}

# The complete public field surface of the temporal objects; anything beyond
# this would be a data/metric/factor field smuggled into a temporal contract.
_ALLOWED_PARTITION_FIELDS = {"folds", "split_rule", "calendar"}
_ALLOWED_FOLD_FIELDS = {"role", "start", "end", "fold_index"}
_ALLOWED_HOLDOUT_FIELDS = {"key", "start", "end", "consumed"}


def _module_tree() -> ast.Module:
    source = pathlib.Path(partition_module.__file__).read_text(encoding="utf-8")
    return ast.parse(source)


def test_partition_module_imports_only_temporal_authority() -> None:
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in _ALLOWED_IMPORTS, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module in _ALLOWED_IMPORTS, node.module


def test_partition_module_has_no_data_authority_identifiers() -> None:
    found: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            found.add(node.arg)
    leaked = found & _FORBIDDEN_IDENTIFIERS
    assert leaked == set(), f"partition.py references forbidden identifiers {leaked}"


def test_temporal_objects_expose_only_temporal_fields() -> None:
    partition_fields = {f.name for f in dataclasses.fields(Partition)}
    fold_fields = {f.name for f in dataclasses.fields(Fold)}
    holdout_fields = {f.name for f in dataclasses.fields(Holdout)}
    assert partition_fields <= _ALLOWED_PARTITION_FIELDS
    assert fold_fields <= _ALLOWED_FOLD_FIELDS
    assert holdout_fields <= _ALLOWED_HOLDOUT_FIELDS

    for field_name in (
        partition_fields | fold_fields | holdout_fields
    ):
        assert field_name not in _FORBIDDEN_IDENTIFIERS


def test_partition_module_has_no_io_helpers() -> None:
    module_names = set(dir(partition_module))
    for forbidden in ("open", "read_csv", "read_parquet", "urlopen", "connect"):
        assert forbidden not in module_names
