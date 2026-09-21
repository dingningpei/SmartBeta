"""Tests for :mod:`smart_beta.evaluation.forward_returns` (Phase 7, P7-B).

Covers the frozen P7-B contract:

* exact horizon semantics (``[t, t+h]`` in realized-return periods) and
  strict no-look-ahead / no-contemporaneous pairing;
* deterministic missing-return ``NaN`` semantics with preserved counts;
* the release-critical §8.1 cross-boundary PURGE rule --
  IS → OOS, OOS → final holdout, and walk-forward fold crossings (purged,
  never truncated, shortened, reassigned, or borrowed);
* non-mutation of both input panels;
* the absence of any provider / PIT authority in this module.

No network, provider, or data-API call is made. The partition predicate used
by the crossing tests is a minimal local stub (P7-A's ``partition.py`` does
not exist in this worktree and is deliberately not imported).
"""

from __future__ import annotations

import ast
import pathlib

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from smart_beta.data.schema import (
    DATE_COL,
    STOCK_COL,
    VALUE_COL,
    SchemaError,
)
from smart_beta.evaluation import forward_returns as fr

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# Twelve month-end observation dates: the realized-return panel's calendar.
DATES = pd.to_datetime(
    [
        "2020-01-31",
        "2020-02-29",
        "2020-03-31",
        "2020-04-30",
        "2020-05-31",
        "2020-06-30",
        "2020-07-31",
        "2020-08-31",
        "2020-09-30",
        "2020-10-31",
        "2020-11-30",
        "2020-12-31",
    ]
)

AAA_RETURNS = np.array(
    [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12]
)
BBB_RETURNS = np.array(
    [-0.01, 0.02, -0.03, 0.04, -0.05, 0.06, -0.07, 0.08, -0.09, 0.10, -0.11, 0.12]
)


def make_factor_panel(records) -> pd.DataFrame:
    """Build a schema-valid ``(date, stock_id, value)`` factor panel."""
    return pd.DataFrame(
        {
            DATE_COL: pd.to_datetime([r[0] for r in records]),
            STOCK_COL: pd.array([r[1] for r in records], dtype="string"),
            VALUE_COL: np.array([r[2] for r in records], dtype="float64"),
        }
    )


def make_returns_panel(records, col: str = fr.DEFAULT_RETURN_COL) -> pd.DataFrame:
    """Build a realized-return panel ``(date, stock_id, <col>)``."""
    return pd.DataFrame(
        {
            DATE_COL: pd.to_datetime([r[0] for r in records]),
            STOCK_COL: pd.array([r[1] for r in records], dtype="string"),
            col: np.array([r[2] for r in records], dtype="float64"),
        }
    )


def full_returns(
    aaa: np.ndarray = AAA_RETURNS,
    bbb: np.ndarray = BBB_RETURNS,
    col: str = fr.DEFAULT_RETURN_COL,
) -> pd.DataFrame:
    """Both stocks over the full calendar, in a deliberately unsorted order."""
    records = []
    for stock, series in (("AAA", aaa), ("BBB", bbb)):
        for d, r in zip(DATES, series):
            records.append((d, stock, float(r)))
    # Reverse to prove input order independence.
    return make_returns_panel(list(reversed(records)), col=col)


def one_obs(date, stock, value, h, returns=None, **kwargs):
    factor = make_factor_panel([(date, stock, value)])
    returns = full_returns() if returns is None else returns
    return fr.align_forward_returns(factor, returns, h, **kwargs)


def row_for(result: fr.ForwardReturnAlignment, date, stock):
    sub = result.panel[
        (result.panel[DATE_COL] == pd.Timestamp(date))
        & (result.panel[STOCK_COL] == stock)
    ]
    assert len(sub) == 1, f"expected exactly one retained row, got {len(sub)}"
    return sub.iloc[0]


# ---------------------------------------------------------------------------
# §8.1 partition stubs (local only; sibling P7-A module is NOT imported)
# ---------------------------------------------------------------------------

