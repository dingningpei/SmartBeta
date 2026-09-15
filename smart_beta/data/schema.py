"""Canonical long-format panel schema for the smart_beta package.

Every internal data structure is a pandas DataFrame in "long" (tidy) form:
one row per (date, stock_id) observation, plus one or more value columns.
This replaces the original notebooks' position-indexed numpy arrays
(``rankrt[:, i - 24:i]``-style slicing), which was the source of most of
the alignment bugs found in the legacy code: off-by-one rolling windows,
silently mismatched dates between arrays computed at different times, and
NaNs coerced to zero to make positional arithmetic "work".

Any code that produces or consumes a panel should validate it against one
of the schemas below via :func:`validate_panel` rather than assuming
column names and dtypes are correct by convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

DATE_COL = "date"
STOCK_COL = "stock_id"
RETURN_COL = "ret"
MARKET_CAP_COL = "mcap"
RISK_FREE_COL = "rf"
VALUE_COL = "value"

TRADING_STATUS_COLS: Sequence[str] = (
    "is_suspended",
    "is_limit_up",
    "is_limit_down",
    "is_st",
)


class SchemaError(ValueError):
    """Raised when a DataFrame does not conform to the expected panel schema."""


@dataclass(frozen=True)
class PanelSchema:
    """Required columns and dtypes for a panel DataFrame.

    ``key_columns`` are the columns that together must uniquely identify a
    row (e.g. ``(date, stock_id)``); duplicates on that key are rejected.
    ``dtypes`` maps a column to a logical type name (``"datetime"``,
    ``"float"``, ``"string"``, ``"bool"``); every column named in either
    ``key_columns`` or ``dtypes`` is required.

    Type checks go through ``pandas.api.types`` predicates rather than
    ``np.issubdtype`` directly: ``np.issubdtype`` raises ``TypeError`` on
    pandas extension dtypes (e.g. the ``StringDtype`` pandas 3.0 uses by
    default for ``.astype(str)``), which would otherwise crash validation
    instead of reporting a schema mismatch.
    """

    key_columns: Sequence[str]
    dtypes: Mapping[str, str] = field(default_factory=dict)

    _TYPE_CHECKS = {
        "datetime": pd.api.types.is_datetime64_any_dtype,
        "float": pd.api.types.is_float_dtype,
        "bool": pd.api.types.is_bool_dtype,
        "string": pd.api.types.is_string_dtype,
    }

    def validate(self, df: pd.DataFrame, *, name: str = "panel") -> None:
        required = list(dict.fromkeys((*self.key_columns, *self.dtypes.keys())))
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise SchemaError(f"{name} is missing required columns: {missing}")

        for col, expected in self.dtypes.items():
            actual = df[col].dtype
            if not self._TYPE_CHECKS[expected](actual):
                raise SchemaError(
                    f"{name}.{col} must be {expected}-like, got dtype {actual}"
                )

        dup = df.duplicated(subset=list(self.key_columns))
        if dup.any():
            raise SchemaError(
                f"{name} has {int(dup.sum())} duplicate rows on key "
                f"{list(self.key_columns)}"
            )


def validate_panel(df: pd.DataFrame, schema: PanelSchema, *, name: str = "panel") -> None:
    """Validate ``df`` against ``schema``, raising :class:`SchemaError` on failure."""
    schema.validate(df, name=name)


RETURN_PANEL_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={DATE_COL: "datetime", STOCK_COL: "string", RETURN_COL: "float"},
)

MARKET_CAP_PANEL_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={DATE_COL: "datetime", STOCK_COL: "string", MARKET_CAP_COL: "float"},
)

# Characteristic/financials panels carry an open-ended set of value columns
# (whatever fields were requested from the DataSource), so only the key and
# join columns are enforced here.
CHARACTERISTIC_PANEL_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={DATE_COL: "datetime", STOCK_COL: "string"},
)

TRADING_STATUS_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={DATE_COL: "datetime", STOCK_COL: "string"},
)

LISTING_INFO_SCHEMA = PanelSchema(
    key_columns=(STOCK_COL,),
    dtypes={STOCK_COL: "string", "list_date": "datetime"},
)

RISK_FREE_SCHEMA = PanelSchema(
    key_columns=(DATE_COL,),
    dtypes={DATE_COL: "datetime", RISK_FREE_COL: "float"},
)

#: Factor/characteristic value panel: one value per ``(date, stock_id)``.
FACTOR_PANEL_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={DATE_COL: "datetime", STOCK_COL: "string", VALUE_COL: "float"},
)
