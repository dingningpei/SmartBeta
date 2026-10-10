"""C3-R — Campaign-3 P6 run driver (bindings, precommitment, resume, stages).

Authority: the frozen ``docs/campaign3_p6_implementation_spec.md`` (task row
C3-R; section 2 mutations R-M1..R-M3 plus R-M4), implementing the frozen
``docs/campaign3_p5_precommitment.md`` (+ erratum P5-E1) and
``docs/campaign3_p4_amendment_a2_deepseek.md`` (+ A2-E1).

This module is the ``run()`` entry point the P6 spec calls for: it wires the
already-merged C3-X generation executor, the C3-E T-A evaluation bridge, the
C3-S selection tier and the P-Q v2.3 / v1.3 data identities into one
write-ahead, resumable run:

* **Bindings (a).** :class:`Campaign3Bindings` carries the Dataset v1.2
  identity, the P-Q overlay ``pq-59c3bd7abe08eef5``, the D7c trusted-input
  identity ``ti-aa23fabd3db4e595`` and the v1.3 EDGAR event layer
  (acquisition ``f0d0ee1b…`` / build ``ff5e83ec…``).  Every value is verified
  against a frozen literal (:func:`verify_bindings`); a mismatch is a
  fail-closed :class:`C3BindingError`, never a warning.
* **Evaluator construction (b).** :func:`build_ta_evaluator` builds the C3-X
  :class:`~smart_beta.campaign.campaign3_eval.TaEvaluator` for the IS fold,
  and :func:`build_tb_evaluator` builds the write-ahead T-B evaluator for the
  OOS + DEVELOPMENT_SEALED folds.  Both use the frozen literals ``TOP_1000``,
  ``h = 21``, ``5`` groups, ``10`` bps, ``T0 + TS`` and ``AUTHORITY_V2_3``.
* **Precommitment (c/d).** :func:`verify_precommitment` re-hashes the frozen
  P5 document (including erratum E1); :func:`verify_kpit_terminal` pins the
  K-PIT terminal seq/hash at launch and refuses a moved terminal.  The
  :data:`CAMPAIGN_RECORD_TYPE` HUMAN decision and the HUMAN-channel control
  ``HYPOTHESIS_FREEZE`` are both written **before** the first generator call,
  and the CAMPAIGN record carries the P5 hash and the SHA-256 of every frozen
  code file.
* **Write-ahead run directory (e/f).** Each stage is atomically persisted
  under ``data_store/campaign3/<attempt_id>/``; a completed stage is never
  recomputed, so a resume cannot double count a candidate or a generator call.

**Testing.**  Every test is offline and synthetic: a stub generator, a stub
evaluator, a disposable :class:`~smart_beta.science.knowledge.KnowledgeLog`
and a temporary run directory.  No production artifact, ``data_store``,
reserve, holdout or network path is read, and no real generator client is
constructed.

**C3-S / PQS.**  ``campaign3_screen`` (C3-S + C3-S-R1) and ``pqv2_series``
(PQS) are merged; this module imports them by their public names behind
:func:`load_screen_runner` / :func:`_load_export_series`.  There is **no
implicit stub**: :func:`load_screen_runner` raises :class:`C3ConfigurationError`
when ``campaign3_screen`` (or its ``run_screen``) is absent, ``run()`` resolves
the real ``run_screen`` unless a runner is injected explicitly, and a
``STUBBED_*`` report is refused unless the test-only ``allow_stub_screen=True``
flag is set explicitly.  The T-A IS diagnostics and their ``pqv2_series``
export are not in the JSON generation snapshot, so the caller injects
``screen_candidate_builder`` (a missing builder fails closed).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smart_beta.campaign import campaign3_eval as _eval
from smart_beta.campaign import campaign3_generation as _gen
from smart_beta.research.policy import ResearchPolicy
from smart_beta.science.contracts import content_hash
from smart_beta.science.knowledge import (
    Channel,
    KnowledgeLog,
    KnowledgeRecord,
    KnowledgeSnapshot,
    RecordKind,
    read_records,
    snapshot_of_records,
)

__all__ = [
    # paths / literals
    "REPO_ROOT",
    "PRECOMMITMENT_PATH",
    "A2_PATH",
    "P6_PATH",
    "RUN_ROOT",
    "KNOWLEDGE_LOG",
    "PRECOMMITMENT_SHA256",
    "A2_SHA256",
    "P6_SHA256",
    "EXPECTED_DATASET_ID",
    "EXPECTED_DATASET_IDENTITY_HASH",
    "EXPECTED_PQ_ID",
    "EXPECTED_PQ_HASH",
    "EXPECTED_TI_ID",
    "EXPECTED_TI_HASH",
    "EXPECTED_ACQ_ID",
    "EXPECTED_EV_ID",
    "EXPECTED_PARTITION_ID",
    "PQ_PINNED_FILES",
    "CAMPAIGN3_CODE_FILES",
    "RESERVE_CHAIN_FILES",
    "CONTROL_HYPOTHESIS_ID",
    "CONTROL_HOLD_MONTHS",
    "CONTROL_SIGN",
    "CAMPAIGN_RECORD_TYPE",
    # errors
    "Campaign3RunError",
    "C3BindingError",
    "C3PreflightError",
    "C3GovernanceConflict",
    "C3ConfigurationError",
    # bindings
    "Campaign3Bindings",
    "verify_bindings",
    "production_bindings",
    "load_overlay",
    "load_event_layer",
    # precommitment
    "verify_precommitment",
    "KpitPreflight",
    "pin_kpit_terminal",
    "verify_kpit_terminal",
    "verify_kpit_ordering",
    # control / records
    "ControlHypothesis",
    "CONTROL",
    "control_factor_hash",
    "control_content",
    "campaign3_code_hashes",
    "campaign_precommitment_content",
    "write_campaign_precommitment",
    "write_control_freeze",
    # run directory
    "run_dir_for",
    "RunJournal",
    # declarations / evaluators
    "build_declaration",
    "build_factor_panel",
    "build_ta_evaluator",
    "TbFoldEvaluation",
    "TbEvaluation",
    "TbEvaluator",
    "build_tb_evaluator",
    # screen seam
    "load_screen_runner",
    "load_screen_module",
    "stub_screen_runner",
    "write_tb_selection_record",
    # driver
    "GenerationSnapshot",
    "Campaign3RunResult",
    "Campaign3Run",
    "run_campaign3",
]

# ---------------------------------------------------------------------------
# fixed paths and frozen literals
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA_STORE = REPO_ROOT.parent / "data_store"

PRECOMMITMENT_PATH = REPO_ROOT / "docs" / "campaign3_p5_precommitment.md"
A2_PATH = REPO_ROOT / "docs" / "campaign3_p4_amendment_a2_deepseek.md"
P6_PATH = REPO_ROOT / "docs" / "campaign3_p6_implementation_spec.md"

#: Default run root (tests inject ``tmp_path``; nothing here is opened by
#: importing the module).
RUN_ROOT = _DATA_STORE / "campaign3"
KNOWLEDGE_LOG = _DATA_STORE / "knowledge_pit" / "knowledge.jsonl"

#: SHA-256 of the frozen P5 precommitment **including erratum E1**.
PRECOMMITMENT_SHA256 = (
    "95093b99692a4c52f33b96830dbe2a7064cf1c7df06646e705a765acdb791288"
)
#: SHA-256 of A2 (+A2-E1) and of the frozen P6 implementation spec.
A2_SHA256 = "efc664adaed8f625cbae3dcaafb13cf19f77877a268ee94c526a70a4d4212e71"
P6_SHA256 = "e1e9cc73c06ce65e2c987d664a0ebf8ff14a49ef414de28e338626dc267662e9"

#: Dataset v1.2 / overlay / D7c / v1.3 identities (never invented; taken from
#: ``docs/discovery_dataset_v1_2_*_admission.md``,
#: ``docs/discovery_dataset_v1_3_admission.md`` and ``runner.py``).
EXPECTED_DATASET_ID = "discovery_dataset_v1_2"
EXPECTED_DATASET_IDENTITY_HASH = (
    "a065493d295883f425d499816a98ac55a03cd67a1086bd7b64249b90fe76b9da"
)
EXPECTED_PQ_ID = "pq-59c3bd7abe08eef5"
EXPECTED_PQ_HASH = (
    "baa920ddfc52805c70445abfb8650e2969d341ae9383e1b1a0c6a788fcc95f2c"
)
EXPECTED_TI_ID = "ti-aa23fabd3db4e595"
EXPECTED_TI_HASH = (
    "30ee0c51602cf792e8cb7654c2501f52c7d664675dbf78845c903df6683c2ae6"
)
EXPECTED_ACQ_ID = (
    "f0d0ee1b4f5875b1d8ad970e041ebe73d8278cc283477378af4bb1bf9fa04f54"
)
EXPECTED_EV_ID = (
    "ff5e83ec253f007493bfc7341fd4dcc90606ee932855efe3313686d1afabcca3"
)
EXPECTED_PARTITION_ID = (
    "b4b56dbfc82377930b8bf87a1d8039a69ccbf7d42d5faf234d3bb8cbbcb936fb"
)

#: The pinned P-Q source files whose SHA-256 is recorded in the CAMPAIGN
#: precommitment record (P6 §1(d)).
PQ_PINNED_FILES: tuple[str, ...] = (
    "smart_beta/dataset_v1/pqv2_core.py",
    "smart_beta/dataset_v1/pqv2_evaluation.py",
    "smart_beta/dataset_v1/pqv2_dependency.py",
    "smart_beta/dataset_v1/pqv2_events.py",
    "smart_beta/dataset_v1/pqv2_sources.py",
    "smart_beta/dataset_v1/pqv2_envelope.py",
    "smart_beta/dataset_v1/pqv2_labels.py",
    "smart_beta/dataset_v1/pqv2_assign_simple.py",
    "smart_beta/dataset_v1/pqv2_assign_sandwich.py",
    "smart_beta/dataset_v1/pqv2_series.py",
)

#: The C3-X / C3-D / C3-E / C3-R / C3-S files whose SHA-256 is recorded.
CAMPAIGN3_CODE_FILES: tuple[str, ...] = (
    "smart_beta/campaign/campaign3_generation.py",
    "smart_beta/research/llm_generator_deepseek.py",
    "smart_beta/campaign/campaign3_eval.py",
    "smart_beta/campaign/campaign3_run.py",
    "smart_beta/campaign/campaign3_screen.py",
)

#: The reserve-chain package files whose SHA-256 is recorded.
RESERVE_CHAIN_FILES: tuple[str, ...] = tuple(
    sorted(
        "smart_beta/dataset_v1/reserve_chain/" + name
        for name in (
            "__init__.py",
            "access.py",
            "confirm.py",
            "fold.py",
            "returns_fold.py",
            "trusted_fold.py",
            "universe_fold.py",
        )
    )
)

#: The P4/P5 HUMAN-channel positive control (D11): ``event_ear_k_kp1``,
#: 1-month hold, sign ``+1``.
CONTROL_HYPOTHESIS_ID = "event_ear_k_kp1"
CONTROL_HOLD_MONTHS = 1
CONTROL_SIGN = 1

#: The K-PIT ``HUMAN_DECISION`` payload key naming the CAMPAIGN record.
CAMPAIGN_RECORD_TYPE = "CAMPAIGN_PRECOMMITMENT"
CAMPAIGN_RECORD_SCHEMA = "campaign3-p6-precommitment-v1"
CONTROL_RECORD_SCHEMA = "campaign3-p6-control-freeze-v1"

# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class Campaign3RunError(Exception):
    """Base class for every C3-R fail-closed error."""


class C3BindingError(Campaign3RunError):
    """A Campaign-3 binding, identity or authority does not match."""


class C3PreflightError(Campaign3RunError):
    """The K-PIT preflight or the pinned terminal check failed."""


class C3GovernanceConflict(Campaign3RunError):
    """The K-PIT ordering or an already-written record conflicts."""


class C3ConfigurationError(Campaign3RunError):
    """The run driver was configured outside the frozen P6 contract."""


# ---------------------------------------------------------------------------
# bindings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Campaign3Bindings:
    """The read-only authority bindings hashed into the attempt identity."""

    dataset_id: str
    dataset_identity_hash: str
    pq_id: str
    pq_hash: str
    ti_id: str
    ti_hash: str
    acq_id: str
    ev_id: str
    partition_id: str

    def to_content(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_identity_hash": self.dataset_identity_hash,
            "pq_id": self.pq_id,
            "pq_hash": self.pq_hash,
            "ti_id": self.ti_id,
            "ti_hash": self.ti_hash,
            "acq_id": self.acq_id,
            "ev_id": self.ev_id,
            "partition_id": self.partition_id,
        }

    @property
    def content_hash(self) -> str:
        return content_hash(self.to_content())


def _assert_equal(name: str, actual: Any, expected: Any) -> None:
    """Fail closed with :class:`C3BindingError` when a bound value differs."""
    if actual != expected:
        raise C3BindingError(f"{name} {actual!r} != frozen {expected!r}")


def verify_bindings(bindings: Campaign3Bindings) -> Campaign3Bindings:
    """Verify every frozen identity literal; fail closed on any mismatch."""
    if not isinstance(bindings, Campaign3Bindings):
        raise C3BindingError("bindings must be a Campaign3Bindings")
    _assert_equal("dataset_id", bindings.dataset_id, EXPECTED_DATASET_ID)
    _assert_equal(
        "dataset_identity_hash",
        bindings.dataset_identity_hash,
        EXPECTED_DATASET_IDENTITY_HASH,
    )
    _assert_equal("pq_id", bindings.pq_id, EXPECTED_PQ_ID)
    _assert_equal("pq_hash", bindings.pq_hash, EXPECTED_PQ_HASH)
    _assert_equal("ti_id", bindings.ti_id, EXPECTED_TI_ID)
    _assert_equal("ti_hash", bindings.ti_hash, EXPECTED_TI_HASH)
    _assert_equal("acq_id", bindings.acq_id, EXPECTED_ACQ_ID)
    _assert_equal("ev_id", bindings.ev_id, EXPECTED_EV_ID)
    _assert_equal("partition_id", bindings.partition_id, EXPECTED_PARTITION_ID)
    return bindings


def _sha256_file(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_overlay(dataset_root: Path | str | None = None) -> Any:
    """Read-only Dataset v1.2 overlay loader (delegates to ``runner``).

    The Campaign-2 ``load_campaign_overlay`` pattern: it re-hashes every
    overlay file against its manifest and asserts the frozen pq/identity
    literals.  Not called by the offline tests.
    """
    from smart_beta.campaign import runner as _runner

    if dataset_root is None:
        return _runner.load_campaign_overlay()
    return _runner.load_campaign_overlay(dataset_root)


def load_event_layer(
    root: Path | str,
    *,
    acq_id: str,
    ev_id: str,
    fold: Any = None,
) -> Any:
    """Read-only v1.3 EDGAR event-layer loader (delegates to ``pqv2_events``).

    Under the default (development) fold it refuses a ``reserve_sealed`` path;
    the run passes the T-A/T-B development folds only.  Not called by the
    offline tests.
    """
    from smart_beta.dataset_v1 import pqv2_events as _events

    kwargs: dict[str, Any] = {}
    if fold is not None:
        kwargs["fold"] = fold
    return _events.load_event_layer(root, acq_id=acq_id, ev_id=ev_id, **kwargs)


def production_bindings(
    dataset_root: Path | str | None = None,
    *,
    knowledge_log_path: Path | str | None = KNOWLEDGE_LOG,
) -> Campaign3Bindings:
    """Derive every Campaign-3 binding from its authoritative source.

    Metadata only: the dataset identity manifest, the overlay manifest, the
    D7c trusted-input manifest and the v1.3 event-layer manifest.  It reads no
    parquet content, no label and no return value, and appends nothing.
    """
    root = Path(dataset_root) if dataset_root is not None else _DATA_STORE / (
        "discovery_dataset_v1"
    )
    try:
        from smart_beta.campaign import campaign2 as _c2
        from smart_beta.dataset_v1 import quarantine as _pqa

        dataset_id = _pqa.DATASET_ID
        dataset_identity_hash = _pqa.verify_dataset_v1_2_identity(
            root, knowledge_log_path=knowledge_log_path
        )
        manifest = _pqa.verify_overlay(root)
        pq_id = manifest.get("pq_id")
        pq_hash = manifest.get("pq_hash")
        ti_path = root / _c2._c1.TI_MANIFEST_RELATIVE
        ti_payload = json.loads(ti_path.read_text(encoding="utf-8"))
        ti_id = ti_payload["ti_id"]
        ti_hash = ti_payload["ti_hash"]
    except Exception as exc:  # noqa: BLE001 - re-typed, fail closed
        raise C3BindingError(f"binding derivation failed: {exc}") from exc

    # The v1.3 event-layer identities are frozen literals (the event layer is
    # not registered as a K-PIT ARTIFACT; its admission evidence is the
    # source, ADM-13).
    acq_id = EXPECTED_ACQ_ID
    ev_id = EXPECTED_EV_ID

    # Partition identity comes from the frozen Campaign partition builder.
    try:
        from smart_beta.campaign.campaign1 import build_partition

        partition_id = build_partition().partition_id
    except Exception as exc:  # noqa: BLE001 - re-typed, fail closed
        raise C3BindingError(f"partition build failed: {exc}") from exc

    bindings = Campaign3Bindings(
        dataset_id=str(dataset_id),
        dataset_identity_hash=str(dataset_identity_hash),
        pq_id=str(pq_id),
        pq_hash=str(pq_hash),
        ti_id=str(ti_id),
        ti_hash=str(ti_hash),
        acq_id=str(acq_id),
        ev_id=str(ev_id),
        partition_id=str(partition_id),
    )
    return verify_bindings(bindings)


# ---------------------------------------------------------------------------
# precommitment / preflight
# ---------------------------------------------------------------------------


def verify_precommitment(path: Path | str = PRECOMMITMENT_PATH) -> str:
    """Verify the frozen P5 precommitment file's SHA-256 (including E1)."""
    digest = _sha256_file(path)
    if digest != PRECOMMITMENT_SHA256:
        raise C3BindingError(
            f"precommitment hash {digest} != bound {PRECOMMITMENT_SHA256}"
        )
    return digest


