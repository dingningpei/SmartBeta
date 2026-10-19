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

#: Per-process child state.  With the ``fork`` start method the children
#: inherit the parent's builder *factory* (a closure over the assembly wiring)
#: and build their own assembly lazily, so no handle is shared across processes.
_CHILD_STATE: dict[str, Any] = {"factory": None, "builder": None}


def _warm_child(task: tuple[str, Mapping[str, Any]]) -> str:
    """Evaluate one cache miss in a worker and write its cache file atomically."""

    cache_dir, spec_payload = task
    if _CHILD_STATE.get("builder") is None:
        factory = _CHILD_STATE.get("factory")
        if factory is None:  # pragma: no cover - only without the fork method
            raise SS.SelectionStudyError(
                "the cache worker has no evaluator factory; --jobs > 1 needs "
                "the fork start method"
            )
        _CHILD_STATE["builder"] = factory()
    cache = SS.SeriesCache(Path(cache_dir))
    spec = FactorSpec.from_dict(dict(spec_payload))
    return cache.get(spec, _CHILD_STATE["builder"]).factor_spec_hash


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
    builder_factory: Callable[[], Callable[[Any], Any]],
    jobs: int = 1,
) -> dict[str, int]:
    """Evaluate every cache miss once; ``jobs > 1`` uses a forked process pool.

    The result (the cache contents) does not depend on ``jobs``: each worker
    owns a disjoint set of specs and writes its own cache file atomically, and
    the study body is serial and reads the cache only.
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
    }
    if not pending:
        return metrics

    workers = int(jobs)
    if workers <= 1:
        builder = builder_factory()
        for spec in pending:
            cache.get(spec, builder)
        metrics["evaluated"] = len(pending)
        return metrics

    try:
        context = multiprocessing.get_context("fork")
    except ValueError as exc:  # pragma: no cover - platform dependent
        raise SS.SelectionStudyError(
            "--jobs > 1 requires the fork start method; use --jobs 1"
        ) from exc
    _CHILD_STATE["factory"] = builder_factory
    _CHILD_STATE["builder"] = None
    tasks = [(str(cache.directory), spec.to_dict()) for spec in pending]
    # ``fork``: the children inherit the builder *factory* (never a built
    # assembly), build their own read-only assembly on their first task and
    # write their own cache files, so no dataset handle is shared.
    with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
        done = list(pool.map(_warm_child, tasks, chunksize=1))
    metrics["evaluated"] = len(done)
    metrics["workers"] = workers
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

        def builder_factory() -> Callable[[Any], Any]:
            assembly = _launch.assemble_production(
                args.dataset_root,
                args.knowledge_log,
                assembly_run_root,
                build_identity_generator(),
            )
            assert_is_fold(assembly.is_fold)
            return _build_evaluator(assembly)

        metrics = warm_series_cache(
            cache=cache,
            specs=specs,
            builder_factory=builder_factory,
            jobs=args.jobs,
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
    )
    report, exit_code = SS.run_selection_study(deps=deps)
    print(
        f"wrote {output_dir / 'c4_selection_study_report.json'} "
        f"(content_sha256={report['content_sha256']})"
    )
    print(f"reproduction: {report['reproduction']['status']}")
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
