"""Canonical point-in-time (PIT) schemas for the trusted Phase 3 boundary.

These schemas are the shapes every later Phase 3 module validates its panels
against. They are deliberately vendor-independent and contain no domain logic
-- only column names, dtypes, and the key columns that make a row unique.
Like :mod:`smart_beta.data.schema`, this module defines data shapes, not the
"latest known as of t" resolution or corporate-action adjustment algorithms
that consume them (those live in ``pit.fundamentals`` and
``pit.corporate_actions``).

Bitemporal decision table
-------------------------

The central design decision is that *effective/event time* (when an economic
fact applies) and *knowledge/availability time* (when a researcher could
actually have known it) are distinct only where that distinction is real --
not mechanically added to every entity:

===================  ==========  ====================  ====================  ==========================
Entity               Bitemporal? Effective time        Knowledge time        Vintage / append-only?
===================  ==========  ====================  ====================  ==========================
Fundamentals         Yes         ``report_period_end`` ``knowledge_date``    Yes: a restatement is a
                                                                              new row with a later
                                                                              ``knowledge_date``;
                                                                              ``is_restatement`` marks
                                                                              non-original vintages.
Corporate actions    Yes         ``effective_date``    ``knowledge_date``    Yes: an amendment or
                                                                              withdrawal is a new row;
                                                                              ``is_superseded`` marks
                                                                              it.
Listing/delisting    No          ``list_date`` /       --                    No. A delisting decision
                                 ``delist_date``                              is not restated like a
                                                                              financial figure; the PIT
                                                                              risk is survivorship bias
                                                                              (a completeness/query
                                                                              concern), tested later by
                                                                              the compliance suite, not
                                                                              encoded as a schema
                                                                              column.
Trading status       No          the date itself       --                    No. Suspension, limit,
                                                                              and ST flags are same-day,
                                                                              immediately observable
                                                                              facts: effective time and
                                                                              knowledge time coincide.
Market data /        No (by     the trading date      --                    Not modeled in Phase 3.
market cap           documented                                                Real vendor delivery lag
                     assumption)                                               is a separate, deferred
                                                                               concern; the
                                                                               ``knowledge_date`` name is
                                                                               reserved and the pattern
                                                                               generalizes if this is
                                                                               revisited later.
===================  ==========  ====================  ====================  ==========================

``KNOWLEDGE_DATE_COL = "knowledge_date"`` is the one canonical name shared by
both bitemporal schemas, so a single "latest row with
``knowledge_date <= as_of``, grouped by entity and effective-time column"
resolver can be written once (Wave 2) and reused for fundamentals and
corporate actions alike rather than two bespoke implementations.

Raw and adjusted returns are separate schema families (``RAW_RETURN_COL`` vs
``ADJUSTED_RETURN_COL``) so the two are never conflated; market cap likewise
always carries both ``float_mcap`` and ``total_mcap`` rather than one
ambiguous ``mcap``. A single later module computes adjusted returns from the
raw returns and the corporate-actions fact table; these schemas only fix the
shapes it reads and writes.

This module reuses :class:`smart_beta.data.schema.PanelSchema` and
:func:`smart_beta.data.schema.validate_panel` rather than redefining them;
there is exactly one canonical implementation package-wide.
"""

from __future__ import annotations

from smart_beta.data.schema import (
    DATE_COL,
    STOCK_COL,
    VALUE_COL,
    PanelSchema,
    SchemaError,
    validate_panel,
)

# --- Bitemporal column names (shared across every bitemporal schema) ---
KNOWLEDGE_DATE_COL = "knowledge_date"

# --- Fundamentals fact table (bitemporal, vintage/append-only) ---
REPORT_PERIOD_END_COL = "report_period_end"
FIELD_COL = "field"
IS_RESTATEMENT_COL = "is_restatement"

# --- Corporate actions fact table (bitemporal, vintage/append-only) ---
EFFECTIVE_DATE_COL = "effective_date"
ACTION_TYPE_COL = "action_type"
ADJUSTMENT_FACTOR_COL = "adjustment_factor"
IS_SUPERSEDED_COL = "is_superseded"

# --- Raw vs. adjusted return panels (distinct, never conflated) ---
RAW_RETURN_COL = "raw_ret"
ADJUSTED_RETURN_COL = "adj_ret"

# --- Market cap: float and total, always both, never one ambiguous column ---
FLOAT_MARKET_CAP_COL = "float_mcap"
TOTAL_MARKET_CAP_COL = "total_mcap"

# --- Listing info (not bitemporal) ---
LIST_DATE_COL = "list_date"
DELIST_DATE_COL = "delist_date"


FUNDAMENTALS_FACT_SCHEMA = PanelSchema(
    key_columns=(STOCK_COL, REPORT_PERIOD_END_COL, FIELD_COL, KNOWLEDGE_DATE_COL),
    dtypes={
        STOCK_COL: "string",
        REPORT_PERIOD_END_COL: "datetime",
        FIELD_COL: "string",
        KNOWLEDGE_DATE_COL: "datetime",
        VALUE_COL: "float",
        IS_RESTATEMENT_COL: "bool",
    },
)

CORPORATE_ACTIONS_SCHEMA = PanelSchema(
    key_columns=(STOCK_COL, EFFECTIVE_DATE_COL, ACTION_TYPE_COL, KNOWLEDGE_DATE_COL),
    dtypes={
        STOCK_COL: "string",
        EFFECTIVE_DATE_COL: "datetime",
        ACTION_TYPE_COL: "string",
        KNOWLEDGE_DATE_COL: "datetime",
        ADJUSTMENT_FACTOR_COL: "float",
        IS_SUPERSEDED_COL: "bool",
    },
)

PIT_RAW_RETURN_PANEL_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={DATE_COL: "datetime", STOCK_COL: "string", RAW_RETURN_COL: "float"},
)

PIT_ADJUSTED_RETURN_PANEL_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={DATE_COL: "datetime", STOCK_COL: "string", ADJUSTED_RETURN_COL: "float"},
)

PIT_MARKET_CAP_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={
        DATE_COL: "datetime",
        STOCK_COL: "string",
        FLOAT_MARKET_CAP_COL: "float",
        TOTAL_MARKET_CAP_COL: "float",
    },
)

PIT_LISTING_INFO_SCHEMA = PanelSchema(
    key_columns=(STOCK_COL,),
    dtypes={STOCK_COL: "string", LIST_DATE_COL: "datetime"},
)

# Flag columns are open-ended/per-vendor, so only the key columns are enforced.
PIT_TRADING_STATUS_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={DATE_COL: "datetime", STOCK_COL: "string"},
)