IS_OOS_HOLDOUT = [
    ("is", "2020-01-01", "2020-06-30"),
    ("oos", "2020-07-01", "2020-09-30"),
    ("holdout", "2020-10-01", "2020-12-31"),
]

WALK_FORWARD = [
    ("f1", "2020-01-01", "2020-03-31"),
    ("f2", "2020-04-01", "2020-06-30"),
    ("f3", "2020-07-01", "2020-09-30"),
    ("f4", "2020-10-01", "2020-12-31"),
]


def _label(folds, d):
    d = pd.Timestamp(d)
    for name, lo, hi in folds:
        if pd.Timestamp(lo) <= d <= pd.Timestamp(hi):
            return name
    return None


def make_boundary_predicate(folds):
    """§8.1 predicate: True iff ``[start, end]`` is not inside one fold."""

    def crosses(start, end) -> bool:
        left = _label(folds, start)
        right = _label(folds, end)
        return left is None or right is None or left != right

    return crosses


class _PartitionStub:
    """Object form of the predicate, exercising the documented duck typing."""

    def __init__(self, folds):
        self._predicate = make_boundary_predicate(folds)

    def crosses_boundary(self, start, end) -> bool:
        return self._predicate(start, end)


# ---------------------------------------------------------------------------
# Exact horizon semantics / no look-ahead
# ---------------------------------------------------------------------------


def test_horizon_one_is_the_next_realized_return_row():
    """h=1 pairs the formation at t with the row dated t+1, never t."""
    result = one_obs("2020-03-31", "AAA", 1.0, 1)
    assert result.panel[fr.FORWARD_RETURN_COL].iloc[0] == pytest.approx(0.04)
    assert result.panel[fr.REALIZATION_START_COL].iloc[0] == pd.Timestamp(
        "2020-03-31"
    )
    assert result.panel[fr.REALIZATION_END_COL].iloc[0] == pd.Timestamp(
        "2020-04-30"
    )


def test_horizon_h_compounds_exactly_the_h_following_rows():
    result = one_obs("2020-03-31", "AAA", 1.0, 3)
    expected = (1.0 + 0.04) * (1.0 + 0.05) * (1.0 + 0.06) - 1.0
    assert result.panel[fr.FORWARD_RETURN_COL].iloc[0] == pytest.approx(expected)
    assert result.panel[fr.REALIZATION_END_COL].iloc[0] == pd.Timestamp(
        "2020-06-30"
    )


def test_contemporaneous_formation_row_is_never_used():
    """A huge return *at* the formation date must not move the label."""
    contaminated = full_returns()
    contaminated.loc[
        contaminated[DATE_COL] == pd.Timestamp("2020-03-31"),
        fr.DEFAULT_RETURN_COL,
    ] = 5.0
    result = one_obs("2020-03-31", "AAA", 1.0, 1, returns=contaminated)
    assert result.panel[fr.FORWARD_RETURN_COL].iloc[0] == pytest.approx(0.04)


def test_no_backward_pairing_uses_no_return_at_or_before_formation():
    """Rewriting all returns up to and including t must not change the label."""
    baseline = one_obs("2020-06-30", "AAA", 1.0, 2)

    rewritten = full_returns()
    dates = rewritten[DATE_COL]
    rewritten.loc[
        dates <= pd.Timestamp("2020-06-30"), fr.DEFAULT_RETURN_COL
    ] = -0.99
    result = one_obs("2020-06-30", "AAA", 1.0, 2, returns=rewritten)

    expected = (1.0 + 0.07) * (1.0 + 0.08) - 1.0
    assert result.panel[fr.FORWARD_RETURN_COL].iloc[0] == pytest.approx(expected)
    assert baseline.panel[fr.FORWARD_RETURN_COL].iloc[0] == pytest.approx(expected)


