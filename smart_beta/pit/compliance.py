"""Reusable point-in-time compliance suite (Phase 3, P3-H).

This module is the final Phase 3 integration gate. It is a stateless,
in-memory check runner -- not a persistence layer, registry, or evaluation
framework -- whose only job is to answer two questions about any future PIT
implementation:

**Layer A -- source contract compliance.**
    "Does this :class:`~smart_beta.pit.source.PITDataSource` expose canonical
    historical raw facts correctly?" It tests the source alone: schema
    conformance, absence of shared mutable state, determinism, and
    scenario-specific raw-fact integrity (survivorship, exchange calendar,
    float/total market cap, trading-status timing, corporate-action raw
    shape, fundamentals-vintage preservation).

**Layer B -- trusted query compliance.**
    "Given these raw facts, can querying them through
    ``PointInTimeView``/``AsOfSnapshot`` produce future-information
    leakage?" It tests the composition of a source with the trusted
    primitives the view already relies on
    (:func:`~smart_beta.pit.corporate_actions.compute_adjusted_returns`,
    :func:`~smart_beta.pit.fundamentals.latest_known_value`) -- never a
    reimplementation of them.

The two layers are deliberately separate. The three Layer-B-primary risk
categories (future-announcement gating, restatement resolution,
corporate-action adjustment) live in algorithms the trusted layer applies.
The four Layer-A-primary categories (survivorship, exchange calendar,
float/total market cap, trading-status timing) are data
completeness/integrity risks that no amount of correct query-layer logic can
manufacture if the source never had the data; for those, Layer B can only
faithfully inherit whatever the source says. Demonstrating that inheritance
is itself worth doing rather than silently assuming it, so
:func:`check_survivorship_through_view` exercises the trusted layer on top of
a source that has already dropped a delisted name.

Design constraints
------------------

* Every ``check_*`` function returns ``list[ComplianceCheckResult]`` -- even
  when it produces exactly one result -- so aggregation is uniform.
* Every Layer B ``check_*`` accepts a :class:`ViewLike` protocol, never the
  concrete ``PointInTimeView`` type. This lets a test file's deliberately
  broken duck-typed doubles be checked by the exact same functions as the
  real thing, proving the checks discriminate on behavior, not on type.
* Layer A checks are fully parameterized and have zero dependency on
  the synthetic reference module. Only
  :func:`run_reference_compliance_suite` imports the reference-fixture
  constants, and it is meaningful only against a source that reproduces that
  exact scenario. For a genuinely different vendor, call the ``check_*``
  functions directly with that vendor's own known facts.
* Failure messages always name the specific concept violated (the stock
  id/date/field involved, ``knowledge_date``, "restatement", etc.), never a
  generic "check failed". A compliance suite whose failures cannot be
  understood has no diagnostic value.
* No check re-derives an answer the trusted layer owns. In particular,
  :func:`check_corporate_action_adjustment_correct` compares against the ONE
  externally documented true return rather than calling
  :func:`~smart_beta.pit.corporate_actions.compute_adjusted_returns` itself
  (anti-tautology).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.schema import SchemaError, validate_panel
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.schema import (
    ADJUSTED_RETURN_COL,
    ADJUSTMENT_FACTOR_COL,
    CORPORATE_ACTIONS_SCHEMA,
    DATE_COL,
    DELIST_DATE_COL,
    EFFECTIVE_DATE_COL,
    FIELD_COL,
    FLOAT_MARKET_CAP_COL,
    FUNDAMENTALS_FACT_SCHEMA,
    KNOWLEDGE_DATE_COL,
    PIT_LISTING_INFO_SCHEMA,
    PIT_MARKET_CAP_SCHEMA,
    PIT_RAW_RETURN_PANEL_SCHEMA,
    PIT_TRADING_STATUS_SCHEMA,
    RAW_RETURN_COL,
    REPORT_PERIOD_END_COL,
    STOCK_COL,
    TOTAL_MARKET_CAP_COL,
    VALUE_COL,
)
from smart_beta.pit.source import PITDataSource
from smart_beta.pit.view import PointInTimeView
from smart_beta.pit.synthetic import (
    CORPORATE_ACTION_ADJUSTMENT_FACTOR,
    CORPORATE_ACTION_EFFECTIVE_DATE,
    CORPORATE_ACTION_RAW_RETURN,
    CORPORATE_ACTION_TRUE_RETURN,
    DELIST_DATE,
    FIXTURE_END,
    FIXTURE_START,
    FUTURE_ANNOUNCE_FIELD,
    FUTURE_ANNOUNCE_KNOWLEDGE_DATE,
    FUTURE_ANNOUNCE_REPORT_PERIOD_END,
    FUTURE_ANNOUNCE_VALUE,
    HOLIDAY_2019_05_01,
    MARKET_CAP_DATE,
    MARKET_CAP_FLOAT,
    MARKET_CAP_TOTAL,
    RESTATEMENT_FIELD,
    RESTATEMENT_REPORT_PERIOD_END,
    RESTATEMENT_T1,
    RESTATEMENT_T2,
    RESTATEMENT_X,
    RESTATEMENT_Y,
    S_CORPORATE_ACTION,
    S_DELISTED,
    S_FUTURE_ANNOUNCE,
    S_MARKET_CAP,
    S_RESTATEMENT,
    S_TRADING_STATUS,
    SUSPENDED_DATE,
    SyntheticPITSource,
)

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComplianceCheckResult:
    """One named, layer-tagged outcome of a compliance check."""

    name: str
    layer: str
    passed: bool
    message: str
    context: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ComplianceReport:
    """An immutable aggregation of every check outcome in a run."""

    checks: tuple[ComplianceCheckResult, ...]

    @property
    def all_passed(self) -> bool:
        """Whether every check in this report passed."""
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[ComplianceCheckResult, ...]:
        """Only the failing checks, in their original order."""
        return tuple(check for check in self.checks if not check.passed)

    def summary(self) -> str:
        """One line per check: name, PASS/FAIL, message."""
        return "\n".join(
            f"{check.name} {'PASS' if check.passed else 'FAIL'} {check.message}"
            for check in self.checks
        )


# ---------------------------------------------------------------------------
# Structural typing for Layer B
# ---------------------------------------------------------------------------


class SnapshotLike(Protocol):
    """The structural snapshot surface Layer B checks compose against.

    Deliberately structural (not the concrete ``AsOfSnapshot``): a
    deliberately broken test double that implements this surface but does
    not inherit from the real class must still be checkable.
    """

    def fundamentals(
        self, start: date | pd.Timestamp, end: date | pd.Timestamp, fields: Sequence[str]
    ) -> pd.DataFrame: ...

    def adjusted_returns(
        self, start: date | pd.Timestamp, end: date | pd.Timestamp
    ) -> pd.DataFrame: ...

    def market_cap(
        self, start: date | pd.Timestamp, end: date | pd.Timestamp
    ) -> pd.DataFrame: ...

    def trading_status(
        self, start: date | pd.Timestamp, end: date | pd.Timestamp
    ) -> pd.DataFrame: ...

    def listing_info(self) -> pd.DataFrame: ...


class ViewLike(Protocol):
    """The structural view surface Layer B checks compose against."""

    def as_of(self, as_of: date | pd.Timestamp) -> SnapshotLike: ...

    def build_panel(
        self,
        start: date | pd.Timestamp,
        end: date | pd.Timestamp,
        fundamental_fields: Sequence[str],
    ) -> pd.DataFrame: ...


# ---------------------------------------------------------------------------
# Small internal helpers
# ---------------------------------------------------------------------------

_LAYER_A = "A"
_LAYER_B = "B"

#: Documented reference-fixture fact used by run_reference_compliance_suite:
#: March 2019's true calendar month-end is Sunday 2019-03-31, so the exchange
#: trading month-end is Friday 2019-03-29. Kept as a literal so the month-end
#: check does not compare the calendar against itself.
_REFERENCE_MONTH_END_YEAR = 2019
_REFERENCE_MONTH_END_MONTH = 3
_REFERENCE_TRADING_MONTH_END = "2019-03-29"

#: Default fundamental fields used by the reference suite and as a convenience
#: default for callers checking the reference fixture.
_REFERENCE_FIELDS = ("revenue", "net_profit")

#: Default field used to exercise build_panel's observation-date logic.
_DEFAULT_PANEL_FIELD = ("revenue",)


def _result(
    name: str,
    layer: str,
    passed: bool,
    message: str,
    context: Mapping[str, object] | None = None,
) -> ComplianceCheckResult:
    return ComplianceCheckResult(
        name=name,
        layer=layer,
        passed=passed,
        message=message,
        context=dict(context) if context else {},
    )


def _close(actual: object, expected: object, *, rel_tol: float = 1e-9) -> bool:
    """Numeric closeness that tolerates float representation noise."""
    try:
        return math.isclose(
            float(actual), float(expected), rel_tol=rel_tol, abs_tol=rel_tol
        )
    except (TypeError, ValueError):
        return False


def _panel_method_specs(
    source: PITDataSource,
    start: date | str,
    end: date | str,
    fields: Sequence[str],
) -> list[tuple[str, object, object]]:
    """``(method_name, callable, schema)`` for the six panel-returning methods."""
    return [
        (
            "get_raw_returns",
            lambda: source.get_raw_returns(start, end),
            PIT_RAW_RETURN_PANEL_SCHEMA,
        ),
        (
            "get_corporate_actions",
            lambda: source.get_corporate_actions(start, end),
            CORPORATE_ACTIONS_SCHEMA,
        ),
        (
            "get_market_cap",
            lambda: source.get_market_cap(start, end),
            PIT_MARKET_CAP_SCHEMA,
        ),
        (
            "get_fundamentals",
            lambda: source.get_fundamentals(start, end, list(fields)),
            FUNDAMENTALS_FACT_SCHEMA,
        ),
        (
            "get_trading_status",
            lambda: source.get_trading_status(start, end),
            PIT_TRADING_STATUS_SCHEMA,
        ),
        ("get_listing_info", source.get_listing_info, PIT_LISTING_INFO_SCHEMA),
    ]


def _mutate_frame_in_place(frame: pd.DataFrame) -> None:
    """Overwrite every column of ``frame`` with same-dtype garbage.

    Used only to detect a source that leaks its own internal frame or shares
    column buffers between calls.
    """
    for column in frame.columns:
        dtype = frame[column].dtype
        if pd.api.types.is_bool_dtype(dtype):
            frame.loc[:, column] = ~frame[column]
        elif pd.api.types.is_numeric_dtype(dtype):
            frame.loc[:, column] = frame[column] + 1.0
        elif pd.api.types.is_datetime64_any_dtype(dtype):
            frame.loc[:, column] = frame[column] + pd.Timedelta(days=1)
        else:
            frame.loc[:, column] = "MUTATED"


def _fact_rows(
    frame: pd.DataFrame,
    stock_id: str,
    report_period_end: object,
    field_name: str,
) -> pd.DataFrame:
    """Rows of ``frame`` for one ``(stock, report_period_end, field)`` fact.

    Returns an empty frame (rather than raising) when the broken double
    returned something missing the canonical columns.
    """
    required = {STOCK_COL, REPORT_PERIOD_END_COL, FIELD_COL}
    if not required.issubset(frame.columns):
        return frame.iloc[0:0]
    target = pd.Timestamp(report_period_end)
    mask = (
        (frame[STOCK_COL] == stock_id)
        & (frame[REPORT_PERIOD_END_COL] == target)
        & (frame[FIELD_COL] == field_name)
    )
    return frame.loc[mask]


def _sorted_unique_dates(frame: pd.DataFrame, column: str) -> set[pd.Timestamp]:
    if column not in frame.columns or frame.empty:
        return set()
    return {pd.Timestamp(value) for value in frame[column].unique()}


# ---------------------------------------------------------------------------
# Layer A -- source contract compliance
# ---------------------------------------------------------------------------


def check_schema_conformance(
    source: PITDataSource,
    start: date | str,
    end: date | str,
    fields: Sequence[str],
) -> list[ComplianceCheckResult]:
    """One result per ``PITDataSource`` method.

    ``trading_calendar`` must return a :class:`TradingCalendar`; the six
    panel methods each validate via :func:`validate_panel` against their
    canonical schema. A :class:`SchemaError` (or any source-side exception)
    becomes a failed result rather than propagating.
    """
    results: list[ComplianceCheckResult] = []

    try:
        calendar = source.trading_calendar()
        passed = isinstance(calendar, TradingCalendar)
        results.append(
            _result(
                "schema_conformance_trading_calendar",
                _LAYER_A,
                passed,
                (
                    "trading_calendar returns a TradingCalendar"
                    if passed
                    else (
                        "trading_calendar returned "
                        f"{type(calendar).__name__}, not a TradingCalendar"
                    )
                ),
                {"method": "trading_calendar", "actual_type": type(calendar).__name__},
            )
        )
    except Exception as exc:  # noqa: BLE001 - a broken source must not crash the suite
        results.append(
            _result(
                "schema_conformance_trading_calendar",
                _LAYER_A,
                False,
                f"trading_calendar raised {type(exc).__name__}: {exc}",
                {"method": "trading_calendar", "error": repr(exc)},
            )
        )

    for method_name, call, schema in _panel_method_specs(source, start, end, fields):
        try:
            frame = call()
            validate_panel(frame, schema, name=method_name)
        except SchemaError as exc:
            results.append(
                _result(
                    f"schema_conformance_{method_name}",
                    _LAYER_A,
                    False,
                    f"{method_name} violates its canonical schema: {exc}",
                    {"method": method_name, "error": str(exc)},
                )
            )
        except Exception as exc:  # noqa: BLE001 - a broken source must not crash the suite
            results.append(
                _result(
                    f"schema_conformance_{method_name}",
                    _LAYER_A,
                    False,
                    f"{method_name} raised {type(exc).__name__}: {exc}",
                    {"method": method_name, "error": repr(exc)},
                )
            )
        else:
            results.append(
                _result(
                    f"schema_conformance_{method_name}",
                    _LAYER_A,
                    True,
                    f"{method_name} conforms to its canonical schema",
                    {"method": method_name},
                )
            )

    return results


def check_no_shared_mutable_state(
    source: PITDataSource,
    start: date | str,
    end: date | str,
    fields: Sequence[str],
) -> list[ComplianceCheckResult]:
    """Per panel method: call twice, mutate the first, confirm the second is
    unaffected. Detects a source returning its internal frame or sharing
    column buffers between calls."""
    results: list[ComplianceCheckResult] = []

    for method_name, call, _schema in _panel_method_specs(source, start, end, fields):
        try:
            first = call()
            second = call()
            expected = second.copy(deep=True)
            _mutate_frame_in_place(first)
            pd.testing.assert_frame_equal(second, expected)
        except Exception as exc:  # noqa: BLE001 - a shared-state source may fail any way
            results.append(
                _result(
                    f"no_shared_state_{method_name}",
                    _LAYER_A,
                    False,
                    (
                        f"{method_name} shares mutable state across calls: "
                        f"mutating one returned frame changed another ({type(exc).__name__})"
                    ),
                    {"method": method_name, "error": repr(exc)},
                )
            )
        else:
            results.append(
                _result(
                    f"no_shared_state_{method_name}",
                    _LAYER_A,
                    True,
                    (
                        f"{method_name} returns independently-owned frames "
                        "(mutating one call does not affect another)"
                    ),
                    {"method": method_name},
                )
            )

    return results


def check_deterministic_results(
    source: PITDataSource,
    start: date | str,
    end: date | str,
    fields: Sequence[str],
) -> list[ComplianceCheckResult]:
    """Per panel method: two identical calls produce identical results."""
    results: list[ComplianceCheckResult] = []

    for method_name, call, _schema in _panel_method_specs(source, start, end, fields):
        try:
            first = call()
            second = call()
            pd.testing.assert_frame_equal(first, second)
        except Exception as exc:  # noqa: BLE001 - determinism failure of any kind
            results.append(
                _result(
                    f"deterministic_{method_name}",
                    _LAYER_A,
                    False,
                    f"{method_name} is not deterministic: two identical calls differ",
                    {"method": method_name, "error": repr(exc)},
                )
            )
        else:
            results.append(
                _result(
                    f"deterministic_{method_name}",
                    _LAYER_A,
                    True,
                    f"{method_name} is deterministic across identical calls",
                    {"method": method_name},
                )
            )

    return results


def check_delisted_security_history_present(
    source: PITDataSource,
    *,
    stock_id: str,
    expected_last_date: object,
    query_start: date | str,
    query_end: date | str,
    fields: Sequence[str] = (),
) -> list[ComplianceCheckResult]:
    """A delisted security's raw history survives, and its real delist date
    is reported.

    Across ``get_raw_returns``/``get_market_cap``/``get_trading_status`` and
    (when ``fields`` are supplied) ``get_fundamentals``, ``stock_id``'s rows
    must be present up to and including ``expected_last_date``.
    ``get_listing_info`` must report that real date, not ``NaT``. ``fields``
    is an explicit parameter because ``get_fundamentals`` requires one.
    """
    target = pd.Timestamp(expected_last_date)
    results: list[ComplianceCheckResult] = []

    specs: list[tuple[str, object, str]] = [
        (
            "get_raw_returns",
            lambda: source.get_raw_returns(query_start, query_end),
            DATE_COL,
        ),
        (
            "get_market_cap",
            lambda: source.get_market_cap(query_start, query_end),
            DATE_COL,
        ),
        (
            "get_trading_status",
            lambda: source.get_trading_status(query_start, query_end),
            DATE_COL,
        ),
    ]
    if fields:
        specs.append(
            (
                "get_fundamentals",
                lambda: source.get_fundamentals(query_start, query_end, list(fields)),
                REPORT_PERIOD_END_COL,
            )
        )

    for method_name, call, date_column in specs:
        name = f"delisted_history_{method_name}"
        try:
            frame = call()
            rows = frame.loc[frame[STOCK_COL] == stock_id]
        except Exception as exc:  # noqa: BLE001
            results.append(
                _result(
                    name,
                    _LAYER_A,
                    False,
                    f"{method_name} raised {type(exc).__name__} for {stock_id}: {exc}",
                    {"stock_id": stock_id, "method": method_name, "error": repr(exc)},
                )
            )
            continue

        if rows.empty:
            results.append(
                _result(
                    name,
                    _LAYER_A,
                    False,
                    (
                        f"survivorship omission: {stock_id} has no rows in "
                        f"{method_name} for [{query_start}, {query_end}]; its "
                        f"history through {target.date()} must remain present"
                    ),
                    {"stock_id": stock_id, "method": method_name},
                )
            )
            continue

        actual_last = pd.Timestamp(rows[date_column].max())
        present_at_last = bool((rows[date_column] == target).any())
        passed = present_at_last and actual_last == target
        if passed:
            message = (
                f"{stock_id} history present through its delist date "
                f"{target.date()} in {method_name}"
            )
        else:
            message = (
                f"survivorship omission: {stock_id} {method_name} history ends at "
                f"{actual_last.date()}, not the real delist date {target.date()} "
                f"(row at delist date present: {present_at_last})"
            )
        results.append(
            _result(
                name,
                _LAYER_A,
                passed,
                message,
                {
                    "stock_id": stock_id,
                    "method": method_name,
                    "expected_last_date": target,
                    "actual_last_date": actual_last,
                },
            )
        )

    # The source must report the real delist date rather than masking it.
    name = "delisted_listing_info"
    try:
        listing = source.get_listing_info()
        rows = listing.loc[listing[STOCK_COL] == stock_id]
    except Exception as exc:  # noqa: BLE001
        results.append(
            _result(
                name,
                _LAYER_A,
                False,
                f"get_listing_info raised {type(exc).__name__} for {stock_id}: {exc}",
                {"stock_id": stock_id, "error": repr(exc)},
            )
        )
        return results

    if rows.empty:
        results.append(
            _result(
                name,
                _LAYER_A,
                False,
                f"survivorship omission: {stock_id} absent from get_listing_info",
                {"stock_id": stock_id},
            )
        )
    else:
        delist = rows[DELIST_DATE_COL].iloc[0]
        passed = (not pd.isna(delist)) and pd.Timestamp(delist) == target
        results.append(
            _result(
                name,
                _LAYER_A,
                passed,
                (
                    f"get_listing_info reports {stock_id} delist_date={target.date()}"
                    if passed
                    else (
                        f"get_listing_info reports {stock_id} delist_date={delist!r}, "
                        f"not its real delist date {target.date()}"
                    )
                ),
                {"stock_id": stock_id, "expected": target, "actual": delist},
            )
        )

    return results


def check_exchange_calendar_recognizes_holiday(
    source: PITDataSource,
    *,
    holiday: object,
) -> list[ComplianceCheckResult]:
    """``source.trading_calendar().is_trading_day(holiday)`` must be False."""
    target = pd.Timestamp(holiday)
    try:
        calendar = source.trading_calendar()
        is_trading = calendar.is_trading_day(target)
        passed = not is_trading
        message = (
            f"exchange calendar correctly treats {target.date()} as a holiday"
            if passed
            else (
                f"exchange calendar treats the holiday {target.date()} as a "
                "trading day"
            )
        )
        return [
            _result(
                "exchange_calendar_recognizes_holiday",
                _LAYER_A,
                passed,
                message,
                {"holiday": target, "is_trading_day": is_trading},
            )
        ]
    except Exception as exc:  # noqa: BLE001
        return [
            _result(
                "exchange_calendar_recognizes_holiday",
                _LAYER_A,
                False,
                f"could not evaluate holiday {target.date()}: {type(exc).__name__}: {exc}",
                {"holiday": target, "error": repr(exc)},
            )
        ]


def check_exchange_calendar_month_end(
    source: PITDataSource,
    *,
    year: int,
    month: int,
    expected_trading_month_end: object,
) -> list[ComplianceCheckResult]:
    """``month_end_trading_date(year, month)`` equals the expected exchange
    trading month-end.

    The caller is responsible for having already confirmed that this value
    differs from the naive calendar month-end (that confirmation belongs in
    the test, using the documented fixture fact, not in this function).
    """
    expected = pd.Timestamp(expected_trading_month_end)
    try:
        actual = source.trading_calendar().month_end_trading_date(year, month)
        passed = pd.Timestamp(actual) == expected
        message = (
            (
                f"exchange trading month-end for {year}-{month:02d} is "
                f"{expected.date()}"
            )
            if passed
            else (
                f"exchange trading month-end for {year}-{month:02d} is "
                f"{pd.Timestamp(actual).date()}, not the expected {expected.date()}"
            )
        )
        return [
            _result(
                "exchange_calendar_month_end",
                _LAYER_A,
                passed,
                message,
                {
                    "year": year,
                    "month": month,
                    "expected": expected,
                    "actual": actual,
                },
            )
        ]
    except Exception as exc:  # noqa: BLE001
        return [
            _result(
                "exchange_calendar_month_end",
                _LAYER_A,
                False,
                f"could not evaluate month-end {year}-{month:02d}: {type(exc).__name__}: {exc}",
                {"year": year, "month": month, "error": repr(exc)},
            )
        ]


def check_float_and_total_market_cap_distinct(
    source: PITDataSource,
    *,
    stock_id: str,
    date: object,
    expected_float: float,
    expected_total: float,
) -> list[ComplianceCheckResult]:
    """``get_market_cap`` at ``(stock_id, date)`` has the documented float and
    total values -- not swapped, not aliased to the same value."""
    target = pd.Timestamp(date)
    name = "float_and_total_market_cap_distinct"
    try:
        frame = source.get_market_cap(target, target)
        rows = frame.loc[(frame[STOCK_COL] == stock_id) & (frame[DATE_COL] == target)]
    except Exception as exc:  # noqa: BLE001
        return [
            _result(
                name,
                _LAYER_A,
                False,
                f"get_market_cap raised {type(exc).__name__} for {stock_id} at "
                f"{target.date()}: {exc}",
                {"stock_id": stock_id, "date": target, "error": repr(exc)},
            )
        ]

    if len(rows) != 1:
        return [
            _result(
                name,
                _LAYER_A,
                False,
                f"expected exactly one get_market_cap row for {stock_id} at "
                f"{target.date()}, found {len(rows)}",
                {"stock_id": stock_id, "date": target, "row_count": len(rows)},
            )
        ]

    actual_float = rows[FLOAT_MARKET_CAP_COL].iloc[0]
    actual_total = rows[TOTAL_MARKET_CAP_COL].iloc[0]
    passed = _close(actual_float, expected_float) and _close(
        actual_total, expected_total
    )

    if passed:
        message = (
            f"{stock_id} at {target.date()} has float_mcap={expected_float} and "
            f"total_mcap={expected_total}, correctly distinct"
        )
    elif _close(actual_float, expected_total) and _close(actual_total, expected_float):
        message = (
            f"float_mcap and total_mcap are swapped for {stock_id} at "
            f"{target.date()}: got float_mcap={actual_float}, "
            f"total_mcap={actual_total}"
        )
    else:
        message = (
            f"float/total market cap wrong for {stock_id} at {target.date()}: "
            f"expected float_mcap={expected_float}, total_mcap={expected_total}; "
            f"got float_mcap={actual_float}, total_mcap={actual_total}"
        )

    return [
        _result(
            name,
            _LAYER_A,
            passed,
            message,
            {
                "stock_id": stock_id,
                "date": target,
                "expected_float": expected_float,
                "expected_total": expected_total,
                "actual_float": actual_float,
                "actual_total": actual_total,
            },
        )
    ]


def check_trading_status_flag_is_date_specific(
    source: PITDataSource,
    *,
    stock_id: str,
    flag_column: str,
    expected_true_date: object,
    adjacent_dates: Sequence[object],
) -> list[ComplianceCheckResult]:
    """The flag is True at ``expected_true_date`` and False at every date in
    ``adjacent_dates``."""
    true_date = pd.Timestamp(expected_true_date)
    adjacent = [pd.Timestamp(value) for value in adjacent_dates]
    all_dates = [true_date, *adjacent]
    start, end = min(all_dates), max(all_dates)

    try:
        frame = source.get_trading_status(start, end)
        rows = frame.loc[frame[STOCK_COL] == stock_id]
    except Exception as exc:  # noqa: BLE001
        error = _result(
            "trading_status_flag_is_date_specific",
            _LAYER_A,
            False,
            f"get_trading_status raised {type(exc).__name__} for {stock_id}: {exc}",
            {"stock_id": stock_id, "flag": flag_column, "error": repr(exc)},
        )
        return [error]

    def flag_at(when: pd.Timestamp) -> bool | None:
        match = rows.loc[
            (rows[DATE_COL] == when) & (rows[STOCK_COL] == stock_id), flag_column
        ]
        if match.empty:
            return None
        return bool(match.iloc[0])

    true_value = flag_at(true_date)
    adjacent_values = {when: flag_at(when) for when in adjacent}

    true_ok = true_value is True
    adjacent_ok = all(value is False for value in adjacent_values.values())
    passed = true_ok and adjacent_ok

    if passed:
        message = (
            f"{stock_id} {flag_column} is True only at {true_date.date()} and "
            f"False at {[d.date().isoformat() for d in adjacent]}"
        )
    elif not true_ok:
        message = (
            f"trading-status timing error: {stock_id} {flag_column} is "
            f"{true_value!r} at {true_date.date()}, expected True"
        )
    else:
        offenders = [
            when.date().isoformat()
            for when, value in adjacent_values.items()
            if value is not False
        ]
        message = (
            f"trading-status timing error: {stock_id} {flag_column} is True at "
            f"adjacent date(s) {offenders}, expected False"
        )

    return [
        _result(
            "trading_status_flag_is_date_specific",
            _LAYER_A,
            passed,
            message,
            {
                "stock_id": stock_id,
                "flag": flag_column,
                "expected_true_date": true_date,
                "true_value": true_value,
                "adjacent": {str(when.date()): value for when, value in adjacent_values.items()},
            },
        )
    ]


def check_corporate_action_raw_facts(
    source: PITDataSource,
    *,
    stock_id: str,
    effective_date: object,
    expected_adjustment_factor: float,
    raw_return_date: object,
    expected_raw_return: float,
) -> list[ComplianceCheckResult]:
    """The corporate-action fact row exists with the documented factor, and
    the source's "raw" return at the action date is the UNADJUSTED number.

    The second condition is what proves the source has not pre-adjusted its
    raw data -- adjustment is exclusively the trusted layer's job.
    """
    results: list[ComplianceCheckResult] = []
    action_date = pd.Timestamp(effective_date)
    raw_date = pd.Timestamp(raw_return_date)

    name = "corporate_action_adjustment_factor"
    try:
        actions = source.get_corporate_actions(action_date, action_date)
        rows = actions.loc[
            (actions[STOCK_COL] == stock_id)
            & (actions[EFFECTIVE_DATE_COL] == action_date)
        ]
    except Exception as exc:  # noqa: BLE001
        results.append(
            _result(
                name,
                _LAYER_A,
                False,
                f"get_corporate_actions raised {type(exc).__name__}: {exc}",
                {"stock_id": stock_id, "effective_date": action_date, "error": repr(exc)},
            )
        )
    else:
        if rows.empty:
            results.append(
                _result(
                    name,
                    _LAYER_A,
                    False,
                    f"no corporate action row for {stock_id} at {action_date.date()}",
                    {"stock_id": stock_id, "effective_date": action_date},
                )
            )
        else:
            actual_factor = rows[ADJUSTMENT_FACTOR_COL].iloc[0]
            passed = _close(actual_factor, expected_adjustment_factor)
            results.append(
                _result(
                    name,
                    _LAYER_A,
                    passed,
                    (
                        f"corporate action for {stock_id} at {action_date.date()} "
                        f"has adjustment_factor={expected_adjustment_factor}"
                        if passed
                        else (
                            f"corporate action for {stock_id} at {action_date.date()} "
                            f"has adjustment_factor={actual_factor}, expected "
                            f"{expected_adjustment_factor}"
                        )
                    ),
                    {
                        "stock_id": stock_id,
                        "effective_date": action_date,
                        "expected_factor": expected_adjustment_factor,
                        "actual_factor": actual_factor,
                    },
                )
            )

    name = "raw_return_is_unadjusted"
    try:
        returns = source.get_raw_returns(raw_date, raw_date)
        rows = returns.loc[
            (returns[STOCK_COL] == stock_id) & (returns[DATE_COL] == raw_date)
        ]
    except Exception as exc:  # noqa: BLE001
        results.append(
            _result(
                name,
                _LAYER_A,
                False,
                f"get_raw_returns raised {type(exc).__name__}: {exc}",
                {"stock_id": stock_id, "raw_return_date": raw_date, "error": repr(exc)},
            )
        )
        return results

    if rows.empty:
        results.append(
            _result(
                name,
                _LAYER_A,
                False,
                f"no raw return row for {stock_id} at {raw_date.date()}",
                {"stock_id": stock_id, "raw_return_date": raw_date},
            )
        )
        return results

    actual_raw = rows[RAW_RETURN_COL].iloc[0]
    passed = _close(actual_raw, expected_raw_return)
    results.append(
        _result(
            name,
            _LAYER_A,
            passed,
            (
                f"raw return for {stock_id} at {raw_date.date()} is the documented "
                f"unadjusted {expected_raw_return}"
                if passed
                else (
                    f"raw return for {stock_id} at {raw_date.date()} is {actual_raw}, "
                    f"not the documented unadjusted {expected_raw_return} (the source "
                    "appears to have pre-adjusted its raw data)"
                )
            ),
            {
                "stock_id": stock_id,
                "raw_return_date": raw_date,
                "expected_raw_return": expected_raw_return,
                "actual_raw_return": actual_raw,
            },
        )
    )
    return results


def check_fundamentals_vintages_preserved(
    source: PITDataSource,
    *,
    stock_id: str,
    report_period_end: object,
    field: str,
    expected_knowledge_dates: Sequence[object],
) -> list[ComplianceCheckResult]:
    """The raw fundamentals table preserves one row per knowledge_date.

    Multiple vintages of the same ``(stock_id, report_period_end, field)``
    must survive rather than being collapsed to one "current" value, so that
    the trusted resolver -- not the source -- decides which was knowable.
    """
    target = pd.Timestamp(report_period_end)
    expected = {pd.Timestamp(value) for value in expected_knowledge_dates}
    name = "fundamentals_vintages_preserved"
    try:
        frame = source.get_fundamentals(target, target, [field])
        rows = _fact_rows(frame, stock_id, target, field)
    except Exception as exc:  # noqa: BLE001
        return [
            _result(
                name,
                _LAYER_A,
                False,
                f"get_fundamentals raised {type(exc).__name__}: {exc}",
                {"stock_id": stock_id, "error": repr(exc)},
            )
        ]

    actual = {pd.Timestamp(value) for value in rows[KNOWLEDGE_DATE_COL]} if not rows.empty else set()
    missing = expected - actual
    passed = not missing

    if passed:
        message = (
            f"{stock_id} {field} for report_period_end={target.date()} preserves "
            f"vintages at knowledge_date(s) "
            f"{sorted(d.date().isoformat() for d in expected)}"
        )
    else:
        message = (
            f"fundamentals vintages collapsed for {stock_id} {field} "
            f"report_period_end={target.date()}: missing knowledge_date(s) "
            f"{sorted(d.date().isoformat() for d in missing)} (found "
            f"{sorted(d.date().isoformat() for d in actual)})"
        )

    return [
        _result(
            name,
            _LAYER_A,
            passed,
            message,
            {
                "stock_id": stock_id,
                "report_period_end": target,
                "field": field,
                "expected_knowledge_dates": sorted(expected),
                "actual_knowledge_dates": sorted(actual),
            },
        )
    ]


# ---------------------------------------------------------------------------
# Layer B -- trusted query compliance
# ---------------------------------------------------------------------------


def check_future_announcement_not_visible(
    view: "ViewLike",
    *,
    stock_id: str,
    report_period_end: object,
    field: str,
    knowledge_date: object,
    before_date: object,
    query_start: date | str,
    query_end: date | str,
    expected_value: float | None = None,
) -> list[ComplianceCheckResult]:
    """A fact is invisible before its knowledge_date and visible at/after it.

    ``view.as_of(before_date).fundamentals(...)`` must have NO row for
    ``(stock_id, report_period_end, field)``; ``view.as_of(knowledge_date)``
    must have one, with the expected value when supplied.
    """
    results: list[ComplianceCheckResult] = []
    rpe = pd.Timestamp(report_period_end)
    knowledge = pd.Timestamp(knowledge_date)
    before = pd.Timestamp(before_date)

    early_rows = _fact_rows(
        view.as_of(before).fundamentals(query_start, query_end, [field]),
        stock_id,
        rpe,
        field,
    )
    early_passed = early_rows.empty
    if early_passed:
        early_message = (
            f"{stock_id} {field} report_period_end={rpe.date()} is correctly "
            f"absent at as_of={before.date()} before its knowledge_date "
            f"{knowledge.date()}"
        )
    else:
        leaked_value = early_rows[VALUE_COL].iloc[0]
        early_message = (
            f"future-announcement leakage: {stock_id} {field} "
            f"report_period_end={rpe.date()} value={leaked_value} is visible at "
            f"as_of={before.date()}, before its knowledge_date {knowledge.date()}"
        )
    results.append(
        _result(
            "future_announcement_not_visible",
            _LAYER_B,
            early_passed,
            early_message,
            {
                "stock_id": stock_id,
                "report_period_end": rpe,
                "field": field,
                "before_date": before,
                "knowledge_date": knowledge,
                "rows_before": len(early_rows),
            },
        )
    )

    late_rows = _fact_rows(
        view.as_of(knowledge).fundamentals(query_start, query_end, [field]),
        stock_id,
        rpe,
        field,
    )
    if late_rows.empty:
        late_passed = False
        late_message = (
            f"{stock_id} {field} report_period_end={rpe.date()} is not visible at "
            f"as_of={knowledge.date()}, its own knowledge_date"
        )
    elif len(late_rows) != 1:
        late_passed = False
        late_message = (
            f"{stock_id} {field} report_period_end={rpe.date()} resolved to "
            f"{len(late_rows)} rows at as_of={knowledge.date()}, expected exactly one"
        )
    else:
        late_value = late_rows[VALUE_COL].iloc[0]
        late_passed = expected_value is None or _close(late_value, expected_value)
        late_message = (
            (
                f"{stock_id} {field} report_period_end={rpe.date()} is visible at "
                f"as_of={knowledge.date()} with value {late_value}"
            )
            if late_passed
            else (
                f"{stock_id} {field} report_period_end={rpe.date()} resolved to "
                f"{late_value} at as_of={knowledge.date()}, expected {expected_value}"
            )
        )
    results.append(
        _result(
            "future_announcement_visible_at_knowledge_date",
            _LAYER_B,
            late_passed,
            late_message,
            {
                "stock_id": stock_id,
                "report_period_end": rpe,
                "field": field,
                "knowledge_date": knowledge,
                "expected_value": expected_value,
                "rows_at_knowledge_date": len(late_rows),
            },
        )
    )
    return results


def check_restatement_not_backfilled(
    view: "ViewLike",
    *,
    stock_id: str,
    report_period_end: object,
    field: str,
    t1: object,
    x_value: float,
    t2: object,
    y_value: float,
    between_date: object,
    query_start: date | str,
    query_end: date | str,
) -> list[ComplianceCheckResult]:
    """An as-of query before a restatement's knowledge_date resolves to the
    original vintage; at/after it, to the restated one.

    ``between_date`` must satisfy ``t1 <= between_date < t2``.
    """
    results: list[ComplianceCheckResult] = []
    rpe = pd.Timestamp(report_period_end)
    original_date = pd.Timestamp(t1)
    restatement_date = pd.Timestamp(t2)
    between = pd.Timestamp(between_date)

    between_rows = _fact_rows(
        view.as_of(between).fundamentals(query_start, query_end, [field]),
        stock_id,
        rpe,
        field,
    )
    if between_rows.empty:
        between_passed = False
        between_message = (
            f"no {field} vintage for {stock_id} report_period_end={rpe.date()} "
            f"visible at as_of={between.date()} (expected original X={x_value})"
        )
    elif len(between_rows) != 1:
        between_passed = False
        between_message = (
            f"{stock_id} {field} report_period_end={rpe.date()} resolved to "
            f"{len(between_rows)} rows at as_of={between.date()}, expected exactly one"
        )
    else:
        got = between_rows[VALUE_COL].iloc[0]
        between_passed = _close(got, x_value)
        between_message = (
            (
                f"{stock_id} {field} report_period_end={rpe.date()} resolves to the "
                f"original value X={x_value} at as_of={between.date()}"
            )
            if between_passed
            else (
                f"restatement backfilled: {stock_id} {field} "
                f"report_period_end={rpe.date()} resolves to Y={got} at "
                f"as_of={between.date()}, before the restatement knowledge_date "
                f"t2={restatement_date.date()} (expected original X={x_value})"
            )
        )
    results.append(
        _result(
            "restatement_not_backfilled",
            _LAYER_B,
            between_passed,
            between_message,
            {
                "stock_id": stock_id,
                "report_period_end": rpe,
                "field": field,
                "between_date": between,
                "t1": original_date,
                "t2": restatement_date,
                "expected_original": x_value,
            },
        )
    )

    late_rows = _fact_rows(
        view.as_of(restatement_date).fundamentals(query_start, query_end, [field]),
        stock_id,
        rpe,
        field,
    )
    if late_rows.empty:
        late_passed = False
        late_message = (
            f"no {field} vintage for {stock_id} report_period_end={rpe.date()} "
            f"visible at as_of=t2={restatement_date.date()}"
        )
    elif len(late_rows) != 1:
        late_passed = False
        late_message = (
            f"{stock_id} {field} report_period_end={rpe.date()} resolved to "
            f"{len(late_rows)} rows at as_of=t2={restatement_date.date()}, "
            "expected exactly one"
        )
    else:
        got_late = late_rows[VALUE_COL].iloc[0]
        late_passed = _close(got_late, y_value)
        late_message = (
            (
                f"{stock_id} {field} report_period_end={rpe.date()} resolves to the "
                f"restated value Y={y_value} at as_of=t2={restatement_date.date()}"
            )
            if late_passed
            else (
                f"{stock_id} {field} report_period_end={rpe.date()} resolves to "
                f"{got_late} at as_of=t2={restatement_date.date()}, expected "
                f"restated Y={y_value}"
            )
        )
    results.append(
        _result(
            "restatement_visible_at_knowledge_date",
            _LAYER_B,
            late_passed,
            late_message,
            {
                "stock_id": stock_id,
                "report_period_end": rpe,
                "field": field,
                "t2": restatement_date,
                "expected_restated": y_value,
            },
        )
    )
    return results


def check_survivorship_through_view(
    view: "ViewLike",
    *,
    stock_id: str,
    as_of: object,
    query_start: date | str,
    query_end: date | str,
) -> list[ComplianceCheckResult]:
    """The trusted layer does not ADD a survivorship omission.

    ``view.as_of(as_of).adjusted_returns(query_start, query_end)`` still
    contains ``stock_id``'s rows. Layer B can only faithfully inherit
    whatever the source provides; this check demonstrates that inheritance
    rather than silently assuming it.
    """
    at = pd.Timestamp(as_of)
    name = "survivorship_through_view"
    try:
        frame = view.as_of(at).adjusted_returns(query_start, query_end)
        rows = frame.loc[frame[STOCK_COL] == stock_id]
    except Exception as exc:  # noqa: BLE001
        return [
            _result(
                name,
                _LAYER_B,
                False,
                f"adjusted_returns raised {type(exc).__name__}: {exc}",
                {"stock_id": stock_id, "as_of": at, "error": repr(exc)},
            )
        ]

    passed = not rows.empty
    message = (
        (
            f"as_of={at.date()} adjusted_returns still contains {stock_id} "
            f"({len(rows)} rows), so the trusted layer adds no survivorship omission"
        )
        if passed
        else (
            f"survivorship omission inherited through the trusted layer: "
            f"{stock_id} has no adjusted_returns rows for "
            f"[{query_start}, {query_end}] at as_of={at.date()}"
        )
    )
    return [
        _result(
            name,
            _LAYER_B,
            passed,
            message,
            {"stock_id": stock_id, "as_of": at, "row_count": len(rows)},
        )
    ]


def check_build_panel_uses_exchange_calendar(
    view: "ViewLike",
    *,
    year: int,
    month: int,
    expected_trading_month_end: object,
    fundamental_fields: Sequence[str] = _DEFAULT_PANEL_FIELD,
) -> list[ComplianceCheckResult]:
    """``build_panel``'s observation dates for the month include the exchange
    trading month-end and exclude the naive calendar month-end when the two
    differ."""
    expected = pd.Timestamp(expected_trading_month_end)
    naive = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
    start = pd.Timestamp(year=year, month=month, day=1)
    end = naive
    name = "build_panel_uses_exchange_calendar"

    try:
        panel = view.build_panel(start, end, list(fundamental_fields))
        observed = _sorted_unique_dates(panel, DATE_COL)
    except Exception as exc:  # noqa: BLE001
        return [
            _result(
                name,
                _LAYER_B,
                False,
                f"build_panel raised {type(exc).__name__} for {year}-{month:02d}: {exc}",
                {"year": year, "month": month, "error": repr(exc)},
            )
        ]

    includes_expected = expected in observed
    naive_leaked = naive != expected and naive in observed
    passed = includes_expected and not naive_leaked

    if passed:
        message = (
            f"build_panel for {year}-{month:02d} observes exchange trading "
            f"month-end {expected.date()}"
            + (
                f" and correctly excludes the naive month-end {naive.date()}"
                if naive != expected
                else ""
            )
        )
    elif not includes_expected:
        message = (
            f"build_panel for {year}-{month:02d} does not observe the exchange "
            f"trading month-end {expected.date()} (observed: "
            f"{sorted(d.date().isoformat() for d in observed)})"
        )
    else:
        message = (
            f"build_panel for {year}-{month:02d} uses the naive calendar month-end "
            f"{naive.date()} instead of the exchange trading month-end "
            f"{expected.date()}"
        )

    return [
        _result(
            name,
            _LAYER_B,
            passed,
            message,
            {
                "year": year,
                "month": month,
                "expected": expected,
                "naive": naive,
                "observed": sorted(observed),
            },
        )
    ]


def check_corporate_action_adjustment_correct(
    view: "ViewLike",
    *,
    stock_id: str,
    as_of: object,
    query_start: date | str,
    query_end: date | str,
    action_date: object,
    expected_true_return: float,
) -> list[ComplianceCheckResult]:
    """The trusted layer's adjusted return at the action date equals the ONE
    externally documented true return.

    The expected value is never recomputed via
    :func:`~smart_beta.pit.corporate_actions.compute_adjusted_returns` inside
    this function (anti-tautology): a bug in the adjuster would otherwise
    produce a matching wrong answer.
    """
    at = pd.Timestamp(as_of)
    target = pd.Timestamp(action_date)
    name = "corporate_action_adjustment_correct"
    try:
        frame = view.as_of(at).adjusted_returns(query_start, query_end)
        rows = frame.loc[
            (frame[STOCK_COL] == stock_id) & (frame[DATE_COL] == target)
        ]
    except Exception as exc:  # noqa: BLE001
        return [
            _result(
                name,
                _LAYER_B,
                False,
                f"adjusted_returns raised {type(exc).__name__}: {exc}",
                {"stock_id": stock_id, "as_of": at, "error": repr(exc)},
            )
        ]

    if len(rows) != 1:
        return [
            _result(
                name,
                _LAYER_B,
                False,
                f"expected exactly one adjusted-return row for {stock_id} at "
                f"{target.date()}, found {len(rows)}",
                {"stock_id": stock_id, "action_date": target, "row_count": len(rows)},
            )
        ]

    actual = rows[ADJUSTED_RETURN_COL].iloc[0]
    passed = _close(actual, expected_true_return)
    message = (
        (
            f"{stock_id} adjusted return at the corporate-action date "
            f"{target.date()} is the true economic return {expected_true_return}"
        )
        if passed
        else (
            f"corporate-action adjustment wrong for {stock_id} at {target.date()}: "
            f"expected adjusted_ret={expected_true_return}, got {actual}"
        )
    )
    return [
        _result(
            name,
            _LAYER_B,
            passed,
            message,
            {
                "stock_id": stock_id,
                "as_of": at,
                "action_date": target,
                "expected_true_return": expected_true_return,
                "actual_adjusted_return": actual,
            },
        )
    ]


# ---------------------------------------------------------------------------
# Orchestration -- the ONLY function that consumes the reference fixture
# ---------------------------------------------------------------------------


def run_reference_compliance_suite(
    source: PITDataSource,
    settings: Settings = DEFAULT_SETTINGS,
) -> ComplianceReport:
    """Run every Layer A and Layer B check against the documented reference
    scenario, constructing ``PointInTimeView(source, settings)`` internally.

    Meaningful only against a source that reproduces
    the synthetic reference module's exact reference scenario
    (:class:`SyntheticPITSource` itself, or a vendor deliberately seeded to
    mirror it). For a genuinely different vendor, call the ``check_*``
    functions above directly with that vendor's own known facts; they have no
    dependency on this fixture.
    """
    view = PointInTimeView(source, settings)
    fields = list(_REFERENCE_FIELDS)

    layers: list[list[ComplianceCheckResult]] = [
        check_schema_conformance(source, FIXTURE_START, FIXTURE_END, fields),
        check_no_shared_mutable_state(source, FIXTURE_START, FIXTURE_END, fields),
        check_deterministic_results(source, FIXTURE_START, FIXTURE_END, fields),
        check_delisted_security_history_present(
            source,
            stock_id=S_DELISTED,
            expected_last_date=DELIST_DATE,
            query_start=FIXTURE_START,
            query_end=FIXTURE_END,
            fields=fields,
        ),
        check_exchange_calendar_recognizes_holiday(
            source, holiday=HOLIDAY_2019_05_01
        ),
        check_exchange_calendar_month_end(
            source,
            year=_REFERENCE_MONTH_END_YEAR,
            month=_REFERENCE_MONTH_END_MONTH,
            expected_trading_month_end=_REFERENCE_TRADING_MONTH_END,
        ),
        check_float_and_total_market_cap_distinct(
            source,
            stock_id=S_MARKET_CAP,
            date=MARKET_CAP_DATE,
            expected_float=MARKET_CAP_FLOAT,
            expected_total=MARKET_CAP_TOTAL,
        ),
        check_trading_status_flag_is_date_specific(
            source,
            stock_id=S_TRADING_STATUS,
            flag_column="is_suspended",
            expected_true_date=SUSPENDED_DATE,
            adjacent_dates=[
                source.trading_calendar().month_end_trading_date(2020, 6),
                source.trading_calendar().month_end_trading_date(2020, 8),
            ],
        ),
        check_corporate_action_raw_facts(
            source,
            stock_id=S_CORPORATE_ACTION,
            effective_date=CORPORATE_ACTION_EFFECTIVE_DATE,
            expected_adjustment_factor=CORPORATE_ACTION_ADJUSTMENT_FACTOR,
            raw_return_date=CORPORATE_ACTION_EFFECTIVE_DATE,
            expected_raw_return=CORPORATE_ACTION_RAW_RETURN,
        ),
        check_fundamentals_vintages_preserved(
            source,
            stock_id=S_RESTATEMENT,
            report_period_end=RESTATEMENT_REPORT_PERIOD_END,
            field=RESTATEMENT_FIELD,
            expected_knowledge_dates=[RESTATEMENT_T1, RESTATEMENT_T2],
        ),
        check_future_announcement_not_visible(
            view,
            stock_id=S_FUTURE_ANNOUNCE,
            report_period_end=FUTURE_ANNOUNCE_REPORT_PERIOD_END,
            field=FUTURE_ANNOUNCE_FIELD,
            knowledge_date=FUTURE_ANNOUNCE_KNOWLEDGE_DATE,
            before_date=FUTURE_ANNOUNCE_REPORT_PERIOD_END,
            query_start=FIXTURE_START,
            query_end=FIXTURE_END,
            expected_value=FUTURE_ANNOUNCE_VALUE,
        ),
        check_restatement_not_backfilled(
            view,
            stock_id=S_RESTATEMENT,
            report_period_end=RESTATEMENT_REPORT_PERIOD_END,
            field=RESTATEMENT_FIELD,
            t1=RESTATEMENT_T1,
            x_value=RESTATEMENT_X,
            t2=RESTATEMENT_T2,
            y_value=RESTATEMENT_Y,
            between_date=RESTATEMENT_T1,
            query_start=FIXTURE_START,
            query_end=FIXTURE_END,
        ),
        check_survivorship_through_view(
            view,
            stock_id=S_DELISTED,
            as_of=FIXTURE_END,
            query_start=FIXTURE_START,
            query_end=FIXTURE_END,
        ),
        check_build_panel_uses_exchange_calendar(
            view,
            year=_REFERENCE_MONTH_END_YEAR,
            month=_REFERENCE_MONTH_END_MONTH,
            expected_trading_month_end=_REFERENCE_TRADING_MONTH_END,
            fundamental_fields=[FUTURE_ANNOUNCE_FIELD],
        ),
        check_corporate_action_adjustment_correct(
            view,
            stock_id=S_CORPORATE_ACTION,
            as_of=FIXTURE_END,
            query_start=FIXTURE_START,
            query_end=FIXTURE_END,
            action_date=CORPORATE_ACTION_EFFECTIVE_DATE,
            expected_true_return=CORPORATE_ACTION_TRUE_RETURN,
        ),
    ]

    return ComplianceReport(
        checks=tuple(itertools.chain.from_iterable(layers))
    )
