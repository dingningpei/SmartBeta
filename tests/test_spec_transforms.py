"""Tests for the numeric transform library (P6-E).

These tests cover exactly the frozen P6-E acceptance criteria (Phase 6 plan
sections 6, 7 and 8):

* **numeric correctness** -- every whitelisted transform is checked against a
  value computed by hand, not against a re-implementation of the kernel;
* **NaN and division-by-zero behaviour** -- ``NaN`` propagates, and division
  by zero yields ``NaN`` rather than ``inf`` or an exception;
* **deterministic ordering** -- rows (observation dates) and columns (stocks)
  are normalized and results are order-independent;
* **cross-sectional versus time-series** -- the per-date (axis=1) and
  per-stock (axis=0) families are applied on the correct axis and cannot be
  confused;

plus the P6-E scope boundary: inputs are *already supplied and already
aligned*, so the library never reindexes, never forward-fills, and fails
closed with the shared typed error on an unaligned operand.

The final test inspects this production module's source with :mod:`ast`
(never by executing it) and asserts that it contains no dynamic execution and
imports no vendor/network/PIT module.
"""

from __future__ import annotations

import ast
import pathlib

import numpy as np
import pandas as pd
import pytest

from smart_beta.spec import expression as ex
from smart_beta.spec import transforms as tr


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
DATES = pd.to_datetime(
    ["2021-01-01", "2021-01-02", "2021-01-03", "2021-01-04", "2021-01-05"]
)
_MODULE_SOURCE_PATH = pathlib.Path(tr.__file__)


def make_frame(data, dates=DATES, columns=None) -> pd.DataFrame:
    """Build a value frame from a column dict and an explicit date index."""
    frame = pd.DataFrame(data, index=dates)
    if columns is not None:
        frame = frame.loc[:, columns]
    return frame


def assert_frame_equal(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False)


# ==========================================================================
# 1. the vocabulary is total: every frozen op has a kernel
# ==========================================================================
def test_rolling_kernel_registry_is_total():
    assert set(tr.ROLLING_KERNELS) == set(ex.RollingOp)
    for op, kernel in tr.ROLLING_KERNELS.items():
        assert callable(kernel)


def test_cross_sectional_kernel_registry_is_total():
    assert set(tr.CROSS_SECTIONAL_KERNELS) == set(ex.CrossSectionalOp)
    for op, kernel in tr.CROSS_SECTIONAL_KERNELS.items():
        assert callable(kernel)


@pytest.mark.parametrize("name", [op.value for op in ex.RollingOp])
def test_every_rolling_op_executes_through_apply_transform(name):
    node = ex.parse_expression(f"{name}(x, 2)")
    operand = make_frame({"x": [1.0, 2.0, 3.0]}, dates=DATES[:3])
    result = tr.apply_transform(node, (operand,))
    assert isinstance(result, pd.DataFrame)
    assert result.shape == operand.shape
    assert result.notna().to_numpy().any()


@pytest.mark.parametrize("name", ["rank", "standardize"])
def test_every_cross_sectional_op_executes_through_apply_transform(name):
    node = ex.parse_expression(f"{name}(x)")
    operand = make_frame({"x": [1.0, 2.0, 3.0]}, dates=DATES[:3])
    result = tr.apply_transform(node, (operand,))
    assert isinstance(result, pd.DataFrame)
    assert result.shape == operand.shape


def test_winsorize_executes_through_apply_transform():
    node = ex.parse_expression("winsorize(x, 0.25, 0.75)")
    operand = pd.DataFrame(
        {"A": [1.0], "B": [2.0], "C": [3.0], "D": [4.0]}, index=DATES[:1]
    )
    result = tr.apply_transform(node, (operand,))
    expected = pd.DataFrame(
        {"A": [1.75], "B": [2.0], "C": [3.0], "D": [3.25]}, index=DATES[:1]
    )
    assert_frame_equal(result, expected)


