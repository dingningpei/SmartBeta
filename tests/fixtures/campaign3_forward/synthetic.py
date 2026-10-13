"""Synthetic fixtures for the C3-F forwarding tests (Erratum P6-E6).

Everything here is synthetic and disposable: a disposable K-PIT seeded with a
production-shaped Campaign-1/2 prefix (the decoys that prove the schema-scoped
lookups), a Campaign-3 ``CAMPAIGN`` precommitment record, a ``TB_SELECTION``
record bound to it, a persisted ``generation.json`` run state and an injected
ranking.  No ``data_store``, reserve, holdout or production K-PIT path is
touched and no network call is made.

The ``CAMPAIGN`` / ``TB_SELECTION`` payload shapes mirror
:mod:`smart_beta.campaign.campaign3_run` so the forward step's read-only
schema-scoped lookups exercise the real contract.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smart_beta.campaign import campaign3_run as R
from smart_beta.campaign import campaign3_screen as S
from smart_beta.science.contracts import content_hash
from smart_beta.science.knowledge import Channel, KnowledgeLog, RecordKind

__all__ = [
    "CAMPAIGN_RECORD_SCHEMA",
    "TB_SELECTION_SCHEMA",
    "FakeAssembly",
    "candidate",
    "candidate_record",
    "control_candidate",
    "human_decision",
    "registry",
    "admission_record",
    "series",
    "seed_campaign",
    "seed_prefix",
    "seed_tb_selection",
    "write_generation",
]


def _load_screen_synthetic():
    """The shared C3-S synthetic candidates/registry (read-only import)."""
    import sys

    name = "c3s_screen_synthetic_shared"
    if name in sys.modules:
        return sys.modules[name]
    path = (
        Path(__file__).resolve().parents[1] / "campaign3_screen" / "synthetic.py"
    )
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_SCR = _load_screen_synthetic()

#: The C3-S synthetic candidate builder.
candidate = _SCR.candidate

#: The C3-S synthetic per-period series helper.
series = _SCR.series

#: The admitted-procedure registry used by the D9 ranking.
registry = _SCR.registry

#: One PROCEDURE_ADMISSION record for the admitted procedure.
admission_record = _SCR.admission_record


#: The Campaign-3 CAMPAIGN / TB_SELECTION record schemas (mirrors the run
#: module; imported as literals so the fixture has no dependency on the
#: module under test).
CAMPAIGN_RECORD_SCHEMA = "campaign3-p6-precommitment-v1"
TB_SELECTION_SCHEMA = "campaign3-tb-selection-v1"

#: A synthetic, superseded attempt-3 CAMPAIGN record hash.
ATTEMPT3_CAMPAIGN_RECORD_HASH = "3" * 64


def human_decision(
    log: KnowledgeLog,
    payload: Mapping[str, Any],
    *,
    program_id: str = "campaign3",
) -> Any:
    """Append a minimal valid HUMAN_DECISION carrying ``payload``."""
    return log.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        program_id=program_id,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "OPERATOR",
            "consulted_all_prior": True,
            **dict(payload),
        },
    )


def seed_prefix(log: KnowledgeLog) -> Any:
    """Seed the production-shaped Campaign-1/2 prefix (decoys)."""
    import importlib.util
    import sys

    name = "c3f_run_production_prefix"
    module = sys.modules.get(name)
    if module is None:
        path = (
            Path(__file__).resolve().parents[1]
            / "campaign3_run"
            / "production_prefix.py"
        )
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return module.seed_production_prefix(log)


def seed_campaign(
    log: KnowledgeLog,
    *,
    attempt: int = 4,
    supersedes: str | None = ATTEMPT3_CAMPAIGN_RECORD_HASH,
) -> Any:
    """Append a disposable Campaign-3 CAMPAIGN precommitment record."""
    body: dict[str, Any] = {
        "schema": CAMPAIGN_RECORD_SCHEMA,
        "record_type": "CAMPAIGN_PRECOMMITMENT",
        "attempt": int(attempt),
        "bindings_hash": content_hash({"attempt": attempt, "bindings": "synthetic"}),
        "control_factor_hash": content_hash(
            {"attempt": attempt, "control": "synthetic"}
        ),
        "control_hypothesis_id": R.CONTROL_HYPOTHESIS_ID,
        "code_sha256": {},
    }
    if supersedes is not None:
        body["supersedes_record_hash"] = supersedes
        body["superseded_disposition"] = {
            "attempt": int(attempt) - 1,
            "status": "RUN_INCOMPLETE",
            "cause": "synthetic prior attempt",
        }
    return human_decision(log, {"campaign_precommitment": body})


def seed_tb_selection(
    log: KnowledgeLog,
    campaign: Any,
    *,
    ranking_table_hash: str,
    selected_ids: Sequence[str],
    control_id: str = R.CONTROL_HYPOTHESIS_ID,
) -> Any:
    """Append a TB_SELECTION record bound to ``campaign``."""
    return human_decision(
        log,
        {
            "tb_selection": {
                "schema": TB_SELECTION_SCHEMA,
                "record_type": "TB_SELECTION",
                "selected_ids": [str(item) for item in selected_ids],
                "control_id": str(control_id),
                "ranking_table_hash": str(ranking_table_hash),
                "campaign_record_hash": campaign.record_hash,
            }
        },
    )


def candidate_record(
    hypothesis_id: str,
    *,
    diagnostics_hash: str,
    canonical_key: str | None = None,
    sign: int = 1,
) -> dict[str, Any]:
    """One persisted generation-snapshot candidate entry."""
    return {
        "hypothesis_id": str(hypothesis_id),
        "proposal_id": f"proposal-{hypothesis_id}",
        "canonical_key": canonical_key or str(hypothesis_id),
        "sign": int(sign),
        "diagnostics_hash": str(diagnostics_hash),
        "factor_spec_hash": content_hash({"factor": hypothesis_id}),
    }


def write_generation(
    run_dir: Path | str,
    campaign_record_hash: str,
    candidates: Sequence[Mapping[str, Any]],
) -> Path:
    """Persist a ``generation.json`` bound to ``campaign_record_hash``."""
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    diagnostics = root / "diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage": "generation",
        "campaign_record_hash": campaign_record_hash,
        "attempt_id": "synthetic-attempt-4",
        "manifest": {"stop_reason": "PROPOSAL_BUDGET_EXHAUSTED"},
        "candidates": [dict(item) for item in candidates],
        "stop_reason": "PROPOSAL_BUDGET_EXHAUSTED",
        "kpit_record_hashes": [],
        "diagnostics_dir": str(diagnostics),
    }
    (root / "generation.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return root


def control_candidate() -> S.ScreeningCandidate:
    """The positive control candidate (never ranks, never forwarded)."""
    return S.ScreeningCandidate(
        hypothesis_id=R.CONTROL_HYPOTHESIS_ID,
        canonical_key=R.CONTROL_HYPOTHESIS_ID,
        sign=1,
        diagnostics=None,
        series_export=None,
    )


@dataclass
class FakeAssembly:
    """An injected assembly exposing only what the forward step consumes."""

    candidates: tuple[Any, ...]
    control: Any = None
    registry: Any = None
    admission_records: tuple[Any, ...] = ()
    procedure_ref: Mapping[str, str] | None = None
    diagnostics_dir: Path | None = None
    screen_cache_dir: Path | None = None
    builder_calls: list[Any] = field(default_factory=list)

    def screen_candidate_builder(self, snapshot: Any) -> tuple[tuple[Any, ...], Any]:
        self.builder_calls.append(snapshot)
        return tuple(self.candidates), self.control
