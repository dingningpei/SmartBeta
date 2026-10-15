#!/usr/bin/env python
"""P-Q v2.5 -- run the real-data TD equivalence barrier (IS only).

Authority: the frozen ``worker_tasks/pq_v2_5/task-pq25-x-equivalence.md`` on
top of the binding constraints in ``worker_tasks/pq_v2_5/pqv25-plan.md``.

The script assembles the real Campaign-3 production dependencies (read-only),
runs the six M0-2 known factors plus the Campaign-3 TB_SELECTION candidates
(rebuilt from their persisted provenance, in the supplied IS-rank order) on the
IS fold through the frozen P-Q v2 evaluation and the PQ25-H TD wrapper, and
writes the canonical ``pqv25_equivalence.json`` + ``pqv25_equivalence.md`` into
a caller-supplied output directory **outside the repository data tiers**.

Firewall.  IS only: a ``Campaign3FirewallError`` aborts before any panel is
built.  No OOS / DEVELOPMENT_SEALED / reserve path is opened, no diagnostics
artifact is persisted, no K-PIT record is appended and no network call is made.
The only generator used is the offline
:class:`~smart_beta.campaign.campaign3_launch.FixedArtifactGenerator` from the
C4M0-2 diagnostic identity, and it is never invoked.

Exit code: ``0`` on PASS, ``3`` on FAIL.

Usage::

    planner_tools/scrubrun.sh scripts/run_pqv25_equivalence.py \\
        --dataset-root <Dataset v1.2 root> \\
        --knowledge-log <K-PIT path> \\
        --run-root <Campaign-3 runs dir> \\
        --output-dir <outside-repo output dir> \\
        --diagnostics-dir <C3 diagnostics dir> \\
        --c3-digest <diagnostics_hash> ... (x5, IS-rank order)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

# Allow ``python scripts/run_pqv25_equivalence.py`` from the repository root,
# and reuse the C4M0-2 diagnostic-only generator identity verbatim.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = Path(__file__).resolve().parent
for _path in (str(_REPO_ROOT), str(_SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from run_m0_known_factors import build_generator  # noqa: E402

from smart_beta.campaign import campaign3_launch as _launch  # noqa: E402
from smart_beta.dataset_v1.pqv25 import equivalence as _equivalence  # noqa: E402
from smart_beta.diagnostics import known_factors as _known  # noqa: E402
from smart_beta.diagnostics import ts_decomposition as _ts  # noqa: E402

#: The frozen exit code for a FAIL verdict (task spec §2).
EXIT_FAIL = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P-Q v2.5 TD equivalence barrier (IS only)."
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
    parser.add_argument(
        "--c3-provenance",
        action="append",
        default=None,
        help="a persisted <digest>.provenance.json for one C3 candidate "
        "(repeatable, in IS-rank order)",
    )
    parser.add_argument(
        "--diagnostics-dir",
        default=None,
        help="the C3 diagnostics dir holding <digest>.provenance.json",
    )
    parser.add_argument(
        "--c3-digest",
        action="append",
        default=None,
        help="a C3 candidate diagnostics_hash; resolved under "
        "--diagnostics-dir (repeatable, in IS-rank order)",
    )
    return parser


def _c3_provenance_paths(args: argparse.Namespace) -> tuple[Path, ...]:
    paths = [Path(item) for item in (args.c3_provenance or [])]
    digests = list(args.c3_digest or [])
    if digests:
        if not args.diagnostics_dir:
            raise SystemExit("--c3-digest requires --diagnostics-dir")
        paths.extend(
            Path(args.diagnostics_dir)
            / f"{digest}{_launch.TA_PROVENANCE_SUFFIX}"
            for digest in digests
        )
    return tuple(paths)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generator = build_generator()
    assembly = _launch.assemble_production(
        args.dataset_root,
        args.knowledge_log,
        args.run_root,
        generator,
    )
    known = _known.build_known_factors(args.campaign1_factorspecs)
    c3 = _ts.build_c3_candidate_factors(_c3_provenance_paths(args))
    factors = known + c3
    if args.factor:
        wanted = set(args.factor)
        factors = tuple(
            factor for factor in factors if factor.factor_id in wanted
        )
    report = _equivalence.run_equivalence(
        assembly,
        factors=factors,
        output_dir=args.output_dir,
    )
    json_path = Path(args.output_dir) / _equivalence.REPORT_JSON_NAME
    print(f"wrote {json_path} (content_sha256={report.content_sha256})")
    print(f"verdict: {report.verdict}")
    for factor in report.factors:
        print(
            f"{factor.factor_id}: E1={factor.e1} E2={factor.e2} "
            f"E3={factor.e3} E4_violations={factor.e4_violations} "
            f"first_mismatch={factor.first_mismatch}"
        )
    return 0 if report.passed else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
