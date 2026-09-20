"""Tests for the Phase 5B PIT-native CH3/CH4 construction (P5B-3).

Everything here is offline and deterministic. A small hand-specified
:class:`PITDataSource` supplies daily returns, market cap, trading status,
fundamentals, and the B2 ``vol``/``total_share`` extras, so the frozen
Monthly Formation Contract, the shell-screen/tradability split, and the
20/250-trading-day abnormal-turnover ratio can be asserted against
hand-computed values rather than re-derived from the code under test.

Covered:

* the additive ``lag_col``/``scope_col`` parameterization of
  ``capm._add_mcap_scope`` / ``ch3._add_ch3_size_groups`` (legacy defaults
  unchanged);
* the Monthly Formation Contract: formation date = last trading day of the
  month, characteristics and weights fixed for the following month, no
  monthly-return compounding, partial-first-month exclusion;
* the shell screen as the sole size-based cut, and ``MKT`` built from the
  shell-screened top-70% universe only;
* the per-``F_m`` ``latest_known_value`` resolution (no global-``end``
  leakage);
* tradable-on-formation-date membership and single-day suspension handling;
* the two diagnostics exclusion layers kept distinct;
* the CH4 abnormal-turnover ratio and its fail-closed 250-observation rule.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from smart_beta.benchmarks.capm import (
    _LAG_COL,
    _SCOPE_COL,
    _add_mcap_scope,
    _market_factor,
    _spread,
    _two_by_three,
    _value_weighted_by,
)
from smart_beta.benchmarks.ch3 import _add_ch3_size_groups
from smart_beta.benchmarks.ch3_pit import (
    _CH_SCOPE_COL,
    _CH_WEIGHT_COL,
    _EP_COL,
    _load_ch_pit_panel,
    compute_ch3_factors_pit,
)
from smart_beta.benchmarks.ch4_pit import compute_ch4_factors_pit
from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.schema import DATE_COL, STOCK_COL
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.schema import (
    ACTION_TYPE_COL,
    ADJUSTMENT_FACTOR_COL,
    DELIST_DATE_COL,
    EFFECTIVE_DATE_COL,
    FIELD_COL,
    FLOAT_MARKET_CAP_COL,
    IS_RESTATEMENT_COL,
    IS_SUPERSEDED_COL,
    KNOWLEDGE_DATE_COL,
    LIST_DATE_COL,
    RAW_RETURN_COL,
    REPORT_PERIOD_END_COL,
    TOTAL_MARKET_CAP_COL,
    VALUE_COL,
)
from smart_beta.pit.source import PITDataSource
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.inputs import (
    RAW_TRADING_VOLUME_COL,
    SHARES_OUTSTANDING_COL,
)
from smart_beta.research_inputs.risk_free import ConstantRiskFreeProvider
from smart_beta.research_inputs.tradability import (
    TRADABLE_COL,
    ChinaAShareTradabilityPolicy,
)

# ---------------------------------------------------------------------------
# Frozen test universe / deterministic values
# ---------------------------------------------------------------------------
_DAILY_START = "2018-01-01"
_DAILY_END = "2020-12-31"
_STOCKS = tuple(f"S{i}" for i in range(10))
_MCAP = {f"S{i}": 100.0 * (i + 1) for i in range(10)}
_RETURN = {f"S{i}": 0.01 * (i + 1) for i in range(10)}
# In-scope E/P targets (S3..S9 are the top-70% universe); out-of-scope names
# get an arbitrary but fixed E/P.
_EP = {
    "S0": 0.05, "S1": 0.05, "S2": 0.05,
    "S3": 0.10, "S4": 0.08, "S5": 0.06, "S6": 0.04,
    "S7": 0.12, "S8": 0.02, "S9": 0.14,
}
_NI = {stock: _EP[stock] * _MCAP[stock] for stock in _STOCKS}
_REPORT_PERIOD_END = pd.Timestamp("2019-06-30")
_KNOWLEDGE_DATE = pd.Timestamp("2019-07-30")
_FACTOR_START = "2019-04-01"
_FACTOR_END = "2020-06-30"

# Hand-computed expectations (constant returns/mcap, zero risk-free rate):
# the bottom-30% shell cutoff over mcap 100..1000 is 370, so the CH factor
# universe is S3..S9.
_EXPECTED_MKT = 371.0 / 4900.0
_EXPECTED_GROSS_FULL_UNIVERSE = 385.0 / 5500.0


def _trading_dates(start: str = _DAILY_START, end: str = _DAILY_END) -> pd.DatetimeIndex:
    return pd.bdate_range(start, end)


# ---------------------------------------------------------------------------
# Deterministic PIT source
# ---------------------------------------------------------------------------
class _DailyChPitSource(PITDataSource):
    """A small hand-specified daily PIT source for the CH3/CH4 construction."""

    def __init__(self, frames: dict[str, pd.DataFrame], calendar: TradingCalendar):
        self._frames = frames
        self._calendar = calendar

    def trading_calendar(self) -> TradingCalendar:
        return self._calendar

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        return self._slice(self._frames["returns"], DATE_COL, start, end)

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return self._frames["corporate_actions"].copy()

    def get_market_cap(self, start, end) -> pd.DataFrame:
        return self._slice(self._frames["market_cap"], DATE_COL, start, end)

    def get_fundamentals(self, start, end, fields):
        frame = self._slice(self._frames["fundamentals"], REPORT_PERIOD_END_COL, start, end)
        return frame.loc[frame[FIELD_COL].isin(list(fields))].reset_index(drop=True)

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._slice(self._frames["status"], DATE_COL, start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._frames["listing"].copy()

    @staticmethod
    def _slice(frame: pd.DataFrame, column: str, start, end) -> pd.DataFrame:
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        mask = (frame[column] >= start_ts) & (frame[column] <= end_ts)
        return frame.loc[mask].reset_index(drop=True).copy()


def _build_source(
    *,
    dates: pd.DatetimeIndex | None = None,
    stocks=_STOCKS,
    returns: dict[str, float] | None = None,
    mcap: dict[str, float] | None = None,
    fundamentals: list[tuple[str, pd.Timestamp, pd.Timestamp, float]] | None = None,
    vol_fn=None,
    shares: dict[str, float] | None = None,
    status_overrides: dict[tuple[str, pd.Timestamp], dict[str, bool]] | None = None,
    list_dates: dict[str, pd.Timestamp] | None = None,
) -> _DailyChPitSource:
    dates = _trading_dates() if dates is None else dates
    returns = dict(_RETURN if returns is None else returns)
    mcap = dict(_MCAP if mcap is None else mcap)
    shares = {stock: 1.0 for stock in stocks} if shares is None else dict(shares)
    status_overrides = status_overrides or {}
    list_dates = list_dates or {}

    return_rows: list[tuple] = []
    mcap_rows: list[tuple] = []
    status_rows: list[tuple] = []
    for stock in stocks:
        for day in dates:
            return_rows.append((day, stock, returns[stock], 1000.0))
            mcap_rows.append(
                (day, stock, mcap[stock] * 0.5, mcap[stock], shares[stock])
            )
            flags = status_overrides.get((stock, day), {})
            status_rows.append(
                (
                    day,
                    stock,
                    bool(flags.get("is_suspended", False)),
                    bool(flags.get("is_limit_up", False)),
                    bool(flags.get("is_limit_down", False)),
                    bool(flags.get("is_st", False)),
                    False,
                )
            )

    returns_frame = pd.DataFrame(
        return_rows, columns=[DATE_COL, STOCK_COL, RAW_RETURN_COL, RAW_TRADING_VOLUME_COL]
    )
    returns_frame[STOCK_COL] = returns_frame[STOCK_COL].astype("string")
    returns_frame[DATE_COL] = pd.to_datetime(returns_frame[DATE_COL])
    returns_frame[RAW_RETURN_COL] = returns_frame[RAW_RETURN_COL].astype("float64")
    returns_frame[RAW_TRADING_VOLUME_COL] = returns_frame[
        RAW_TRADING_VOLUME_COL
    ].astype("float64")

    mcap_frame = pd.DataFrame(
        mcap_rows,
        columns=[
            DATE_COL,
            STOCK_COL,
            FLOAT_MARKET_CAP_COL,
            TOTAL_MARKET_CAP_COL,
            SHARES_OUTSTANDING_COL,
        ],
    )
    mcap_frame[STOCK_COL] = mcap_frame[STOCK_COL].astype("string")
    mcap_frame[DATE_COL] = pd.to_datetime(mcap_frame[DATE_COL])
    for column in (FLOAT_MARKET_CAP_COL, TOTAL_MARKET_CAP_COL, SHARES_OUTSTANDING_COL):
        mcap_frame[column] = mcap_frame[column].astype("float64")

    # Optional per-(stock, date) turnover step: ``vol_fn(stock, day)`` in
    # native volume units; shares are 1.0 by default, so vol == turnover.
    if vol_fn is not None:
        returns_frame[RAW_TRADING_VOLUME_COL] = [
            float(vol_fn(stock, day))
            for stock, day in zip(
                returns_frame[STOCK_COL], returns_frame[DATE_COL]
            )
        ]

    status_frame = pd.DataFrame(
        status_rows,
        columns=[
            DATE_COL,
            STOCK_COL,
            "is_suspended",
            "is_limit_up",
            "is_limit_down",
            "is_st",
            "is_zero_volume",
        ],
    )
    status_frame[STOCK_COL] = status_frame[STOCK_COL].astype("string")
    status_frame[DATE_COL] = pd.to_datetime(status_frame[DATE_COL])
    for column in ("is_suspended", "is_limit_up", "is_limit_down", "is_st", "is_zero_volume"):
        status_frame[column] = status_frame[column].astype(bool)

    if fundamentals is None:
        fundamentals = [
            (stock, _REPORT_PERIOD_END, _KNOWLEDGE_DATE, _NI[stock])
            for stock in stocks
        ]
    fundamentals_frame = pd.DataFrame(
        fundamentals,
        columns=[STOCK_COL, REPORT_PERIOD_END_COL, KNOWLEDGE_DATE_COL, VALUE_COL],
    )
    fundamentals_frame[STOCK_COL] = fundamentals_frame[STOCK_COL].astype("string")
    fundamentals_frame[FIELD_COL] = "ni_ex_nonrecurring"
    fundamentals_frame[REPORT_PERIOD_END_COL] = pd.to_datetime(
        fundamentals_frame[REPORT_PERIOD_END_COL]
    )
    fundamentals_frame[KNOWLEDGE_DATE_COL] = pd.to_datetime(
        fundamentals_frame[KNOWLEDGE_DATE_COL]
    )
    fundamentals_frame[VALUE_COL] = fundamentals_frame[VALUE_COL].astype("float64")
    fundamentals_frame[IS_RESTATEMENT_COL] = False
    fundamentals_frame = fundamentals_frame[
        [
            STOCK_COL,
            REPORT_PERIOD_END_COL,
            FIELD_COL,
            KNOWLEDGE_DATE_COL,
            VALUE_COL,
            IS_RESTATEMENT_COL,
        ]
    ]

    listing_frame = pd.DataFrame(
        {
            STOCK_COL: pd.Series(list(stocks), dtype="string"),
            LIST_DATE_COL: pd.to_datetime(
                [list_dates.get(stock, pd.Timestamp("2010-01-01")) for stock in stocks]
            ),
            DELIST_DATE_COL: pd.NaT,
        }
    )

    corporate_actions = pd.DataFrame(
        {
            STOCK_COL: pd.Series(dtype="string"),
            EFFECTIVE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
            ACTION_TYPE_COL: pd.Series(dtype="string"),
            KNOWLEDGE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
            ADJUSTMENT_FACTOR_COL: pd.Series(dtype="float64"),
            IS_SUPERSEDED_COL: pd.Series(dtype="bool"),
        }
    )

    frames = {
        "returns": returns_frame.sort_values([DATE_COL, STOCK_COL]).reset_index(drop=True),
        "market_cap": mcap_frame.sort_values([DATE_COL, STOCK_COL]).reset_index(drop=True),
        "status": status_frame.sort_values([DATE_COL, STOCK_COL]).reset_index(drop=True),
        "fundamentals": fundamentals_frame.reset_index(drop=True),
        "listing": listing_frame,
        "corporate_actions": corporate_actions,
    }
    return _DailyChPitSource(frames, TradingCalendar(dates))


def _view(source: _DailyChPitSource) -> PointInTimeView:
    return PointInTimeView(source)


def _risk_free(source: _DailyChPitSource) -> ConstantRiskFreeProvider:
    return ConstantRiskFreeProvider(0.0, source.trading_calendar())


def _ch_settings(**overrides) -> Settings:
    base = replace(DEFAULT_SETTINGS, min_listing_age_months=0)
    return replace(base, **overrides)


# ---------------------------------------------------------------------------
# Additive parameterization (no PIT fixture needed)
# ---------------------------------------------------------------------------
def test_add_mcap_scope_custom_columns_and_legacy_default_unchanged():
    date = pd.Timestamp("2020-01-31")
    frame = pd.DataFrame(
        {
            DATE_COL: [date] * 5,
            STOCK_COL: list("abcde"),
            _LAG_COL: [1.0, 2.0, 3.0, 4.0, 5.0],
            "pit_mcap_lag": [10.0, 20.0, 30.0, 40.0, 50.0],
        }
    )
    _add_mcap_scope(frame, 0.4, lag_col="pit_mcap_lag", scope_col="_pit_scope")

    assert "_pit_scope" in frame.columns
    assert _SCOPE_COL not in frame.columns
    # 0.4 quantile of [10..50] is 26; only 30/40/50 are above it.
    assert frame["_pit_scope"].tolist() == [False, False, True, True, True]

    legacy = frame.loc[:, [DATE_COL, STOCK_COL, _LAG_COL]].copy()
    _add_mcap_scope(legacy, 0.4)
    assert legacy[_SCOPE_COL].tolist() == [False, False, True, True, True]


def test_add_ch3_size_groups_custom_columns_and_legacy_default_unchanged():
    date = pd.Timestamp("2020-01-31")
    frame = pd.DataFrame(
        {
            DATE_COL: [date] * 6,
            STOCK_COL: list("abcdef"),
            _LAG_COL: [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "pit_mcap_lag": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        }
    )
    _add_ch3_size_groups(
        frame,
        0.34,
        lag_col="pit_mcap_lag",
        scope_col="_pit_scope",
    )
    assert "_pit_scope" in frame.columns
    assert "size_grp" in frame.columns
    # The smallest two are out of scope and never assigned a size leg.
    assert frame["_pit_scope"].tolist() == [False, False, True, True, True, True]
    assert frame.loc[~frame["_pit_scope"], "size_grp"].isna().all()

    legacy = frame.loc[
        :, [DATE_COL, STOCK_COL, _LAG_COL]
    ].copy()
    _add_ch3_size_groups(legacy, 0.34)
    assert legacy[_SCOPE_COL].tolist() == [False, False, True, True, True, True]


# ---------------------------------------------------------------------------
# Monthly Formation Contract / construction
# ---------------------------------------------------------------------------
def _load(source=None, *, settings=None, include_turnover=False, start=_FACTOR_START, end=_FACTOR_END):
    source = _build_source() if source is None else source
    return _load_ch_pit_panel(
        _view(source),
        start,
        end,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=_risk_free(source),
        settings=_ch_settings() if settings is None else settings,
        include_turnover=include_turnover,
    )


def test_ch3_pit_output_schema_and_partial_first_month():
    frame = compute_ch3_factors_pit(
        _view(_build_source()),
        _FACTOR_START,
        _FACTOR_END,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=_risk_free(_build_source()),
        settings=_ch_settings(),
    )
    assert list(frame.columns) == ["MKT", "SMB", "VMG"]
    assert frame.index.name == DATE_COL
    expected_dates = pd.bdate_range(_FACTOR_START, _FACTOR_END)
    assert frame.index.equals(pd.DatetimeIndex(expected_dates, name=DATE_COL))

    # The partial first month (April 2019) has no formation date inside the
    # window ahead of it, so it is excluded (all-NaN), never synthesized.
    first_month = frame.loc["2019-04"]
    assert first_month.isna().all().all()


def test_ch3_pit_mkt_uses_shell_screened_universe():
    source = _build_source()
    frame = compute_ch3_factors_pit(
        _view(source),
        _FACTOR_START,
        _FACTOR_END,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=_risk_free(source),
        settings=_ch_settings(),
    )
    finite = frame["MKT"].dropna()
    assert not finite.empty
    assert np.allclose(finite.to_numpy(), _EXPECTED_MKT)
    # And it is provably not the (wrong) full eligible-universe market return.
    assert not np.isclose(finite.iloc[0], _EXPECTED_GROSS_FULL_UNIVERSE)


def test_ch3_pit_hand_computed_smb_and_vmg():
    source = _build_source()
    frame = compute_ch3_factors_pit(
        _view(source),
        _FACTOR_START,
        _FACTOR_END,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=_risk_free(source),
        settings=_ch_settings(),
    )
    # From the August 2019 holding month onward the 2019-06-30 report is
    # visible as of the preceding formation date, so SMB/VMG are finite.
    assert frame.loc["2019-07", ["SMB", "VMG"]].isna().all().all()
    august = frame.loc["2019-08"]
    # Hand-computed from the 2x3 value-weighted cells (see module docstring):
    # SMB = mean(small low, small neutral) - mean(big low, big high);
    # VMG = big high - mean(small low, big low).
    assert np.allclose(august["SMB"].to_numpy(), -0.03508547008547010)
    assert np.allclose(august["VMG"].to_numpy(), 0.01341880341880340)


def test_ch3_pit_formation_characteristics_fixed_across_holding_month():
    """The value weight is the formation-date market cap, held fixed for
    every trading day of the following month -- and the following month only."""
    source = _build_source()
    panel = _load(source).panel

    # Every holding day of August 2019 carries the 2019-07-31 formation value.
    august = panel.loc[panel[DATE_COL].dt.strftime("%Y-%m") == "2019-08"]
    assert not august.empty
    per_stock = august.groupby(STOCK_COL)[_CH_WEIGHT_COL].nunique(dropna=True)
    assert (per_stock <= 1).all()
    expected = _MCAP["S9"]
    assert (
        august.loc[august[STOCK_COL] == "S9", _CH_WEIGHT_COL].iloc[0]
        == pytest.approx(expected)
    )
    # The formation date itself is a July trading day, not an August day.
    assert pd.Timestamp("2019-07-31") not in set(august[DATE_COL])


def test_ch3_pit_resolves_fundamentals_once_per_formation_date():
    """A revised report becomes visible only at the first formation date on
    or after its knowledge_date -- never earlier, and never globally at the
    window end."""
    newer_ni = {stock: _NI[stock] * 2.0 for stock in _STOCKS}
    facts = [
        (stock, _REPORT_PERIOD_END, _KNOWLEDGE_DATE, _NI[stock])
        for stock in _STOCKS
    ] + [
        (stock, pd.Timestamp("2019-09-30"), pd.Timestamp("2019-10-30"), newer_ni[stock])
        for stock in _STOCKS
    ]
    source = _build_source(fundamentals=facts)
    panel = _load(source).panel

    # August 2019 holding month uses the original report.
    august = panel.loc[
        (panel[DATE_COL].dt.strftime("%Y-%m") == "2019-08")
        & (panel[STOCK_COL] == "S9")
    ]
    assert august[_EP_COL].iloc[0] == pytest.approx(_NI["S9"] / _MCAP["S9"])

    # October 2019's holding month (November) is formed on 2019-10-31, after
    # the 2019-10-30 knowledge date, so it uses the revised value.
    november = panel.loc[
        (panel[DATE_COL].dt.strftime("%Y-%m") == "2019-11")
        & (panel[STOCK_COL] == "S9")
    ]
    assert november[_EP_COL].iloc[0] == pytest.approx(
        2.0 * _NI["S9"] / _MCAP["S9"]
    )


def test_ch3_pit_backward_knowledge_never_leaks():
    """A report whose knowledge_date is after a formation date must not
    enter that formation's characteristic, even though its
    report_period_end is earlier."""
    # Report exists with report_period_end 2019-03-31 but knowledge 2019-05-15:
    # it must NOT be visible at the 2019-04-30 formation.
    facts = [
        (stock, pd.Timestamp("2019-03-31"), pd.Timestamp("2019-05-15"), _NI[stock])
        for stock in _STOCKS
    ]
    source = _build_source(fundamentals=facts)
    panel = _load(source).panel
    may = panel.loc[panel[DATE_COL].dt.strftime("%Y-%m") == "2019-05"]
    assert may[_EP_COL].isna().all()


def test_ch3_pit_tradable_on_formation_date_required_for_whole_month():
    """Suspending a stock on the formation date excludes it for the whole
    holding month even though it is tradable again on the holding days."""
    formation_date = pd.Timestamp("2019-07-31")
    source = _build_source(
        status_overrides={("S9", formation_date): {"is_suspended": True}}
    )
    panel = _load(source).panel
    august = panel.loc[panel[DATE_COL].dt.strftime("%Y-%m") == "2019-08"]
    s9 = august.loc[august[STOCK_COL] == "S9"]
    assert not s9.empty
    # S9 is tradable on every August holding day...
    assert s9[TRADABLE_COL].all()
    # ...but was not in the formation universe, so it carries no scope and
    # is excluded from every factor cross-section for the month.
    assert s9[_CH_SCOPE_COL].fillna(False).eq(False).all()
    assert s9["size_grp"].isna().all()
    assert s9["value_grp"].isna().all()

    frame = compute_ch3_factors_pit(
        _view(source),
        _FACTOR_START,
        _FACTOR_END,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=_risk_free(source),
        settings=_ch_settings(),
    )
    assert np.allclose(
        frame.loc["2019-08", "MKT"].dropna().to_numpy(), 271.0 / 3900.0
    )


def test_ch3_pit_mid_month_suspension_removes_only_that_day():
    """A single holding-day suspension removes exactly that day's
    contribution and no other. Returns/mcap are constant, so the affected
    day's MKT is the hand-computed subset value while adjacent days are
    unchanged."""
    suspension_date = pd.Timestamp("2019-08-15")
    source = _build_source(
        status_overrides={("S9", suspension_date): {"is_suspended": True}}
    )
    panel = _load(source).panel
    suspended = panel.loc[
        (panel[DATE_COL] == suspension_date) & (panel[STOCK_COL] == "S9")
    ]
    assert not suspended.empty
    assert not bool(suspended[TRADABLE_COL].iloc[0])
    # The stock keeps its formation-fixed weight/labels for the month.
    assert suspended[_CH_WEIGHT_COL].iloc[0] == pytest.approx(_MCAP["S9"])
    assert suspended[_CH_SCOPE_COL].iloc[0]

    frame = compute_ch3_factors_pit(
        _view(source),
        _FACTOR_START,
        _FACTOR_END,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=_risk_free(source),
        settings=_ch_settings(),
    )
    assert frame.loc[suspension_date, "MKT"] == pytest.approx(271.0 / 3900.0)
    assert frame.loc[pd.Timestamp("2019-08-14"), "MKT"] == pytest.approx(_EXPECTED_MKT)
    assert frame.loc[pd.Timestamp("2019-08-16"), "MKT"] == pytest.approx(_EXPECTED_MKT)


def test_ch3_pit_matches_manual_panel_reconstruction():
    """Public function == panel + unmodified value-weighting helpers."""
    source = _build_source()
    result = _load(source)
    panel = result.panel
    universe = panel.loc[
        panel[TRADABLE_COL].fillna(False).astype(bool)
        & panel[_CH_SCOPE_COL].fillna(False).astype(bool)
    ]
    vw = _two_by_three(
        universe, "value_grp", ret_col="adj_ret", weight_col=_CH_WEIGHT_COL
    )
    expected = pd.DataFrame(
        {
            "MKT": _market_factor(
                universe, ret_col="adj_ret", weight_col=_CH_WEIGHT_COL
            ),
            "SMB": _spread(vw, "size_grp", "small", "big"),
            "VMG": _spread(vw, "value_grp", "high", "low"),
        }
    ).reindex(result.trading_dates)
    expected.index.name = DATE_COL
    actual = compute_ch3_factors_pit(
        _view(source),
        _FACTOR_START,
        _FACTOR_END,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=_risk_free(source),
        settings=_ch_settings(),
    )
    pd.testing.assert_frame_equal(actual, expected)


def test_ch3_pit_diagnostics_separate_the_two_exclusion_layers():
    source = _build_source()
    diagnostics = _load(source).diagnostics
    july = diagnostics.loc[diagnostics[DATE_COL] == pd.Timestamp("2019-07-31")]
    reasons = dict(zip(july[STOCK_COL], july["exclusion_reason"]))

    # Bottom-30% names are shell-screen exclusions, not tradability ones.
    assert reasons["S0"] == "shell_screen:bottom_30pct"
    assert reasons["S1"] == "shell_screen:bottom_30pct"
    assert reasons["S2"] == "shell_screen:bottom_30pct"
    # In-scope names are clean.
    assert pd.isna(reasons["S3"])
    assert pd.isna(reasons["S9"])


def test_ch3_pit_diagnostics_name_tradability_subreason():
    formation_date = pd.Timestamp("2019-07-31")
    source = _build_source(
        status_overrides={("S9", formation_date): {"is_suspended": True}}
    )
    diagnostics = _load(source).diagnostics
    row = diagnostics.loc[
        (diagnostics[DATE_COL] == formation_date)
        & (diagnostics[STOCK_COL] == "S9")
    ]
    assert row["exclusion_reason"].iloc[0] == "tradability:suspended"
    # S9 is not also labelled as a shell-screen exclusion.
    assert row["exclusion_reason"].iloc[0] != "shell_screen:bottom_30pct"


def test_ch3_pit_tradability_cut_is_not_double_applied():
    """The shell screen is the sole market-cap cut: the same settings object
    drives the shell screen, while tradability is called with the size cut
    disabled internally. If both applied the 30% cut, the universe would be
    the top 49% (0.7*0.7) and the count/hand-value would differ."""
    source = _build_source()
    diagnostics = _load(source).diagnostics
    july = diagnostics.loc[diagnostics[DATE_COL] == pd.Timestamp("2019-07-31")]
    # Exactly the bottom three names are shell-excluded; seven are in scope.
    assert (july["exclusion_reason"] == "shell_screen:bottom_30pct").sum() == 3
    assert july[_CH_SCOPE_COL].fillna(False).sum() == 7


# ---------------------------------------------------------------------------
# CH4 abnormal turnover
# ---------------------------------------------------------------------------
def test_ch4_pit_output_schema_and_pmo_present():
    source = _build_source()
    frame = compute_ch4_factors_pit(
        _view(source),
        _FACTOR_START,
        _FACTOR_END,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=_risk_free(source),
        settings=_ch_settings(),
    )
    assert list(frame.columns) == ["MKT", "SMB", "VMG", "PMO"]
    # Constant turnover -> every stock's ratio is 1.0, but PMO is still a
    # genuine cross-leg spread (legs hold different stocks), and finite from
    # the first full-history formation onward.
    assert frame["PMO"].notna().any()


def test_ch4_pit_abnormal_turnover_ratio_hand_computed():
    """vol == turnover (shares == 1); a step from 100 to 200 at a fixed date
    makes the 20/250 ratio at the 2019-10-31 formation exactly computable."""
    step_date = pd.Timestamp("2019-10-01")
    formation_date = pd.Timestamp("2019-10-31")

    def vol_fn(stock, day):
        if stock == "S3" and pd.Timestamp(day) >= step_date:
            return 200.0
        return 100.0

    source = _build_source(vol_fn=vol_fn)
    calendar = source.trading_calendar()
    all_dates = calendar.dates
    position = all_dates.get_loc(formation_date)
    long_dates = all_dates[position - 250 + 1 : position + 1]
    short_dates = all_dates[position - 20 + 1 : position + 1]
    assert len(long_dates) == 250 and len(short_dates) == 20

    long_values = [200.0 if d >= step_date else 100.0 for d in long_dates]
    short_values = [200.0 if d >= step_date else 100.0 for d in short_dates]
    expected = (sum(short_values) / 20.0) / (sum(long_values) / 250.0)

    panel = _load(source, include_turnover=True).panel
    november = panel.loc[
        (panel[DATE_COL].dt.strftime("%Y-%m") == "2019-11")
        & (panel[STOCK_COL] == "S3")
    ]
    assert not november.empty
    assert november["abnormal_turnover"].iloc[0] == pytest.approx(expected)
    assert expected != pytest.approx(1.0)


def test_ch4_pit_fails_closed_without_full_long_window():
    """A short-history source gives a first formation without a full 250
    observations -> NaN, no partial-window average."""
    short_dates = _trading_dates("2019-01-01", _DAILY_END)
    source = _build_source(dates=short_dates)
    panel = _load(source, include_turnover=True, start=_FACTOR_START).panel
    # The first holding month (May 2019) is formed on 2019-04-30 with fewer
    # than 250 trading days of history.
    may = panel.loc[panel[DATE_COL].dt.strftime("%Y-%m") == "2019-05"]
    assert may["abnormal_turnover"].isna().all()


def test_ch4_pit_does_not_call_the_legacy_turnover_proxy():
    """Literal source guard: the retracted month-based placeholder must not
    be referenced by the PIT-native CH4 path."""
    from pathlib import Path

    import smart_beta.benchmarks.ch3_pit as ch3_pit_mod
    import smart_beta.benchmarks.ch4_pit as ch4_pit_mod

    for module in (ch3_pit_mod, ch4_pit_mod):
        source = Path(module.__file__).read_text(encoding="utf-8")
        # A real call/import would contain the call syntax; the module
        # docstring may still *name* the retracted placeholder in prose.
        assert "_add_turnover_proxy(" not in source
        assert "turnover_abnormal_window_months" not in source


def test_settings_new_turnover_fields_are_additive():
    assert DEFAULT_SETTINGS.ch4_turnover_short_window_days == 20
    assert DEFAULT_SETTINGS.ch4_turnover_long_window_days == 250
    # The legacy month-based window is untouched.
    assert DEFAULT_SETTINGS.turnover_abnormal_window_months == 6


def test_pit_modules_have_no_monthly_resampling_or_align_import():
    """The frozen contract keeps factor output daily: nothing resamples
    returns to monthly, and ``smart_beta.data.align`` is untouched by P5B-3."""
    import ast
    from pathlib import Path

    import smart_beta.benchmarks.ch3_pit as ch3_pit_mod
    import smart_beta.benchmarks.ch4_pit as ch4_pit_mod

    for module in (ch3_pit_mod, ch4_pit_mod):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "smart_beta.data.align"
            if isinstance(node, ast.ImportFrom):
                assert node.module != "smart_beta.data.align"
            if isinstance(node, ast.Attribute):
                assert node.attr != "resample"
