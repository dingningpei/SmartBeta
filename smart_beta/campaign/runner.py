"""C1-LX-E5 — Campaign-1 production runner (overlay loader, bindings,
expected realizations, read-only preflight and the operator entry point).

Authority: ``worker_tasks/campaign1/task-c1-ledger-executor-erratum-e5.md``
(the E5 erratum) governed by ``docs/campaign1_identity_architecture_correction.md``
and ``docs/campaign1_execution_readiness_evidence.md``, on top of the C1-LX
task and errata E1-E4.

This module is the *production* wiring layer. It reuses the frozen Campaign
code in :mod:`smart_beta.campaign.campaign1` (the executor, identities and K-PIT
preflight) and the Dataset-v1.2 P-Q code in
:mod:`smart_beta.dataset_v1.quarantine` and
:mod:`smart_beta.dataset_v1.pq_evaluation`; it copies none of their logic and
modifies none of them.

Scope firewall (E5): this module never evaluates a factor, never builds a PQB
or D7b artefact, never reads a label or a return value and never appends to the
Knowledge-PIT log. ``preflight_campaign1`` is strictly read-only: it creates no
directory and writes no file. Running the Campaign (``run_campaign1`` or the
``run`` CLI) is Campaign-1 execution and is **not authorized** by E5; it is
implemented here for a future, separately authorized execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from smart_beta.campaign import campaign1 as _c1
from smart_beta.campaign.campaign1 import (
    C1BindingError,
    C1GovernanceConflict,
    CampaignBindings,
    CampaignExecutor,
    build_partition,
    campaign1_id,
    estimand_policy,
    factor_spec_hash,
    hypothesis_identities,
    render_report,
    verify_kpit_ordering,
)
from smart_beta.campaign.ledger import CampaignLedger
from smart_beta.dataset_v1 import pq_evaluation as _pqb
from smart_beta.dataset_v1 import quarantine as _pqa

__all__ = [
    "DATASET_ROOT",
    "KNOWLEDGE_LOG",
    "OUTPUT_ROOT",
    "EVIDENCE_PATH",
    "EXPECTED_DATASET_ID",
    "EXPECTED_DATASET_IDENTITY_HASH",
    "EXPECTED_AMENDMENT_SHA256",
    "EXPECTED_ADMISSION_SHA256",
    "EXPECTED_PQ_ID",
    "EXPECTED_PQ_HASH",
    "EXPECTED_QUARANTINE_RULE_HASH",
    "EXPECTED_PQ_REPORTING_SHA256",
    "EXPECTED_PQ_EVALUATION_SHA256",
    "EXPECTED_PARTITION_ID",
    "EXPECTED_EVIDENCE_SHA256",
    "EXPECTED_FACTOR_REALIZATIONS",
    "load_campaign_overlay",
    "production_bindings",
    "expected_realizations_from_evidence",
    "preflight_campaign1",
    "run_campaign1",
    "main",
]

# ---------------------------------------------------------------------------
# Fixed paths and frozen literals (E5.5, E5.2, E5.3)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: E5.5 fixed defaults. ``<repo>/../data_store`` resolves to the repository's
#: shared data store when the runner is executed from the primary checkout.
_DATA_STORE = _REPO_ROOT.parent / "data_store"
DATASET_ROOT = _DATA_STORE / "discovery_dataset_v1"
KNOWLEDGE_LOG = _DATA_STORE / "knowledge_pit" / "knowledge.jsonl"
OUTPUT_ROOT = _DATA_STORE / "campaign1"

EVIDENCE_PATH = (
    _REPO_ROOT / "docs" / "campaign1_execution_readiness_evidence.md"
)
ADMISSION_PATH = (
    _REPO_ROOT / "docs" / "discovery_dataset_v1_2_development_admission.md"
)
AMENDMENT_PATH = _REPO_ROOT / "docs" / "discovery_dataset_v1_2_pq_amendment.md"
RULES_PATH = (
    _REPO_ROOT / "docs" / "discovery_dataset_v1_2_pq_quarantine_rules.md"
)
PQ_REPORTING_PATH = (
    _REPO_ROOT / "smart_beta" / "dataset_v1" / "pq_reporting.py"
)
PQ_EVALUATION_PATH = (
    _REPO_ROOT / "smart_beta" / "dataset_v1" / "pq_evaluation.py"
)

#: E5.2 frozen values.
EXPECTED_DATASET_ID = "discovery_dataset_v1_2"
EXPECTED_DATASET_IDENTITY_HASH = (
    "a065493d295883f425d499816a98ac55a03cd67a1086bd7b64249b90fe76b9da"
)
EXPECTED_AMENDMENT_SHA256 = (
    "9261a55b9b82a5708dded65bfd3083d5a960cfa4375681b1b23390da62fb3ed0"
)
EXPECTED_ADMISSION_SHA256 = (
    "8c720c29938497aca98716dc317b77d0b68ea8126db33b35459fdf90132129ee"
)
EXPECTED_PQ_ID = "pq-59c3bd7abe08eef5"
EXPECTED_PQ_HASH = (
    "baa920ddfc52805c70445abfb8650e2969d341ae9383e1b1a0c6a788fcc95f2c"
)
EXPECTED_QUARANTINE_RULE_HASH = (
    "df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886"
)
EXPECTED_PQ_REPORTING_SHA256 = (
    "8a892999a4b887b92a212a53571f1e28cbec63ca976741f13175b75a432341dd"
)
EXPECTED_PQ_EVALUATION_SHA256 = (
    "322570ec810516c6b16493c6a060fe26d1479078477f14b4e05d6b8ea0b8aca3"
)
EXPECTED_PARTITION_ID = (
    "b4b56dbfc82377930b8bf87a1d8039a69ccbf7d42d5faf234d3bb8cbbcb936fb"
)

#: E5.3: the SHA-256 of ``docs/campaign1_execution_readiness_evidence.md``.
EXPECTED_EVIDENCE_SHA256 = (
    "70b920bfc7346794bb82d0a757d32491d72eaf961d1be7d99b47d7026769ccd4"
)

#: E5.3: the 7 agreed factor realizations, cross-checked against the parsed
#: evidence document. The evidence parse is not trusted on its own, and this
#: constant is not trusted on its own: equality of the two is required.
EXPECTED_FACTOR_REALIZATIONS: dict[str, str] = {
    "C1-H1": (
        "c1dfedb7e77758b11cda2547f20ed3c012d3ffc48d26d0ca627322ff0041c714"
    ),
    "C1-H2": (
        "e8d94efb31e5b4063869aa2e1de5eedd5d244fe5812eb9abb5aa2b0106c8243f"
    ),
    "C1-H3": (
        "72d9f8538bcc8fa9631dfc4c0f4a13301189fd2cf60d4e5960ebcda0fe9f52cf"
    ),
    "C1-H4": (
        "5f490ac4df78c213b3e12c3433c1fcbf740563c3b3ed52fbda5b318a83146c25"
    ),
    "C1-H5": (
        "201fed58d42c2e9c247c914cad1abc8c6fc39644ebc66e811a2dd923807c49e9"
    ),
    "C1-H6": (
        "93cf20645ca8389e0be21bd24000a5f3d1328166f84e9741299f9a0b4ea2d280"
    ),
    "C1-H7": (
        "529fdf7b2b8ca69200b8d64a3123de565ca1906c157eaaf8d722f9b79bd7657d"
    ),
}

_OVERLAY_PARQUET = (
    "flags.parquet",
    "primitive_states.parquet",
    "input_states.parquet",
    "label_states.parquet",
    "liquidity_exposure.parquet",
)


def _sha256_file(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _assert_equal(name: str, actual: Any, expected: Any) -> None:
    """Fail closed with :class:`C1BindingError` when a bound value differs."""
    if actual != expected:
        raise C1BindingError(f"{name} {actual!r} != frozen {expected!r}")


# ---------------------------------------------------------------------------
# E5.1 Overlay loader (H-1)
# ---------------------------------------------------------------------------


def load_campaign_overlay(dataset_root: Path | str = DATASET_ROOT) -> _pqa.Overlay:
    """Load and fully bind the admitted Dataset-v1.2 P-Q overlay (E5.1).

    The existing :func:`quarantine.verify_overlay` re-hashes every overlay file
    against its manifest; the returned manifest is then asserted against the
    four frozen identities. Loading the parquet files and ``dataset_identity.json``
    into a :class:`quarantine.Overlay` is metadata-only: it reads quarantine
    *states* and never a label or return value. Any failure raises
    :class:`C1BindingError` (a pre-open Stage-A failure under E3).
    """
    root = Path(dataset_root)
    try:
        manifest = _pqa.verify_overlay(root)
    except Exception as exc:  # noqa: BLE001 - re-typed, fail closed
        raise C1BindingError(f"overlay verification failed: {exc}") from exc
    if not isinstance(manifest, Mapping):
        raise C1BindingError("overlay manifest is not a mapping")

    pq_id = manifest.get("pq_id")
    pq_hash = manifest.get("pq_hash")
    identity_hash = manifest.get("identity_hash")
    rule_hash = manifest.get("quarantine_rule_hash")
    _assert_equal("pq_id", pq_id, EXPECTED_PQ_ID)
    _assert_equal("pq_hash", pq_hash, EXPECTED_PQ_HASH)
    _assert_equal("identity_hash", identity_hash, EXPECTED_DATASET_IDENTITY_HASH)
    _assert_equal(
        "quarantine_rule_hash", rule_hash, EXPECTED_QUARANTINE_RULE_HASH
    )

    overlay_dir = _pqa._overlay_directory(root, _pqa.DEFAULT_BINDINGS)
    try:
        frames = {
            name: pd.read_parquet(overlay_dir / name) for name in _OVERLAY_PARQUET
        }
        identity = json.loads(
            (overlay_dir / "dataset_identity.json").read_text(encoding="utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - re-typed, fail closed
        raise C1BindingError(f"overlay contents unreadable: {exc}") from exc

    return _pqa.Overlay(
        flags=frames["flags.parquet"],
        primitive_states=frames["primitive_states.parquet"],
        input_states=frames["input_states.parquet"],
        label_states=frames["label_states.parquet"],
        liquidity_exposure=frames["liquidity_exposure.parquet"],
        identity=identity,
        pq_id=pq_id,
        pq_hash=pq_hash,
    )


# ---------------------------------------------------------------------------
# E5.2 Production bindings (H-2)
# ---------------------------------------------------------------------------


def production_bindings(
    dataset_root: Path | str = DATASET_ROOT,
    *,
    knowledge_log_path: Path | str | None = KNOWLEDGE_LOG,
) -> CampaignBindings:
    """Derive and assert every frozen Campaign binding (E5.2).

    Each value comes from its authoritative source and is asserted against the
    frozen value; the first mismatch raises :class:`C1BindingError`.
    """
    root = Path(dataset_root)

    dataset_id = _pqb.DATASET_ID
    _assert_equal("dataset_id", dataset_id, EXPECTED_DATASET_ID)

    try:
        dataset_identity_hash = _pqa.verify_dataset_v1_2_identity(
            root, knowledge_log_path=knowledge_log_path
        )
    except Exception as exc:  # noqa: BLE001 - re-typed, fail closed
        raise C1BindingError(
            f"dataset identity verification failed: {exc}"
        ) from exc
    _assert_equal(
        "dataset_identity_hash",
        dataset_identity_hash,
        EXPECTED_DATASET_IDENTITY_HASH,
    )

    _assert_equal(
        "quarantine.AMENDMENT_SHA256",
        _pqa.AMENDMENT_SHA256,
        EXPECTED_AMENDMENT_SHA256,
    )
    _assert_equal(
        "amendment document",
        _sha256_file(AMENDMENT_PATH),
        EXPECTED_AMENDMENT_SHA256,
    )
    _assert_equal(
        "admission document",
        _sha256_file(ADMISSION_PATH),
        EXPECTED_ADMISSION_SHA256,
    )

    try:
        manifest = _pqa.verify_overlay(root)
    except Exception as exc:  # noqa: BLE001 - re-typed, fail closed
        raise C1BindingError(f"overlay verification failed: {exc}") from exc
    pq_id = manifest.get("pq_id")
    pq_hash = manifest.get("pq_hash")
    _assert_equal("pq_id", pq_id, EXPECTED_PQ_ID)
    _assert_equal("pq_hash", pq_hash, EXPECTED_PQ_HASH)

    _assert_equal(
        "quarantine.QUARANTINE_RULE_HASH",
        _pqa.QUARANTINE_RULE_HASH,
        EXPECTED_QUARANTINE_RULE_HASH,
    )
    _assert_equal(
        "quarantine rules document",
        _sha256_file(RULES_PATH),
        EXPECTED_QUARANTINE_RULE_HASH,
    )

    # R3 is the dataset identity; the two must never be decoupled.
    r3_identity_hash = dataset_identity_hash
    _assert_equal("r3_identity_hash", r3_identity_hash, dataset_identity_hash)

    _assert_equal(
        "pq_reporting_sha256",
        _sha256_file(PQ_REPORTING_PATH),
        EXPECTED_PQ_REPORTING_SHA256,
    )
    _assert_equal(
        "pq_evaluation_sha256",
        _sha256_file(PQ_EVALUATION_PATH),
        EXPECTED_PQ_EVALUATION_SHA256,
    )

    partition_id = build_partition().partition_id
    _assert_equal("partition_id", partition_id, EXPECTED_PARTITION_ID)

    return CampaignBindings(
        dataset_id=dataset_id,
        dataset_identity_hash=dataset_identity_hash,
        amendment_sha256=EXPECTED_AMENDMENT_SHA256,
        admission_sha256=EXPECTED_ADMISSION_SHA256,
        pq_id=pq_id,
        pq_hash=pq_hash,
        quarantine_rule_hash=EXPECTED_QUARANTINE_RULE_HASH,
        r3_identity_hash=r3_identity_hash,
        pq_reporting_sha256=EXPECTED_PQ_REPORTING_SHA256,
        pq_evaluation_sha256=EXPECTED_PQ_EVALUATION_SHA256,
        partition_id=partition_id,
    )


# ---------------------------------------------------------------------------
# E5.3 Expected realizations from the execution-readiness evidence (H-2)
# ---------------------------------------------------------------------------

_EVIDENCE_ROW = re.compile(r"^C1-H[1-7]$")


def _parse_evidence_realizations(text: str) -> list[list[str]]:
    """Parse the seven results-table rows from the evidence markdown."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [
            cell.strip().strip("`").strip()
            for cell in stripped.strip("|").split("|")
        ]
        if cells and _EVIDENCE_ROW.match(cells[0]):
            rows.append(cells)
    return rows


