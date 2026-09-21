"""Tests for the P6-G reference / migration fixtures.

These tests exercise the trusted end-to-end Phase 6 path for the reference
:class:`~smart_beta.spec.factor_spec.FactorSpec` vocabulary fixtures defined
in :mod:`smart_beta.spec.reference`::

    FactorSpec -> DataRequirement admission -> P6-F evaluate_factor
              -> P6-D evaluator -> P6-E transforms -> expected values

Every expectation is derived **independently** of the production evaluation
path: either as plain pandas arithmetic written here, or as a closed-form
value computed from the four hand-specified reference securities. A fixture
is therefore never "confirmed" by re-running the same code under test.

Fixture principles checked here: deterministic, small, human-auditable,
vendor-independent, PIT-safe by construction, and explicit about expected
values. Scope is respected: no CH3 / ``profit_dedt`` dependency, no
portfolio-level CAPM/CH3/CH4 benchmark re-expression, and no live provider
call. The turnover-ratio fixture is a reference **behaviour** fixture only --
not CH4 empirical replication, not benchmark certification, not
production-data certification, and not economic validation.
"""

from __future__ import annotations

import ast
import json
import math
import pathlib

import numpy as np
import pandas as pd
import pytest

from smart_beta.data.schema import DATE_COL, FACTOR_PANEL_SCHEMA, STOCK_COL, VALUE_COL
from smart_beta.spec import engine as eng
from smart_beta.spec import evaluator as ev
from smart_beta.spec import reference as ref
from smart_beta.spec.requirements import find_vendor_names

# ---------------------------------------------------------------------------
# independent helpers
# ---------------------------------------------------------------------------
#: The nine frozen section 10 reference categories and their fixture names.
_FROZEN_FIXTURE_NAMES = (
    "raw_market_cap",
    "lagged_return",
    "rolling_mean_return",
    "ratio_of_fields",
    "difference_of_fields",
    "standardize_market_cap",
    "rank_market_cap",
    "winsorize_market_cap",
    "turnover_ratio",
)

_FORBIDDEN_SPEC_TOKENS = ("ch3", "ch4", "capm", "profit_dedt", "fama", "french")


def _wide(role: str) -> pd.DataFrame:
    """The independent reference frame for ``role`` (a fresh copy)."""
    return ref.reference_dataset()[role]


def _long(wide: pd.DataFrame) -> pd.DataFrame:
    """Independent long ``(date, stock_id, value)`` reshaping of a frame.

    Deliberately does not use the production reshaping helper: this is part
    of the independent expectation route.
    """
    indexed = wide.copy()
    indexed.index = pd.DatetimeIndex(indexed.index, name=DATE_COL)
    long = indexed.reset_index().melt(
        id_vars=DATE_COL, var_name=STOCK_COL, value_name=VALUE_COL
    )
    long[DATE_COL] = pd.to_datetime(long[DATE_COL])
    long[STOCK_COL] = long[STOCK_COL].astype("string")
    long[VALUE_COL] = long[VALUE_COL].astype("float64")
    long = long.sort_values([DATE_COL, STOCK_COL], kind="mergesort")
    return long.reset_index(drop=True)[[DATE_COL, STOCK_COL, VALUE_COL]]


def _panel(fixture: ref.ReferenceFixture) -> pd.DataFrame:
    """Run one fixture through the trusted end-to-end path (P6-F -> D -> E)."""
    result = eng.evaluate_factor(fixture.spec, fixture.inputs)
    return result.evaluation.panel


def _get(panel: pd.DataFrame, stock: str, date: str) -> float:
    """The single value for ``(stock, date)`` in a long factor panel."""
    match = panel[
        (panel[STOCK_COL] == stock) & (panel[DATE_COL] == pd.Timestamp(date))
    ]
    assert len(match) == 1
    return float(match[VALUE_COL].iloc[0])


def _assert_matches(panel: pd.DataFrame, expected: pd.DataFrame) -> None:
    """Compare a result panel to an independently computed expectation."""
    assert list(panel.columns) == [DATE_COL, STOCK_COL, VALUE_COL]
    pd.testing.assert_frame_equal(panel, expected)


# ---------------------------------------------------------------------------
# registry / contract-level checks
# ---------------------------------------------------------------------------
def test_reference_fixture_registry_matches_the_frozen_fixture_list() -> None:
    """The registry covers exactly the nine frozen section 10 categories."""
    fixtures = ref.reference_fixtures()
    assert tuple(fixture.name for fixture in fixtures) == _FROZEN_FIXTURE_NAMES
    assert len(fixtures) == 9
    # Every fixture declares the roles its expression references.
    for fixture in fixtures:
        assert set(fixture.inputs) == set(fixture.spec.referenced_roles)