@dataclass(frozen=True)
class KpitPreflight:
    """The read-only Knowledge-PIT preflight result."""

    ok: bool
    reason: str | None
    terminal_seq: int | None = None
    terminal_hash: str | None = None
    record_hashes: tuple[str, ...] = ()


def pin_kpit_terminal(log_path: Path | str) -> KnowledgeSnapshot:
    """Read and pin the K-PIT terminal ``(seq, hash)`` at launch."""
    return snapshot_of_records(read_records(log_path))


def verify_kpit_terminal(
    log_path: Path | str, pinned: KnowledgeSnapshot
) -> KnowledgeSnapshot:
    """Refuse a K-PIT terminal that moved since launch.

    P6 §1(c): the terminal seq/hash is pinned at launch; any movement while the
    run is starting (another writer, a stray append) is a hard stop, never a
    silent append onto an unexpected prefix.
    """
    current = snapshot_of_records(read_records(log_path))
    if current != pinned:
        raise C3PreflightError(
            "the K-PIT terminal moved since launch: pinned "
            f"(seq={pinned.length}, hash={pinned.head_hash}) != current "
            f"(seq={current.length}, hash={current.head_hash})"
        )
    return current


def _decision_kind(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("decision_kind")
    return value if isinstance(value, str) else None


def verify_kpit_ordering(
    log_path: Path | str,
    *,
    pinned: KnowledgeSnapshot | None = None,
) -> KpitPreflight:
    """The read-only Campaign-3 K-PIT ordering preflight.

    The CAMPAIGN precommitment record must precede the HUMAN-channel control
    ``HYPOTHESIS_FREEZE``; both must precede every GENERATOR-channel record.
    When ``pinned`` is supplied the terminal check is included.  The function
    is strictly read-only.
    """
    try:
        records = read_records(log_path)
    except Exception as exc:  # noqa: BLE001 - defensive
        return KpitPreflight(False, f"knowledge log unreadable: {exc}")
    if pinned is not None:
        terminal = snapshot_of_records(records)
        if terminal != pinned:
            return KpitPreflight(
                False,
                "the K-PIT terminal moved since launch",
                terminal.length,
                terminal.head_hash,
            )
    campaign = [
        record
        for record in records
        if record.kind is RecordKind.HUMAN_DECISION
        and isinstance(record.payload.get("campaign_precommitment"), Mapping)
    ]
    control = [
        record
        for record in records
        if record.kind is RecordKind.HYPOTHESIS_FREEZE
        and record.channel is Channel.HUMAN
    ]
    generator = [
        record for record in records if record.channel is Channel.GENERATOR
    ]
    if len(campaign) != 1:
        return KpitPreflight(False, "CAMPAIGN precommitment must appear exactly once")
    if len(control) != 1:
        return KpitPreflight(False, "the control freeze must appear exactly once")
    campaign_record = campaign[0]
    control_record = control[0]
    if control_record.seq <= campaign_record.seq:
        return KpitPreflight(False, "the control freeze precedes the CAMPAIGN record")
    if generator and min(record.seq for record in generator) <= control_record.seq:
        return KpitPreflight(False, "a GENERATOR record precedes the control freeze")
    hashes = tuple(record.record_hash for record in records)
    return KpitPreflight(
        True,
        None,
        len(records),
        records[-1].record_hash if records else None,
        hashes,
    )


def _campaign_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = payload.get("campaign_precommitment")
    return value if isinstance(value, Mapping) else {}


# ---------------------------------------------------------------------------
# control hypothesis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlHypothesis:
    """The frozen P4/P5 positive control (D11)."""

    hypothesis_id: str = CONTROL_HYPOTHESIS_ID
    hold_months: int = CONTROL_HOLD_MONTHS
    sign: int = CONTROL_SIGN

    def __post_init__(self) -> None:
        if self.hypothesis_id != CONTROL_HYPOTHESIS_ID:
            raise C3ConfigurationError(
                f"control id must be {CONTROL_HYPOTHESIS_ID!r}"
            )
        if self.hold_months != CONTROL_HOLD_MONTHS or self.sign != CONTROL_SIGN:
            raise C3ConfigurationError("the control hold/sign are frozen")

    def to_content(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "semantic_id": _event_role_id(),
            "hold_months": int(self.hold_months),
            "sign": int(self.sign),
            "channel": Channel.HUMAN.value,
        }


def _event_role_id() -> str:
    """The v1.3 event semantic id of the control (read-only literal)."""
    from smart_beta.dataset_v1 import pqv2_events as _events

    return _events.EVENT_EA_KKP1


CONTROL = ControlHypothesis()


def control_factor_hash(control: ControlHypothesis = CONTROL) -> str:
    """A deterministic 64-hex hash of the frozen control definition."""
    return content_hash(
        {"domain": "campaign3:positive-control", **control.to_content()}
    )


def control_content(control: ControlHypothesis = CONTROL) -> dict[str, Any]:
    return control.to_content()


# ---------------------------------------------------------------------------
# code hashes
# ---------------------------------------------------------------------------


def campaign3_code_hashes(repo_root: Path | str = REPO_ROOT) -> dict[str, str | None]:
    """SHA-256 of every frozen code file named by P6 §1(d).

    A file that is absent (a concurrent task not yet merged) is recorded as
    ``None``; it is never silently omitted.  ``campaign3_run.py`` itself is
    included, so the record hashes the file that produced it.
    """
    root = Path(repo_root)
    files = list(CAMPAIGN3_CODE_FILES) + list(PQ_PINNED_FILES) + list(
        RESERVE_CHAIN_FILES
    )
    hashes: dict[str, str | None] = {}
    for relative in files:
        path = root / relative
        hashes[relative] = _sha256_file(path) if path.is_file() else None
    return hashes


# ---------------------------------------------------------------------------
# K-PIT records
# ---------------------------------------------------------------------------


def campaign_precommitment_content(
    *,
    bindings: Campaign3Bindings,
    precommitment_sha256: str,
    a2_sha256: str,
    code_sha256: Mapping[str, str | None],
    control: ControlHypothesis = CONTROL,
) -> dict[str, Any]:
    """The canonical CAMPAIGN precommitment payload."""
    return {
        "schema": CAMPAIGN_RECORD_SCHEMA,
        "record_type": CAMPAIGN_RECORD_TYPE,
        "precommitment_sha256": precommitment_sha256,
        "a2_sha256": a2_sha256,
        "bindings": bindings.to_content(),
        "bindings_hash": bindings.content_hash,
        "control": control.to_content(),
        "control_factor_hash": control_factor_hash(control),
        "authority": {
            "pq_authority": _eval.AUTHORITY.version,
            "methodology_sha256": _eval.AUTHORITY.methodology_sha256,
            "cs_id": _eval.AUTHORITY.cs_id,
            "ed_id": _eval.AUTHORITY.ed_id,
            "ce_id": _eval.AUTHORITY.ce_id,
        },
        "evaluation": {
            "universe": _eval.TA_UNIVERSE,
            "horizon": _eval.TA_HORIZON,
            "n_groups": _eval.TA_N_GROUPS,
            "cost_bps": _eval.TA_COST_BPS,
            "scenarios": ["T0", "TS"],
        },
        "code_sha256": dict(code_sha256),
    }


def _existing_campaign_record(log: KnowledgeLog) -> KnowledgeRecord | None:
    for record in log.read():
        if record.kind is not RecordKind.HUMAN_DECISION:
            continue
        if isinstance(record.payload.get("campaign_precommitment"), Mapping):
            return record
    return None


def _campaign_decision_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "decision_kind": "OTHER",
        "actor_role": "OPERATOR",
        "consulted_all_prior": True,
        "campaign_precommitment": dict(payload),
    }


