"""Campaign-4 M1 -- treatment / control renderers (task C4M1-1).

Authority: ``worker_tasks/campaign4_m1/task-c4m1-1-contract-core.md``
section 5, ``docs/campaign4_m1a_contract_calibration_draft.md`` section 2 and
the C4M1-1 erratum E1 (R1 firewall binding, R2 per-proposal control metrics).

Two renderers live here:

* :func:`render_c4_feedback` -- the **treatment** feedback block: one
  canonical compact JSON line per proposal, in proposal order.  Each line
  carries the A2 declaration, the expression, the training-window rank
  statistic and rank, the L-T0 / L-TD EWC t, the A6 restatement and the A4
  nearest neighbour.
* :func:`render_control_prompt` -- the **control** prompt: the frozen C3 v2
  template (hash-equal to :data:`PROMPT_TEMPLATE_V2_HASH`) rendered through
  ``llm_generator.render_prompt`` with a caller-supplied
  :class:`~smart_beta.research.history.ResearchFeedback`.  The caller builds
  that feedback the C3 way (C4M1-2's executor history), with the per-cell
  metrics produced by :func:`control_metrics`.

The frozen C4 prompt file ``smart_beta/research/prompts/campaign4_v1.txt`` is a
byte-identical copy of ``worker_tasks/campaign4_m1/campaign4_v1_prompt.txt``;
its SHA-256 is asserted at import against the frozen hash.  The only
placeholder is ``<<<C4_FEEDBACK>>>``.

Firewall (erratum E1 R1)
------------------------

The treatment renderer no longer trusts a single per-record date.  Each
statistic it renders is a :class:`~smart_beta.campaign4.ranking.RankStatistic`
carrying the ``window_cut`` it was computed under and the latest formation date
it actually used (``last_period``).  :func:`render_c4_feedback` raises
:class:`C4FirewallError` unless every statistic's ``window_cut`` equals the
renderer's cut and every ``last_period`` is ``None`` or strictly before the
cut.  The A4/A6 values carry the gate's ``window_cut``, which must also equal
the renderer's cut.

Nothing here reads a real dataset, opens a network path or writes the
Knowledge-PIT.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from smart_beta.campaign4.contract import C4ContractError, C4FirewallError
from smart_beta.campaign4.ranking import RankStatistic
from smart_beta.evaluation.metrics import long_short_mean_tstat
from smart_beta.evaluation.spec import MetricValue
from smart_beta.research import llm_generator, llm_generator_deepseek
from smart_beta.research.history import (
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
)

__all__ = [
    "FROZEN_PROMPT_TEMPLATE_C4_SHA256",
    "PROMPT_TEMPLATE_C4_PATH",
    "PROMPT_TEMPLATE_C4_HASH",
    "C4_FEEDBACK_TOKEN",
    "C4_SYSTEM_PROMPT",
    "CONTROL_PROMPT_TEMPLATE_HASH",
    "C4_STATUSES",
    "FeedbackError",
    "FeedbackRecord",
    "prompt_template_c4_hash",
    "load_prompt_template_c4",
    "render_c4_prompt",
    "render_c4_feedback",
    "control_metrics",
    "render_control_prompt",
]


class FeedbackError(C4ContractError):
    """A renderer was given a malformed record (fail closed)."""


# ---------------------------------------------------------------------------
# frozen prompt literals
# ---------------------------------------------------------------------------

#: The frozen Campaign-4 prompt template path (owned by C4M1-1).
PROMPT_TEMPLATE_C4_PATH = (
    Path(__file__).resolve().parents[1]
    / "research"
    / "prompts"
    / "campaign4_v1.txt"
)

#: The frozen prompt-template SHA-256 (calibration draft Q8).
FROZEN_PROMPT_TEMPLATE_C4_SHA256 = (
    "2d616f1689a487d1e5f039f2cc1ab310367b0c47901cf9d4f371d0bffa92d05e"
)

#: The treatment template's single placeholder.
C4_FEEDBACK_TOKEN = "<<<C4_FEEDBACK>>>"

#: The Campaign-4 system prompt: the C3 text with the campaign label changed.
#: It is a new constant; the C3 ``SYSTEM_PROMPT`` is untouched.
C4_SYSTEM_PROMPT = llm_generator.SYSTEM_PROMPT.replace("Campaign-3", "Campaign-4")

#: The control template hash (the frozen C3 v2 template hash, reused).
CONTROL_PROMPT_TEMPLATE_HASH = llm_generator_deepseek.PROMPT_TEMPLATE_V2_HASH

#: The frozen C4 feedback status vocabulary (task spec section 5).
C4_STATUSES: frozenset[str] = frozenset(
    {
        "EVALUATED",
        "NOT_ASSESSED",
        "NOT_CERTIFIED",
        "NEAR_DUPLICATE",
        "FAMILY_CAP",
        "A2_DECLARATION_INVALID",
        "SCHEMA_REJECTED",
    }
)


def prompt_template_c4_hash() -> str:
    """The SHA-256 of the frozen Campaign-4 prompt bytes."""
    return hashlib.sha256(PROMPT_TEMPLATE_C4_PATH.read_bytes()).hexdigest()


#: Exposed at load and verified against the frozen hash (task spec section 1).
PROMPT_TEMPLATE_C4_HASH = prompt_template_c4_hash()
if PROMPT_TEMPLATE_C4_HASH != FROZEN_PROMPT_TEMPLATE_C4_SHA256:
    raise FeedbackError(
        "the Campaign-4 prompt template hash "
        f"{PROMPT_TEMPLATE_C4_HASH!r} != the frozen "
        f"{FROZEN_PROMPT_TEMPLATE_C4_SHA256!r}"
    )


def load_prompt_template_c4() -> str:
    """Read the frozen Campaign-4 prompt template, hash-verified."""
    text = PROMPT_TEMPLATE_C4_PATH.read_text(encoding="utf-8")
    actual = hashlib.sha256(
        PROMPT_TEMPLATE_C4_PATH.read_bytes()
    ).hexdigest()
    if actual != FROZEN_PROMPT_TEMPLATE_C4_SHA256:
        raise FeedbackError(
            "the Campaign-4 prompt template changed after import: "
            f"{actual!r} != {FROZEN_PROMPT_TEMPLATE_C4_SHA256!r}"
        )
    return text


def render_c4_prompt(
    feedback_block: str, *, template: str | None = None
) -> str:
    """Substitute the treatment feedback block into the frozen C4 prompt."""
    if not isinstance(feedback_block, str):
        raise FeedbackError("feedback_block must be text")
    text = load_prompt_template_c4() if template is None else template
    if C4_FEEDBACK_TOKEN not in text:
        raise FeedbackError(
            f"the Campaign-4 template is missing {C4_FEEDBACK_TOKEN!r}"
        )
    return text.replace(C4_FEEDBACK_TOKEN, feedback_block)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return pd.Timestamp(value).date()


def _canonical_json(obj: Any) -> str:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _round3(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    rounded = round(number, 3)
    return 0.0 if rounded == 0 else rounded


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


# ---------------------------------------------------------------------------
# treatment feedback
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedbackRecord:
    """One proposal's treatment feedback row.

    ``rank_stat`` / ``l_t0_ewc_t`` / ``l_td_ewc_t`` are the
    :class:`~smart_beta.campaign4.ranking.RankStatistic` objects the renderer
    firewall-checks (erratum E1 R1).  ``gate_window_cut`` is the ``window_cut``
    of the :class:`~smart_beta.campaign4.diversity.DiversityGate` that produced
    ``restatement`` / ``nearest``.
    """

    proposal_id: str
    factor_spec_hash: str
    expression: Mapping[str, Any]
    family: str | None
    predicted_direction: str | None
    status: str
    reason: str | None
    rank_stat: RankStatistic
    rank: int | None
    l_t0_ewc_t: RankStatistic
    l_td_ewc_t: RankStatistic
    restatement: tuple[str | None, float | None] | None
    nearest: tuple[str, float] | None
    gate_window_cut: date | str | datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "factor_spec_hash": self.factor_spec_hash,
            "expression": self.expression,
            "family": self.family,
            "predicted_direction": self.predicted_direction,
            "status": self.status,
            "reason": self.reason,
            "rank_stat": self.rank_stat.to_dict(),
            "rank": self.rank,
            "l_t0_ewc_t": self.l_t0_ewc_t.to_dict(),
            "l_td_ewc_t": self.l_td_ewc_t.to_dict(),
            "restatement": self.restatement,
            "nearest": self.nearest,
            "gate_window_cut": self.gate_window_cut,
        }


def _coerce_feedback_record(value: Any) -> FeedbackRecord:
    if isinstance(value, FeedbackRecord):
        return value
    if isinstance(value, Mapping):
        return FeedbackRecord(
            proposal_id=value["proposal_id"],
            factor_spec_hash=value["factor_spec_hash"],
            expression=value["expression"],
            family=value.get("family"),
            predicted_direction=value.get("predicted_direction"),
            status=value["status"],
            reason=value.get("reason"),
            rank_stat=value["rank_stat"],
            rank=value.get("rank"),
            l_t0_ewc_t=value["l_t0_ewc_t"],
            l_td_ewc_t=value["l_td_ewc_t"],
            restatement=value.get("restatement"),
            nearest=value.get("nearest"),
            gate_window_cut=value.get("gate_window_cut"),
        )
    raise FeedbackError(
        f"a feedback record must be a FeedbackRecord or mapping, got "
        f"{type(value).__name__}"
    )


def _check_statistic(stat: Any, cut: date, field_name: str) -> None:
    """Fail closed unless ``stat`` was computed strictly before ``cut``."""

    if not isinstance(stat, RankStatistic):
        raise FeedbackError(
            f"{field_name} must be a RankStatistic, got {type(stat).__name__}"
        )
    if _as_date(stat.window_cut) != cut:
        raise C4FirewallError(
            f"{field_name} was computed under window_cut "
            f"{_as_date(stat.window_cut).isoformat()}, not the renderer's "
            f"{cut.isoformat()}"
        )
    if stat.last_period is not None and _as_date(stat.last_period) >= cut:
        raise C4FirewallError(
            f"{field_name} used period {_as_date(stat.last_period).isoformat()} "
            f"at/after the window cut {cut.isoformat()}"
        )


def _feedback_line(record: FeedbackRecord) -> dict[str, Any]:
    restatement = record.restatement
    nearest = record.nearest
    return {
        "proposal_id": record.proposal_id,
        "factor_spec_hash": record.factor_spec_hash,
        "expression": _canonical_json(dict(record.expression)),
        "family": record.family,
        "predicted_direction": record.predicted_direction,
        "status": record.status,
        "reason": record.reason,
        "rank_stat": _round3(record.rank_stat.value),
        "rank": None if record.rank is None else int(record.rank),
        "l_t0_ewc_t": _round3(record.l_t0_ewc_t.value),
        "l_td_ewc_t": _round3(record.l_td_ewc_t.value),
        "restatement": (
            None
            if restatement is None
            else {
                "driver": restatement[0],
                "abs_e": _round3(restatement[1]),
            }
        ),
        "nearest": (
            None
            if nearest is None
            else {
                "proposal_id": nearest[0],
                "rho": _round3(nearest[1]),
            }
        ),
    }


def render_c4_feedback(
    records: Sequence[Any],
    *,
    window_cut: date | str | datetime,
) -> str:
    """Render the treatment feedback block, firewall-bound to the statistics.

    With no proposals the block is exactly ``No prior proposals.``.  Otherwise
    it is one canonical compact JSON line per proposal, in proposal order.
    """

    cut = _as_date(window_cut)
    coerced = tuple(_coerce_feedback_record(value) for value in records)
    if not coerced:
        return "No prior proposals."
    lines: list[str] = []
    for record in coerced:
        if record.status not in C4_STATUSES:
            raise FeedbackError(
                f"unknown feedback status {record.status!r}; expected one of "
                f"{sorted(C4_STATUSES)}"
            )
        _check_statistic(record.rank_stat, cut, "rank_stat")
        _check_statistic(record.l_t0_ewc_t, cut, "l_t0_ewc_t")
        _check_statistic(record.l_td_ewc_t, cut, "l_td_ewc_t")
        if record.gate_window_cut is not None:
            if _as_date(record.gate_window_cut) != cut:
                raise C4FirewallError(
                    "the A4/A6 gate was built with window_cut "
                    f"{_as_date(record.gate_window_cut).isoformat()}, not the "
                    f"renderer's {cut.isoformat()}"
                )
        elif record.restatement is not None or record.nearest is not None:
            raise FeedbackError(
                "restatement/nearest require the A4 gate's window_cut"
            )
        lines.append(_canonical_json(_feedback_line(record)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# control feedback
# ---------------------------------------------------------------------------


def _slice_before(
    periods: Sequence[Any],
    values: Sequence[Any],
    cut: date,
) -> tuple[tuple[Any, ...], tuple[float, ...]]:
    """Keep the periods strictly before ``cut`` (paired with their values)."""

    kept_periods: list[Any] = []
    kept_values: list[float] = []
    for period, value in zip(periods, values):
        dated = _as_date(period)
        if dated < cut:
            kept_periods.append(period)
            kept_values.append(float(value))
    return tuple(kept_periods), tuple(kept_values)


def control_metrics(
    series_export: Any,
    *,
    not_assessed: bool,
    window_cut: date | str | datetime,
) -> tuple[MetricValue, ...]:
    """The C3 control metrics of one export, recomputed on the training window.

    The names, order and structure mirror
    :func:`smart_beta.campaign3_eval.metrics_from_diagnostics`: for every record
    in export order, ``<cell>_net_long_short_mean`` then ``<cell>_hac_t``, then
    ``is_assessable``.  ``B`` cells use ``b_series`` and ``L`` cells use
    ``l_series``; each value is recomputed on the periods strictly before
    ``window_cut`` with :func:`~smart_beta.evaluation.metrics.long_short_mean_tstat`.

    A NOT_ASSESSED cell yields ``value=None`` and ``n_obs=0`` (the frozen
    function's shape for such a cell).  For an assessed cell ``n_obs`` is the
    number of training periods the recomputed statistic consumed.
    ``is_assessable`` follows ``not_assessed`` exactly as the frozen function
    does.
    """

    cut = _as_date(window_cut)
    records = tuple(getattr(series_export, "records", series_export))
    metrics: list[MetricValue] = []
    for record in records:
        cell = str(record.cell).lower()
        if not record.assessed:
            metrics.append(
                MetricValue(name=f"{cell}_net_long_short_mean", value=None, n_obs=0)
            )
            metrics.append(
                MetricValue(name=f"{cell}_hac_t", value=None, n_obs=0)
            )
            continue
        raw_values = (
            record.b_series if str(record.estimand) == "B" else record.l_series
        )
        used_periods, used_values = _slice_before(record.periods, raw_values, cut)
        for period in used_periods:
            if _as_date(period) >= cut:
                raise C4FirewallError(
                    f"control cell {record.cell!r} would use period "
                    f"{_as_date(period).isoformat()} at/after the window cut "
                    f"{cut.isoformat()}"
                )
        if not used_values:
            metrics.append(
                MetricValue(name=f"{cell}_net_long_short_mean", value=None, n_obs=0)
            )
            metrics.append(
                MetricValue(name=f"{cell}_hac_t", value=None, n_obs=0)
            )
            continue
        summary = long_short_mean_tstat(list(used_values))
        n_obs = int(summary.n_obs)
        metrics.append(
            MetricValue(
                name=f"{cell}_net_long_short_mean",
                value=_finite_or_none(summary.value),
                n_obs=n_obs,
            )
        )
        metrics.append(
            MetricValue(
                name=f"{cell}_hac_t",
                value=_finite_or_none(summary.t_stat),
                n_obs=n_obs,
            )
        )
    metrics.append(
        MetricValue(
            name="is_assessable",
            value=0.0 if not_assessed else 1.0,
            n_obs=0 if not_assessed else 1,
        )
    )
    return tuple(metrics)


def _verified_v2_template() -> str:
    template = llm_generator_deepseek.load_prompt_template_v2()
    actual = hashlib.sha256(template.encode("utf-8")).hexdigest()
    if actual != llm_generator_deepseek.PROMPT_TEMPLATE_V2_HASH:
        raise FeedbackError(
            "the campaign3_v2 template hash changed: "
            f"{actual!r} != {llm_generator_deepseek.PROMPT_TEMPLATE_V2_HASH!r}"
        )
    return template


def render_control_prompt(
    visible_history: GeneratorVisibleResearchHistory | None,
    feedback: ResearchFeedback,
    *,
    window_cut: date | str | datetime,
) -> str:
    """Render the control prompt: the C3 v2 template with C3-style feedback.

    ``feedback`` is supplied by the caller (C4M1-2 builds it from the
    executor's history, whose ``TaEvaluation.metrics`` come from
    :func:`control_metrics`).  No experiment or hypothesis identity is
    fabricated here.  ``window_cut`` is validated and carried for interface
    parity; the training-window guarantee is owned by :func:`control_metrics`,
    which sliced the export before the feedback was formed.
    """

    _as_date(window_cut)
    if not isinstance(feedback, ResearchFeedback):
        raise FeedbackError(
            f"feedback must be a ResearchFeedback, got {type(feedback).__name__}"
        )
    template = _verified_v2_template()
    visible = (
        GeneratorVisibleResearchHistory()
        if visible_history is None
        else visible_history
    )
    return llm_generator.render_prompt(visible, feedback, template=template)