def test_reference_specs_are_vendor_free_and_not_benchmark_expressions() -> None:
    """No vendor name and no CAPM/CH3/CH4 benchmark token appears anywhere."""
    for spec in ref.reference_specs():
        payload = json.dumps(spec.to_dict(), sort_keys=True)
        assert find_vendor_names(payload) == ()
        lowered = payload.lower()
        for token in _FORBIDDEN_SPEC_TOKENS:
            assert token not in lowered, (spec.id, token)
    for frame in ref.reference_dataset().values():
        for column in frame.columns:
            assert find_vendor_names(str(column)) == ()


def test_reference_specs_are_deterministic_and_frozen() -> None:
    """Rebuilding every spec yields the same content hash and version."""
    first = ref.reference_specs()
    second = ref.reference_specs()
    assert tuple(spec.version for spec in first) == tuple(
        spec.version for spec in second
    )
    for spec in first:
        assert len(spec.version) == 64
        assert all(character in "0123456789abcdef" for character in spec.version)


def test_reference_dataset_returns_fresh_copies() -> None:
    """Mutating a returned frame never touches the frozen reference panel."""
    frame = ref.reference_dataset()["market_cap"]
    frame.iloc[0, 0] = -1.0
    assert ref.reference_dataset()["market_cap"].iloc[0, 0] == 100.0


def test_reference_fixtures_admit_through_the_trusted_boundary() -> None:
    """Every fixture admits fail-closed and preserves CONSTRUCTED provenance."""
    for fixture in ref.reference_fixtures():
        result = eng.admit(fixture.spec, fixture.inputs)
        assert result.admitted, (fixture.name, result.to_dict())
        assert result.factor_id == fixture.spec.id
        for admission in result.aliases:
            assert admission.admitted
            assert admission.reasons == ()
            assert admission.evidence_class is eng.EvidenceClass.CONSTRUCTED


def test_all_reference_fixtures_evaluate_end_to_end_deterministically() -> None:
    """End-to-end evaluation is deterministic and matches the factor schema."""
    for fixture in ref.reference_fixtures():
        first = eng.evaluate_factor(fixture.spec, fixture.inputs)
        second = eng.evaluate_factor(fixture.spec, fixture.inputs)
        pd.testing.assert_frame_equal(
            first.evaluation.panel, second.evaluation.panel
        )
        assert first.content_hash == second.content_hash
        assert first.evaluation.panel.shape == (24, 3)
        FACTOR_PANEL_SCHEMA.validate(
            first.evaluation.panel, name=f"reference {fixture.name}"
        )


# ---------------------------------------------------------------------------
# 1. raw-field factor
# ---------------------------------------------------------------------------
def test_raw_market_cap_factor_matches_hand_computed_values() -> None:
    """A raw market-cap field is reproduced exactly (identity over a role)."""
    fixture = ref.reference_fixture("raw_market_cap")
    panel = _panel(fixture)
    _assert_matches(panel, _long(_wide("market_cap")))
    # Closed form: values are the fixture constants, untransformed.
    assert _get(panel, "A", "2021-01-31") == 100.0
    assert _get(panel, "C", "2021-03-31") == 60.0
    assert _get(panel, "D", "2021-06-30") == 300.0


# ---------------------------------------------------------------------------
# 2. lagged-return factor
# ---------------------------------------------------------------------------
def test_lagged_return_factor_matches_hand_computed_values() -> None:
    """Lag-by-1 shifts strictly backwards; the first observation is NaN."""
    fixture = ref.reference_fixture("lagged_return")
    panel = _panel(fixture)
    _assert_matches(panel, _long(_wide("return").shift(1, axis=0)))
    assert math.isnan(_get(panel, "A", "2021-01-31"))
    assert _get(panel, "A", "2021-02-28") == 0.01
    assert _get(panel, "D", "2021-02-28") == 0.04
    assert _get(panel, "D", "2021-03-31") == -0.02


# ---------------------------------------------------------------------------
# 3. rolling-mean return factor
# ---------------------------------------------------------------------------
def test_rolling_mean_return_factor_matches_hand_computed_values() -> None:
    """A trailing 3-period mean is undefined until the window is full."""
    fixture = ref.reference_fixture("rolling_mean_return")
    panel = _panel(fixture)
    expected = _long(
        _wide("return").rolling(window=3, min_periods=3, center=False).mean()
    )
    _assert_matches(panel, expected)
    assert math.isnan(_get(panel, "A", "2021-01-31"))
    assert math.isnan(_get(panel, "A", "2021-02-28"))
    # mean(0.01, 0.02, 0.03) = 0.02; mean(0.02, 0.03, 0.04) = 0.03.
    assert _get(panel, "A", "2021-03-31") == pytest.approx(0.02)
    assert _get(panel, "A", "2021-04-30") == pytest.approx(0.03)