@pytest.mark.parametrize(
    "op,left,right,expected",
    [
        ("add", [1.0, 2.0], [3.0, 4.0], [4.0, 6.0]),
        ("sub", [1.0, 2.0], [3.0, 4.0], [-2.0, -2.0]),
        ("mul", [1.0, 2.0], [3.0, 4.0], [3.0, 8.0]),
        ("div", [1.0, 2.0], [2.0, 4.0], [0.5, 0.5]),
    ],
)
def test_arithmetic_nodes_execute_through_apply_transform(op, left, right, expected):
    node = ex.parse_expression({"op": op, "left": {"op": "field", "role": "a"},
                               "right": {"op": "field", "role": "b"}})
    left_frame = make_frame({"X": left}, dates=DATES[:2])
    right_frame = make_frame({"X": right}, dates=DATES[:2])
    result = tr.apply_transform(node, (left_frame, right_frame))
    expected_frame = make_frame({"X": expected}, dates=DATES[:2])
    assert_frame_equal(result, expected_frame)


def test_apply_transform_literal_returns_scalar_and_lag_shifts():
    assert tr.apply_transform(ex.Literal(2.5)) == 2.5
    node = ex.parse_expression("lag(a, 1)")
    operand = make_frame({"a": [1.0, 2.0, 3.0]}, dates=DATES[:3])
    result = tr.apply_transform(node, (operand,))
    expected = pd.DataFrame(
        {"a": [np.nan, 1.0, 2.0]}, index=DATES[:3]
    )
    assert_frame_equal(result, expected)


# ==========================================================================
# 2. numeric correctness (hand-computed expectations)
# ==========================================================================
def test_arithmetic_matches_hand_computed_values():
    a = make_frame({"S": [1.0, 2.0, 3.0]}, dates=DATES[:3])
    b = make_frame({"S": [10.0, 20.0, 30.0]}, dates=DATES[:3])
    assert_frame_equal(
        tr.add(a, b), make_frame({"S": [11.0, 22.0, 33.0]}, dates=DATES[:3])
    )
    assert_frame_equal(
        tr.subtract(b, a), make_frame({"S": [9.0, 18.0, 27.0]}, dates=DATES[:3])
    )
    assert_frame_equal(
        tr.multiply(a, b), make_frame({"S": [10.0, 40.0, 90.0]}, dates=DATES[:3])
    )
    assert_frame_equal(
        tr.divide(b, a), make_frame({"S": [10.0, 10.0, 10.0]}, dates=DATES[:3])
    )


def test_scalar_operands_broadcast_without_alignment():
    a = make_frame({"a": [1.0, 2.0, 4.0]}, dates=DATES[:3])
    assert_frame_equal(
        tr.add(a, 1.0), make_frame({"a": [2.0, 3.0, 5.0]}, dates=DATES[:3])
    )
    assert_frame_equal(
        tr.multiply(2.0, a), make_frame({"a": [2.0, 4.0, 8.0]}, dates=DATES[:3])
    )
    # "0 - x" is the whitelisted negation form.
    assert_frame_equal(
        tr.subtract(0.0, a), make_frame({"a": [-1.0, -2.0, -4.0]}, dates=DATES[:3])
    )
    assert_frame_equal(
        tr.divide(a, 2.0), make_frame({"a": [0.5, 1.0, 2.0]}, dates=DATES[:3])
    )


def test_lag_shifts_by_observation_dates_per_stock():
    frame = make_frame(
        {"a": [1.0, 2.0, 3.0, 4.0], "b": [10.0, 20.0, 30.0, 40.0]},
        dates=DATES[:4],
    )
    expected = pd.DataFrame(
        {
            "a": [np.nan, 1.0, 2.0, 3.0],
            "b": [np.nan, 10.0, 20.0, 30.0],
        },
        index=DATES[:4],
    )
    assert_frame_equal(tr.lag_values(frame, 1), expected)
    lag2 = tr.lag_values(frame, 2)
    assert lag2["a"].tolist()[2:] == [1.0, 2.0]
    assert pd.isna(lag2["a"].iloc[0]) and pd.isna(lag2["a"].iloc[1])


