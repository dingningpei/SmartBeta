"""C2-LX — Campaign-2 development ledger and executor (P-Q v2.1).

Effective authority (in order): ``docs/campaign2_precommitment.md`` (frozen
2026-10-08), the frozen task ``worker_tasks/campaign2/task-c2-lx-executor.md``
(C2-LX), and the C1-LX task ``worker_tasks/campaign1/task-c1-ledger-executor.md``
plus its errata E1-E6.1 applied **mutatis mutandis**.

This module is the Campaign-2 counterpart of :mod:`smart_beta.campaign.campaign1`.
It reuses the unchanged Campaign-1 infrastructure by import:

* :mod:`smart_beta.campaign.ledger` — the append-only, hash-chained ledger
  (C1-LX section 6 / E3 terminal semantics);
* :mod:`smart_beta.campaign.diagnostics_store` — the write-once,
  content-addressed diagnostics artifacts (E6/E6.1);
* :mod:`smart_beta.campaign.campaign1` — the frozen partition, FactorSpecs,
  hypothesis-definition (E4) helpers and screen constants that are identical
  across the two campaigns.

It evaluates the P-Q v2.1 H API only
(:func:`smart_beta.dataset_v1.pqv2_evaluation.build_pqv2_diagnostics`,
:func:`smart_beta.dataset_v1.pqv2_evaluation.classify_pqv2`,
:class:`smart_beta.dataset_v1.pqv2_evaluation.PQv2EvaluationDeclaration`). There
is no PQB / P-Q v1 call, no O2 guard and no robustness diagnostic.

Campaign-1 stays permanently P-Q v1. This module never modifies and never
imports any P-Q v1 evaluation symbol.

Vocabulary note: this layer emits only the frozen development disposition
vocabulary (NOT_ASSESSED / DEVELOPMENT_SCREEN_FAIL / DEVELOPMENT_SCREEN_PASS)
and constructs no Phase-7 evaluation or decision record object. Behaviour is
synthetic-fixture tested only; no provider/network call, no reserve or holdout
read and no K-PIT write occur anywhere in this module.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd
from scipy.stats import norm

from smart_beta.campaign import campaign1 as _c1
from smart_beta.campaign import diagnostics_store as _store
from smart_beta.campaign.ledger import (
    CampaignLedger,
    C1ReplayMismatchError,
)
from smart_beta.dataset_v1 import delisting_bridge as _d7b
from smart_beta.dataset_v1 import pqv2_core as _core
from smart_beta.dataset_v1 import pqv2_evaluation as _evaluation
from smart_beta.dataset_v1.delisting_bridge import (
    CampaignEvaluationDeclaration,
    _canonical_factor_hash as _canonical_panel_hash,
    fold_key as _fold_key,
)
from smart_beta.evaluation.partition import Fold, FoldRole, Partition
from smart_beta.experiment.registry import canonical_json, content_hash
from smart_beta.research.policy import StopReason
from smart_beta.science.contracts import EstimandKind, RecordKind
from smart_beta.science.knowledge import read_records
from smart_beta.science.preregistration import (
    ESTIMAND_POLICY,
    EstimandPolicy,
    estimand_policy_from_record,
)
from smart_beta.spec.factor_spec import FactorSpec, factor_spec_hash

__all__ = [
    # constants
    "PRECOMMITMENT_SHA256",
    "FACTORSPECS_SHA256",
    "PROGRAM_ID",
    "SCREEN_ID",
    "SCREEN_POLICY_JSON",
    "SCREEN_POLICY_SHA256",
    "PQ_METHODOLOGY",
    "PQ_METHODOLOGY_SHA256",
    "PQ_CS_ID",
    "PQ_ED_ID",
    "PQ_CE_ID",
    "HYPOTHESIS_ID_DOMAIN",
    "EXPECTED_HYPOTHESIS_DEFINITION_HASHES",
    "EXPECTED_HYPOTHESIS_IDS",
    "PRIMARY_UNIVERSE",
    "CELL_NAMES",
    "CRITICAL_VALUE",
    "ALPHA_PER_TEST",
    "MIN_PERIODS_IS",
    "MIN_PERIODS_OOS",
    "BUDGET_LIMITS",
    "EXPECTED_LABELS",
    "FACTOR_SPECS",
    "REUSE_DISCLOSURE",
    "REPLAY_IDENTICAL",
    "PHASE9_STOP_REASON_MAP",
    # errors
    "C2CampaignError",
    "C2BindingError",
    "C2GovernanceConflict",
    "C2SealedFoldError",
    "C2Stop",
    "C2BudgetExhausted",
    # values
    "C2StopReason",
    "Disposition",
    "BudgetLimits",
    "BudgetCounters",
    "Campaign2Bindings",
    "KpitPreflight",
    # api
    "build_partition",
    "load_factor_specs",
    "verify_precommitment",
    "estimand_policy",
    "estimand_policy_content",
    "input_manifest",
    "campaign2_id",
    "campaign_design_hash",
    "campaign_attempt_id",
    "hypothesis_definition",
    "hypothesis_definition_hash",
    "campaign2_hypothesis_id",
    "hypothesis_identities",
    "verify_kpit_ordering",
    "assert_not_sealed_fold",
    "build_declaration",
    "wrap_declaration",
    "is_assessed",
    "screen_outcomes",
    "attempt_disposition",
    "cause_sets",
    "Campaign2Executor",
    "Campaign2RunResult",
    "render_report",
]

# ---------------------------------------------------------------------------
# Frozen paths, literals and v2.1 identities
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]

PRECOMMITMENT_PATH = _REPO_ROOT / "docs" / "campaign2_precommitment.md"

#: SHA-256 of ``docs/campaign2_precommitment.md`` (frozen 2026-10-08).
PRECOMMITMENT_SHA256 = (
    "0815259b60479670fe56d6dda110c46be38d719c2c0cb90da6f4902c61d80bc5"
)
#: The FactorSpecs declaration is shared with Campaign-1 and unchanged.
FACTORSPECS_SHA256 = _c1.FACTORSPECS_SHA256

PROGRAM_ID = "campaign2:" + PRECOMMITMENT_SHA256

#: The frozen P-Q v2.1 authority ids (``pqv2_core.CURRENT_AUTHORITY``).
PQ_METHODOLOGY = _evaluation.PQ_METHODOLOGY
PQ_METHODOLOGY_SHA256 = _core.METHODOLOGY_SHA256
PQ_CS_ID = _core.CS_ID
PQ_ED_ID = _core.ED_ID
PQ_CE_ID = _core.CE_ID

#: The Campaign-2 hypothesis-id namespace: ``H("campaign2", definition_hash)``.
HYPOTHESIS_ID_DOMAIN = "campaign2"

PRIMARY_UNIVERSE = "TOP_500"
CELL_NAMES: tuple[str, ...] = ("B-T0", "B-TS", "L-T0", "L-TS")

MIN_PERIODS_IS = 24
MIN_PERIODS_OOS = 12

#: Family size m = 7, family alpha = 0.05 (precommitment section 3).
FAMILY_BUDGET_M = 7
FAMILY_ALPHA = 0.05
ALPHA_PER_TEST = FAMILY_ALPHA / FAMILY_BUDGET_M
CRITICAL_VALUE = float(norm.ppf(1.0 - ALPHA_PER_TEST))
if abs(CRITICAL_VALUE - 2.44999766060273) >= 1e-12:
    raise AssertionError(
        f"critical value {CRITICAL_VALUE!r} != 2.44999766060273"
    )

BUDGET_LIMITS: dict[str, int] = {
    "generation": 0,
    "proposal": 7,
    "experiment": 7,
    "diagnostic": 14,
    "statistical": 7,
}

#: ``C2-DEV-SCREEN-v1`` exactly as precommitment section 4.
SCREEN_POLICY_JSON: dict[str, Any] = {
    "basis": "NET",
    "cells": ["B-T0", "B-TS", "L-T0", "L-TS"],
    "direction": "POSITIVE",
    "estimand": "MEAN_NET_LONG_SHORT",
    "is_rule": {
        "comparison": ">=",
        "critical_value_formula": "Phi^-1(1 - family_alpha/family_budget_m)",
        "distribution": "ASYMPTOTIC_STANDARD_NORMAL_ONE_SIDED",
        "family_alpha": 0.05,
        "family_budget_m": 7,
        "minimum_available_periods": 24,
        "statistic": (
            "economic HAC t of the net long-short mean (B cells: unmodified "
            "D7b/Phase-7 NW t; L cells: the P-Q v2.1 L statistic, the same "
            "Phase-7 estimator on B's period set)"
        ),
    },
    "metric_key": "turnover_cost_adjusted",
    "not_assessed": (
        "any required cell NOT_ASSESSED or below minimum periods -> screen "
        "verdict None (NOT_ASSESSED); DEVELOPMENT_SCREEN_PASS impossible"
    ),
    "oos_rule": {
        "comparison": ">",
        "minimum_available_periods": 12,
        "statistic": (
            "arithmetic mean of available net long-short periods (L cells: "
            "over B's periods; any NOT_ASSESSED period -> None)"
        ),
        "threshold": 0.0,
    },
    "pass_requires": (
        "IS rule in all four cells AND OOS rule in all four cells AND "
        "CORRECTION_SENSITIVITY_SCREEN_PASS in both T0 and TS for both IS "
        "and OOS"
    ),
    "screen_id": "C2-DEV-SCREEN-v1",
}
SCREEN_ID = "C2-DEV-SCREEN-v1"
SCREEN_POLICY_SHA256 = hashlib.sha256(
    canonical_json(SCREEN_POLICY_JSON).encode("utf-8")
).hexdigest()

EXPECTED_LABELS = _c1.EXPECTED_LABELS
FACTOR_SPECS: tuple[FactorSpec, ...] = _c1.FACTOR_SPECS

#: Reused E4 hypothesis-definition helpers (identical definitions).
hypothesis_definition = _c1.hypothesis_definition
hypothesis_definition_hash = _c1.hypothesis_definition_hash
build_partition = _c1.build_partition
load_factor_specs = _c1.load_factor_specs

# ---------------------------------------------------------------------------
# Re-use disclosure (binding; precommitment section 2). Rendered verbatim.
# ---------------------------------------------------------------------------

REUSE_DISCLOSURE = (
    "RE-USE DISCLOSURE (binding). The 7 hypotheses and the IS/OOS folds are "
    "the same as Campaign-1's. A planner audit on 2026-10-08, structural only, "
    "found that Campaign-1's report.json and report.md expose no outcome "
    "statistics (the only number is the precommitted critical value), but its "
    "ledger.jsonl contains IS classification floats (economic_b_t0, "
    "economic_b_ts, economic_a_*). Those B-cell values exist in "
    "machine-readable form and it cannot be certified that they were never "
    "viewed. Campaign-2 is therefore not an independent test of these "
    "hypotheses. Its results must always be reported together with this "
    "disclosure."
)

# ---------------------------------------------------------------------------
# Typed errors / stop reasons
# ---------------------------------------------------------------------------


class C2CampaignError(Exception):
    """Base class for every Campaign-2 fail-closed error."""


class C2BindingError(C2CampaignError):
    """A Campaign, hypothesis, attempt or authority binding does not match."""


class C2GovernanceConflict(C2CampaignError):
    """A locked governance field changed, or the K-PIT order is invalid."""


class C2SealedFoldError(C2CampaignError):
    """A declaration was requested for the DEVELOPMENT_SEALED_FOLD."""


class C2Stop(C2CampaignError):
    """A typed Campaign stop (carries the frozen :class:`C2StopReason`)."""

    def __init__(self, reason: "C2StopReason") -> None:
        self.reason = reason
        super().__init__(reason.value)


class C2BudgetExhausted(C2Stop):
    """A budget counter would be exceeded."""


class _StageAFailure(Exception):
    """Internal: a post-open Stage-A identity failure (E3.1)."""

    def __init__(self, reason: "C2StopReason") -> None:
        self.reason = reason
        super().__init__(reason.value)


class C2StopReason(str, Enum):
    """The frozen Campaign stop vocabulary (precommitment section 7)."""

    PROPOSAL_BUDGET_EXHAUSTED = "PROPOSAL_BUDGET_EXHAUSTED"
    EXPERIMENT_BUDGET_EXHAUSTED = "EXPERIMENT_BUDGET_EXHAUSTED"
    STATISTICAL_BUDGET_EXHAUSTED = "STATISTICAL_BUDGET_EXHAUSTED"
    ALL_PRECOMMITTED_HYPOTHESES_EVALUATED = "ALL_PRECOMMITTED_HYPOTHESES_EVALUATED"
    INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE = (
        "INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE"
    )
    GOVERNANCE_CONFLICT = "GOVERNANCE_CONFLICT"
    RESERVE_OR_FIREWALL_VIOLATION = "RESERVE_OR_FIREWALL_VIOLATION"
    SEALED_FOLD_ACCESS_ATTEMPT = "SEALED_FOLD_ACCESS_ATTEMPT"


PHASE9_STOP_REASON_MAP: dict[C2StopReason, StopReason] = {
    C2StopReason.PROPOSAL_BUDGET_EXHAUSTED: StopReason.PROPOSAL_BUDGET_EXHAUSTED,
    C2StopReason.STATISTICAL_BUDGET_EXHAUSTED: (
        StopReason.STATISTICAL_BUDGET_EXHAUSTED
    ),
    C2StopReason.GOVERNANCE_CONFLICT: StopReason.GOVERNANCE_CONFLICT,
    C2StopReason.SEALED_FOLD_ACCESS_ATTEMPT: StopReason.HOLDOUT_FIREWALL_VIOLATION,
}

DISPOSITION_NOT_ASSESSED = "NOT_ASSESSED"
DISPOSITION_FAIL = "DEVELOPMENT_SCREEN_FAIL"
DISPOSITION_PASS = "DEVELOPMENT_SCREEN_PASS"

REPLAY_IDENTICAL = "REPLAY_IDENTICAL"

#: Cause-set / reason vocabulary (precommitment section 5).
NOT_ASSESSED_REASONS = (
    "BINDING_OR_IDENTITY_FAILURE",
    "PQ_NOT_ASSESSED",
    "SCREEN_VERDICT_NONE",
)
FAIL_REASONS = (
    "QUARANTINE_SENSITIVE",
    "INVARIANT_VIOLATION_FAIL_CLOSED",
    "DEVELOPMENT_SCREEN_FAIL",
)

#: Classification statuses (P-Q v2.1 M section 14), re-exported as literals.
STATUS_NOT_ASSESSED = _core.ClassificationStatus.NOT_ASSESSED
STATUS_PASS = _core.ClassificationStatus.CORRECTION_SENSITIVITY_SCREEN_PASS
STATUS_QUARANTINE = _core.ClassificationStatus.QUARANTINE_SENSITIVE
STATUS_INVARIANT = _core.ClassificationStatus.INVARIANT_VIOLATION_FAIL_CLOSED
STATUS_FAIL = _core.ClassificationStatus.DEVELOPMENT_SCREEN_FAIL


class Disposition(str, Enum):
    """The three frozen development dispositions."""

    NOT_ASSESSED = DISPOSITION_NOT_ASSESSED
    DEVELOPMENT_SCREEN_FAIL = DISPOSITION_FAIL
    DEVELOPMENT_SCREEN_PASS = DISPOSITION_PASS


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Campaign2Bindings:
    """The read-only authority bindings hashed into ``campaign2_id``."""

    dataset_id: str
    dataset_identity_hash: str
    pq_id: str
    pq_hash: str
    ti_id: str
    ti_hash: str
    ti_manifest_sha256: str
    d7a_artifact_id: str
    d7a_returns_hash: str
    partition_id: str


@dataclass(frozen=True)
class BudgetLimits:
    """The frozen, non-substitutable budget limits."""

    generation: int = 0
    proposal: int = 7
    experiment: int = 7
    diagnostic: int = 14
    statistical: int = 7

    def to_dict(self) -> dict[str, int]:
        return {
            "generation": self.generation,
            "proposal": self.proposal,
            "experiment": self.experiment,
            "diagnostic": self.diagnostic,
            "statistical": self.statistical,
        }


@dataclass
class BudgetCounters:
    """Independent, non-substitutable budget accounting."""

    limits: BudgetLimits = field(default_factory=BudgetLimits)
    generation_used: int = 0
    proposal_used: int = 0
    experiment_used: int = 0
    diagnostic_used: int = 0
    statistical_slots_used: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "generation_used": self.generation_used,
            "proposal_used": self.proposal_used,
            "experiment_used": self.experiment_used,
            "diagnostic_used": self.diagnostic_used,
            "statistical_slots_used": self.statistical_slots_used,
        }

    def check_primary(self) -> None:
        if self.proposal_used >= self.limits.proposal:
            raise C2BudgetExhausted(C2StopReason.PROPOSAL_BUDGET_EXHAUSTED)
        if self.experiment_used >= self.limits.experiment:
            raise C2BudgetExhausted(C2StopReason.EXPERIMENT_BUDGET_EXHAUSTED)
        if self.statistical_slots_used >= self.limits.statistical:
            raise C2BudgetExhausted(C2StopReason.STATISTICAL_BUDGET_EXHAUSTED)

    def consume_primary(self) -> None:
        self.proposal_used += 1
        self.experiment_used += 1
        self.statistical_slots_used += 1

    def check_diagnostic(self) -> None:
        if self.diagnostic_used >= self.limits.diagnostic:
            raise C2BudgetExhausted(C2StopReason.GOVERNANCE_CONFLICT)

    def consume_diagnostic(self) -> None:
        self.diagnostic_used += 1

    def request_generation(self) -> None:
        raise C2BudgetExhausted(C2StopReason.GOVERNANCE_CONFLICT)


@dataclass(frozen=True)
class KpitPreflight:
    """The read-only Knowledge-PIT ordering preflight result."""

    ok: bool
    reason: str | None
    record_hashes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Canonical helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "to_dict"):
        return _json_safe(value.to_dict())
    raise TypeError(f"cannot canonicalise {type(value).__name__}")


def _decision_kind(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("decision_kind")
    if hasattr(value, "value"):
        value = value.value
    return value if isinstance(value, str) else None


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "value") and not isinstance(value, str):
        return value.value
    return value


def _ordered_unique(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


# ---------------------------------------------------------------------------
# Frozen constants / estimand policy
# ---------------------------------------------------------------------------


def verify_precommitment(path: Path | str = PRECOMMITMENT_PATH) -> str:
    """Verify the Campaign-2 precommitment file's SHA-256; fail closed."""
    digest = _sha256_file(path)
    if digest != PRECOMMITMENT_SHA256:
        raise C2BindingError(
            f"precommitment hash {digest} != bound {PRECOMMITMENT_SHA256}"
        )
    return digest


