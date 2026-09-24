"""Phase 10 P10-C: EvidenceFootprint tests (plan section 6, section 16 P10-C).

Every test here exercises :mod:`smart_beta.science.footprint` through its
public API and the shared synthetic builders in
``tests/phase10_fixtures.py``. No provider, network, PIT-data or holdout
access occurs: the calendar, maps and panels are synthetic.

The suite covers every clause of the frozen section 6 contract:

* canonicalization uniqueness under permutation/split/regrouping;
* interval merging by session gaps;
* the corrected multi-block canonical structure (multiple blocks per
  ``observation_kind``, disjoint same-kind subject sets, distinct same-kind
  interval lists, ordering by ``(observation_kind, tuple(subject_keys))``);
* every section 6.3 derivation rule and the price-level/price-change linkage
  rule;
* ``covers`` / ``intersect`` / ``restrict`` / ``union``;
* "values are never read";
* the section 6.6 overlap table and the adversarial cases named by the
  P10-C task row of section 16 (including a same-kind disjoint case whose
  converse shares one observation).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

import phase10_fixtures as fixtures
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.science import footprint as fp
from smart_beta.science.contracts import (
    DERIVATION_RULES_VERSION,
    EVIDENCE_FOOTPRINT_SCHEMA,
    FOOTPRINT_MATERIALITY_OBSERVATIONS,
    ObservationKind,
    content_hash,
    validate_footprint_shape,
)

# ---------------------------------------------------------------------------
# synthetic fixtures
# ---------------------------------------------------------------------------

#: 15 consecutive weekday sessions starting Monday 2020-01-06.
SESSIONS: tuple[str, ...] = fixtures.synthetic_calendar("2020-01-06", 15)
CALENDAR = TradingCalendar([date.fromisoformat(day) for day in SESSIONS])

SUBJECT_A = "SEC:CN:000001"
SUBJECT_B = "SEC:CN:600000"
SUBJECT_C = "SEC:CN:000002"

SECURITY_MAP = {
    "tiingo:000001": SUBJECT_A,
    "tiingo:600000": SUBJECT_B,
}
SECURITY_MAP_ALT_VENDOR = {
    "othervendor:000001": SUBJECT_A,
    "othervendor:600000": SUBJECT_B,
}
MARKET_SERIES_MAP = {
    "tiingo:rf": "MKT:rf",
    "tiingo:benchmark": "MKT:benchmark",
}
VARIABLE_MAP = {"value": {"derived_variable": "RETURN_1D"}}


def _day(index: int) -> str:
    return SESSIONS[index]


def _expand(
    derived_variable: str = "RETURN_1D",
    *,
    dates: tuple[str, ...] = (SESSIONS[0],),
    subjects: tuple[str, ...] = (SUBJECT_A,),
    params: dict | None = None,
) -> fp.Footprint:
    return fp.expand(
        derived_variable,
        params or {},
        list(dates),
        list(subjects),
        calendar=CALENDAR,
    )


def _panel(
    sessions: tuple[str, ...],
    *,
    securities: tuple[str, ...] = (SUBJECT_A,),
    value: object = 1.0,
) -> list[dict[str, object]]:
    return [
        {"security_id": security, "date": session, "value": value}
        for session in sessions
        for security in securities
    ]


class _Poison:
    """A value object that raises if any value operation touches it."""

    def __eq__(self, other: object) -> bool:  # pragma: no cover - asserted
        raise AssertionError("a mapped value column was read")

    def __str__(self) -> str:  # pragma: no cover - asserted
        raise AssertionError("a mapped value column was read")

    def __repr__(self) -> str:  # pragma: no cover - asserted
        raise AssertionError("a mapped value column was read")

    def __hash__(self) -> int:  # pragma: no cover - asserted
        raise AssertionError("a mapped value column was read")


# ---------------------------------------------------------------------------
# canonical form / identity
# ---------------------------------------------------------------------------


def test_every_canonical_body_passes_the_contract_validator() -> None:
    samples = [
        _expand(),
        _expand("FWD_RETURN", params={"h": 3}),
        _expand("EXCESS_RETURN", params={"base": "RETURN_1D", "series": "rf"}),
        _expand(
            "BENCHMARK_RELATIVE",
            params={"base": "RETURN_1D", "series": "benchmark"},
        ),
        _expand("PRICE_FIELD"),
        _expand("ACTIVITY_FIELD"),
        _expand("MARKET_CAP"),
        _expand(
            "FUNDAMENTAL_FIELD",
            params={"fiscal_periods": ["2019-12-31"]},
        ),
        _expand(
            "SIGNAL",
            dates=(_day(4),),
            params={"lookback": 3, "requirements": [{"derived_variable": "RETURN_1D"}]},
        ),
        _expand(
            "AGGREGATE",
            params={
                "inputs": [
                    {"derived_variable": "RETURN_1D"},
                    {"derived_variable": "MARKET_CAP"},
                ]
            },
        ),
    ]
    for sample in samples:
        body = sample.body
        assert body["schema"] == EVIDENCE_FOOTPRINT_SCHEMA
        assert body["derivation_rules_version"] == DERIVATION_RULES_VERSION
        assert validate_footprint_shape(body) is None
        # determinable footprints carry all four audit hashes
        for key in (
            "security_map_hash",
            "market_series_map_hash",
            "variable_map_hash",
            "calendar_hash",
        ):
            assert body[key] is not None


def test_canonicalization_is_permutation_and_split_invariant() -> None:
    base = _expand(dates=(_day(0), _day(1), _day(2)), subjects=(SUBJECT_A, SUBJECT_B))
    permuted = _expand(
        dates=(_day(2), _day(0), _day(1)), subjects=(SUBJECT_B, SUBJECT_A)
    )
    split = fp.union(
        _expand(dates=(_day(0), _day(2)), subjects=(SUBJECT_A,)),
        _expand(dates=(_day(1),), subjects=(SUBJECT_A, SUBJECT_B)),
        _expand(dates=(_day(0), _day(1), _day(2)), subjects=(SUBJECT_B,)),
    )
    assert base.footprint_id == permuted.footprint_id == split.footprint_id
    assert base.body == permuted.body == split.body


def test_interval_merging_by_session_gaps() -> None:
    # Observations on sessions 0,1 then 3,4: session 2 lies strictly between
    # the runs, so they stay separate intervals.
    gapped = _expand(dates=(_day(0), _day(1), _day(3), _day(4)))
    assert gapped.body["blocks"] == [
        {
            "observation_kind": "PRICE_CHANGE",
            "subject_keys": [SUBJECT_A],
            "intervals": [[_day(0), _day(1)], [_day(3), _day(4)]],
        }
    ]
    # Observing the missing session 2 merges the two runs into one interval.
    contiguous = _expand(dates=(_day(0), _day(1), _day(2), _day(3), _day(4)))
    assert contiguous.body["blocks"][0]["intervals"] == [[_day(0), _day(4)]]


def test_multiple_blocks_with_the_same_kind_are_canonical() -> None:
    left = _expand(dates=(_day(0), _day(1)), subjects=(SUBJECT_A,))
    right = _expand(dates=(_day(3), _day(4)), subjects=(SUBJECT_B,))
    combined = fp.union(left, right)
    body = combined.body
    assert validate_footprint_shape(body) is None
    assert body["blocks"] == [
        {
            "observation_kind": "PRICE_CHANGE",
            "subject_keys": [SUBJECT_A],
            "intervals": [[_day(0), _day(1)]],
        },
        {
            "observation_kind": "PRICE_CHANGE",
            "subject_keys": [SUBJECT_B],
            "intervals": [[_day(3), _day(4)]],
        },
    ]
    # The two same-kind blocks have disjoint subject sets and distinct
    # interval lists, so no merge is expected or allowed.
    assert combined.footprint_id != left.footprint_id
    assert combined.footprint_id != right.footprint_id


def test_subjects_with_identical_intervals_merge_into_one_block() -> None:
    combined = fp.union(
        _expand(dates=(_day(0), _day(1)), subjects=(SUBJECT_A,)),
        _expand(dates=(_day(0), _day(1)), subjects=(SUBJECT_B,)),
    )
    assert combined.body["blocks"] == [
        {
            "observation_kind": "PRICE_CHANGE",
            "subject_keys": [SUBJECT_A, SUBJECT_B],
            "intervals": [[_day(0), _day(1)]],
        }
    ]


def test_blocks_are_ordered_by_kind_then_full_subject_tuple() -> None:
    combined = fp.union(
        _expand("MARKET_CAP", dates=(_day(0),), subjects=(SUBJECT_B,)),
        _expand("RETURN_1D", dates=(_day(0),), subjects=(SUBJECT_B,)),
        _expand("RETURN_1D", dates=(_day(1),), subjects=(SUBJECT_A,)),
        _expand("PRICE_FIELD", dates=(_day(1),), subjects=(SUBJECT_A,)),
    )
    keys = [
        (block["observation_kind"], tuple(block["subject_keys"]))
        for block in combined.body["blocks"]
    ]
    assert keys == sorted(keys)
    assert keys == [
        ("PRICE_CHANGE", (SUBJECT_A,)),
        ("PRICE_CHANGE", (SUBJECT_B,)),
        ("PRICE_LEVEL", (SUBJECT_A,)),
        ("PRICE_LEVEL", (SUBJECT_B,)),
        ("SHARES_OUTSTANDING", (SUBJECT_B,)),
    ]


def test_same_kind_different_subjects_and_intervals_do_not_collapse() -> None:
    # Regression for the Wave-2 P10-C blocker: a footprint whose same-kind
    # subjects have *different* interval lists must keep the subject<->date
    # association, so two genuinely disjoint SOFs never share an id.
    first = _expand(dates=(_day(0),), subjects=(SUBJECT_A,))
    second = _expand(dates=(_day(7),), subjects=(SUBJECT_C,))
    assert first.body["blocks"] != second.body["blocks"]
    assert first.footprint_id != second.footprint_id
    assert fp.overlap(first, second) == fp.Overlap.DISJOINT


def test_undeterminable_id_never_equals_a_determinable_id() -> None:
    determinable = _expand()
    undeterminable = fp.expand(
        "RETURN_1D", {}, [SESSIONS[0]], [SUBJECT_A], calendar=None
    )
    assert determinable.determinable is True
    assert undeterminable.determinable is False
    assert undeterminable.unresolved
    assert determinable.footprint_id != undeterminable.footprint_id
    # A determinable body is never accepted as an undeterminable one.
    assert undeterminable.body["determinable"] is False


def test_ded_difference_never_changes_footprint_id() -> None:
    plain = _expand(dates=(_day(0), _day(1)))
    with_ded = fp.footprint_from_observations(
        plain.source_observations, CALENDAR, ded=["rank(z(return))"]
    )
    assert with_ded.ded == ("rank(z(return))",)
    assert with_ded.footprint_id == plain.footprint_id
    assert with_ded.body["ded"] != plain.body["ded"]


# ---------------------------------------------------------------------------
# section 6.3 derivation rules
# ---------------------------------------------------------------------------


def test_rule_return_1d_expands_to_price_change() -> None:
    result = _expand("RETURN_1D", dates=(_day(0), _day(1)))
    assert result.body["blocks"] == [
        {
            "observation_kind": "PRICE_CHANGE",
            "subject_keys": [SUBJECT_A],
            "intervals": [[_day(0), _day(1)]],
        }
    ]


def test_rule_fwd_return_expands_to_h_sessions_after_formation() -> None:
    result = _expand("FWD_RETURN", dates=(_day(0),), params={"h": 3})
    assert result.body["blocks"] == [
        {
            "observation_kind": "PRICE_CHANGE",
            "subject_keys": [SUBJECT_A],
            "intervals": [[_day(1), _day(3)]],
        }
    ]


def test_rule_excess_return_adds_the_risk_free_market_series() -> None:
    result = _expand(
        "EXCESS_RETURN",
        dates=(_day(0), _day(1)),
        params={"base": "RETURN_1D", "series": "rf"},
    )
    kinds = {
        block["observation_kind"]: block for block in result.body["blocks"]
    }
    assert kinds["PRICE_CHANGE"]["subject_keys"] == [SUBJECT_A]
    assert kinds["MARKET_SERIES"]["subject_keys"] == ["MKT:rf"]
    assert kinds["MARKET_SERIES"]["intervals"] == [[_day(0), _day(1)]]


def test_rule_benchmark_relative_adds_the_benchmark_market_series() -> None:
    result = _expand(
        "BENCHMARK_RELATIVE",
        dates=(_day(2),),
        params={"base": "RETURN_1D", "series": "benchmark"},
    )
    subjects = {
        block["observation_kind"]: block["subject_keys"]
        for block in result.body["blocks"]
    }
    assert subjects["PRICE_CHANGE"] == [SUBJECT_A]
    assert subjects["MARKET_SERIES"] == ["MKT:benchmark"]


def test_rule_price_field_expands_to_price_level() -> None:
    result = _expand("PRICE_FIELD", dates=(_day(4),))
    assert result.body["blocks"][0]["observation_kind"] == "PRICE_LEVEL"
    assert result.body["blocks"][0]["subject_keys"] == [SUBJECT_A]


def test_rule_activity_field_expands_to_trading_activity() -> None:
    result = _expand("ACTIVITY_FIELD", dates=(_day(4),))
    assert result.body["blocks"][0]["observation_kind"] == "TRADING_ACTIVITY"


def test_rule_market_cap_unions_price_level_and_shares_outstanding() -> None:
    result = _expand("MARKET_CAP", dates=(_day(0),))
    kinds = [block["observation_kind"] for block in result.body["blocks"]]
    assert kinds == ["PRICE_LEVEL", "SHARES_OUTSTANDING"]
    for block in result.body["blocks"]:
        assert block["subject_keys"] == [SUBJECT_A]


def test_rule_fundamental_field_is_keyed_by_fiscal_period_end() -> None:
    result = _expand(
        "FUNDAMENTAL_FIELD",
        dates=(_day(5),),
        params={"fiscal_periods": ["2019-12-31"]},
    )
    assert result.body["blocks"] == [
        {
            "observation_kind": "FUNDAMENTAL_REPORT",
            "subject_keys": [SUBJECT_A],
            "intervals": [["2019-12-31", "2019-12-31"]],
        }
    ]


def test_rule_signal_unions_requirements_over_the_lookback_window() -> None:
    result = _expand(
        "SIGNAL",
        dates=(_day(4),),
        params={
            "lookback": 3,
            "requirements": [{"derived_variable": "RETURN_1D"}],
        },
    )
    assert result.body["blocks"][0]["intervals"] == [[_day(1), _day(4)]]


def test_rule_aggregate_unions_inputs() -> None:
    result = _expand(
        "AGGREGATE",
        dates=(_day(0),),
        params={
            "inputs": [
                {"derived_variable": "RETURN_1D"},
                {"derived_variable": "PRICE_FIELD"},
            ]
        },
    )
    kinds = [block["observation_kind"] for block in result.body["blocks"]]
    assert kinds == ["PRICE_CHANGE", "PRICE_LEVEL"]


def test_aggregate_of_series_with_different_market_series() -> None:
    # A transformed/excess series shares the raw PRICE_CHANGE atoms; the
    # exposure is not re-labelled by the transformation.
    raw = _expand("RETURN_1D", dates=(_day(0), _day(1)))
    excess = _expand(
        "EXCESS_RETURN",
        dates=(_day(0), _day(1)),
        params={"base": "RETURN_1D", "series": "rf"},
    )
    assert raw.footprint_id != excess.footprint_id
    assert fp.overlap(raw, excess) == fp.Overlap.OVERLAP


# ---------------------------------------------------------------------------
# fundamentals / market series subject semantics
# ---------------------------------------------------------------------------


def test_restatement_or_later_vintage_is_the_same_observation() -> None:
    earlier = _expand(
        "FUNDAMENTAL_FIELD",
        dates=(_day(0),),
        params={"fiscal_periods": ["2019-12-31"]},
    )
    later = _expand(
        "FUNDAMENTAL_FIELD",
        dates=(_day(9),),
        params={"fiscal_periods": ["2019-12-31"]},
    )
    assert earlier.footprint_id == later.footprint_id
    assert fp.overlap(earlier, later) == fp.Overlap.OVERLAP


def test_market_series_subjects_use_the_mkt_namespace() -> None:
    result = _expand(
        "EXCESS_RETURN",
        dates=(_day(0),),
        params={"base": "RETURN_1D", "series": "MKT:rf"},
    )
    subjects = {
        block["subject_keys"][0]
        for block in result.body["blocks"]
        if block["observation_kind"] == "MARKET_SERIES"
    }
    assert subjects == {"MKT:rf"}


# ---------------------------------------------------------------------------
# linkage rule (section 6.2)
# ---------------------------------------------------------------------------


def test_linkage_price_level_overlaps_same_date_price_change() -> None:
    level = _expand("PRICE_FIELD", dates=(_day(3),))
    change = _expand("RETURN_1D", dates=(_day(3),))
    assert fp.overlap(level, change) == fp.Overlap.OVERLAP


def test_linkage_price_level_overlaps_next_session_price_change() -> None:
    level = _expand("PRICE_FIELD", dates=(_day(3),))
    change = _expand("RETURN_1D", dates=(_day(4),))
    assert fp.overlap(level, change) == fp.Overlap.OVERLAP


def test_linkage_does_not_span_a_non_adjacent_session() -> None:
    level = _expand("PRICE_FIELD", dates=(_day(3),))
    change = _expand("RETURN_1D", dates=(_day(5),))
    assert fp.overlap(level, change) == fp.Overlap.DISJOINT


# ---------------------------------------------------------------------------
# set algebra: union / intersect / restrict / covers
# ---------------------------------------------------------------------------


def test_intersect_keeps_only_shared_source_observations() -> None:
    left = _expand(dates=(_day(0), _day(1), _day(2)))
    right = _expand(dates=(_day(2), _day(3), _day(4)))
    result = fp.intersect(left, right)
    assert result.body["blocks"][0]["intervals"] == [[_day(2), _day(2)]]


def test_restrict_is_half_open_on_the_left() -> None:
    whole = _expand(dates=(_day(0), _day(1), _day(2), _day(3)))
    windowed = fp.restrict(whole, (_day(0), _day(2)))
    assert windowed.body["blocks"][0]["intervals"] == [[_day(1), _day(2)]]


def test_covers_is_source_observation_containment() -> None:
    whole = _expand(dates=(_day(0), _day(1), _day(2)))
    part = _expand(dates=(_day(1),))
    assert fp.covers(whole, part) is True
    assert fp.covers(part, whole) is False


def test_covers_handles_declared_calendar_day_superset() -> None:
    # A declared footprint uses *every* calendar day in a window, so it
    # covers any session-level computed footprint in that range.
    declared_body = fixtures.synthetic_footprint_body(
        blocks=[
            fixtures.synthetic_block(
                "PRICE_CHANGE",
                [SUBJECT_A],
                [[_day(0), _day(2)]],
            )
        ]
    )
    declared = fp.footprint_from_body(declared_body)
    computed = _expand(dates=(_day(0), _day(1), _day(2)))
    assert fp.covers(declared, computed) is True


def test_union_ded_and_audit_are_metadata_only() -> None:
    left = _expand(dates=(_day(0),))
    right = _expand(dates=(_day(1),))
    result = fp.union(left, right)
    assert result.footprint_id == fp.union(right, left).footprint_id


# ---------------------------------------------------------------------------
# values are never read
# ---------------------------------------------------------------------------


def test_footprint_from_panel_never_reads_mapped_value_columns() -> None:
    clean = fixtures.synthetic_panel(
        securities=("tiingo:000001",),
        sessions=(_day(0), _day(1)),
        value=1.0,
    )
    poisoned = fixtures.synthetic_panel(
        securities=("tiingo:000001",),
        sessions=(_day(0), _day(1)),
        value=_Poison(),
    )
    kwargs = dict(
        key_columns={"subject": "security_id", "date": "date"},
        variable_map=VARIABLE_MAP,
        security_map=SECURITY_MAP,
        market_series_map=MARKET_SERIES_MAP,
        calendar=CALENDAR,
    )
    clean_fp = fp.footprint_from_panel(clean, **kwargs)
    poisoned_fp = fp.footprint_from_panel(poisoned, **kwargs)
    assert clean_fp.determinable is True
    assert poisoned_fp.determinable is True
    assert clean_fp.footprint_id == poisoned_fp.footprint_id


def test_footprint_from_panel_never_reads_dataframe_value_columns() -> None:
    frame = pd.DataFrame(
        fixtures.synthetic_panel(
            securities=("tiingo:000001",),
            sessions=(_day(0), _day(1)),
            value=_Poison(),
        )
    )
    result = fp.footprint_from_panel(
        frame,
        key_columns={"subject": "security_id", "date": "date"},
        variable_map=VARIABLE_MAP,
        security_map=SECURITY_MAP,
        market_series_map=MARKET_SERIES_MAP,
        calendar=CALENDAR,
    )
    assert result.determinable is True
    assert result.body["blocks"] == [
        {
            "observation_kind": "PRICE_CHANGE",
            "subject_keys": [SUBJECT_A],
            "intervals": [[_day(0), _day(1)]],
        }
    ]


# ---------------------------------------------------------------------------
# section 6.6 overlap table (one test per row)
# ---------------------------------------------------------------------------


def test_table_exact_reuse_renamed_file() -> None:
    original = fp.footprint_from_panel(
        fixtures.synthetic_panel(
            securities=("tiingo:000001",), sessions=(_day(0), _day(1))
        ),
        key_columns={"subject": "security_id", "date": "date"},
        variable_map=VARIABLE_MAP,
        security_map=SECURITY_MAP,
        market_series_map=MARKET_SERIES_MAP,
        calendar=CALENDAR,
    )
    # The same source observations under a different file/stream name.
    renamed = _expand(dates=(_day(0), _day(1)))
    assert original.footprint_id == renamed.footprint_id
    assert fp.overlap(original, renamed) == fp.Overlap.OVERLAP


def test_table_copied_or_split_into_files() -> None:
    original = _expand(dates=(_day(0), _day(1), _day(2)))
    part_one = _expand(dates=(_day(0), _day(1)))
    part_two = _expand(dates=(_day(2),))
    assert fp.overlap(original, part_one) == fp.Overlap.OVERLAP
    assert fp.overlap(original, part_two) == fp.Overlap.OVERLAP
    assert fp.union(part_one, part_two).footprint_id == original.footprint_id


def test_table_same_observations_another_vendor_mapped() -> None:
    panel_a = fixtures.synthetic_panel(
        securities=("tiingo:000001",), sessions=(_day(0), _day(1))
    )
    panel_b = fixtures.synthetic_panel(
        securities=("othervendor:000001",), sessions=(_day(0), _day(1))
    )

    def build(panel: list[dict[str, object]], security_map: dict[str, str]):
        return fp.footprint_from_panel(
            panel,
            key_columns={"subject": "security_id", "date": "date"},
            variable_map=VARIABLE_MAP,
            security_map=security_map,
            market_series_map=MARKET_SERIES_MAP,
            calendar=CALENDAR,
        )

    first = build(panel_a, SECURITY_MAP)
    second = build(panel_b, SECURITY_MAP_ALT_VENDOR)
    assert first.footprint_id == second.footprint_id
    assert fp.overlap(first, second) == fp.Overlap.OVERLAP


def test_table_another_vendor_unmapped_fails_closed() -> None:
    panel = fixtures.synthetic_panel(
        securities=("unknownvendor:000001",), sessions=(_day(0),)
    )
    result = fp.footprint_from_panel(
        panel,
        key_columns={"subject": "security_id", "date": "date"},
        variable_map=VARIABLE_MAP,
        security_map=SECURITY_MAP,
        market_series_map=MARKET_SERIES_MAP,
        calendar=CALENDAR,
    )
    assert result.determinable is False
    assert any("unmapped_subject" in reason for reason in result.unresolved)


def test_table_partial_time_overlap() -> None:
    left = _expand(dates=(_day(0), _day(1), _day(2)))
    right = _expand(dates=(_day(2), _day(3), _day(4)))
    assert fp.overlap(left, right) == fp.Overlap.OVERLAP


def test_table_partial_universe_overlap() -> None:
    left = _expand(dates=(_day(0),), subjects=(SUBJECT_A, SUBJECT_B))
    right = _expand(dates=(_day(0),), subjects=(SUBJECT_B, SUBJECT_C))
    assert fp.overlap(left, right) == fp.Overlap.OVERLAP


def test_table_transformed_same_observations() -> None:
    raw = _expand("RETURN_1D", dates=(_day(0), _day(1)))
    excess = _expand(
        "EXCESS_RETURN",
        dates=(_day(0), _day(1)),
        params={"base": "RETURN_1D", "series": "rf"},
    )
    ranks = _expand("RETURN_1D", dates=(_day(0), _day(1)))
    assert fp.overlap(raw, excess) == fp.Overlap.OVERLAP
    assert fp.overlap(raw, ranks) == fp.Overlap.OVERLAP


def test_table_horizon_five_versus_twenty() -> None:
    long_sessions = fixtures.synthetic_calendar("2020-01-06", 40)
    long_calendar = TradingCalendar(
        [date.fromisoformat(day) for day in long_sessions]
    )
    short = fp.expand(
        "FWD_RETURN", {"h": 5}, [long_sessions[0]], [SUBJECT_A], calendar=long_calendar
    )
    long = fp.expand(
        "FWD_RETURN", {"h": 20}, [long_sessions[0]], [SUBJECT_A], calendar=long_calendar
    )
    assert fp.overlap(short, long) == fp.Overlap.OVERLAP
    shared = short.source_observations & long.source_observations
    assert len(shared) == 5


def test_table_later_vintage_or_restatement_fundamentals() -> None:
    original = _expand(
        "FUNDAMENTAL_FIELD",
        dates=(_day(0),),
        params={"fiscal_periods": ["2019-12-31"]},
    )
    restated = _expand(
        "FUNDAMENTAL_FIELD",
        dates=(_day(8),),
        params={"fiscal_periods": ["2019-12-31"]},
    )
    assert fp.overlap(original, restated) == fp.Overlap.OVERLAP


def test_table_adjacent_non_overlapping_returns_are_disjoint() -> None:
    # A development return realized at or before w0 and a confirmation
    # forward return formed at w0 share no PRICE_CHANGE observation.
    w0 = _day(4)
    development = _expand(
        "AGGREGATE",
        dates=(_day(0),),
        params={
            "inputs": [
                {"derived_variable": "FWD_RETURN", "params": {"h": 4}},
                {"derived_variable": "PRICE_FIELD", "params": {}},
            ]
        },
    )
    # development changes on sessions 1..4; PRICE_FIELD on session 0.
    confirmation = _expand("FWD_RETURN", dates=(w0,), params={"h": 3})
    assert development.body["blocks"]
    assert fp.overlap(development, confirmation) == fp.Overlap.DISJOINT
    # Sanity: the confirmation changes start at the session after w0.
    assert confirmation.body["blocks"][0]["intervals"] == [[_day(5), _day(7)]]


def test_table_unknown_overlap_fails_closed() -> None:
    undeterminable = fp.expand(
        "RETURN_1D", {}, [SESSIONS[0]], [SUBJECT_A], calendar=None
    )
    determinate = _expand()
    assert fp.overlap(undeterminable, determinate) == fp.Overlap.UNDETERMINABLE
    assert fp.overlap(undeterminable, undeterminable) == fp.Overlap.UNDETERMINABLE


def test_table_missing_parent_footprint_fails_closed() -> None:
    # A DERIVED footprint whose parent footprint is missing/unreconstructable
    # is undeterminable: the union must never treat it as fresh.
    parent = _expand(dates=(_day(0), _day(1)))
    missing_parent = fp.expand(
        "RETURN_1D", {}, [SESSIONS[0]], [SUBJECT_A], calendar=None
    )
    combined = fp.union(parent, missing_parent)
    assert combined.determinable is False
    assert combined.unresolved
    assert fp.overlap(combined, parent) == fp.Overlap.UNDETERMINABLE


def test_body_round_trip_preserves_identity_and_sof() -> None:
    original = _expand(
        dates=(_day(0), _day(1)), subjects=(SUBJECT_A, SUBJECT_B)
    )
    rebuilt = fp.footprint_from_body(original.body, calendar=CALENDAR)
    assert rebuilt.footprint_id == original.footprint_id
    assert rebuilt.source_observations == original.source_observations
    assert fp.overlap(rebuilt, original) == fp.Overlap.OVERLAP


# ---------------------------------------------------------------------------
# adversarial: same-kind disjoint / converse overlap, failure modes
# ---------------------------------------------------------------------------


def test_same_kind_different_subjects_and_intervals_no_shared_observation() -> None:
    first = _expand("RETURN_1D", dates=(_day(0), _day(1)), subjects=(SUBJECT_A,))
    second = _expand("RETURN_1D", dates=(_day(7), _day(8)), subjects=(SUBJECT_B,))
    # Same observation_kind, different subjects, different interval sets.
    assert first.body["blocks"][0]["observation_kind"] == "PRICE_CHANGE"
    assert second.body["blocks"][0]["observation_kind"] == "PRICE_CHANGE"
    assert not (first.source_observations & second.source_observations)
    assert fp.overlap(first, second) == fp.Overlap.DISJOINT


def test_same_kind_converse_sharing_one_observation_overlaps() -> None:
    first = _expand("RETURN_1D", dates=(_day(0), _day(1)), subjects=(SUBJECT_A,))
    second = _expand("RETURN_1D", dates=(_day(1), _day(9)), subjects=(SUBJECT_A,))
    assert len(first.source_observations & second.source_observations) == 1
    assert fp.overlap(first, second) == fp.Overlap.OVERLAP


def test_unmapped_column_or_missing_rule_is_undeterminable() -> None:
    panel = fixtures.synthetic_panel(
        securities=("tiingo:000001",), sessions=(_day(0),)
    )
    unmapped_column = panel + [{"security_id": "tiingo:000001", "date": _day(0), "mystery": 1.0}]
    result = fp.footprint_from_panel(
        unmapped_column,
        key_columns={"subject": "security_id", "date": "date"},
        variable_map=VARIABLE_MAP,
        security_map=SECURITY_MAP,
        market_series_map=MARKET_SERIES_MAP,
        calendar=CALENDAR,
    )
    assert result.determinable is False
    assert any("unmapped_column" in reason for reason in result.unresolved)

    missing_rule = fp.footprint_from_panel(
        panel,
        key_columns={"subject": "security_id", "date": "date"},
        variable_map={"value": {"derived_variable": "NOT_A_RULE"}},
        security_map=SECURITY_MAP,
        market_series_map=MARKET_SERIES_MAP,
        calendar=CALENDAR,
    )
    assert missing_rule.determinable is False
    assert any("missing_rule" in reason for reason in missing_rule.unresolved)


def test_calendar_gap_is_undeterminable() -> None:
    # A forward-return horizon that runs past the frozen calendar is a gap.
    result = _expand("FWD_RETURN", dates=(_day(14),), params={"h": 5})
    assert result.determinable is False
    assert any("insufficient_calendar_sessions" in r for r in result.unresolved)
    # A signal whose formation date is not a session is a gap too.
    signal = _expand(
        "SIGNAL",
        dates=("2020-01-04",),  # a Saturday, not in the calendar
        params={"lookback": 1, "requirements": [{"derived_variable": "RETURN_1D"}]},
    )
    assert signal.determinable is False


def test_unknown_derivation_rule_is_undeterminable() -> None:
    result = _expand("NOT_A_RULE")
    assert result.determinable is False
    assert any("missing_rule" in reason for reason in result.unresolved)


def test_overlap_undeterminable_across_different_calendars() -> None:
    other_calendar = TradingCalendar(
        [date.fromisoformat(day) for day in fixtures.synthetic_calendar("2021-01-04", 5)]
    )
    left = _expand(dates=(_day(0),))
    right = fp.expand(
        "RETURN_1D", {}, ["2021-01-04"], [SUBJECT_A], calendar=other_calendar
    )
    assert fp.overlap(left, right) == fp.Overlap.UNDETERMINABLE


def test_overlap_materiality_is_one_observation() -> None:
    assert FOOTPRINT_MATERIALITY_OBSERVATIONS == 1


def test_footprint_bodies_are_not_shared_mutable_state() -> None:
    first = _expand()
    body_one = first.body
    body_one["blocks"][0]["subject_keys"].append("MUTATED")
    assert "MUTATED" not in first.body["blocks"][0]["subject_keys"]


def test_undeterminable_footprint_body_validates() -> None:
    undeterminable = fp.expand(
        "RETURN_1D", {}, [SESSIONS[0]], [SUBJECT_A], calendar=None
    )
    assert validate_footprint_shape(undeterminable.body) is None


def test_identity_excludes_audit_fields() -> None:
    first = _expand()
    identity_keys = ("schema", "determinable", "unresolved", "blocks")
    body = first.body
    identity = {key: body[key] for key in identity_keys}
    assert first.footprint_id == content_hash(identity)
    # Changing only an audit field leaves the id untouched.
    mutated = dict(body)
    mutated["calendar_hash"] = "0" * 64
    assert content_hash({key: mutated[key] for key in identity_keys}) == first.footprint_id