def test_lag_zero_is_the_identity():
    frame = make_frame({"a": [1.0, 2.0, 3.0]}, dates=DATES[:3])
    assert_frame_equal(tr.lag_values(frame, 0), frame)


def test_rolling_mean_is_trailing_and_hand_computed():
    frame = make_frame(
        {"a": [1.0, 2.0, 3.0, 4.0, 5.0], "b": [10.0, 20.0, 30.0, 40.0, 50.0]},
        dates=DATES,
    )
    result = tr.rolling_mean(frame, 3)
    assert pd.isna(result["a"].iloc[0]) and pd.isna(result["a"].iloc[1])
    assert result["a"].iloc[2:].tolist() == pytest.approx([2.0, 3.0, 4.0])
    # column b is rolled independently (per stock): window 2.
    result_b = tr.rolling_mean(frame, 2)
    assert result_b["b"].iloc[1:].tolist() == pytest.approx([15.0, 25.0, 35.0, 45.0])
    assert pd.isna(result_b["b"].iloc[0])


def test_rolling_sum_min_max_hand_computed():
    frame = make_frame({"a": [1.0, 2.0, 3.0, 4.0, 5.0]}, dates=DATES)
    assert tr.rolling_sum(frame, 3)["a"].iloc[2:].tolist() == pytest.approx(
        [6.0, 9.0, 12.0]
    )
    assert tr.rolling_min(frame, 3)["a"].iloc[2:].tolist() == pytest.approx(
        [1.0, 2.0, 3.0]
    )
    assert tr.rolling_max(frame, 3)["a"].iloc[2:].tolist() == pytest.approx(
        [3.0, 4.0, 5.0]
    )


def test_rolling_std_hand_computed_sample():
    frame = make_frame({"a": [1.0, 2.0, 3.0, 4.0, 5.0]}, dates=DATES)
    result = tr.rolling_std(frame, 3)
    # windows [1,2,3], [2,3,4], [3,4,5] all have sample std 1.0 (ddof=1)
    assert pd.isna(result["a"].iloc[0]) and pd.isna(result["a"].iloc[1])
    assert result["a"].iloc[2:].tolist() == pytest.approx([1.0, 1.0, 1.0])


def test_cross_sectional_rank_hand_computed_with_ties():
    frame = pd.DataFrame(
        {"A": [10.0, 5.0, 1.0], "B": [30.0, 5.0, 2.0], "C": [20.0, 1.0, 3.0]},
        index=DATES[:3],
    )
    result = tr.cross_sectional_rank(frame)
    assert result.loc[DATES[0]].tolist() == pytest.approx([1.0, 3.0, 2.0])
    # ties receive the average rank: {5, 5, 1} -> 2.5, 2.5, 1
    assert result.loc[DATES[1]].tolist() == pytest.approx([2.5, 2.5, 1.0])
    assert result.loc[DATES[2]].tolist() == pytest.approx([1.0, 2.0, 3.0])


def test_cross_sectional_winsorize_hand_computed():
    frame = pd.DataFrame(
        {"A": [1.0], "B": [2.0], "C": [3.0], "D": [4.0]}, index=DATES[:1]
    )
    result = tr.cross_sectional_winsorize(frame, 0.25, 0.75)
    # linear quantiles q25 = 1.75, q75 = 3.25
    assert result.loc[DATES[0]].tolist() == pytest.approx([1.75, 2.0, 3.0, 3.25])


def test_cross_sectional_standardize_hand_computed_population():
    frame = pd.DataFrame(
        {"A": [1.0], "B": [2.0], "C": [3.0]}, index=DATES[:1]
    )
    result = tr.cross_sectional_standardize(frame)
    population_std = float(np.sqrt(2.0 / 3.0))
    expected = [(-1.0) / population_std, 0.0, 1.0 / population_std]
    assert result.loc[DATES[0]].tolist() == pytest.approx(expected)


