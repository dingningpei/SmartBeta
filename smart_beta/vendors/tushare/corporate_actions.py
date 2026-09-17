"""Map Tushare ``dividend`` / ``adj_factor`` data into
``CORPORATE_ACTIONS_SCHEMA``.

This is a raw-facts-only adapter (Phase 4D-B, task P4DB-5). It never calls
:func:`smart_beta.pit.corporate_actions.compute_adjusted_returns`, never
reimplements it, and never adjusts a return itself. Adjustment remains
exclusively Phase 3's job; this module only emits the ``adjustment_factor``
values that function later multiplies. It performs no I/O: every function
takes already-fetched rows and returns a panel.

Provenance of the specimens this module is built against
--------------------------------------------------------
Two evidence classes appear in the test fixtures and must never be
conflated (the Phase 4D-B discipline, restated):

* **Proxy-observed live evidence.** All fixtures under
  ``tests/fixtures/tushare/corporate_actions/`` were recorded through the
  third-party proxy ``pcd.mobcvb.cn/tushare/pro`` (``X-API-Key`` auth) on
  2026-09-17, three identical samples per request, with the P4DB-1
  canonical-consistency check enforcing that the non-empty payloads agreed.
  They are the vendor's real data *as the proxy delivered it*.
* **Direct-official-Tushare behavior is NOT observed or certified here.**
  Nothing in this module or its tests may be read as evidence about the
  paid official Tushare API. ``map_adj_factor_to_corporate_actions``'
  cross-check below is a check against proxy-observed vendor data, not
  against the official service.

The field names and endpoint semantics documented here are Tushare's
(upstream-documented); the *values* are proxy-observed. Where this module
asserts a formula, the formula is frozen below and the tests check it
against the proxy-observed numbers, not assumed from documentation.

``dividend`` row semantics (upstream-documented, proxy-confirmed)
-----------------------------------------------------------------
Fields used: ``ts_code``, ``end_date``, ``ann_date``, ``div_proc``,
``stk_div``, ``cash_div``, ``cash_div_tax``, ``record_date``, ``ex_date``.
Tushare returns **several rows per fiscal ``end_date``**, one per
announcement/process stage: ``预披露``, ``预案``, ``股东大会通过``, and
``实施``. Only ``div_proc == "实施"`` rows are implemented, executable
corporate actions carrying a real ``ex_date``; the proposal/approval rows
usually carry ``ex_date=None`` and placeholder ``cash_div`` values and are
**not facts** (e.g. the 2023-08-24 ``cash_div=0.0``/``None`` rows the task
names). This module emits only implemented rows.

Many implemented events are returned as **two rows with identical terms
but different ``ann_date``** (the proxy-observed
``000001.SZ``/``end_date=20121231`` specimen has ``ann_date=20130524`` and
``ann_date=20130308``, both ``div_proc=实施``, both ``stk_div=0.6``,
``cash_div=0.1315``, ``cash_div_tax=0.17``, ``ex_date=20130620``; see the
``knowledge_date`` policy below). The two rows differ *only* in
``ann_date``; the terms are identical, so this is a repeated announcement
of one fact, not an amendment.

``knowledge_date`` policy (frozen)
----------------------------------
For every emitted action,

    knowledge_date = the earliest ``ann_date`` among the implemented rows
    carrying the same terms ``(ts_code, ex_date, stk_div, cash basis)``.

Rationale and evidence: the 2012/2013 specimen's two implemented rows have
byte-identical economics and differ only in ``ann_date``; the earlier date
(``20130308``) is the first announcement on which those exact terms were
fixable, so a researcher could have known the whole fact then. Using the
later ``20130524`` would throw away a real, PIT-valid knowledge window and
is not required by anything in the data. Both candidate dates precede
``ex_date=20130620``. The same repeated-announcement shape was confirmed
across nineteen further implemented events in the same real history
(e.g. 2014-06-12 ``20140307``/``20140523``; 2015-04-13
``20150313``/``20150403``), and in no observed event did the two rows
disagree on the normalized terms (share ratio and pre-tax cash basis; the
only variance seen was ``cash_div_tax`` null vs ``0.0`` on the 1996-05-27
event, equivalent after null-normalization). If a future event ever
carried *different* terms on the
same ``ex_date``, the term sets are emitted as separate rows with their
own (different) ``knowledge_date`` values, which the schema key already
allows; no such case exists in the proxy history recorded here.

``is_superseded = False`` for every emitted row: the proxy's ``dividend``
payload exposes no vendor-asserted amendment/withdrawal marker among
implemented rows, and no differing-term specimen was observed. ``False``
therefore means "no evidence of supersession", never "verified final".

Action-type enum (frozen, closed)
---------------------------------
The three values mirror Phase 4B's Tiingo precedent
(:mod:`smart_beta.vendors.tiingo.corporate_actions` uses ``"split"`` for a
share-count change and ``"dividend"`` for cash) and add one combined value
because Tushare reports the stock and cash parts of one ex-date together
and the frozen factor below is a single combined multiplier (it is *not*
the product of a separate split factor and dividend factor):

======================  ==========================================
``action_type``         condition
======================  ==========================================
``"split"``             ``stk_div > 0`` and no cash
``"dividend"``          ``stk_div == 0`` and cash > 0
``"split_dividend"``    ``stk_div > 0`` and cash > 0
======================  ==========================================

``map_adj_factor_to_corporate_actions`` uses one further value,
``"adj_factor_step"``, because an ``adj_factor`` step carries no
information about whether the underlying event was stock, cash, or both.
Its output is an **independent event reconstruction for cross-checking
only**: it must never be concatenated with
``map_dividend_to_corporate_actions``' output, because that would
double-count the same events.

Frozen ``adjustment_factor`` formula
------------------------------------
Let, for one implemented dividend event:

* ``stk_div`` -- share bonus/transfer per existing share (Tushare
  ``stk_div``);
* ``cash_div_tax`` -- pre-tax cash dividend per share, preferring
  Tushare's ``cash_div_tax`` and falling back to ``cash_div`` when
  ``cash_div_tax`` is null (the fixed spec intentionally records both);
* ``P_prev`` -- the raw close on the trading day immediately before
  ``ex_date`` (equivalently, the previous trading day's ``daily.close``).

The ex-rights reference price the exchange/vendor uses is

    P_ex_ref = (P_prev - cash_div_tax) / (1 + stk_div)

and the frozen factor is the multiplier that makes Phase 3's
``adjusted_ret = (1 + raw_ret) * factor - 1`` remove the mechanical
ex-rights price drop:

    adjustment_factor = P_prev / P_ex_ref
                      = (1 + stk_div) * P_prev / (P_prev - cash_div_tax)

Why ``cash_div_tax`` (pre-tax) rather than ``cash_div``: the observed
``ex_date`` reference price is computed from the pre-tax dividend.
For the 2013 specimen ``P_prev = 19.24``, ``cash_div_tax = 0.17``,
``stk_div = 0.6`` gives ``P_ex_ref = 11.91875`` -> rounded to the real
``daily.pre_close = 11.92`` on 2013-06-20, whereas the after-tax
``cash_div = 0.1315`` would give ``11.9428 -> 11.94``, which the vendor
did not use. The same pre-tax basis reconciles the pure-cash 2012
specimen (``P_prev = 13.51``, ``cash_div_tax = 0.1`` -> ``13.41``, the
real ``pre_close`` on 2012-10-19).

Degenerate cases (frozen):

* No cash (``cash_div_tax`` null or ``0``): ``adjustment_factor =
  1 + stk_div`` exactly -- the price-free share-count factor.
* ``P_prev`` not supplied by the caller: the cash component cannot be
  evaluated from the ``dividend`` payload alone, so the documented
  fallback is the price-free ``1 + stk_div`` and the emitted row carries
  ``_cash_adjustment_included = False``. A pure-cash row therefore has
  fallback factor ``1.0`` -- deliberately *no* spurious split factor.
* ``P_prev <= cash_div_tax`` (or non-positive): refuses with
  :class:`ValueError` rather than emitting a nonsensical/negative factor.

Cross-check against the vendor's own ``adj_factor`` (proxy-observed)
--------------------------------------------------------------------
``adj_factor`` steps on the ``ex_date``; the observed ratio for
2013-06-20 is ``58.387 / 36.173 = 1.61407``, against the frozen formula's
``(1.6 * 19.24) / (19.24 - 0.17) = 1.61426`` (relative difference
~0.01%, within the tests' documented 0.1% tolerance), and against the
raw-price reconciliation ``19.24 / 11.92 = 1.61409``. For the pure-cash
2012-10-19 event the observed ratio ``36.173 / 35.906 = 1.00744`` matches
``13.51 / 13.41 = 1.00746``. This is proxy-observed corroboration, not a
certification of direct official-Tushare behavior.

``map_adj_factor_to_corporate_actions``
---------------------------------------
Input fields: ``ts_code``, ``trade_date``, ``adj_factor``. Rows are sorted
per stock by ``trade_date``; wherever consecutive factors differ, one
action is emitted at the later ``trade_date`` with
``adjustment_factor = later / earlier``. ``knowledge_date =
effective_date`` because the endpoint carries no announcement date.
"""
from __future__ import annotations