def _assert_screen_policy() -> None:
    digest = hashlib.sha256(
        canonical_json(SCREEN_POLICY_JSON).encode("utf-8")
    ).hexdigest()
    if digest != SCREEN_POLICY_SHA256:
        raise C2BindingError(
            f"screen policy hash {digest} != bound {SCREEN_POLICY_SHA256}"
        )


SESOI = 0.0025
SESOI_JUSTIFICATION_TEXT = (
    "C2 estimand policy: 0.0025 net long-short return per 21-session holding "
    "period (about 3.0% annualized), the smallest net quintile long-short "
    "spread on the D6 top-500 equal-weighted universe, after 10 bps Phase-7 "
    "transaction cost per unit of membership turnover, that Campaign-2 treats "
    "as economically meaningful; the Campaign-1 SESOI, fixed before any "
    "Campaign-1 empirical evaluation and unchanged for Campaign-2."
)
SESOI_JUSTIFICATION_HASH = hashlib.sha256(
    SESOI_JUSTIFICATION_TEXT.encode("utf-8")
).hexdigest()

ESTIMAND_CONSTRUCTION: dict[str, Any] = {
    "n_groups": 5,
    "cost_bps": 10.0,
    "winsorization": 0.01,
    "factor_missing_policy": "PROPAGATE",
}
HORIZON = 21
N_GROUPS = 5
WINSORIZATION = 0.01
TRANSACTION_COST_BPS = 10.0
PERIODS_PER_YEAR = 12
WEIGHTING = "EQUAL"
PRIMARY_METRIC = "turnover_cost_adjusted"
BASIS = "NET"