def test_ch4_like_turnover_ratio_factor():
    """Section 10 reference factor: mean(turnover, 2) / mean(turnover, 4)."""
    turnover = make_frame(
        {"turnover": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]},
        dates=pd.to_datetime(
            [
                "2021-01-01",
                "2021-01-02",
                "2021-01-03",
                "2021-01-04",
                "2021-01-05",
                "2021-01-06",
            ]
        ),
    )
    node = ex.parse_expression("mean(turnover, 2) / mean(turnover, 4)")
    short = tr.apply_transform(node.left, (turnover,))
    long = tr.apply_transform(node.right, (turnover,))
    result = tr.apply_transform(node, (short, long))
    expected = short / long
    assert_frame_equal(result, expected)
    # At the last date: mean(5,6)=5.5, mean(3,4,5,6)=4.5 -> 11/9
    assert result["turnover"].iloc[-1] == pytest.approx(5.5 / 4.5)


# ==========================================================================
# 3. NaN and division-by-zero behaviour
# ==========================================================================
def test_division_by_zero_yields_nan_never_inf_or_exception():
    numerator = make_frame({"a": [1.0, -2.0, 0.0, 3.0]}, dates=DATES[:4])
    denominator = make_frame({"a": [0.0, 0.0, 0.0, 1.5]}, dates=DATES[:4])
    result = tr.divide(numerator, denominator)
    assert pd.isna(result["a"].iloc[0])
    assert pd.isna(result["a"].iloc[1])
    assert pd.isna(result["a"].iloc[2])
    assert result["a"].iloc[3] == pytest.approx(2.0)
    assert not np.isinf(result.to_numpy(dtype="float64")).any()


def test_zero_over_zero_yields_nan():
    frame = make_frame({"a": [0.0]}, dates=DATES[:1])
    result = tr.divide(frame, frame)
    assert pd.isna(result["a"].iloc[0])


def test_division_by_nan_yields_nan():
    numerator = make_frame({"a": [1.0, 2.0]}, dates=DATES[:2])
    denominator = make_frame({"a": [np.nan, 2.0]}, dates=DATES[:2])
    result = tr.divide(numerator, denominator)
    assert pd.isna(result["a"].iloc[0])
    assert result["a"].iloc[1] == pytest.approx(1.0)


def test_scalar_division_by_zero_yields_nan_never_zero_division_error():
    # Regression (review defect): the scalar path used raw Python float
    # division, which raises ZeroDivisionError instead of the frozen NaN.
    assert pd.isna(tr.divide(1.0, 0.0))
    assert pd.isna(tr.divide(0.0, 0.0))
    assert pd.isna(tr.divide(-1.0, 0.0))
    assert pd.isna(tr.divide(1.0, -0.0))


def test_ordinary_scalar_division_still_works():
    assert tr.divide(6.0, 3.0) == pytest.approx(2.0)
    assert tr.divide(-6.0, 3.0) == pytest.approx(-2.0)
    assert tr.divide(1.0, 4.0) == pytest.approx(0.25)


def test_non_finite_scalar_quotients_are_mapped_to_nan():
    # The frozen non-finite policy must hold for the scalar path too: an
    # overflowing quotient is NaN, never a bare ``inf``.
    for result in (tr.divide(1e308, 1e-10), tr.divide(-1e308, 1e-10)):
        assert pd.isna(result)
        assert not np.isinf(float(result))


def test_scalar_division_through_apply_transform_never_leaks_zero_division():
    # A Literal evaluates to a float scalar (the apply_transform contract), so
    # this is the reachable expression path for the defect.
    node = ex.ratio(ex.literal(1.0), ex.literal(0.0))
    result = tr.apply_transform(node, (1.0, 0.0))
    assert pd.isna(result)
    # "1.0 / 0.0" is a legal frozen textual expression and must be equally safe.
    parsed = ex.parse_expression("1.0 / 0.0")
    assert pd.isna(tr.apply_transform(parsed, (1.0, 0.0)))
    # ordinary scalar division through the dispatch still works
    assert tr.apply_transform(
        ex.ratio(ex.literal(6.0), ex.literal(3.0)), (6.0, 3.0)
    ) == pytest.approx(2.0)