from typing import Mapping, Sequence

import pandas as pd

from smart_beta.pit.schema import (
    ACTION_TYPE_COL,
    ADJUSTMENT_FACTOR_COL,
    CORPORATE_ACTIONS_SCHEMA,
    EFFECTIVE_DATE_COL,
    IS_SUPERSEDED_COL,
    KNOWLEDGE_DATE_COL,
    STOCK_COL,
    validate_panel,
)

# --- ``dividend`` field names (Tushare-documented, proxy-confirmed) -------
TS_CODE_FIELD = "ts_code"
DIV_PROC_FIELD = "div_proc"
ANN_DATE_FIELD = "ann_date"
STK_DIV_FIELD = "stk_div"
CASH_DIV_FIELD = "cash_div"
CASH_DIV_TAX_FIELD = "cash_div_tax"
EX_DATE_FIELD = "ex_date"

# --- ``adj_factor`` field names ------------------------------------------
ADJ_TRADE_DATE_FIELD = "trade_date"
ADJ_FACTOR_FIELD = "adj_factor"

#: The only ``div_proc`` value that denotes an implemented, executable
#: corporate action with a real ex-date.
IMPLEMENTED_DIV_PROC = "实施"

#: Closed :data:`ACTION_TYPE_COL` enum for ``dividend``-derived actions.
ACTION_TYPE_SPLIT = "split"
ACTION_TYPE_DIVIDEND = "dividend"
ACTION_TYPE_SPLIT_AND_DIVIDEND = "split_dividend"

