"""O1 — structural residual cross-sectional influence diagnostic.

Authority: ``worker_tasks/campaign1/task-c1-o1-structural-residual.md``
(Campaign-1 O1 repair), itself bound to the P-Q rules
``docs/discovery_dataset_v1_2_pq_quarantine_rules.md`` Q3.3 and Q12.

The legacy ``factor_input_states(...).attrs["residual_cs_cells"]`` counts
every finite cell whose value differs between the factor values ``V`` and the
quarantine-masked shadow ``V'``.  That includes pure floating-point drift from
pandas' sliding-window accumulators in time-series rolling ops (the O1 defect).

This module computes the **corrected, structural** diagnostic required by
Q3.3/Q12: a cell ``(d, K)`` is *residually cross-sectionally influenced* iff

1. its Q3.3 quarantine state is ``CLEAN`` (0), and its value is finite;
2. its computation structurally depends, through the Q3.3 rules (lag shift,
   rolling window, arithmetic union), on the output cell ``(d', K)`` of some
   cross-sectional node (``rank`` / ``winsorize`` / ``standardize``);
3. the operand **row** ``d'`` of that cross-sectional node contains at least one
   cell in state 1 or 2.

No value comparison, tolerance or floating-point test is used anywhere.  The
implementation is a second structural taint pass over the same closed node set
as :mod:`smart_beta.dataset_v1.quarantine`, whose oracle/state helpers it reuses
read-only without modifying them.

Every use here is offline and synthetic: provider/network calls = 0, reserve
reads = 0, real factor evaluation = 0.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from smart_beta.dataset_v1 import quarantine as _pqa
from smart_beta.spec.engine import TrustedInput
from smart_beta.spec.expression import (
    Add,
    CrossSectional,
    Div,
    Expr,
    Field,
    Lag,
    Literal,
    Mul,
    Rolling,
    Sub,
)
from smart_beta.spec.factor_spec import FactorSpec

__all__ = [
    "structural_residual_cs",
]

#: Q3.3 states that make a cell "quarantined" for the row-seed test.
_QUARANTINED_STATES = (_pqa.STATE_QUARANTINED, _pqa.STATE_PROPAGATED)

_COLUMNS = ("date", "residual_cs_cells")


def _zeros(template: pd.DataFrame) -> np.ndarray:
    return np.zeros(template.shape, dtype=bool)


def _structural_taint(
    node: Expr,
    spec: FactorSpec,
    overlay: _pqa.Overlay,
    inputs: Mapping[str, TrustedInput],
    template: pd.DataFrame,
) -> np.ndarray:
    """Second structural taint pass over the closed Q3.3 node set.

    Returns a boolean grid of the same shape as ``template``: ``True`` marks an
    output cell of ``node`` that depends on a seeded cross-sectional output.
    The propagation rules mirror the Q3.3 oracle exactly (lag shift, rolling
    window, arithmetic union); cross-sectional nodes are the seeds.
    """
    if isinstance(node, (Literal, Field)):
        return _zeros(template)
    if isinstance(node, (Add, Sub, Mul, Div)):
        left = _structural_taint(node.left, spec, overlay, inputs, template)
        right = _structural_taint(node.right, spec, overlay, inputs, template)
        return left | right
    if isinstance(node, Lag):
        child = _structural_taint(node.operand, spec, overlay, inputs, template)
        if node.periods == 0:
            return child
        shifted = np.zeros_like(child)
        if node.periods < child.shape[0]:
            shifted[node.periods :] = child[: -node.periods]
        return shifted
    if isinstance(node, Rolling):
        child = _structural_taint(node.operand, spec, overlay, inputs, template)
        out = np.zeros_like(child)
        window = int(node.window)
        for i in range(child.shape[0]):
            start = i - window + 1
            if start < 0:
                continue
            out[i] = child[start : i + 1].any(axis=0)
        return out
    if isinstance(node, CrossSectional):
        # Seed: the node's operand row contains at least one quarantined cell.
        operand_state = _pqa._oracle_node(  # read-only reuse of the Q3.3 oracle
            node.operand, spec, overlay, inputs, template
        )
        row_seed = np.isin(operand_state, _QUARANTINED_STATES).any(axis=1)
        # The cross-sectional operation reads the whole operand row, so any
        # downstream taint in that row is also carried by every output cell.
        child = _structural_taint(node.operand, spec, overlay, inputs, template)
        row_taint = row_seed | child.any(axis=1)
        return np.repeat(row_taint[:, None], template.shape[1], axis=1)
    raise _pqa.UnsupportedQuarantineNode(
        f"node type {type(node).__name__} is outside the Q3.3 closed set"
    )


def _residual_frame(
    expression: Expr,
    values: pd.DataFrame,
    spec: FactorSpec,
    overlay: _pqa.Overlay,
    inputs: Mapping[str, TrustedInput],
) -> pd.DataFrame:
    """Structural count on an explicit value grid for ``expression``.

    ``values`` supplies the cell domain (dates x security keys) and the
    finiteness test; the Q3.3 state grid and the structural taint grid are both
    derived from ``expression`` through the read-only quarantine helpers.  The
    public entry point passes the factor's evaluated grid here.
    """
    if values.shape == (0, 0):
        empty = pd.DataFrame(
            {
                "date": pd.Series([], dtype="object"),
                "residual_cs_cells": pd.Series([], dtype="int64"),
            },
            columns=list(_COLUMNS),
        )
        empty.attrs["total_residual_cs_cells"] = 0
        return empty

    template = pd.DataFrame(np.nan, index=values.index, columns=values.columns)
    final_state = _pqa._oracle_node(expression, spec, overlay, inputs, template)
    taint = _structural_taint(expression, spec, overlay, inputs, template)

    finite = np.isfinite(values.to_numpy(dtype="float64"))
    clean = final_state == _pqa.STATE_CLEAN
    eligible = clean & finite
    counted = eligible & taint
    per_row = counted.sum(axis=1).astype("int64")

    frame = pd.DataFrame(
        {
            "date": [pd.Timestamp(d).date() for d in values.index],
            "residual_cs_cells": per_row,
        },
        columns=list(_COLUMNS),
    )
    frame = frame.sort_values("date", kind="mergesort").reset_index(drop=True)
    frame["residual_cs_cells"] = frame["residual_cs_cells"].astype("int64")
    frame.attrs["total_residual_cs_cells"] = int(per_row.sum())
    return frame


def structural_residual_cs(
    factor_spec: FactorSpec,
    *,
    overlay: _pqa.Overlay,
    inputs: Mapping[str, TrustedInput],
) -> pd.DataFrame:
    """The structural residual cross-sectional influence diagnostic (O1).

    Returns a sorted ``pandas.DataFrame`` with integer columns
    ``(date, residual_cs_cells)``: one row per date in the factor grid, holding
    the number of *CLEAN, finite* factor cells on that date that are
    structurally influenced through a cross-sectional operation whose operand
    row carries a quarantined cell.

    The frame's ``attrs["total_residual_cs_cells"]`` holds the integer total
    (the sum over dates).  Expressions with no cross-sectional node return all
    zeros by definition.  No value comparison is performed.
    """
    values = _pqa._evaluate_wide(factor_spec, inputs)
    return _residual_frame(
        factor_spec.expression, values, factor_spec, overlay, inputs
    )
