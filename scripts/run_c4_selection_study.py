#!/usr/bin/env python
"""C4M1-3 -- run the Campaign-4 M1-s selection-rule study (IS only, no LLM).

Authority: ``worker_tasks/campaign4_m1/campaign4-m1-plan.md`` (binding
constraints), ``worker_tasks/campaign4_m1/task-c4m1-3-selection-study.md``
section 2 and the frozen ``docs/campaign4_m1s_selection_study_draft.md``
sections 1--4.

The study makes **no** proposals and **no** model calls.  It reads the four
M1-e ``executor_state.json`` pools read-only, re-evaluates every eligible
candidate once on the full IS fold through the **imported** production
evaluator (:func:`run_c4_walkforward._build_evaluator`, v2.5, ``d = -0.55``),
caches each candidate's per-period ``L-TD`` series, applies the four
pre-registered rules ``S0``--``S3`` (writing and hashing each selection file
before any test-window value is read), and writes the canonical
``c4_selection_study_report.json`` + ``.md``.

Cache warm-up is the only step that evaluates; ``--jobs N`` runs it in ``N``
``spawn``-ed workers (never ``fork``), each of which builds its own read-only
assembly through :func:`build_production_evaluator` and writes its own cache
files atomically, so the resulting cache and report never depend on ``--jobs``.

Reproduction.  ``--m1e-reports`` supplies the report whose ``counts["EVALUATED"]``
is the frozen hard check (draft section 1).  Review R3: the count is read up
front and compared against the pool **after** the VALID training-rank-statistic
filter and **before** any selection file is written; a mismatch writes the
report only (marked ``REPRODUCTION_FAILED``) and exits non-zero.

Firewall.  IS only: nothing opens an OOS, DEVELOPMENT_SEALED, HOLDOUT or
reserve path.  Every rule value is computed from formation dates strictly
before the window cut.  The Knowledge-PIT is only read (the assembly binds it);
this script never appends to it, and it refuses to write into the M1-e run
root, into ``data_store`` or into the repository.

Usage::

    planner_tools/scrubrun.sh scripts/run_c4_selection_study.py \\
        --dataset-root <Dataset v1.2 root> \\
        --knowledge-log <K-PIT copy path> \\
        --m1e-run-root <M1-e run root, read-only> \\
        --series-cache <cache dir outside the repo> \\
        --output-dir <report dir outside the repo> \\
        [--runs treatment_W1,treatment_W2,control_W1,control_W2] \\
        [--jobs 1] \\
        [--m1e-reports <dir> ...]
"""

from __future__ import annotations

import argparse
import importlib
import multiprocessing
import sys
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