def write_campaign_precommitment(
    log: KnowledgeLog,
    *,
    payload: Mapping[str, Any],
) -> KnowledgeRecord:
    """Append the CAMPAIGN precommitment HUMAN decision (idempotent)."""
    decision_payload = _campaign_decision_payload(payload)
    existing = _existing_campaign_record(log)
    if existing is not None:
        if _jsonable(dict(existing.payload)) != _jsonable(decision_payload):
            raise C3GovernanceConflict(
                "an existing CAMPAIGN record has different content"
            )
        return existing
    return log.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        payload=decision_payload,
    )


def _existing_control_record(log: KnowledgeLog) -> KnowledgeRecord | None:
    for record in log.read():
        if record.kind is RecordKind.HYPOTHESIS_FREEZE and record.channel is (
            Channel.HUMAN
        ):
            return record
    return None


def write_control_freeze(
    log: KnowledgeLog,
    *,
    campaign_record: KnowledgeRecord,
    control: ControlHypothesis = CONTROL,
) -> KnowledgeRecord:
    """Append the HUMAN-channel control ``HYPOTHESIS_FREEZE`` (idempotent)."""
    existing = _existing_control_record(log)
    if existing is not None:
        if existing.payload.get("hypothesis_id") != control.hypothesis_id:
            raise C3GovernanceConflict("an existing control freeze differs")
        return existing
    return log.append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        channel=Channel.HUMAN,
        payload={
            "hypothesis_id": control.hypothesis_id,
            "factor_spec_hash": control_factor_hash(control),
            "hold_months": int(control.hold_months),
            "sign": int(control.sign),
            "control": True,
            "schema": CONTROL_RECORD_SCHEMA,
        },
        refs={"influenced_by": (campaign_record.record_hash,)},
    )