# ---------------------------------------------------------------------------
# 4. ratio of two fields
# ---------------------------------------------------------------------------
def test_ratio_of_fields_factor_matches_hand_computed_values() -> None:
    """revenue / earnings is elementwise; the fixture ratio is exactly 10."""
    fixture = ref.reference_fixture("ratio_of_fields")
    panel = _panel(fixture)
    _assert_matches(panel, _long(_wide("revenue") / _wide("earnings")))
    # Closed form: revenue is exactly 10x earnings for every observation.
    assert (panel[VALUE_COL] == 10.0).all()
    assert _get(panel, "A", "2021-01-31") == 10.0
    assert _get(panel, "D", "2021-06-30") == 10.0


# ---------------------------------------------------------------------------
# 5. difference of two fields
# ---------------------------------------------------------------------------
def test_difference_of_fields_factor_matches_hand_computed_values() -> None:
    """revenue - earnings is elementwise subtraction."""
    fixture = ref.reference_fixture("difference_of_fields")
    panel = _panel(fixture)
    _assert_matches(panel, _long(_wide("revenue") - _wide("earnings")))
    assert _get(panel, "A", "2021-01-31") == 90.0
    assert _get(panel, "A", "2021-06-30") == 135.0
    assert _get(panel, "C", "2021-01-31") == 72.0


# ---------------------------------------------------------------------------
# 6. cross-sectional standardize
# ---------------------------------------------------------------------------
def test_standardize_of_market_cap_factor_matches_closed_form() -> None:
    """Per-date z-score uses the population standard deviation."""
    fixture = ref.reference_fixture("standardize_market_cap")
    panel = _panel(fixture)
    market_cap = _wide("market_cap")
    centered = market_cap.sub(market_cap.mean(axis=1), axis=0)
    expected = _long(centered.div(market_cap.std(axis=1, ddof=0), axis=0))
    _assert_matches(panel, expected)
    # Closed form for 2021-01-31: values [100, 200, 50, 400], mean 187.5 and
    # population variance 17968.75, so A standardizes to -87.5/sqrt(17968.75).
    assert _get(panel, "A", "2021-01-31") == pytest.approx(
        -87.5 / math.sqrt(17968.75), rel=1e-12
    )
    # Every non-degenerate cross-section standardizes to mean zero.
    for date in ("2021-01-31", "2021-04-30"):
        row = panel[panel[DATE_COL] == pd.Timestamp(date)][VALUE_COL]
        assert float(row.mean()) == pytest.approx(0.0, abs=1e-12)


# ---------------------------------------------------------------------------
# 7. cross-sectional rank
# ---------------------------------------------------------------------------
def test_rank_of_market_cap_factor_matches_hand_computed_values() -> None:
    """Per-date average ranks, including the tie on the final date."""
    fixture = ref.reference_fixture("rank_market_cap")
    panel = _panel(fixture)
    expected = _long(
        _wide("market_cap").rank(axis=1, method="average", na_option="keep")
    )
    _assert_matches(panel, expected)
    # 2021-01-31: [100, 200, 50, 400] -> A=2, B=3, C=1, D=4.
    assert _get(panel, "A", "2021-01-31") == 2.0
    assert _get(panel, "B", "2021-01-31") == 3.0
    assert _get(panel, "C", "2021-01-31") == 1.0
    assert _get(panel, "D", "2021-01-31") == 4.0
    # 2021-06-30: A and B are tied at 150 -> average rank 2.5.
    assert _get(panel, "A", "2021-06-30") == 2.5
    assert _get(panel, "B", "2021-06-30") == 2.5
    assert _get(panel, "C", "2021-06-30") == 1.0
    assert _get(panel, "D", "2021-06-30") == 4.0