def test_frame_division_by_zero_behaviour_is_unchanged():
    frame = make_frame({"a": [1.0, -2.0, 0.0]}, dates=DATES[:3])
    # frame / 0.0 -> all NaN
    by_scalar = tr.divide(frame, 0.0)
    assert by_scalar["a"].isna().all()
    # frame / frame-with-zeros -> NaN at the zero cells, finite elsewhere
    denominator = make_frame({"a": [0.0, 0.0, 2.0]}, dates=DATES[:3])
    by_frame = tr.divide(frame, denominator)
    assert pd.isna(by_frame["a"].iloc[0])
    assert pd.isna(by_frame["a"].iloc[1])
    assert by_frame["a"].iloc[2] == pytest.approx(0.0)
    # 0.0 / frame unchanged: finite where the denominator is non-zero
    reversed_div = tr.divide(0.0, denominator)
    assert pd.isna(reversed_div["a"].iloc[0])
    assert pd.isna(reversed_div["a"].iloc[1])
    assert reversed_div["a"].iloc[2] == pytest.approx(0.0)
    # mixed scalar / frame-with-zero -> NaN only at the zero cell
    mixed = tr.divide(1.0, denominator)
    assert pd.isna(mixed["a"].iloc[0])
    assert pd.isna(mixed["a"].iloc[1])
    assert mixed["a"].iloc[2] == pytest.approx(0.5)


def test_arithmetic_propagates_nan():
    a = make_frame({"a": [1.0, np.nan, 3.0]}, dates=DATES[:3])
    b = make_frame({"a": [1.0, 2.0, np.nan]}, dates=DATES[:3])
    for result in (tr.add(a, b), tr.subtract(a, b), tr.multiply(a, b)):
        assert pd.isna(result["a"].iloc[1])
        assert pd.isna(result["a"].iloc[2])


def test_no_transform_result_contains_infinity():
    messy = make_frame(
        {"a": [1.0, 0.0, np.nan, -3.0], "b": [0.0, 0.0, 2.0, -0.0]},
        dates=DATES[:4],
    )
    results = [
        tr.divide(messy, messy),
        tr.divide(messy, 0.0),
        tr.rolling_mean(messy, 2),
        tr.rolling_std(messy, 2),
        tr.cross_sectional_standardize(messy),
        tr.cross_sectional_rank(messy),
        tr.cross_sectional_winsorize(messy, 0.1, 0.9),
    ]
    for result in results:
        values = result.to_numpy(dtype="float64")
        assert not np.isinf(values).any()


def test_rolling_requires_a_full_window():
    frame = make_frame({"a": [1.0, 2.0, 3.0]}, dates=DATES[:3])
    for result in (
        tr.rolling_mean(frame, 3),
        tr.rolling_sum(frame, 3),
        tr.rolling_min(frame, 3),
        tr.rolling_max(frame, 3),
        tr.rolling_std(frame, 3),
    ):
        assert pd.isna(result["a"].iloc[0])
        assert pd.isna(result["a"].iloc[1])
        assert not pd.isna(result["a"].iloc[2])


def test_rolling_window_containing_nan_yields_nan():
    frame = make_frame({"a": [1.0, np.nan, 3.0]}, dates=DATES[:3])
    result = tr.rolling_mean(frame, 3)
    # the full window at the last date contains a NaN -> NaN, not a 2-period mean
    assert pd.isna(result["a"].iloc[2])


def test_cross_sectional_statistics_ignore_nan_members():
    frame = pd.DataFrame(
        {"A": [1.0, 10.0], "B": [np.nan, 20.0], "C": [3.0, 30.0]},
        index=DATES[:2],
    )
    ranked = tr.cross_sectional_rank(frame)
    # row 0 is {1, NaN, 3} -> B stays NaN, A=1, C=2
    assert ranked.loc[DATES[0]].tolist() == pytest.approx([1.0, np.nan, 2.0],
                                                          nan_ok=True)
    standardized = tr.cross_sectional_standardize(frame)
    assert pd.isna(standardized.loc[DATES[0], "B"])