def test_future_returns_do_change_the_label():
    """Sanity: the forward label really is derived from the future."""
    changed = full_returns()
    changed.loc[
        changed[DATE_COL] == pd.Timestamp("2020-04-30"), fr.DEFAULT_RETURN_COL
    ] = 0.50
    result = one_obs("2020-03-31", "AAA", 1.0, 1, returns=changed)
    assert result.panel[fr.FORWARD_RETURN_COL].iloc[0] == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# Missing-return semantics
# ---------------------------------------------------------------------------


def test_missing_return_is_nan_not_dropped_and_is_counted():
    rets = full_returns()
    # AAA has no observation on 2020-04-30 (the h=1 window of the 03-31 label).
    rets = rets[
        ~(
            (rets[DATE_COL] == pd.Timestamp("2020-04-30"))
            & (rets[STOCK_COL] == "AAA")
        )
    ]
    result = one_obs("2020-03-31", "AAA", 1.0, 1, returns=rets)

    assert len(result.panel) == 1
    assert np.isnan(result.panel[fr.FORWARD_RETURN_COL].iloc[0])
    assert (
        result.panel[fr.STATUS_COL].iloc[0] == fr.STATUS_MISSING_RETURN
    )
    assert result.n_missing_returns == 1
    assert result.n_nan_returns == 1
    assert result.n_retained == 1


def test_nan_return_value_inside_window_is_missing_not_zero():
    rets = full_returns()
    rets.loc[
        (rets[DATE_COL] == pd.Timestamp("2020-04-30"))
        & (rets[STOCK_COL] == "AAA"),
        fr.DEFAULT_RETURN_COL,
    ] = np.nan
    result = one_obs("2020-03-31", "AAA", 1.0, 1, returns=rets)
    assert np.isnan(result.panel[fr.FORWARD_RETURN_COL].iloc[0])
    assert result.panel[fr.STATUS_COL].iloc[0] == fr.STATUS_MISSING_RETURN


def test_insufficient_forward_window_counted():
    """The last calendar date has no following row for h=1."""
    result = one_obs("2020-12-31", "AAA", 1.0, 2)
    assert result.panel[fr.STATUS_COL].iloc[0] == (
        fr.STATUS_INSUFFICIENT_FORWARD_WINDOW
    )
    assert np.isnan(result.panel[fr.FORWARD_RETURN_COL].iloc[0])
    assert result.n_insufficient_forward_window == 1
    assert result.n_missing_returns == 0


def test_counts_are_self_consistent_and_exposed():
    records = []
    for d in DATES[:-2]:
        records.append((d, "AAA", 1.0))
        records.append((d, "BBB", 2.0))
    factor = make_factor_panel(records)
    result = fr.align_forward_returns(factor, full_returns(), 2)

    assert result.n_observations == len(records)
    assert result.n_retained + result.n_purged == result.n_observations
    assert result.n_aligned + result.n_nan_returns == result.n_retained
    assert (
        result.n_missing_returns + result.n_insufficient_forward_window
        == result.n_nan_returns
    )
    assert result.n_purged == 0


def test_empty_realized_return_panel_is_all_insufficient():
    empty = make_returns_panel([])
    factor = make_factor_panel([("2020-01-31", "AAA", 1.0)])
    result = fr.align_forward_returns(factor, empty, 1)
    assert result.n_insufficient_forward_window == 1
    assert np.isnan(result.panel[fr.FORWARD_RETURN_COL].iloc[0])
    assert pd.api.types.is_string_dtype(result.panel[fr.STATUS_COL])

def test_empty_factor_panel_returns_empty_alignment():
    result = fr.align_forward_returns(make_factor_panel([]), full_returns(), 1)
    assert result.panel.empty
    assert result.purged.empty
    assert result.n_observations == 0
    assert list(result.panel.columns) == list(fr._ALIGNED_COLUMNS)


# ---------------------------------------------------------------------------
# §8.1 release-critical purge: IS → OOS
# ---------------------------------------------------------------------------


