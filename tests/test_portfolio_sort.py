"""Tests for the portfolio sort engine.

Each test targets one of the four legacy ``calresult()`` bugs described in
:mod:`smart_beta.engines.portfolio_sort`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smart_beta.engines.portfolio_sort import (
    EW_RETURN_COL,
    GROUP_COL,
    N_RETURNS_COL,
    N_STOCKS_COL,
    VW_RETURN_COL,
    WEIGHT_SUM_COL,
    assign_groups,
    double_sort_portfolios,
    group_return_stats,
    long_short_return,
    sort_portfolios,
)


def _single_date_panel() -> pd.DataFrame:
    """10 stocks, characteristic 1..10, one date.

    Group 1 / group 2 split is ranks 1-5 / 6-10. Group 1 has five tiny
    caps, group 2 has five huge caps, so a value-weight denominator taken
    over the whole cross-section would be wildly wrong for group 1.
    """
    return pd.DataFrame(
        {
            "date": pd.Timestamp("2020-01-31"),
            "stock_id": [f"S{i:02d}" for i in range(1, 11)],
            "char": np.arange(1.0, 11.0),
            "ret": [0.01, 0.02, 0.03, 0.04, 0.05, 0.10, 0.11, 0.12, 0.13, 0.14],
            "mcap": [1.0] * 5 + [100.0] * 5,
        }
    )


# ---------------------------------------------------------------------------
# Bug #1: value-weighted return must use the group's own weight denominator
# ---------------------------------------------------------------------------
def test_vw_return_uses_group_own_weight_denominator():
    panel = _single_date_panel()
    result = sort_portfolios(panel, "char", "ret", "mcap", n_groups=2)

    group1 = result.loc[result[GROUP_COL] == 1].iloc[0]
    group2 = result.loc[result[GROUP_COL] == 2].iloc[0]

    rets = panel["ret"].to_numpy()
    caps = panel["mcap"].to_numpy()

    expected_g1 = np.average(rets[:5], weights=caps[:5])
    expected_g2 = np.average(rets[5:], weights=caps[5:])
    assert group1[VW_RETURN_COL] == pytest.approx(expected_g1)
    assert group2[VW_RETURN_COL] == pytest.approx(expected_g2)

    # The legacy bug: divide by the whole cross-section's weight sum.
    buggy_g1 = np.dot(rets[:5], caps[:5] / caps.sum())
    assert group1[VW_RETURN_COL] != pytest.approx(buggy_g1)

    # A proper within-group VW mean must lie inside its members' [min, max];
    # the buggy denominator makes group 1 collapse towards zero.
    assert min(rets[:5]) <= group1[VW_RETURN_COL] <= max(rets[:5])
    # And groups do NOT sum to the overall market return anymore.
    assert group1[VW_RETURN_COL] + group2[VW_RETURN_COL] != pytest.approx(
        np.average(rets, weights=caps)
    )


def test_vw_return_ignores_nan_returns_and_nan_weights():
    panel = _single_date_panel()
    # Drop one stock's return and one stock's weight from group 1.
    panel.loc[0, "ret"] = np.nan
    panel.loc[1, "mcap"] = np.nan
    result = sort_portfolios(panel, "char", "ret", "mcap", n_groups=2)
    group1 = result.loc[result[GROUP_COL] == 1].iloc[0]

    # Stocks with char 3,4,5 are the only ones with both ret and weight;
    # char-1 is missing its return and char-2 its weight.
    expected = np.average([0.03, 0.04, 0.05], weights=[1.0, 1.0, 1.0])
    assert group1[VW_RETURN_COL] == pytest.approx(expected)
    assert group1[N_RETURNS_COL] == 4  # char 2,3,4,5 have returns
    assert group1[WEIGHT_SUM_COL] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Bug #2: groups partition the cross-section -- no boundary drops
# ---------------------------------------------------------------------------
def test_groups_partition_full_cross_section_with_ties():
    # Date A has every characteristic tied (worst case for percentile
    # boundaries); date B is a normal 1..6 spread.
    panel = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2020-01-31"] * 6 + ["2020-02-29"] * 6
            ),
            "stock_id": [f"S{i}" for i in range(1, 7)] * 2,
            "char": [5.0] * 6 + list(np.arange(1.0, 7.0)),
            "ret": list(np.arange(0.01, 0.07, 0.01)) * 2,
            "mcap": [1.0] * 12,
        }
    )
    result = sort_portfolios(panel, "char", "ret", "mcap", n_groups=5)

    for date in panel["date"].unique():
        day = result.loc[result["date"] == date]
        assert day[N_STOCKS_COL].sum() == 6
        # Every one of the five groups is present for every date.
        assert sorted(day[GROUP_COL]) == [1, 2, 3, 4, 5]

    # 6 observations over 5 buckets -> sizes 2,1,1,1,1 with no drop.
    day_a = result.loc[result["date"] == pd.Timestamp("2020-01-31")]
    assert sorted(day_a[N_STOCKS_COL].tolist()) == [1, 1, 1, 1, 2]


def test_boundary_tied_values_are_not_dropped():
    # Characteristic duplicated exactly on the 20/40/60/80 percentile
    # boundaries -- the legacy code's half-open intervals dropped these
    # (and any value equal to an interior boundary) from every group.
    char = [1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0, 5.0, 5.0]
    panel = pd.DataFrame(
        {
            "date": pd.Timestamp("2020-01-31"),
            "stock_id": [f"S{i}" for i in range(10)],
            "char": char,
            "ret": np.linspace(0.01, 0.10, 10),
            "mcap": [1.0] * 10,
        }
    )
    result = sort_portfolios(panel, "char", "ret", "mcap", n_groups=5)
    assert result[N_STOCKS_COL].sum() == 10
    assert sorted(result[N_STOCKS_COL].tolist()) == [2, 2, 2, 2, 2]


def test_every_sortable_observation_is_assigned_exactly_once():
    rng = np.random.default_rng(0)
    panel = pd.DataFrame(
        {
            "date": pd.Timestamp("2020-01-31"),
            "stock_id": [f"S{i}" for i in range(50)],
            "char": rng.normal(size=50),
            "ret": rng.normal(size=50),
            "mcap": rng.lognormal(size=50),
        }
    )
    result = sort_portfolios(panel, "char", "ret", "mcap", n_groups=7)
    assert result[N_STOCKS_COL].sum() == len(panel)
    # Non-decreasing group returns are expected for a well-behaved sort,
    # but above all the partition must be exhaustive.
    assert result[N_STOCKS_COL].sum() == int(panel["char"].notna().sum())


# ---------------------------------------------------------------------------
# Bug #3: empty cross-sections are explicit, not silently skipped
# ---------------------------------------------------------------------------
def test_empty_cross_section_date_is_emitted_not_skipped():
    panel = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2020-01-31", "2020-01-31", "2020-02-29", "2020-02-29"]
            ),
            "stock_id": ["S1", "S2", "S1", "S2"],
            "char": [1.0, 2.0, np.nan, np.nan],
            "ret": [0.01, 0.02, 0.03, 0.04],
            "mcap": [1.0, 1.0, 1.0, 1.0],
        }
    )
    result = sort_portfolios(panel, "char", "ret", "mcap", n_groups=2)

    empty_date = pd.Timestamp("2020-02-29")
    assert empty_date in set(result["date"])
    empty_rows = result.loc[result["date"] == empty_date]
    assert len(empty_rows) == 2  # n_groups rows, even though empty
    assert (empty_rows[N_STOCKS_COL] == 0).all()
    assert empty_rows[EW_RETURN_COL].isna().all()
    assert empty_rows[VW_RETURN_COL].isna().all()

    # The date survives into the long-short series as NaN rather than
    # vanishing and misaligning the time series.
    spread = long_short_return(result)
    assert empty_date in spread.index
    assert np.isnan(spread.loc[empty_date])


def test_date_with_valid_chars_but_no_returns_is_visible():
    panel = pd.DataFrame(
        {
            "date": pd.Timestamp("2020-01-31"),
            "stock_id": ["S1", "S2", "S3"],
            "char": [1.0, 2.0, 3.0],
            "ret": [np.nan, np.nan, np.nan],
            "mcap": [1.0, 2.0, 3.0],
        }
    )
    result = sort_portfolios(panel, "char", "ret", "mcap", n_groups=3)
    assert result[N_STOCKS_COL].sum() == 3
    assert (result[N_RETURNS_COL] == 0).all()
    assert result[EW_RETURN_COL].isna().all()
    assert result[VW_RETURN_COL].isna().all()


def test_empty_panel_returns_empty_frame_with_expected_columns():
    panel = pd.DataFrame(
        {
            "date": pd.Series(dtype="datetime64[ns]"),
            "char": pd.Series(dtype="float64"),
            "ret": pd.Series(dtype="float64"),
            "mcap": pd.Series(dtype="float64"),
        }
    )
    result = sort_portfolios(panel, "char", "ret", "mcap", n_groups=5)
    assert result.empty
    assert list(result.columns) == [
        "date",
        GROUP_COL,
        EW_RETURN_COL,
        VW_RETURN_COL,
        N_STOCKS_COL,
        N_RETURNS_COL,
        WEIGHT_SUM_COL,
    ]


# ---------------------------------------------------------------------------
# Bug #4: the sort characteristic is explicit and column-specific
# ---------------------------------------------------------------------------
def test_two_different_characteristics_produce_column_specific_results():
    # "momentum" rises with return; "reversal" is its exact reverse. The
    # legacy code passed the wrong array and silently reproduced momentum;
    # here the two calls must differ and each must reflect its own column.
    ret = np.array([0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10])
    panel = pd.DataFrame(
        {
            "date": pd.Timestamp("2020-01-31"),
            "stock_id": [f"S{i}" for i in range(10)],
            "momentum": np.arange(1.0, 11.0),
            "reversal": np.arange(10.0, 0.0, -1.0),
            "ret": ret,
            "mcap": [1.0] * 10,
        }
    )
    momentum_sort = sort_portfolios(
        panel, "momentum", "ret", "mcap", n_groups=2
    )
    reversal_sort = sort_portfolios(
        panel, "reversal", "ret", "mcap", n_groups=2
    )

    mom_g1 = momentum_sort.loc[momentum_sort[GROUP_COL] == 1, EW_RETURN_COL].iloc[0]
    mom_g2 = momentum_sort.loc[momentum_sort[GROUP_COL] == 2, EW_RETURN_COL].iloc[0]
    rev_g1 = reversal_sort.loc[reversal_sort[GROUP_COL] == 1, EW_RETURN_COL].iloc[0]
    rev_g2 = reversal_sort.loc[reversal_sort[GROUP_COL] == 2, EW_RETURN_COL].iloc[0]

    # Results are not identical (the copy-paste bug).
    assert not momentum_sort.equals(reversal_sort)
    # Momentum top bucket == reversal bottom bucket, and vice versa.
    assert mom_g2 == pytest.approx(rev_g1)
    assert mom_g1 == pytest.approx(rev_g2)
    assert mom_g2 > mom_g1
    # A descending "reversal" column inverts the groups.
    assert rev_g1 > rev_g2


# ---------------------------------------------------------------------------
# Double sort
# ---------------------------------------------------------------------------
def _double_sort_panel() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.Timestamp("2020-01-31"),
            "stock_id": [f"S{i}" for i in range(1, 9)],
            "size": np.arange(1.0, 9.0),
            "beta": np.arange(8.0, 0.0, -1.0),
            "ret": np.arange(0.01, 0.09, 0.01),
            "mcap": np.arange(10.0, 90.0, 10.0),
        }
    )


def test_double_sort_composes_two_characteristics():
    panel = _double_sort_panel()
    result = double_sort_portfolios(
        panel, "size", "beta", "ret", "mcap", n_groups_1=2, n_groups_2=2
    )

    assert len(result) == 4
    assert result[N_STOCKS_COL].sum() == 8
    assert (result[N_STOCKS_COL] == 2).all()

    def cell(g1: int, g2: int) -> pd.Series:
        return result.loc[
            (result["group_1"] == g1) & (result["group_2"] == g2)
        ].iloc[0]

    # Membership derived by hand:
    #   size group 1 = sizes 1-4 = S1..S4, group 2 = S5..S8.
    #   within size 1, beta descending -> low-beta sub-bucket = {S4, S3},
    #   high-beta = {S2, S1}; within size 2 -> {S8, S7} and {S6, S5}.
    expected = {
        (1, 1): ([0.03, 0.04], [30.0, 40.0]),
        (1, 2): ([0.01, 0.02], [10.0, 20.0]),
        (2, 1): ([0.07, 0.08], [70.0, 80.0]),
        (2, 2): ([0.05, 0.06], [50.0, 60.0]),
    }
    for (g1, g2), (rets, caps) in expected.items():
        row = cell(g1, g2)
        assert row[EW_RETURN_COL] == pytest.approx(np.mean(rets))
        assert row[VW_RETURN_COL] == pytest.approx(np.average(rets, weights=caps))


def test_double_sort_excludes_rows_missing_second_characteristic():
    panel = _double_sort_panel()
    panel.loc[panel["stock_id"] == "S1", "beta"] = np.nan
    result = double_sort_portfolios(
        panel, "size", "beta", "ret", "mcap", n_groups_1=2, n_groups_2=2
    )
    assert result[N_STOCKS_COL].sum() == 7


def test_double_sort_empty_panel():
    panel = pd.DataFrame(
        {
            "date": pd.Series(dtype="datetime64[ns]"),
            "size": pd.Series(dtype="float64"),
            "beta": pd.Series(dtype="float64"),
            "ret": pd.Series(dtype="float64"),
            "mcap": pd.Series(dtype="float64"),
        }
    )
    result = double_sort_portfolios(
        panel, "size", "beta", "ret", "mcap", n_groups_1=2, n_groups_2=3
    )
    assert result.empty


# ---------------------------------------------------------------------------
# long_short_return
# ---------------------------------------------------------------------------
def test_long_short_return_matches_top_minus_bottom():
    panel = _single_date_panel()
    result = sort_portfolios(panel, "char", "ret", "mcap", n_groups=2)

    spread = long_short_return(result)
    expected = (
        result.loc[result[GROUP_COL] == 2, VW_RETURN_COL].iloc[0]
        - result.loc[result[GROUP_COL] == 1, VW_RETURN_COL].iloc[0]
    )
    assert spread.iloc[0] == pytest.approx(expected)
    assert spread.name == f"long_short_{VW_RETURN_COL}"

    ew_spread = long_short_return(result, measure=EW_RETURN_COL, high_group=2)
    expected_ew = (
        result.loc[result[GROUP_COL] == 2, EW_RETURN_COL].iloc[0]
        - result.loc[result[GROUP_COL] == 1, EW_RETURN_COL].iloc[0]
    )
    assert ew_spread.iloc[0] == pytest.approx(expected_ew)


# ---------------------------------------------------------------------------
# Input hygiene
# ---------------------------------------------------------------------------
def test_inputs_are_not_mutated():
    panel = _single_date_panel()
    original = panel.copy(deep=True)
    sort_portfolios(panel, "char", "ret", "mcap", n_groups=2)
    pd.testing.assert_frame_equal(panel, original)

    double_sort_portfolios(
        _double_sort_panel(), "size", "beta", "ret", "mcap", 2, 2
    )
    # rebuild and check again with explicit copy
    panel2 = _double_sort_panel()
    original2 = panel2.copy(deep=True)
    double_sort_portfolios(panel2, "size", "beta", "ret", "mcap", 2, 2)
    pd.testing.assert_frame_equal(panel2, original2)


def test_missing_columns_and_bad_n_groups_raise():
    panel = _single_date_panel()
    with pytest.raises(ValueError, match="missing required column"):
        sort_portfolios(panel, "nope", "ret", "mcap", n_groups=2)
    with pytest.raises(ValueError, match="n_groups must be >= 1"):
        sort_portfolios(panel, "char", "ret", "mcap", n_groups=0)
    with pytest.raises(TypeError, match="must be an integer"):
        sort_portfolios(panel, "char", "ret", "mcap", n_groups=2.5)
    with pytest.raises(TypeError, match="must be numeric"):
        sort_portfolios(panel, "stock_id", "ret", "mcap", n_groups=2)


# ---------------------------------------------------------------------------
# Integration smoke test on the synthetic fixture
# ---------------------------------------------------------------------------
def test_synthetic_panel_smoke(synthetic_source):
    source = synthetic_source
    start = source.ground_truth.market_return.index[0].date()
    end = source.ground_truth.market_return.index[-1].date()

    rets = source.get_returns(start, end)
    caps = source.get_market_cap(start, end)
    signal = source.get_financials(start, end, ["signal"])

    panel = rets.merge(caps, on=["date", "stock_id"]).merge(
        signal, on=["date", "stock_id"]
    )
    panel = panel.sort_values(["stock_id", "date"])
    # Forward return: next month's return aligned to this month's signal.
    panel["fwd_ret"] = panel.groupby("stock_id")["ret"].shift(-1)
    panel = panel.dropna(subset=["signal", "fwd_ret", "mcap", "ret"])

    result = sort_portfolios(panel, "signal", "fwd_ret", "mcap", n_groups=5)
    assert set(result[GROUP_COL].unique()) == {1, 2, 3, 4, 5}
    per_date = result.groupby("date")[N_STOCKS_COL].sum()
    expected = panel.groupby("date")["signal"].apply(lambda s: int(s.notna().sum()))
    pd.testing.assert_series_equal(
        per_date.rename(None), expected.rename(None), check_dtype=False
    )


# ---------------------------------------------------------------------------
# Direct unit tests for the public primitives (used independently of the
# full aggregation loop, e.g. by smart_beta.benchmarks).
# ---------------------------------------------------------------------------
def test_assign_groups_known_bucket_boundaries_with_tie():
    # n == 10 over n_groups == 5, so ranks r map to bucket floor((r-1)/2)+1.
    # The two observations tied at 20 get sequential ranks 2 and 3 and
    # therefore straddle the group-1/group-2 boundary rather than being
    # dropped or lumped entirely into one bucket.
    values = pd.Series([10.0, 20.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0])
    groups = assign_groups(values, 5)

    assert groups.tolist() == [1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0, 5.0, 5.0]
    assert groups.dtype == "float64"
    # Every sortable observation is assigned exactly once.
    assert int(groups.notna().sum()) == len(values)
    assert sorted(groups.dropna().unique().tolist()) == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_assign_groups_fewer_observations_than_groups_leaves_empty_buckets():
    # n == 3 over n_groups == 5: buckets 3 and 5 receive no members but the
    # assigned buckets are still a subset of 1..n_groups with no drops.
    values = pd.Series([1.0, 2.0, 3.0])
    groups = assign_groups(values, 5)

    assert groups.tolist() == [1.0, 2.0, 4.0]
    assert set(groups.dropna().unique()).issubset({1.0, 2.0, 3.0, 4.0, 5.0})
    assert set(groups.unique()) < {1.0, 2.0, 3.0, 4.0, 5.0}


def test_assign_groups_nan_stays_nan_and_is_excluded():
    values = pd.Series([1.0, np.nan, 2.0, np.nan, 3.0, 4.0])
    groups = assign_groups(values, 2)

    assert np.isnan(groups.iloc[1])
    assert np.isnan(groups.iloc[3])
    # Only the four valid observations are ranked/partitioned.
    assert int(groups.notna().sum()) == 4
    assert sorted(groups.dropna().unique().tolist()) == [1.0, 2.0]


def test_assign_groups_all_nan_returns_all_nan():
    values = pd.Series([np.nan, np.nan], index=["a", "b"])
    groups = assign_groups(values, 3)

    assert groups.index.tolist() == ["a", "b"]
    assert groups.isna().all()


def test_group_return_stats_equal_and_value_weighted():
    members = pd.DataFrame(
        {
            "ret": [0.01, 0.02, 0.03, 0.04],
            "mcap": [1.0, 2.0, 3.0, 4.0],
        }
    )
    ew, vw, n_stocks, n_returns, weight_sum = group_return_stats(
        members, "ret", "mcap"
    )

    assert ew == pytest.approx(np.mean([0.01, 0.02, 0.03, 0.04]))
    assert vw == pytest.approx(
        np.average([0.01, 0.02, 0.03, 0.04], weights=[1.0, 2.0, 3.0, 4.0])
    )
    assert n_stocks == 4
    assert n_returns == 4
    assert weight_sum == pytest.approx(10.0)


def test_group_return_stats_empty_group():
    members = pd.DataFrame(
        {
            "ret": pd.Series(dtype="float64"),
            "mcap": pd.Series(dtype="float64"),
        }
    )
    ew, vw, n_stocks, n_returns, weight_sum = group_return_stats(
        members, "ret", "mcap"
    )

    assert np.isnan(ew)
    assert np.isnan(vw)
    assert n_stocks == 0
    assert n_returns == 0
    assert weight_sum == 0.0


def test_group_return_stats_ignores_nan_returns_and_nan_weights():
    members = pd.DataFrame(
        {
            "ret": [0.01, np.nan, 0.03, 0.04],
            "mcap": [1.0, 2.0, np.nan, 4.0],
        }
    )
    ew, vw, n_stocks, n_returns, weight_sum = group_return_stats(
        members, "ret", "mcap"
    )

    # EW ignores the NaN return (rows 2-4), VW additionally ignores the
    # NaN weight (row 3), and weight_sum only counts valid-return weights.
    assert ew == pytest.approx(np.mean([0.01, 0.03, 0.04]))
    assert vw == pytest.approx(np.average([0.01, 0.04], weights=[1.0, 4.0]))
    assert n_stocks == 4
    assert n_returns == 3
    assert weight_sum == pytest.approx(5.0)


def test_group_return_stats_all_nan_returns_gives_nan_means():
    members = pd.DataFrame(
        {
            "ret": [np.nan, np.nan],
            "mcap": [1.0, 2.0],
        }
    )
    ew, vw, n_stocks, n_returns, weight_sum = group_return_stats(
        members, "ret", "mcap"
    )

    assert np.isnan(ew)
    assert np.isnan(vw)
    assert n_stocks == 2
    assert n_returns == 0
    assert weight_sum == 0.0