def expected_realizations_from_evidence(
    path: Path | str = EVIDENCE_PATH,
) -> dict[str, str]:
    """Parse and cross-check the 7 agreed factor realizations (E5.3).

    The evidence document is SHA-256 pinned; the parsed rows must carry the
    frozen labels in order, the frozen ``hypothesis_id``s, the frozen
    ``factor_spec_hash``es, and a realization map equal to
    :data:`EXPECTED_FACTOR_REALIZATIONS`. Neither the document nor the constant
    is the sole source.
    """
    evidence_path = Path(path)
    _assert_equal(
        "evidence sha256",
        _sha256_file(evidence_path),
        EXPECTED_EVIDENCE_SHA256,
    )
    try:
        text = evidence_path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensive
        raise C1BindingError(f"evidence unreadable: {exc}") from exc

    rows = _parse_evidence_realizations(text)
    labels = list(_c1.EXPECTED_LABELS)
    if len(rows) != len(labels):
        raise C1BindingError(
            f"evidence carries {len(rows)} rows, expected {len(labels)}"
        )
    parsed: dict[str, str] = {}
    for index, cells in enumerate(rows):
        if len(cells) < 4:
            raise C1BindingError(
                f"evidence row {index} has fewer than four columns"
            )
        label, hypothesis_id, spec_hash, realization = cells[:4]
        if label != labels[index]:
            raise C1BindingError(
                f"evidence row {index} label {label!r} != {labels[index]!r}"
            )
        expected_id = _c1.EXPECTED_HYPOTHESIS_IDS.get(label)
        if hypothesis_id != expected_id:
            raise C1BindingError(
                f"evidence {label} hypothesis_id {hypothesis_id} != {expected_id}"
            )
        expected_spec = factor_spec_hash(_c1.FACTOR_SPECS[index])
        if spec_hash != expected_spec:
            raise C1BindingError(
                f"evidence {label} factor_spec_hash {spec_hash} != {expected_spec}"
            )
        parsed[label] = realization

    if parsed != EXPECTED_FACTOR_REALIZATIONS:
        raise C1BindingError(
            "parsed evidence realizations != EXPECTED_FACTOR_REALIZATIONS"
        )
    return parsed