def estimand_policy() -> EstimandPolicy:
    """The single frozen Campaign-2 program :class:`EstimandPolicy`."""
    return EstimandPolicy(
        program_id=PROGRAM_ID,
        estimand_kind=EstimandKind.MEAN_NET_LONG_SHORT,
        horizon=HORIZON,
        construction=dict(ESTIMAND_CONSTRUCTION),
        sesoi=SESOI,
        sesoi_justification_hash=SESOI_JUSTIFICATION_HASH,
    )


def estimand_policy_content() -> dict[str, Any]:
    """The canonical ESTIMAND_POLICY content recorded at open."""
    return estimand_policy().to_content()


_assert_screen_policy()
verify_precommitment()

#: The expected hypothesis ids are computed from the frozen inputs (the real
#: D7c manifest) by :func:`_expected_hypothesis_ids` when first needed; a test
#: may monkeypatch this mapping with a synthetic-manifest fixture.
EXPECTED_HYPOTHESIS_DEFINITION_HASHES: dict[str, str] = {}
EXPECTED_HYPOTHESIS_IDS: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Identities
# ---------------------------------------------------------------------------


def input_manifest(bindings: Campaign2Bindings) -> dict[str, Any]:
    """The canonical Campaign-2 input manifest hashed into ``campaign2_id``."""
    return {
        "precommitment_sha256": PRECOMMITMENT_SHA256,
        "pq_methodology_sha256": PQ_METHODOLOGY_SHA256,
        "pq_cs_id": PQ_CS_ID,
        "pq_ed_id": PQ_ED_ID,
        "pq_ce_id": PQ_CE_ID,
        "dataset_id": bindings.dataset_id,
        "dataset_identity_hash": bindings.dataset_identity_hash,
        "pq_id": bindings.pq_id,
        "pq_hash": bindings.pq_hash,
        "ti_id": bindings.ti_id,
        "ti_hash": bindings.ti_hash,
        "ti_manifest_sha256": bindings.ti_manifest_sha256,
        "d7a_artifact_id": bindings.d7a_artifact_id,
        "d7a_returns_hash": bindings.d7a_returns_hash,
        "factorspecs_sha256": FACTORSPECS_SHA256,
        "partition_id": bindings.partition_id,
        "screen_id": SCREEN_ID,
        "screen_policy_sha256": SCREEN_POLICY_SHA256,
    }


def campaign2_id(bindings: Campaign2Bindings) -> str:
    """The frozen Campaign-2 identity (C2-LX section 1.1)."""
    return content_hash(input_manifest(bindings))


def campaign2_hypothesis_id(definition_hash: str) -> str:
    """``H("campaign2", definition_hash)`` (C2-LX section 1.1)."""
    return content_hash(
        {
            "domain": HYPOTHESIS_ID_DOMAIN,
            "hypothesis_definition_hash": definition_hash,
        }
    )


def _hypothesis_identities_for(
    factor_specs: Sequence[FactorSpec],
    *,
    root: Path | str,
    expected_ids: Mapping[str, str] | None,
) -> tuple[dict[str, str], ...]:
    identities: list[dict[str, str]] = []
    for spec in factor_specs:
        label = spec.hypothesis or spec.id
        definition = hypothesis_definition(spec, root=root)
        definition_hash = hypothesis_definition_hash(definition)
        hypothesis_id = campaign2_hypothesis_id(definition_hash)
        if expected_ids is not None and expected_ids.get(label) != hypothesis_id:
            raise C2BindingError(
                f"hypothesis_id {hypothesis_id} for {label} != bound "
                f"{expected_ids.get(label)}"
            )
        identities.append(
            {
                "label": label,
                "factor_spec_hash": factor_spec_hash(spec),
                "hypothesis_definition_hash": definition_hash,
                "hypothesis_id": hypothesis_id,
            }
        )
    return tuple(identities)


