"""C2-LX production runner — Campaign-2 overlay, bindings, read-only preflight.

Authority: ``worker_tasks/campaign2/task-c2-lx-executor.md`` (C2-LX) governed by
``docs/campaign2_precommitment.md`` and
``docs/campaign2_registration_transaction_plan.md``, mirroring the C1-LX-E5
runner (E5) **mutatis mutandis**.

This module is the *production wiring* layer for Campaign-2 and the P-Q v2.1
pipeline. It reuses the unchanged Campaign-1 runner overlay loader and the
frozen executor in :mod:`smart_beta.campaign.campaign2`; it copies none of
their evaluation logic and modifies none of them.

Scope firewall: this module never evaluates a factor, never appends to the
Knowledge-PIT log and never reads a label or return value. ``preflight_campaign2``
is strictly read-only: it creates no directory and writes no file. Running the
Campaign (``run_campaign2`` or the ``run`` CLI) is Campaign-2 execution and is
**not authorized** by C2-LX; the entry point therefore fails closed.

The Campaign-2 output lives under ``data_store/campaign2/<campaign2_id>/``.
Campaign-1's directory is never touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from smart_beta.campaign import campaign1 as _c1
from smart_beta.campaign import campaign2 as _c2
from smart_beta.dataset_v1 import delisting_bridge as _d7b
from smart_beta.dataset_v1 import pqv2_core as _core
from smart_beta.dataset_v1 import quarantine as _pqa
from smart_beta.campaign.campaign2 import (
    C2BindingError,
    Campaign2Bindings,
    build_partition,
    campaign2_id,
    estimand_policy,
    hypothesis_identities,
    verify_kpit_ordering,
)

__all__ = [
    "DATASET_ROOT",
    "KNOWLEDGE_LOG",
    "OUTPUT_ROOT",
    "OUTPUT_DIRNAME",
    "production_bindings",
    "preflight_campaign2",
    "campaign_output_dir",
    "run_campaign2",
    "main",
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA_STORE = _REPO_ROOT.parent / "data_store"

DATASET_ROOT = _DATA_STORE / "discovery_dataset_v1"
KNOWLEDGE_LOG = _DATA_STORE / "knowledge_pit" / "knowledge.jsonl"

#: Campaign-2's output directory name; Campaign-1's is never written.
OUTPUT_DIRNAME = "campaign2"
OUTPUT_ROOT = _DATA_STORE / OUTPUT_DIRNAME


def _sha256_file(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def production_bindings(
    dataset_root: Path | str = DATASET_ROOT,
    *,
    knowledge_log_path: Path | str | None = KNOWLEDGE_LOG,
) -> Campaign2Bindings:
    """Derive and assert every Campaign-2 authority binding (E5.2 analogue).

    Metadata only: this reads the dataset identity manifest, the overlay
    manifest (via ``quarantine.verify_overlay``), the D7c manifest bytes and
    the D7a binding. It reads no parquet content and no label or return value.
    """
    root = Path(dataset_root)

    if _core.CURRENT_AUTHORITY is not _core.AUTHORITY_V2_1:
        raise C2BindingError("CURRENT_AUTHORITY is not the P-Q v2.1 authority")

    dataset_id = _pqa.DATASET_ID
    try:
        dataset_identity_hash = _pqa.verify_dataset_v1_2_identity(
            root, knowledge_log_path=knowledge_log_path
        )
    except Exception as exc:  # noqa: BLE001 - re-typed, fail closed
        raise C2BindingError(
            f"dataset identity verification failed: {exc}"
        ) from exc

    try:
        manifest = _pqa.verify_overlay(root)
    except Exception as exc:  # noqa: BLE001 - re-typed, fail closed
        raise C2BindingError(f"overlay verification failed: {exc}") from exc
    pq_id = manifest.get("pq_id")
    pq_hash = manifest.get("pq_hash")

    ti_path = root / _c1.TI_MANIFEST_RELATIVE
    try:
        ti_bytes = ti_path.read_bytes()
        ti_payload = json.loads(ti_bytes)
        ti_id = ti_payload["ti_id"]
        ti_hash = ti_payload["ti_hash"]
    except Exception as exc:  # noqa: BLE001 - re-typed, fail closed
        raise C2BindingError(f"D7c manifest unreadable: {exc}") from exc

    d7a = _d7b.DEFAULT_BINDINGS.d7a
    partition_id = build_partition().partition_id

    return Campaign2Bindings(
        dataset_id=dataset_id,
        dataset_identity_hash=dataset_identity_hash,
        pq_id=str(pq_id),
        pq_hash=str(pq_hash),
        ti_id=str(ti_id),
        ti_hash=str(ti_hash),
        ti_manifest_sha256=hashlib.sha256(ti_bytes).hexdigest(),
        d7a_artifact_id=d7a.artifact_id,
        d7a_returns_hash=d7a.returns_hash,
        partition_id=partition_id,
    )


def campaign_output_dir(
    campaign_id: str, output_root: Path | str = OUTPUT_ROOT
) -> Path:
    """``<output_root>/<campaign2_id>/`` (C2-LX section 1.8)."""
    return Path(output_root) / campaign_id


def preflight_campaign2(
    dataset_root: Path | str = DATASET_ROOT,
    *,
    knowledge_log_path: Path | str | None = KNOWLEDGE_LOG,
    output_root: Path | str = OUTPUT_ROOT,
) -> dict[str, Any]:
    """The read-only Campaign-2 preflight.

    Performs the authority bindings and the C2-LX K-PIT ordering check,
    computes ``campaign2_id``, refuses an output directory that already holds
    a ledger, and returns hashes and identities only. It creates no directory,
    writes no file, evaluates no factor and appends no K-PIT record.
    """
    bindings = production_bindings(
        dataset_root, knowledge_log_path=knowledge_log_path
    )

    identities = hypothesis_identities(dataset_root)
    kpit = verify_kpit_ordering(
        knowledge_log_path,
        policy=estimand_policy(),
        identities=identities,
    )
    if not kpit.ok:
        raise C2BindingError(kpit.reason or "K-PIT preflight failed")

    campaign_id = campaign2_id(bindings)
    ledger_path = campaign_output_dir(campaign_id, output_root) / "ledger.jsonl"
    if ledger_path.exists():
        raise C2BindingError(f"a ledger already exists at {ledger_path}")

    return {
        "campaign2_id": campaign_id,
        "dataset_id": bindings.dataset_id,
        "dataset_identity_hash": bindings.dataset_identity_hash,
        "pq_id": bindings.pq_id,
        "pq_hash": bindings.pq_hash,
        "ti_id": bindings.ti_id,
        "ti_hash": bindings.ti_hash,
        "ti_manifest_sha256": bindings.ti_manifest_sha256,
        "d7a_artifact_id": bindings.d7a_artifact_id,
        "d7a_returns_hash": bindings.d7a_returns_hash,
        "partition_id": bindings.partition_id,
        "pq_identities": {
            "methodology_sha256": _c2.PQ_METHODOLOGY_SHA256,
            "cs_id": _c2.PQ_CS_ID,
            "ed_id": _c2.PQ_ED_ID,
            "ce_id": _c2.PQ_CE_ID,
        },
        "screen_id": _c2.SCREEN_ID,
        "hypothesis_ids": {
            str(item["label"]): str(item["hypothesis_id"]) for item in identities
        },
        "kpit_preflight_record_hashes": list(kpit.record_hashes),
    }


def run_campaign2(
    dataset_root: Path | str = DATASET_ROOT,
    *,
    knowledge_log_path: Path | str | None = KNOWLEDGE_LOG,
    output_root: Path | str = OUTPUT_ROOT,
) -> dict[str, Any]:
    """Campaign-2 execution — **not authorized** by C2-LX.

    The production wiring is intentionally fail-closed: only the read-only
    preflight (and the merged executor, exercised synthetically) are
    authorized. A future, separately authorized execution task replaces this
    body.
    """
    raise C2BindingError(
        "Campaign-2 execution is not authorized by C2-LX; run "
        "preflight_campaign2 only"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python -m smart_beta.campaign.runner2 preflight|run``."""
    parser = argparse.ArgumentParser(
        description="Campaign-2 production runner (read-only preflight)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="read-only binding preflight")
    sub.add_parser("run", help="run the Campaign-2 development ledger")
    args = parser.parse_args(argv)
    if args.command == "preflight":
        payload = preflight_campaign2()
    else:
        payload = run_campaign2()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())
