"""P-Q v2.5 — B-TD scenario reconstruction (PQ25-T).

New-modules-only: this module imports the frozen D7b pipeline
(:mod:`smart_beta.dataset_v1.delisting_bridge`) and the v2.5 authority
(:mod:`smart_beta.dataset_v1.pqv25.authority`) read-only and never modifies a
bound file.

It provides the B-TD estimand of the frozen v2.5 design
(``docs/discovery_dataset_v1_2_pq_v2_5_terminal_td_design_draft.md`` §1) and
erratum E1 (``docs/discovery_dataset_v1_2_pq_v2_5_erratum_e1.md``):

* :func:`reconstruct_td` replicates ``pqv2_evaluation._reconstruct_b`` step for
  step (same frozen D7b helpers, import-only), then evaluates a third terminal
  scenario **TD** beside the frozen T0/TS;
* :func:`scenario_panels_td` re-expresses the frozen D7b ``_scenario_panels``
  loop, producing the T0, TS **and** TD value columns in the identical row
  order;
* the re-expression is self-proving: every call compares the re-expressed T0/TS
  panels and records against the frozen ``_d7b._scenario_panels`` output
  (``pandas.testing.assert_frame_equal(..., check_exact=True)``) and raises
  :class:`TDReconstructionError` on any difference.  Four TD invariants are
  checked fail-closed as well.

The TD arithmetic is exactly the frozen expression
``(1.0 + r) * (1.0 + tau) - 1.0`` with ``r`` the D7b ``r_obs`` input
(``0.0`` for ``ADMISSIBLE_TERMINAL_EMPTY``) and ``tau`` ``d`` on the economic
long leg, ``-d`` on the economic short leg and ``0.0`` otherwise; plain
``ADMISSIBLE`` members keep the observed return itself.  This is the
comment-to-code fix erratum E1.1 requires for the ``d = -1`` identity barrier.

Nothing here reads ``data_store``, a production artifact, the reserve, a
holdout, the Knowledge-PIT or the network.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from smart_beta.dataset_v1 import delisting_bridge as _d7b
from smart_beta.dataset_v1.pqv25.authority import TD_D

__all__ = [
    "TDReconstruction",
    "TDScenario",
    "TDReconstructionError",
    "reconstruct_td",
    "scenario_panels_td",
]


class TDReconstructionError(Exception):
    """The TD re-expression failed a fail-closed equivalence or invariant check."""


@dataclass
class TDScenario:
    """One evaluated scenario, shaped like the frozen ``_ScenarioB``."""

    portfolio: Any
    panel: pd.DataFrame
    gross: pd.Series
    net: pd.Series
    value: float | None
    t_stat: float | None
    n_obs: int


@dataclass
class TDReconstruction:
    """The B-TD reconstruction: the three scenarios plus the shared inputs."""

    t0: TDScenario
    ts: TDScenario
    td: TDScenario
    records: tuple[_d7b.AccountingRecord, ...]
    groups: Mapping[tuple[date, str], float]
    loaded: Any


# ---------------------------------------------------------------------------
# Re-expression of the frozen D7b scenario-panel loop
# ---------------------------------------------------------------------------


def scenario_panels_td(
    *,
    winsorized: pd.DataFrame,
    groups: Mapping[tuple[date, str], float],
    r_obs: Mapping[tuple[date, str], float],
    observed_returns: Mapping[tuple[date, str], float],
    declaration: Any,
    loaded: Any,
    sign: int,
    d: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, tuple[_d7b.AccountingRecord, ...]]:
    """Re-express ``delisting_bridge._scenario_panels`` with a TD column.

    The loop is the frozen one, verbatim, with a third scenario value added:
    for ``ADMISSIBLE_TERMINAL`` / ``ADMISSIBLE_TERMINAL_EMPTY`` members

        ``v^TD = (1.0 + r) * (1.0 + tau_td) - 1.0``

    where ``tau_td`` is ``d`` on the economic long leg, ``-d`` on the economic
    short leg and ``0.0`` otherwise; plain ``ADMISSIBLE`` members take the
    observed return; every other member is NaN.  It returns
    ``(panel_t0, panel_ts, panel_td, records)``.
    """

    n_groups = declaration.n_groups
    t0_values: list[float] = []
    ts_values: list[float] = []
    td_values: list[float] = []
    records: list[_d7b.AccountingRecord] = []
    for row in winsorized.itertuples(index=False):
        key = (_d7b._as_date(row.date), str(row.stock_id))
        label = loaded.label_by_key.get(key)
        if label is None:
            raise _d7b.D7bConsistencyError(f"missing label for {key}")
        state = _d7b._admissibility(label)
        group_value = groups.get(key)
        group = (
            None
            if group_value is None or math.isnan(group_value)
            else int(group_value)
        )
        affected = _d7b._as_bool(label.get("terminal_affected"))
        label_state = str(label.get("label_state"))
        if group is None:
            leg = _d7b.ECONOMIC_NONE
        elif sign == 1:
            if group == n_groups:
                leg = _d7b.ECONOMIC_LONG
            elif group == 1:
                leg = _d7b.ECONOMIC_SHORT
            else:
                leg = _d7b.ECONOMIC_MIDDLE
        else:
            if group == 1:
                leg = _d7b.ECONOMIC_LONG
            elif group == n_groups:
                leg = _d7b.ECONOMIC_SHORT
            else:
                leg = _d7b.ECONOMIC_MIDDLE
        expected_admissible = group is not None and state in _d7b.ADMISSIBLE_STATES
        record_state = state if group is not None else _d7b.FACTOR_VALUE_MISSING
        t0_value = float("nan")
        ts_value = float("nan")
        td_value = float("nan")
        if expected_admissible:
            if state == _d7b.ADMISSIBLE:
                base = observed_returns.get(key)
                if base is None:
                    raise _d7b.D7bConsistencyError(
                        f"{key} is admissible but has no P7-B return"
                    )
                t0_value = base
                ts_value = base
                td_value = base
            else:
                r = 0.0 if state == _d7b.ADMISSIBLE_TERMINAL_EMPTY else r_obs.get(key)
                if r is None:
                    raise _d7b.D7bConsistencyError(
                        f"{key} terminal admissible but has no R_obs"
                    )
                t0_value = (1.0 + r) * (1.0 + 0.0) - 1.0
                tau = 0.0
                if leg == _d7b.ECONOMIC_LONG:
                    tau = -1.0
                elif leg == _d7b.ECONOMIC_SHORT:
                    tau = 1.0
                ts_value = (1.0 + r) * (1.0 + tau) - 1.0
                tau_td = 0.0
                if leg == _d7b.ECONOMIC_LONG:
                    tau_td = d
                elif leg == _d7b.ECONOMIC_SHORT:
                    tau_td = -d
                td_value = (1.0 + r) * (1.0 + tau_td) - 1.0
            for value in (t0_value, ts_value, td_value):
                if not math.isfinite(value) or value < -1.0:
                    raise _d7b.D7bConsistencyError(
                        f"{key} produced an inadmissible scenario value {value!r}"
                    )
        for scenario in (_d7b.SCENARIO_T0, _d7b.SCENARIO_TS):
            records.append(
                _d7b.AccountingRecord(
                    formation_date=key[0],
                    stock_id=key[1],
                    horizon=declaration.horizon,
                    scenario=scenario,
                    group=group,
                    economic_leg=leg,
                    admissibility=record_state,
                    terminal_affected=affected,
                    label_state=label_state,
                    expected_admissible=expected_admissible,
                )
            )
        t0_values.append(t0_value)
        ts_values.append(ts_value)
        td_values.append(td_value)

    base = winsorized.copy().reset_index(drop=True)
    panel_t0 = base.copy()
    panel_t0[_d7b.fr.FORWARD_RETURN_COL] = np.asarray(t0_values, dtype="float64")
    panel_ts = base.copy()
    panel_ts[_d7b.fr.FORWARD_RETURN_COL] = np.asarray(ts_values, dtype="float64")
    panel_td = base.copy()
    panel_td[_d7b.fr.FORWARD_RETURN_COL] = np.asarray(td_values, dtype="float64")
    panel_t0 = panel_t0.sort_values(
        [_d7b.DATE_COL, _d7b.STOCK_COL], kind="mergesort"
    ).reset_index(drop=True)
    panel_ts = panel_ts.sort_values(
        [_d7b.DATE_COL, _d7b.STOCK_COL], kind="mergesort"
    ).reset_index(drop=True)
    panel_td = panel_td.sort_values(
        [_d7b.DATE_COL, _d7b.STOCK_COL], kind="mergesort"
    ).reset_index(drop=True)
    records.sort(
        key=lambda item: (
            item.scenario,
            item.formation_date,
            item.stock_id,
        )
    )
    return panel_t0, panel_ts, panel_td, tuple(records)


# ---------------------------------------------------------------------------
# Fail-closed checks
# ---------------------------------------------------------------------------


def _bits(value: Any) -> bytes:
    return struct.pack(">d", float(value))


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _scenario_from_panel(panel: pd.DataFrame, declaration: Any) -> TDScenario:
    portfolio, value, t_stat, n_obs = _d7b._evaluate_scenario(panel, declaration)
    return TDScenario(
        portfolio=portfolio,
        panel=panel,
        gross=portfolio.gross_returns,
        net=portfolio.net_returns,
        value=_finite_or_none(value),
        t_stat=_finite_or_none(t_stat),
        n_obs=int(n_obs),
    )


def _check_equivalence(
    *,
    panel_t0: pd.DataFrame,
    panel_ts: pd.DataFrame,
    frozen_t0: pd.DataFrame,
    frozen_ts: pd.DataFrame,
    records: tuple[_d7b.AccountingRecord, ...],
    frozen_records: tuple[_d7b.AccountingRecord, ...],
) -> None:
    """The self-proof: the re-expressed T0/TS must equal the frozen output."""

    try:
        pd.testing.assert_frame_equal(panel_t0, frozen_t0, check_exact=True)
        pd.testing.assert_frame_equal(panel_ts, frozen_ts, check_exact=True)
    except AssertionError as exc:  # pragma: no cover - defensive re-wrap
        raise TDReconstructionError(
            f"re-expressed T0/TS differ from the frozen _scenario_panels: {exc}"
        ) from exc
    if records != frozen_records:
        raise TDReconstructionError(
            "re-expressed records differ from the frozen _scenario_panels"
        )


def _check_td_invariants(
    *,
    panel_t0: pd.DataFrame,
    panel_td: pd.DataFrame,
    groups: Mapping[tuple[date, str], float],
    declaration: Any,
    records: tuple[_d7b.AccountingRecord, ...],
) -> None:
    """Four fail-closed TD invariants (PQ25-T spec §5)."""

    date_col = _d7b.DATE_COL
    stock_col = _d7b.STOCK_COL
    return_col = _d7b.fr.FORWARD_RETURN_COL

    # 1. The same (date, stock) rows.
    left = panel_td[[date_col, stock_col]].reset_index(drop=True)
    right = panel_t0[[date_col, stock_col]].reset_index(drop=True)
    if not left.equals(right):
        raise TDReconstructionError("TD row grid differs from T0")

    # 2. TD's finite mask equals T0's.
    t0_finite = panel_t0[return_col].notna().to_numpy()
    td_finite = panel_td[return_col].notna().to_numpy()
    if not np.array_equal(t0_finite, td_finite):
        raise TDReconstructionError("TD finite mask differs from T0")

    # 3. The same groups.
    replay = _d7b._frozen_groups(panel_td, declaration.n_groups)
    if set(replay) != set(groups):
        raise TDReconstructionError("TD grouping keys differ from T0")
    for key, group in replay.items():
        expected = groups[key]
        same = group == expected or (
            math.isnan(group) and math.isnan(expected)
        )
        if not same:
            raise TDReconstructionError(
                f"TD grouping differs at {key}: {group} != {expected}"
            )

    # 4. Members outside the two terminal states have TD == T0 bitwise.
    terminal_keys = {
        (record.formation_date, record.stock_id)
        for record in records
        if record.scenario == _d7b.SCENARIO_T0
        and record.admissibility
        in {_d7b.ADMISSIBLE_TERMINAL, _d7b.ADMISSIBLE_TERMINAL_EMPTY}
    }
    t0_lookup = {
        (_d7b._as_date(row.date), str(row.stock_id)): getattr(row, return_col)
        for row in panel_t0.itertuples(index=False)
    }
    for row in panel_td.itertuples(index=False):
        key = (_d7b._as_date(row.date), str(row.stock_id))
        if key in terminal_keys:
            continue
        if _bits(getattr(row, return_col)) != _bits(t0_lookup[key]):
            raise TDReconstructionError(
                f"non-terminal member {key} has TD != T0 bitwise"
            )


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------


def reconstruct_td(
    root: Path | str,
    declaration: Any,
    factor_spec: Any,
    factor_panel: pd.DataFrame,
    partition: Any,
    bindings: Any,
    *,
    d: float = TD_D,
) -> TDReconstruction:
    """Re-run the unmodified D7b pipeline and evaluate the TD scenario.

    The steps replicate ``pqv2_evaluation._reconstruct_b`` step for step, using
    the same frozen D7b helpers (import-only).  The re-expressed T0/TS panels
    and records are checked bit-exactly against the frozen
    ``_d7b._scenario_panels`` output, and the TD panel passes the four
    fail-closed invariants, before any scenario is returned.
    """

    base = declaration.base
    resolver = _d7b._Resolver(root, bindings)
    fold = _d7b._resolve_fold(base, partition)
    loaded = _d7b._load_d7a(resolver, bindings, base, partition, fold)
    panel = _d7b._validate_factor_panel(
        factor_panel,
        declaration=base,
        formations=loaded.formations,
        universe_by_formation=loaded.universe_by_formation,
    )
    base_panel = _d7b._base_panel(
        panel,
        formations=loaded.formations,
        universe_by_formation=loaded.universe_by_formation,
    )
    winsorized = _d7b._winsorize(base_panel, base.winsorization)
    groups = _d7b._frozen_groups(winsorized, base.n_groups)
    observed = _d7b._observed_panel(loaded.daily)
    observed_returns = _d7b._admissible_returns(
        factor_panel=panel,
        observed=observed,
        fold=fold,
        declaration=base,
        loaded=loaded,
    )
    r_obs = _d7b._terminal_obs_returns(
        factor_panel=panel,
        observed=observed,
        fold=fold,
        declaration=base,
        loaded=loaded,
    )

    frozen_t0, frozen_ts, frozen_records = _d7b._scenario_panels(
        winsorized=winsorized,
        groups=groups,
        r_obs=r_obs,
        observed_returns=observed_returns,
        declaration=base,
        loaded=loaded,
        sign=int(factor_spec.sign),
    )
    panel_t0, panel_ts, panel_td, records = scenario_panels_td(
        winsorized=winsorized,
        groups=groups,
        r_obs=r_obs,
        observed_returns=observed_returns,
        declaration=base,
        loaded=loaded,
        sign=int(factor_spec.sign),
        d=d,
    )

    _check_equivalence(
        panel_t0=panel_t0,
        panel_ts=panel_ts,
        frozen_t0=frozen_t0,
        frozen_ts=frozen_ts,
        records=records,
        frozen_records=frozen_records,
    )
    _check_td_invariants(
        panel_t0=frozen_t0,
        panel_td=panel_td,
        groups=groups,
        declaration=base,
        records=frozen_records,
    )

    return TDReconstruction(
        t0=_scenario_from_panel(frozen_t0, base),
        ts=_scenario_from_panel(frozen_ts, base),
        td=_scenario_from_panel(panel_td, base),
        records=frozen_records,
        groups=groups,
        loaded=loaded,
    )