#: Closed enum for ``adj_factor``-derived (cross-check only) event steps.
ACTION_TYPE_ADJ_FACTOR_STEP = "adj_factor_step"

DIVIDEND_ACTION_TYPES = frozenset(
    {
        ACTION_TYPE_SPLIT,
        ACTION_TYPE_DIVIDEND,
        ACTION_TYPE_SPLIT_AND_DIVIDEND,
    }
)

_SOURCE_VENDOR = "tushare"
_SOURCE_VENDOR_COL = "_source_vendor"
_SOURCE_ENDPOINT_COL = "_source_endpoint"
_INGESTED_AT_COL = "_ingested_at"
_PREV_CLOSE_COL = "_prev_close"
_CASH_BASIS_COL = "_cash_div_basis"
_CASH_INCLUDED_COL = "_cash_adjustment_included"
_PREV_ADJ_FACTOR_COL = "_prev_adj_factor"
_NEW_ADJ_FACTOR_COL = "_new_adj_factor"

_DIVIDEND_OUTPUT_COLUMNS = (
    STOCK_COL,
    EFFECTIVE_DATE_COL,
    ACTION_TYPE_COL,
    KNOWLEDGE_DATE_COL,
    ADJUSTMENT_FACTOR_COL,
    IS_SUPERSEDED_COL,
    _SOURCE_VENDOR_COL,
    _SOURCE_ENDPOINT_COL,
    _INGESTED_AT_COL,
    _PREV_CLOSE_COL,
    _CASH_BASIS_COL,
    _CASH_INCLUDED_COL,
)

_ADJ_FACTOR_OUTPUT_COLUMNS = (
    STOCK_COL,
    EFFECTIVE_DATE_COL,
    ACTION_TYPE_COL,
    KNOWLEDGE_DATE_COL,
    ADJUSTMENT_FACTOR_COL,
    IS_SUPERSEDED_COL,
    _SOURCE_VENDOR_COL,
    _SOURCE_ENDPOINT_COL,
    _INGESTED_AT_COL,
    _PREV_ADJ_FACTOR_COL,
    _NEW_ADJ_FACTOR_COL,
)

