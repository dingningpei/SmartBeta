"""Synthetic C4M1-2 fixtures: a fixed generator, a synthetic assembly.

Authority: ``worker_tasks/campaign4_m1/task-c4m1-2-walkforward-harness.md``
"Tests (synthetic only)".  Nothing here reads a production artifact,
``data_store``, the reserve, a holdout or the Knowledge-PIT, and no network
call is made: the LLM is an in-process scripted callable and the full-IS
evaluation is built from deterministic per-period series.

The raw candidate builder reuses the C3 synthetic SIMPLE
``lag(tri,a)/lag(tri,b)`` FactorSpec (Phase-6 admissible and P-Q v2.3
certifiable with no event layer) and adds the three Campaign-4 A2 fields.
"""

from __future__ import annotations

import calendar
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

import pandas as pd

from smart_beta.campaign4 import contract as C
from smart_beta.campaign4.generation import CandidateEvaluation
from smart_beta.campaign4.ranking import RankStatistic
from smart_beta.dataset_v1 import pqv2_evaluation as _eval
from smart_beta.dataset_v1.pqv2_series import PQv2SeriesExport, PQv2SeriesRecord
from smart_beta.research.generator import RawArtifact
from smart_beta.research.llm_generator import GenerationOutput
from smart_beta.spec.factor_spec import factor_spec_hash

# The C3 SIMPLE builder (import-only; no protected file is modified).
from tests.fixtures.campaign3.synthetic import simple_spec_dict

__all__ = [
    "IS_FORMATIONS",
    "STOCKS",
    "month_ends",
    "formations_54",
    "panel_key",
    "spec_dict",
    "c4_candidate",
    "c3_candidate",
    "c4_response",
    "c3_response",
    "FakeCompleter",
    "make_panel",
    "make_exports",
    "SyntheticEvaluationBuilder",
]


def month_ends(start_year: int, start_month: int, count: int) -> tuple[date, ...]:
    """``count`` month-end dates starting at ``start_year``/``start_month``."""

    result: list[date] = []
    year, month = start_year, start_month
    for _ in range(count):
        last = calendar.monthrange(year, month)[1]
        result.append(date(year, month, last))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return tuple(result)


def formations_54() -> tuple[date, ...]:
    """The 54 synthetic month-ends that reproduce the frozen W1/W2 splits."""

    return month_ends(2016, 1, 54)


IS_FORMATIONS: tuple[date, ...] = formations_54()

#: The A4 gate needs at least ``MIN_COMMON_MEMBERS`` (30) common members
#: per formation, so the synthetic panel carries 40.
STOCKS: tuple[str, ...] = tuple(f"S{index:02d}" for index in range(40))


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------


def panel_key(spec_or_id: Any) -> str:
    """The panel-family key encoded in a spec id (``<key>__<a>__<b>``)."""

    identifier = spec_or_id if isinstance(spec_or_id, str) else spec_or_id.id
    return str(identifier).split("__", 1)[0]


def spec_dict(
    key: str,
    *,
    numerator_lag: int = 21,
    denominator_lag: int = 252,
    family: str | None = None,
    sign: int = 1,
) -> dict[str, Any]:
    """A synthetic Phase-6 SIMPLE FactorSpec dict with a stable id.

    ``sign=-1`` is Phase-6 valid but refused by P-Q v2.3 certification, so it
    produces a ``NOT_CERTIFIED`` proposal.
    """

    return simple_spec_dict(
        numerator_lag=numerator_lag,
        denominator_lag=denominator_lag,
        sign=sign,
        spec_id=f"{key}__{numerator_lag}__{denominator_lag}",
    )


def c4_candidate(
    spec: dict[str, Any],
    *,
    family: str = "momentum",
    direction: str = "higher_return",
    invalid_a2: bool = False,
) -> dict[str, Any]:
    """A C4 raw candidate (with the three A2 fields)."""

    candidate = {
        "factor_spec": spec,
        "research_question": "does the synthetic probe predict the cross-section?",
        "economic_rationale": "a synthetic, non-empirical C4 fixture rationale",
        "predicted_relation": {"variable": "synthetic probe", "direction": direction},
        "direction_check": "a high expression value is the long side",
        "family": family,
    }
    if invalid_a2:
        candidate["predicted_relation"] = {"variable": "", "direction": direction}
    return candidate


def c3_candidate(spec: dict[str, Any]) -> dict[str, Any]:
    """A C3 raw candidate (no A2 fields)."""

    return {
        "factor_spec": spec,
        "research_question": "does the synthetic probe predict the cross-section?",
        "economic_rationale": "a synthetic, non-empirical C3 fixture rationale",
    }


def c4_response(candidates: Sequence[dict[str, Any]]) -> str:
    return json.dumps(
        {"schema_version": C.CAMPAIGN4_SCHEMA, "candidates": list(candidates)},
        sort_keys=True,
        separators=(",", ":"),
    )