def hypothesis_identities(
    root: Path | str,
    *,
    factor_specs: Sequence[FactorSpec] | None = None,
    expected_ids: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], ...]:
    """The Campaign-2 definition-domain identities from the real D7c manifest."""
    specs = tuple(factor_specs) if factor_specs is not None else FACTOR_SPECS
    expected = EXPECTED_HYPOTHESIS_IDS if expected_ids is None else expected_ids
    return _hypothesis_identities_for(specs, root=root, expected_ids=expected)


def campaign_design_hash(
    declaration: CampaignEvaluationDeclaration,
    *,
    campaign2_id_value: str,
) -> str:
    """The D7b base declaration hash minus fold/panel plus the bindings."""
    body = dict(declaration.to_dict())
    body.pop("fold_key", None)
    body.pop("factor_panel_sha256", None)
    body["campaign2_id"] = campaign2_id_value
    body["pq_methodology_sha256"] = PQ_METHODOLOGY_SHA256
    body["pq_ed_id"] = PQ_ED_ID
    return content_hash(body)


def campaign_attempt_id(
    *,
    domain: str,
    campaign2_id_value: str,
    hypothesis_id: str,
    design_hash: str,
) -> str:
    """The distinct-domain Campaign attempt identity (C2-LX section 1.5)."""
    return content_hash(
        {
            "domain": domain,
            "campaign2_id": campaign2_id_value,
            "hypothesis_id": hypothesis_id,
            "campaign_design_hash": design_hash,
        }
    )


# ---------------------------------------------------------------------------
# Knowledge-PIT preflight (read-only; no append anywhere)
# ---------------------------------------------------------------------------


def verify_kpit_ordering(
    knowledge_log_path: Path | str,
    *,
    policy: EstimandPolicy | None = None,
    identities: Sequence[Mapping[str, str]] | None = None,
    program_id: str = PROGRAM_ID,
) -> KpitPreflight:
    """Verify the frozen Campaign-2 Knowledge-PIT ordering (C2-LX section 1.7)."""
    expected_policy = policy if policy is not None else estimand_policy()
    if identities is None:
        return KpitPreflight(False, "no hypothesis identities supplied")
    expected_pairs = sorted(
        (item["hypothesis_id"], item["factor_spec_hash"]) for item in identities
    )
    try:
        records = read_records(knowledge_log_path)
    except Exception as exc:  # pragma: no cover - defensive
        return KpitPreflight(False, f"knowledge log unreadable: {exc}")

    program_records = [rec for rec in records if rec.program_id == program_id]
    if not program_records:
        return KpitPreflight(False, "no program records")

    first = program_records[0]
    if first.kind is not RecordKind.HUMAN_DECISION or (
        _decision_kind(first.payload) != ESTIMAND_POLICY
    ):
        return KpitPreflight(False, "a program record precedes ESTIMAND_POLICY")

    estimand_records = [
        rec
        for rec in program_records
        if rec.kind is RecordKind.HUMAN_DECISION
        and _decision_kind(rec.payload) == ESTIMAND_POLICY
    ]
    if len(estimand_records) != 1:
        return KpitPreflight(False, "ESTIMAND_POLICY must appear exactly once")
    estimand_record = estimand_records[0]
    try:
        recorded_policy = estimand_policy_from_record(estimand_record)
    except Exception as exc:
        return KpitPreflight(False, f"invalid ESTIMAND_POLICY: {exc}")
    if _plain(recorded_policy) != _plain(expected_policy):
        return KpitPreflight(False, "ESTIMAND_POLICY content mismatch")

    freeze_records = [
        rec
        for rec in program_records
        if rec.kind is RecordKind.HUMAN_DECISION
        and _decision_kind(rec.payload) == "PROGRAM_FREEZE"
    ]
    if len(freeze_records) != 1:
        return KpitPreflight(False, "PROGRAM_FREEZE must appear exactly once")
    program_freeze_record = freeze_records[0]
    if program_freeze_record.seq <= estimand_record.seq:
        return KpitPreflight(False, "PROGRAM_FREEZE precedes ESTIMAND_POLICY")

    hyp_records = [
        rec for rec in program_records if rec.kind is RecordKind.HYPOTHESIS_FREEZE
    ]
    if len(hyp_records) != len(expected_pairs):
        return KpitPreflight(
            False, "HYPOTHESIS_FREEZE count does not match the family"
        )
    if any(record.seq <= program_freeze_record.seq for record in hyp_records):
        return KpitPreflight(False, "HYPOTHESIS_FREEZE precedes PROGRAM_FREEZE")
    actual_pairs = sorted(
        (
            str(rec.payload.get("hypothesis_id")),
            str(rec.payload.get("factor_spec_hash")),
        )
        for rec in hyp_records
    )
    if actual_pairs != expected_pairs:
        return KpitPreflight(False, "HYPOTHESIS_FREEZE identities do not match")
    required_refs = {
        estimand_record.record_hash,
        program_freeze_record.record_hash,
    }
    for record in hyp_records:
        if not required_refs.issubset(set(record.refs.get("influenced_by", ()))):
            return KpitPreflight(
                False, "HYPOTHESIS_FREEZE influenced_by is incomplete"
            )
    hashes = tuple(
        [estimand_record.record_hash, program_freeze_record.record_hash]
        + [record.record_hash for record in hyp_records]
    )
    return KpitPreflight(True, None, hashes)


# ---------------------------------------------------------------------------
# Declarations and the sealed-fold firewall
# ---------------------------------------------------------------------------


def assert_not_sealed_fold(fold: Fold) -> None:
    """The Campaign firewall: never construct a sealed-fold declaration."""
    if fold.role is FoldRole.HOLDOUT:
        raise C2SealedFoldError(
            "DEVELOPMENT_SEALED_FOLD is never read by Campaign-2"
        )
    if "holdout" in _fold_key(fold):
        raise C2SealedFoldError("holdout-role folds are sealed")


def build_declaration(
    *,
    spec: FactorSpec,
    panel: pd.DataFrame,
    fold: Fold,
    universe: str,
    bindings: Campaign2Bindings,
    screen_id: str = SCREEN_ID,
    screen_policy_hash: str = SCREEN_POLICY_SHA256,
) -> CampaignEvaluationDeclaration:
    """Build one D7b base declaration, refusing the sealed fold first."""
    assert_not_sealed_fold(fold)
    return CampaignEvaluationDeclaration(
        precommitment_ref=(
            "docs/campaign2_precommitment.md@" + PRECOMMITMENT_SHA256
        ),
        factor_spec_version=spec.version,
        factor_panel_sha256=_canonical_panel_hash(panel),
        partition_id=bindings.partition_id,
        fold_key=_fold_key(fold),
        universe=universe,
        horizon=HORIZON,
        n_groups=N_GROUPS,
        winsorization=WINSORIZATION,
        transaction_cost_bps=TRANSACTION_COST_BPS,
        periods_per_year=PERIODS_PER_YEAR,
        weighting=WEIGHTING,
        primary_metric=PRIMARY_METRIC,
        basis=BASIS,
        screen_id=screen_id,
        screen_policy_hash=screen_policy_hash,
    )


def wrap_declaration(
    declaration: CampaignEvaluationDeclaration,
    bindings: Campaign2Bindings,
) -> _evaluation.PQv2EvaluationDeclaration:
    """Wrap a D7b declaration with the frozen P-Q v2.1 identities."""
    return _evaluation.PQv2EvaluationDeclaration(
        base=declaration,
        dataset_id=bindings.dataset_id,
        dataset_identity_hash=bindings.dataset_identity_hash,
        pq_id=bindings.pq_id,
        pq_hash=bindings.pq_hash,
        methodology_sha256=PQ_METHODOLOGY_SHA256,
        cs_id=PQ_CS_ID,
        ed_id=PQ_ED_ID,
        ce_id=PQ_CE_ID,
    )


# ---------------------------------------------------------------------------
# Screen outcomes, cause sets and disposition
# ---------------------------------------------------------------------------


def is_assessed(cell: Any) -> bool:
    """A required P-Q v2.1 cell is assessed iff ``cell.status is None``."""
    if cell is None:
        return False
    return cell.status is None


