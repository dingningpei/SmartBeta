#!/usr/bin/env python
"""Pilot 1A runner entry point.

Usage (from the repository root)::

    python scripts/run_pilot1a.py \
        --config pilot_configs/pilot1a-dryrun.json \
        --approved-config-hash <sha256>

The script loads the frozen configuration, runs the governed Pilot-1A loop and
prints the run disposition as JSON. It never reads a data-provider credential,
never adds a provider SDK and (through H6) only ever drives the deterministic
stub model. The run's artifacts are written under the configured
``artifact_destination`` (``pilot_runs/pilot1a/<run_id>/``).

Exit code 0 means the run reached a legal typed STOP; 1 means it was
interrupted or failed preflight. Neither code means scientific acceptance.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow ``python scripts/run_pilot1a.py`` from the repository root without an
# editable install.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from smart_beta.pilot.config import load_config  # noqa: E402
from smart_beta.pilot.contracts import RunStatus  # noqa: E402
from smart_beta.pilot.runner import run_pilot  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_pilot1a",
        description=(
            "Run one governed Pilot-1A operational-validation loop over the "
            "frozen offline configuration."
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        help="path to the frozen Pilot-1A config JSON",
    )
    parser.add_argument(
        "--approved-config-hash",
        required=True,
        help="the user-approved SHA-256 of the frozen config",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="repository root (defaults to the checkout that owns this script)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    repo = None if args.repo is None else Path(args.repo)
    outcome = run_pilot(
        config,
        approved_config_hash=args.approved_config_hash,
        repo=repo,
    )
    print(json.dumps(outcome.to_dict(), indent=2, sort_keys=True))
    return 0 if outcome.status is RunStatus.COMPLETED_STOP else 1


if __name__ == "__main__":
    raise SystemExit(main())