# ---------------------------------------------------------------------------
# E5.5 Preflight and entry point
# ---------------------------------------------------------------------------


def _assert_output_outside_git(output_root: Path) -> Path:
    """Refuse an output directory that resolves inside the Git working tree."""
    resolved = Path(output_root).resolve()
    repo = _REPO_ROOT.resolve()
    if resolved == repo or resolved.is_relative_to(repo):
        raise C1BindingError(
            f"output directory {resolved} must resolve outside the Git "
            f"working tree {repo}"
        )
    return resolved


def preflight_campaign1(
    dataset_root: Path | str = DATASET_ROOT,
    *,
    knowledge_log_path: Path | str | None = KNOWLEDGE_LOG,
    output_root: Path | str = OUTPUT_ROOT,
    evidence_path: Path | str = EVIDENCE_PATH,
) -> dict[str, Any]:
    """The read-only Campaign-1 preflight (E5.5).

    Performs E5.1/E5.2/E5.3 and the C1-LX K-PIT ordering check, computes
    ``campaign1_id``, refuses an output directory inside the Git working tree
    and refuses a directory that already holds a ledger. It creates no
    directory, writes no file, opens no ledger, evaluates no factor, builds no
    PQB/D7b artefact and reads no label or return value.
    """
    _assert_output_outside_git(output_root)

    bindings = production_bindings(
        dataset_root, knowledge_log_path=knowledge_log_path
    )
    load_campaign_overlay(dataset_root)
    expected = expected_realizations_from_evidence(evidence_path)

    identities = hypothesis_identities(dataset_root)
    kpit = verify_kpit_ordering(
        knowledge_log_path,
        policy=estimand_policy(),
        identities=identities,
    )
    if not kpit.ok:
        raise C1GovernanceConflict(kpit.reason or "K-PIT preflight failed")

    campaign_id = campaign1_id(bindings)
    ledger_path = Path(output_root) / campaign_id / "ledger.jsonl"
    if ledger_path.exists():
        raise C1BindingError(f"a ledger already exists at {ledger_path}")

    return {
        "campaign1_id": campaign_id,
        "dataset_id": bindings.dataset_id,
        "dataset_identity_hash": bindings.dataset_identity_hash,
        "amendment_sha256": bindings.amendment_sha256,
        "admission_sha256": bindings.admission_sha256,
        "pq_id": bindings.pq_id,
        "pq_hash": bindings.pq_hash,
        "quarantine_rule_hash": bindings.quarantine_rule_hash,
        "r3_identity_hash": bindings.r3_identity_hash,
        "pq_reporting_sha256": bindings.pq_reporting_sha256,
        "pq_evaluation_sha256": bindings.pq_evaluation_sha256,
        "partition_id": bindings.partition_id,
        "expected_factor_realizations": dict(sorted(expected.items())),
        "hypothesis_ids": {
            str(item["label"]): str(item["hypothesis_id"]) for item in identities
        },
        "kpit_preflight_record_hashes": list(kpit.record_hashes),
    }


