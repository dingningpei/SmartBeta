"""PQC — gap-aware HAC tests (Dataset v1.2, P-Q).

Every test is offline and synthetic.  A socket guard is installed for the
whole module and a credential sentinel is asserted absent.  The pinned
Phase-7 files (``inference.py``, ``metrics.py``, ``settings.py``) are
hash-checked read-only; nothing here builds or reads a production artifact.

The frozen contract is
``worker_tasks/dataset_v1/task-pqc-gap-aware-hac.md`` (spec item numbers in
§6, mutations G1-G20 in §7).  Test names are ``test_itemNN_*`` so the
test-to-item map in the completion report is mechanical.
"""

from __future__ import annotations

import ast
import hashlib
import math
import os
import socket
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smart_beta.config.settings import DEFAULT_SETTINGS
from smart_beta.engines import gap_hac
from smart_beta.engines.gap_hac import GapAwareHACResult, gap_aware_mean_tstat
from smart_beta.evaluation.metrics import _newey_west_aggregate

REPO_ROOT = Path(__file__).resolve().parents[1]
CREDENTIAL_SENTINEL = "pqc-credential-sentinel-91d7"

# Pinned read-only Phase-7 files (frozen §"Pinned read-only code").
_PINNED_SHA256 = {
    "smart_beta/engines/inference.py": (
        "516b290e37098a27d8f5d9864cf473ef4d541cb2465359b3d340b5948c825b51"
    ),
    "smart_beta/evaluation/metrics.py": (
        "747282a2ee7cc7b63d0511d29ab3bdf8c3c9aceea9fb965dd64de543d76831a8"
    ),
    "smart_beta/config/settings.py": (
        "08ecf0d24e2376bd1346d83e165b254f7e0d2a60408d48f0bfeb84221bee43af"
    ),
}


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    """No PQC test may open a socket."""

    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQC tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