def test_standardize_degenerate_cross_section_yields_nan():
    constant = pd.DataFrame(
        {"A": [2.0], "B": [2.0], "C": [2.0]}, index=DATES[:1]
    )
    result = tr.cross_sectional_standardize(constant)
    assert result.loc[DATES[0]].isna().all()

    single = pd.DataFrame({"A": [2.0]}, index=DATES[:1])
    assert pd.isna(tr.cross_sectional_standardize(single).loc[DATES[0], "A"])


def test_lag_introduces_nan_at_the_head():
    frame = make_frame({"a": [1.0, 2.0]}, dates=DATES[:2])
    result = tr.lag_values(frame, 1)
    assert pd.isna(result["a"].iloc[0])
    assert result["a"].iloc[1] == pytest.approx(1.0)


# ==========================================================================
# 4. deterministic row/column ordering
# ==========================================================================
def test_normalize_sorts_rows_and_columns():
    frame = pd.DataFrame(
        {"B": [4.0, 1.0], "A": [1.0, 2.0]}, index=DATES[:2][::-1]
    )
    normalized = tr.normalize_value_frame(frame)
    assert normalized.index.tolist() == DATES[:2].tolist()
    assert normalized.columns.tolist() == ["A", "B"]


def test_transform_output_is_sorted_regardless_of_input_order():
    data = {"B": [1.0, 2.0, 3.0], "A": [3.0, 2.0, 1.0]}
    ordered = pd.DataFrame(data, index=DATES[:3])
    # the same observations, with both rows (dates) and columns (stocks) reversed
    shuffled = ordered.iloc[::-1, ::-1]
    for kernel in (
        lambda f: tr.add(f, 1.0),
        lambda f: tr.lag_values(f, 1),
        lambda f: tr.rolling_mean(f, 2),
        lambda f: tr.cross_sectional_rank(f),
        lambda f: tr.cross_sectional_standardize(f),
        lambda f: tr.cross_sectional_winsorize(f, 0.25, 0.75),
    ):
        assert_frame_equal(kernel(ordered), kernel(shuffled))


def test_repeated_application_is_bit_identical():
    frame = make_frame({"a": [1.0, 2.0, 3.0]}, dates=DATES[:3])
    first = tr.rolling_mean(frame, 2)
    second = tr.rolling_mean(frame, 2)
    pd.testing.assert_frame_equal(first, second, check_exact=True)


def test_value_frame_to_long_is_sorted_with_canonical_columns():
    frame = pd.DataFrame(
        {"B": [2.0, np.nan], "A": [1.0, 3.0]}, index=DATES[:2]
    )
    long = tr.value_frame_to_long(frame)
    assert list(long.columns) == ["date", "stock_id", "value"]
    assert long["date"].tolist() == [DATES[0], DATES[0], DATES[1], DATES[1]]
    assert long["stock_id"].tolist() == ["A", "B", "A", "B"]
    assert long["value"].tolist()[:2] == [1.0, 2.0]
    assert pd.isna(long["value"].iloc[3])


def test_long_round_trip_preserves_values():
    frame = pd.DataFrame(
        {"B": [2.0, np.nan, 6.0], "A": [1.0, 3.0, 5.0]}, index=DATES[:3]
    )
    rebuilt = tr.value_frame_from_long(tr.value_frame_to_long(frame))
    assert_frame_equal(rebuilt, tr.normalize_value_frame(frame))


def test_value_frame_from_long_rejects_duplicate_keys():
    long = pd.DataFrame(
        {
            "date": [DATES[0], DATES[0]],
            "stock_id": ["A", "A"],
            "value": [1.0, 2.0],
        }
    )
    with pytest.raises(tr.TransformError):
        tr.value_frame_from_long(long)