# ---------------------------------------------------------------------------
# run directory (write-ahead, resumable)
# ---------------------------------------------------------------------------


def run_dir_for(attempt_id: str, run_root: Path | str = RUN_ROOT) -> Path:
    """``<run_root>/<attempt_id>/`` (P6 §1(e))."""
    return Path(run_root) / attempt_id


_STAGE_NAMES = ("generation", "ranking", "tb_selection", "tb", "report", "manifest")


@dataclass
class RunJournal:
    """A tiny write-ahead JSON journal for the run stages.

    Each stage is atomically persisted once.  ``completed`` lets a resume skip
    a stage that already finished, so a generator call or an evaluation is
    never double counted.
    """

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, stage: str) -> Path:
        if stage not in _STAGE_NAMES:
            raise C3ConfigurationError(f"unknown stage {stage!r}")
        return self.root / f"{stage}.json"

    def is_complete(self, stage: str) -> bool:
        return self.path_for(stage).is_file()

    def write(self, stage: str, payload: Mapping[str, Any]) -> Path:
        path = self.path_for(stage)
        body = dict(payload)
        body.setdefault("stage", stage)
        body["content_hash"] = content_hash(_jsonable(body))
        _atomic_write_json(path, body)
        return path

    def read(self, stage: str) -> dict[str, Any] | None:
        path = self.path_for(stage)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def completed_stages(self) -> tuple[str, ...]:
        return tuple(stage for stage in _STAGE_NAMES if self.is_complete(stage))


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(_jsonable(payload), indent=2, sort_keys=True, ensure_ascii=True)
        + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    if hasattr(value, "to_content"):
        return _jsonable(value.to_content())
    if isinstance(value, Path):
        return str(value)
    return str(value)


# ---------------------------------------------------------------------------
# declarations / panels
# ---------------------------------------------------------------------------

#: The frozen C3 base-declaration literals (P4 §4, P5 §4).
C3_HORIZON = 21
C3_N_GROUPS = 5
C3_COST_BPS = 10.0
C3_WINSORIZATION = 0.01
C3_PERIODS_PER_YEAR = 252
C3_UNIVERSE = _eval.TA_UNIVERSE
#: NET basis: the P-Q L envelope is defined only for the net metric, and P4 §4
#: / P5 D12 gate on the **net** long-short mean.
C3_BASIS = "NET"
C3_PRECOMMITMENT_REF = "docs/campaign3_p5_precommitment.md@" + PRECOMMITMENT_SHA256


