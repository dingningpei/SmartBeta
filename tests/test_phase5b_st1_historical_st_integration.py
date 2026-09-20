"""Phase 5B P5B-ST1 -- historical ST integration repair tests.

P5B-6 Stage 3 discovered that ``TushareAShareSource.get_trading_status()``
never supplied a historical ``name_by_date`` to P4DB-4's
:func:`~smart_beta.vendors.tushare.market_data.map_to_trading_status`, so
``is_st`` was structurally ``pd.NA`` and
:class:`~smart_beta.research_inputs.tradability.ChinaAShareTradabilityPolicy`
failed closed for every row. This task wires the already-supported
``bak_basic`` per-date name evidence into that assembler path.

Everything here is deterministic and offline:

* the real, proxy-observed ``bak_basic`` ``002450.SZ`` specimen
  (``*ST康得`` on 2021-04-13, ``康得新`` on 2018-04-13) proves the assembled
  source resolves the already-certified exact-date evidence;
* in-file synthetic ``daily`` / ``stk_limit`` / ``suspend_d`` / ``bak_basic``
  payloads prove point-in-time alignment, the fail-closed missing-evidence
  contract, and the downstream tradability integration.

No mapping rule is re-tested here; P4DB-4's mapper owns the ST semantics and
its own tests already cover them. These tests prove only that the *assembled*
source now feeds that mapper correct, point-in-time name evidence.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd
import pytest

from smart_beta.config.settings import Settings
from smart_beta.data.schema import DATE_COL, STOCK_COL
from smart_beta.research_inputs.tradability import (
    TRADABLE_COL,
    ChinaAShareTradabilityPolicy,
)
from smart_beta.vendors.tushare.source import TushareAShareSource

_FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "tushare"
    / "market_data"
)
_ST_TRUE = "bak_basic_trade_date-20210413_ts_code-002450.SZ.json"
_ST_FALSE = "bak_basic_trade_date-20180413_ts_code-002450.SZ.json"

_KANGDE = "002450.SZ"
#: A tradable non-ST window and a window whose middle date is a real ST date.
_TRANSITION_START, _TRANSITION_END = "2021-04-12", "2021-04-20"
_TRADABLE_START, _TRADABLE_END = "2021-04-12", "2021-04-16"


# ---------------------------------------------------------------------------
# Deterministic offline client
# ---------------------------------------------------------------------------
def _payload(rows: Sequence[Mapping], fields: Sequence[str]) -> dict:
    return {
        "fields": list(fields),
        "items": [[row.get(field) for field in fields] for row in rows],
    }


class _SyntheticTushareClient:
    """A deterministic, network-free ``TushareClient``.

    ``daily`` is ``{ts_code: {trade_date: close}}``; ``limits`` is
    ``{ts_code: {trade_date: (up_limit, down_limit)}}``; ``names`` is
    ``{ts_code: {trade_date: name}}`` (a missing key means the vendor has no
    ``bak_basic`` row for that date). ``suspend_d`` is unused in these tests.
    """

    def __init__(
        self,
        *,
        daily: Mapping[str, Mapping[str, float]] | None = None,
        limits: Mapping[str, Mapping[str, tuple[float, float]]] | None = None,
        names: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self._daily = dict(daily or {})
        self._limits = dict(limits or {})
        self._names = dict(names or {})
        self.calls: list[tuple[str, dict]] = []

    def fetch(
        self, api_name: str, *, retry_on_empty: bool = False, **params: str
    ) -> dict:
        self.calls.append((api_name, dict(params)))
        ts_code = params.get("ts_code", "")

        if api_name == "daily":
            rows = [
                {"ts_code": ts_code, "trade_date": day, "close": close}
                for day, close in sorted(self._daily.get(ts_code, {}).items())
            ]
            return _payload(rows, ["ts_code", "trade_date", "close"])
        if api_name == "stk_limit":
            rows = [
                {
                    "ts_code": ts_code,
                    "trade_date": day,
                    "up_limit": up,
                    "down_limit": down,
                }
                for day, (up, down) in sorted(self._limits.get(ts_code, {}).items())
            ]
            return _payload(
                rows, ["ts_code", "trade_date", "up_limit", "down_limit"]
            )
        if api_name == "suspend_d":
            return _payload([], ["ts_code", "trade_date", "suspend_type"])
        if api_name == "bak_basic":
            day = params.get("trade_date", "")
            name = self._names.get(ts_code, {}).get(day)
            rows = (
                []
                if name is None
                else [{"ts_code": ts_code, "trade_date": day, "name": name}]
            )
            return _payload(rows, ["ts_code", "trade_date", "name"])
        raise AssertionError(f"unexpected fetch to api_name={api_name!r}")


class _WrongRowDateClient(_SyntheticTushareClient):
    """``bak_basic`` answers every request with a row dated 2021-04-14.

    Proves the assembler keys the name by the row's *own* ``trade_date``, so a
    mislabeled/later vendor row can never be copied back onto an earlier
    requested date.
    """

    def fetch(
        self, api_name: str, *, retry_on_empty: bool = False, **params: str
    ) -> dict:
        if api_name == "bak_basic":
            self.calls.append((api_name, dict(params)))
            return _payload(
                [
                    {
                        "ts_code": params.get("ts_code", ""),
                        "trade_date": "20210414",
                        "name": "*ST康得",
                    }
                ],
                ["ts_code", "trade_date", "name"],
            )
        return super().fetch(api_name, retry_on_empty=retry_on_empty, **params)


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject a deterministic "
            "client instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read_bak_basic(name: str) -> dict:
    body = json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))
    data = body["data"]
    fields = list(data["fields"])
    return dict(zip(fields, data["items"][0]))


def _flat_daily(ts_code: str, days: Sequence[str], close: float = 10.0):
    return {ts_code: {day: close for day in days}}


def _flat_limits(ts_code: str, days: Sequence[str], up=11.0, down=9.0):
    return {ts_code: {day: (up, down) for day in days}}


def _status_by_date(frame: pd.DataFrame) -> "pd.Series":
    return frame.set_index(DATE_COL)["is_st"].sort_index()


def _make_policy_inputs(
    frame: pd.DataFrame, ts_code: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build ``(keys, listing_info, market_cap)`` for the assembled frame."""
    keys = frame[[DATE_COL, STOCK_COL]].copy()
    listing_info = pd.DataFrame(
        {
            STOCK_COL: [ts_code],
            "list_date": [pd.Timestamp("2010-07-16")],
            "delist_date": [pd.NaT],
        }
    )
    market_cap = keys.copy()
    market_cap["total_mcap"] = 1.0e9
    return keys, listing_info, market_cap


