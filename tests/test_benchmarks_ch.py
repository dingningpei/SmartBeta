"""Tests for the non-migrated CH-3/CH-4 benchmark constructions.

These are the CH3/CH4-relevant tests moved verbatim out of the pre-Phase-4C
``tests/test_benchmarks.py``.  ``smart_beta/benchmarks/ch3.py`` and
``ch4.py`` are deliberately **not** migrated in Phase 4C: they still consume
``smart_beta.data.sources.base.DataSource`` directly, and these tests still
exercise them against the legacy ``SyntheticDataSource`` with exactly the
same calls and assertions as before.

Covered:

* shape/column contract: one row per date in the requested range;
* no look-ahead: truncating the sample at date ``t`` cannot change any factor
  value at or before ``t``;
* CH-3's shell-stock screen really excludes the bottom 30% by market cap from
  the size sort.

Do not migrate this file's calls: it is the behavior-preservation guard for
CH3/CH4's still-valid legacy contract.
"""

from __future__ import annotations

import pandas as pd
import pytest

from smart_beta.benchmarks.capm import _load_panel
from smart_beta.benchmarks.ch3 import _add_ch3_size_groups, compute_ch3_factors
from smart_beta.benchmarks.ch4 import compute_ch4_factors
from smart_beta.config.settings import DEFAULT_SETTINGS
from smart_beta.data.schema import DATE_COL, MARKET_CAP_COL

START = "2015-01-31"
END = "2030-12-31"  # wide enough to cover the whole synthetic fixture

# (constructor, expected factor columns)
FACTOR_CASES = [
    (compute_ch3_factors, ("MKT", "SMB", "VMG")),
    (compute_ch4_factors, ("MKT", "SMB", "VMG", "PMO")),
]
CASE_IDS = [
    "ch3",
    "ch4",
]


@pytest.mark.parametrize("constructor,columns", FACTOR_CASES, ids=CASE_IDS)
def test_factor_has_one_row_per_date_in_range(synthetic_source, constructor, columns):
    frame = constructor(synthetic_source, START, END)
    expected_dates = pd.DatetimeIndex(
        synthetic_source.get_returns(START, END)[DATE_COL].unique(), name=DATE_COL
    )
    assert frame.index.equals(expected_dates)
    assert frame.index.name == DATE_COL
    assert list(frame.columns) == list(columns)


@pytest.mark.parametrize("constructor,columns", FACTOR_CASES, ids=CASE_IDS)
def test_factor_has_one_row_per_date_in_subrange(synthetic_source, constructor, columns):
    all_dates = pd.DatetimeIndex(
        synthetic_source.get_returns(START, END)[DATE_COL].unique(), name=DATE_COL
    )
    end = all_dates[len(all_dates) // 2]
    frame = constructor(synthetic_source, START, end)
    expected_dates = pd.DatetimeIndex(
        synthetic_source.get_returns(START, end)[DATE_COL].unique(), name=DATE_COL
    )
    assert frame.index.equals(expected_dates)


@pytest.mark.parametrize("constructor,columns", FACTOR_CASES, ids=CASE_IDS)
def test_factor_has_no_lookahead(synthetic_source, constructor, columns):
    """A factor value at date ``t`` must not depend on data after ``t``.

    Strong form: reconstruct every factor using only data up to a midpoint and
    require it to equal the full-sample factor on that same sub-period, row for
    row (NaN included).
    """
    full = constructor(synthetic_source, START, END)
    split = full.index[len(full.index) // 2]
    truncated = constructor(synthetic_source, START, split)

    common = truncated.index
    pd.testing.assert_frame_equal(full.loc[common], truncated)


@pytest.mark.parametrize("constructor,columns", FACTOR_CASES, ids=CASE_IDS)
def test_factor_construction_is_deterministic(synthetic_source, constructor, columns):
    first = constructor(synthetic_source, START, END)
    second = constructor(synthetic_source, START, END)
    pd.testing.assert_frame_equal(first, second)


def test_ch3_size_sort_excludes_bottom_30pct_by_market_cap(synthetic_source):
    """CH-3's defining feature: the smallest 30% of stocks (shell stocks) must
    never appear in the ``small`` leg of the size sort, on any date.
    """
    assert DEFAULT_SETTINGS.bottom_mcap_exclude_pct == pytest.approx(0.30)

    panel = _load_panel(synthetic_source, START, END, fields=["book_value"])
    _add_ch3_size_groups(panel, DEFAULT_SETTINGS.bottom_mcap_exclude_pct)

    checked_dates = 0
    for _, cross_section in panel.groupby(DATE_COL):
        stocks = cross_section.dropna(subset=[MARKET_CAP_COL + "_lag"])
        if stocks.empty:
            continue  # first date has no lagged market cap for anyone
        cutoff = stocks[MARKET_CAP_COL + "_lag"].quantile(0.30)
        smallest = stocks[stocks[MARKET_CAP_COL + "_lag"] <= cutoff]

        assert not smallest["_in_scope"].any()
        assert not (smallest["size_grp"] == "small").any()
        # The excluded bottom is never even assigned a size leg.
        assert smallest["size_grp"].isna().all()
        checked_dates += 1

    assert checked_dates > 50


def test_ch3_size_sort_uses_remaining_median_breakpoint(synthetic_source):
    """After dropping the bottom 30%, each size leg should contain about half
    of the *remaining* stocks (not of the full cross-section)."""
    panel = _load_panel(synthetic_source, START, END, fields=["book_value"])
    _add_ch3_size_groups(panel, DEFAULT_SETTINGS.bottom_mcap_exclude_pct)

    mid_date = sorted(panel[DATE_COL].unique())[40]
    cross_section = panel[panel[DATE_COL] == mid_date].dropna(
        subset=[MARKET_CAP_COL + "_lag"]
    )
    in_scope = cross_section[cross_section["_in_scope"]]
    n_small = int((in_scope["size_grp"] == "small").sum())
    n_big = int((in_scope["size_grp"] == "big").sum())
    assert n_small + n_big == len(in_scope)
    assert abs(n_small - n_big) <= 1
    # Bottom 30% of the full cross-section is gone.
    assert n_small + n_big < len(cross_section)