def _screen_cell(cell: Any, fold: FoldRole) -> bool | None:
    if cell.status is not None:
        return None
    if fold is FoldRole.IS:
        if cell.t_stat is None or not math.isfinite(cell.t_stat):
            return None
        if cell.n_obs < MIN_PERIODS_IS:
            return None
        return bool(cell.t_stat >= CRITICAL_VALUE)
    if cell.value is None or not math.isfinite(cell.value):
        return None
    if cell.n_obs < MIN_PERIODS_OOS:
        return None
    return bool(cell.value > 0.0)


def _screen_cell_names() -> tuple[str, ...]:
    return ("B-T0", "B-TS", "L-T0", "L-TS")


def screen_outcomes(
    diagnostics: Any,
    fold: FoldRole,
) -> tuple[_evaluation.PQv2ScreenOutcome, ...]:
    """One :class:`PQv2ScreenOutcome` per P-Q v2.1 cell, IS or OOS."""
    outcomes: list[_evaluation.PQv2ScreenOutcome] = []
    for name in _screen_cell_names():
        cell = diagnostics.cell(name)
        if cell is None:
            raise C2BindingError(f"diagnostics missing required cell {name}")
        outcomes.append(
            _evaluation.PQv2ScreenOutcome(
                cell=name,
                passed=_screen_cell(cell, fold),
            )
        )
    return tuple(outcomes)


def _has_none_screen(screens: Sequence[Any]) -> bool:
    return any(screen.passed is None for screen in screens)


def cause_sets(diagnostics: Any, record: Any) -> dict[str, list[str]]:
    """The full persisted P-Q v2.1 cause set per scenario, verbatim.

    For every scenario the set is the ordered union of the whole-evaluation
    gate reason (if any), the classification's own ``not_assessed_reason`` and
    the B/L cell reasons plus the L-cell ``cause_union`` copied from the
    persisted diagnostics.
    """
    result: dict[str, list[str]] = {}
    for index, scenario in enumerate(record.scenario):
        causes: list[str] = []
        if diagnostics.not_assessed:
            causes.append(str(diagnostics.not_assessed))
        for name in (f"B-{scenario}", f"L-{scenario}"):
            cell = diagnostics.cell(name)
            if cell is None:
                continue
            if getattr(cell, "reason", None):
                causes.append(str(cell.reason))
            causes.extend(str(item) for item in getattr(cell, "cause_union", ()))
        reason = record.not_assessed_reason[index]
        if reason:
            causes.append(str(reason))
        result[scenario] = _ordered_unique(causes)
    return result


def _ordered_reasons(reasons: Sequence[str], canonical: Sequence[str]) -> list[str]:
    return [reason for reason in canonical if reason in set(reasons)]


def _four_statuses(
    classify_is: Any, classify_oos: Any
) -> tuple[str, str, str, str]:
    """The required {IS, OOS} x {T0, TS} classification statuses."""
    return (
        classify_is.status[0],
        classify_is.status[1],
        classify_oos.status[0],
        classify_oos.status[1],
    )


def attempt_disposition(
    *,
    diagnostics_is: Any,
    diagnostics_oos: Any,
    screens_is: Sequence[Any],
    screens_oos: Sequence[Any],
    classify_is: Any,
    classify_oos: Any,
) -> tuple[Disposition, list[str]]:
    """The frozen first-match disposition with every applicable reason.

    Precedence (precommitment section 5): NOT_ASSESSED before
    DEVELOPMENT_SCREEN_FAIL before DEVELOPMENT_SCREEN_PASS. A PASS requires
    ``CORRECTION_SENSITIVITY_SCREEN_PASS`` in both T0 and TS for both folds.
    """
    collected: list[str] = []
    not_assessed = False
    statuses = _four_statuses(classify_is, classify_oos)
    if any(status == STATUS_NOT_ASSESSED for status in statuses):
        collected.append("PQ_NOT_ASSESSED")
        not_assessed = True
    if _has_none_screen(screens_is) or _has_none_screen(screens_oos):
        collected.append("SCREEN_VERDICT_NONE")
        not_assessed = True
    if not_assessed:
        return (
            Disposition.NOT_ASSESSED,
            _ordered_reasons(collected, NOT_ASSESSED_REASONS),
        )

    fail_reasons: list[str] = []
    for status in statuses:
        if status == STATUS_QUARANTINE:
            fail_reasons.append("QUARANTINE_SENSITIVE")
        elif status == STATUS_INVARIANT:
            fail_reasons.append("INVARIANT_VIOLATION_FAIL_CLOSED")
        elif status == STATUS_FAIL:
            fail_reasons.append("DEVELOPMENT_SCREEN_FAIL")
    if fail_reasons:
        return (
            Disposition.DEVELOPMENT_SCREEN_FAIL,
            _ordered_reasons(fail_reasons, FAIL_REASONS),
        )
    return Disposition.DEVELOPMENT_SCREEN_PASS, []


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


@dataclass
class Campaign2RunResult:
    """The deterministic result of a Campaign-2 executor run."""

    stop_reason: C2StopReason
    campaign2_id: str | None
    counters: dict[str, int]
    ledger_path: str