# ===========================================================================
# 1. Assembled source uses the already-certified real specimen (exact date)
# ===========================================================================
def test_assembled_source_resolves_real_bak_basic_specimen_by_exact_date() -> None:
    st_row = _read_bak_basic(_ST_TRUE)
    control_row = _read_bak_basic(_ST_FALSE)
    assert st_row["name"] == "*ST康得"
    assert control_row["name"] == "康得新"

    names = {
        _KANGDE: {
            "20210413": st_row["name"],
            "20180413": control_row["name"],
        }
    }

    # The 2021-04-13 ST date -> True; neighbouring dates have no specimen.
    source = TushareAShareSource([_KANGDE], _SyntheticTushareClient(names=names))
    st_status = _status_by_date(source.get_trading_status("2021-04-09", "2021-04-13"))
    assert bool(st_status.loc[pd.Timestamp("2021-04-13")]) is True
    assert st_status.loc[pd.Timestamp("2021-04-12")] is pd.NA
    assert st_status.loc[pd.Timestamp("2021-04-09")] is pd.NA

    # The 2018-04-13 control date -> False; neighbours are unknown.
    control = _status_by_date(
        TushareAShareSource([_KANGDE], _SyntheticTushareClient(names=names))
        .get_trading_status("2018-04-11", "2018-04-17")
    )
    assert bool(control.loc[pd.Timestamp("2018-04-13")]) is False
    assert control.loc[pd.Timestamp("2018-04-12")] is pd.NA


