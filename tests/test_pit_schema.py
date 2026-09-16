import pandas as pd
import pytest

from smart_beta.config.settings import Settings
from smart_beta.data import schema as data_schema
from smart_beta.data.schema import (
    DATE_COL,
    STOCK_COL,
    VALUE_COL,
    SchemaError,
    validate_panel,
)
from smart_beta.pit import schema as pit_schema
from smart_beta.pit.schema import (
    ACTION_TYPE_COL,
    ADJUSTED_RETURN_COL,
    ADJUSTMENT_FACTOR_COL,
    CORPORATE_ACTIONS_SCHEMA,
    EFFECTIVE_DATE_COL,
    FIELD_COL,
    FLOAT_MARKET_CAP_COL,
    FUNDAMENTALS_FACT_SCHEMA,
    IS_RESTATEMENT_COL,
    IS_SUPERSEDED_COL,
    KNOWLEDGE_DATE_COL,
    LIST_DATE_COL,
    PIT_ADJUSTED_RETURN_PANEL_SCHEMA,
    PIT_LISTING_INFO_SCHEMA,
    PIT_MARKET_CAP_SCHEMA,
    PIT_RAW_RETURN_PANEL_SCHEMA,
    PIT_TRADING_STATUS_SCHEMA,
    RAW_RETURN_COL,
    REPORT_PERIOD_END_COL,
    TOTAL_MARKET_CAP_COL,
)


# --- Well-formed example panels (one per new schema) ---


def _valid_fundamentals_fact() -> pd.DataFrame:
    return pd.DataFrame(
        {
            STOCK_COL: ["S0001", "S0002"],
            REPORT_PERIOD_END_COL: pd.to_datetime(["2020-03-31", "2020-03-31"]),
            FIELD_COL: ["revenue", "revenue"],
            KNOWLEDGE_DATE_COL: pd.to_datetime(["2020-04-30", "2020-04-30"]),
            VALUE_COL: [1.0e9, 2.0e9],
            IS_RESTATEMENT_COL: [False, False],
        }
    )


def _valid_corporate_actions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            STOCK_COL: ["S0001", "S0001"],
            EFFECTIVE_DATE_COL: pd.to_datetime(["2020-06-15", "2020-09-15"]),
            ACTION_TYPE_COL: ["split", "dividend"],
            KNOWLEDGE_DATE_COL: pd.to_datetime(["2020-06-01", "2020-09-01"]),
            ADJUSTMENT_FACTOR_COL: [2.0, 0.5],
            IS_SUPERSEDED_COL: [False, False],
        }
    )


def _valid_raw_returns() -> pd.DataFrame:
    return pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(["2020-01-31", "2020-01-31", "2020-02-29"]),
            STOCK_COL: ["S0001", "S0002", "S0001"],
            RAW_RETURN_COL: [0.01, -0.02, 0.03],
        }
    )


def _valid_adjusted_returns() -> pd.DataFrame:
    return pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(["2020-01-31", "2020-01-31", "2020-02-29"]),
            STOCK_COL: ["S0001", "S0002", "S0001"],
            ADJUSTED_RETURN_COL: [0.01, -0.02, 0.03],
        }
    )


def _valid_market_cap() -> pd.DataFrame:
    return pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(["2020-01-31", "2020-01-31"]),
            STOCK_COL: ["S0001", "S0002"],
            FLOAT_MARKET_CAP_COL: [1.0e9, 2.0e9],
            TOTAL_MARKET_CAP_COL: [2.0e9, 3.0e9],
        }
    )


def _valid_listing_info() -> pd.DataFrame:
    return pd.DataFrame(
        {
            STOCK_COL: ["S0001", "S0002"],
            LIST_DATE_COL: pd.to_datetime(["2010-01-04", "2011-05-20"]),
        }
    )


def _valid_trading_status() -> pd.DataFrame:
    return pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(["2020-01-31", "2020-01-31"]),
            STOCK_COL: ["S0001", "S0002"],
            "is_suspended": [False, True],
        }
    )


# (schema, builder, a required column to drop, a datetime column to corrupt)
_SCHEMA_CASES = [
    pytest.param(
        FUNDAMENTALS_FACT_SCHEMA,
        _valid_fundamentals_fact,
        REPORT_PERIOD_END_COL,
        KNOWLEDGE_DATE_COL,
        id="fundamentals_fact",
    ),
    pytest.param(
        CORPORATE_ACTIONS_SCHEMA,
        _valid_corporate_actions,
        ADJUSTMENT_FACTOR_COL,
        EFFECTIVE_DATE_COL,
        id="corporate_actions",
    ),
    pytest.param(
        PIT_RAW_RETURN_PANEL_SCHEMA,
        _valid_raw_returns,
        RAW_RETURN_COL,
        DATE_COL,
        id="raw_returns",
    ),
    pytest.param(
        PIT_ADJUSTED_RETURN_PANEL_SCHEMA,
        _valid_adjusted_returns,
        ADJUSTED_RETURN_COL,
        DATE_COL,
        id="adjusted_returns",
    ),
    pytest.param(
        PIT_MARKET_CAP_SCHEMA,
        _valid_market_cap,
        FLOAT_MARKET_CAP_COL,
        DATE_COL,
        id="market_cap",
    ),
    pytest.param(
        PIT_LISTING_INFO_SCHEMA,
        _valid_listing_info,
        LIST_DATE_COL,
        LIST_DATE_COL,
        id="listing_info",
    ),
    pytest.param(
        PIT_TRADING_STATUS_SCHEMA,
        _valid_trading_status,
        DATE_COL,
        DATE_COL,
        id="trading_status",
    ),
]


