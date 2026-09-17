# TODO(P4C-9): update for the new build_fama_macbeth_premium signature.
"""End-to-end tests for :mod:`smart_beta.pipelines.fama_macbeth_premium`.

The test bodies below were moved verbatim from the old shared
``tests/test_pipelines.py`` (task P4C-7). They still call the pre-Phase-4C
``build_universe_and_tradable_returns`` signature and will not run until
P4C-9 migrates them.
"""

from __future__ import annotations

import pandas as pd
import pytest

from smart_beta.pipelines import (
    FamaMacBethPipelineResult,
    build_fama_macbeth_premium,
)

START = "2015-01-31"
END = "2030-12-31"


# ---------------------------------------------------------------------------
# Required test 5: end-to-end Fama-MacBeth known answer
# ---------------------------------------------------------------------------


def test_pipeline_recovers_true_signal_coefficient(synthetic_source):
    gt = synthetic_source.ground_truth

    result = build_fama_macbeth_premium(
        synthetic_source, ["signal"], START, END
    )

    assert isinstance(result, FamaMacBethPipelineResult)
    assert result.result.mean_coefficients["signal"] == pytest.approx(
        gt.true_signal_coef, abs=0.01
    )
    assert abs(result.result.t_stats["signal"]) > 3.0


# ---------------------------------------------------------------------------
# Required test 7 (Fama-MacBeth half): determinism
# ---------------------------------------------------------------------------


def test_pipelines_are_deterministic(synthetic_source):
    first_fm = build_fama_macbeth_premium(synthetic_source, ["signal"], START, END)
    second_fm = build_fama_macbeth_premium(synthetic_source, ["signal"], START, END)

    assert isinstance(first_fm, FamaMacBethPipelineResult)
    pd.testing.assert_frame_equal(first_fm.universe, second_fm.universe)
    pd.testing.assert_frame_equal(first_fm.aligned_panel, second_fm.aligned_panel)
    pd.testing.assert_frame_equal(
        first_fm.result.coefficients, second_fm.result.coefficients
    )
    pd.testing.assert_frame_equal(
        first_fm.result.std_errors, second_fm.result.std_errors
    )
    for field in (
        "r_squared",
        "n_obs",
        "mean_coefficients",
        "mean_std_errors",
        "t_stats",
        "p_values",
    ):
        pd.testing.assert_series_equal(
            getattr(first_fm.result, field), getattr(second_fm.result, field)
        )
