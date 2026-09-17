"""Phase 4B certification (P4B-9): Phase 3's ``check_*`` functions against
the real, fixture-fed :class:`TiingoPITSource`.

This is not an ordinary unit-test module. It runs the already-merged,
already-parameterized :mod:`smart_beta.pit.compliance` checks directly
against the real Tiingo adapter using real, recorded Tiingo specimens, and
its assertions are the executable half of the checked-in certification
report at ``docs/phase4b_tiingo_certification.md``. Every disposition in
that document must match an assertion in this file -- the report is never
allowed to claim a capability these tests did not actually verify.

Deliberate scope decisions (all reported in the document):

* ``check_fundamentals_vintages_preserved`` and
  ``check_restatement_not_backfilled`` are **never** invoked against
  ``TiingoPITSource`` -- no multi-vintage specimen exists under the current
  plan tier, so neither check can be honestly answered here.
* ``run_reference_compliance_suite`` is **never** invoked -- it is
  meaningful only against ``SyntheticPITSource``'s exact reference scenario.
* ``check_future_announcement_not_visible`` **is** run, against a genuine
  AAPL specimen (report period 2026-06-27, knowledge date 2026-07-31).
* One test (``test_fundamentals_ranged_query_semantics``) deliberately makes
  a real live call, only when ``TIINGO_API_KEY`` is present. Every other
  test is offline: the source is fed by ``replay_transport`` recordings under
  ``tests/fixtures/tiingo/certification/``.

A note on the two-ticker universe and the plan tier
---------------------------------------------------
``TiingoPITSource(["AAPL", "TWTR"], ...)`` is the certification universe.
However, Tiingo's fundamentals endpoints (statements *and* daily) return
HTTP 400 for any non-DOW-30 ticker under the current plan tier -- confirmed
live for TWTR and recorded verbatim as ``twtr_statements_*.json`` /
``twtr_fundamentals_daily.json``. That means a two-ticker
``get_fundamentals``/``get_market_cap`` call raises for *any* range, not
because of the adapter but because the vendor refuses the request. The
structural (schema/determinism) checks are therefore run over the AAPL-only
universe, where those endpoints are supported; the TWTR survivorship check
runs over the two-ticker universe and records the plan-tier failure
explicitly instead of hiding it. See the certification report for the full
disposition.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.pit.compliance import (
    check_build_panel_uses_exchange_calendar,
    check_corporate_action_adjustment_correct,
    check_corporate_action_raw_facts,
    check_delisted_security_history_present,
    check_deterministic_results,
    check_exchange_calendar_month_end,
    check_exchange_calendar_recognizes_holiday,
    check_future_announcement_not_visible,
    check_no_shared_mutable_state,
    check_schema_conformance,
    check_survivorship_through_view,
    check_trading_status_flag_is_date_specific,
)
from smart_beta.pit.schema import DELIST_DATE_COL, STOCK_COL
from smart_beta.pit.view import PointInTimeView
from smart_beta.vendors.tiingo.client import (
    API_KEY_ENV_VAR,
    TiingoAPIError,
    TiingoClient,
    replay_transport,
)
from smart_beta.vendors.tiingo.fundamentals import (
    FiscalPeriodReconciliationError,
    map_asreported_to_fundamentals,
)
from smart_beta.vendors.tiingo.source import TiingoPITSource

# ---------------------------------------------------------------------------
# Constants / paths
# ---------------------------------------------------------------------------
_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "tiingo" / "certification"
_REPORT_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "phase4b_tiingo_certification.md"
)

_AAPL = "AAPL"
_TWTR = "TWTR"

_AAPL_META = "aapl_meta.json"
_AAPL_EOD = "aapl_eod_prices_2020-08-20_2020-09-05.json"
_AAPL_DAILY = "aapl_fundamentals_daily_2024-01-02_2024-01-05.json"
_TWTR_META = "twtr_meta.json"
_TWTR_EOD = "twtr_eod_prices_2022-10-20_2022-10-28.json"

# Coverage-consistent ranged statement pair (Q3 + Q2 2026 both endpoints).
_AAPL_2026_AR = "aapl_statements_asreported_2026-03-01_2026-07-31.json"
_AAPL_2026_NO = "aapl_statements_normalized_2026-03-01_2026-07-31.json"
# A second pair whose period end (2023-12-30) falls in a month whose real NYSE
# trading month-end (2023-12-29) differs from the naive calendar month-end.
_AAPL_2023_AR = "aapl_statements_asreported_2023-12-01_2024-02-29.json"
_AAPL_2023_NO = "aapl_statements_normalized_2023-12-01_2024-02-29.json"
# Real plan-tier-blocked responses: HTTP 400 for a non-DOW-30 ticker.
_TWTR_STATEMENTS_AR = "twtr_statements_asreported.json"
_TWTR_STATEMENTS_NO = "twtr_statements_normalized.json"
_TWTR_DAILY = "twtr_fundamentals_daily.json"
# Wider range used to demonstrate the ranged-query irreconcilability.
_AAPL_FULL_AR = "aapl_statements_asreported_2026-01-01_2026-12-31.json"
_AAPL_FULL_NO = "aapl_statements_normalized_2026-01-01_2026-12-31.json"

_AAPL_STATEMENTS_PATH = "/tiingo/fundamentals/AAPL/statements"
_TWTR_STATEMENTS_PATH = "/tiingo/fundamentals/TWTR/statements"
_AAPL_DAILY_PATH = "/tiingo/fundamentals/AAPL/daily"
_TWTR_DAILY_PATH = "/tiingo/fundamentals/TWTR/daily"

_FIELDS = ("revenue", "netinc")

#: AAPL 2020-08-31 4-for-1 split, from the recorded raw closes:
#: raw_ret = 129.04 / 499.23 - 1; true_ret = (1 + raw_ret) * 4 - 1.
_AAPL_SPLIT_DATE = "2020-08-31"
_AAPL_SPLIT_FACTOR = 4.0
_AAPL_SPLIT_RAW_RETURN = -0.7415219437934419
_AAPL_SPLIT_TRUE_RETURN = 0.03391222482623224

_TWTR_LAST_EOD_DATE = "2022-10-28"

#: Every compliance check this task considered relevant in principle. The
#: certification report must give each an explicit disposition; the three
#: restatement/future-announcement checks are listed even though only one of
#: them can actually run against Tiingo under the current access.
CONSIDERED_CHECKS: tuple[str, ...] = (
    "check_schema_conformance",
    "check_no_shared_mutable_state",
    "check_deterministic_results",
    "check_delisted_security_history_present",
    "check_exchange_calendar_recognizes_holiday",
    "check_exchange_calendar_month_end",
    "check_corporate_action_raw_facts",
    "check_survivorship_through_view",
    "check_build_panel_uses_exchange_calendar",
    "check_corporate_action_adjustment_correct",
    "check_fundamentals_vintages_preserved",
    "check_restatement_not_backfilled",
    "check_future_announcement_not_visible",
    "check_float_and_total_market_cap_distinct",
    "check_trading_status_flag_is_date_specific",
)

_DISPOSITION_TOKENS = ("PASS", "FAIL", "NOT CERTIFIED", "NOT RUN")

#: Compliance result names the two forbidden restatement/vintage checks would
#: produce if a future edit quietly re-added them to this file.
_FORBIDDEN_CHECK_NAMES = (
    "fundamentals_vintages_preserved",
    "restatement_not_backfilled",
    "restatement_visible_at_knowledge_date",
)


# ---------------------------------------------------------------------------
# Fixture loading / source construction
# ---------------------------------------------------------------------------
def _body(filename: str) -> object:
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _make_client(
    *,
    asreported_file: str = _AAPL_2026_AR,
    normalized_file: str = _AAPL_2026_NO,
) -> TiingoClient:
    """A fixture-fed client using P4B-R1's integrated ``param_recordings``
    mechanism to disambiguate the asReported / normalized statements pair."""
    path_recordings: dict[str, tuple[int, object]] = {
        "/tiingo/daily/AAPL": (200, _body(_AAPL_META)),
        "/tiingo/daily/AAPL/prices": (200, _body(_AAPL_EOD)),
        "/tiingo/daily/TWTR": (200, _body(_TWTR_META)),
        "/tiingo/daily/TWTR/prices": (200, _body(_TWTR_EOD)),
        _AAPL_STATEMENTS_PATH: (200, _body(normalized_file)),
        _AAPL_DAILY_PATH: (200, _body(_AAPL_DAILY)),
        # Real plan-tier HTTP 400s: TWTR is not a DOW-30 ticker.
        _TWTR_STATEMENTS_PATH: (400, _body(_TWTR_STATEMENTS_NO)),
        _TWTR_DAILY_PATH: (400, _body(_TWTR_DAILY)),
    }
    param_recordings: dict[tuple[str, str, str], tuple[int, object]] = {
        (_AAPL_STATEMENTS_PATH, "asReported", "true"): (200, _body(asreported_file)),
        (_TWTR_STATEMENTS_PATH, "asReported", "true"): (
            400,
            _body(_TWTR_STATEMENTS_AR),
        ),
    }
    return TiingoClient(
        transport=replay_transport(
            path_recordings, param_recordings=param_recordings
        )
    )


def _build_source(
    tickers: tuple[str, ...] = (_AAPL, _TWTR),
    *,
    asreported_file: str = _AAPL_2026_AR,
    normalized_file: str = _AAPL_2026_NO,
) -> TiingoPITSource:
    """The real, assembled :class:`TiingoPITSource` over recorded fixtures."""
    return TiingoPITSource(
        list(tickers),
        client=_make_client(
            asreported_file=asreported_file, normalized_file=normalized_file
        ),
    )


def _by_name(results) -> dict[str, object]:
    return {result.name: result for result in results}


# ---------------------------------------------------------------------------
# Layer A -- schema and determinism (P4B-D1 proof, end to end)
# ---------------------------------------------------------------------------
def test_layer_a_schema_and_determinism() -> None:
    """Schema conformance, no-shared-state, and canonical determinism all
    PASS on the real adapter.

    Run over the AAPL-only universe: TWTR's fundamentals/daily endpoints are
    blocked at the plan tier (HTTP 400), so a two-ticker source raises for
    ``get_fundamentals``/``get_market_cap`` regardless of range -- a vendor
    access limitation, not an adapter defect (see the survivorship test
    below and the certification report).

    The query range ``2020-01-01..2026-12-31`` is real and NON-EMPTY for
    every panel method, so this is a meaningful exercise of
    ``check_deterministic_results``, not a trivially-passing empty range.
    """
    source = _build_source((_AAPL,))
    start, end = "2020-01-01", "2026-12-31"

    schema = _by_name(check_schema_conformance(source, start, end, _FIELDS))
    assert len(schema) == 7  # trading_calendar + six panel methods
    assert all(result.passed for result in schema.values()), {
        name: result.message for name, result in schema.items() if not result.passed
    }

    shared = _by_name(check_no_shared_mutable_state(source, start, end, _FIELDS))
    assert len(shared) == 6
    assert all(result.passed for result in shared.values()), {
        name: result.message for name, result in shared.items() if not result.passed
    }

    deterministic = _by_name(
        check_deterministic_results(source, start, end, _FIELDS)
    )
    assert len(deterministic) == 6
    assert all(result.passed for result in deterministic.values()), {
        name: result.message
        for name, result in deterministic.items()
        if not result.passed
    }

    # Prove the range really was non-empty for the panel methods; an empty
    # output trivially "passes" determinism and would prove nothing.
    for frame in (
        source.get_raw_returns(start, end),
        source.get_corporate_actions(start, end),
        source.get_market_cap(start, end),
        source.get_fundamentals(start, end, _FIELDS),
        source.get_trading_status(start, end),
        source.get_listing_info(),
    ):
        assert not frame.empty


# ---------------------------------------------------------------------------
# Layer A -- TWTR survivorship (mixed, expected result)
# ---------------------------------------------------------------------------
def test_layer_a_survivorship_twtr() -> None:
    """TWTR's raw history persists through its real last EOD date, but its
    listing info cannot corroborate a delist date, and its market cap is
    unavailable under the plan tier.

    Expected breakdown (all asserted explicitly; the whole check is NOT
    asserted to pass):

    * ``get_raw_returns`` / ``get_trading_status`` row presence: PASS --
      the rows genuinely persist through 2022-10-28.
    * ``get_market_cap``: FAIL -- Tiingo's daily-fundamentals endpoint
      returns HTTP 400 for TWTR (non-DOW-30) under the current plan tier.
      This is a vendor-access limitation, not a dropped-data omission and
      not an adapter defect.
    * ``delisted_listing_info``: FAIL -- P4B-7's frozen corroboration policy
      sees a trailing zero-volume run of length 1 against the minimum of 5,
      so ``delist_date`` is ``NaT``. This is the correct, expected
      consequence of the policy applied to real evidence, not a bug.
    """
    source = _build_source()
    results = _by_name(
        check_delisted_security_history_present(
            source,
            stock_id=_TWTR,
            expected_last_date=_TWTR_LAST_EOD_DATE,
            query_start="2022-10-20",
            query_end="2022-10-28",
        )
    )

    assert results["delisted_history_get_raw_returns"].passed is True
    assert results["delisted_history_get_trading_status"].passed is True
    assert results["delisted_history_get_market_cap"].passed is False
    assert results["delisted_listing_info"].passed is False

    # The market-cap failure is the real plan-tier 400, not a silent gap.
    market_cap_message = results["delisted_history_get_market_cap"].message
    assert "TiingoAPIError" in market_cap_message
    assert "400" in market_cap_message

    # The listing-info failure is the frozen corroboration policy: NaT.
    listing_message = results["delisted_listing_info"].message
    assert "NaT" in listing_message or "not its real delist date" in listing_message

    # Unambiguous, direct evidence for the TWTR DELISTING CORROBORATION line.
    listing = source.get_listing_info()
    twtr_row = listing.loc[listing[STOCK_COL] == _TWTR]
    assert len(twtr_row) == 1
    assert pd.isna(twtr_row[DELIST_DATE_COL].iloc[0])


# ---------------------------------------------------------------------------
# Layer A -- exchange calendar
# ---------------------------------------------------------------------------
def test_layer_a_exchange_calendar() -> None:
    source = _build_source()

    holiday = _by_name(
        check_exchange_calendar_recognizes_holiday(source, holiday="2021-01-01")
    )
    assert holiday["exchange_calendar_recognizes_holiday"].passed is True

    # March 2019's naive calendar month-end is Sunday 2019-03-31; NYSE's real
    # trading month-end is Friday 2019-03-29.
    month_end = _by_name(
        check_exchange_calendar_month_end(
            source, year=2019, month=3, expected_trading_month_end="2019-03-29"
        )
    )
    assert month_end["exchange_calendar_month_end"].passed is True


# ---------------------------------------------------------------------------
# Layer A -- corporate-action raw facts (AAPL 4-for-1 split)
# ---------------------------------------------------------------------------
def test_layer_a_corporate_action_raw_facts_aapl_split() -> None:
    results = _by_name(
        check_corporate_action_raw_facts(
            _build_source((_AAPL,)),
            stock_id=_AAPL,
            effective_date=_AAPL_SPLIT_DATE,
            expected_adjustment_factor=_AAPL_SPLIT_FACTOR,
            raw_return_date=_AAPL_SPLIT_DATE,
            expected_raw_return=_AAPL_SPLIT_RAW_RETURN,
        )
    )
    assert results["corporate_action_adjustment_factor"].passed is True
    assert results["raw_return_is_unadjusted"].passed is True


# ---------------------------------------------------------------------------
# Layer B -- trusted query compliance
# ---------------------------------------------------------------------------
def test_layer_b_survivorship_through_view() -> None:
    view = PointInTimeView(_build_source())
    results = _by_name(
        check_survivorship_through_view(
            view,
            stock_id=_TWTR,
            as_of="2022-10-28",
            query_start="2022-10-20",
            query_end="2022-10-28",
        )
    )
    assert results["survivorship_through_view"].passed is True


def test_layer_b_build_panel_uses_exchange_calendar() -> None:
    # The 2023-12-01..2024-02-29 statement pair has a report_period_end of
    # 2023-12-30, so December 2023's panel row exists; its real NYSE trading
    # month-end (2023-12-29) differs from the naive 2023-12-31.
    view = PointInTimeView(
        _build_source((_AAPL,), asreported_file=_AAPL_2023_AR, normalized_file=_AAPL_2023_NO)
    )
    results = _by_name(
        check_build_panel_uses_exchange_calendar(
            view,
            year=2023,
            month=12,
            expected_trading_month_end="2023-12-29",
            fundamental_fields=("revenue",),
        )
    )
    assert results["build_panel_uses_exchange_calendar"].passed is True


def test_layer_b_corporate_action_adjustment_correct_aapl_split() -> None:
    view = PointInTimeView(_build_source((_AAPL,)))
    results = _by_name(
        check_corporate_action_adjustment_correct(
            view,
            stock_id=_AAPL,
            as_of="2020-09-05",
            query_start="2020-08-20",
            query_end="2020-09-05",
            action_date=_AAPL_SPLIT_DATE,
            # The ONE externally documented true economic return for the
            # 4-for-1 split date: 4 * 129.04 / 499.23 - 1. Deliberately not
            # recomputed via compute_adjusted_returns (anti-tautology).
            expected_true_return=_AAPL_SPLIT_TRUE_RETURN,
        )
    )
    assert results["corporate_action_adjustment_correct"].passed is True


# ---------------------------------------------------------------------------
# Layer A -- extra considered checks (reported for completeness)
# ---------------------------------------------------------------------------
def test_trading_status_flag_is_date_specific_twtr_zero_volume() -> None:
    """TWTR's terminal zero-volume day is flagged (and only that day).

    ``is_zero_volume`` is the only trading-status flag this US-equity
    adapter provides (there is no suspension/limit/ST source); the check's
    ``flag_column`` parameter makes it usable unchanged.
    """
    results = _by_name(
        check_trading_status_flag_is_date_specific(
            _build_source(),
            stock_id=_TWTR,
            flag_column="is_zero_volume",
            expected_true_date="2022-10-28",
            adjacent_dates=["2022-10-27", "2022-10-26"],
        )
    )
    assert results["trading_status_flag_is_date_specific"].passed is True


# ---------------------------------------------------------------------------
# Semantic lines that must be test-backed, not just prose
# ---------------------------------------------------------------------------
def test_identifier_continuity_is_ticker_fallback() -> None:
    """``IDENTIFIER CONTINUITY = NOT CERTIFIED`` is backed by real code.

    ``TiingoPITSource`` wires only ``get_meta`` -> ``resolve_stock_id``;
    ``permaTicker`` lives on a different endpoint it never calls, so every
    configured ticker resolves via the mutable-ticker fallback.
    """
    from smart_beta.vendors.tiingo.identifiers import resolve_stock_id

    for ticker, filename in ((_AAPL, _AAPL_META), (_TWTR, _TWTR_META)):
        resolved = resolve_stock_id(_body(filename))
        assert resolved.stock_id == ticker
        assert resolved.is_permanent is False
        assert resolved.source_field == "ticker"


def test_float_market_cap_is_approximation() -> None:
    """``FLOAT MARKET CAP = NOT CERTIFIED`` is backed by the real adapter:
    ``get_market_cap`` emits ``float_mcap == total_mcap`` by design."""
    from smart_beta.pit.schema import FLOAT_MARKET_CAP_COL, TOTAL_MARKET_CAP_COL
    from smart_beta.vendors.tiingo.returns_and_market_cap import (
        FLOAT_MARKET_CAP_IS_APPROXIMATED,
    )

    assert FLOAT_MARKET_CAP_IS_APPROXIMATED is True
    frame = _build_source((_AAPL,)).get_market_cap("2024-01-02", "2024-01-05")
    assert not frame.empty
    assert (frame[FLOAT_MARKET_CAP_COL] == frame[TOTAL_MARKET_CAP_COL]).all()


# ---------------------------------------------------------------------------
# Future-announcement gating (genuine AAPL specimen)
# ---------------------------------------------------------------------------
def test_future_announcement_not_visible_aapl_q3_2026() -> None:
    """A real AAPL fact is invisible before its knowledge_date and visible
    at it.

    Specimen: report_period_end 2026-06-27, knowledge_date 2026-07-31
    (as-reported filing date), revenue 109,417,000,000.
    """
    view = PointInTimeView(_build_source((_AAPL,)))
    results = _by_name(
        check_future_announcement_not_visible(
            view,
            stock_id=_AAPL,
            report_period_end="2026-06-27",
            field="revenue",
            knowledge_date="2026-07-31",
            before_date="2026-07-01",
            query_start="2026-01-01",
            query_end="2026-12-31",
            expected_value=109_417_000_000.0,
        )
    )
    assert results["future_announcement_not_visible"].passed is True
    assert results["future_announcement_visible_at_knowledge_date"].passed is True


# ---------------------------------------------------------------------------
# The two restatement/vintage checks are deliberately NOT invoked
# ---------------------------------------------------------------------------
def _executed_check_names() -> set[str]:
    """Run every check this task actually calls and collect result names."""
    aapl = _build_source((_AAPL,))
    two = _build_source()
    view_a = PointInTimeView(aapl)
    view_two = PointInTimeView(two)
    names: set[str] = set()
    layers = [
        check_schema_conformance(aapl, "2020-01-01", "2026-12-31", _FIELDS),
        check_no_shared_mutable_state(aapl, "2020-01-01", "2026-12-31", _FIELDS),
        check_deterministic_results(aapl, "2020-01-01", "2026-12-31", _FIELDS),
        check_delisted_security_history_present(
            two,
            stock_id=_TWTR,
            expected_last_date=_TWTR_LAST_EOD_DATE,
            query_start="2022-10-20",
            query_end="2022-10-28",
        ),
        check_exchange_calendar_recognizes_holiday(two, holiday="2021-01-01"),
        check_exchange_calendar_month_end(
            two, year=2019, month=3, expected_trading_month_end="2019-03-29"
        ),
        check_corporate_action_raw_facts(
            aapl,
            stock_id=_AAPL,
            effective_date=_AAPL_SPLIT_DATE,
            expected_adjustment_factor=_AAPL_SPLIT_FACTOR,
            raw_return_date=_AAPL_SPLIT_DATE,
            expected_raw_return=_AAPL_SPLIT_RAW_RETURN,
        ),
        check_survivorship_through_view(
            view_two,
            stock_id=_TWTR,
            as_of="2022-10-28",
            query_start="2022-10-20",
            query_end="2022-10-28",
        ),
        check_build_panel_uses_exchange_calendar(
            PointInTimeView(
                _build_source(
                    (_AAPL,),
                    asreported_file=_AAPL_2023_AR,
                    normalized_file=_AAPL_2023_NO,
                )
            ),
            year=2023,
            month=12,
            expected_trading_month_end="2023-12-29",
            fundamental_fields=("revenue",),
        ),
        check_corporate_action_adjustment_correct(
            view_a,
            stock_id=_AAPL,
            as_of="2020-09-05",
            query_start="2020-08-20",
            query_end="2020-09-05",
            action_date=_AAPL_SPLIT_DATE,
            expected_true_return=_AAPL_SPLIT_TRUE_RETURN,
        ),
        check_future_announcement_not_visible(
            view_a,
            stock_id=_AAPL,
            report_period_end="2026-06-27",
            field="revenue",
            knowledge_date="2026-07-31",
            before_date="2026-07-01",
            query_start="2026-01-01",
            query_end="2026-12-31",
            expected_value=109_417_000_000.0,
        ),
        check_trading_status_flag_is_date_specific(
            two,
            stock_id=_TWTR,
            flag_column="is_zero_volume",
            expected_true_date="2022-10-28",
            adjacent_dates=["2022-10-27", "2022-10-26"],
        ),
    ]
    for layer in layers:
        for result in layer:
            names.add(result.name)
    return names


def test_restatement_vintage_reconstruction_not_invoked() -> None:
    """A deliberate, explicit test (not merely an omission): this file never
    invokes the two checks that require multi-vintage evidence against
    ``TiingoPITSource``.

    If a future edit quietly adds either check back, its result name appears
    in ``_executed_check_names()`` and this test fails.
    """
    names = _executed_check_names()
    for forbidden in _FORBIDDEN_CHECK_NAMES:
        assert not any(
            name == forbidden or name.startswith(forbidden) for name in names
        ), f"{forbidden} was invoked against TiingoPITSource"

    # The report must acknowledge them (the completeness invariant), but with
    # a NOT CERTIFIED disposition, never a runtime result.
    document = _REPORT_PATH.read_text(encoding="utf-8")
    for function_name in (
        "check_fundamentals_vintages_preserved",
        "check_restatement_not_backfilled",
    ):
        assert function_name in document


# ---------------------------------------------------------------------------
# The report covers every considered check
# ---------------------------------------------------------------------------
def test_certification_report_covers_every_considered_check() -> None:
    """Completeness invariant: every check considered relevant in principle
    has an explicit disposition line in the checked-in report -- no silent
    omissions, for any reason."""
    assert _REPORT_PATH.is_file(), f"missing certification report: {_REPORT_PATH}"
    document = _REPORT_PATH.read_text(encoding="utf-8")
    lines = document.splitlines()

    for function_name in CONSIDERED_CHECKS:
        matching = [line for line in lines if function_name in line]
        assert matching, f"{function_name} is absent from the certification report"
        assert any(
            any(token in line for token in _DISPOSITION_TOKENS)
            for line in matching
        ), f"{function_name} has no explicit PASS/FAIL/NOT CERTIFIED/NOT RUN disposition"

    # The six required named lines (four unconditional + ranged-query; the
    # dividend line is conditional and correctly omitted -- see below).
    for required_line in (
        "RESTATEMENT/VINTAGE RECONSTRUCTION = NOT CERTIFIED",
        "IDENTIFIER CONTINUITY = NOT CERTIFIED",
        "FLOAT MARKET CAP = NOT CERTIFIED",
        "TWTR DELISTING CORROBORATION = NOT CERTIFIED",
        "FUNDAMENTALS RANGED-QUERY SEMANTICS = NOT CERTIFIED",
    ):
        assert required_line in document, f"missing report line: {required_line!r}"

    # The report must also carry the deferred-scope list.
    for deferred in (
        "SEC/XBRL",
        "compositing",
        "Phase 4C",
        "Phase 4D",
    ):
        assert deferred in document, f"missing deferred-scope item: {deferred!r}"


def test_dividend_semantics_line_is_correctly_omitted_or_present() -> None:
    """The DIVIDEND ADJUSTMENT SEMANTICS line is conditional.

    Re-verify P4B-5's merged constant: ``DIVIDEND_SEMANTICS_CERTIFIED`` is
    ``True`` (real Nasdaq + SEC 8-K cross-check), so the report must NOT
    claim it is NOT CERTIFIED; it must instead state that the omission is
    deliberate.
    """
    from smart_beta.vendors.tiingo import corporate_actions

    document = _REPORT_PATH.read_text(encoding="utf-8")
    if corporate_actions.DIVIDEND_SEMANTICS_CERTIFIED:
        assert "DIVIDEND ADJUSTMENT SEMANTICS = NOT CERTIFIED" not in document
        assert "DIVIDEND_SEMANTICS_CERTIFIED" in document
    else:
        assert "DIVIDEND ADJUSTMENT SEMANTICS = NOT CERTIFIED" in document


# ---------------------------------------------------------------------------
# Fundamentals ranged-query semantics (the one deliberate live test)
# ---------------------------------------------------------------------------
def test_fundamentals_ranged_query_semantics() -> None:
    """Verify live whether Tiingo's date-bounded statements endpoints return a
    mutually reconcilable fiscal-identity subset.

    Findings (real live calls, AAPL; see the certification report):

    * A range bounded to periods with coverage on both endpoints
      (``2026-03-01..2026-07-31``) reconciles: 2026 Q3 and Q2 are present in
      both the as-reported and normalized responses.
    * A full calendar-year range (``2026-01-01..2026-12-31``) does NOT: the
      as-reported response carries 2026 Q1, whose normalized period-end
      record does not exist, so the fail-closed reconciliation correctly
      raises. The adapter must never weaken that behavior.

    Skipped, with a stated reason, only when no key is available or the live
    call cannot complete. The key itself is never printed or persisted.
    """
    if not os.environ.get(API_KEY_ENV_VAR):
        pytest.skip(
            f"{API_KEY_ENV_VAR} not set; live ranged-query verification not attempted"
        )

    client = TiingoClient()
    try:
        ar_bounded = client.get_fundamentals_asreported(
            _AAPL, "2026-03-01", "2026-07-31"
        )
        no_bounded = client.get_fundamentals_normalized(
            _AAPL, "2026-03-01", "2026-07-31"
        )
        ar_full = client.get_fundamentals_asreported(
            _AAPL, "2026-01-01", "2026-12-31"
        )
        no_full = client.get_fundamentals_normalized(
            _AAPL, "2026-01-01", "2026-12-31"
        )
    except (TiingoAPIError, OSError) as exc:
        pytest.skip(
            "live Tiingo ranged-query verification could not complete: "
            f"{type(exc).__name__}"
        )

    # Coverage-consistent range: every as-reported statement reconciles.
    reconciles = {
        (statement["year"], statement["quarter"]) for statement in ar_bounded
    }
    assert reconciles == {(2026, 3), (2026, 2)}
    frame = map_asreported_to_fundamentals(
        _AAPL, ar_bounded, no_bounded, ["revenue"]
    )
    assert len(frame) == 2
    assert set(frame["report_period_end"].astype(str)) == {
        "2026-06-27",
        "2026-03-28",
    }

    # Full-year range: 2026 Q1 as-reported has no normalized match, so the
    # fail-closed mapper aborts the whole batch. This is the finding, not a
    # defect to route around.
    full_identities = {
        (statement["year"], statement["quarter"]) for statement in ar_full
    }
    assert (2026, 1) in full_identities
    with pytest.raises(FiscalPeriodReconciliationError):
        map_asreported_to_fundamentals(_AAPL, ar_full, no_full, ["revenue"])


def test_ranged_query_evidence_fixtures_match_live_findings() -> None:
    """The captured live evidence fixtures reproduce the same reconciliation
    outcomes offline, so the report's claim is checkable without a key."""
    ar_bounded = _body(_AAPL_2026_AR)
    no_bounded = _body(_AAPL_2026_NO)
    ar_full = _body(_AAPL_FULL_AR)
    no_full = _body(_AAPL_FULL_NO)

    assert len(
        map_asreported_to_fundamentals(_AAPL, ar_bounded, no_bounded, ["revenue"])
    ) == 2
    with pytest.raises(FiscalPeriodReconciliationError):
        map_asreported_to_fundamentals(_AAPL, ar_full, no_full, ["revenue"])