__all__ = [
    "ACTION_TYPE_ADJ_FACTOR_STEP",
    "ACTION_TYPE_DIVIDEND",
    "ACTION_TYPE_SPLIT",
    "ACTION_TYPE_SPLIT_AND_DIVIDEND",
    "ADJ_FACTOR_FIELD",
    "ADJ_TRADE_DATE_FIELD",
    "ANN_DATE_FIELD",
    "CASH_DIV_FIELD",
    "CASH_DIV_TAX_FIELD",
    "DIVIDEND_ACTION_TYPES",
    "DIV_PROC_FIELD",
    "EX_DATE_FIELD",
    "IMPLEMENTED_DIV_PROC",
    "STK_DIV_FIELD",
    "TS_CODE_FIELD",
    "adjustment_factor_for_dividend",
    "ex_rights_reference_price",
    "map_adj_factor_to_corporate_actions",
    "map_dividend_to_corporate_actions",
]


# ---------------------------------------------------------------------------
# Row normalization / small parsing helpers
# ---------------------------------------------------------------------------
def _normalize_rows(rows: object) -> list[Mapping[str, object]]:
    """Accept either a raw Tushare payload ``{"fields": [...],
    "items": [...]}`` or a sequence of row mappings / a DataFrame, and
    return a list of row mappings.

    Tushare (and the proxy) return column-oriented payloads; callers that
    already zipped rows are served too. An empty payload yields ``[]``.
    """
    if isinstance(rows, pd.DataFrame):
        return rows.to_dict("records")
    if isinstance(rows, Mapping):
        if "items" in rows:
            fields = list(rows.get("fields") or [])
            out: list[Mapping[str, object]] = []
            for item in rows.get("items") or []:
                if isinstance(item, Mapping):
                    out.append(item)
                else:
                    out.append(dict(zip(fields, item)))
            return out
        # A single row mapping is accepted for convenience.
        return [rows]
    if isinstance(rows, (str, bytes)) or rows is None:
        raise TypeError(
            "rows must be a Tushare payload, a sequence of row mappings, "
            f"or a DataFrame, got {type(rows).__name__}"
        )
    return [row for row in rows]  # type: ignore[union-attr]