# ===========================================================================
# 2/6. Assembled-source PIT alignment across real ST prefix forms
# ===========================================================================
def test_assembled_source_st_transitions_and_prefix_forms_exact_date() -> None:
    """One window covers a normal name, entry into ST, the four
    ``is_st_name`` prefix forms, exit from ST, and a missing name.

    Expected ``is_st`` (calendar dates 2021-04-12..2021-04-20):
    12th 康得新 False | 13th *ST康得 True | 14th ST康得 True |
    15th SST前锋 True | 16th S*ST前锋 True | 19th 康得新 False |
    20th (no row) NA.
    """
    names = {
        _KANGDE: {
            "20210412": "康得新",
            "20210413": "*ST康得",
            "20210414": "ST康得",
            "20210415": "SST前锋",
            "20210416": "S*ST前锋",
            "20210419": "康得新",
        }
    }
    source = TushareAShareSource([_KANGDE], _SyntheticTushareClient(names=names))
    status = _status_by_date(
        source.get_trading_status(_TRANSITION_START, _TRANSITION_END)
    )

    expected = {
        "2021-04-12": False,
        "2021-04-13": True,
        "2021-04-14": True,
        "2021-04-15": True,
        "2021-04-16": True,
        "2021-04-19": False,
        "2021-04-20": pd.NA,
    }
    for day, want in expected.items():
        got = status.loc[pd.Timestamp(day)]
        if want is pd.NA:
            assert got is pd.NA, (day, got)
        else:
            assert bool(got) is want, (day, got)


def test_assembled_source_missing_evidence_stays_fail_closed_na() -> None:
    """No ``bak_basic`` rows at all => every ``is_st`` is ``pd.NA``.

    The structural-NA defect was a missing *input*; the mapper's own
    fail-closed contract is unchanged and must never surface ``False`` here.
    """
    source = TushareAShareSource([_KANGDE], _SyntheticTushareClient())
    status = source.get_trading_status(_TRADABLE_START, _TRADABLE_END)
    assert not status.empty
    assert status["is_st"].isna().all()
    assert (status["is_st"].dtype == "boolean")


def test_assembled_source_does_not_backfill_a_later_name_into_earlier_dates() -> None:
    """A row whose own ``trade_date`` is later must not classify an earlier
    request; the earlier dates stay unknown."""
    source = TushareAShareSource([_KANGDE], _WrongRowDateClient())
    status = _status_by_date(
        source.get_trading_status("2021-04-12", "2021-04-14")
    )
    assert status.loc[pd.Timestamp("2021-04-12")] is pd.NA
    assert status.loc[pd.Timestamp("2021-04-13")] is pd.NA
    assert bool(status.loc[pd.Timestamp("2021-04-14")]) is True


def test_get_trading_status_fetches_bak_basic_per_trade_date_not_stock_basic() -> None:
    """The name source is the per-date ``bak_basic`` endpoint, never
    ``stock_basic``'s current/latest snapshot."""
    client = _SyntheticTushareClient(names={_KANGDE: {"20210412": "康得新"}})
    source = TushareAShareSource([_KANGDE], client)
    source.get_trading_status("2021-04-12", "2021-04-13")

    fetched = {api_name for api_name, _ in client.calls}
    assert "bak_basic" in fetched
    assert "stock_basic" not in fetched
    bak_dates = [
        params["trade_date"]
        for api_name, params in client.calls
        if api_name == "bak_basic"
    ]
    assert sorted(bak_dates) == ["20210412", "20210413"]