def test_is_to_oos_crossing_is_purged_not_truncated_or_reassigned():
    """Required case 1. An IS formation whose label realizes in OOS is purged."""
    rets = full_returns()
    # A distinctive OOS return that must never label the IS observation.
    rets.loc[
        (rets[DATE_COL] == pd.Timestamp("2020-07-31"))
        & (rets[STOCK_COL] == "AAA"),
        fr.DEFAULT_RETURN_COL,
    ] = 0.42

    predicate = make_boundary_predicate(IS_OOS_HOLDOUT)

    factor = make_factor_panel(
        [
            ("2020-05-31", "AAA", 1.0),  # IS -> IS, h=1, retained
            ("2020-06-30", "AAA", 1.0),  # IS -> OOS (Jul), h=1, purged
        ]
    )
    result = fr.align_forward_returns(factor, rets, 1, partition_boundary=predicate)

    # The 06-30 h=1 label crosses; nothing about it survives in the panel.
    assert ("2020-06-30" not in result.panel[DATE_COL].dt.strftime("%Y-%m-%d").tolist())
    purged_dates = result.purged[DATE_COL].dt.strftime("%Y-%m-%d").tolist()
    assert purged_dates == ["2020-06-30"]
    assert set(result.purged[fr.STATUS_COL]) == {
        fr.STATUS_PURGED_CROSSES_BOUNDARY
    }
    assert result.n_purged == 1
    # The OOS return never labels anything.
    assert 0.42 not in result.panel[fr.FORWARD_RETURN_COL].tolist()


def test_is_to_oos_purge_uses_the_complete_horizon_and_never_borrows():
    """h=2 view of the same boundary: no truncation, no next-partition borrow."""
    rets = full_returns()
    rets.loc[
        (rets[DATE_COL] == pd.Timestamp("2020-07-31"))
        & (rets[STOCK_COL] == "AAA"),
        fr.DEFAULT_RETURN_COL,
    ] = 0.42
    rets.loc[
        (rets[DATE_COL] == pd.Timestamp("2020-08-31"))
        & (rets[STOCK_COL] == "AAA"),
        fr.DEFAULT_RETURN_COL,
    ] = 0.99

    predicate = make_boundary_predicate(IS_OOS_HOLDOUT)
    result = one_obs(
        "2020-06-30", "AAA", 1.0, 2, returns=rets, partition_boundary=predicate
    )

    assert result.panel.empty
    assert result.n_purged == 1
    row = result.purged.iloc[0]
    # The attempted interval is recorded, not shortened.
    assert row[fr.REALIZATION_START_COL] == pd.Timestamp("2020-06-30")
    assert row[fr.REALIZATION_END_COL] == pd.Timestamp("2020-08-31")
    # Neither a truncated (Jul only) nor a borrowed next-partition label appears.
    truncated = 0.42
    assert truncated not in result.panel[fr.FORWARD_RETURN_COL].tolist()


def test_no_partition_supplied_is_pure_alignment():
    """The same crossing observation is retained when no partition is applied."""
    result = one_obs("2020-06-30", "AAA", 1.0, 1)
    assert not result.panel.empty
    assert result.n_purged == 0
    assert result.panel[fr.FORWARD_RETURN_COL].iloc[0] == pytest.approx(0.07)


# ---------------------------------------------------------------------------
# §8.1 release-critical purge: OOS → final holdout
# ---------------------------------------------------------------------------


def test_oos_to_final_holdout_crossing_is_purged():
    """Required case 2. The holdout return never labels an OOS observation."""
    rets = full_returns()
    rets.loc[
        (rets[DATE_COL] == pd.Timestamp("2020-10-31"))
        & (rets[STOCK_COL] == "AAA"),
        fr.DEFAULT_RETURN_COL,
    ] = 9.99

    predicate = make_boundary_predicate(IS_OOS_HOLDOUT)
    factor = make_factor_panel(
        [
            ("2020-08-31", "AAA", 1.0),  # OOS -> OOS, h=1, retained
            ("2020-09-30", "AAA", 1.0),  # OOS -> holdout, h=1, purged
        ]
    )
    result = fr.align_forward_returns(factor, rets, 1, partition_boundary=predicate)

    assert result.panel[DATE_COL].dt.strftime("%Y-%m-%d").tolist() == [
        "2020-08-31"
    ]
    assert result.purged[DATE_COL].dt.strftime("%Y-%m-%d").tolist() == [
        "2020-09-30"
    ]
    assert result.purged.iloc[0][fr.REALIZATION_END_COL] == pd.Timestamp(
        "2020-10-31"
    )
    assert 9.99 not in result.panel[fr.FORWARD_RETURN_COL].tolist()