def build_declaration(
    *,
    spec: Any,
    panel: Any,
    fold: Any,
    bindings: Campaign3Bindings,
    universe: str = C3_UNIVERSE,
) -> _eval._evaluation.PQv2EvaluationDeclaration:
    """Build the C3 T-A/T-B declaration wrapped with ``AUTHORITY_V2_3``.

    Mirrors the Campaign-2 ``build_declaration`` / ``wrap_declaration``
    pattern, but binds the P-Q v2.3 authority and the Campaign-3 D7c/D7b
    identities.  The sealed fold is deliberately allowed here (T-B is its
    first use, P6 §1).
    """
    from smart_beta.dataset_v1.delisting_bridge import (
        CampaignEvaluationDeclaration,
        _canonical_factor_hash,
        fold_key,
    )

    base = CampaignEvaluationDeclaration(
        precommitment_ref=C3_PRECOMMITMENT_REF,
        factor_spec_version=spec.version,
        factor_panel_sha256=_canonical_factor_hash(panel),
        partition_id=bindings.partition_id,
        fold_key=fold_key(fold),
        universe=universe,
        horizon=C3_HORIZON,
        n_groups=C3_N_GROUPS,
        winsorization=C3_WINSORIZATION,
        transaction_cost_bps=C3_COST_BPS,
        periods_per_year=C3_PERIODS_PER_YEAR,
        weighting="EQUAL",
        primary_metric=_eval.TA_METRIC_BASIS,
        basis=C3_BASIS,
        screen_id="campaign3-p6-screen-v1",
        screen_policy_hash=PRECOMMITMENT_SHA256,
    )
    return _eval.build_ta_declaration(
        base,
        dataset_id=bindings.dataset_id,
        dataset_identity_hash=bindings.dataset_identity_hash,
        pq_id=bindings.pq_id,
        pq_hash=bindings.pq_hash,
    )


