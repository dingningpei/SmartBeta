#!/usr/bin/env python
"""C4M1-2 -- run the Campaign-4 walk-forward harness (IS only).

Authority: ``worker_tasks/campaign4_m1/campaign4-m1-plan.md`` and
``worker_tasks/campaign4_m1/task-c4m1-2-walkforward-harness.md`` section 4 on
top of the frozen ``docs/campaign4_m1a_contract_calibration_draft.md``
decisions Q5--Q7.

The script assembles the real Campaign-3 production dependencies (read-only)
through the frozen C4M0-2 identity path
(:func:`run_m0_known_factors.build_generator` + ``assemble_production``), then
runs the harness with the C4 generation generator (DeepSeek, or a fixed
in-process artifact for the planner's synthetic dry run).  It writes the
canonical ``c4_walkforward_report.json`` + ``.md`` and the per-run selection
files into a caller-supplied output directory **outside the repository data
tiers**.

Firewall.  IS only: the IS fold is asserted before any panel is built; the
executor runs with ``kpit=None``; no K-PIT record is appended.  The real
DeepSeek runs need a separate authorization (they spend money and generate
trial count).

Usage::

    planner_tools/scrubrun.sh scripts/run_c4_walkforward.py \\
        --dataset-root <Dataset v1.2 root> \\
        --knowledge-log <K-PIT copy path> \\
        --run-root <scratch run root> \\
        --output-dir <outside-repo output dir> \\
        [--arms treatment,control] [--origins W1,W2] \\
        [--generator deepseek | --generator fixed:<path>] \\
        [--price-table <json>]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

# Allow ``python scripts/run_c4_walkforward.py`` from the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
for _path in (_REPO_ROOT, _SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from smart_beta.campaign import campaign3_launch as _launch  # noqa: E402
from smart_beta.campaign.campaign3_eval import assert_is_fold  # noqa: E402
from smart_beta.campaign4 import deepseek as _deepseek  # noqa: E402
from smart_beta.campaign4 import walkforward as W  # noqa: E402
from smart_beta.campaign4.generation import (  # noqa: E402
    CandidateEvaluation,
    CONTROL,
    TREATMENT,
)
from smart_beta.campaign4.ranking import assert_v25_admitted  # noqa: E402
from smart_beta.dataset_v1 import pqv2_core as _pqv2_core  # noqa: E402
from smart_beta.dataset_v1.pqv25 import equivalence as _equiv  # noqa: E402
from smart_beta.dataset_v1.pqv25.authority import TD_D  # noqa: E402
from smart_beta.dataset_v1.pqv25.evaluation import (  # noqa: E402
    build_pqv25_diagnostics,
)
from smart_beta.dataset_v1.pqv25.series import export_td_series  # noqa: E402
from smart_beta.diagnostics import direction_exposure as _de  # noqa: E402
from smart_beta.research.llm_generator import GenerationOutput, PriceTable  # noqa: E402
from smart_beta.research.generator import RawArtifact  # noqa: E402
from smart_beta.spec.factor_spec import factor_spec_hash  # noqa: E402

#: The frozen C4M0-2 identity path, reused verbatim (task spec section 4).
from run_m0_known_factors import build_generator as build_identity_generator  # noqa: E402


# ---------------------------------------------------------------------------
# fixed generator (planner dry run)
# ---------------------------------------------------------------------------


class FixedCompleter:
    """A deterministic ``(system, user) -> GenerationOutput`` fixed artifact."""

    def __init__(self, responses: Sequence[str], *, tokens: int = 1000) -> None:
        self._responses = list(responses)
        if not self._responses:
            raise ValueError("a fixed generator needs at least one response")
        self._tokens = int(tokens)
        self.calls = 0

    def __call__(self, system_prompt: str, user_prompt: str) -> GenerationOutput:
        raw = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return GenerationOutput(
            raw_artifact=RawArtifact(content=raw),
            tokens_used=self._tokens,
            cost_used=None,
        )


def _load_fixed_responses(path: Path) -> dict[str, list[str]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("the fixed generator file must be a JSON object")
    out: dict[str, list[str]] = {}
    for arm in (TREATMENT, CONTROL):
        entries = payload.get(arm, [])
        if isinstance(entries, str):
            entries = [entries]
        responses: list[str] = []
        for entry in entries:
            if isinstance(entry, str):
                responses.append(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("raw"), str):
                responses.append(entry["raw"])
            else:
                raise SystemExit(
                    f"fixed generator entry for {arm!r} must be a raw JSON "
                    "string or an object with a 'raw' string"
                )
        out[arm] = responses
    return out


# ---------------------------------------------------------------------------
# production evaluation / assembly wiring
# ---------------------------------------------------------------------------


def _is_formations(assembly: Any) -> tuple[Any, ...]:
    """The IS formation dates, read from one frozen control-factor panel."""

    spec = _launch.control_factor_spec()
    panel = assembly.fast_panel_for(spec, assembly.is_fold)
    dates = sorted({_as_date(value) for value in panel["date"].tolist()})
    return tuple(dates)


def _as_date(value: Any):
    from datetime import date, datetime

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return value


def _build_evaluator(assembly: Any) -> Callable[[Any], CandidateEvaluation]:
    """The production full-IS evaluator, bound exactly as the admitted v2.5 path.

    This reuses the frozen :mod:`smart_beta.dataset_v1.pqv25.equivalence`
    private helpers by import -- ``_evaluation_kwargs`` (which pins
    ``fold = pqv2_core.DEVELOPMENT_FOLD``; the IS scope comes from the
    declaration's ``fold_key``), ``_event_layer_for`` (an event layer only for
    event factors) and ``_export_bindings``.  The C4 evaluation is therefore
    byte-for-byte the admitted PQ25-X evaluation, and the kwargs are never
    re-derived here.
    """

    def evaluate(spec: Any) -> CandidateEvaluation:
        # Erratum (plan constraint 4): the v2.5 admission is asserted before
        # any TD build.
        assert_v25_admitted()
        selected_fold = assembly.is_fold
        panel = assembly.fast_panel_for(spec, selected_fold)
        declaration = assembly.declaration_builder(spec, panel, selected_fold)
        event_layer = _equiv._event_layer_for(assembly, spec)
        evaluation_kwargs = _equiv._evaluation_kwargs(
            assembly, event_layer=event_layer
        )
        root = assembly.dataset_root
        partition = assembly.partition
        diagnostics = build_pqv25_diagnostics(
            root,
            declaration,
            spec,
            panel,
            partition,
            d=TD_D,
            **evaluation_kwargs,
        )
        export_kwargs = {
            "overlay": evaluation_kwargs["overlay"],
            "inputs": evaluation_kwargs["inputs"],
            "source_bindings": evaluation_kwargs["source_bindings"],
            "pq_bindings": evaluation_kwargs["pq_bindings"],
            "pq_read_records_fn": evaluation_kwargs["pq_read_records_fn"],
            "sessions": evaluation_kwargs["sessions"],
            "event_layer": event_layer,
            "bindings": _equiv._export_bindings(assembly),
            "fold": _pqv2_core.DEVELOPMENT_FOLD,
        }
        export_fn = getattr(assembly, "export_series_fn", None)
        if export_fn is None:
            raise SystemExit(
                "the production assembly binds no export_series_fn; the C4 "
                "evaluator cannot build the base T0/TS export"
            )
        base_export = export_fn(
            root,
            declaration,
            spec,
            panel,
            partition,
            diagnostics=diagnostics.base,
            **export_kwargs,
        )
        td_export = export_td_series(
            root,
            declaration,
            spec,
            panel,
            partition,
            diagnostics=diagnostics,
            d=TD_D,
            **export_kwargs,
        )
        return CandidateEvaluation(
            factor_spec_hash=factor_spec_hash(spec),
            declaration_hash=declaration.declaration_hash,
            diagnostics_hash=diagnostics.diagnostics_hash,
            diagnostics=diagnostics,
            base_export=base_export,
            td_export=td_export,
            not_assessed=diagnostics.not_assessed is not None,
            not_assessed_reason=(
                None
                if diagnostics.not_assessed is None
                else str(diagnostics.not_assessed)
            ),
        )

    return evaluate


def _driver_panels(assembly: Any) -> dict[str, Any]:
    panels: dict[str, Any] = {}
    for driver in _de.build_driver_specs():
        if driver.certified_class is None:
            continue
        panels[driver.driver_id] = assembly.fast_panel_for(
            driver.spec, assembly.is_fold
        )
    return panels


def _make_llm_factory(
    *, generator: str, fixed_path: Path | None, price_table: PriceTable | None
) -> Callable[[str], Callable[[str, str], GenerationOutput]]:
    if generator == "deepseek":
        completer = _deepseek.build_deepseek_completer(price_table=price_table)

        def make_llm(arm: str) -> Callable[[str, str], GenerationOutput]:
            return completer

        return make_llm

    if generator.startswith("fixed:"):
        fixed_path = fixed_path or Path(generator.split(":", 1)[1])
    if fixed_path is None:
        raise SystemExit("--generator fixed:<path> requires a path")
    responses = _load_fixed_responses(fixed_path)
    completers = {
        arm: FixedCompleter(responses.get(arm, [])) for arm in (TREATMENT, CONTROL)
    }

    def make_llm(arm: str) -> Callable[[str, str], GenerationOutput]:
        return completers[arm]

    return make_llm


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Campaign-4 M1 walk-forward harness (IS only)."
    )
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--knowledge-log", required=True, help="the K-PIT copy")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--arms",
        default="treatment,control",
        help="comma-separated arms (default: treatment,control)",
    )
    parser.add_argument(
        "--origins",
        default="W1,W2",
        help="comma-separated origins (default: W1,W2)",
    )
    parser.add_argument(
        "--generator",
        default="deepseek",
        help="'deepseek' or 'fixed:<path>' (an offline fixed artifact)",
    )
    parser.add_argument(
        "--price-table",
        default=None,
        help="optional JSON {'input_per_mtok','output_per_mtok'}",
    )
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--max-proposal-budget", type=int, default=100)
    parser.add_argument("--max-llm-token-budget", type=int, default=4_000_000)
    parser.add_argument("--max-llm-cost-budget", type=float, default=20.0)
    return parser


def _assert_not_in_data_store(path: Path | str) -> None:
    resolved = Path(path).resolve()
    for parent in (resolved, *resolved.parents):
        if parent.name == "data_store":
            raise SystemExit(
                f"{resolved} is inside the repository data_store; C4 outputs "
                "and run roots must live outside it"
            )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _assert_not_in_data_store(args.run_root)
    _assert_not_in_data_store(args.output_dir)

    price_table = None
    if args.price_table:
        payload = json.loads(Path(args.price_table).read_text(encoding="utf-8"))
        price_table = PriceTable(
            input_per_mtok=payload["input_per_mtok"],
            output_per_mtok=payload["output_per_mtok"],
        )

    generator_kind = args.generator
    fixed_path = None
    if generator_kind.startswith("fixed:") and generator_kind != "fixed:":
        fixed_path = Path(generator_kind.split(":", 1)[1])
        generator_kind = "fixed"

    identity_generator = build_identity_generator()
    assembly = _launch.assemble_production(
        args.dataset_root,
        args.knowledge_log,
        args.run_root,
        identity_generator,
    )
    assert_is_fold(assembly.is_fold)

    arms = tuple(item for item in args.arms.split(",") if item)
    origins = tuple(item for item in args.origins.split(",") if item)

    deps = W.WalkforwardDeps(
        is_formations=_is_formations(assembly),
        evaluate=_build_evaluator(assembly),
        panel_builder=lambda spec: assembly.fast_panel_for(spec, assembly.is_fold),
        make_llm=_make_llm_factory(
            generator=generator_kind,
            fixed_path=fixed_path,
            price_table=price_table,
        ),
        driver_panels=_driver_panels(assembly),
        price_table=price_table,
    )
    report = W.run_walkforward(
        deps=deps,
        run_root=args.run_root,
        output_dir=args.output_dir,
        arms=arms,
        origins=origins,
        seed=args.seed,
        max_proposal_budget=args.max_proposal_budget,
        max_llm_token_budget=args.max_llm_token_budget,
        max_llm_cost_budget=args.max_llm_cost_budget,
    )
    json_path = Path(args.output_dir) / "c4_walkforward_report.json"
    print(f"wrote {json_path} (content_sha256={report['content_sha256']})")
    print(f"trial accounting total: {report['trial_accounting']['total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