def test_holdout_observation_itself_is_retained_within_holdout():
    """The purge removes crossings only; an in-holdout label is untouched."""
    predicate = make_boundary_predicate(IS_OOS_HOLDOUT)
    # 2020-10-31 h=1 -> 2020-11-30, both holdout.
    result = one_obs(
        "2020-10-31", "AAA", 1.0, 1, partition_boundary=predicate
    )
    assert result.n_purged == 0
    assert result.panel[fr.FORWARD_RETURN_COL].iloc[0] == pytest.approx(0.11)


# ---------------------------------------------------------------------------
# §8.1 release-critical purge: walk-forward fold crossing
# ---------------------------------------------------------------------------


def test_walk_forward_fold_crossing_purged_on_every_boundary():
    """Required case 3. Crossing any fold boundary purges, never reassigns."""
    predicate = make_boundary_predicate(WALK_FORWARD)
    factor = make_factor_panel(
        [
            ("2020-02-29", "AAA", 1.0),  # f1 -> f1, h=1, retained
            ("2020-03-31", "AAA", 1.0),  # f1 -> f2, h=1, purged
            ("2020-05-31", "AAA", 1.0),  # f2 -> f2, h=1, retained
            ("2020-06-30", "AAA", 1.0),  # f2 -> f3, h=1, purged
            ("2020-08-31", "AAA", 1.0),  # f3 -> f3, h=1, retained
            ("2020-09-30", "AAA", 1.0),  # f3 -> f4, h=2, purged
        ]
    )
    result = fr.align_forward_returns(
        factor, full_returns(), 1, partition_boundary=predicate
    )
    # Note: this call uses h=1, so the 09-30 -> 10-31 crossing is purged; the
    # h=2 variant is exercised below to cover a multi-fold-spanning window.

    retained = result.panel[DATE_COL].dt.strftime("%Y-%m-%d").tolist()
    purged = result.purged[DATE_COL].dt.strftime("%Y-%m-%d").tolist()
    assert retained == ["2020-02-29", "2020-05-31", "2020-08-31"]
    assert purged == ["2020-03-31", "2020-06-30", "2020-09-30"]

    # Every retained observation has both endpoints inside one fold.
    for _, row in result.panel.iterrows():
        assert _label(WALK_FORWARD, row[DATE_COL]) == _label(
            WALK_FORWARD, row[fr.REALIZATION_END_COL]
        )
    # No purged observation survived anywhere in the aligned panel.
    assert not set(purged) & set(retained)


def test_horizon_spanning_multiple_folds_is_purged():
    predicate = make_boundary_predicate(WALK_FORWARD)
    # h=3 from 2020-03-31 reaches 2020-06-30 -- crossing f1/f2 and f2/f3.
    result = one_obs(
        "2020-03-31", "AAA", 1.0, 3, partition_boundary=predicate
    )
    assert result.panel.empty
    assert result.n_purged == 1
    assert result.purged.iloc[0][fr.REALIZATION_END_COL] == pd.Timestamp(
        "2020-06-30"
    )


def test_purge_is_deterministic_regardless_of_input_order():
    predicate = make_boundary_predicate(WALK_FORWARD)
    records = [
        ("2020-03-31", "AAA", 1.0),
        ("2020-05-31", "BBB", 2.0),
        ("2020-06-30", "AAA", 3.0),
    ]
    forward = fr.align_forward_returns(
        make_factor_panel(records), full_returns(), 1, partition_boundary=predicate
    )
    backward = fr.align_forward_returns(
        make_factor_panel(list(reversed(records))),
        full_returns(),
        1,
        partition_boundary=predicate,
    )
    assert_frame_equal(forward.panel, backward.panel)
    assert_frame_equal(forward.purged, backward.purged)


