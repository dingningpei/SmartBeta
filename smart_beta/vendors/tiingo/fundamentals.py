"""Tiingo as-reported fundamentals mapping (Phase 4B, P4B-6).

This module is the only place that turns Tiingo statement JSON into
``FUNDAMENTALS_FACT_SCHEMA`` rows. It produces raw facts only: it never
calls or reimplements :func:`smart_beta.pit.fundamentals.latest_known_value`.

Two Tiingo responses, two roles
-------------------------------
``get_fundamentals_asreported`` supplies canonical **fact values**
(``statementData`` line items) and ``knowledge_date`` (its ``date``
field -- the filing/publication date). ``get_fundamentals_normalized``
supplies ``report_period_end`` **lookup values only** (its ``date``
field -- the fiscal period end). Normalized ``statementData`` is never
emitted as a fact.

Reconciliation is by **fiscal identity**: the real Tiingo fields
``year`` and ``quarter`` (confirmed against the live AAPL capture;
there is no ``fiscalYear`` / ``fiscalQuarter``). Never by date, never
by position/order, never by calendar-quarter arithmetic. AAPL's fiscal
Q3 2026 ends **2026-06-27**, not 2026-06-30; deriving a period end from
``(year, quarter)`` is forbidden.

Fail-closed invariant
---------------------
Exactly one normalized match is required for every asReported
statement. Zero matches or more than one match raises
:class:`FiscalPeriodReconciliationError`. Unmatched periods are never
silently dropped, never inferred, never forward/back-filled, and an
arbitrary duplicate is never chosen. The asReported ``date`` is never
returned as ``report_period_end`` -- that field is ``knowledge_date``.

If *any* statement in a batch fails to reconcile, the whole
:func:`map_asreported_to_fundamentals` call aborts. A partial frame
that omitted just the bad period is the silent-drop anti-pattern
relocated one layer up.

Restatement status
------------------
``is_restatement`` is emitted as ``False`` on every row because
``FUNDAMENTALS_FACT_SCHEMA`` requires a bool and Tiingo's ``asReported``
endpoint, under current plan-tier access, returns at most one vintage
per fact. False != verified non-restated; restatement status remains NOT CERTIFIED.

Derived-field exclusion
-----------------------
Vendor-computed fields (Piotroski F-score, margins, QoQ changes, and
the rest of Tiingo's ``overview`` section) can change retroactively
with no ``knowledge_date``. They are structurally unreachable: only
names in :data:`RAW_LINE_ITEM_FIELDS` (genuine balance-sheet, income-
statement, and cash-flow line items from the captured AAPL
``statementData``) can ever be emitted, even if a caller asks for a
derived name in ``fields``.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from smart_beta.pit.schema import (
    FIELD_COL,
    FUNDAMENTALS_FACT_SCHEMA,
    IS_RESTATEMENT_COL,
    KNOWLEDGE_DATE_COL,
    REPORT_PERIOD_END_COL,
    STOCK_COL,
    VALUE_COL,
    validate_panel,
)

# Empirically confirmed Tiingo fiscal-identity field names (AAPL live
# capture: top-level ``year`` and ``quarter``, not ``fiscalYear``).
FISCAL_YEAR_FIELD = "year"
FISCAL_QUARTER_FIELD = "quarter"

# asReported ``date`` -> knowledge_date; normalized ``date`` ->
# report_period_end lookup only. Never swapped.
_DATE_FIELD = "date"
_STATEMENT_DATA_FIELD = "statementData"
_DATA_CODE_FIELD = "dataCode"
_VALUE_FIELD = "value"

# Statement sections that hold genuine reported line items. ``overview``
# is excluded here as well as by name, so a derived metric cannot sneak
# in by being requested.
_RAW_STATEMENT_SECTIONS: tuple[str, ...] = (
    "balanceSheet",
    "incomeStatement",
    "cashFlow",
)

# Genuine reported line items observed on the captured AAPL asReported
# ``statementData`` (balance sheet, income statement, cash flow). Names
# outside this set -- including every ``overview`` metric -- are
# unreachable.
RAW_LINE_ITEM_FIELDS: frozenset[str] = frozenset(
    {
        # balanceSheet
        "accoci",
        "acctPay",
        "acctRec",
        "assetsCurrent",
        "assetsNonCurrent",
        "cashAndEq",
        "debt",
        "debtCurrent",
        "debtNonCurrent",
        "deferredRev",
        "deposits",
        "equity",
        "intangibles",
        "inventory",
        "investments",
        "investmentsCurrent",
        "investmentsNonCurrent",
        "liabilitiesCurrent",
        "liabilitiesNonCurrent",
        "ppeq",
        "retainedEarnings",
        "sharesBasic",
        "taxAssets",
        "taxLiabilities",
        "totalAssets",
        "totalLiabilities",
        # incomeStatement
        "consolidatedIncome",
        "costRev",
        "ebit",
        "ebitda",
        "ebt",
        "eps",
        "epsDil",
        "grossProfit",
        "intexp",
        "netIncComStock",
        "netIncDiscOps",
        "netinc",
        "nonControllingInterests",
        "opex",
        "opinc",
        "prefDVDs",
        "revenue",
        "rnd",
        "sga",
        "shareswa",
        "shareswaDil",
        "taxExp",
        # cashFlow
        "businessAcqDisposals",
        "capex",
        "depamor",
        "freeCashFlow",
        "investmentsAcqDisposals",
        "issrepayDebt",
        "issrepayEquity",
        "ncf",
        "ncff",
        "ncfi",
        "ncfo",
        "ncfx",
        "payDiv",
        "sbcomp",
    }
)

# Named derived/computed fields that must remain unreachable. Kept as
# documentation and as a belt-and-suspenders exclusion even if a caller
# asks for them; the allowlist above is the actual gate.
DERIVED_FIELDS: frozenset[str] = frozenset(
    {
        "bookVal",
        "bvps",
        "currentRatio",
        "debtEquity",
        "epsQoQ",
        "grossMargin",
        "longTermDebtEquity",
        "piotroskiFScore",
        "profitMargin",
        "revenueQoQ",
        "roa",
        "roe",
        "rps",
        "shareFactor",
    }
)

_SOURCE_VENDOR_COL = "_source_vendor"
_SOURCE_ENDPOINT_COL = "_source_endpoint"
_INGESTED_AT_COL = "_ingested_at"
_PROVENANCE_VENDOR = "tiingo"
_PROVENANCE_ENDPOINT = "get_fundamentals_asreported"

_OUTPUT_COLUMNS: tuple[str, ...] = (
    STOCK_COL,
    REPORT_PERIOD_END_COL,
    FIELD_COL,
    KNOWLEDGE_DATE_COL,
    VALUE_COL,
    IS_RESTATEMENT_COL,
    _SOURCE_VENDOR_COL,
    _SOURCE_ENDPOINT_COL,
    _INGESTED_AT_COL,
)


class FiscalPeriodReconciliationError(Exception):
    """Raised when reconciling one asReported statement to its
    report_period_end does not yield exactly one match in the
    normalized response."""


def _fiscal_identity(statement: dict) -> tuple[object, object]:
    """Return ``(year, quarter)`` from a Tiingo statement dict.

    Missing identity fields fail closed rather than matching by accident
    or falling back to a date.
    """
    if FISCAL_YEAR_FIELD not in statement or FISCAL_QUARTER_FIELD not in statement:
        raise FiscalPeriodReconciliationError(
            "statement is missing fiscal-identity fields "
            f"{FISCAL_YEAR_FIELD!r}/{FISCAL_QUARTER_FIELD!r}; "
            f"keys={sorted(statement)!r}"
        )
    return statement[FISCAL_YEAR_FIELD], statement[FISCAL_QUARTER_FIELD]


def reconcile_report_period_end(
    as_reported_statement: dict,
    normalized_statements: list[dict],
) -> pd.Timestamp:
    """Find every entry in ``normalized_statements`` whose fiscal identity
    (fiscal year + fiscal quarter) matches ``as_reported_statement``'s own
    fiscal identity.

    count == 1  -> return that one entry's ``date`` field as report_period_end.
    count == 0  -> raise FiscalPeriodReconciliationError. NEVER silently
                   drop this statement, NEVER infer a calendar-quarter-end
                   date, NEVER forward/back-fill from an adjacent period.
    count > 1   -> raise FiscalPeriodReconciliationError. NEVER choose an
                   arbitrary duplicate.

    NEVER return as_reported_statement's own ``date`` field here -- that
    field is knowledge_date, and using it as report_period_end is exactly
    the "use knowledge_date as report_period_end" mistake this function
    exists to make impossible.
    """
    identity = _fiscal_identity(as_reported_statement)
    matches = [
        entry
        for entry in normalized_statements
        if FISCAL_YEAR_FIELD in entry
        and FISCAL_QUARTER_FIELD in entry
        and (entry[FISCAL_YEAR_FIELD], entry[FISCAL_QUARTER_FIELD]) == identity
    ]
    match_count = len(matches)
    if match_count != 1:
        year, quarter = identity
        raise FiscalPeriodReconciliationError(
            "reconciling asReported statement to report_period_end requires "
            f"exactly one normalized match; got {match_count} for "
            f"{FISCAL_YEAR_FIELD}={year!r}, {FISCAL_QUARTER_FIELD}={quarter!r}. "
            "Refusing to drop, infer, forward/back-fill, or pick a duplicate."
        )
    match = matches[0]
    if _DATE_FIELD not in match:
        year, quarter = identity
        raise FiscalPeriodReconciliationError(
            "normalized match for "
            f"{FISCAL_YEAR_FIELD}={year!r}, {FISCAL_QUARTER_FIELD}={quarter!r} "
            f"has no {_DATE_FIELD!r} field; refusing to substitute "
            "asReported knowledge_date as report_period_end"
        )
    # Taken from the normalized match only. The asReported ``date`` is
    # never consulted here.
    return pd.Timestamp(match[_DATE_FIELD])


def _line_items(statement: dict) -> dict[str, float]:
    """Flatten genuine reported line items from ``statementData``.

    Only ``balanceSheet`` / ``incomeStatement`` / ``cashFlow`` are read.
    ``overview`` (derived metrics) is not visited. First occurrence of a
    ``dataCode`` wins; values of ``None`` are omitted.
    """
    statement_data = statement.get(_STATEMENT_DATA_FIELD)
    if not isinstance(statement_data, dict):
        return {}
    items: dict[str, float] = {}
    for section in _RAW_STATEMENT_SECTIONS:
        entries = statement_data.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            code = entry.get(_DATA_CODE_FIELD)
            value = entry.get(_VALUE_FIELD)
            if not isinstance(code, str) or value is None:
                continue
            if code in items:
                continue
            items[code] = float(value)
    return items


def _empty_frame() -> pd.DataFrame:
    """Schema-conforming empty result, including provenance columns."""
    return pd.DataFrame(
        {
            STOCK_COL: pd.Series(dtype="string"),
            REPORT_PERIOD_END_COL: pd.Series(dtype="datetime64[ns]"),
            FIELD_COL: pd.Series(dtype="string"),
            KNOWLEDGE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
            VALUE_COL: pd.Series(dtype="float64"),
            IS_RESTATEMENT_COL: pd.Series(dtype="bool"),
            _SOURCE_VENDOR_COL: pd.Series(dtype="string"),
            _SOURCE_ENDPOINT_COL: pd.Series(dtype="string"),
            _INGESTED_AT_COL: pd.Series(dtype="datetime64[ns]"),
        }
    )


def map_asreported_to_fundamentals(
    stock_id: str,
    as_reported_statements: list[dict],
    normalized_statements: list[dict],
    fields: Sequence[str],
) -> pd.DataFrame:
    """FUNDAMENTALS_FACT_SCHEMA-conforming, plus provenance columns.

    For every statement in as_reported_statements: call
    reconcile_report_period_end. If it raises for ANY statement in the
    batch, let the exception propagate and abort the WHOLE call -- do not
    catch it, skip that one statement, and return a partial frame missing
    just the bad row. A partial result that silently omits one unreconciled
    period IS the "silently drop the fiscal period" anti-pattern this
    policy forbids, just relocated one layer up. Fail the whole batch,
    loudly, every time.

    For each successfully reconciled statement, emit one row per requested
    field present in ``statementData`` (restricted to ``fields``, and further
    restricted to :data:`RAW_LINE_ITEM_FIELDS`). Derived/computed names
    such as ``piotroskiFScore`` are unreachable even when explicitly
    requested.

    is_restatement = False for every row because FUNDAMENTALS_FACT_SCHEMA
    requires a bool and Tiingo currently returns at most one vintage per
    fact. False != verified non-restated; restatement status remains NOT CERTIFIED.
    This is a schema-constrained placeholder, not a claim that the fact
    has been verified as non-restated.
    """
    # Reconcile the entire batch first. Any failure aborts before a single
    # output row is built, so there is no partial frame to return.
    reconciled: list[tuple[dict, pd.Timestamp]] = []
    for statement in as_reported_statements:
        period_end = reconcile_report_period_end(statement, normalized_statements)
        reconciled.append((statement, period_end))

    requested = [name for name in dict.fromkeys(fields) if name in RAW_LINE_ITEM_FIELDS]
    ingested_at = pd.Timestamp.now(tz="UTC").tz_localize(None)

    records: list[dict] = []
    for statement, period_end in reconciled:
        knowledge_date = pd.Timestamp(statement[_DATE_FIELD])
        items = _line_items(statement)
        for field_name in requested:
            if field_name not in items:
                continue
            records.append(
                {
                    STOCK_COL: stock_id,
                    REPORT_PERIOD_END_COL: period_end,
                    FIELD_COL: field_name,
                    KNOWLEDGE_DATE_COL: knowledge_date,
                    VALUE_COL: items[field_name],
                    # Schema-constrained placeholder, not a verified claim.
                    IS_RESTATEMENT_COL: False,
                    _SOURCE_VENDOR_COL: _PROVENANCE_VENDOR,
                    _SOURCE_ENDPOINT_COL: _PROVENANCE_ENDPOINT,
                    _INGESTED_AT_COL: ingested_at,
                }
            )

    if not records:
        frame = _empty_frame()
    else:
        frame = pd.DataFrame.from_records(records, columns=_OUTPUT_COLUMNS)
        frame[STOCK_COL] = frame[STOCK_COL].astype("string")
        frame[FIELD_COL] = frame[FIELD_COL].astype("string")
        frame[REPORT_PERIOD_END_COL] = pd.to_datetime(frame[REPORT_PERIOD_END_COL])
        frame[KNOWLEDGE_DATE_COL] = pd.to_datetime(frame[KNOWLEDGE_DATE_COL])
        frame[VALUE_COL] = frame[VALUE_COL].astype("float64")
        frame[IS_RESTATEMENT_COL] = frame[IS_RESTATEMENT_COL].astype("bool")
        frame[_SOURCE_VENDOR_COL] = frame[_SOURCE_VENDOR_COL].astype("string")
        frame[_SOURCE_ENDPOINT_COL] = frame[_SOURCE_ENDPOINT_COL].astype("string")
        frame[_INGESTED_AT_COL] = pd.to_datetime(frame[_INGESTED_AT_COL])

    validate_panel(frame, FUNDAMENTALS_FACT_SCHEMA, name="tiingo_fundamentals")
    return frame


__all__ = [
    "DERIVED_FIELDS",
    "FISCAL_QUARTER_FIELD",
    "FISCAL_YEAR_FIELD",
    "FiscalPeriodReconciliationError",
    "RAW_LINE_ITEM_FIELDS",
    "map_asreported_to_fundamentals",
    "reconcile_report_period_end",
]
