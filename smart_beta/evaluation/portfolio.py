"""Evaluation-time portfolio formation orchestration, turnover and cost (P7-E).

This module is the Phase 7 evaluation layer's **evaluation-time formation
orchestrator**: it turns a P7-B ``ForwardReturnAlignment`` (an already
aligned, already §8.1-purged ``(date, stock_id, value, forward_return)``
panel) into the long-short gross return series, the per-rebalance turnover
series, and the single cost-adjusted (net) return series, with full
per-rebalance accounting. It implements plan section 7 item 6.

Authority split (frozen, release-critical)
------------------------------------------
* Portfolio formation **mechanics** (rank-based bucketing, equal-/value-
  weighted within-group aggregation, high-minus-low spread) are owned by
  :mod:`smart_beta.engines.portfolio_sort`. This module **delegates** to it:
  :func:`~smart_beta.engines.portfolio_sort.sort_portfolios` (sorting +
  weighting), :func:`~smart_beta.engines.portfolio_sort.long_short_return`
  (the spread) and :func:`~smart_beta.engines.portfolio_sort.assign_groups`
  (the rank-based group membership used for turnover). Sorting, bucketing
  and group-return aggregation are **never** reimplemented here.
* Evaluation-time orchestration, turnover accounting and transaction-cost
  application are owned here. If a requested formation rule cannot be
  expressed by the existing engine the call **fails closed**: it raises
  :class:`UnsupportedFormationRuleError` and never approximates, invents a
  similar rule, silently falls back or changes weighting semantics.

Turnover convention (frozen, membership-based, hand-calculable)
--------------------------------------------------------------
Turnover is measured at each rebalance ``t`` by the change in **group
membership** versus the previous rebalance ``t-1``, over the names eligible
at either rebalance. Let ``U_t`` be the set of stock ids with a non-``NaN``
group at rebalance ``t`` (after purge, i.e. the names the delegated rank
assignment actually placed in a bucket). For consecutive rebalances::

    U = U_{t-1} UNION U_t
    changed(t) = count of ids in U whose group state changed, where
                 "absent" is a distinct state:
                   * present in both, different group -> changed;
                   * entered (in U_t, not U_{t-1})  -> changed;
                   * exited  (in U_{t-1}, not U_t)  -> changed;
                   * present in both, same group    -> unchanged.
    turnover_t = changed(t) / |U|

with these hand-calculable consequences, all exercised by the tests:

* an unchanged portfolio -> ``0.0``;
* a complete replacement (disjoint membership sets) -> ``1.0``;
* a partial rebalance -> ``|changed| / |union|`` exactly;
* an entry and an exit each count as a change;
* the **first** rebalance has no previous rebalance -> ``0.0``;
* a previous membership that is empty (or a ``|U|`` of zero) is treated as
  a first rebalance -> ``0.0`` (nothing to compare against; no cost).

Turnover is therefore a deterministic function of the membership sequence
alone. It depends only on the ``t``-observable factor value column; the
forward return is **never** consulted when forming membership or weights.

Transaction-cost rule (frozen, single application point, release-critical)
-------------------------------------------------------------------------
This module is the **single** cost-application point in the Phase 7 stack.
The delegated engine produces **gross** returns only and never knows about
cost. The explicit trace is::

    gross_t  = delegated long-short spread at rebalance t   (no cost)
    turnover_t = membership change at rebalance t           (see above)
    cost_rate  = transaction_cost_bps / 10000.0             (bps -> decimal)
    cost_t     = cost_rate * turnover_t
    net_t      = gross_t - cost_t

There is no other conversion and no second deduction. Because the cost is
computed exactly once from the gross series and the turnover series, a
"repeated evaluation" of the same frozen inputs is idempotent: the same
panel evaluated twice yields the same net series and the same path is never
charged twice.

Temporal authority (frozen)
---------------------------
This module **consumes** P7-B's aligned output. It never calls
``smart_beta.data.align.lag_panel``/``shift`` and never re-aligns anything.
Purged rows are already absent from the P7-B panel and are never resurrected.
Formation at ``t`` uses only the ``t``-observable factor value column; the
forward return is used only as the realized return being measured, exactly
as P7-B labelled it.

Benchmark
---------
No benchmark methodology is reconstructed here and no benchmark series is
accepted: benchmark *comparison* (plan section 7 item 5) belongs to
``evaluation/metrics.py`` (P7-D). This module reports the gross, turnover and
net long-short series only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from smart_beta.data.schema import (
    DATE_COL,
    STOCK_COL,
    VALUE_COL,
    PanelSchema,
    SchemaError,
)
from smart_beta.engines import portfolio_sort as portfolio_sort_engine
from smart_beta.evaluation.forward_returns import (
    FORWARD_RETURN_COL,
    ForwardReturnAlignment,
)

__all__ = [
    "PortfolioEvaluation",
    "evaluate_portfolio",
    "PortfolioEvaluationError",
    "UnsupportedFormationRuleError",
    "InvalidCostRateError",
    "InvalidWeightError",
    "FORMATION_RULE_RANK",
    "GROSS_RETURN_SERIES_NAME",
    "TURNOVER_SERIES_NAME",
    "COST_SERIES_NAME",
    "NET_RETURN_SERIES_NAME",
    "ACCOUNTING_COLUMNS",
]

# ---------------------------------------------------------------------------
# Frozen vocabulary
# ---------------------------------------------------------------------------

#: The single formation rule this orchestrator can express: a rank-based
#: single sort into ``n_groups`` equal-ish buckets, consumed as the
#: high-minus-low spread. It maps one-to-one onto
#: :func:`smart_beta.engines.portfolio_sort.sort_portfolios` +
#: :func:`~smart_beta.engines.portfolio_sort.long_short_return`.
FORMATION_RULE_RANK = "rank"

#: Formation rules the delegated engine cannot express through this
#: orchestrator's input contract. Anything outside this tuple fails closed.
_SUPPORTED_FORMATION_RULES = (FORMATION_RULE_RANK,)

#: Internal placeholder weight used to keep the delegated engine's required
#: ``weight_col`` signature satisfied when the caller requested an
#: equal-weighted spread. It is a constant 1.0 for every row, so the engine's
#: value-weighted column is definitionally equal to its equal-weighted
#: column; the orchestrator returns the equal-weighted measure in that case.
_UNIT_WEIGHT_COL = "_p7e_unit_weight"

GROSS_RETURN_SERIES_NAME = "gross_return"
TURNOVER_SERIES_NAME = "turnover"
COST_SERIES_NAME = "cost"
NET_RETURN_SERIES_NAME = "net_return"

#: Per-rebalance accounting table columns, in deterministic order.
ACCOUNTING_COLUMNS = (
    DATE_COL,
    "n_obs",
    "n_members",
    "n_groups",
    GROSS_RETURN_SERIES_NAME,
    TURNOVER_SERIES_NAME,
    COST_SERIES_NAME,
    NET_RETURN_SERIES_NAME,
)


class PortfolioEvaluationError(ValueError):
    """Base class for fail-closed portfolio-evaluation input errors."""


class UnsupportedFormationRuleError(PortfolioEvaluationError):
    """Raised when a requested formation rule is not expressible by the engine.

    The orchestrator fails closed rather than approximating, substituting a
    similar rule, or silently falling back to a different weighting scheme.
    """


class InvalidCostRateError(PortfolioEvaluationError):
    """Raised for a negative, non-finite or non-numeric transaction cost."""


class InvalidWeightError(PortfolioEvaluationError):
    """Raised for a malformed, negative or non-finite value-weight column."""


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioEvaluation:
    """Result of :func:`evaluate_portfolio`.

    ``gross_returns``
        Long-short spread per rebalance, **gross** of transaction cost. Its
        index is the rebalance (formation) date, ascending.
    ``turnover``
        Membership-based turnover per rebalance (see the module docstring).
    ``costs``
        ``cost_rate * turnover`` deducted at each rebalance.
    ``net_returns``
        ``gross_returns - costs``; the single cost-adjusted series.
    ``accounting``
        Per-rebalance table (one row per rebalance, ascending by date) with
        :data:`ACCOUNTING_COLUMNS`: the rebalance ``date``, ``n_obs`` (rows in
        the aligned panel at that rebalance), ``n_members`` (``|U_t|``, names
        with a non-``NaN`` group), the constant ``n_groups``, ``gross_return``,
        ``turnover``, ``cost`` and ``net_return``.
    ``n_groups``
        The number of buckets used.
    ``cost_bps`` / ``cost_rate``
        The transaction cost exactly as applied (``cost_rate = bps / 10000``).
    ``weight_col``
        The value-weight column name, or ``None`` for an equal-weighted run.
    ``measure``
        The delegated engine measure actually used (``ew_return`` or
        ``vw_return``).
    ``formation_rule``
        The frozen formation rule used.
    """

    gross_returns: pd.Series
    turnover: pd.Series
    costs: pd.Series
    net_returns: pd.Series
    accounting: pd.DataFrame
    n_groups: int
    cost_bps: float
    cost_rate: float
    weight_col: str | None
    measure: str
    formation_rule: str

    @property
    def n_rebalances(self) -> int:
        """Number of rebalances (formation dates) evaluated."""
        return int(len(self.accounting))

    @property
    def total_cost(self) -> float:
        """Sum of the per-rebalance cost deductions (``NaN``-safe)."""
        if self.costs.empty:
            return 0.0
        total = self.costs.sum(skipna=True)
        return float(total)


# ---------------------------------------------------------------------------
# Input validation (fail closed)
# ---------------------------------------------------------------------------


def _check_n_groups(n_groups: object) -> int:
    if isinstance(n_groups, bool) or not isinstance(n_groups, (int, np.integer)):
        raise TypeError(
            "n_groups must be an integer, got "
            f"{type(n_groups).__name__}"
        )
    n_groups = int(n_groups)
    if n_groups < 2:
        raise ValueError(
            "n_groups must be >= 2: a high-minus-low long-short spread needs "
            f"at least a low and a high bucket, got {n_groups}"
        )
    return n_groups


def _check_cost_bps(transaction_cost_bps: object) -> float:
    if isinstance(transaction_cost_bps, bool) or not isinstance(
        transaction_cost_bps, (int, float, np.integer, np.floating)
    ):
        raise InvalidCostRateError(
            "transaction_cost_bps must be a real number, got "
            f"{type(transaction_cost_bps).__name__}"
        )
    bps = float(transaction_cost_bps)
    if not np.isfinite(bps):
        raise InvalidCostRateError(
            f"transaction_cost_bps must be finite, got {bps!r}"
        )
    if bps < 0:
        raise InvalidCostRateError(
            f"transaction_cost_bps must be non-negative, got {bps!r}"
        )
    return bps


def _check_formation_rule(formation_rule: object) -> str:
    if formation_rule not in _SUPPORTED_FORMATION_RULES:
        raise UnsupportedFormationRuleError(
            f"formation_rule {formation_rule!r} is not supported by this "
            f"orchestrator; supported rules are "
            f"{list(_SUPPORTED_FORMATION_RULES)}. The requested rule cannot "
            "be expressed through the delegated portfolio_sort engine and no "
            "approximation, fallback or re-weighted substitute is performed."
        )
    return str(formation_rule)


def _extract_panel(alignment: object) -> pd.DataFrame:
    """Return the aligned panel carried by a P7-B ``ForwardReturnAlignment``.

    A raw ``DataFrame`` already carrying the aligned columns is also
    accepted (convenient for hand-calculable tests); anything else fails
    closed with :class:`TypeError`.
    """
    if isinstance(alignment, ForwardReturnAlignment):
        return alignment.panel
    if isinstance(alignment, pd.DataFrame):
        return alignment
    raise TypeError(
        "alignment must be a smart_beta.evaluation.forward_returns."
        "ForwardReturnAlignment or an aligned pandas DataFrame, got "
        f"{type(alignment).__name__}"
    )


def _validate_panel(
    panel: pd.DataFrame,
    *,
    date_col: str,
    stock_col: str,
    value_col: str,
    return_col: str,
    weight_col: str | None,
) -> None:
    schema = PanelSchema(
        key_columns=(date_col, stock_col),
        dtypes={
            date_col: "datetime",
            stock_col: "string",
            value_col: "float",
            return_col: "float",
        },
    )
    schema.validate(panel, name="alignment panel")

    if panel[date_col].isna().any():
        raise SchemaError("alignment panel date column must not contain NaT")

    if weight_col is None:
        return

    if weight_col not in panel.columns:
        raise InvalidWeightError(
            f"weight_col {weight_col!r} is not present in the alignment panel "
            f"(columns present: {list(panel.columns)})"
        )
    weights = panel[weight_col]
    if pd.api.types.is_bool_dtype(weights) or not pd.api.types.is_numeric_dtype(
        weights
    ):
        raise InvalidWeightError(
            f"value-weight column {weight_col!r} must be numeric, got dtype "
            f"{weights.dtype}"
        )
    values = weights.to_numpy(dtype="float64")
    if not np.all(np.isfinite(values)):
        raise InvalidWeightError(
            f"value-weight column {weight_col!r} must be finite (no NaN/inf)"
        )
    if np.any(values < 0):
        raise InvalidWeightError(
            f"value-weight column {weight_col!r} must be non-negative"
        )


# ---------------------------------------------------------------------------
# Membership + turnover
# ---------------------------------------------------------------------------


def _memberships_by_date(
    panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    *,
    date_col: str,
    stock_col: str,
    value_col: str,
    n_groups: int,
) -> tuple[list[dict[str, int]], list[int]]:
    """Group membership per rebalance, delegated to the engine's assignment.

    Returns ``(memberships, n_obs)`` where ``memberships[i]`` maps stock id
    to group for the rebalance ``dates[i]`` (names with a ``NaN`` group are
    omitted) and ``n_obs[i]`` is the number of aligned rows at that date.
    """
    memberships: list[dict[str, int]] = []
    n_obs: list[int] = []
    for date in dates:
        sub = panel.loc[panel[date_col] == date]
        n_obs.append(int(len(sub)))
        groups = portfolio_sort_engine.assign_groups(sub[value_col], n_groups)
        member: dict[str, int] = {}
        for stock, group in zip(
            sub[stock_col].to_numpy(), groups.to_numpy(dtype="float64")
        ):
            if not pd.isna(group):
                member[str(stock)] = int(group)
        memberships.append(member)
    return memberships, n_obs


def _turnover_series(
    memberships: list[dict[str, int]],
    dates: pd.DatetimeIndex,
) -> pd.Series:
    """Membership-based turnover over consecutive rebalances (frozen rule)."""
    values: list[float] = []
    previous: dict[str, int] | None = None
    for member in memberships:
        if previous is None or not previous:
            # First rebalance, or a previous membership that was empty: there
            # is nothing to compare against, so turnover is exactly 0.0.
            values.append(0.0)
        else:
            union = set(previous) | set(member)
            if not union:
                values.append(0.0)
            else:
                changed = 0
                for stock in union:
                    if stock not in previous or stock not in member:
                        changed += 1  # entered or exited
                    elif previous[stock] != member[stock]:
                        changed += 1  # moved group
                values.append(changed / len(union))
        previous = member
    return pd.Series(values, index=dates, name=TURNOVER_SERIES_NAME, dtype="float64")


def _empty_result(
    *,
    n_groups: int,
    cost_bps: float,
    cost_rate: float,
    weight_col: str | None,
    measure: str,
    formation_rule: str,
) -> PortfolioEvaluation:
    empty_index = pd.DatetimeIndex([], name=DATE_COL)
    gross = pd.Series([], index=empty_index, name=GROSS_RETURN_SERIES_NAME, dtype="float64")
    turnover = pd.Series([], index=empty_index, name=TURNOVER_SERIES_NAME, dtype="float64")
    costs = pd.Series([], index=empty_index, name=COST_SERIES_NAME, dtype="float64")
    net = pd.Series([], index=empty_index, name=NET_RETURN_SERIES_NAME, dtype="float64")
    accounting = pd.DataFrame({column: pd.Series([], dtype="float64") for column in ACCOUNTING_COLUMNS})
    accounting[DATE_COL] = pd.Series([], dtype="datetime64[ns]")
    accounting["n_obs"] = pd.Series([], dtype="int64")
    accounting["n_members"] = pd.Series([], dtype="int64")
    accounting["n_groups"] = pd.Series([], dtype="int64")
    return PortfolioEvaluation(
        gross_returns=gross,
        turnover=turnover,
        costs=costs,
        net_returns=net,
        accounting=accounting.loc[:, list(ACCOUNTING_COLUMNS)],
        n_groups=n_groups,
        cost_bps=cost_bps,
        cost_rate=cost_rate,
        weight_col=weight_col,
        measure=measure,
        formation_rule=formation_rule,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_portfolio(
    alignment: ForwardReturnAlignment | pd.DataFrame,
    n_groups: int,
    *,
    transaction_cost_bps: float,
    weight_col: str | None = None,
    formation_rule: str = FORMATION_RULE_RANK,
    date_col: str = DATE_COL,
    stock_col: str = STOCK_COL,
    value_col: str = VALUE_COL,
    return_col: str = FORWARD_RETURN_COL,
) -> PortfolioEvaluation:
    """Form evaluation-time portfolios, measure turnover and apply cost once.

    Parameters
    ----------
    alignment:
        The P7-B ``ForwardReturnAlignment`` whose ``panel`` carries the
        aligned, already §8.1-purged columns ``(date, stock_id, value,
        forward_return)``. The purged rows are already absent and are never
        resurrected. A raw aligned ``DataFrame`` is accepted for convenience.
    n_groups:
        Number of rank buckets (``int >= 2``); the spread is
        ``group n_groups - group 1``.
    transaction_cost_bps:
        One-way transaction cost in basis points, ``>= 0`` and finite.
        Applied exactly once as ``cost_rate = bps / 10000`` times turnover.
    weight_col:
        Optional value-weight column (e.g. market cap). When ``None`` the
        equal-weighted long-short spread is returned; when supplied, the
        value-weighted spread is returned. Weights must be numeric, finite
        and non-negative.
    formation_rule:
        The formation rule. Only :data:`FORMATION_RULE_RANK` (a rank-based
        single sort delegated to ``portfolio_sort.sort_portfolios``) is
        supported; anything else raises
        :class:`UnsupportedFormationRuleError` (fail closed).
    date_col, stock_col, value_col, return_col:
        Column names in the aligned panel, defaulting to the canonical P7-B
        names.

    Returns
    -------
    PortfolioEvaluation
        Gross, turnover, cost and net series plus per-rebalance accounting.

    Notes
    -----
    The forward return column is consumed only by the delegated engine's
    within-group aggregation, i.e. only as the realized return being
    measured. Membership and weights are functions of the ``t``-observable
    ``value_col`` alone, so no future return can influence formation.
    """
    n_groups = _check_n_groups(n_groups)
    cost_bps = _check_cost_bps(transaction_cost_bps)
    cost_rate = cost_bps / 10000.0
    formation_rule = _check_formation_rule(formation_rule)

    panel = _extract_panel(alignment)
    _validate_panel(
        panel,
        date_col=date_col,
        stock_col=stock_col,
        value_col=value_col,
        return_col=return_col,
        weight_col=weight_col,
    )

    measure = (
        portfolio_sort_engine.VW_RETURN_COL
        if weight_col is not None
        else portfolio_sort_engine.EW_RETURN_COL
    )

    columns = [date_col, stock_col, value_col, return_col]
    if weight_col is not None:
        columns.append(weight_col)
    canonical = (
        panel.loc[:, columns]
        .copy()
        .sort_values([date_col, stock_col], kind="mergesort")
        .reset_index(drop=True)
    )

    if canonical.empty:
        return _empty_result(
            n_groups=n_groups,
            cost_bps=cost_bps,
            cost_rate=cost_rate,
            weight_col=weight_col,
            measure=measure,
            formation_rule=formation_rule,
        )

    engine_weight_col = weight_col if weight_col is not None else _UNIT_WEIGHT_COL
    engine_input = canonical.copy()
    if weight_col is None:
        engine_input[_UNIT_WEIGHT_COL] = 1.0

    dates = pd.DatetimeIndex(
        np.sort(canonical[date_col].unique()), name=date_col
    )

    # --- 1. membership (delegated rank assignment) + turnover ---------------
    memberships, n_obs = _memberships_by_date(
        canonical,
        dates,
        date_col=date_col,
        stock_col=stock_col,
        value_col=value_col,
        n_groups=n_groups,
    )
    turnover = _turnover_series(memberships, dates)

    # --- 2. gross long-short spread (delegated sorting/weighting) ----------
    sorted_returns = portfolio_sort_engine.sort_portfolios(
        engine_input,
        char_col=value_col,
        ret_col=return_col,
        weight_col=engine_weight_col,
        date_col=date_col,
        n_groups=n_groups,
    )
    gross = portfolio_sort_engine.long_short_return(
        sorted_returns,
        low_group=1,
        high_group=n_groups,
        measure=measure,
        date_col=date_col,
    )
    gross = gross.sort_index().astype("float64")
    gross.name = GROSS_RETURN_SERIES_NAME

    # Rebalance dates are the delegated spread's own index, deterministically
    # ordered; turnover is aligned to exactly the same calendar.
    turnover = turnover.reindex(gross.index)
    turnover.name = TURNOVER_SERIES_NAME

    # --- 3. the single cost application ------------------------------------
    costs = (turnover * cost_rate).astype("float64")
    costs.name = COST_SERIES_NAME
    net = (gross - costs).astype("float64")
    net.name = NET_RETURN_SERIES_NAME

    # --- 4. per-rebalance accounting ---------------------------------------
    membership_size = pd.Series(
        [len(member) for member in memberships],
        index=dates,
        dtype="int64",
    )
    accounting = pd.DataFrame(
        {
            "n_obs": pd.Series(n_obs, index=dates, dtype="int64"),
            "n_members": membership_size.reindex(gross.index).astype("int64"),
            "n_groups": n_groups,
            GROSS_RETURN_SERIES_NAME: gross,
            TURNOVER_SERIES_NAME: turnover,
            COST_SERIES_NAME: costs,
            NET_RETURN_SERIES_NAME: net,
        }
    )
    accounting.index.name = date_col
    accounting = accounting.reset_index().loc[:, list(ACCOUNTING_COLUMNS)]

    return PortfolioEvaluation(
        gross_returns=gross,
        turnover=turnover,
        costs=costs,
        net_returns=net,
        accounting=accounting,
        n_groups=n_groups,
        cost_bps=cost_bps,
        cost_rate=cost_rate,
        weight_col=weight_col,
        measure=measure,
        formation_rule=formation_rule,
    )
