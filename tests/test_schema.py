import pandas as pd
import pytest

from smart_beta.data.schema import (
    DATE_COL,
    FACTOR_PANEL_SCHEMA,
    RETURN_COL,
    RETURN_PANEL_SCHEMA,
    STOCK_COL,
    VALUE_COL,
    SchemaError,
    validate_panel,
)


def _valid_return_panel() -> pd.DataFrame:
    return pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(["2020-01-31", "2020-01-31", "2020-02-29"]),
            STOCK_COL: ["S0001", "S0002", "S0001"],
            RETURN_COL: [0.01, -0.02, 0.03],
        }
    )


def test_validate_panel_accepts_well_formed_panel():
    validate_panel(_valid_return_panel(), RETURN_PANEL_SCHEMA, name="test")


def test_validate_panel_rejects_missing_column():
    df = _valid_return_panel().drop(columns=[RETURN_COL])
    with pytest.raises(SchemaError, match="missing required columns"):
        validate_panel(df, RETURN_PANEL_SCHEMA, name="test")


def test_validate_panel_rejects_duplicate_keys():
    df = _valid_return_panel()
    df.loc[len(df)] = [df[DATE_COL].iloc[0], df[STOCK_COL].iloc[0], 0.05]
    with pytest.raises(SchemaError, match="duplicate rows"):
        validate_panel(df, RETURN_PANEL_SCHEMA, name="test")


def test_validate_panel_rejects_wrong_dtype():
    df = _valid_return_panel()
    df[DATE_COL] = df[DATE_COL].astype(str)
    with pytest.raises(SchemaError, match="datetime-like"):
        validate_panel(df, RETURN_PANEL_SCHEMA, name="test")


def test_factor_panel_schema_is_shared_with_factors_base():
    from smart_beta.factors.base import FACTOR_PANEL_SCHEMA as base_schema
    from smart_beta.factors.base import VALUE_COL as base_value_col

    assert base_schema is FACTOR_PANEL_SCHEMA
    assert base_value_col is VALUE_COL
