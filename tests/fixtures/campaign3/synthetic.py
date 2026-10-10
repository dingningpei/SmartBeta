"""Synthetic C3-X fixtures: raw candidates, a stub generator and evaluator.

Authority: ``docs/campaign3_p4_implementation_spec.md`` (task C3-X). Nothing
here reads a production artifact, ``data_store``, the reserve, holdout or the
Knowledge-PIT; no network call is made. The raw candidate builder emits an
A§15A SIMPLE expression (``lag(tri,a)/lag(tri,b)``) so every candidate is
Phase-6 admissible and P-Q v2.3-certifiable without an event layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from smart_beta.campaign.campaign3_eval import TaEvaluation
from smart_beta.campaign.campaign3_generation import GenerationOutput
from smart_beta.evaluation.spec import MetricValue
from smart_beta.research.generator import RawArtifact

__all__ = [
    "TRI_REQUIREMENT",
    "simple_spec_dict",
    "candidate",
    "raw_artifact",
    "generation_output",
    "ScriptedGenerator",
    "StubEvaluator",
    "make_ta_evaluation",
]

TRI_REQUIREMENT: dict[str, Any] = {
    "semantic_id": "total_return_index",
    "frequency": "daily",
    "observation_period": "instant",
    "units": "ratio",
    "lookback": 0,
    "revision_policy": "point_in_time",
    "require_knowledge_date": True,
    "require_positive_vintage_identity": False,
}


def simple_spec_dict(
    *,
    numerator_lag: int = 21,
    denominator_lag: int = 252,
    sign: int = 1,
    spec_id: str | None = None,
) -> dict[str, Any]:
    """A Phase-6 SIMPLE ``lag(tri,a)/lag(tri,b)`` FactorSpec dict.

    ``0 <= numerator_lag < denominator_lag <= 504`` keeps it inside the
    A§15A SIMPLE_G grammar and the frozen bounds, so distinct lag pairs give
    distinct factor specs. ``sign=-1`` yields a Phase-6-valid spec that P-Q
    v2.3 nonetheless refuses (``PQV2_SIGN_NOT_CERTIFIED``).
    """
    if not (0 <= numerator_lag < denominator_lag <= 504):
        raise ValueError("require 0 <= numerator_lag < denominator_lag <= 504")
    return {
        "id": spec_id or f"c3_probe_{numerator_lag}_{denominator_lag}",
        "description": "synthetic C3-X SIMPLE tri-ratio probe",
        "hypothesis": "C3-PROBE",
        "expression": {
            "op": "div",
            "left": {
                "op": "lag",
                "operand": {"op": "field", "role": "tri"},
                "periods": numerator_lag,
            },
            "right": {
                "op": "lag",
                "operand": {"op": "field", "role": "tri"},
                "periods": denominator_lag,
            },
        },
        "inputs": [{"alias": "tri", "requirement": dict(TRI_REQUIREMENT)}],
        "frequency": "daily",
        "missing_policy": "propagate",
        "sign": sign,
    }


def candidate(
    spec: dict[str, Any],
    *,
    question: str = "Does the synthetic C3-X probe predict returns?",
    rationale: str = "A synthetic, non-empirical C3-X fixture rationale.",
) -> dict[str, Any]:
    """One raw candidate in the frozen normalization schema."""
    return {
        "factor_spec": spec,
        "research_question": question,
        "economic_rationale": rationale,
    }


def raw_artifact(*candidates: dict[str, Any]) -> RawArtifact:
    """A frozen raw-artifact payload carrying the given candidates."""
    payload = {"schema_version": 1, "candidates": list(candidates)}
    return RawArtifact(
        content=json.dumps(payload, sort_keys=True, separators=(",", ":"))
    )


def generation_output(
    *candidates: dict[str, Any],
    tokens_used: int = 100,
    cost_used: float | None = 0.01,
    model_id: str = "claude-opus-5-5",
) -> GenerationOutput:
    return GenerationOutput(
        raw_artifact=raw_artifact(*candidates),
        tokens_used=tokens_used,
        cost_used=cost_used,
        model_id=model_id,
    )


@dataclass
class ScriptedGenerator:
    """A deterministic stub matching ``(visible, feedback) -> GenerationOutput``.

    Either supplies a fixed sequence of outputs (the last one repeats) or a
    ``factory`` callable that receives the call index. Every call's
    ``(visible, feedback)`` is recorded for firewall/order assertions.
    """

    outputs: Sequence[Any] = ()
    factory: Any = None
    calls: list[tuple[Any, Any]] = field(default_factory=list)
    raise_on: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.factory is None and not self.outputs:
            raise ValueError("ScriptedGenerator needs outputs or a factory")

    def __call__(self, visible: Any, feedback: Any) -> GenerationOutput:
        index = len(self.calls)
        self.calls.append((visible, feedback))
        if index in self.raise_on:
            raise RuntimeError("synthetic generator failure")
        if self.factory is not None:
            return self.factory(visible, feedback, index)
        if index < len(self.outputs):
            return self.outputs[index]
        return self.outputs[-1]


def make_ta_evaluation(
    proposal: Any,
    *,
    metrics: tuple[MetricValue, ...] | None = None,
    assessed: bool = True,
    reason: str | None = None,
    fold_key: str = "is",
    diagnostics_hash: str | None = None,
) -> TaEvaluation:
    """A synthetic :class:`TaEvaluation` bound to ``proposal``."""
    pid = proposal.proposal_id
    fhash = proposal.proposed_factor_spec_hash
    if metrics is None:
        metrics = (
            MetricValue(name="b-t0_net_long_short_mean", value=0.0188, n_obs=54),
            MetricValue(name="b-t0_hac_t", value=3.4, n_obs=54),
            MetricValue(name="l-t0_net_long_short_mean", value=0.0188, n_obs=54),
            MetricValue(name="l-t0_hac_t", value=3.4, n_obs=54),
            MetricValue(name="is_assessable", value=1.0, n_obs=1),
        )
    return TaEvaluation(
        proposal_id=pid,
        factor_spec_hash=fhash,
        declaration_hash=(diagnostics_hash or ("d" * 64)),
        is_fold_key=fold_key,
        diagnostics_hash=diagnostics_hash or ("e" * 64),
        assessed=assessed,
        not_assessed_reason=reason if not assessed else None,
        metrics=metrics,
    )


@dataclass
class StubEvaluator:
    """A deterministic stub matching ``proposal -> TaEvaluation``.

    ``on_call`` is invoked with the proposal before the result is returned, so
    tests can assert ordering (e.g. registration already happened, the K-PIT
    freeze already exists).
    """

    assessed: bool = True
    reason: str | None = None
    on_call: Any = None
    calls: list[Any] = field(default_factory=list)

    def __call__(self, proposal: Any) -> TaEvaluation:
        self.calls.append(proposal)
        if self.on_call is not None:
            self.on_call(proposal, len(self.calls))
        return make_ta_evaluation(
            proposal,
            assessed=self.assessed,
            reason=self.reason,
        )