def run_campaign1(
    dataset_root: Path | str = DATASET_ROOT,
    *,
    knowledge_log_path: Path | str | None = KNOWLEDGE_LOG,
    output_root: Path | str = OUTPUT_ROOT,
    evidence_path: Path | str = EVIDENCE_PATH,
) -> dict[str, Any]:
    """Run the Campaign-1 development ledger (E5.5).

    Campaign-1 execution is **not authorized** by E5: this function is wired
    for a future, separately authorized execution and must never be invoked
    against the real data store in this task.
    """
    state = preflight_campaign1(
        dataset_root,
        knowledge_log_path=knowledge_log_path,
        output_root=output_root,
        evidence_path=evidence_path,
    )
    campaign_id = state["campaign1_id"]
    campaign_dir = Path(output_root) / campaign_id
    campaign_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = campaign_dir / "ledger.jsonl"

    bindings = production_bindings(
        dataset_root, knowledge_log_path=knowledge_log_path
    )
    overlay = load_campaign_overlay(dataset_root)
    expected = expected_realizations_from_evidence(evidence_path)

    executor = CampaignExecutor(
        ledger=CampaignLedger(ledger_path),
        bindings=bindings,
        partition=build_partition(),
        factor_specs=_c1.FACTOR_SPECS,
        overlay=overlay,
        root=dataset_root,
        expected_realizations=expected,
        knowledge_log_path=knowledge_log_path,
        require_kpit=True,
    )
    result = executor.run()

    report_json, report_markdown = render_report(ledger_path)
    (campaign_dir / "report.json").write_bytes(report_json)
    (campaign_dir / "report.md").write_bytes(report_markdown)

    return {
        "campaign1_id": campaign_id,
        "stop_reason": result.stop_reason.value,
        "counters": dict(result.counters),
        "ledger_path": str(ledger_path),
        "report_json_path": str(campaign_dir / "report.json"),
        "report_markdown_path": str(campaign_dir / "report.md"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python -m smart_beta.campaign.runner preflight|run``."""
    parser = argparse.ArgumentParser(
        description="Campaign-1 production runner (read-only preflight or run)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="read-only binding preflight")
    sub.add_parser("run", help="run the Campaign-1 development ledger")
    args = parser.parse_args(argv)
    if args.command == "preflight":
        payload = preflight_campaign1()
    else:
        payload = run_campaign1()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())