# Allow ``python scripts/run_c4_selection_study.py`` from the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
for _path in (_REPO_ROOT, _SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from smart_beta.campaign import campaign3_launch as _launch  # noqa: E402
from smart_beta.campaign.campaign3_eval import assert_is_fold  # noqa: E402
from smart_beta.campaign4 import selection_study as SS  # noqa: E402
from smart_beta.campaign4.walkforward import ORIGINS  # noqa: E402
from smart_beta.spec.factor_spec import FactorSpec, factor_spec_hash  # noqa: E402

#: The production assembly + evaluator wiring, imported (never copied) from the
#: frozen C4M1-2 script (task spec section 2).
from run_c4_walkforward import _build_evaluator, build_identity_generator  # noqa: E402

#: The four M1-e runs, in the frozen report order.
DEFAULT_RUNS = "treatment_W1,treatment_W2,control_W1,control_W2"


# ---------------------------------------------------------------------------
# cache warm-up (the only step that evaluates; --jobs parallelises it)
# ---------------------------------------------------------------------------


def build_production_evaluator(
    dataset_root: str | Path,
    knowledge_log: str | Path,
    assembly_run_root: str | Path,
) -> Callable[[Any], Any]:
    """The read-only production assembly + full-IS evaluator of one worker.

    Reuses the frozen C4M1-2 wiring **by import** -- ``assemble_production``
    plus ``run_c4_walkforward._build_evaluator`` / ``build_identity_generator``
    -- exactly as the C3 production launcher does.  Nothing is copied.  The
    Knowledge-PIT is only bound (never appended to) and the IS fold is asserted
    before any panel is built.
    """

    assembly = _launch.assemble_production(
        dataset_root,
        knowledge_log,
        assembly_run_root,
        build_identity_generator(),
    )
    assert_is_fold(assembly.is_fold)
    return _build_evaluator(assembly)


def _resolve_builder(
    builder_ref: str | None,
    dataset_root: str | Path | None,
    knowledge_log: str | Path | None,
    assembly_run_root: str | Path | None,
) -> Callable[[Any], Any]:
    """The evaluator of one worker: the production path or a named factory.

    ``builder_ref`` (``"module:attribute"``) is the offline-test hook: the named
    module-level factory is called with ``(dataset_root, knowledge_log,
    assembly_run_root)`` and must return a ``spec -> CandidateEvaluation``
    builder.  It is resolved by name so it survives pickling into a spawned
    worker; the production path needs no factory at all.
    """

    if builder_ref:
        module_name, _, attribute = str(builder_ref).partition(":")
        if not module_name or not attribute:
            raise SS.SelectionStudyError(
                f"builder_ref must be 'module:attribute', got {builder_ref!r}"
            )
        factory = getattr(importlib.import_module(module_name), attribute)
        return factory(dataset_root, knowledge_log, assembly_run_root)
    if dataset_root is None or knowledge_log is None or assembly_run_root is None:
        raise SS.SelectionStudyError(
            "--jobs > 1 needs the dataset root, the K-PIT copy and the assembly "
            "run root (or an explicit builder_ref)"
        )
    return build_production_evaluator(
        dataset_root, knowledge_log, assembly_run_root
    )


#: The per-worker state, filled by :func:`worker_init` in each spawned child.
#: ``spawn`` starts a clean interpreter, so nothing (no assembly, no dataset
#: handle, no lock) is inherited from this process.
_WORKER: dict[str, Any] = {"cache": None, "builder": None}


def worker_init(
    dataset_root: str | None,
    knowledge_log: str | None,
    assembly_run_root: str | None,
    cache_dir: str,
    builder_ref: str | None,
) -> None:
    """Module-level spawn initializer: one cache handle + one evaluator.

    The arguments are plain picklable values only, so a spawned worker builds
    its own read-only assembly (through :func:`build_production_evaluator`) and
    writes its own cache files.
    """

    _WORKER["cache"] = SS.SeriesCache(Path(cache_dir))
    _WORKER["builder"] = _resolve_builder(
        builder_ref, dataset_root, knowledge_log, assembly_run_root
    )


def warm_one_spec(spec_payload: Mapping[str, Any]) -> str:
    """Evaluate one cache miss in a spawned worker (atomic cache write)."""

    cache = _WORKER["cache"]
    builder = _WORKER["builder"]
    if cache is None or builder is None:  # pragma: no cover - init guarantees it
        raise SS.SelectionStudyError(
            "the cache worker was not initialised; --jobs > 1 needs a worker "
            "initializer"
        )
    spec = FactorSpec.from_dict(dict(spec_payload))
    return cache.get(spec, builder).factor_spec_hash


def _unique_specs(pools: Sequence[SS.RunPool]) -> list[Any]:
    seen: set[str] = set()
    specs: list[Any] = []
    for pool in pools:
        for candidate in pool.proposals:
            if candidate.factor_spec_hash in seen:
                continue
            seen.add(candidate.factor_spec_hash)
            specs.append(candidate.factor_spec)
    return specs


def warm_series_cache(
    *,
    cache: SS.SeriesCache,
    specs: Sequence[Any],
    jobs: int = 1,
    builder: Callable[[Any], Any] | None = None,
    dataset_root: str | Path | None = None,
    knowledge_log: str | Path | None = None,
    assembly_run_root: str | Path | None = None,
    builder_ref: str | None = None,
) -> dict[str, int]:
    """Evaluate every cache miss once; ``jobs > 1`` uses spawned workers.

    The result (the cache contents) never depends on ``jobs``: each worker owns
    a disjoint set of specs and writes its own cache file atomically, and the
    study body is serial and reads the cache only.  ``jobs > 1`` uses the
    ``spawn`` start method (never ``fork``): every worker is a clean
    interpreter that builds its own assembly from plain arguments
    (:func:`worker_init`), so a multi-hour run cannot deadlock on a forked
    lock.  ``builder`` (jobs 1 only) and ``builder_ref`` are the offline-test
    hooks.
    """

    pending: list[Any] = []
    seen: set[str] = set()
    for spec in specs:
        key = factor_spec_hash(spec)
        if key in seen:
            continue
        seen.add(key)
        if not cache.has(key):
            pending.append(spec)
    metrics = {
        "requested": len(seen),
        "already_cached": len(seen) - len(pending),
        "evaluated": 0,
        "workers": 1,
        "start_method": "inline",
    }
    if not pending:
        return metrics

    workers = int(jobs)
    if workers <= 1:
        evaluator = (
            builder
            if builder is not None
            else _resolve_builder(
                builder_ref, dataset_root, knowledge_log, assembly_run_root
            )
        )
        for spec in pending:
            cache.get(spec, evaluator)
        metrics["evaluated"] = len(pending)
        return metrics

    initargs = (
        None if dataset_root is None else str(dataset_root),
        None if knowledge_log is None else str(knowledge_log),
        None if assembly_run_root is None else str(assembly_run_root),
        str(cache.directory),
        builder_ref,
    )
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=context,
        initializer=worker_init,
        initargs=initargs,
    ) as pool:
        done = list(
            pool.map(warm_one_spec, [spec.to_dict() for spec in pending], chunksize=1)
        )
    metrics["evaluated"] = len(done)
    metrics["workers"] = workers
    metrics["start_method"] = context.get_start_method()
    return metrics


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Campaign-4 M1-s selection-rule study (IS only, no LLM)."
    )
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument(
        "--knowledge-log", required=True, help="the K-PIT copy (read-only)"
    )
    parser.add_argument(
        "--m1e-run-root", required=True, help="the M1-e run root (read-only)"
    )
    parser.add_argument("--series-cache", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--runs",
        default=DEFAULT_RUNS,
        help=f"comma-separated M1-e runs (default: {DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="parallel cache-miss evaluations (default 1)",
    )
    parser.add_argument(
        "--m1e-reports",
        nargs="+",
        default=(),
        help="M1-e report directories/files for the S0 reproduction check",
    )
    parser.add_argument(
        "--assembly-run-root",
        default=None,
        help=(
            "scratch run root for the read-only production assembly (default: "
            "<output-dir>/_assembly_run_root); only used when the cache is cold"
        ),
    )
    return parser


