"""Tests for :mod:`smart_beta.data.align`.

Covers the canonical per-stock lag utility:

- shift correctness within each stock, including stocks with different
  listing histories (no value may leak across a stock boundary);
- non-mutation of the input frame;
- shifting multiple columns in one call and ``periods > 1``;
- the cross-module regression hazard this module exists to prevent: a beta
  panel from :func:`smart_beta.factors.beta.rolling_ols_beta`, which is
  inclusive of the current date, must lag cleanly onto next-period returns.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smart_beta.data.align import lag_panel
from smart_beta.data.schema import DATE_COL, RETURN_COL, RISK_FREE_COL, STOCK_COL
from smart_beta.factors.base import VALUE_COL
from smart_beta.factors.beta import rolling_ols_beta

START = "2015-01-31"
END = "2030-12-31"


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------


def test_lag_panel_shifts_within_stock_and_preserves_input():
    """Direct port of ``lag_characteristics``' existing test pattern."""
    panel = pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(
                ["2020-01-31", "2020-02-29", "2020-03-31"] * 2
            ),
            STOCK_COL: ["A"] * 3 + ["B"] * 3,
            "char": [1.0, 2.0, 3.0, 10.0, 20.0, 30.0],
            RETURN_COL: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        }
    )
    before = panel.copy(deep=True)

    out = lag_panel(panel, ["char"], periods=1)

    a = out.loc[out[STOCK_COL] == "A"].sort_values(DATE_COL)["char"].tolist()
    b = out.loc[out[STOCK_COL] == "B"].sort_values(DATE_COL)["char"].tolist()
    assert np.isnan(a[0]) and a[1:] == [1.0, 2.0]
    assert np.isnan(b[0]) and b[1:] == [10.0, 20.0]
    pd.testing.assert_frame_equal(panel, before)


def test_lag_panel_never_leaks_across_different_listing_histories():
    """Stock B lists later than A: B's early rows must be NaN, never A's values.

    A positional ``shift`` over the concatenated rows would carry A's last
    observation into B's first row; the per-stock groupby shift must not.
    """
    panel = pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(
                [
                    "2020-01-31",
                    "2020-02-29",
                    "2020-03-31",
                    "2020-04-30",
                    "2020-03-31",
                    "2020-04-30",
                    "2020-05-31",
                ]
            ),
            STOCK_COL: ["A"] * 4 + ["B"] * 3,
            "char": [1.0, 2.0, 3.0, 4.0, 100.0, 200.0, 300.0],
            RETURN_COL: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
        }
    )
    before = panel.copy(deep=True)

    out = lag_panel(panel, ["char"], periods=1)

    a = (
        out.loc[out[STOCK_COL] == "A"]
        .sort_values(DATE_COL)["char"]
        .tolist()
    )
    b = (
        out.loc[out[STOCK_COL] == "B"]
        .sort_values(DATE_COL)["char"]
        .tolist()
    )
    assert np.isnan(a[0]) and a[1:] == [1.0, 2.0, 3.0]
    # B's first listed month (2020-03-31) must be NaN even though A has a
    # value at that same date, and the rest shift only B's own values.
    assert np.isnan(b[0])
    assert b[1:] == [100.0, 200.0]
    pd.testing.assert_frame_equal(panel, before)


def test_lag_panel_does_not_mutate_input():
    panel = pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(["2020-01-31", "2020-02-29"] * 2),
            STOCK_COL: ["A", "A", "B", "B"],
            "x": [1.0, 2.0, 3.0, 4.0],
            "y": [10.0, 20.0, 30.0, 40.0],
        }
    )
    before = panel.copy(deep=True)

    lag_panel(panel, ["x", "y"], periods=1)

    pd.testing.assert_frame_equal(panel, before)


def test_lag_panel_shifts_multiple_columns_in_one_call():
    panel = pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"] * 2),
            STOCK_COL: ["A"] * 3 + ["B"] * 3,
            "x": [1.0, 2.0, 3.0, 10.0, 20.0, 30.0],
            "y": [0.1, 0.2, 0.3, 1.1, 1.2, 1.3],
        }
    )
    out = lag_panel(panel, ["x", "y"], periods=1)

    a = out.loc[out[STOCK_COL] == "A"].sort_values(DATE_COL)
    b = out.loc[out[STOCK_COL] == "B"].sort_values(DATE_COL)
    assert np.isnan(a["x"].iloc[0]) and np.isnan(a["y"].iloc[0])
    assert a["x"].iloc[1:].tolist() == [1.0, 2.0]
    assert a["y"].iloc[1:].tolist() == [0.1, 0.2]
    assert np.isnan(b["x"].iloc[0]) and np.isnan(b["y"].iloc[0])
    assert b["x"].iloc[1:].tolist() == [10.0, 20.0]
    assert b["y"].iloc[1:].tolist() == [1.1, 1.2]