# ---------------------------------------------------------------------------
# 8. cross-sectional winsorize
# ---------------------------------------------------------------------------
def test_winsorize_of_market_cap_factor_matches_hand_computed_values() -> None:
    """Per-date winsorization at explicit (0.25, 0.75) quantile bounds."""
    fixture = ref.reference_fixture("winsorize_market_cap")
    panel = _panel(fixture)
    market_cap = _wide("market_cap")
    low = market_cap.quantile(0.25, axis=1, interpolation="linear")
    high = market_cap.quantile(0.75, axis=1, interpolation="linear")
    expected = _long(market_cap.clip(lower=low, upper=high, axis=0))
    _assert_matches(panel, expected)
    # 2021-01-31: [50, 100, 200, 400] -> bounds 87.5 / 250.
    assert _get(panel, "C", "2021-01-31") == 87.5
    assert _get(panel, "D", "2021-01-31") == 250.0
    assert _get(panel, "A", "2021-01-31") == 100.0  # interior, unchanged
    assert _get(panel, "B", "2021-01-31") == 200.0  # interior, unchanged
    # 2021-06-30: [75, 150, 150, 300] -> bounds 131.25 / 187.5.
    assert _get(panel, "C", "2021-06-30") == 131.25
    assert _get(panel, "D", "2021-06-30") == 187.5
    assert _get(panel, "A", "2021-06-30") == 150.0


# ---------------------------------------------------------------------------
# 9. CH4-like turnover-ratio factor
# ---------------------------------------------------------------------------
def test_turnover_ratio_factor_matches_hand_computed_values() -> None:
    """mean(turnover, 3) / mean(turnover, 5), exactly, at full windows."""
    fixture = ref.reference_fixture("turnover_ratio")
    panel = _panel(fixture)
    turnover = _wide("turnover")
    numerator = turnover.rolling(window=3, min_periods=3).mean()
    denominator = turnover.rolling(window=5, min_periods=5).mean()
    _assert_matches(panel, _long(numerator / denominator))
    # Short window needs 3 obs, long window 5 -> first two dates are NaN.
    assert math.isnan(_get(panel, "A", "2021-01-31"))
    assert math.isnan(_get(panel, "A", "2021-02-28"))
    # 2021-05-31: mean3 A = 0.8, mean5 A = 0.7 -> 8/7.
    assert _get(panel, "A", "2021-05-31") == pytest.approx(8.0 / 7.0, rel=1e-12)
    # 2021-06-30: mean3 A = 0.9, mean5 A = 0.8 -> 9/8.
    assert _get(panel, "A", "2021-06-30") == pytest.approx(9.0 / 8.0, rel=1e-12)
    # The constant-turnover security is exactly 1.0 at full windows.
    assert _get(panel, "D", "2021-06-30") == 1.0


def test_turnover_ratio_fixture_is_constructed_evidence_only() -> None:
    """The behaviour fixture is CONSTRUCTED and makes no certification claim."""
    fixture = ref.reference_fixture("turnover_ratio")
    result = eng.evaluate_factor(fixture.spec, fixture.inputs)
    for admission in result.admission.aliases:
        assert admission.evidence_class is eng.EvidenceClass.CONSTRUCTED
    # It consumes no CH3-only field and names no vendor.
    assert fixture.spec.aliases == ("turnover",)
    assert fixture.spec.inputs[0].requirement.semantic_id == "turnover"


# ---------------------------------------------------------------------------
# explicit lower-layer coverage (deliberately bypasses P6-F)
# ---------------------------------------------------------------------------
def test_lagged_return_fixture_at_the_evaluator_layer() -> None:
    """A lower-layer check that deliberately bypasses P6-F (targets P6-D).

    This test is *not* the trusted end-to-end path: it feeds bare value
    frames straight to the P6-D evaluator to show the reference fixture also
    reproduces expected behaviour one layer down. The representative
    end-to-end fixtures are the tests above.
    """
    fixture = ref.reference_fixture("lagged_return")
    frames = {alias: item.values for alias, item in fixture.inputs.items()}
    direct = ev.evaluate(fixture.spec, frames)
    end_to_end = eng.evaluate_factor(fixture.spec, fixture.inputs)
    pd.testing.assert_frame_equal(direct.panel, end_to_end.evaluation.panel)


# ---------------------------------------------------------------------------
# trust-boundary meta-check
# ---------------------------------------------------------------------------
def test_reference_module_has_no_vendor_pit_or_dynamic_execution() -> None:
    """Statically inspect the reference module (never execute it)."""
    source = pathlib.Path(ref.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    forbidden = [
        module
        for module in imported
        if "vendor" in module or module.startswith("smart_beta.pit")
    ]
    assert forbidden == []

    dynamic = {"eval", "exec", "compile", "__import__", "open"}
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert dynamic.isdisjoint(calls)


def test_reference_panel_values_are_finite_and_complete() -> None:
    """The reference data contains no hidden surprise for a human auditor."""
    for role, frame in ref.reference_dataset().items():
        assert frame.shape == (len(ref.REFERENCE_DATES), len(ref.REFERENCE_STOCKS))
        assert list(frame.columns) == list(ref.REFERENCE_STOCKS)
        values = frame.to_numpy(dtype="float64")
        assert np.isfinite(values).all(), role