def _to_naive_timestamp(value: object, *, field: str) -> pd.Timestamp:
    """Parse a Tushare ``YYYYMMDD`` date (or any pandas-parsable date) to a
    timezone-naive midnight timestamp."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        raise ValueError(f"{field} is missing")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} is empty")
    try:
        ts = pd.Timestamp(text)
    except (ValueError, TypeError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"{field}={value!r} is not a parsable date") from exc
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.normalize()


def _as_float(value: object, *, default: float = 0.0) -> float:
    """Coerce a vendor numeric to ``float``; null/empty becomes ``default``."""
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        return float(text)
    if isinstance(value, float) and pd.isna(value):
        return default
    return float(value)  # type: ignore[arg-type]


def _cash_basis(row: Mapping[str, object]) -> float:
    """Pre-tax per-share cash dividend, preferring ``cash_div_tax``.

    Falls back to ``cash_div`` only when ``cash_div_tax`` is null/blank.
    """
    tax = row.get(CASH_DIV_TAX_FIELD)
    if tax is not None and not (isinstance(tax, str) and not tax.strip()):
        if not (isinstance(tax, float) and pd.isna(tax)):
            return _as_float(tax)
    return _as_float(row.get(CASH_DIV_FIELD))


def _normalize_prev_close_map(
    prev_close_by_ex_date: Mapping[object, object] | None,
) -> dict[pd.Timestamp, float]:
    if prev_close_by_ex_date is None:
        return {}
    normalized: dict[pd.Timestamp, float] = {}
    for key, value in prev_close_by_ex_date.items():
        normalized[_to_naive_timestamp(key, field="prev_close_by_ex_date key")] = float(
            value
        )
    return normalized


# ---------------------------------------------------------------------------
# Frozen factor formulas
# ---------------------------------------------------------------------------
def ex_rights_reference_price(
    prev_close: float, stk_div: float, cash_div_tax: float
) -> float:
    """Frozen ex-rights reference price: ``(P_prev - cash) / (1 + stk_div)``.

    Raises :class:`ValueError` when the inputs cannot produce a positive,
    finite reference price (fail-closed rather than emitting garbage).
    """
    prev = float(prev_close)
    stk = float(stk_div)
    cash = float(cash_div_tax)
    if not (prev > 0):
        raise ValueError(f"prev_close must be positive, got {prev_close!r}")
    if stk < 0:
        raise ValueError(f"stk_div must be >= 0, got {stk_div!r}")
    if cash < 0:
        raise ValueError(f"cash_div_tax must be >= 0, got {cash_div_tax!r}")
    denominator = 1.0 + stk
    reference = (prev - cash) / denominator
    if not (reference > 0):
        raise ValueError(
            "ex-rights reference price is non-positive "
            f"(prev_close={prev!r}, cash_div_tax={cash!r}, stk_div={stk!r}); "
            "refusing to emit an adjustment_factor"
        )
    return reference


def adjustment_factor_for_dividend(
    *,
    stk_div: float,
    cash_div_tax: float,
    prev_close: float | None,
) -> float:
    """The frozen per-event ``adjustment_factor`` (see module docstring).

    ``(1 + stk_div) * prev_close / (prev_close - cash_div_tax)`` when a
    ``prev_close`` is available and cash is present; otherwise the
    price-free share-count factor ``1 + stk_div``.
    """
    stk = float(stk_div)
    cash = float(cash_div_tax)
    if cash == 0.0 or prev_close is None:
        return 1.0 + stk
    # Validate that a positive reference price exists (raises otherwise),
    # then return the frozen multiplier P_prev / P_ex_ref.
    ex_rights_reference_price(prev_close, stk, cash)
    return (1.0 + stk) * float(prev_close) / (float(prev_close) - cash)


# ---------------------------------------------------------------------------
# Output-frame helpers
# ---------------------------------------------------------------------------
def _empty_dividend_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            STOCK_COL: pd.Series(dtype="string"),
            EFFECTIVE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
            ACTION_TYPE_COL: pd.Series(dtype="string"),
            KNOWLEDGE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
            ADJUSTMENT_FACTOR_COL: pd.Series(dtype="float64"),
            IS_SUPERSEDED_COL: pd.Series(dtype="bool"),
            _SOURCE_VENDOR_COL: pd.Series(dtype="string"),
            _SOURCE_ENDPOINT_COL: pd.Series(dtype="string"),
            _INGESTED_AT_COL: pd.Series(dtype="datetime64[ns, UTC]"),
            _PREV_CLOSE_COL: pd.Series(dtype="float64"),
            _CASH_BASIS_COL: pd.Series(dtype="float64"),
            _CASH_INCLUDED_COL: pd.Series(dtype="bool"),
        }
    )


def _empty_adj_factor_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            STOCK_COL: pd.Series(dtype="string"),
            EFFECTIVE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
            ACTION_TYPE_COL: pd.Series(dtype="string"),
            KNOWLEDGE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
            ADJUSTMENT_FACTOR_COL: pd.Series(dtype="float64"),
            IS_SUPERSEDED_COL: pd.Series(dtype="bool"),
            _SOURCE_VENDOR_COL: pd.Series(dtype="string"),
            _SOURCE_ENDPOINT_COL: pd.Series(dtype="string"),
            _INGESTED_AT_COL: pd.Series(dtype="datetime64[ns, UTC]"),
            _PREV_ADJ_FACTOR_COL: pd.Series(dtype="float64"),
            _NEW_ADJ_FACTOR_COL: pd.Series(dtype="float64"),
        }
    )


def _finalize(
    records: list[dict],
    columns: Sequence[str],
    empty_factory,
    *,
    sort_columns: Sequence[str],
) -> pd.DataFrame:
    if not records:
        out = empty_factory()
    else:
        out = pd.DataFrame.from_records(records, columns=list(columns))
        out[STOCK_COL] = out[STOCK_COL].astype("string")
        out[ACTION_TYPE_COL] = out[ACTION_TYPE_COL].astype("string")
        out[EFFECTIVE_DATE_COL] = pd.to_datetime(out[EFFECTIVE_DATE_COL])
        out[KNOWLEDGE_DATE_COL] = pd.to_datetime(out[KNOWLEDGE_DATE_COL])
        out[ADJUSTMENT_FACTOR_COL] = out[ADJUSTMENT_FACTOR_COL].astype("float64")
        out[IS_SUPERSEDED_COL] = out[IS_SUPERSEDED_COL].astype("bool")
        out[_SOURCE_VENDOR_COL] = out[_SOURCE_VENDOR_COL].astype("string")
        out[_SOURCE_ENDPOINT_COL] = out[_SOURCE_ENDPOINT_COL].astype("string")
        out[_INGESTED_AT_COL] = pd.to_datetime(out[_INGESTED_AT_COL], utc=True)
        out = out.sort_values(list(sort_columns), kind="mergesort").reset_index(
            drop=True
        )
    validate_panel(out, CORPORATE_ACTIONS_SCHEMA, name="corporate_actions")
    return out


# ---------------------------------------------------------------------------
# Public mappings
# ---------------------------------------------------------------------------
def map_dividend_to_corporate_actions(
    rows: object,
    *,
    prev_close_by_ex_date: Mapping[object, object] | None = None,
    source_endpoint: str = "dividend",
) -> pd.DataFrame:
    """Map Tushare ``dividend`` rows to ``CORPORATE_ACTIONS_SCHEMA``.

    Only ``div_proc == "实施"`` rows with a parsable ``ex_date`` are
    emitted (see module docstring). Repeated implemented rows with
    identical terms collapse to one action whose ``knowledge_date`` is
    the earliest such ``ann_date``. ``effective_date`` is the ``ex_date``.

    ``prev_close_by_ex_date`` is an optional caller-supplied mapping from
    an ``ex_date`` (``"YYYYMMDD"``, ``datetime.date``, or ``Timestamp``)
    to the raw close on the trading day immediately before it. The adapter
    performs no market-data I/O: this price is what the frozen formula
    needs to evaluate the cash component, and when it is absent the
    documented price-free fallback ``1 + stk_div`` is used
    (``_cash_adjustment_included = False``). See the module docstring for
    the frozen formula and its proxy-observed cross-check.

    Neither ``rows`` nor any row mapping is mutated.
    """
    ingested_at = pd.Timestamp.now(tz="UTC")
    prev_close_map = _normalize_prev_close_map(prev_close_by_ex_date)

    # Event key -> [earliest knowledge date, representative row values].
    events: dict[tuple, dict] = {}

    for raw in _normalize_rows(rows):
        if str(raw.get(DIV_PROC_FIELD, "")).strip() != IMPLEMENTED_DIV_PROC:
            continue
        ex_date_raw = raw.get(EX_DATE_FIELD)
        if ex_date_raw is None or (
            isinstance(ex_date_raw, str) and not ex_date_raw.strip()
        ):
            continue

        stock_id = raw.get(TS_CODE_FIELD)
        if not isinstance(stock_id, str) or not stock_id.strip():
            raise ValueError(
                f"dividend row has no usable ts_code: {raw!r}"
            )
        stock_id = stock_id.strip()

        effective_date = _to_naive_timestamp(ex_date_raw, field=EX_DATE_FIELD)
        knowledge_date = _to_naive_timestamp(
            raw.get(ANN_DATE_FIELD), field=ANN_DATE_FIELD
        )
        stk_div = _as_float(raw.get(STK_DIV_FIELD))
        cash = _cash_basis(raw)
        if stk_div < 0 or cash < 0:
            raise ValueError(
                "dividend row has a negative term: "
                f"ts_code={stock_id!r} ex_date={ex_date_raw!r} "
                f"stk_div={stk_div!r} cash={cash!r}"
            )
        if stk_div == 0.0 and cash == 0.0:
            # Implemented but economically empty (no shares, no cash):
            # not a corporate action. The task's 2023-08-24 zero rows are
            # non-implemented and already filtered above; this guards the
            # same shape on an implemented row.
            continue

        if stk_div > 0 and cash > 0:
            action_type = ACTION_TYPE_SPLIT_AND_DIVIDEND
        elif stk_div > 0:
            action_type = ACTION_TYPE_SPLIT
        else:
            action_type = ACTION_TYPE_DIVIDEND

        key = (stock_id, effective_date, stk_div, cash)
        existing = events.get(key)
        if existing is None or knowledge_date < existing["knowledge_date"]:
            events[key] = {
                "stock_id": stock_id,
                "effective_date": effective_date,
                "knowledge_date": knowledge_date,
                "stk_div": stk_div,
                "cash": cash,
                "action_type": action_type,
            }

    records: list[dict] = []
    for event in events.values():
        prev_close = prev_close_map.get(event["effective_date"])
        cash_included = bool(event["cash"] > 0.0 and prev_close is not None)
        factor = adjustment_factor_for_dividend(
            stk_div=event["stk_div"],
            cash_div_tax=event["cash"],
            prev_close=prev_close,
        )
        records.append(
            {
                STOCK_COL: event["stock_id"],
                EFFECTIVE_DATE_COL: event["effective_date"],
                ACTION_TYPE_COL: event["action_type"],
                KNOWLEDGE_DATE_COL: event["knowledge_date"],
                ADJUSTMENT_FACTOR_COL: factor,
                IS_SUPERSEDED_COL: False,
                _SOURCE_VENDOR_COL: _SOURCE_VENDOR,
                _SOURCE_ENDPOINT_COL: source_endpoint,
                _INGESTED_AT_COL: ingested_at,
                _PREV_CLOSE_COL: (
                    float(prev_close) if prev_close is not None else float("nan")
                ),
                _CASH_BASIS_COL: event["cash"],
                _CASH_INCLUDED_COL: cash_included,
            }
        )

    return _finalize(
        records,
        _DIVIDEND_OUTPUT_COLUMNS,
        _empty_dividend_frame,
        sort_columns=(EFFECTIVE_DATE_COL, ACTION_TYPE_COL),
    )


def map_adj_factor_to_corporate_actions(
    rows: object,
    *,
    source_endpoint: str = "adj_factor",
) -> pd.DataFrame:
    """Derive discrete corporate-action events from a Tushare ``adj_factor``
    series.

    ``adj_factor`` is a cumulative adjustment series, not an event table.
    This function sorts each stock's rows by ``trade_date`` and emits one
    :data:`ACTION_TYPE_ADJ_FACTOR_STEP` action wherever the factor changes
    from one observed trading day to the next, with
    ``effective_date`` = the later ``trade_date`` and ``adjustment_factor``
    = ``later / earlier``.

    This is an **independent reconstruction for cross-checking only** and
    must never be concatenated with
    :func:`map_dividend_to_corporate_actions` (double-counting). The step
    carries no action-type information, which is why it uses its own enum
    value. ``knowledge_date = effective_date`` (no announcement date in
    the endpoint); ``is_superseded = False``.

    Neither ``rows`` nor any row mapping is mutated.
    """
    ingested_at = pd.Timestamp.now(tz="UTC")

    by_stock: dict[str, list[tuple[pd.Timestamp, float]]] = {}
    for raw in _normalize_rows(rows):
        stock_id = raw.get(TS_CODE_FIELD)
        if not isinstance(stock_id, str) or not stock_id.strip():
            raise ValueError(f"adj_factor row has no usable ts_code: {raw!r}")
        trade_date = _to_naive_timestamp(
            raw.get(ADJ_TRADE_DATE_FIELD), field=ADJ_TRADE_DATE_FIELD
        )
        try:
            factor = float(raw.get(ADJ_FACTOR_FIELD))  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"adj_factor row has a non-numeric adj_factor: {raw!r}"
            ) from exc
        by_stock.setdefault(stock_id.strip(), []).append((trade_date, factor))

    records: list[dict] = []
    for stock_id, series in by_stock.items():
        series.sort(key=lambda pair: pair[0])
        for (prev_date, prev_factor), (curr_date, curr_factor) in zip(
            series, series[1:]
        ):
            if curr_factor == prev_factor:
                continue
            if prev_factor == 0.0:
                raise ValueError(
                    f"adj_factor is zero on {prev_date.date()} for "
                    f"{stock_id!r}; cannot derive a step ratio"
                )
            records.append(
                {
                    STOCK_COL: stock_id,
                    EFFECTIVE_DATE_COL: curr_date,
                    ACTION_TYPE_COL: ACTION_TYPE_ADJ_FACTOR_STEP,
                    KNOWLEDGE_DATE_COL: curr_date,
                    ADJUSTMENT_FACTOR_COL: curr_factor / prev_factor,
                    IS_SUPERSEDED_COL: False,
                    _SOURCE_VENDOR_COL: _SOURCE_VENDOR,
                    _SOURCE_ENDPOINT_COL: source_endpoint,
                    _INGESTED_AT_COL: ingested_at,
                    _PREV_ADJ_FACTOR_COL: prev_factor,
                    _NEW_ADJ_FACTOR_COL: curr_factor,
                }
            )

    return _finalize(
        records,
        _ADJ_FACTOR_OUTPUT_COLUMNS,
        _empty_adj_factor_frame,
        sort_columns=(STOCK_COL, EFFECTIVE_DATE_COL),
    )
