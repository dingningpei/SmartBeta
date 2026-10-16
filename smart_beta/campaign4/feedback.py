"""Campaign-4 M1 -- treatment / control renderers (task C4M1-1).

Authority: ``worker_tasks/campaign4_m1/task-c4m1-1-contract-core.md``
section 5 and ``docs/campaign4_m1a_contract_calibration_draft.md`` section 2.

Two renderers live here:

* :func:`render_c4_feedback` -- the **treatment** feedback block: one
  canonical compact JSON line per proposal, in proposal order.  Each line
  carries the A2 declaration, the expression, the training-window rank
  statistic and rank, the L-T0 / L-TD EWC t, the A6 restatement and the A4
  nearest neighbour.
* :func:`render_control_prompt` -- the **control** prompt: the frozen C3 v2
  template (hash-equal to :data:`PROMPT_TEMPLATE_V2_HASH`) rendered through
  ``llm_generator.render_prompt`` with C3 structures whose IS metrics are
  recomputed on the training periods with the same estimator the frozen
  ``campaign3_eval.metrics_from_diagnostics`` uses
  (:func:`smart_beta.evaluation.metrics.long_short_mean_tstat`).

The frozen C4 prompt file ``smart_beta/research/prompts/campaign4_v1.txt`` is a
byte-identical copy of ``worker_tasks/campaign4_m1/campaign4_v1_prompt.txt``;
its SHA-256 is asserted at import against the frozen hash.  The only
placeholder is ``<<<C4_FEEDBACK>>>``.

Firewall
--------

Both renderers take ``window_cut`` and raise :class:`C4FirewallError` if any
value they are given carries a period at/after the cut.  No renderer can see a
test-window value.

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
from smart_beta.evaluation.metrics import long_short_mean_tstat
from smart_beta.evaluation.spec import MetricValue
from smart_beta.research import llm_generator, llm_generator_deepseek
from smart_beta.research.history import (
    DevelopmentFoldRole,
    ExperimentFeedback,
    FoldEvidence,
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
    "ControlSeries",
    "prompt_template_c4_hash",
    "load_prompt_template_c4",
    "render_c4_prompt",
    "render_c4_feedback",
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

#: The deterministic C3-style control identities (never a clock / UUID).
_CONTROL_EXPERIMENT_ID = hashlib.sha256(
    b"campaign4:m1:control:experiment"
).hexdigest()
_CONTROL_HYPOTHESIS_ID = hashlib.sha256(
    b"campaign4:m1:control:hypothesis"
).hexdigest()
_CONTROL_IS_FOLD_KEY = "campaign4-m1-control-is"


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


# ---------------------------------------------------------------------------
# treatment feedback
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedbackRecord:
    """One proposal's treatment feedback row.

    ``period`` is the proposal's evaluation period; it is firewall-checked and
    never rendered.  ``expression`` is the FactorSpec AST.
    """

    proposal_id: str
    factor_spec_hash: str
    expression: Mapping[str, Any]
    family: str | None
    predicted_direction: str | None
    status: str
    reason: str | None
    rank_stat: float | None
    rank: int | None
    l_t0_ewc_t: float | None
    l_td_ewc_t: float | None
    restatement: tuple[str | None, float | None] | None
    nearest: tuple[str, float] | None
    period: date | str | datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "factor_spec_hash": self.factor_spec_hash,
            "expression": self.expression,
            "family": self.family,
            "predicted_direction": self.predicted_direction,
            "status": self.status,
            "reason": self.reason,
            "rank_stat": self.rank_stat,
            "rank": self.rank,
            "l_t0_ewc_t": self.l_t0_ewc_t,
            "l_td_ewc_t": self.l_td_ewc_t,
            "restatement": self.restatement,
            "nearest": self.nearest,
            "period": self.period,
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
            rank_stat=value.get("rank_stat"),
            rank=value.get("rank"),
            l_t0_ewc_t=value.get("l_t0_ewc_t"),
            l_td_ewc_t=value.get("l_td_ewc_t"),
            restatement=value.get("restatement"),
            nearest=value.get("nearest"),
            period=value["period"],
        )
    raise FeedbackError(
        f"a feedback record must be a FeedbackRecord or mapping, got "
        f"{type(value).__name__}"
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
        "rank_stat": _round3(record.rank_stat),
        "rank": None if record.rank is None else int(record.rank),
        "l_t0_ewc_t": _round3(record.l_t0_ewc_t),
        "l_td_ewc_t": _round3(record.l_td_ewc_t),
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
    """Render the treatment feedback block.

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
        if _as_date(record.period) >= cut:
            raise C4FirewallError(
                f"proposal {record.proposal_id!r} carries period "
                f"{_as_date(record.period).isoformat()} at/after the window cut "
                f"{cut.isoformat()}; the feedback is training-window only"
            )
        lines.append(_canonical_json(_feedback_line(record)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# control prompt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlSeries:
    """One C3-style cell's raw training-period return series.

    ``cell`` is e.g. ``B-T0`` / ``L-T0`` / ``B-TS`` / ``L-TS``.  The metrics
    are recomputed from ``values`` by
    :func:`~smart_beta.evaluation.metrics.long_short_mean_tstat`.
    """

    cell: str
    periods: tuple[Any, ...]
    values: tuple[float, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "periods": list(self.periods),
            "values": list(self.values),
        }


def _coerce_control_series(value: Any) -> ControlSeries:
    if isinstance(value, ControlSeries):
        return value
    if isinstance(value, Mapping):
        return ControlSeries(
            cell=value["cell"],
            periods=tuple(value["periods"]),
            values=tuple(float(item) for item in value["values"]),
        )
    raise FeedbackError(
        "a control series must be a ControlSeries or mapping, got "
        f"{type(value).__name__}"
    )


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
    training_series: Sequence[Any],
    *,
    window_cut: date | str | datetime,
) -> str:
    """Render the control prompt: the C3 v2 template with training-window C3
    feedback metrics recomputed by the frozen estimator."""

    cut = _as_date(window_cut)
    template = _verified_v2_template()
    metrics: list[MetricValue] = []
    for raw in training_series:
        series = _coerce_control_series(raw)
        if len(series.periods) != len(series.values):
            raise FeedbackError(
                f"control series {series.cell!r} has {len(series.periods)} "
                f"periods but {len(series.values)} values"
            )
        for period in series.periods:
            if _as_date(period) >= cut:
                raise C4FirewallError(
                    f"control series {series.cell!r} carries period "
                    f"{_as_date(period).isoformat()} at/after the window cut "
                    f"{cut.isoformat()}; the control feedback is training-window "
                    "only"
                )
        summary = long_short_mean_tstat(list(series.values))
        cell = str(series.cell).lower()
        metrics.append(
            summary.to_metric_value(f"{cell}_net_long_short_mean")
        )
        metrics.append(summary.to_metric_value(f"{cell}_hac_t"))
    metrics.append(MetricValue(name="is_assessable", value=1.0, n_obs=1))

    fold = FoldEvidence(
        fold_key=_CONTROL_IS_FOLD_KEY,
        role=DevelopmentFoldRole.IS,
        metrics=tuple(metrics),
    )
    experiment = ExperimentFeedback(
        experiment_id=_CONTROL_EXPERIMENT_ID,
        hypothesis_id=_CONTROL_HYPOTHESIS_ID,
        is_folds=(fold,),
    )
    feedback = ResearchFeedback(experiments=(experiment,))
    visible = (
        GeneratorVisibleResearchHistory()
        if visible_history is None
        else visible_history
    )
    return llm_generator.render_prompt(visible, feedback, template=template)
