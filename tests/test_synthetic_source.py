import numpy as np
import pandas as pd
import pytest

from smart_beta.data.schema import (
    DATE_COL,
    MARKET_CAP_COL,
    RETURN_COL,
    RISK_FREE_COL,
    STOCK_COL,
    TRADING_STATUS_COLS,
)

START = "2015-01-31"
END = "2030-12-31"  # wide enough to cover the whole fixture


def test_get_returns_shape_and_schema(synthetic_source):
    df = synthetic_source.get_returns(START, END)
    assert not df.empty
    assert set(df.columns) == {DATE_COL, STOCK_COL, RETURN_COL}
    assert df[RETURN_COL].notna().all()  # pre-listing rows must be dropped, not zero-filled


def test_get_market_cap_is_positive_where_present(synthetic_source):
    df = synthetic_source.get_market_cap(START, END)
    assert (df[MARKET_CAP_COL] > 0).all()


def test_get_financials_rejects_unknown_field(synthetic_source):
    with pytest.raises(ValueError, match="Unknown financial field"):
        synthetic_source.get_financials(START, END, fields=["not_a_real_field"])


def test_get_financials_returns_requested_fields(synthetic_source):
    df = synthetic_source.get_financials(START, END, fields=["book_value", "signal"])
    assert set(df.columns) == {DATE_COL, STOCK_COL, "book_value", "signal"}


def test_get_trading_status_columns_are_boolean(synthetic_source):
    df = synthetic_source.get_trading_status(START, END)
    for col in TRADING_STATUS_COLS:
        assert df[col].dtype == np.bool_


def test_get_risk_free_schema(synthetic_source):
    df = synthetic_source.get_risk_free(START, END)
    assert set(df.columns) == {DATE_COL, RISK_FREE_COL}
    assert df[RISK_FREE_COL].notna().all()


def test_listing_info_matches_return_availability(synthetic_source):
    listing = synthetic_source.get_listing_info()
    returns = synthetic_source.get_returns(START, END)
    first_return_date = returns.groupby(STOCK_COL)[DATE_COL].min()

    merged = listing.set_index(STOCK_COL)["list_date"].loc[first_return_date.index]
    # no stock should have a return before its recorded list_date
    assert (first_return_date >= merged).all()


def test_ground_truth_betas_are_well_formed(synthetic_source):
    gt = synthetic_source.ground_truth
    assert set(gt.true_betas.index) == set(synthetic_source.get_listing_info()[STOCK_COL])
    assert (gt.true_betas > 0).all()


def test_estimated_betas_recover_true_betas(synthetic_source):
    """Regression smoke test: a plain per-stock OLS of excess return on
    excess market return should recover something close to the true beta
    used to generate the data. This is deliberately loose (correlation,
    not exact match) since it is only checking the fixture is sane, not
    validating an estimator (that is Phase 1, Task B's job).
    """
    gt = synthetic_source.ground_truth
    returns = synthetic_source.get_returns(START, END)
    rf = synthetic_source.get_risk_free(START, END).set_index(DATE_COL)[RISK_FREE_COL]

    estimated = {}
    for stock_id, group in returns.groupby(STOCK_COL):
        s = group.set_index(DATE_COL)[RETURN_COL]
        common_dates = s.index.intersection(gt.market_return.index)
        if len(common_dates) < 30:
            continue
        y = s.loc[common_dates] - rf.loc[common_dates]
        x = gt.market_return.loc[common_dates] - rf.loc[common_dates]
        beta_hat = np.polyfit(x.values, y.values, deg=1)[0]
        estimated[stock_id] = beta_hat

    est = pd.Series(estimated)
    true = gt.true_betas.loc[est.index]
    corr = np.corrcoef(est.values, true.values)[0, 1]
    assert corr > 0.8, f"expected strong beta recovery, got correlation {corr:.3f}"


def test_lagged_signal_predicts_return_with_expected_sign_and_magnitude(synthetic_source):
    """Pooled OLS smoke test for the signal -> next-return relationship
    baked into the generator; a real Fama-MacBeth estimate (Phase 1, Task D)
    should do at least this well.
    """
    gt = synthetic_source.ground_truth
    returns = synthetic_source.get_returns(START, END)
    signal = synthetic_source.get_financials(START, END, fields=["signal"])

    returns = returns.sort_values([STOCK_COL, DATE_COL])
    signal = signal.sort_values([STOCK_COL, DATE_COL])
    signal["date"] = signal.groupby(STOCK_COL)[DATE_COL].shift(-1)  # align signal_t -> ret_{t+1}
    merged = returns.merge(signal, on=[STOCK_COL, DATE_COL], how="inner")

    coef = np.polyfit(merged["signal"].values, merged[RETURN_COL].values, deg=1)[0]
    assert coef == pytest.approx(gt.true_signal_coef, abs=0.02)
