#!/usr/bin/env python
"""C4M0-13 -- run the direction audit + common exposure diagnostic (IS only).

Authority: the frozen
``worker_tasks/campaign4_m0/task-c4m0-13-direction-exposure.md`` on top of the
M0 plan ``worker_tasks/campaign4_m0/campaign4-m0-plan.md`` section 2.  The
script assembles the real Campaign-3 production dependencies (read-only),
composes the persisted Campaign-3 candidates (generation + executor state +
screen cache) with the five frozen section 1.1 reference drivers, applies the
planner's blind rationale coding, and writes the canonical
``m0_13_direction_exposure.json`` + ``.md`` into a caller-supplied output
directory **outside the repository data tiers**.

Firewall.  IS only: the coding-file SHA-256 is verified before any data access
and a ``Campaign3FirewallError`` aborts before any panel is built.  No OOS /
DEVELOPMENT_SEALED / reserve path is opened, no diagnostics artifact is
persisted, no K-PIT record is appended and no network call is made (the only
generator used is the offline
:class:`~smart_beta.campaign.campaign3_launch.FixedArtifactGenerator`, and it
is never invoked).

The generator identity is the C4M0-2 identity
(:func:`run_m0_known_factors.build_generator`), reused verbatim as the frozen
task spec requires.

Usage::

    planner_tools/scrubrun.sh scripts/run_m0_direction_exposure.py \\
        --dataset-root <Dataset v1.2 root> \\
        --knowledge-log <K-PIT path> \\
        --run-root <Campaign-3 runs dir> \\
        --generation <generation.json> \\
        --executor-state <executor_state.json> \\
        --screen-cache-dir <screen_cache dir> \\
        --coding <rationale coding json> \\
        --coding-sha256 <sha256 of the coding file> \\
        --output-dir <outside-repo output dir>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

# Allow ``python scripts/run_m0_direction_exposure.py`` from the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
for _path in (_REPO_ROOT, _SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from smart_beta.campaign import campaign3_launch as _launch  # noqa: E402
from smart_beta.diagnostics import direction_exposure as _de  # noqa: E402

# The frozen C4M0-2 identity path, reused verbatim (task spec "Content").
from run_m0_known_factors import build_generator  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="C4M0-13 direction audit + common exposure (IS only)."
    )
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--knowledge-log", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument(
        "--output-dir",
        required=True,
        help="output directory outside the repository data tiers",
    )
    parser.add_argument(
        "--generation",
        required=True,
        help="the persisted Campaign-3 generation.json (99 candidates)",
    )
    parser.add_argument(
        "--executor-state",
        required=True,
        help="the persisted Campaign-3 executor_state.json (proposals)",
    )
    parser.add_argument(
        "--screen-cache-dir",
        required=True,
        help="the Campaign-3 screen_cache holding <digest>.export.json",
    )
    parser.add_argument(
        "--coding",
        required=True,
        help="the planner's blind rationale-coding JSON",
    )
    parser.add_argument(
        "--coding-sha256",
        required=True,
        help="the SHA-256 of the raw coding file (verified first)",
    )
    parser.add_argument(
        "--bound-alpha",
        type=float,
        default=_de.DEFAULT_BOUND_ALPHA,
        help="the one-sided EWC bound alpha (descriptive; default 0.05)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generator = build_generator()
    assembly = _launch.assemble_production(
        args.dataset_root,
        args.knowledge_log,
        args.run_root,
        generator,
    )
    report = _de.run_direction_exposure(
        assembly,
        generation_path=args.generation,
        executor_state_path=args.executor_state,
        screen_cache_dir=args.screen_cache_dir,
        coding_path=args.coding,
        coding_sha256=args.coding_sha256,
        output_dir=args.output_dir,
        bound_alpha=args.bound_alpha,
    )
    json_path = Path(args.output_dir) / _de.REPORT_JSON_NAME
    print(f"wrote {json_path} (content_sha256={report['content_sha256']})")
    print(f"M0-1 reading: {report['m0_1']['reading']}")
    print(f"M0-3 reading: {report['m0_3']['reading']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