# ==========================================================================
# 5. cross-sectional versus time-series boundary
# ==========================================================================
def test_rank_is_applied_per_date_across_stocks():
    frame = pd.DataFrame(
        {"A": [1.0, 3.0], "B": [3.0, 1.0]}, index=DATES[:2]
    )
    ranked = tr.cross_sectional_rank(frame)
    # If rank were (incorrectly) applied per stock down dates, both columns
    # would be [1, 2]; per-date it is [1, 2] on row 0 and [2, 1] on row 1.
    assert ranked.loc[DATES[0]].tolist() == [1.0, 2.0]
    assert ranked.loc[DATES[1]].tolist() == [2.0, 1.0]


def test_rolling_is_applied_per_stock_down_dates():
    frame = pd.DataFrame(
        {"A": [1.0, 3.0], "B": [3.0, 1.0]}, index=DATES[:2]
    )
    rolled = tr.rolling_sum(frame, 2)
    # If the sum were (incorrectly) taken across stocks per date it would be
    # [4, 4]; per stock it is [NaN, 4] for both columns.
    assert pd.isna(rolled.loc[DATES[0], "A"])
    assert pd.isna(rolled.loc[DATES[0], "B"])
    assert rolled.loc[DATES[1], "A"] == pytest.approx(4.0)
    assert rolled.loc[DATES[1], "B"] == pytest.approx(4.0)


def test_cross_sectional_and_time_series_act_on_different_axes():
    frame = pd.DataFrame(
        {"A": [1.0, 4.0, 2.0], "B": [2.0, 5.0, 1.0], "C": [3.0, 6.0, 3.0]},
        index=DATES[:3],
    )
    temporal_then_cross = tr.cross_sectional_rank(tr.rolling_sum(frame, 2))
    cross_then_temporal = tr.rolling_sum(tr.cross_sectional_rank(frame), 2)
    assert not temporal_then_cross.equals(cross_then_temporal)


def test_temporal_over_cross_sectional_is_rejected_by_the_validator():
    """The alignment-order boundary the library must respect is structural."""
    with pytest.raises(ex.AlignmentOrderError):
        ex.validate_expression(ex.parse_expression("lag(rank(return), 1)"))
    # the aligned order (temporal first, cross-sectional last) is allowed
    assert ex.validate_expression(ex.parse_expression("rank(lag(return, 1))"))


# ==========================================================================
# 6. already-aligned inputs / fail-closed trust boundary
# ==========================================================================
def test_binary_operands_must_share_exactly_the_same_dates():
    left = make_frame({"a": [1.0, 2.0, 3.0]}, dates=DATES[:3])
    right = make_frame({"a": [1.0, 2.0, 3.0]}, dates=DATES[1:4])
    with pytest.raises(tr.TransformError):
        tr.add(left, right)


def test_binary_operands_must_share_exactly_the_same_stocks():
    left = pd.DataFrame({"A": [1.0], "B": [2.0]}, index=DATES[:1])
    right = pd.DataFrame({"A": [1.0], "C": [2.0]}, index=DATES[:1])
    with pytest.raises(tr.TransformError):
        tr.subtract(left, right)


def test_non_frame_inputs_are_rejected():
    with pytest.raises(tr.TransformError):
        tr.add("not a frame", 1.0)
    with pytest.raises(tr.TransformError):
        tr.lag_values("not a frame", 1)
    with pytest.raises(tr.TransformError):
        tr.rolling_mean(None, 2)


def test_duplicate_keys_are_rejected():
    duplicate_dates = pd.DataFrame(
        {"a": [1.0, 2.0]}, index=pd.to_datetime(["2021-01-01", "2021-01-01"])
    )
    with pytest.raises(tr.TransformError):
        tr.normalize_value_frame(duplicate_dates)
    duplicate_columns = pd.DataFrame(
        [[1.0, 2.0]], index=DATES[:1], columns=["A", "A"]
    )
    with pytest.raises(tr.TransformError):
        tr.normalize_value_frame(duplicate_columns)