class Campaign2Executor:
    """The C2-LX Campaign-2 development executor (P-Q v2.1 only).

    All real P-Q v2.1 / Phase-6 dependencies are injected so the executor is
    exercised offline with synthetic fixtures and the PQ2-H synthetic dataset
    fixture. ``panel_of`` returns the fold/universe Phase-6 panel;
    ``build_pqv2_diagnostics`` (module-level, monkeypatchable) performs the
    frozen P-Q v2.1 evaluation.
    """

    def __init__(
        self,
        *,
        ledger: CampaignLedger,
        bindings: Campaign2Bindings,
        partition: Partition,
        factor_specs: Sequence[FactorSpec] | None = None,
        panel_of: Callable[[FactorSpec, str, Fold], pd.DataFrame] | None = None,
        overlay: Any = None,
        root: Path | str = ".",
        inputs: Mapping[str, Any] | None = None,
        d7b_bindings: Any = None,
        source_bindings: Any = None,
        pq_bindings: Any = None,
        pq_read_records_fn: Any = None,
        knowledge_log_path: Path | str | None = None,
        labels: Sequence[str] | None = None,
        require_kpit: bool = True,
    ) -> None:
        self.ledger = ledger
        self.bindings = bindings
        self.partition = partition
        self.factor_specs = tuple(
            factor_specs if factor_specs is not None else FACTOR_SPECS
        )
        self.panel_of = panel_of
        self.overlay = overlay
        self.root = Path(root)
        self.inputs = None if inputs is None else dict(inputs)
        self.d7b_bindings = d7b_bindings
        self.source_bindings = source_bindings
        self.pq_bindings = pq_bindings
        self.pq_read_records_fn = pq_read_records_fn
        self.knowledge_log_path = (
            Path(knowledge_log_path) if knowledge_log_path is not None else None
        )
        self.labels = tuple(labels) if labels is not None else EXPECTED_LABELS
        self.require_kpit = require_kpit
        self.counters = BudgetCounters(limits=BudgetLimits(**BUDGET_LIMITS))
        self._attempt_ordinal = 0
        self._spec_by_label = {
            (spec.hypothesis or spec.id): spec for spec in self.factor_specs
        }

    # -- public ----------------------------------------------------------
    def run(self) -> Campaign2RunResult:
        # E3.2: a ledger containing CAMPAIGN_STOP is terminal.
        recorded_stop = self._existing_stop()
        if recorded_stop is not None:
            self.ledger.verify()
            self._replay_counters()
            return Campaign2RunResult(
                stop_reason=recorded_stop,
                campaign2_id=self._existing_campaign2_id(),
                counters=self.counters.to_dict(),
                ledger_path=str(self.ledger.path),
            )

        self._replay_counters()

        # E3.1 pre-open Stage-A: no CAMPAIGN_OPEN yet, so write nothing.
        try:
            identities = _hypothesis_identities_for(
                self.factor_specs,
                root=self.root,
                expected_ids=EXPECTED_HYPOTHESIS_IDS or None,
            )
        except C2BindingError:
            return self._stage_a_result(
                C2StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
            )

        # C2-LX section 1.7: the read-only K-PIT preflight requires the 9
        # Campaign-2 registration records. The executor never writes K-PIT.
        if self.require_kpit:
            if self.knowledge_log_path is None:
                return self._stage_a_result(C2StopReason.GOVERNANCE_CONFLICT)
            preflight = verify_kpit_ordering(
                self.knowledge_log_path,
                policy=estimand_policy(),
                identities=identities,
            )
        else:
            preflight = KpitPreflight(True, None, ())
        if not preflight.ok:
            return self._stage_a_result(C2StopReason.GOVERNANCE_CONFLICT)

        try:
            campaign_id = self._open_campaign(preflight, identities)
        except C2GovernanceConflict:
            return self._stage_a_result(C2StopReason.GOVERNANCE_CONFLICT)
        except C2BindingError:
            return self._stage_a_result(
                C2StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
            )

        try:
            for label in self.labels:
                stop = self._process_label(label, campaign_id)
                if stop is None or stop == REPLAY_IDENTICAL:
                    continue
                return self._stop(stop, campaign_id)
        except _StageAFailure as failure:
            return self._stage_a_result(failure.reason)
        except _d7b.ReserveFirewallError:
            return self._stop(
                C2StopReason.RESERVE_OR_FIREWALL_VIOLATION, campaign_id
            )
        except C2SealedFoldError:
            return self._stop(C2StopReason.SEALED_FOLD_ACCESS_ATTEMPT, campaign_id)
        except C2GovernanceConflict:
            return self._stop(C2StopReason.GOVERNANCE_CONFLICT, campaign_id)
        except (C2BindingError, _d7b.D7bBindingError, _core.PQv2Error):
            return self._stop(
                C2StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE,
                campaign_id,
            )
        except C2Stop as stop:
            return self._stop(stop.reason, campaign_id)
        except C1ReplayMismatchError:
            raise
        except Exception:
            self._append_stop(
                C2StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
            )
            raise

        return self._stop(
            C2StopReason.ALL_PRECOMMITTED_HYPOTHESES_EVALUATED, campaign_id
        )

    # -- state helpers ---------------------------------------------------
    def _ledger_is_post_open(self) -> bool:
        return bool(self.ledger.records("CAMPAIGN_OPEN"))

    def _stage_a_result(self, reason: C2StopReason) -> Campaign2RunResult:
        """E3.1: pre-open writes nothing; post-open appends one CAMPAIGN_STOP."""
        if self._ledger_is_post_open():
            self._append_stop(reason)
        return Campaign2RunResult(
            stop_reason=reason,
            campaign2_id=self._existing_campaign2_id(),
            counters=self.counters.to_dict(),
            ledger_path=str(self.ledger.path),
        )

    def _open_campaign(
        self,
        preflight: KpitPreflight,
        identities: Sequence[Mapping[str, str]],
    ) -> str:
        campaign_id = campaign2_id(self.bindings)
        partition_id = self.partition.partition_id
        if partition_id != self.bindings.partition_id:
            raise C2BindingError(
                f"partition_id {partition_id} != bound "
                f"{self.bindings.partition_id}"
            )
        record = {
            "record_type": "CAMPAIGN_OPEN",
            "program_id": PROGRAM_ID,
            "campaign2_id": campaign_id,
            "input_manifest": input_manifest(self.bindings),
            "pq_identities": {
                "methodology_sha256": PQ_METHODOLOGY_SHA256,
                "cs_id": PQ_CS_ID,
                "ed_id": PQ_ED_ID,
                "ce_id": PQ_CE_ID,
                "pq_methodology": PQ_METHODOLOGY,
            },
            "hypothesis_ids": {
                str(item["label"]): str(item["hypothesis_id"])
                for item in identities
            },
            "budgets": dict(BUDGET_LIMITS),
            "critical_value": CRITICAL_VALUE,
            "critical_value_repr": repr(CRITICAL_VALUE),
            "estimand_policy": estimand_policy_content(),
            "kpit_preflight_record_hashes": list(preflight.record_hashes),
        }
        existing = self.ledger.records("CAMPAIGN_OPEN")
        if existing:
            if not _lock_matches(existing[0], record):
                raise C2GovernanceConflict(
                    "CAMPAIGN_OPEN family fields changed for an existing ledger"
                )
            return campaign_id
        self.ledger.append(_json_safe(record))
        return campaign_id

    # -- per-hypothesis --------------------------------------------------
    def _hypothesis_identity(
        self, spec: FactorSpec, label: str
    ) -> tuple[str, str]:
        definition = hypothesis_definition(spec, root=self.root)
        definition_hash = hypothesis_definition_hash(definition)
        hypothesis_id = campaign2_hypothesis_id(definition_hash)
        expected = EXPECTED_HYPOTHESIS_IDS.get(label)
        if expected is not None and expected != hypothesis_id:
            raise C2BindingError(
                f"hypothesis_id {hypothesis_id} for {label} != bound {expected}"
            )
        return definition_hash, hypothesis_id

    def _process_label(
        self, label: str, campaign_id: str
    ) -> C2StopReason | str | None:
        if label not in self._spec_by_label:
            raise C2GovernanceConflict(f"proposal {label!r} is not precommitted")
        spec = self._spec_by_label[label]
        try:
            _, hypothesis_id = self._hypothesis_identity(spec, label)
        except C2BindingError as exc:
            raise _StageAFailure(
                C2StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
            ) from exc

        is_fold = _fold(self.partition, FoldRole.IS)
        oos_fold = _fold(self.partition, FoldRole.OOS)
        declaration_is = _design_declaration(
            spec=spec,
            fold=is_fold,
            universe=PRIMARY_UNIVERSE,
            bindings=self.bindings,
        )
        design_hash = campaign_design_hash(
            declaration_is, campaign2_id_value=campaign_id
        )
        attempt_id = campaign_attempt_id(
            domain="campaign2-development-attempt",
            campaign2_id_value=campaign_id,
            hypothesis_id=hypothesis_id,
            design_hash=design_hash,
        )

        existing = self.ledger.find_attempt(attempt_id)
        if existing is None:
            self.counters.check_primary()

        self._attempt_ordinal += 1
        slot_index = self._attempt_ordinal
        return self._run_primary(
            spec=spec,
            label=label,
            hypothesis_id=hypothesis_id,
            campaign_id=campaign_id,
            design_hash=design_hash,
            attempt_id=attempt_id,
            slot_index=slot_index,
            is_fold=is_fold,
            oos_fold=oos_fold,
            existing=existing,
        )

    def _run_primary(
        self,
        *,
        spec: FactorSpec,
        label: str,
        hypothesis_id: str,
        campaign_id: str,
        design_hash: str,
        attempt_id: str,
        slot_index: int,
        is_fold: Fold,
        oos_fold: Fold,
        existing: Mapping[str, Any] | None,
    ) -> C2StopReason | str | None:
        try:
            diag_is, diag_oos, record = self._primary_payload(
                spec=spec,
                label=label,
                hypothesis_id=hypothesis_id,
                campaign_id=campaign_id,
                design_hash=design_hash,
                attempt_id=attempt_id,
                slot_index=slot_index,
                is_fold=is_fold,
                oos_fold=oos_fold,
            )
        except (C2BindingError, _d7b.D7bBindingError, _core.PQv2Error):
            return self._stage_b_attempt(
                existing=existing,
                label=label,
                hypothesis_id=hypothesis_id,
                design_hash=design_hash,
                attempt_id=attempt_id,
                slot_index=slot_index,
            )

        # E6.4 write-ahead: both artifacts are durable before the record that
        # references them is appended.
        self._persist_diagnostics(diag_is)
        self._persist_diagnostics(diag_oos)
        if existing is not None:
            if canonical_json(existing) != canonical_json(_json_safe(record)):
                raise C1ReplayMismatchError(
                    "a re-run of a recorded attempt produced a different payload"
                )
            return REPLAY_IDENTICAL
        self.ledger.append(_json_safe(record))
        self.counters.consume_primary()
        return None

    def _stage_b_attempt(
        self,
        *,
        existing: Mapping[str, Any] | None,
        label: str,
        hypothesis_id: str,
        design_hash: str,
        attempt_id: str,
        slot_index: int,
    ) -> C2StopReason | str | None:
        record = {
            "record_type": "PRIMARY_ATTEMPT",
            "campaign2_id": None,
            "campaign_attempt_id": attempt_id,
            "hypothesis_id": hypothesis_id,
            "label": label,
            "universe": PRIMARY_UNIVERSE,
            "slot_index": slot_index,
            "alpha": ALPHA_PER_TEST,
            "critical_value": CRITICAL_VALUE,
            "campaign_design_hash": design_hash,
            "failure_detail": "BINDING_OR_IDENTITY_FAILURE",
            "declarations": {"is": None, "oos": None},
            "diagnostics": {"is": None, "oos": None},
            "screens": {"is": [], "oos": []},
            "classifications": {"is": None, "oos": None},
            "disposition": DISPOSITION_NOT_ASSESSED,
            "reasons": ["BINDING_OR_IDENTITY_FAILURE"],
            "cause_sets": {},
        }
        if existing is not None:
            if canonical_json(existing) != canonical_json(_json_safe(record)):
                raise C1ReplayMismatchError(
                    "stage-B replay produced a different record"
                )
            return C2StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
        self.ledger.append(_json_safe(record))
        self.counters.consume_primary()
        return C2StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE

    def _primary_payload(
        self,
        *,
        spec: FactorSpec,
        label: str,
        hypothesis_id: str,
        campaign_id: str,
        design_hash: str,
        attempt_id: str,
        slot_index: int,
        is_fold: Fold,
        oos_fold: Fold,
    ) -> tuple[Any, Any, dict[str, Any]]:
        panel_is = self.panel_of(spec, PRIMARY_UNIVERSE, is_fold)
        panel_oos = self.panel_of(spec, PRIMARY_UNIVERSE, oos_fold)
        decl_is = self._pq_declaration(spec, panel_is, is_fold, PRIMARY_UNIVERSE)
        decl_oos = self._pq_declaration(spec, panel_oos, oos_fold, PRIMARY_UNIVERSE)
        diag_is = self._build_diagnostics(spec, decl_is, panel_is)
        diag_oos = self._build_diagnostics(spec, decl_oos, panel_oos)
        screens_is = screen_outcomes(diag_is, FoldRole.IS)
        screens_oos = screen_outcomes(diag_oos, FoldRole.OOS)
        classify_is = classify_pqv2(diag_is, screens_is)
        classify_oos = classify_pqv2(diag_oos, screens_oos)
        disposition, reasons = attempt_disposition(
            diagnostics_is=diag_is,
            diagnostics_oos=diag_oos,
            screens_is=screens_is,
            screens_oos=screens_oos,
            classify_is=classify_is,
            classify_oos=classify_oos,
        )
        record = {
            "record_type": "PRIMARY_ATTEMPT",
            "campaign2_id": campaign_id,
            "campaign_attempt_id": attempt_id,
            "hypothesis_id": hypothesis_id,
            "label": label,
            "universe": PRIMARY_UNIVERSE,
            "slot_index": slot_index,
            "alpha": ALPHA_PER_TEST,
            "critical_value": CRITICAL_VALUE,
            "campaign_design_hash": design_hash,
            "failure_detail": None,
            "declarations": {
                "is": decl_is.declaration_hash,
                "oos": decl_oos.declaration_hash,
            },
            "diagnostics": {
                "is": diag_is.diagnostics_hash,
                "oos": diag_oos.diagnostics_hash,
            },
            "screens": {
                "is": [
                    {"fold": "is", **screen.to_dict()}
                    for screen in screens_is
                ],
                "oos": [
                    {"fold": "oos", **screen.to_dict()}
                    for screen in screens_oos
                ],
            },
            "classifications": {
                "is": classify_is.to_dict(),
                "oos": classify_oos.to_dict(),
            },
            "disposition": disposition.value,
            "reasons": reasons,
            "cause_sets": {
                "is": cause_sets(diag_is, classify_is),
                "oos": cause_sets(diag_oos, classify_oos),
            },
        }
        return diag_is, diag_oos, record

    def _pq_declaration(
        self,
        spec: FactorSpec,
        panel: pd.DataFrame,
        fold: Fold,
        universe: str,
    ) -> _evaluation.PQv2EvaluationDeclaration:
        declaration = build_declaration(
            spec=spec,
            panel=panel,
            fold=fold,
            universe=universe,
            bindings=self.bindings,
        )
        return wrap_declaration(declaration, self.bindings)

    def _build_diagnostics(
        self,
        spec: FactorSpec,
        declaration: _evaluation.PQv2EvaluationDeclaration,
        panel: pd.DataFrame,
    ) -> Any:
        return build_pqv2_diagnostics(
            self.root,
            declaration,
            spec,
            panel,
            self.partition,
            overlay=self.overlay,
            inputs=self.inputs,
            bindings=self.d7b_bindings,
            source_bindings=self.source_bindings,
            pq_bindings=self.pq_bindings,
            pq_read_records_fn=self.pq_read_records_fn,
        )

    # -- E6 write-once diagnostics artifacts ----------------------------
    def _diagnostics_dir(self) -> Path:
        return Path(self.ledger.path).parent / _store.DIAGNOSTICS_DIRNAME

    def _persist_diagnostics(self, diagnostics: Any) -> None:
        directory = self._diagnostics_dir()
        _store.persist(diagnostics, directory)
        attached = getattr(diagnostics, "b_diagnostics", None)
        if attached is not None:
            _store.persist(attached, directory)

    # -- stop / state helpers -------------------------------------------
    def _existing_stop(self) -> C2StopReason | None:
        stops = self.ledger.records("CAMPAIGN_STOP")
        if not stops:
            return None
        value = stops[-1].get("stop_reason")
        try:
            return C2StopReason(value)
        except ValueError:  # pragma: no cover - defensive
            return C2StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE

    def _existing_campaign2_id(self) -> str | None:
        opens = self.ledger.records("CAMPAIGN_OPEN")
        if not opens:
            return None
        return opens[0].get("campaign2_id")

    def _replay_counters(self) -> None:
        attempts = len(self.ledger.records("PRIMARY_ATTEMPT"))
        self.counters.proposal_used = attempts
        self.counters.experiment_used = attempts
        self.counters.statistical_slots_used = attempts
        self.counters.diagnostic_used = 0
        opens = self.ledger.records("CAMPAIGN_OPEN")
        if opens:
            self._attempt_ordinal = 0

    def _append_stop(self, reason: C2StopReason) -> None:
        self.ledger.append(
            _json_safe(
                {
                    "record_type": "CAMPAIGN_STOP",
                    "stop_reason": reason.value,
                    "budget_counters": self.counters.to_dict(),
                }
            )
        )

    def _stop(
        self, reason: C2StopReason, campaign_id: str
    ) -> Campaign2RunResult:
        self._append_stop(reason)
        return Campaign2RunResult(
            stop_reason=reason,
            campaign2_id=campaign_id,
            counters=self.counters.to_dict(),
            ledger_path=str(self.ledger.path),
        )