def test_credential_sentinel_absent(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", CREDENTIAL_SENTINEL)
    monkeypatch.setenv("TUSHARE_PROXY_TOKEN", CREDENTIAL_SENTINEL)
    monkeypatch.setenv("DEEPSEEK_API_KEY", CREDENTIAL_SENTINEL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", CREDENTIAL_SENTINEL)
    monkeypatch.setenv("OPENAI_API_KEY", CREDENTIAL_SENTINEL)

    arr = np.array([1.0, 2.0, np.nan, 4.0, 5.0, 6.0, 7.0], dtype="float64")
    result = gap_aware_mean_tstat(arr)
    assert result.status == "OK"

    captured = capsys.readouterr()
    assert CREDENTIAL_SENTINEL not in captured.out
    assert CREDENTIAL_SENTINEL not in captured.err


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reference_mean_S(values, lags: int) -> tuple[float, float]:
    """Independent explicit-loop reference for the frozen §4 definition."""
    arr = np.asarray(values, dtype="float64")
    finite = np.isfinite(arr)
    positions = [int(i) for i in np.flatnonzero(finite)]
    n = len(positions)
    mean = float(arr[positions].mean())
    resid = {p: float(arr[p] - mean) for p in positions}
    long_run = float(sum(resid[p] * resid[p] for p in positions))
    for k in range(1, lags + 1):
        weight = 1.0 - k / (lags + 1.0)
        total = 0.0
        for p in positions:
            if p + k in resid:
                total += resid[p] * resid[p + k]
        long_run += 2.0 * weight * total
    return mean, long_run


def _implied_series_long_run(result: GapAwareHACResult) -> float:
    """Recover the frozen long-run sum S from a result's mean and t-stat."""
    return (result.mean / result.t_stat) ** 2 * result.n_obs**2


def _assert_t_parity(actual: float, expected: float) -> None:
    assert math.isfinite(actual), actual
    assert math.isfinite(expected), expected
    assert abs(actual - expected) <= max(1e-10 * abs(expected), 1e-12)


def _bits(value: float) -> bytes:
    return struct.pack("d", float(value))


# ---------------------------------------------------------------------------
# Item 1 — gap-free parity over seeds and sizes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("n", [2, 3, 5, 7, 8, 13, 50, 200])
def test_item01_gap_free_parity(seed: int, n: int) -> None:
    rng = np.random.default_rng(seed)
    arr = rng.normal(size=n) * rng.uniform(0.1, 3.0)

    result = gap_aware_mean_tstat(arr, lags=DEFAULT_SETTINGS.newey_west_lags)
    ref_mean, ref_t, ref_n = _newey_west_aggregate(arr)

    assert struct.pack("d", result.mean) == struct.pack("d", ref_mean)
    assert result.n_obs == ref_n
    assert result.status == "OK"
    _assert_t_parity(result.t_stat, ref_t)


# ---------------------------------------------------------------------------
# Item 2 — leading / trailing non-finite positions only
# ---------------------------------------------------------------------------


def test_item02_leading_trailing_nan_equals_compressed() -> None:
    arr = np.array([np.nan, np.nan, 1.0, 2.0, 3.0, 4.0, 5.0, np.nan])
    result = gap_aware_mean_tstat(arr)
    ref_mean, ref_t, ref_n = _newey_west_aggregate(arr)

    assert struct.pack("d", result.mean) == struct.pack("d", ref_mean)
    assert result.n_obs == ref_n
    assert result.n_interior_gaps == 0
    assert result.status == "OK"
    _assert_t_parity(result.t_stat, ref_t)

    leading_only = np.array([np.nan, 1.0, 2.0, 3.0, 4.0, 5.0])
    trailing_only = np.array([1.0, 2.0, 3.0, 4.0, 5.0, np.nan])
    assert gap_aware_mean_tstat(leading_only).n_interior_gaps == 0
    assert gap_aware_mean_tstat(trailing_only).n_interior_gaps == 0


# ---------------------------------------------------------------------------
# Item 3 — canonical gap example, hand-computed Γ_k
# ---------------------------------------------------------------------------


def test_item03_canonical_hand_computed() -> None:
    # Positions: t1=0, t2=1, gaps 2..6, t8=7, t9=8.
    arr = np.array([2.0, 5.0, np.nan, np.nan, np.nan, np.nan, np.nan, 3.0, 6.0])
    result = gap_aware_mean_tstat(arr, lags=6)

    assert result.n_obs == 4
    assert result.n_index == 9
    assert result.n_interior_gaps == 5
    assert result.status == "OK"
    assert result.mean == 4.0

    # Hand computation: mean 4, residuals (-2, 1, -1, 2) at (0, 1, 7, 8).
    # Γ_1 = u0 u1 + u7 u8 = -4 ; Γ_6 = u1 u7 = -1 ; all other Γ_k = 0.
    # S = 10 + 2[(6/7)(-4) + (1/7)(-1)] = 20/7.
    assert math.isclose(_implied_series_long_run(result), 20.0 / 7.0, rel_tol=1e-12)

    rng = np.random.default_rng(11)
    for _ in range(20):
        vals = rng.normal(size=4)
        arr_local = np.array(
            [vals[0], vals[1], np.nan, np.nan, np.nan, np.nan, np.nan, vals[2], vals[3]]
        )
        res = gap_aware_mean_tstat(arr_local, lags=6)
        mean, S = _reference_mean_S(arr_local, 6)
        assert struct.pack("d", res.mean) == struct.pack("d", mean)
        assert math.isclose(_implied_series_long_run(res), S, rel_tol=1e-12, abs_tol=1e-15)

    # t2 (position 1) and t8 (position 7) never pair at k = 1: at L = 1 the
    # long-run sum is Gamma_0 + Gamma_1 = 10 + Gamma_1, so recovering 6 proves
    # Gamma_1 = -4 (the adjacent pairs only).  The presence of the t2/t8 pair at
    # original distance 6 is what the full S = 20/7 above already pins.
    short = gap_aware_mean_tstat(arr, lags=1)
    assert math.isclose(
        _implied_series_long_run(short), 6.0, rel_tol=1e-12
    )


# ---------------------------------------------------------------------------
# Item 4 — a gap at distance exactly L+1 contributes nothing
# ---------------------------------------------------------------------------


def test_item04_distance_l_plus_one_contributes_nothing() -> None:
    arr = np.array([1.0, np.nan, np.nan, np.nan, 2.0])

    res_l3 = gap_aware_mean_tstat(arr, lags=3)
    assert res_l3.n_obs == 2
    assert res_l3.status == "OK"
    assert math.isclose(_implied_series_long_run(res_l3), 0.5, rel_tol=1e-12)

    # At L = 4 the distance-4 pair is (correctly) included.
    res_l4 = gap_aware_mean_tstat(arr, lags=4)
    assert math.isclose(_implied_series_long_run(res_l4), 0.4, rel_tol=1e-12)
    assert not math.isclose(
        _implied_series_long_run(res_l3), _implied_series_long_run(res_l4)
    )


# ---------------------------------------------------------------------------
# Item 5 — exact Bartlett weights and the L = 0 White-type sum
# ---------------------------------------------------------------------------


def test_item05_weights_exact_and_l_zero() -> None:
    arr = np.array([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0])

    for lags in (1, 2, 3, 6):
        result = gap_aware_mean_tstat(arr, lags=lags)
        mean, S = _reference_mean_S(arr, lags)
        assert result.mean == mean
        assert math.isclose(_implied_series_long_run(result), S, rel_tol=1e-12)

    # L = 0 reduces to the White-type sum and must match statsmodels at 0 lags.
    result_l0 = gap_aware_mean_tstat(arr, lags=0)
    from smart_beta.engines.inference import newey_west_ols

    expected = float(newey_west_ols(arr, np.ones((arr.size, 1)), 0).tvalues[0])
    assert result_l0.lags == 0
    _assert_t_parity(result_l0.t_stat, expected)

    mean0, S0 = _reference_mean_S(arr, 0)
    assert result_l0.mean == mean0
    assert math.isclose(_implied_series_long_run(result_l0), S0, rel_tol=1e-12)


# ---------------------------------------------------------------------------
# Item 6 — n = 0, 1, 2 edge statuses
# ---------------------------------------------------------------------------


def test_item06_edge_statuses() -> None:
    empty = gap_aware_mean_tstat(np.array([], dtype="float64"))
    assert empty.status == "INSUFFICIENT_SUPPORT"
    assert math.isnan(empty.mean) and math.isnan(empty.t_stat)
    assert empty.n_obs == 0 and empty.n_index == 0 and empty.n_interior_gaps == 0

    single = gap_aware_mean_tstat(np.array([5.0]))
    assert single.status == "INSUFFICIENT_SUPPORT"
    assert math.isnan(single.mean) and math.isnan(single.t_stat)
    assert single.n_obs == 1

    pair = gap_aware_mean_tstat(np.array([1.0, 2.0]))
    assert pair.status == "OK"
    assert pair.mean == 1.5
    assert math.isfinite(pair.t_stat)

    # A two-point series separated by more than L still has positive support.
    separated = gap_aware_mean_tstat(np.array([1.0, np.nan, 2.0]), lags=1)
    assert separated.status == "OK"
    assert separated.n_obs == 2 and separated.n_interior_gaps == 1


# ---------------------------------------------------------------------------
# Item 7 — zero range
# ---------------------------------------------------------------------------


def test_item07_zero_range() -> None:
    arr = np.array([0.05, 0.05, np.nan, 0.05, 0.05])
    result = gap_aware_mean_tstat(arr)
    assert result.status == "ZERO_RANGE"
    assert result.mean == 0.05
    assert math.isnan(result.t_stat)
    assert result.n_obs == 4


# ---------------------------------------------------------------------------
# Item 8 — fail-closed non-positive / non-finite long-run variance
# ---------------------------------------------------------------------------
#
# NOTE (reported blocker): ``S <= 0`` is mathematically unreachable for the
# frozen masked-Bartlett definition.  The lag weights w_k = 1 - k/(L+1) make
# the estimator a principal submatrix of the positive-semidefinite Bartlett
# Toeplitz, so S >= 0 identically (see
# ``test_item08_evidence_masked_long_run_is_psd``).  The fail-closed status is
# therefore exercised here through the other trigger the frozen spec lists:
# a non-finite long-run variance.


def test_item08_nonpositive_variance_fail_closed() -> None:
    arr = np.array([1e300, 1.1e300, np.nan, -1e300], dtype="float64")
    result = gap_aware_mean_tstat(arr, lags=2)
    assert result.status == "NONPOSITIVE_VARIANCE"
    assert math.isnan(result.t_stat)
    assert result.n_obs == 3

    # No exception is raised for a degenerate (all-gap) input either.
    all_gap = gap_aware_mean_tstat(np.array([np.nan, np.inf, -np.inf]))
    assert all_gap.status == "INSUFFICIENT_SUPPORT"
    assert math.isnan(all_gap.t_stat)


def test_item08_evidence_masked_long_run_is_psd() -> None:
    """Diagnostic evidence contradicting the frozen §9 "not PSD" claim."""
    lags = 6
    size = 24
    kernel = np.zeros((size, size))
    for i in range(size):
        for j in range(size):
            k = abs(i - j)
            if k <= lags:
                kernel[i, j] = 1.0 - k / (lags + 1.0)
    assert np.linalg.eigvalsh(kernel).min() > 0.0

    rng = np.random.default_rng(23)
    for _ in range(200):
        arr = rng.normal(size=size)
        arr[rng.random(size) < 0.4] = np.nan
        if np.isfinite(arr).sum() < 2:
            continue
        _, S = _reference_mean_S(arr, lags)
        assert S >= 0.0


# ---------------------------------------------------------------------------
# Item 9 — ±inf are gaps
# ---------------------------------------------------------------------------


def test_item09_inf_are_gaps() -> None:
    arr = np.array([1.0, np.inf, 2.0, -np.inf, 3.0])
    result = gap_aware_mean_tstat(arr)
    same = gap_aware_mean_tstat(np.array([1.0, np.nan, 2.0, np.nan, 3.0]))

    assert result.n_obs == 3
    assert result.n_index == 5
    assert result.n_interior_gaps == 2
    assert result.status == same.status == "OK"
    assert _bits(result.mean) == _bits(same.mean)
    assert _bits(result.t_stat) == _bits(same.t_stat)


# ---------------------------------------------------------------------------
# Item 10 — timezone / locale invariance
# ---------------------------------------------------------------------------


_TZ_SCRIPT = (
    "import struct\n"
    "import numpy as np\n"
    "from smart_beta.engines.gap_hac import gap_aware_mean_tstat\n"
    "arr = np.array([1.0, 2.0, np.nan, 4.5, np.nan, -3.0, 2.5, 0.0], dtype='float64')\n"
    "r = gap_aware_mean_tstat(arr, lags=4)\n"
    "print(repr((struct.pack('d', r.mean).hex(), struct.pack('d', r.t_stat).hex(),"
    " r.n_obs, r.n_index, r.n_interior_gaps, r.status, r.lags)))\n"
)


def _run_tz_script(env_overrides: dict[str, str]) -> str:
    env = dict(os.environ)
    for name in (
        "TIINGO_API_KEY",
        "TUSHARE_PROXY_TOKEN",
        "TUSHARE_BASIC_PROXY_TOKEN",
        "TUSHARE_API_TOKEN",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
    ):
        env.pop(name, None)
    env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, "-c", _TZ_SCRIPT],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def test_item10_timezone_locale_invariance() -> None:
    baseline = _run_tz_script({})
    shifted = _run_tz_script({"TZ": "Asia/Tokyo", "LC_ALL": "C"})
    assert baseline == shifted


# ---------------------------------------------------------------------------
# Item 11 — input immutability
# ---------------------------------------------------------------------------


def test_item11_input_immutable() -> None:
    arr = np.array([1.0, 2.0, np.nan, 4.0, 5.0, 6.0, 7.0, 8.0])
    before = arr.copy()
    gap_aware_mean_tstat(arr, lags=3)
    assert np.array_equal(arr, before, equal_nan=True)

    data = [1.0, np.nan, 3.0, 4.0]
    snapshot = list(data)
    gap_aware_mean_tstat(data)
    assert all(
        (a == b) or (math.isnan(a) and math.isnan(b))
        for a, b in zip(data, snapshot)
    )


# ---------------------------------------------------------------------------
# Item 12 — input validation
# ---------------------------------------------------------------------------


def test_item12_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        gap_aware_mean_tstat(np.array([[1.0, 2.0], [3.0, 4.0]]))

    with pytest.raises(TypeError):
        gap_aware_mean_tstat(pd.Series([1.0, 2.0]))

    with pytest.raises(TypeError):
        gap_aware_mean_tstat([1.0, 2.0], lags=1.5)

    with pytest.raises(TypeError):
        gap_aware_mean_tstat([1.0, 2.0], lags=True)

    with pytest.raises(TypeError):
        gap_aware_mean_tstat([1.0, 2.0], lags="6")

    with pytest.raises(ValueError):
        gap_aware_mean_tstat([1.0, 2.0], lags=-1)


# ---------------------------------------------------------------------------
# Item 13 — pinned read-only code hashes
# ---------------------------------------------------------------------------


def test_item13_pinned_code_hashes() -> None:
    for relative, pinned in _PINNED_SHA256.items():
        digest = hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest()
        assert digest == pinned, relative


# ---------------------------------------------------------------------------
# Item 14 — static check: no newey_west_ols in the module
# ---------------------------------------------------------------------------


def test_item14_module_never_uses_newey_west_ols() -> None:
    source = Path(gap_hac.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden = "newey_west_ols"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[-1] != forbidden
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or node.module.split(".")[-1] != forbidden
            for alias in node.names:
                assert alias.name != forbidden
        elif isinstance(node, ast.Attribute):
            assert node.attr != forbidden
        elif isinstance(node, ast.Name):
            assert node.id != forbidden

    assert not hasattr(gap_hac, forbidden)


# ---------------------------------------------------------------------------
# Item 15 — default lag equals the settings value
# ---------------------------------------------------------------------------


def test_item15_default_lag_is_settings_value() -> None:
    arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    result = gap_aware_mean_tstat(arr)
    assert result.lags == DEFAULT_SETTINGS.newey_west_lags


# ---------------------------------------------------------------------------
# Item 16 — bit-equal mean against an in-index-order numpy reference
# ---------------------------------------------------------------------------


def test_item16_mean_bit_equal_to_numpy_reference() -> None:
    rng = np.random.default_rng(31)
    for _ in range(50):
        arr = rng.normal(size=17) * 1e3
        arr[rng.random(17) < 0.35] = np.nan
        if np.isfinite(arr).sum() < 2:
            continue
        result = gap_aware_mean_tstat(arr, lags=2)
        reference = np.asarray(arr, dtype="float64")[np.isfinite(arr)].mean()
        assert _bits(result.mean) == _bits(float(reference))