def test_non_numeric_frames_are_rejected():
    frame = pd.DataFrame({"A": [1.0], "B": ["x"]}, index=DATES[:1])
    with pytest.raises(tr.TransformError):
        tr.normalize_value_frame(frame)


def test_apply_transform_rejects_a_raw_field():
    with pytest.raises(tr.TransformError):
        tr.apply_transform(ex.Field("return"))


def test_apply_transform_rejects_wrong_operand_counts():
    frame = make_frame({"a": [1.0]}, dates=DATES[:1])
    node = ex.parse_expression("a + b")
    with pytest.raises(tr.TransformError):
        tr.apply_transform(node, (frame,))
    with pytest.raises(tr.TransformError):
        tr.apply_transform(ex.parse_expression("lag(a, 1)"), ())


def test_unknown_transform_names_are_rejected():
    frame = make_frame({"a": [1.0, 2.0, 3.0]}, dates=DATES[:3])
    with pytest.raises(tr.TransformError):
        tr.rolling(frame, "median", 2)
    with pytest.raises(tr.TransformError):
        tr.cross_sectional(frame, "zscore")


def test_negative_lag_is_rejected():
    frame = make_frame({"a": [1.0, 2.0]}, dates=DATES[:2])
    with pytest.raises(tr.TransformError):
        tr.lag_values(frame, -1)


def test_window_and_min_periods_are_validated():
    frame = make_frame({"a": [1.0, 2.0, 3.0]}, dates=DATES[:3])
    for bad_window in (0, -1, 1.5, True):
        with pytest.raises(tr.TransformError):
            tr.rolling(frame, ex.RollingOp.MEAN, bad_window)
    with pytest.raises(tr.TransformError):
        tr.rolling(frame, ex.RollingOp.MEAN, 3, min_periods=0)
    with pytest.raises(tr.TransformError):
        tr.rolling(frame, ex.RollingOp.MEAN, 3, min_periods=4)


def test_winsorize_bounds_are_validated():
    frame = make_frame({"a": [1.0, 2.0, 3.0]}, dates=DATES[:3])
    for lower, upper in ((0.9, 0.1), (0.0, 0.0), (-0.1, 0.9), (0.1, 1.1)):
        with pytest.raises(tr.TransformError):
            tr.cross_sectional_winsorize(frame, lower, upper)


def test_standardize_ddof_is_validated():
    frame = make_frame({"a": [1.0, 2.0, 3.0]}, dates=DATES[:3])
    with pytest.raises(tr.TransformError):
        tr.cross_sectional_standardize(frame, ddof=-1)
    with pytest.raises(tr.TransformError):
        tr.cross_sectional_standardize(frame, ddof=1.5)
    # an explicit ddof=1 (sample) is honoured
    result = tr.cross_sectional_standardize(frame, ddof=1)
    assert result.shape == frame.shape


def test_transform_error_is_in_the_frozen_error_taxonomy():
    assert issubclass(tr.TransformError, ex.EvaluationError)
    assert issubclass(tr.TransformError, ex.ExpressionError)


# ==========================================================================
# 7. module-level trust-boundary discipline
# ==========================================================================
def test_module_source_has_no_dynamic_execution_or_forbidden_imports():
    tree = ast.parse(_MODULE_SOURCE_PATH.read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    forbidden_roots = {
        "os",
        "sys",
        "subprocess",
        "socket",
        "urllib",
        "http",
        "requests",
        "importlib",
        "ctypes",
    }
    for name in imported:
        assert name.split(".")[0] not in forbidden_roots, name
        assert "vendors" not in name, name
        assert "pit" not in name.split("."), name
    # the spec layer may only reach the frozen node types and the schema names
    for name in imported:
        if name == "smart_beta" or name.startswith("smart_beta."):
            assert name in {
                "smart_beta.data.schema",
                "smart_beta.spec.expression",
            }, name

    banned_calls = {"eval", "exec", "compile", "__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in banned_calls, node.func.id
