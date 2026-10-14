#!/usr/bin/env python
"""C4M0-4 -- run the TS terminal-stress decomposition (IS only).

Authority: the frozen ``worker_tasks/campaign4_m0/task-c4m0-4-ts-decomposition.md``.
The script assembles the real Campaign-3 production dependencies (read-only),
runs the six M0-2 known factors plus the Campaign-3 TB_SELECTION candidates
(rebuilt from their persisted provenance, in the supplied IS-rank order) on the
IS fold through the frozen T-A evaluation and D7b reconstruction, and writes
the canonical ``ts_decomposition.json`` + ``ts_decomposition.md`` into a
caller-supplied output directory **outside the repository data tiers**.

Firewall.  IS only: a ``Campaign3FirewallError`` aborts before any panel is
built.  No OOS / DEVELOPMENT_SEALED / reserve path is opened, no diagnostics
artifact is persisted, no K-PIT record is appended and no network call is made
(the only generator used is the offline
:class:`~smart_beta.campaign.campaign3_launch.FixedArtifactGenerator`, and it
is never invoked).

Usage::

    planner_tools/scrubrun.sh scripts/run_m0_ts_decomposition.py \\
        --dataset-root <Dataset v1.2 root> \\
        --knowledge-log <K-PIT path> \\
        --run-root <Campaign-3 runs dir> \\
        --output-dir <outside-repo output dir> \\
        [--c3-digest <diagnostics_hash> --diagnostics-dir <dir>]...
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Sequence

# Allow ``python scripts/run_m0_ts_decomposition.py`` from the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from smart_beta.campaign import campaign3_launch as _launch  # noqa: E402
from smart_beta.diagnostics import known_factors as _known  # noqa: E402
from smart_beta.diagnostics import ts_decomposition as _ts  # noqa: E402

#: A diagnostic-only identity hash (not a generator prompt).  The offline
#: :class:`FixedArtifactGenerator` is never invoked by this diagnostic; its
#: hash only feeds the unused policy/attempt identity, which the frozen
#: ``campaign3_generation.build_campaign3_policy`` requires to be a 64-char
#: lowercase hex SHA-256.  The literal is hashed at import so that the value
#: is a genuine content address of the diagnostic identity.
_DIAGNOSTIC_PROMPT_TEMPLATE_HASH = hashlib.sha256(
    b"c4m0-ts-decomposition-diagnostic"
).hexdigest()


def build_generator() -> _launch.FixedArtifactGenerator:
    """The offline generator the diagnostic binds (never invoked).

    The frozen control artifact and the diagnostic-only identity hash are the
    exact inputs ``assemble_production`` forwards to
    ``build_production_policy``; keeping the construction here lets the policy
    path be exercised without a real dataset or a network call.
    """

    return _launch.FixedArtifactGenerator(
        artifact=_launch.control_raw_artifact(),
        prompt_template_hash=_DIAGNOSTIC_PROMPT_TEMPLATE_HASH,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="C4M0-4 TS terminal-stress decomposition (IS only)."
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
    report = _ts.run_ts_decomposition(
        assembly,
        output_dir=args.output_dir,
        factors=factors,
    )
    json_path = Path(args.output_dir) / _ts.REPORT_JSON_NAME
    print(f"wrote {json_path} (content_sha256={report.content_sha256})")
    reading = _ts.classify_reading(report)
    print(f"reading: {reading.label}")
    for factor in report.factors:
        if not factor.assessed:
            print(f"{factor.factor_id}: NOT_ASSESSED ({factor.not_assessed_reason})")
            continue
        print(
            f"{factor.factor_id}: n={factor.n_formations} "
            f"mean_gap={factor.mean_gap_gross!r} "
            f"mean_pred={factor.mean_pred!r} excess={factor.excess_exposure!r}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