def _fold(partition: Partition, role: FoldRole) -> Fold:
    for fold in partition.folds:
        if fold.role is role:
            return fold
    raise C2BindingError(f"partition has no {role.value} fold")


def _lock_matches(existing: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    for key in (
        "pq_identities",
        "budgets",
        "critical_value_repr",
        "program_id",
        "campaign2_id",
        "estimand_policy",
    ):
        if existing.get(key) != current.get(key):
            return False
    return True


def _design_declaration(
    *,
    spec: FactorSpec,
    fold: Fold,
    universe: str,
    bindings: Campaign2Bindings,
) -> CampaignEvaluationDeclaration:
    """A metadata-only declaration used to derive ``campaign_design_hash``.

    ``campaign_design_hash`` excludes ``fold_key`` and ``factor_panel_sha256``
    (C2-LX section 1.5), so this carries a placeholder panel hash. No factor is
    evaluated and no panel is built, so attempt identity establishment cannot
    depend on any evaluation.
    """
    assert_not_sealed_fold(fold)
    return CampaignEvaluationDeclaration(
        precommitment_ref=(
            "docs/campaign2_precommitment.md@" + PRECOMMITMENT_SHA256
        ),
        factor_spec_version=spec.version,
        factor_panel_sha256="0" * 64,
        partition_id=bindings.partition_id,
        fold_key=_fold_key(fold),
        universe=universe,
        horizon=HORIZON,
        n_groups=N_GROUPS,
        winsorization=WINSORIZATION,
        transaction_cost_bps=TRANSACTION_COST_BPS,
        periods_per_year=PERIODS_PER_YEAR,
        weighting=WEIGHTING,
        primary_metric=PRIMARY_METRIC,
        basis=BASIS,
        screen_id=SCREEN_ID,
        screen_policy_hash=SCREEN_POLICY_SHA256,
    )


# ``build_pqv2_diagnostics`` / ``classify_pqv2`` are module-level bindings so the
# synthetic end-to-end tests can monkeypatch them.
build_pqv2_diagnostics = _evaluation.build_pqv2_diagnostics
classify_pqv2 = _evaluation.classify_pqv2


# ---------------------------------------------------------------------------
# Deterministic report
# ---------------------------------------------------------------------------

_CLAIM_BOUNDARY = (
    "DEVELOPMENT_SCREEN_PASS means only that the precommitted development "
    "screen was satisfied. It is not confirmation, not holdout validation, "
    "not confirmation authorization, not production readiness and not "
    "investability. No result in this report licenses reserve access or any "
    "final-holdout consumption."
)


def render_report(ledger_path: Path | str) -> tuple[bytes, bytes]:
    """Render the deterministic (json, markdown) Campaign-2 report."""
    ledger = CampaignLedger(ledger_path)
    opens = ledger.records("CAMPAIGN_OPEN")
    attempts = ledger.records("PRIMARY_ATTEMPT")
    stops = ledger.records("CAMPAIGN_STOP")

    by_label: dict[str, Mapping[str, Any]] = {
        str(record.get("label")): record for record in attempts
    }

    required_classifications = len(EXPECTED_LABELS) * 2 * 2
    non_not_assessed = 0
    non_not_assessed_hypotheses = 0
    hypotheses: list[dict[str, Any]] = []
    for spec in FACTOR_SPECS:
        label = spec.hypothesis or spec.id
        attempt = by_label.get(label)
        classifications = (
            None if attempt is None else attempt.get("classifications")
        )
        hypothesis_has_non_na = False
        for fold in ("is", "oos"):
            record = None if classifications is None else classifications.get(fold)
            if record is None:
                continue
            for status in record.get("status", ()):
                if status != STATUS_NOT_ASSESSED:
                    non_not_assessed += 1
                    hypothesis_has_non_na = True
        disposition = None if attempt is None else attempt.get("disposition")
        if disposition is not None and disposition != DISPOSITION_NOT_ASSESSED:
            non_not_assessed_hypotheses += 1
        hypotheses.append(
            {
                "label": label,
                "hypothesis_id": (
                    None if attempt is None else attempt.get("hypothesis_id")
                ),
                "factor_spec_hash": factor_spec_hash(spec),
                "expected_direction": "+1",
                "identity": {
                    "campaign2_id": (
                        None if not opens else opens[0].get("campaign2_id")
                    ),
                    "campaign_design_hash": (
                        None
                        if attempt is None
                        else attempt.get("campaign_design_hash")
                    ),
                },
                "design": {
                    "universe": PRIMARY_UNIVERSE,
                    "partition_id": (
                        None
                        if not opens
                        else opens[0].get("input_manifest", {}).get(
                            "partition_id"
                        )
                    ),
                    "horizon": HORIZON,
                    "transaction_cost_bps": TRANSACTION_COST_BPS,
                    "slot_index": (
                        None if attempt is None else attempt.get("slot_index")
                    ),
                    "alpha": None if attempt is None else attempt.get("alpha"),
                    "critical_value": (
                        None if attempt is None else attempt.get("critical_value")
                    ),
                },
                "results": {
                    "declarations": (
                        None if attempt is None else attempt.get("declarations")
                    ),
                    "diagnostics": (
                        None if attempt is None else attempt.get("diagnostics")
                    ),
                    "screens": None if attempt is None else attempt.get("screens"),
                    "classifications": classifications,
                    "cause_sets": (
                        None if attempt is None else attempt.get("cause_sets")
                    ),
                },
                "outcome": {
                    "disposition": disposition,
                    "reasons": [] if attempt is None else attempt.get("reasons"),
                },
            }
        )

    payload = {
        "schema": "campaign2-development-report-v1",
        "campaign": {
            "program_id": PROGRAM_ID,
            "campaign2_id": None if not opens else opens[0].get("campaign2_id"),
            "precommitment_sha256": PRECOMMITMENT_SHA256,
            "pq_identities": {
                "methodology_sha256": PQ_METHODOLOGY_SHA256,
                "cs_id": PQ_CS_ID,
                "ed_id": PQ_ED_ID,
                "ce_id": PQ_CE_ID,
            },
            "critical_value": CRITICAL_VALUE,
            "budgets": dict(BUDGET_LIMITS),
            "partition_id": (
                None
                if not opens
                else opens[0].get("input_manifest", {}).get("partition_id")
            ),
            "screen_id": SCREEN_ID,
        },
        "reuse_disclosure": REUSE_DISCLOSURE,
        "assessability": {
            "required_classifications": required_classifications,
            "non_not_assessed_classifications": non_not_assessed,
            "hypotheses_with_non_not_assessed_disposition": (
                non_not_assessed_hypotheses
            ),
            "hypotheses_total": len(EXPECTED_LABELS),
        },
        "hypotheses": hypotheses,
        "stop_reason": None if not stops else stops[-1].get("stop_reason"),
        "claim_boundary": _CLAIM_BOUNDARY,
    }
    json_bytes = (canonical_json(_json_safe(payload)) + "\n").encode("utf-8")
    markdown_bytes = _render_markdown(payload).encode("utf-8")
    return json_bytes, markdown_bytes


def _render_markdown(payload: Mapping[str, Any]) -> str:
    lines: list[str] = []
    campaign = payload["campaign"]
    lines.append("# Campaign-2 development report (P-Q v2.1)")
    lines.append("")
    lines.append(f"- program_id: {campaign['program_id']}")
    lines.append(f"- campaign2_id: {campaign['campaign2_id']}")
    lines.append(f"- critical_value: {campaign['critical_value']}")
    lines.append(f"- screen_id: {campaign['screen_id']}")
    lines.append(f"- stop_reason: {payload['stop_reason']}")
    assess = payload["assessability"]
    lines.append(
        "- non-NOT_ASSESSED classifications: "
        f"{assess['non_not_assessed_classifications']} / "
        f"{assess['required_classifications']}"
    )
    lines.append(
        "- hypotheses with a non-NOT_ASSESSED disposition: "
        f"{assess['hypotheses_with_non_not_assessed_disposition']} / "
        f"{assess['hypotheses_total']}"
    )
    lines.append("")
    lines.append("## Re-use disclosure")
    lines.append(payload["reuse_disclosure"])
    lines.append("")
    lines.append("## Hypotheses")
    for item in payload["hypotheses"]:
        outcome = item["outcome"]
        lines.append(
            f"### {item['label']} — {outcome['disposition']}"
            + (
                f"({', '.join(outcome['reasons'])})"
                if outcome["reasons"]
                else ""
            )
        )
        lines.append(f"- hypothesis_id: {item['hypothesis_id']}")
        lines.append(f"- factor_spec_hash: {item['factor_spec_hash']}")
        lines.append(f"- slot_index: {item['design']['slot_index']}")
        lines.append(f"- cause_sets: {json_text(item['results']['cause_sets'])}")
        lines.append("")
    lines.append("## Claim boundary")
    lines.append(payload["claim_boundary"])
    lines.append("")
    return "\n".join(lines)


def json_text(value: Any) -> str:
    """A compact canonical JSON rendering for the markdown report."""
    return json.dumps(
        _json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