def _run_names(raw: str) -> list[str]:
    names = [item.strip() for item in str(raw).split(",") if item.strip()]
    if not names:
        raise SS.SelectionStudyError("--runs must name at least one M1-e run")
    for name in names:
        arm, _, origin = name.rpartition("_")
        if origin not in ORIGINS:
            raise SS.SelectionStudyError(
                f"--runs entry {name!r} does not end in a frozen origin "
                f"{sorted(ORIGINS)}"
            )
    return names


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Refusals first: no dataset path is touched before these pass (task spec
    # section 2, "Refusals").
    output_dir = SS.assert_outside_repository(args.output_dir)
    cache_dir = SS.assert_outside_repository(args.series_cache)
    m1e_root = Path(args.m1e_run_root).expanduser().resolve()
    SS.assert_not_inside(output_dir, m1e_root, what="the output directory")
    SS.assert_not_inside(cache_dir, m1e_root, what="the series cache")

    run_names = _run_names(args.runs)

    # The pools come from the persisted states only: no dataset access here.
    pools = [SS.load_run_pool(m1e_root / name) for name in run_names]
    specs = _unique_specs(pools)
    cache = SS.SeriesCache(cache_dir)

    if any(not cache.has(factor_spec_hash(spec)) for spec in specs):
        assembly_run_root = SS.assert_outside_repository(
            args.assembly_run_root or (output_dir / "_assembly_run_root")
        )
        SS.assert_not_inside(
            assembly_run_root, m1e_root, what="the assembly run root"
        )

        metrics = warm_series_cache(
            cache=cache,
            specs=specs,
            jobs=args.jobs,
            dataset_root=args.dataset_root,
            knowledge_log=args.knowledge_log,
            assembly_run_root=assembly_run_root,
        )
        print(
            "cache warm-up: "
            f"{metrics['evaluated']} evaluated, "
            f"{metrics['already_cached']} already cached, "
            f"{metrics['workers']} worker(s), {metrics['requested']} specs"
        )

    deps = SS.SelectionStudyDeps(
        m1e_run_root=m1e_root,
        cache=cache,
        output_dir=output_dir,
        run_names=run_names,
        m1e_reports=tuple(Path(item) for item in args.m1e_reports),
        # Review R3: the frozen hard check is each M1-e report's own
        # counts["EVALUATED"], compared against the VALID-filtered pool before
        # any selection file is written.
        expected_evaluated=(
            SS.expected_counts_from_reports(
                [Path(item) for item in args.m1e_reports], run_names=run_names
            )
            if args.m1e_reports
            else None
        ),
    )
    report, exit_code = SS.run_selection_study(deps=deps)
    print(
        f"wrote {output_dir / 'c4_selection_study_report.json'} "
        f"(content_sha256={report['content_sha256']})"
    )
    print(
        f"reproduction: {report['reproduction']['status']}"
        + (
            f" ({report['reproduction']['reason']})"
            if report["reproduction"].get("reason")
            else ""
        )
    )
    print(
        "flags: promising="
        + ", ".join(
            f"{rule}={flag}"
            for rule, flag in sorted(report["flags"]["promising"].items())
        )
        + f"; pool_limited={report['flags']['pool_limited']}"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