def test_lag_panel_periods_greater_than_one():
    panel = pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(
                ["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"] * 2
            ),
            STOCK_COL: ["A"] * 4 + ["B"] * 4,
            "char": [1.0, 2.0, 3.0, 4.0, 10.0, 20.0, 30.0, 40.0],
        }
    )
    out = lag_panel(panel, ["char"], periods=2)

    a = out.loc[out[STOCK_COL] == "A"].sort_values(DATE_COL)["char"].tolist()
    b = out.loc[out[STOCK_COL] == "B"].sort_values(DATE_COL)["char"].tolist()
    assert np.isnan(a[0]) and np.isnan(a[1]) and a[2:] == [1.0, 2.0]
    assert np.isnan(b[0]) and np.isnan(b[1]) and b[2:] == [10.0, 20.0]


def test_lag_panel_missing_columns_raise():
    panel = pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(["2020-01-31"]),
            STOCK_COL: ["A"],
            "char": [1.0],
        }
    )
    with pytest.raises(ValueError, match="missing required columns"):
        lag_panel(panel, ["not_a_column"])
    with pytest.raises(ValueError, match="missing required columns"):
        lag_panel(panel, ["char"], date_col="not_a_date")


def test_lag_panel_output_is_sorted_and_reset():
    panel = pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(
                ["2020-02-29", "2020-01-31", "2020-02-29", "2020-01-31"]
            ),
            STOCK_COL: ["B", "B", "A", "A"],
            "char": [20.0, 10.0, 2.0, 1.0],
        }
    )
    out = lag_panel(panel, ["char"], periods=1)

    assert out.index.tolist() == [0, 1, 2, 3]
    keys = list(zip(out[STOCK_COL], out[DATE_COL]))
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# Cross-module regression: beta panel alignment
# ---------------------------------------------------------------------------


def test_lag_panel_aligns_real_beta_panel_to_previous_date(synthetic_source):
    """``rolling_ols_beta`` is inclusive of date *t*, so ``lag_panel`` at *t*
    must reproduce the unlagged beta at *t - 1* for the same stock -- and
    must never borrow another stock's (or another date's) beta.
    """
    gt = synthetic_source.ground_truth
    returns = synthetic_source.get_returns(START, END)
    rf = synthetic_source.get_risk_free(START, END).set_index(DATE_COL)[
        RISK_FREE_COL
    ]
    beta = rolling_ols_beta(returns, gt.market_return, rf)
    beta_before = beta.copy(deep=True)

    lagged = lag_panel(beta, [VALUE_COL], periods=1, date_col=DATE_COL, stock_col=STOCK_COL)

    # Input beta panel untouched.
    pd.testing.assert_frame_equal(beta, beta_before)

    unlagged_lookup = {
        (stock, date): value
        for stock, date, value in beta[[STOCK_COL, DATE_COL, VALUE_COL]].itertuples(
            index=False, name=None
        )
    }

    checked = 0
    for stock, group in beta.groupby(STOCK_COL, sort=False):
        ordered = group.sort_values(DATE_COL)
        dates = ordered[DATE_COL].tolist()
        values = ordered[VALUE_COL].tolist()
        # First date must be NaN (nothing before it to carry forward).
        first = lagged.loc[
            (lagged[STOCK_COL] == stock) & (lagged[DATE_COL] == dates[0]),
            VALUE_COL,
        ].iloc[0]
        assert np.isnan(first)

        # Each subsequent row equals the *previous* unlagged value.
        for i in range(1, len(dates)):
            lagged_value = lagged.loc[
                (lagged[STOCK_COL] == stock) & (lagged[DATE_COL] == dates[i]),
                VALUE_COL,
            ].iloc[0]
            previous_unlagged = unlagged_lookup[(stock, dates[i - 1])]
            if np.isnan(previous_unlagged):
                assert np.isnan(lagged_value)
            else:
                assert lagged_value == pytest.approx(previous_unlagged)
            checked += 1

    assert checked > 0


def test_lag_panel_aligns_late_listing_beta_without_cross_stock_leak(
    synthetic_source,
):
    """A stock that lists partway through the sample gets a clean first-row
    NaN, not the previous (earlier-listed) stock's beta.
    """
    gt = synthetic_source.ground_truth
    returns = synthetic_source.get_returns(START, END)
    rf = synthetic_source.get_risk_free(START, END).set_index(DATE_COL)[
        RISK_FREE_COL
    ]
    beta = rolling_ols_beta(returns, gt.market_return, rf)

    listing = synthetic_source.get_listing_info()
    first_date = beta[DATE_COL].min()
    late = listing.loc[listing["list_date"] > first_date, STOCK_COL]
    if late.empty:  # defensive: fixture should always contain late listers
        pytest.skip("fixture produced no late-listing stocks")

    stock = late.iloc[0]
    stock_panel = beta.loc[beta[STOCK_COL] == stock].sort_values(DATE_COL)
    list_date = stock_panel[DATE_COL].iloc[0]

    lagged = lag_panel(beta, [VALUE_COL], periods=1)
    lagged_first = lagged.loc[
        (lagged[STOCK_COL] == stock) & (lagged[DATE_COL] == list_date),
        VALUE_COL,
    ].iloc[0]

    assert np.isnan(lagged_first)