@pytest.mark.parametrize("schema,builder,drop_col,dtype_col", _SCHEMA_CASES)
def test_schema_accepts_well_formed_panel(schema, builder, drop_col, dtype_col):
    validate_panel(builder(), schema, name="test")


@pytest.mark.parametrize("schema,builder,drop_col,dtype_col", _SCHEMA_CASES)
def test_schema_rejects_missing_required_column(schema, builder, drop_col, dtype_col):
    df = builder().drop(columns=[drop_col])
    with pytest.raises(SchemaError, match="missing required columns"):
        validate_panel(df, schema, name="test")


@pytest.mark.parametrize("schema,builder,drop_col,dtype_col", _SCHEMA_CASES)
def test_schema_rejects_wrong_dtype(schema, builder, drop_col, dtype_col):
    df = builder()
    df[dtype_col] = df[dtype_col].astype(str)
    with pytest.raises(SchemaError, match="datetime-like"):
        validate_panel(df, schema, name="test")


@pytest.mark.parametrize("schema,builder,drop_col,dtype_col", _SCHEMA_CASES)
def test_schema_rejects_duplicate_keys(schema, builder, drop_col, dtype_col):
    df = builder()
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    with pytest.raises(SchemaError, match="duplicate rows"):
        validate_panel(df, schema, name="test")


def test_fundamentals_schema_allows_restatement_vintages():
    """Same (stock, period, field) with a later knowledge_date is a new vintage."""
    df = pd.DataFrame(
        {
            STOCK_COL: ["S0001", "S0001"],
            REPORT_PERIOD_END_COL: pd.to_datetime(["2020-03-31", "2020-03-31"]),
            FIELD_COL: ["revenue", "revenue"],
            KNOWLEDGE_DATE_COL: pd.to_datetime(["2020-04-30", "2020-08-15"]),
            VALUE_COL: [1.0e9, 1.1e9],
            IS_RESTATEMENT_COL: [False, True],
        }
    )
    validate_panel(df, FUNDAMENTALS_FACT_SCHEMA, name="test")

    # A third row duplicating an existing full key (even with a different
    # value) must still be rejected as a duplicate.
    repeat = pd.DataFrame(
        {
            STOCK_COL: ["S0001"],
            REPORT_PERIOD_END_COL: pd.to_datetime(["2020-03-31"]),
            FIELD_COL: ["revenue"],
            KNOWLEDGE_DATE_COL: pd.to_datetime(["2020-04-30"]),
            VALUE_COL: [9.9e9],
            IS_RESTATEMENT_COL: [True],
        }
    )
    with pytest.raises(SchemaError, match="duplicate rows"):
        validate_panel(
            pd.concat([df, repeat], ignore_index=True),
            FUNDAMENTALS_FACT_SCHEMA,
            name="test",
        )


def test_corporate_actions_schema_allows_superseding_vintages():
    """Same (stock, effective_date, action_type) with a later knowledge_date is
    a new vintage; only a full-key duplicate is rejected."""
    df = pd.DataFrame(
        {
            STOCK_COL: ["S0001", "S0001"],
            EFFECTIVE_DATE_COL: pd.to_datetime(["2020-06-15", "2020-06-15"]),
            ACTION_TYPE_COL: ["split", "split"],
            KNOWLEDGE_DATE_COL: pd.to_datetime(["2020-05-01", "2020-06-01"]),
            ADJUSTMENT_FACTOR_COL: [2.0, 4.0],
            IS_SUPERSEDED_COL: [True, False],
        }
    )
    validate_panel(df, CORPORATE_ACTIONS_SCHEMA, name="test")

    repeat = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    with pytest.raises(SchemaError, match="duplicate rows"):
        validate_panel(repeat, CORPORATE_ACTIONS_SCHEMA, name="test")


def test_pit_schema_reuses_data_schema_objects():
    assert pit_schema.PanelSchema is data_schema.PanelSchema
    assert pit_schema.validate_panel is data_schema.validate_panel
    assert pit_schema.SchemaError is data_schema.SchemaError
    assert pit_schema.DATE_COL is data_schema.DATE_COL
    assert pit_schema.STOCK_COL is data_schema.STOCK_COL
    assert pit_schema.VALUE_COL is data_schema.VALUE_COL
    assert pit_schema.DATE_COL == DATE_COL
    assert pit_schema.STOCK_COL == STOCK_COL
    assert pit_schema.VALUE_COL == VALUE_COL


def test_pit_availability_buffer_days_defaults_to_zero():
    assert Settings().pit_availability_buffer_days == 0


def test_pit_availability_buffer_days_can_be_overridden():
    assert Settings(pit_availability_buffer_days=5).pit_availability_buffer_days == 5