def build_factor_panel(
    spec: Any,
    inputs: Mapping[str, Any],
    *,
    universe_members: Mapping[Any, Sequence[str]] | None = None,
) -> Any:
    """The Campaign-2 FactorSpec -> panel path (``spec.evaluator.evaluate``).

    ``inputs`` maps every declared alias to its trusted value frame.  When
    ``universe_members`` is supplied (formation -> member keys), the panel is
    restricted to those members; otherwise the full long panel is returned.
    """
    import pandas as pd

    from smart_beta.spec import evaluator as _spec_evaluator

    aliases = {
        item.alias: inputs[item.requirement.semantic_id] for item in spec.inputs
    }
    result = _spec_evaluator.evaluate(spec, aliases)
    panel = result.panel
    wide = (
        panel.pivot(index="date", columns="stock_id", values="value")
        .sort_index()
        .sort_index(axis=1)
    )
    if not universe_members:
        return panel
    rows: list[dict[str, Any]] = []
    for formation in sorted(universe_members):
        stamp = pd.Timestamp(formation)
        for key in sorted(universe_members[formation]):
            value = (
                float(wide.at[stamp, key])
                if stamp in wide.index and key in wide.columns
                else float("nan")
            )
            rows.append({"date": formation, "stock_id": key, "value": value})
    frame = pd.DataFrame(rows, columns=["date", "stock_id", "value"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame["stock_id"] = frame["stock_id"].astype("string")
    frame["value"] = frame["value"].astype("float64")
    return frame


def build_ta_evaluator(
    *,
    root: Path | str,
    partition: Any,
    fold: Any,
    overlay: Any,
    inputs: Mapping[str, Any],
    declaration_builder: Callable[[Any, Any], Any],
    panel_builder: Callable[[Any], Any],
    persist_dir: Path | str | None = None,
    bindings: Any | None = None,
    source_bindings: Any | None = None,
    pq_bindings: Any | None = None,
    pq_read_records_fn: Any | None = None,
    event_layer: Any | None = None,
    diagnostics_sink: Callable[[Any], None] | None = None,
) -> _eval.TaEvaluator:
    """Construct the C3-X T-A (IS-only) evaluator.

    The IS firewall is enforced by :class:`TaEvaluator` itself
    (:func:`campaign3_eval.assert_is_fold`).  The declaration/panel builders
    are injected because the panel depends on the candidate expression.
    """
    _eval.assert_is_fold(fold)
    return _eval.TaEvaluator(
        root=root,
        partition=partition,
        fold=fold,
        overlay=overlay,
        inputs=inputs,
        declaration_builder=declaration_builder,
        panel_builder=panel_builder,
        persist_dir=persist_dir,
        bindings=bindings,
        source_bindings=source_bindings,
        pq_bindings=pq_bindings,
        pq_read_records_fn=pq_read_records_fn,
        event_layer=event_layer,
        diagnostics_sink=diagnostics_sink,
    )


# ---------------------------------------------------------------------------
# T-B evaluator (OOS + DEVELOPMENT_SEALED), write-ahead persisted
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TbFoldEvaluation:
    """One T-B fold's diagnostics plus the exported per-period series."""

    fold_key: str
    diagnostics: Any
    series: Any
    diagnostics_hash: str

    def to_content(self) -> dict[str, Any]:
        return {
            "fold_key": self.fold_key,
            "diagnostics_hash": self.diagnostics_hash,
            "series_hash": getattr(self.series, "export_hash", None),
            "series": _jsonable(self.series),
        }


@dataclass(frozen=True)
class TbEvaluation:
    """The write-ahead T-B result of one hypothesis (P6 §1(c) + P6-E1)."""

    hypothesis_id: str
    folds: tuple[TbFoldEvaluation, ...]
    write_ahead_hashes: tuple[str, ...] = ()

    @property
    def series_records(self) -> tuple[Any, ...]:
        records: list[Any] = []
        for fold in self.folds:
            records.extend(tuple(getattr(fold.series, "records", ()) or ()))
        return tuple(records)

    @property
    def diagnostics_hashes(self) -> tuple[str, ...]:
        return tuple(fold.diagnostics_hash for fold in self.folds)

    def to_content(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "folds": [fold.to_content() for fold in self.folds],
            "write_ahead_hashes": list(self.write_ahead_hashes),
        }


def _load_export_series() -> Any:
    """Load the required PQS ``pqv2_series.export_series`` (fail closed).

    Erratum P6-E1 makes ``pqv2_series`` a required consumer of the T-B
    evaluation; a missing module or exporter is a
    :class:`C3ConfigurationError`, never ``None``.
    """
    try:
        module = importlib.import_module("smart_beta.dataset_v1.pqv2_series")
    except ImportError:
        raise C3ConfigurationError(
            "pqv2_series is required by P6-E1 and is not importable"
        ) from None
    exporter = getattr(module, "export_series", None)
    if not callable(exporter):
        raise C3ConfigurationError(
            "pqv2_series is present but exposes no export_series"
        )
    return exporter


@dataclass
class TbEvaluator:
    """A bound T-B evaluator: ``hypothesis_id -> TbEvaluation``.

    For each declared T-B fold (OOS + DEVELOPMENT_SEALED) it builds the
    candidate's panel and declaration, calls the sealed
    ``build_pqv2_diagnostics`` under :data:`AUTHORITY_V2_3`, persists the
    diagnostics write-ahead and exports the P6-E1 B/L/gross/net series with
    ``pqv2_series.export_series`` (fail-closed: an absent exporter is an
    error, never a silent substitution).
    """

    root: Path | str
    partition: Any
    folds: tuple[Any, ...]
    overlay: Any
    inputs: Mapping[str, Any]
    declaration_builder: Callable[[Any, Any, Any], Any]
    panel_builder: Callable[[Any], Any]
    proposal_lookup: Mapping[str, Any]
    persist_dir: Path | str
    bindings: Any | None = None
    source_bindings: Any | None = None
    pq_bindings: Any | None = None
    pq_read_records_fn: Any | None = None
    event_layer: Any | None = None
    diagnostics_builder: Callable[..., Any] | None = None
    export_series_fn: Callable[..., Any] | None = None
    diagnostics_sink: Callable[[Any], None] | None = None
    evaluations: dict[str, TbEvaluation] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.folds:
            raise C3ConfigurationError("the T-B evaluator needs at least one fold")
        self.persist_dir = Path(self.persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

    def _builder(self) -> Callable[..., Any]:
        if self.diagnostics_builder is not None:
            return self.diagnostics_builder
        # T-B is *not* the IS fold, so the C3-E IS-only firewall wrapper is
        # deliberately bypassed here: the sealed P-Q v2.3 entry point is
        # called directly for the OOS + DEVELOPMENT_SEALED folds.
        from smart_beta.dataset_v1 import pqv2_evaluation as _pe

        return _pe.build_pqv2_diagnostics

    def _exporter(self) -> Callable[..., Any]:
        if self.export_series_fn is not None:
            return self.export_series_fn
        return _load_export_series()

    def __call__(self, hypothesis_id: str) -> TbEvaluation:
        if hypothesis_id in self.evaluations:
            return self.evaluations[hypothesis_id]
        proposal = self.proposal_lookup.get(hypothesis_id)
        if proposal is None:
            raise C3ConfigurationError(
                f"no proposal for T-B hypothesis {hypothesis_id!r}"
            )
        factor_spec = proposal.proposed_factor_spec
        panel = self.panel_builder(factor_spec)
        builder = self._builder()
        exporter = self._exporter()
        folds: list[TbFoldEvaluation] = []
        write_ahead: list[str] = []
        for fold in self.folds:
            declaration = self.declaration_builder(factor_spec, panel, fold)
            diagnostics = builder(
                self.root,
                declaration,
                factor_spec,
                panel,
                self.partition,
                fold=fold,
                overlay=self.overlay,
                inputs=self.inputs,
                bindings=self.bindings,
                source_bindings=self.source_bindings,
                pq_bindings=self.pq_bindings,
                pq_read_records_fn=self.pq_read_records_fn,
                event_layer=self.event_layer,
            )
            digest = _persist_diagnostics(diagnostics, self.persist_dir)
            write_ahead.append(digest)
            if self.diagnostics_sink is not None:
                self.diagnostics_sink(diagnostics)
            series = exporter(
                self.root,
                declaration,
                factor_spec,
                panel,
                self.partition,
                diagnostics=diagnostics,
                overlay=self.overlay,
                inputs=self.inputs,
                bindings=self.bindings,
                source_bindings=self.source_bindings,
                pq_bindings=self.pq_bindings,
                pq_read_records_fn=self.pq_read_records_fn,
                event_layer=self.event_layer,
            )
            folds.append(
                TbFoldEvaluation(
                    fold_key=declaration.base.fold_key,
                    diagnostics=diagnostics,
                    series=series,
                    diagnostics_hash=digest,
                )
            )
        evaluation = TbEvaluation(
            hypothesis_id=hypothesis_id,
            folds=tuple(folds),
            write_ahead_hashes=tuple(write_ahead),
        )
        self.evaluations[hypothesis_id] = evaluation
        return evaluation


def _persist_diagnostics(diagnostics: Any, directory: Path) -> str:
    from smart_beta.campaign import diagnostics_store as _store

    digest = _store.persist(diagnostics, directory)
    attached = getattr(diagnostics, "b_diagnostics", None)
    if attached is not None:
        _store.persist(attached, directory)
    return str(digest)


def build_tb_evaluator(
    *,
    root: Path | str,
    partition: Any,
    folds: Sequence[Any],
    overlay: Any,
    inputs: Mapping[str, Any],
    declaration_builder: Callable[[Any, Any, Any], Any],
    panel_builder: Callable[[Any], Any],
    proposal_lookup: Mapping[str, Any],
    persist_dir: Path | str,
    **kwargs: Any,
) -> TbEvaluator:
    """Construct the write-ahead T-B evaluator (OOS + DEVELOPMENT_SEALED).

    The T-B folds are supplied by the caller: the OOS fold and the
    development **sealed** fold (the frozen partition's ``holdout`` role).
    The reserve (T-C) fold is never passed here, and no reserve path is
    opened.
    """
    return TbEvaluator(
        root=root,
        partition=partition,
        folds=tuple(folds),
        overlay=overlay,
        inputs=inputs,
        declaration_builder=declaration_builder,
        panel_builder=panel_builder,
        proposal_lookup=proposal_lookup,
        persist_dir=persist_dir,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# C3-S seam
# ---------------------------------------------------------------------------


def stub_screen_runner(**kwargs: Any) -> dict[str, Any]:
    """A **test-only** fallback screen runner (never resolved implicitly).

    It returns a ``STUBBED_C3_S`` report.  It exists so the offline tests can
    inject a deterministic runner explicitly; :func:`load_screen_runner` never
    falls back to it, and :meth:`Campaign3Run.run` refuses the resulting
    report unless ``allow_stub_screen=True`` is set explicitly (test-only).
    """
    return {
        "status": "STUBBED_C3_S",
        "note": (
            "test-only stub injected explicitly; the real C3-S selection "
            "tier (C3-S / C3-S-R1) is required for a production run"
        ),
        "candidate_ids": [
            getattr(item, "hypothesis_id", None)
            for item in kwargs.get("candidates", ())
        ],
        "control_id": getattr(
            kwargs.get("control"), "hypothesis_id", None
        ),
    }


def load_screen_runner() -> Callable[..., Any]:
    """Return the merged ``campaign3_screen.run_screen``; fail closed.

    C3-R-R1: there is **no implicit stub fallback**.  A missing
    ``campaign3_screen`` module — or one that exposes no ``run_screen`` — is a
    :class:`C3ConfigurationError`, so a production run can never silently
    proceed with a stubbed selection tier.
    """
    try:
        module = importlib.import_module("smart_beta.campaign.campaign3_screen")
    except ImportError:
        raise C3ConfigurationError(
            "campaign3_screen is not merged; the C3-S selection tier is "
            "required and no implicit stub is accepted"
        ) from None
    runner = getattr(module, "run_screen", None)
    if runner is None:
        raise C3ConfigurationError(
            "campaign3_screen is present but exposes no run_screen"
        )
    return runner


def load_screen_module() -> Any:
    """Return the merged ``campaign3_screen`` module; fail closed.

    Same guard as :func:`load_screen_runner`; used by the driver to reach the
    real ``ScreeningCandidate`` / ``TbDiagnostics`` types when adapting the
    inferred T-B evaluator.
    """
    load_screen_runner()  # fail closed before importing the module
    return importlib.import_module("smart_beta.campaign.campaign3_screen")


# ---------------------------------------------------------------------------
# generation snapshot
# ---------------------------------------------------------------------------


def _candidate_record(proposal: Any, evaluation: Any) -> dict[str, Any]:
    sign = 1
    factor_spec = getattr(proposal, "proposed_factor_spec", None)
    if factor_spec is not None:
        sign = int(getattr(factor_spec, "sign", 1) or 1)
    return {
        "proposal_id": proposal.proposal_id,
        "hypothesis_id": _gen._derive_id(
            "campaign3:hypothesis",
            proposal.proposal_id,
            proposal.proposed_factor_spec_hash,
        ),
        "factor_spec_hash": proposal.proposed_factor_spec_hash,
        "canonical_key": proposal.proposed_factor_spec_hash,
        "sign": sign,
        "assessed": bool(getattr(evaluation, "assessed", False)),
        "not_assessed_reason": getattr(evaluation, "not_assessed_reason", None),
        "diagnostics_hash": getattr(evaluation, "diagnostics_hash", None),
        "is_fold_key": getattr(evaluation, "is_fold_key", None),
        "metrics": [
            metric.to_dict() for metric in getattr(evaluation, "metrics", ())
        ],
    }


@dataclass(frozen=True)
class GenerationSnapshot:
    """The persisted, resumable output of the generation stage."""

    manifest: Mapping[str, Any]
    candidates: tuple[Mapping[str, Any], ...]
    stop_reason: str
    kpit_record_hashes: tuple[str, ...]
    diagnostics_dir: str

    def to_content(self) -> dict[str, Any]:
        return {
            "manifest": _jsonable(self.manifest),
            "candidates": [_jsonable(item) for item in self.candidates],
            "stop_reason": self.stop_reason,
            "kpit_record_hashes": list(self.kpit_record_hashes),
            "diagnostics_dir": self.diagnostics_dir,
        }

    @classmethod
    def from_content(cls, payload: Mapping[str, Any]) -> "GenerationSnapshot":
        return cls(
            manifest=dict(payload.get("manifest", {})),
            candidates=tuple(payload.get("candidates", ())),
            stop_reason=str(payload.get("stop_reason", "")),
            kpit_record_hashes=tuple(payload.get("kpit_record_hashes", ())),
            diagnostics_dir=str(payload.get("diagnostics_dir", "")),
        )


# ---------------------------------------------------------------------------
# the run driver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Campaign3RunResult:
    """The deterministic result of one C3-R run (or resume)."""

    attempt_id: str
    run_dir: str
    stop_reason: str
    completed_stages: tuple[str, ...]
    manifest: Mapping[str, Any]
    screen_report: Mapping[str, Any] | None
    kpit_record_hashes: tuple[str, ...]


def _generator_prompt_template_hash(generator: Any) -> str | None:
    """The injected generator's own prompt-template hash, when it exposes one.

    Erratum A2-E2: the Campaign-3 policy's ``prompt_template_hash`` must be the
    v2 template hash carried by the injected DeepSeek generator, never the
    module-level v1 ``llm_generator.PROMPT_TEMPLATE_HASH`` literal.
    """
    value = getattr(generator, "prompt_template_hash", None)
    if callable(value):
        value = value()
    if isinstance(value, str) and value:
        return value
    return None


def _policy_with_generator_prompt_hash(
    policy: ResearchPolicy, generator: Any
) -> ResearchPolicy:
    """Bind the policy's prompt hash to the injected generator (A2-E2)."""
    import dataclasses as _dataclasses

    prompt_hash = _generator_prompt_template_hash(generator)
    if prompt_hash is None:
        raise C3ConfigurationError(
            "the injected generator must expose a non-empty "
            "prompt_template_hash (erratum A2-E2); the policy's v1 literal is "
            "never used as a fallback"
        )
    if policy.prompt_template_hash == prompt_hash:
        return policy
    return _dataclasses.replace(policy, prompt_template_hash=prompt_hash)


@dataclass
class Campaign3Run:
    """The C3-R ``run()`` driver.

    Every external dependency is injected so the driver is exercised offline:
    a stub generator, a stub T-A evaluator, an injected ``screen_runner`` and
    a disposable :class:`KnowledgeLog`.
    """

    bindings: Campaign3Bindings
    policy: ResearchPolicy
    generator: _gen.GeneratorCallable
    ta_evaluator: Any
    knowledge_log: KnowledgeLog
    run_root: Path | str = RUN_ROOT
    repo_root: Path | str = REPO_ROOT
    precommitment_path: Path | str = PRECOMMITMENT_PATH
    a2_path: Path | str = A2_PATH
    control: ControlHypothesis = CONTROL
    screen_runner: Callable[..., Any] | None = None
    #: Builds the C3-S ``ScreeningCandidate`` sequence and the control from the
    #: persisted generation snapshot.  Required for the real screen (the T-A IS
    #: diagnostics and their ``pqv2_series`` export are not in the snapshot).
    screen_candidate_builder: (
        Callable[[GenerationSnapshot], tuple[Sequence[Any], Any]] | None
    ) = None
    screen_inference_runner: Callable[..., Any] | None = None
    screen_report_dir: Path | str | None = None
    #: Test-only escape hatch: when explicitly ``True`` a screen runner that
    #: returns a ``STUBBED_*`` report is accepted.  Production runs leave it
    #: ``False`` so a stubbed selection tier can never pass silently.
    allow_stub_screen: bool = False
    tb_evaluator: Any | None = None
    registry: Any | None = None
    admission_records: Sequence[Any] = ()
    procedure_ref: Mapping[str, str] | None = None
    attempt_id: str | None = None
    launch_terminal: KnowledgeSnapshot | None = None
    inclusion_provider: Callable[..., Sequence[str]] | None = None

    def __post_init__(self) -> None:
        self.bindings = verify_bindings(self.bindings)
        self.policy = _policy_with_generator_prompt_hash(
            self.policy, self.generator
        )
        if self.attempt_id is not None and not isinstance(self.attempt_id, str):
            raise C3ConfigurationError("attempt_id must be text")

    # -- identity --------------------------------------------------------
    def compute_attempt_id(self) -> str:
        precommitment = verify_precommitment(self.precommitment_path)
        return content_hash(
            {
                "domain": "campaign3-p6-attempt",
                "precommitment_sha256": precommitment,
                "bindings_hash": self.bindings.content_hash,
                "control_factor_hash": control_factor_hash(self.control),
                "policy_hash": self.policy.content_hash,
            }
        )

    # -- precommitment ---------------------------------------------------
    def _campaign_payload(self) -> dict[str, Any]:
        precommitment_sha = verify_precommitment(self.precommitment_path)
        a2_sha = _sha256_file(self.a2_path)
        code = campaign3_code_hashes(self.repo_root)
        return campaign_precommitment_content(
            bindings=self.bindings,
            precommitment_sha256=precommitment_sha,
            a2_sha256=a2_sha,
            code_sha256=code,
            control=self.control,
        )

    def _write_campaign_record(self) -> KnowledgeRecord:
        return write_campaign_precommitment(
            self.knowledge_log, payload=self._campaign_payload()
        )

    def _write_control_record(
        self, campaign_record: KnowledgeRecord
    ) -> KnowledgeRecord:
        return write_control_freeze(
            self.knowledge_log,
            campaign_record=campaign_record,
            control=self.control,
        )

    # -- generation ------------------------------------------------------
    def _generation_snapshot(self, journal: RunJournal) -> GenerationSnapshot:
        existing = journal.read("generation")
        if existing is not None:
            return GenerationSnapshot.from_content(existing)
        diagnostics_dir = journal.root / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        kpit = _gen.KnowledgeLogKpitWriter(
            self.knowledge_log,
            program_id=self.policy.program.program_id,
        )
        executor = _gen.Campaign3Executor(
            policy=self.policy,
            generator=self.generator,
            evaluator=self.ta_evaluator,
            kpit=kpit,
            inclusion_provider=self.inclusion_provider,
        )
        result = executor.run()
        evaluations = {item.proposal_id: item for item in result.evaluations}
        candidates = tuple(
            _candidate_record(entry.proposal, evaluations[entry.proposal_id])
            for entry in result.registry.entries
            if entry.proposal_id in evaluations
        )
        manifest = result.manifest.to_dict()
        snapshot = GenerationSnapshot(
            manifest=manifest,
            candidates=candidates,
            stop_reason=str(manifest.get("stop_reason", "")),
            kpit_record_hashes=tuple(manifest.get("kpit_record_hashes", ())),
            diagnostics_dir=str(diagnostics_dir),
        )
        journal.write("generation", snapshot.to_content())
        return snapshot

    # -- run -------------------------------------------------------------
    def run(self) -> Campaign3RunResult:
        """Run/reconcile the P6 stages to a frozen stop (idempotent)."""
        attempt_id = self.attempt_id or self.compute_attempt_id()
        run_dir = run_dir_for(attempt_id, self.run_root)
        journal = RunJournal(run_dir)

        # The C3-S selection tier is required: there is no implicit stub
        # fallback.  A runner is either explicitly injected or resolved from
        # the merged campaign3_screen module, which raises when it is absent.
        screen_runner = self.screen_runner
        if screen_runner is None:
            screen_runner = load_screen_runner()

        # (c) the terminal pinned at launch must not have moved.
        if self.launch_terminal is not None:
            verify_kpit_terminal(self.knowledge_log.path, self.launch_terminal)

        # (d) precommitment + control BEFORE the first generator call.
        campaign_record = self._write_campaign_record()
        control_record = self._write_control_record(campaign_record)

        # (e) generation (write-ahead; a resume never re-calls the generator).
        generation = self._generation_snapshot(journal)

        # (f) the C3-S selection tier (rank -> TB_SELECTION -> T-B -> report).
        screen_report: Mapping[str, Any] | None = None
        if journal.is_complete("report"):
            stored = journal.read("report")
            screen_report = dict(stored) if isinstance(stored, Mapping) else None
        else:
            screen_report = self._run_screen(journal, generation, screen_runner)

        manifest = dict(generation.manifest)
        manifest["attempt_id"] = attempt_id
        manifest["campaign_record_hash"] = campaign_record.record_hash
        manifest["control_record_hash"] = control_record.record_hash
        journal.write("manifest", {"manifest": manifest})
        return Campaign3RunResult(
            attempt_id=attempt_id,
            run_dir=str(run_dir),
            stop_reason=generation.stop_reason,
            completed_stages=journal.completed_stages(),
            manifest=manifest,
            screen_report=screen_report,
            kpit_record_hashes=tuple(
                record.record_hash for record in self.knowledge_log.read()
            ),
        )

    def _run_screen(
        self,
        journal: RunJournal,
        generation: GenerationSnapshot,
        screen_runner: Callable[..., Any],
    ) -> Mapping[str, Any]:
        from smart_beta.campaign import campaign3_eval as _ev

        # The IS ranking stage consumes the persisted IS series (P6-E1).  The
        # driver hands the real C3-S ``run_screen`` its frozen inputs; C3-S-R1
        # reads pqv2_series output, never cell.period_values.
        ranking_payload = {
            "candidates": [_jsonable(item) for item in generation.candidates],
            "control": control_content(self.control),
            "generation_manifest_hash": content_hash(
                _jsonable(generation.manifest)
            ),
        }
        journal.write("ranking", ranking_payload)
        candidates, control = self._screen_candidates(generation)
        screen_kwargs: dict[str, Any] = {
            "candidates": candidates,
            "control": control,
            "registry": self.registry,
            "admission_records": self.admission_records,
            "selection_writer": self._selection_writer,
            "tb_evaluator": self._screen_tb_evaluator,
            "procedure_ref": self.procedure_ref,
            "report_dir": (
                self.screen_report_dir
                if self.screen_report_dir is not None
                else journal.root / "screen"
            ),
        }
        if self.screen_inference_runner is not None:
            screen_kwargs["inference_runner"] = self.screen_inference_runner
        screen_report = screen_runner(**screen_kwargs)
        payload = (
            screen_report.to_dict()
            if hasattr(screen_report, "to_dict")
            else _jsonable(screen_report)
        )
        # A stubbed selection tier must never pass as a real run.  The only
        # way to accept a ``STUBBED_*`` report is the test-only explicit flag.
        status = payload.get("status") if isinstance(payload, Mapping) else None
        if isinstance(status, str) and status.startswith("STUBBED_") and not (
            self.allow_stub_screen
        ):
            raise C3ConfigurationError(
                "the screen runner returned a stubbed report "
                f"(status={status!r}); C3-S is required and no implicit stub is "
                "accepted (allow_stub_screen is test-only)"
            )
        # The TB_SELECTION write-ahead is the C3-S module's responsibility
        # (``selection_writer`` above); the driver records the report stage
        # last.  ``_ev`` is imported so the frozen C3-E literals are asserted
        # available before a stage is written.
        _ = _ev.AUTHORITY
        journal.write("report", {"report": payload})
        return payload

    # -- C3-S adapters ---------------------------------------------------
    def _screen_candidates(
        self, generation: GenerationSnapshot
    ) -> tuple[tuple[Any, ...], Any]:
        """Build the C3-S candidates + control from the generation snapshot.

        The T-A IS diagnostics and their ``pqv2_series`` export are not part
        of the JSON snapshot, so the production wiring must inject
        ``screen_candidate_builder``.  A missing builder fails closed rather
        than fabricating an eligible candidate.
        """
        if self.screen_candidate_builder is None:
            raise C3ConfigurationError(
                "the run requires a screen_candidate_builder to build the C3-S "
                "candidates (IS diagnostics + pqv2_series export) from the "
                "generation snapshot; none was injected"
            )
        candidates, control = self.screen_candidate_builder(generation)
        return tuple(candidates), control

    def _selection_writer(self, selection: Any) -> KnowledgeRecord:
        """Write the TB_SELECTION K-PIT record (write-ahead, before T-B)."""
        return write_tb_selection_record(self.knowledge_log, selection)

    def _screen_tb_evaluator(self, hypothesis_id: str) -> Any:
        """Adapt the bound T-B evaluator to the C3-S ``TbDiagnostics``."""
        if self.tb_evaluator is None:
            raise C3ConfigurationError(
                "the run requires a T-B evaluator for the C3-S screen"
            )
        result = self.tb_evaluator(hypothesis_id)
        screen = load_screen_module()
        if isinstance(result, screen.TbDiagnostics):
            return result
        if isinstance(result, TbEvaluation):
            return _tb_evaluation_to_diagnostics(
                screen, hypothesis_id, result
            )
        raise C3ConfigurationError(
            "the T-B evaluator returned "
            f"{type(result).__name__}; expected TbDiagnostics or a "
            "single-fold TbEvaluation"
        )


def _tb_evaluation_to_diagnostics(
    screen: Any, hypothesis_id: str, evaluation: TbEvaluation
) -> Any:
    """One combined T-B fold -> the C3-S ``TbDiagnostics`` (fail closed).

    The real C3-S screen expects a single diagnostics record spanning the T-B
    formations; the driver does **not** silently pick one fold of a multi-fold
    evaluation.
    """
    if len(evaluation.folds) != 1:
        raise C3ConfigurationError(
            "the screen T-B adapter requires a single combined T-B fold; got "
            f"{len(evaluation.folds)} folds"
        )
    fold = evaluation.folds[0]
    return screen.TbDiagnostics(
        hypothesis_id=hypothesis_id,
        diagnostics=fold.diagnostics,
        series_export=fold.series,
    )


def write_tb_selection_record(
    log: KnowledgeLog, selection: Any
) -> KnowledgeRecord:
    """Append the TB_SELECTION HUMAN decision (the P6 write-ahead).

    Written **before** any T-B evaluation; the C3-S ``write_tb_selection``
    caller marks the selection written only after this returns a record.
    """
    payload = selection.to_content()
    payload.setdefault("record_type", "TB_SELECTION")
    return log.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "OPERATOR",
            "consulted_all_prior": True,
            "tb_selection": payload,
        },
    )


def run_campaign3(**kwargs: Any) -> Campaign3RunResult:
    """Convenience entry point: ``Campaign3Run(**kwargs).run()``."""
    return Campaign3Run(**kwargs).run()