def test_object_form_of_the_boundary_predicate_is_accepted():
    predicate = _PartitionStub(WALK_FORWARD)
    result = one_obs(
        "2020-03-31", "AAA", 1.0, 1, partition_boundary=predicate
    )
    assert result.n_purged == 1
    assert result.panel.empty


def test_boundary_predicate_exception_propagates_fail_closed():
    def exploding(start, end):
        raise RuntimeError("partition unavailable")

    with pytest.raises(RuntimeError, match="partition unavailable"):
        one_obs(
            "2020-03-31", "AAA", 1.0, 1, partition_boundary=exploding
        )


def test_invalid_boundary_predicate_type_fails_closed():
    with pytest.raises(TypeError, match="partition_boundary"):
        one_obs("2020-03-31", "AAA", 1.0, 1, partition_boundary=5)


def test_out_of_partition_observation_is_purged_fail_closed():
    """A formation outside every declared fold is purged, not silently kept."""
    predicate = make_boundary_predicate(
        [("x", "2020-01-01", "2020-02-29")]
    )
    result = one_obs(
        "2020-06-30", "AAA", 1.0, 1, partition_boundary=predicate
    )
    assert result.panel.empty
    assert result.n_purged == 1


# ---------------------------------------------------------------------------
# Weekend / holiday boundary trap
# ---------------------------------------------------------------------------


def test_boundary_check_uses_the_realization_date_not_calendar_arithmetic():
    """A fold boundary on a weekend is applied to the *trading* realization date."""
    dates = pd.to_datetime(
        ["2020-06-26", "2020-06-29", "2020-06-30", "2020-07-01"]
    )
    returns = make_returns_panel(
        [(d, "AAA", 0.01 * (i + 1)) for i, d in enumerate(dates)]
    )
    factor = make_factor_panel([("2020-06-26", "AAA", 1.0)])
    # Boundary Sunday 2020-06-28: fold A ends 06-28, fold B starts 06-29.
    predicate = make_boundary_predicate(
        [("a", "2020-06-01", "2020-06-28"), ("b", "2020-06-29", "2020-07-31")]
    )

    crossing = fr.align_forward_returns(
        factor, returns, 1, partition_boundary=predicate
    )
    assert crossing.n_purged == 1
    assert crossing.purged.iloc[0][fr.REALIZATION_END_COL] == pd.Timestamp(
        "2020-06-29"
    )

    pure = fr.align_forward_returns(factor, returns, 1)
    assert pure.n_purged == 0
    assert pure.panel[fr.FORWARD_RETURN_COL].iloc[0] == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# Non-mutation / contract boundaries
# ---------------------------------------------------------------------------


def test_factor_panel_and_return_panel_are_not_mutated():
    factor = make_factor_panel(
        [("2020-03-31", "AAA", 1.0), ("2020-05-31", "BBB", 2.0)]
    )
    returns = full_returns()
    factor_before = factor.copy(deep=True)
    returns_before = returns.copy(deep=True)

    fr.align_forward_returns(
        factor,
        returns,
        2,
        partition_boundary=make_boundary_predicate(WALK_FORWARD),
    )

    assert_frame_equal(factor, factor_before)
    assert_frame_equal(returns, returns_before)


def test_factor_values_are_copied_verbatim():
    factor = make_factor_panel(
        [("2020-03-31", "AAA", 3.5), ("2020-05-31", "BBB", -1.25)]
    )
    result = fr.align_forward_returns(factor, full_returns(), 1)
    merged = result.panel.sort_values(STOCK_COL).reset_index(drop=True)
    assert merged[VALUE_COL].tolist() == [3.5, -1.25]