def c3_response(candidates: Sequence[dict[str, Any]]) -> str:
    return json.dumps(
        {"schema_version": 1, "candidates": list(candidates)},
        sort_keys=True,
        separators=(",", ":"),
    )


# ---------------------------------------------------------------------------
# a fixed in-process generator
# ---------------------------------------------------------------------------


@dataclass
class FakeCompleter:
    """A deterministic ``(system, user) -> GenerationOutput`` callable."""

    responses: Sequence[str]
    tokens_used: int = 1000
    cost_used: float | None = 0.001
    raise_on: tuple[int, ...] = ()
    prompts: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, system_prompt: str, user_prompt: str) -> GenerationOutput:
        index = len(self.prompts)
        self.prompts.append((system_prompt, user_prompt))
        if index in self.raise_on:
            raise RuntimeError("synthetic generator failure")
        raw = self.responses[min(index, len(self.responses) - 1)]
        return GenerationOutput(
            raw_artifact=RawArtifact(content=raw),
            tokens_used=self.tokens_used,
            cost_used=self.cost_used,
        )


# ---------------------------------------------------------------------------
# panels / exports
# ---------------------------------------------------------------------------


def _value(seed: str, index: int) -> float:
    digest = hashlib.sha256(f"{seed}:{index}".encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % 2001 - 1000) / 10000.0


def make_panel(
    formations: Sequence[date],
    key: str,
    *,
    stocks: Sequence[str] = STOCKS,
) -> pd.DataFrame:
    """A deterministic ``(date, stock_id, value)`` panel for one panel key."""

    return pd.DataFrame(
        [
            {"date": day, "stock_id": stock, "value": _value(f"{key}:{stock}", i)}
            for i, day in enumerate(formations)
            for stock in stocks
        ]
    )


def _record(
    cell: str,
    periods: Sequence[date],
    values: Sequence[float],
    *,
    estimand: str,
) -> PQv2SeriesRecord:
    series = tuple(float(value) for value in values)
    return PQv2SeriesRecord(
        cell=cell,
        estimand=estimand,
        scenario=cell.split("-")[1],
        event_scenario=None,
        periods=tuple(periods),
        b_series=series,
        l_series=series,
        gross_series=series,
        net_series=series,
        status=None,
        reason=None,
        declaration_hash="c" * 64,
    )


def make_exports(
    formations: Sequence[date], key: str
) -> tuple[PQv2SeriesExport, PQv2SeriesExport]:
    """The base T0/TS export and the TD export of one synthetic candidate."""

    values = [_value(key, index) for index in range(len(formations))]
    base = PQv2SeriesExport(
        declaration_hash="c" * 64,
        diagnostics_hash="d" * 64,
        estimator=_eval.ESTIMATOR_IDENTITY,
        records=(
            _record("B-T0", formations, values, estimand="B"),
            _record("L-T0", formations, values, estimand="L"),
            _record("B-TS", formations, values, estimand="B"),
            _record("L-TS", formations, values, estimand="L"),
        ),
    )
    td = PQv2SeriesExport(
        declaration_hash="c" * 64,
        diagnostics_hash="d" * 64,
        estimator=_eval.ESTIMATOR_IDENTITY,
        records=(
            _record("B-TD", formations, values, estimand="B"),
            _record("L-TD", formations, values, estimand="L"),
        ),
    )
    return base, td


# ---------------------------------------------------------------------------
# the synthetic full-IS evaluation builder
# ---------------------------------------------------------------------------


class SyntheticEvaluationBuilder:
    """A deterministic ``spec -> CandidateEvaluation`` builder.

    The same spec hash always yields the same evaluation (so the cache's
    "evaluate once" property is observable), and the builder records every
    call so a test can prove a rejected candidate was never evaluated.
    """

    def __init__(
        self,
        formations: Sequence[date] = IS_FORMATIONS,
        *,
        not_assessed_keys: Sequence[str] = (),
    ) -> None:
        self.formations = tuple(formations)
        self.not_assessed_keys = set(not_assessed_keys)
        self.calls: list[str] = []

    def __call__(self, spec: Any) -> CandidateEvaluation:
        key = factor_spec_hash(spec)
        self.calls.append(key)
        base, td = make_exports(self.formations, panel_key(spec))
        not_assessed = panel_key(spec) in self.not_assessed_keys
        return CandidateEvaluation(
            factor_spec_hash=key,
            declaration_hash="c" * 64,
            diagnostics_hash=hashlib.sha256(key.encode("utf-8")).hexdigest(),
            diagnostics=None,
            base_export=base,
            td_export=td,
            not_assessed=not_assessed,
            not_assessed_reason="SYNTHETIC_NOT_ASSESSED" if not_assessed else None,
        )


# Re-exported so a test can build a hand-made RankStatistic without importing
# the C4M1-1 module directly.
__all__.append("RankStatistic")
