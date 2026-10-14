#!/usr/bin/env python
"""C4M0-2 -- run the known-factor replication IS diagnostic.

Authority: the frozen ``worker_tasks/campaign4_m0/campaign4-m0-plan.md``
section 3.  The script assembles the real Campaign-3 production dependencies
(read-only), runs the six frozen known factors on the IS fold through the
frozen T-A evaluation and per-period export, and writes the canonical
``known_factors.json`` + ``known_factors.md`` into a caller-supplied output
directory **outside the repository data tiers**.

Firewall.  IS only: the partition fold is resolved from the frozen Campaign-1/2
partition and must be the single ``IS`` fold; a ``Campaign3FirewallError``
aborts before any panel is built.  No OOS / DEVELOPMENT_SEALED / reserve path
is opened, no diagnostics artifact is persisted, no K-PIT record is appended
and no network call is made (the only generator used is the offline
:class:`~smart_beta.campaign.campaign3_launch.FixedArtifactGenerator`, and it
is never invoked).

Usage::

    planner_tools/scrubrun.sh scripts/run_m0_known_factors.py \\
        --dataset-root <Dataset v1.2 root> \\
        --knowledge-log <K-PIT path> \\
        --run-root <Campaign-3 runs dir> \\
        --output-dir <outside-repo output dir>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

# Allow ``python scripts/run_m0_known_factors.py`` from the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from smart_beta.campaign import campaign3_launch as _launch  # noqa: E402
from smart_beta.diagnostics import known_factors as _known  # noqa: E402

#: The generator is never invoked by this diagnostic; its prompt-template hash
#: only feeds the (unused) policy/attempt identity.
_DIAGNOSTIC_PROMPT_TEMPLATE_HASH = "c4m0-known-factors-diagnostic"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="C4M0-2 known-factor replication (IS only)."
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
        "--campaign1-factorspecs",
        default=None,
        help="override the frozen docs/campaign1_factorspecs.json path",
    )
    parser.add_argument(
        "--factor",
        action="append",
        default=None,
        help="run only the named factor id(s); repeatable; default = all",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generator = _launch.FixedArtifactGenerator(
        artifact=_launch.control_raw_artifact(),
        prompt_template_hash=_DIAGNOSTIC_PROMPT_TEMPLATE_HASH,
    )
    assembly = _launch.assemble_production(
        args.dataset_root,
        args.knowledge_log,
        args.run_root,
        generator,
    )
    factors = _known.build_known_factors(args.campaign1_factorspecs)
    report = _known.run_known_factors(
        assembly,
        output_dir=args.output_dir,
        factors=factors,
        factor_ids=args.factor,
    )
    json_path = Path(args.output_dir) / _known.REPORT_JSON_NAME
    print(f"wrote {json_path} (content_sha256={report.content_sha256})")
    for factor in report.factors:
        cell = factor.cell("B-T0") or factor.cell("B-T0-K0")
        ewc = "n/a" if cell is None or cell.ewc_t is None else f"{cell.ewc_t:.3f}"
        print(f"{factor.factor_id}: class={factor.certified_class} ewc_t(B-T0)={ewc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