def test_input_row_order_does_not_affect_output():
    records = [
        ("2020-03-31", "AAA", 1.0),
        ("2020-03-31", "BBB", 2.0),
        ("2020-05-31", "AAA", 3.0),
    ]
    a = fr.align_forward_returns(make_factor_panel(records), full_returns(), 2)
    b = fr.align_forward_returns(
        make_factor_panel(list(reversed(records))), full_returns(), 2
    )
    assert_frame_equal(a.panel, b.panel)


def test_output_schema_and_dtypes():
    result = one_obs("2020-03-31", "AAA", 1.0, 1)
    assert list(result.panel.columns) == list(fr._ALIGNED_COLUMNS)
    assert list(result.purged.columns) == list(fr._PURGED_COLUMNS)
    assert result.panel[fr.FORWARD_RETURN_COL].dtype == np.dtype("float64")
    assert pd.api.types.is_datetime64_any_dtype(result.panel[DATE_COL])
    assert pd.api.types.is_datetime64_any_dtype(
        result.panel[fr.REALIZATION_END_COL]
    )


# ---------------------------------------------------------------------------
# Fail-closed input validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, -5])
def test_non_positive_horizon_is_rejected(bad):
    with pytest.raises(ValueError, match="horizon must be >= 1"):
        one_obs("2020-03-31", "AAA", 1.0, bad)


@pytest.mark.parametrize("bad", [1.5, "1", True])
def test_non_integer_horizon_is_rejected(bad):
    with pytest.raises(TypeError, match="horizon must be an int"):
        one_obs("2020-03-31", "AAA", 1.0, bad)


def test_missing_adjusted_return_column_fails_closed_without_fallback():
    rets = full_returns(col="ret")
    with pytest.raises(ValueError, match="never falls back"):
        one_obs("2020-03-31", "AAA", 1.0, 1, returns=rets)


def test_explicit_return_col_is_honoured():
    rets = full_returns(col="ret")
    result = one_obs(
        "2020-03-31", "AAA", 1.0, 1, returns=rets, return_col="ret"
    )
    assert result.panel[fr.FORWARD_RETURN_COL].iloc[0] == pytest.approx(0.04)


def test_duplicate_factor_keys_are_rejected():
    factor = make_factor_panel(
        [("2020-03-31", "AAA", 1.0), ("2020-03-31", "AAA", 2.0)]
    )
    with pytest.raises(SchemaError):
        fr.align_forward_returns(factor, full_returns(), 1)


def test_duplicate_return_keys_are_rejected():
    returns = make_returns_panel(
        [
            ("2020-03-31", "AAA", 0.01),
            ("2020-03-31", "AAA", 0.02),
        ]
    )
    factor = make_factor_panel([("2020-03-31", "AAA", 1.0)])
    with pytest.raises(SchemaError):
        fr.align_forward_returns(factor, returns, 1)


# ---------------------------------------------------------------------------
# Authority boundary: no provider / PIT authority in this module
# ---------------------------------------------------------------------------

_FORBIDDEN_IMPORT_PREFIXES = (
    "smart_beta.vendors",
    "smart_beta.pit",
    "smart_beta.research_inputs",
    "smart_beta.pipelines",
    "smart_beta.data.sources",
    "smart_beta.benchmarks",
)


def test_module_has_no_provider_or_pit_authority():
    tree = ast.parse(pathlib.Path(fr.__file__).read_text(encoding="utf-8"))

    imported: list[str] = []
    names: set[str] = set()
    attributes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            attributes.add(node.attr)

    offenders = [
        name
        for name in imported
        if name == "smart_beta.vendors"
        or any(name.startswith(prefix) for prefix in _FORBIDDEN_IMPORT_PREFIXES)
    ]
    assert offenders == []
    # No PIT selection or provider-fetch symbol may appear at all.
    assert "PointInTimeView" not in names
    assert "get_realized_returns" not in names
    assert "as_of" not in attributes
    assert "lag_panel" not in names
    assert not hasattr(fr, "PointInTimeView")
    assert not hasattr(fr, "PITDataSource")
