"""Synthetic C4M1-3 fixtures: scripted IS evaluations + genuine C4 run states.

Authority: ``worker_tasks/campaign4_m1/task-c4m1-3-selection-study.md``
"Tests (synthetic only; no network, no real data)".

Everything here is offline and in-process: the per-period series are built from
a deterministic shape (a chosen first-half / second-half / test-window level
plus one shared wiggle), and the ``executor_state.json`` files are produced by
running the **real** C4 generation/executor path in a temporary directory, so
the pool reconstruction is exercised against the persisted state the M1-e runs
actually wrote.  Nothing reads ``data_store``, the reserve, a holdout or the
Knowledge-PIT, and no network call is made.

The pool shapes (:data:`POOL_SHAPES`) are chosen so the four pre-registered
rules select *different* candidates with wide margins (see the table comment):
the ordering asserted in the rule tests is a fixture property, not something
derived from the implementation under test.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from smart_beta.campaign.campaign3_generation import GenerationJournal
from smart_beta.campaign4 import generation as GEN
from smart_beta.campaign4.feedback import (
    CONTROL_PROMPT_TEMPLATE_HASH,
    PROMPT_TEMPLATE_C4_HASH,
)
from smart_beta.campaign4.generation import CandidateEvaluation
from smart_beta.campaign4.selection_study import CandidateSeries, CellSeries
from smart_beta.campaign4.walkforward import ORIGINS, W1_CUT, W2_CUT
from smart_beta.dataset_v1.pqv2_evaluation import ESTIMATOR_IDENTITY
from smart_beta.dataset_v1.pqv2_series import PQv2SeriesExport, PQv2SeriesRecord
from smart_beta.spec.factor_spec import FactorSpec, factor_spec_hash

from tests.fixtures.c4m1.walkforward import synthetic as W

__all__ = [
    "IS_FORMATIONS",
    "STOCKS",
    "ORIGINS",
    "W1_CUT",
    "W2_CUT",
    "spec_dict",
    "c4_candidate",
    "c3_candidate",
    "c4_response",
    "c3_response",
    "FakeCompleter",
    "make_panel",
    "panel_key",
    "wiggle",
    "shaped_series",
    "candidate_series",
    "td_export",
    "base_export",
    "ScriptedBuilder",
    "POOL_FAMILIES",
    "pool_specs",
    "pool_series",
    "pool_shapes",
    "make_pool_run",
    "make_four_runs",
    "make_six_status_run",
    "fake_m1e_report",
]

IS_FORMATIONS: tuple[date, ...] = W.IS_FORMATIONS
STOCKS: tuple[str, ...] = W.STOCKS

spec_dict = W.spec_dict
c4_candidate = W.c4_candidate
c3_candidate = W.c3_candidate
c4_response = W.c4_response
c3_response = W.c3_response
FakeCompleter = W.FakeCompleter
make_panel = W.make_panel
panel_key = W.panel_key


# ---------------------------------------------------------------------------
# scripted per-period series
# ---------------------------------------------------------------------------


def wiggle(count: int, *, amplitude: float = 0.01, seed: str = "wiggle") -> list[float]:
    """A deterministic perturbation shared by every synthetic candidate."""

    return [amplitude * W._value(seed, index) for index in range(count)]


def shaped_series(
    *,
    first: float,
    second: float,
    test: float,
    formations: Sequence[date] = IS_FORMATIONS,
    cut: date = W1_CUT,
) -> list[float]:
    """One 54-period series with chosen first-half / second-half / test levels.

    ``first`` and ``second`` are the means of the two halves of the training
    window before ``cut`` (15 + 15 for W1, 21 + 21 for W2); ``test`` is the mean
    of the periods at/after ``cut``.
    """

    training = [index for index, day in enumerate(formations) if day < cut]
    count = len(training)
    half = count // 2
    perturbation = wiggle(len(formations))
    values: list[float] = []
    for index in range(len(formations)):
        if index < half:
            level = first
        elif index < count:
            level = second
        else:
            level = test
        values.append(level + perturbation[index])
    return values


def _record(
    cell: str,
    formations: Sequence[date],
    values: Sequence[float],
    *,
    assessed: bool = True,
    reason: str | None = None,
) -> PQv2SeriesRecord:
    parts = cell.split("-")
    scenario = parts[1]
    event_scenario = parts[2] if len(parts) > 2 else None
    if not assessed:
        return PQv2SeriesRecord(
            cell=cell,
            estimand="L",
            scenario=scenario,
            event_scenario=event_scenario,
            periods=(),
            b_series=(),
            l_series=(),
            gross_series=(),
            net_series=(),
            status="INSUFFICIENT_SAMPLE",
            reason=reason or "SYNTHETIC_NOT_ASSESSED",
            declaration_hash="c" * 64,
        )
    series = tuple(float(value) for value in values)
    return PQv2SeriesRecord(
        cell=cell,
        estimand="L",
        scenario=scenario,
        event_scenario=event_scenario,
        periods=tuple(formations),
        b_series=series,
        l_series=series,
        gross_series=series,
        net_series=series,
        status=None,
        reason=None,
        declaration_hash="c" * 64,
    )


def td_export(
    cells: Mapping[str, Sequence[float]],
    *,
    formations: Sequence[date] = IS_FORMATIONS,
    not_assessed: bool = False,
    reason: str | None = None,
) -> PQv2SeriesExport:
    """The TD export of one scripted candidate (``L-TD`` and/or ``L-TD-K0/K1``)."""

    records = tuple(
        _record(
            name,
            formations,
            values,
            assessed=not not_assessed,
            reason=reason,
        )
        for name, values in cells.items()
    )
    return PQv2SeriesExport(
        declaration_hash="c" * 64,
        diagnostics_hash="d" * 64,
        estimator=ESTIMATOR_IDENTITY,
        records=records,
    )


def base_export(
    values: Sequence[float], *, formations: Sequence[date] = IS_FORMATIONS
) -> PQv2SeriesExport:
    """The base T0/TS export (only ``L-T0`` is read by the C4 rank path)."""

    return PQv2SeriesExport(
        declaration_hash="c" * 64,
        diagnostics_hash="d" * 64,
        estimator=ESTIMATOR_IDENTITY,
        records=tuple(
            _record(cell, formations, values)
            for cell in ("B-T0", "L-T0", "B-TS", "L-TS")
        ),
    )


def candidate_series(
    values: Sequence[float],
    *,
    key: str = "synthetic",
    cells: Mapping[str, Sequence[float]] | None = None,
    formations: Sequence[date] = IS_FORMATIONS,
) -> CandidateSeries:
    """A ``CandidateSeries`` for the statistics tests (no cache, no evaluation)."""

    named = dict(cells) if cells is not None else {"L-TD": tuple(values)}
    return CandidateSeries(
        factor_spec_hash=key,
        cells=tuple(
            CellSeries(
                cell=name,
                periods=tuple(formations),
                l_series=tuple(float(value) for value in series),
                net_series=tuple(float(value) for value in series),
                assessed=True,
                reason=None,
            )
            for name, series in named.items()
        ),
    )


# ---------------------------------------------------------------------------
# the scripted full-IS evaluation builder
# ---------------------------------------------------------------------------


def _default_script(spec: Any) -> Mapping[str, Any]:
    key = str(panel_key(spec))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    first = 0.02 + 0.001 * (int(digest[:8], 16) % 5)
    return {"cells": {"L-TD": shaped_series(first=first, second=first, test=0.0)}}


@dataclass
class ScriptedBuilder:
    """A deterministic ``spec -> CandidateEvaluation`` builder with per-hash scripts.

    ``scripts`` maps ``factor_spec_hash`` to ``{"cells": {name: values},
    "not_assessed": bool, "reason": str | None}``.  Every call is recorded, and
    the returned evaluations are kept so a test can compare a cached series
    against the live export it came from.
    """

    scripts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    formations: Sequence[date] = IS_FORMATIONS
    default: Callable[[Any], Mapping[str, Any]] = _default_script
    calls: list[str] = field(default_factory=list)
    evaluations: dict[str, Any] = field(default_factory=dict)

    def __call__(self, spec: Any) -> CandidateEvaluation:
        key = factor_spec_hash(spec)
        self.calls.append(key)
        script = self.scripts.get(key) or self.default(spec)
        cells = dict(script["cells"])
        not_assessed = bool(script.get("not_assessed", False))
        reason = script.get("reason")
        first_values = next(iter(cells.values()))
        evaluation = CandidateEvaluation(
            factor_spec_hash=key,
            declaration_hash="c" * 64,
            diagnostics_hash="d" * 64,
            diagnostics=None,
            base_export=base_export(first_values, formations=self.formations),
            td_export=td_export(
                cells,
                formations=self.formations,
                not_assessed=not_assessed,
                reason=reason,
            ),
            not_assessed=not_assessed,
            not_assessed_reason=(reason or "SYNTHETIC_NOT_ASSESSED")
            if not_assessed
            else None,
        )
        self.evaluations[key] = evaluation
        return evaluation


# ---------------------------------------------------------------------------
# a genuine C4 run state (the real generation/executor path)
# ---------------------------------------------------------------------------


def _run_generation(
    run_dir: Path | str,
    *,
    arm: str,
    origin: str,
    responses: Sequence[str],
    builder: ScriptedBuilder,
    n_candidates: int,
    family_cap: int = 2,
) -> Any:
    template = (
        PROMPT_TEMPLATE_C4_HASH if arm == GEN.TREATMENT else CONTROL_PROMPT_TEMPLATE_HASH
    )
    return GEN.run_generation(
        arm=arm,
        policy=GEN.build_campaign4_policy(
            prompt_template_hash=template,
            max_proposal_budget=max(n_candidates, 1),
        ),
        window_cut=ORIGINS[origin],
        llm=FakeCompleter(list(responses)),
        evaluate=builder,
        panel_builder=lambda spec: make_panel(IS_FORMATIONS, panel_key(spec)),
        driver_panels={"D-MOM": make_panel(IS_FORMATIONS, "driver-mom")},
        journal=GenerationJournal(Path(run_dir)),
        family_cap=family_cap,
    )


def _responses(arm: str, candidates: Sequence[Mapping[str, Any]], *, extra: Sequence[str] = ()) -> list[str]:
    if arm == GEN.TREATMENT:
        first = c4_response(candidates)
    else:
        first = c3_response([c3_candidate(item["factor_spec"]) for item in candidates])
    return [first, *extra]


#: The six-shape pool.  ``(name, first-half level, second-half level, test
#: level, early-noise scale, late-noise scale, test-noise scale)``; the first
#: six *training* periods carry the "early" scale and the rest of the training
#: window the "late" one.  Every period also carries the one shared wiggle
#: ``W._value("wiggle", index) * scale``, so the series are deterministic and
#: the four rules select different candidates with wide margins:
#:
#: * ``a`` -- strongly negative first half, strongly positive second half: the
#:   largest S0, but it fails S2's both-halves filter;
#: * ``b`` -- a clean late training window behind a noisy first six periods: a
#:   low S0 and by far the largest S1;
#: * ``c``/``d``/``e``/``f`` -- both halves positive, decreasing S0; ``c`` is
#:   the largest S2 survivor;
#: * the S3 composite is taken over ``a, c, b, d, e`` (the five largest S0).
POOL_SHAPES: tuple[tuple[str, float, float, float, float, float, float], ...] = (
    ("a", -0.06, 0.14, 0.08, 1e-4, 1e-4, 1e-4),
    ("b", 0.030, 0.050, 0.05, 8.0, 1e-4, 1e-4),
    ("c", 0.012, 0.014, -0.010, 1.2, 1.2, 1.2),
    ("d", 0.011, 0.012, -0.010, 1.5, 1.5, 1.5),
    ("e", 0.0095, 0.0105, -0.005, 1.8, 1.8, 1.8),
    ("f", 0.008, 0.009, 0.020, 2.0, 2.0, 2.0),
)

#: One frozen A2 family per shape (``contract.FAMILIES``; each is used once, so
#: the A4 family cap never fires in the pool runs).
POOL_FAMILIES: dict[str, str] = {
    "a": "momentum",
    "b": "reversal",
    "c": "volatility_risk",
    "d": "liquidity_volume",
    "e": "earnings_event",
    "f": "interaction",
}

_LAGS = {"a": 21, "b": 22, "c": 23, "d": 24, "e": 25, "f": 26}


def pool_shapes() -> tuple[tuple[str, float, float, float, float, float, float], ...]:
    return POOL_SHAPES


def pool_series(
    shape: Sequence[float],
    *,
    cut: date,
    formations: Sequence[date] = IS_FORMATIONS,
) -> list[float]:
    """One pool candidate's per-period series under ``cut``'s training split."""

    _name, first, second, test, early, late, test_noise = shape
    training = [index for index, day in enumerate(formations) if day < cut]
    count = len(training)
    half = count // 2
    values: list[float] = []
    for index in range(len(formations)):
        if index < half:
            level = first
        elif index < count:
            level = second
        else:
            level = test
        if index < 6:
            scale = early
        elif index < count:
            scale = late
        else:
            scale = test_noise
        values.append(level + scale * W._value("wiggle", index))
    return values


def pool_specs(origin: str) -> list[dict[str, Any]]:
    """The six specs of one synthetic pool (unique panel key per origin)."""

    return [
        spec_dict(
            f"{origin.lower()}{name}",
            numerator_lag=_LAGS[name],
            denominator_lag=252,
        )
        for name, *_rest in POOL_SHAPES
    ]


def pool_scripts(origin: str) -> dict[str, dict[str, Any]]:
    """``factor_spec_hash -> script`` for the six-shape pool of one origin."""

    scripts: dict[str, dict[str, Any]] = {}
    for shape in POOL_SHAPES:
        name = shape[0]
        spec = spec_dict(
            f"{origin.lower()}{name}",
            numerator_lag=_LAGS[name],
            denominator_lag=252,
        )
        key = factor_spec_hash(FactorSpec.from_dict(spec))
        scripts[key] = {
            "cells": {
                "L-TD": pool_series(shape, cut=ORIGINS[origin]),
            }
        }
    return scripts


def make_pool_run(
    run_dir: Path | str,
    *,
    arm: str,
    origin: str,
    builder: ScriptedBuilder,
    family_cap: int = 2,
) -> Any:
    """Write one synthetic M1-e-shaped run state with the six-shape pool."""

    candidates = [
        c4_candidate(spec, family=POOL_FAMILIES[name])
        for (name, *_rest), spec in zip(POOL_SHAPES, pool_specs(origin))
    ]
    responses = _responses(arm, candidates)
    return _run_generation(
        run_dir,
        arm=arm,
        origin=origin,
        responses=responses,
        builder=builder,
        n_candidates=len(candidates),
        family_cap=family_cap,
    )


def make_four_runs(
    run_root: Path | str, *, builder: ScriptedBuilder, origins: Sequence[str] = ("W1", "W2")
) -> dict[str, Path]:
    """Write the four synthetic runs (both arms x both origins)."""

    root = Path(run_root)
    created: dict[str, Path] = {}
    for origin in origins:
        for arm in (GEN.TREATMENT, GEN.CONTROL):
            directory = root / f"{arm}_{origin}"
            make_pool_run(
                directory, arm=arm, origin=origin, builder=builder
            )
            created[f"{arm}_{origin}"] = directory
    return created


def make_six_status_run(
    run_dir: Path | str, *, builder: ScriptedBuilder, origin: str = "W1"
) -> Path:
    """A treatment run whose state carries every C4 status the pool must sort out.

    ``EVALUATED`` (two), ``NOT_ASSESSED``, ``NOT_CERTIFIED``, ``NEAR_DUPLICATE``,
    ``FAMILY_CAP`` and ``SCHEMA_REJECTED`` -- the six statuses the M1-e runs
    reported, produced by the real gate/certification executor path.
    """

    directory = Path(run_dir)
    specs = {
        "eval_a": spec_dict("sx_eval", numerator_lag=21),
        "not_assessed": spec_dict("sx_na", numerator_lag=22),
        "not_certified": spec_dict("sx_nc", numerator_lag=23, sign=-1),
        "duplicate_a": spec_dict("sx_dup", numerator_lag=24),
        "duplicate_b": spec_dict("sx_dup", numerator_lag=25),
        "size_a": spec_dict("sx_size1", numerator_lag=26),
        "size_b": spec_dict("sx_size2", numerator_lag=27),
        "size_c": spec_dict("sx_size3", numerator_lag=28),
        "schema_rejected": spec_dict("sx_schema", numerator_lag=29),
    }
    families = {
        "eval_a": "momentum",
        "not_assessed": "volatility_risk",
        "not_certified": "liquidity_volume",
        "duplicate_a": "earnings_event",
        "duplicate_b": "earnings_event",
        "size_a": "reversal",
        "size_b": "reversal",
        "size_c": "reversal",
    }
    # The NOT_ASSESSED candidate is a fully admitted proposal whose v2.5
    # diagnostics did not assess its TD cell: the state records it with
    # ``assessed=False`` and a typed reason.
    scripts = dict(builder.scripts)
    scripts[factor_spec_hash(FactorSpec.from_dict(specs["not_assessed"]))] = {
        "cells": {
            "L-TD": shaped_series(first=0.01, second=0.02, test=0.0)
        },
        "not_assessed": True,
        "reason": "SYNTHETIC_NOT_ASSESSED",
    }
    builder.scripts = scripts

    candidates = [
        c4_candidate(specs[name], family=families[name])
        for name in (
            "eval_a",
            "not_assessed",
            "not_certified",
            "duplicate_a",
            "duplicate_b",
            "size_a",
            "size_b",
            "size_c",
        )
    ]
    wrong_schema = json.dumps(
        {
            "schema_version": "campaign3-candidates-v1",
            "candidates": [c3_candidate(specs["schema_rejected"])],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    _run_generation(
        directory,
        arm=GEN.TREATMENT,
        origin=origin,
        responses=_responses(GEN.TREATMENT, candidates, extra=(wrong_schema,)),
        builder=builder,
        n_candidates=len(candidates) + 1,
        family_cap=2,
    )
    return directory


# ---------------------------------------------------------------------------
# an M1-e-shaped report for the reproduction check
# ---------------------------------------------------------------------------


def fake_m1e_report(study_report: Mapping[str, Any]) -> dict[str, Any]:
    """An M1-e-shaped report built from a study report (reproduction inputs)."""

    runs: list[dict[str, Any]] = []
    for run in study_report["runs"]:
        rows = {row["factor_spec_hash"]: row for row in run["base_rate"]["rows"]}
        top5: list[dict[str, Any]] = []
        for index, item in enumerate(run["ranked"][:5]):
            row = rows[item["factor_spec_hash"]]
            top5.append(
                {
                    "rank": index + 1,
                    "proposal_id": item["proposal_id"],
                    "factor_spec_hash": item["factor_spec_hash"],
                    "training_rank_stat": item["s0"],
                    "test_statistics": {
                        "ewc_t": row["ewc_t"],
                        "net_mean": row["net_mean"],
                        "positive_share": row["positive_share"],
                        "n": row["n"],
                    },
                    "descriptive_only": row["ewc_t"] is None,
                }
            )
        selection = run["selections"]["S0"]
        runs.append(
            {
                "arm": run["arm"],
                "origin": run["origin"],
                "window_cut": run["window_cut"],
                "selection": None
                if selection["pick"] is None
                else {
                    "proposal_id": selection["pick"]["proposal_id"],
                    "factor_spec_hash": selection["pick"]["factor_spec_hash"],
                    "rank_stat": selection["training_value"],
                },
                "test_statistics_top5": top5,
                "counts": {"EVALUATED": run["pool"]["n_eligible"]},
            }
        )
    return {"campaign": "campaign4-m1-walkforward", "runs": runs}