# ===========================================================================
# 3. Downstream integration: source -> ChinaAShareTradabilityPolicy
# ===========================================================================
def _evaluate_policy(
    frame: pd.DataFrame, ts_code: str, settings: Settings
) -> pd.Series:
    keys, listing_info, market_cap = _make_policy_inputs(frame, ts_code)
    decision = ChinaAShareTradabilityPolicy().evaluate(
        trading_status=frame,
        listing_info=listing_info,
        market_cap=market_cap,
        keys=keys,
        settings=settings,
    )
    return decision.set_index(DATE_COL)[TRADABLE_COL].sort_index()


def _window_days() -> list[str]:
    return ["20210412", "20210413", "20210414", "20210415", "20210416"]


def test_policy_is_no_longer_blocked_by_structural_is_st_na() -> None:
    """With valid historical ST evidence present, the policy can mark a
    non-ST, otherwise-eligible row tradable -- the defect's whole point."""
    days = _window_days()
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)
    client = _SyntheticTushareClient(
        daily=_flat_daily(_KANGDE, days),
        limits=_flat_limits(_KANGDE, days),
        names={_KANGDE: {day: "康得新" for day in days}},
    )
    frame = TushareAShareSource([_KANGDE], client).get_trading_status(
        _TRADABLE_START, _TRADABLE_END
    )
    tradable = _evaluate_policy(frame, _KANGDE, settings)
    assert tradable.all(), tradable.to_dict()


def test_policy_still_fails_closed_when_st_evidence_is_missing() -> None:
    """Same eligible row, but no ``bak_basic`` name: ``is_st`` is ``pd.NA``,
    so the policy must keep rejecting it -- never silently treat NA as
    ``False``."""
    days = _window_days()
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)
    client = _SyntheticTushareClient(
        daily=_flat_daily(_KANGDE, days),
        limits=_flat_limits(_KANGDE, days),
    )
    frame = TushareAShareSource([_KANGDE], client).get_trading_status(
        _TRADABLE_START, _TRADABLE_END
    )
    assert frame["is_st"].isna().all()
    tradable = _evaluate_policy(frame, _KANGDE, settings)
    assert not tradable.any(), tradable.to_dict()


def test_policy_rejects_dates_whose_historical_name_is_st() -> None:
    """A genuine historical ``*ST`` name makes the row not tradable."""
    days = _window_days()
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)
    client = _SyntheticTushareClient(
        daily=_flat_daily(_KANGDE, days),
        limits=_flat_limits(_KANGDE, days),
        names={_KANGDE: {day: "*ST康得" for day in days}},
    )
    frame = TushareAShareSource([_KANGDE], client).get_trading_status(
        _TRADABLE_START, _TRADABLE_END
    )
    assert frame["is_st"].all()
    tradable = _evaluate_policy(frame, _KANGDE, settings)
    assert not tradable.any(), tradable.to_dict()


def test_policy_transitions_with_the_historical_name_not_the_latest_name() -> None:
    """The policy follows the *historical* name per date: a stock enters ST
    mid-window, and only the ST dates are rejected."""
    days = _window_days()
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)
    names = {
        "20210412": "康得新",
        "20210413": "*ST康得",
        "20210414": "*ST康得",
        "20210415": "康得新",
        "20210416": "康得新",
    }
    client = _SyntheticTushareClient(
        daily=_flat_daily(_KANGDE, days),
        limits=_flat_limits(_KANGDE, days),
        names={_KANGDE: names},
    )
    frame = TushareAShareSource([_KANGDE], client).get_trading_status(
        _TRADABLE_START, _TRADABLE_END
    )
    tradable = _evaluate_policy(frame, _KANGDE, settings)
    expected = {
        "2021-04-12": True,
        "2021-04-13": False,
        "2021-04-14": False,
        "2021-04-15": True,
        "2021-04-16": True,
    }
    for day, want in expected.items():
        assert bool(tradable.loc[pd.Timestamp(day)]) is want, day
