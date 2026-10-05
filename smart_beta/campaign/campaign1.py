"""C1-LX — Campaign-1 development ledger and executor.

Effective authority (in order): ``docs/campaign1_precommitment.md`` (copied
verbatim here where frozen), the original frozen task
``worker_tasks/campaign1/task-c1-ledger-executor.md`` (C1-LX), C1-LX-E1
(Phase-8 ``family_id`` identity and the αᵢ source), C1-LX-E2 (PQCell
``status is None`` assessed predicate, registry-import boundary, and the
Stage A / Stage B / Stage C binding-failure handling), C1-LX-E3 (pre-open
failures write nothing; a ledger with ``CAMPAIGN_STOP`` is terminal) and
C1-LX-E4 (definition-based hypothesis identity and realization provenance),
governed by ``docs/campaign1_identity_architecture_correction.md``.

This module reuses the frozen existing value objects (Phase-8
``SearchPolicy``/``registry.content_hash``, PQB
``build_pq_diagnostics``/``classify_pq``/``PQScreenOutcome``, Phase-10
``EstimandPolicy``/``read_records``, Phase-7 ``Partition`` and D7b
``fold_key``/``_canonical_factor_hash``) and copies none of their logic.

Vocabulary note (C1-LX section 5): this layer emits only the frozen
development disposition vocabulary (NOT_ASSESSED / DEVELOPMENT_SCREEN_FAIL /
DEVELOPMENT_SCREEN_PASS) and constructs no Phase-7 evaluation or decision
record object; it calls no Phase-7 engine evaluate and consumes no holdout
token. Behaviour is synthetic-fixture tested only; no provider/network calls
and no reserve reads occur anywhere in this module.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd
from scipy.stats import norm

from smart_beta.campaign import diagnostics_store as _store
from smart_beta.campaign.ledger import (
    CampaignLedger,
    C1LedgerIntegrityError,
    C1ReplayMismatchError,
    LedgerEntry,
)
from smart_beta.dataset_v1 import delisting_bridge as _d7b
from smart_beta.dataset_v1 import pq_evaluation as _pqb
from smart_beta.dataset_v1 import quarantine as _pqa
from smart_beta.dataset_v1.delisting_bridge import (
    CampaignEvaluationDeclaration,
    _canonical_factor_hash,
    fold_key as _fold_key,
)
from smart_beta.dataset_v1.trusted_inputs import load_trusted_inputs
from smart_beta.data.schema import DATE_COL, STOCK_COL, VALUE_COL
from smart_beta.evaluation.partition import Fold, FoldRole, Partition
from smart_beta.experiment.policy import (
    BudgetExhaustion,
    ReplayRule,
    SearchPolicy,
    SearchProcedure,
    TrialUnit,
)
from smart_beta.experiment.registry import (
    canonical_json,
    content_hash,
)
from smart_beta.research.policy import StopReason
from smart_beta.science.contracts import EstimandKind, RecordKind
from smart_beta.science.knowledge import read_records
from smart_beta.science.preregistration import (
    ESTIMAND_POLICY,
    EstimandPolicy,
    estimand_policy_from_record,
)
from smart_beta.spec.factor_spec import FactorSpec, factor_spec_hash
from smart_beta.spec.factor_spec import from_dict as _spec_from_dict
from smart_beta.spec.engine import evaluate_factor as _evaluate_factor

__all__ = [
    # constants
    "PRECOMMITMENT_SHA256",
    "FACTORSPECS_SHA256",
    "O1_TASK_SPEC_SHA256",
    "PROGRAM_ID",
    "SCREEN_ID",
    "SCREEN_POLICY_SHA256",
    "SCREEN_POLICY_JSON",
    "DIAG_SCREEN_ID",
    "DIAG_SCREEN_POLICY_SHA256",
    "DIAG_SCREEN_POLICY_JSON",
    "FAMILY_DISPLAY_LABEL",
    "EXPECTED_FAMILY_ID",
    "PRIMARY_UNIVERSE",
    "DIAGNOSTIC_UNIVERSES",
    "CRITICAL_VALUE",
    "SEARCH_POLICY",
    "ESTIMAND_CONSTRUCTION",
    "SESOI",
    "SESOI_JUSTIFICATION_HASH",
    "MIN_PERIODS_IS",
    "MIN_PERIODS_OOS",
    "O2_THRESHOLD",
    "BUDGET_LIMITS",
    "EXPECTED_LABELS",
    "FACTOR_SPECS",
    "HYPOTHESIS_DEFINITION_SCHEMA",
    "HYPOTHESIS_ID_DOMAIN",
    "TI_MANIFEST_RELATIVE",
    "ENGINE_SEMANTICS_FILES",
    "EXPECTED_HYPOTHESIS_IDS",
    "CELL_MAPPING",
    "REPLAY_IDENTICAL",
    "PHASE9_STOP_REASON_MAP",
    # errors
    "C1CampaignError",
    "C1BindingError",
    "C1GovernanceConflict",
    "C1SealedFoldError",
    "C1RealizationError",
    "C1Stop",
    "C1BudgetExhausted",
    # values
    "C1StopReason",
    "Disposition",
    "BudgetLimits",
    "BudgetCounters",
    "CampaignBindings",
    "KpitPreflight",
    # api
    "build_partition",
    "load_factor_specs",
    "verify_precommitment",
    "family_declaration",
    "build_search_policy",
    "estimand_policy",
    "input_manifest",
    "campaign1_id",
    "campaign_design_hash",
    "campaign_attempt_id",
    "hypothesis_definition",
    "hypothesis_definition_hash",
    "campaign1_hypothesis_id",
    "hypothesis_identities",
    "factor_realization_hash",
    "build_fold_panel",
    "evaluate_o2_count",
    "RealInputAdapter",
    "verify_kpit_ordering",
    "assert_not_sealed_fold",
    "build_declaration",
    "wrap_declaration",
    "is_assessed",
    "screen_outcomes",
    "attempt_disposition",
    "count_o2_cells",
    "o2_denominator_spec",
    "CampaignExecutor",
    "CampaignRunResult",
    "render_report",
]

# ---------------------------------------------------------------------------
# Frozen paths and literals
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]

PRECOMMITMENT_PATH = _REPO_ROOT / "docs" / "campaign1_precommitment.md"
FACTORSPECS_PATH = _REPO_ROOT / "docs" / "campaign1_factorspecs.json"
O1_TASK_SPEC_PATH = (
    _REPO_ROOT / "worker_tasks" / "campaign1" / "task-c1-o1-structural-residual.md"
)
ADMISSION_PATH = (
    _REPO_ROOT / "docs" / "discovery_dataset_v1_2_development_admission.md"
)
PQ_REPORTING_PATH = _REPO_ROOT / "smart_beta" / "dataset_v1" / "pq_reporting.py"
PQ_EVALUATION_PATH = _REPO_ROOT / "smart_beta" / "dataset_v1" / "pq_evaluation.py"

PRECOMMITMENT_SHA256 = (
    "15f700606929889d4b9b64799e49603f0f5e84081df5557519d815cc556619d7"
)
FACTORSPECS_SHA256 = (
    "f93a218d1e8a5bc07a7e64b52e1ce66be09b6708479d5c76a5f9a2973d6d532c"
)
O1_TASK_SPEC_SHA256 = (
    "7907aa87057c609c0640a7bb9118ebbb413f59213a4c35ac69ff1e0904c8cb6c"
)

PROGRAM_ID = "campaign1:" + PRECOMMITMENT_SHA256

FAMILY_DISPLAY_LABEL = "campaign1-family-1"
EXPECTED_FAMILY_ID = (
    "2d2f16f56fe34ee5fecfb4613a6a96ecdc621f2836e5c2d6ea4fba5c77f8d1d0"
)

#: The exact canonical screen policy JSON of precommitment section 8.2.
SCREEN_POLICY_JSON: dict[str, Any] = {
    "basis": "NET",
    "cells": [
        "ORIGINAL-T0",
        "ORIGINAL-TS",
        "QUARANTINED-T0",
        "QUARANTINED-TS",
    ],
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
            "economic HAC t of the net long-short mean (A cells: PQC "
            "gap_aware_mean_tstat; B cells: unmodified D7b/Phase-7 NW t)"
        ),
    },
    "metric_key": "turnover_cost_adjusted",
    "not_assessed": (
        "any required statistic NOT_ASSESSED or below minimum periods -> "
        "screen verdict None (NOT_ASSESSED); DEVELOPMENT_SCREEN_PASS impossible"
    ),
    "oos_rule": {
        "comparison": ">",
        "minimum_available_periods": 12,
        "statistic": "arithmetic mean of available net long-short periods",
        "threshold": 0.0,
    },
    "pass_requires": (
        "IS rule in all four cells AND OOS rule in all four cells AND both "
        "fold PQ records not NOT_ASSESSED, not QUARANTINE_SENSITIVE, "
        "DELISTING NOT_SENSITIVE in A and B AND O2 guard not triggered"
    ),
    "screen_id": "C1-DEV-SCREEN-v1",
}
SCREEN_ID = "C1-DEV-SCREEN-v1"
SCREEN_POLICY_SHA256 = (
    "b7414db54add862dc12711564cdf7e0f4dec1ec07a0eb5b2bff507b81d9e592e"
)

DIAG_SCREEN_ID = "C1-ROBUSTNESS-DESCRIPTIVE-v1"
DIAG_SCREEN_POLICY_JSON: dict[str, Any] = {
    "judged": False,
    "role": "ROBUSTNESS_DESCRIPTIVE_DIAGNOSTIC",
    "screen_id": "C1-ROBUSTNESS-DESCRIPTIVE-v1",
    "verdict": "NONE",
}
DIAG_SCREEN_POLICY_SHA256 = (
    "e6ed8b27390d399e2ecd5c0fd7dcb9d4f71095eedf1bdf44cfcd75c6d17ca7c7"
)

PRIMARY_UNIVERSE = "TOP_500"
DIAGNOSTIC_UNIVERSES = ("TOP_200", "TOP_1000")

#: PQB cell name -> economic estimand/scenario interpretation (Dataset v1.2:
#: B = ORIGINAL, A = QUARANTINED).
CELL_MAPPING: dict[str, str] = {
    "B-T0": "ORIGINAL-T0",
    "B-TS": "ORIGINAL-TS",
    "A-T0": "QUARANTINED-T0",
    "A-TS": "QUARANTINED-TS",
}

MIN_PERIODS_IS = 24
MIN_PERIODS_OOS = 12
O2_THRESHOLD = 1e-6

HORIZON = 21
N_GROUPS = 5
WINSORIZATION = 0.01
TRANSACTION_COST_BPS = 10.0
PERIODS_PER_YEAR = 12
WEIGHTING = "EQUAL"
PRIMARY_METRIC = "turnover_cost_adjusted"
BASIS = "NET"

ESTIMAND_CONSTRUCTION: dict[str, Any] = {
    "n_groups": N_GROUPS,
    "cost_bps": TRANSACTION_COST_BPS,
    "winsorization": WINSORIZATION,
    "factor_missing_policy": "PROPAGATE",
}
SESOI = 0.0025
SESOI_JUSTIFICATION_HASH = (
    "2565ce442a0edc4d4916d2dcb8621a7d9260cb6742bbcf2f6b0f4e5203844493"
)
SESOI_JUSTIFICATION_TEXT = (
    "C1 SESOI: 0.0025 net long-short return per 21-session holding period "
    "(about 3.0% annualized), the smallest net quintile long-short spread on "
    "the D6 top-500 equal-weighted universe, after 10 bps Phase-7 transaction "
    "cost per unit of membership turnover, that Campaign-1 treats as "
    "economically meaningful; fixed before any Campaign-1 empirical evaluation."
)

BUDGET_LIMITS: dict[str, int] = {
    "generation": 0,
    "proposal": 7,
    "experiment": 7,
    "diagnostic": 14,
    "statistical": 7,
}

PARTITION_SPLIT_RULE = "campaign1-is-oos-sealed-v1"
EXPECTED_PARTITION_ID = (
    "b4b56dbfc82377930b8bf87a1d8039a69ccbf7d42d5faf234d3bb8cbbcb936fb"
)

EXPECTED_LABELS = tuple(f"C1-H{i}" for i in range(1, 8))

# ---------------------------------------------------------------------------
# E4 definition-based hypothesis identity (Correction sections 3-4)
# ---------------------------------------------------------------------------

#: The frozen HypothesisDefinition schema tag (Correction section 3).
HYPOTHESIS_DEFINITION_SCHEMA = "campaign1-hypothesis-definition-v1"

#: The frozen domain tag separating Campaign-1 IDs from the Phase-8 domain.
HYPOTHESIS_ID_DOMAIN = "campaign1-hypothesis-id-v1"

#: The D7c TrustedInputs manifest, relative to the dataset root.
TI_MANIFEST_RELATIVE = (
    "derived/trusted_inputs/ti-aa23fabd3db4e595/trusted_inputs.json"
)

#: The import closure of ``evaluate_factor``, in the frozen Correction order.
ENGINE_SEMANTICS_FILES: tuple[str, ...] = (
    "smart_beta/__init__.py",
    "smart_beta/data/__init__.py",
    "smart_beta/data/schema.py",
    "smart_beta/spec/__init__.py",
    "smart_beta/spec/engine.py",
    "smart_beta/spec/evaluator.py",
    "smart_beta/spec/expression.py",
    "smart_beta/spec/factor_spec.py",
    "smart_beta/spec/requirements.py",
    "smart_beta/spec/transforms.py",
)

#: The 7 frozen Campaign-1 definition-domain hypothesis ids (Correction
#: section 4). These are the check, never the source of the value; the code
#: recomputes them from the frozen inputs and asserts equality.
EXPECTED_HYPOTHESIS_IDS: dict[str, str] = {
    "C1-H1": (
        "b5e2ced5365c5963e7ca52ec839f9a356a629ee3d41ecbaa9ded3f5572826b6e"
    ),
    "C1-H2": (
        "39f6ded371924f6c7d7d9d25c59950123b673a3837422d36fb0e15172762374f"
    ),
    "C1-H3": (
        "4f6a7ddfdc67c3be4f6ddedb2f42c8ba1c52165bf01ede7ea84ff3f2cdcb53f9"
    ),
    "C1-H4": (
        "b7dce1008472217507d3140e1ea9dd351ee11c532e9540a541f83e2f911b7a2c"
    ),
    "C1-H5": (
        "a6de29a5a21930c249b010978f837557e287900ad0391b7d9bd9788584920e72"
    ),
    "C1-H6": (
        "e34ecaf78791d9387a4e1b06f0d28e816e08f0dc2d19ebb5a3ceac9d5e39ed22"
    ),
    "C1-H7": (
        "983189fe6c5c188c0e488c26d284b34853deadac8c4cba1e1e1eb5f33ccf3539"
    ),
}


# ---------------------------------------------------------------------------
# Typed errors / stop reasons
# ---------------------------------------------------------------------------


class C1CampaignError(Exception):
    """Base class for every Campaign fail-closed error."""


class C1BindingError(C1CampaignError):
    """A Campaign, hypothesis, attempt or authority binding does not match."""


class C1GovernanceConflict(C1CampaignError):
    """A locked governance field changed, or the K-PIT order is invalid."""


class C1SealedFoldError(C1CampaignError):
    """A declaration was requested for the DEVELOPMENT_SEALED_FOLD."""


class C1RealizationError(C1CampaignError):
    """A factor realization could not be computed (E4.2.4)."""


class _StageAFailure(Exception):
    """Internal: a post-open Stage-A identity failure (E3.1)."""

    def __init__(self, reason: "C1StopReason") -> None:
        self.reason = reason
        super().__init__(reason.value)


class C1Stop(C1CampaignError):
    """A typed Campaign stop (carries the frozen :class:`C1StopReason`)."""

    def __init__(self, reason: "C1StopReason") -> None:
        self.reason = reason
        super().__init__(reason.value)


class C1BudgetExhausted(C1Stop):
    """A budget counter would be exceeded."""


class C1StopReason(str, Enum):
    """The frozen Campaign stop vocabulary (precommitment section 11)."""

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


#: C1 stop -> Phase-9 StopReason where the meaning is identical (C1-LX
#: section 8). ``EXPERIMENT_BUDGET_EXHAUSTED`` has no Phase-9 twin.
PHASE9_STOP_REASON_MAP: dict[C1StopReason, StopReason] = {
    C1StopReason.PROPOSAL_BUDGET_EXHAUSTED: StopReason.PROPOSAL_BUDGET_EXHAUSTED,
    C1StopReason.STATISTICAL_BUDGET_EXHAUSTED: (
        StopReason.STATISTICAL_BUDGET_EXHAUSTED
    ),
    C1StopReason.GOVERNANCE_CONFLICT: StopReason.GOVERNANCE_CONFLICT,
    C1StopReason.SEALED_FOLD_ACCESS_ATTEMPT: (
        StopReason.HOLDOUT_FIREWALL_VIOLATION
    ),
}

DISPOSITION_NOT_ASSESSED = "NOT_ASSESSED"
DISPOSITION_FAIL = "DEVELOPMENT_SCREEN_FAIL"
DISPOSITION_PASS = "DEVELOPMENT_SCREEN_PASS"

#: The C1-LX section 6 replay value: a re-run matched the recorded attempt on
#: the byte.
REPLAY_IDENTICAL = "REPLAY_IDENTICAL"

NOT_ASSESSED_REASONS = (
    "BINDING_OR_IDENTITY_FAILURE",
    "O2_ESCALATION",
    "PQ_NOT_ASSESSED",
    "SCREEN_VERDICT_NONE",
)
FAIL_REASONS = (
    "QUARANTINE_SENSITIVE",
    "DELISTING_SENSITIVE",
    "IS_THRESHOLD_NOT_MET",
    "OOS_DIRECTION_NOT_MET",
)


class Disposition(str, Enum):
    """The three frozen development dispositions."""

    NOT_ASSESSED = DISPOSITION_NOT_ASSESSED
    DEVELOPMENT_SCREEN_FAIL = DISPOSITION_FAIL
    DEVELOPMENT_SCREEN_PASS = DISPOSITION_PASS


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


# ---------------------------------------------------------------------------
# Frozen bindings and value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CampaignBindings:
    """The read-only authority bindings C1-LX section 2.1 hashes."""

    dataset_id: str
    dataset_identity_hash: str
    amendment_sha256: str
    admission_sha256: str
    pq_id: str
    pq_hash: str
    quarantine_rule_hash: str
    r3_identity_hash: str
    pq_reporting_sha256: str
    pq_evaluation_sha256: str
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
    """Independent, non-substitutable budget accounting.

    A new primary attempt consumes one proposal, one experiment and one
    statistical slot; a new diagnostic design consumes one diagnostic
    evaluation. Generation is always zero. Exceeding a counter is refused
    before any computation with the corresponding typed stop.
    """

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
        """Refuse a primary attempt if any of its three counters is full."""
        if self.proposal_used >= self.limits.proposal:
            raise C1BudgetExhausted(C1StopReason.PROPOSAL_BUDGET_EXHAUSTED)
        if self.experiment_used >= self.limits.experiment:
            raise C1BudgetExhausted(C1StopReason.EXPERIMENT_BUDGET_EXHAUSTED)
        if self.statistical_slots_used >= self.limits.statistical:
            raise C1BudgetExhausted(C1StopReason.STATISTICAL_BUDGET_EXHAUSTED)

    def consume_primary(self) -> None:
        self.proposal_used += 1
        self.experiment_used += 1
        self.statistical_slots_used += 1

    def check_diagnostic(self) -> None:
        if self.diagnostic_used >= self.limits.diagnostic:
            raise C1BudgetExhausted(C1StopReason.GOVERNANCE_CONFLICT)

    def consume_diagnostic(self) -> None:
        self.diagnostic_used += 1

    def request_generation(self) -> None:
        raise C1BudgetExhausted(C1StopReason.GOVERNANCE_CONFLICT)


@dataclass(frozen=True)
class KpitPreflight:
    """The read-only Knowledge-PIT ordering preflight result."""

    ok: bool
    reason: str | None
    record_hashes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Frozen constants derived at import time
# ---------------------------------------------------------------------------


def verify_precommitment(path: Path | str = PRECOMMITMENT_PATH) -> str:
    """Verify the precommitment file's SHA-256; fail closed on mismatch."""
    digest = _sha256_file(path)
    if digest != PRECOMMITMENT_SHA256:
        raise C1BindingError(
            f"precommitment hash {digest} != bound {PRECOMMITMENT_SHA256}"
        )
    return digest


def _assert_screen_policy() -> None:
    digest = hashlib.sha256(
        canonical_json(SCREEN_POLICY_JSON).encode("utf-8")
    ).hexdigest()
    if digest != SCREEN_POLICY_SHA256:
        raise C1BindingError(
            f"screen policy hash {digest} != bound {SCREEN_POLICY_SHA256}"
        )
    diag = hashlib.sha256(
        canonical_json(DIAG_SCREEN_POLICY_JSON).encode("utf-8")
    ).hexdigest()
    if diag != DIAG_SCREEN_POLICY_SHA256:
        raise C1BindingError(
            f"diagnostic screen policy hash {diag} != bound "
            f"{DIAG_SCREEN_POLICY_SHA256}"
        )


def _assert_sesoi() -> None:
    digest = hashlib.sha256(SESOI_JUSTIFICATION_TEXT.encode("utf-8")).hexdigest()
    if digest != SESOI_JUSTIFICATION_HASH:
        raise C1BindingError(
            f"SESOI justification hash {digest} != bound "
            f"{SESOI_JUSTIFICATION_HASH}"
        )


def build_partition() -> Partition:
    """The frozen Campaign partition (C1-LX section 1)."""
    partition = Partition(
        folds=(
            Fold(
                FoldRole.IS,
                pd.Timestamp("2016-01-29"),
                pd.Timestamp("2020-07-31"),
            ),
            Fold(
                FoldRole.OOS,
                pd.Timestamp("2020-07-31"),
                pd.Timestamp("2022-01-31"),
            ),
            Fold(
                FoldRole.HOLDOUT,
                pd.Timestamp("2022-01-31"),
                pd.Timestamp("2023-07-03"),
            ),
        ),
        split_rule=PARTITION_SPLIT_RULE,
    )
    if partition.partition_id != EXPECTED_PARTITION_ID:
        raise C1BindingError(
            f"partition_id {partition.partition_id} != bound "
            f"{EXPECTED_PARTITION_ID}"
        )
    return partition


def load_factor_specs(path: Path | str = FACTORSPECS_PATH) -> tuple[FactorSpec, ...]:
    """Load and fully validate the frozen FactorSpec declaration (section 1)."""
    digest = _sha256_file(path)
    if digest != FACTORSPECS_SHA256:
        raise C1BindingError(
            f"factorspecs hash {digest} != bound {FACTORSPECS_SHA256}"
        )
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = payload.get("specs")
    if not isinstance(entries, list) or len(entries) != len(EXPECTED_LABELS):
        raise C1BindingError("factorspecs must carry exactly 7 specs")
    specs: list[FactorSpec] = []
    labels: list[str] = []
    for entry in entries:
        spec = _spec_from_dict(entry["factor_spec_canonical_json"])
        digest = factor_spec_hash(spec)
        if digest != entry["factor_spec_canonical_sha256"] or digest != spec.version:
            raise C1BindingError(
                f"factor spec hash {digest} != declared "
                f"{entry['factor_spec_canonical_sha256']}"
            )
        if spec.sign != 1:
            raise C1BindingError("every Campaign FactorSpec must have sign == +1")
        specs.append(spec)
        labels.append(entry["hypothesis_label"])
    if tuple(labels) != EXPECTED_LABELS:
        raise C1BindingError(f"hypothesis labels {labels} != {list(EXPECTED_LABELS)}")
    return tuple(specs)


def family_declaration(
    members: Sequence[str],
    *,
    precommitment_sha256: str = PRECOMMITMENT_SHA256,
    factorspecs_sha256: str = FACTORSPECS_SHA256,
) -> dict[str, Any]:
    """The E1 family declaration hashed by ``registry.content_hash``."""
    return {
        "domain": "campaign1-search-family",
        "factorspecs_sha256": factorspecs_sha256,
        "lineage": None,
        "members": list(members),
        "precommitment_sha256": precommitment_sha256,
    }


def build_search_policy(
    factor_specs: Sequence[FactorSpec],
    *,
    precommitment_sha256: str = PRECOMMITMENT_SHA256,
    factorspecs_sha256: str = FACTORSPECS_SHA256,
    expected_family_id: str = EXPECTED_FAMILY_ID,
) -> SearchPolicy:
    """Build the real Phase-8 :class:`SearchPolicy` with the E1 family id."""
    members = [factor_spec_hash(spec) for spec in factor_specs]
    family_id = content_hash(
        family_declaration(
            members,
            precommitment_sha256=precommitment_sha256,
            factorspecs_sha256=factorspecs_sha256,
        )
    )
    if family_id != expected_family_id:
        raise C1BindingError(
            f"family_id {family_id} != bound {expected_family_id}"
        )
    return SearchPolicy(
        family_id=family_id,
        family_budget_m=7,
        family_alpha=0.05,
        trial_unit=TrialUnit.EXPERIMENT_ID,
        procedure=SearchProcedure.FIXED_M_BONFERRONI,
        budget_exhaustion=BudgetExhaustion.GOVERNANCE_FAILURE,
        replay_rule=ReplayRule.DETERMINISTIC_REPLAY,
    )


def estimand_policy() -> EstimandPolicy:
    """The single frozen program :class:`EstimandPolicy` (section 1)."""
    return EstimandPolicy(
        program_id=PROGRAM_ID,
        estimand_kind=EstimandKind.MEAN_NET_LONG_SHORT,
        horizon=HORIZON,
        construction=dict(ESTIMAND_CONSTRUCTION),
        sesoi=SESOI,
        sesoi_justification_hash=SESOI_JUSTIFICATION_HASH,
    )


# Validate the frozen literals once at import.
_assert_screen_policy()
_assert_sesoi()
verify_precommitment()
FACTOR_SPECS: tuple[FactorSpec, ...] = load_factor_specs()
SEARCH_POLICY: SearchPolicy = build_search_policy(FACTOR_SPECS)
CRITICAL_VALUE: float = float(norm.ppf(1.0 - SEARCH_POLICY.alpha_per_test))
if abs(CRITICAL_VALUE - 2.44999766060273) >= 1e-12:
    raise C1BindingError(
        f"critical value {CRITICAL_VALUE!r} != 2.44999766060273"
    )


# ---------------------------------------------------------------------------
# Identities
# ---------------------------------------------------------------------------


def input_manifest(bindings: CampaignBindings) -> dict[str, Any]:
    """The C1-LX section 2.1 input set hashed into ``campaign1_id``."""
    return {
        "dataset_id": bindings.dataset_id,
        "dataset_identity_hash": bindings.dataset_identity_hash,
        "amendment_sha256": bindings.amendment_sha256,
        "admission_sha256": bindings.admission_sha256,
        "pq_id": bindings.pq_id,
        "pq_hash": bindings.pq_hash,
        "quarantine_rule_hash": bindings.quarantine_rule_hash,
        "r3_identity_hash": bindings.r3_identity_hash,
        "precommitment_sha256": PRECOMMITMENT_SHA256,
        "factorspecs_sha256": FACTORSPECS_SHA256,
        "screen_policy_sha256": SCREEN_POLICY_SHA256,
        "diag_screen_policy_sha256": DIAG_SCREEN_POLICY_SHA256,
        "o1_task_spec_sha256": O1_TASK_SPEC_SHA256,
        "pq_reporting_sha256": bindings.pq_reporting_sha256,
        "pq_evaluation_sha256": bindings.pq_evaluation_sha256,
        "partition_id": bindings.partition_id,
        "universes": [PRIMARY_UNIVERSE, *DIAGNOSTIC_UNIVERSES],
        "horizon": HORIZON,
        "evaluation_construction": {
            "n_groups": N_GROUPS,
            "winsorization": WINSORIZATION,
            "transaction_cost_bps": TRANSACTION_COST_BPS,
            "periods_per_year": PERIODS_PER_YEAR,
            "weighting": WEIGHTING,
            "primary_metric": PRIMARY_METRIC,
            "basis": BASIS,
        },
        "search_policy": SEARCH_POLICY.to_dict(),
        "budgets": dict(BUDGET_LIMITS),
        "critical_value_repr": repr(CRITICAL_VALUE),
    }


def campaign1_id(bindings: CampaignBindings) -> str:
    """The frozen Campaign identity (C1-LX section 2.1)."""
    return content_hash(input_manifest(bindings))


def campaign_design_hash(
    declaration: CampaignEvaluationDeclaration,
    *,
    campaign1_id_value: str,
    pq_id: str,
    pq_hash: str,
) -> str:
    """The D7b base declaration hash minus fold/panel plus the bindings."""
    body = dict(declaration.to_dict())
    body.pop("fold_key", None)
    body.pop("factor_panel_sha256", None)
    body["pq_id"] = pq_id
    body["pq_hash"] = pq_hash
    body["campaign1_id"] = campaign1_id_value
    return content_hash(body)


def campaign_attempt_id(
    *,
    domain: str,
    campaign1_id_value: str,
    hypothesis_id: str,
    design_hash: str,
) -> str:
    """The distinct-domain Campaign attempt identity (C1-LX section 2.5)."""
    return content_hash(
        {
            "domain": domain,
            "campaign1_id": campaign1_id_value,
            "hypothesis_id": hypothesis_id,
            "campaign_design_hash": design_hash,
        }
    )


def hypothesis_definition(
    spec: FactorSpec,
    *,
    root: Path | str,
) -> dict[str, Any]:
    """The E4/C1-LX-E4 HypothesisDefinition (Correction section 3).

    Exactly the four frozen fields: ``schema``, ``factor_spec_hash``,
    ``trusted_inputs`` and ``engine_semantics``. It reads only the D7c
    manifest JSON bytes and the repository engine-semantics code files; it
    reads no parquet and evaluates no factor. No Campaign-level field
    (program, Campaign, estimand, universe, screen) enters the definition.
    """
    manifest_path = Path(root) / TI_MANIFEST_RELATIVE
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise C1BindingError(
            f"trusted-inputs manifest unreadable: {exc}"
        ) from exc
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise C1BindingError(
            f"trusted-inputs manifest malformed: {exc}"
        ) from exc
    ti_id = manifest.get("ti_id") if isinstance(manifest, Mapping) else None
    ti_hash = manifest.get("ti_hash") if isinstance(manifest, Mapping) else None
    if not isinstance(ti_id, str) or not isinstance(ti_hash, str):
        raise C1BindingError("trusted-inputs manifest lacks ti_id/ti_hash")
    engine_semantics: dict[str, str] = {}
    for relative in ENGINE_SEMANTICS_FILES:
        try:
            engine_semantics[relative] = _sha256_file(_REPO_ROOT / relative)
        except OSError as exc:
            raise C1BindingError(
                f"engine-semantics file {relative} unreadable: {exc}"
            ) from exc
    return {
        "schema": HYPOTHESIS_DEFINITION_SCHEMA,
        "factor_spec_hash": factor_spec_hash(spec),
        "trusted_inputs": {
            "ti_id": ti_id,
            "ti_hash": ti_hash,
            "manifest_sha256": manifest_sha256,
        },
        "engine_semantics": engine_semantics,
    }


def hypothesis_definition_hash(definition: Mapping[str, Any]) -> str:
    """``registry.content_hash`` of a HypothesisDefinition (E4.1)."""
    return content_hash(definition)


def campaign1_hypothesis_id(definition_hash: str) -> str:
    """The Campaign-1 definition-domain hypothesis id (Correction section 3)."""
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
    expected_ids: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    """The C1-H1..H7 identity tuple (label, spec, definition, hypothesis id)."""
    identities: list[dict[str, str]] = []
    for spec in factor_specs:
        label = spec.hypothesis or spec.id
        definition = hypothesis_definition(spec, root=root)
        definition_hash = hypothesis_definition_hash(definition)
        hypothesis_id = campaign1_hypothesis_id(definition_hash)
        if expected_ids.get(label) != hypothesis_id:
            raise C1BindingError(
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
    """The E4 ``hypothesis_identities(root)`` adapter.

    Reproduces the 7 frozen definition-domain ids from the real D7c manifest
    bytes and the repository engine-semantics files, with zero factor
    evaluation. A mismatch raises :class:`C1BindingError` (a Stage-A failure).
    """
    specs = tuple(factor_specs) if factor_specs is not None else FACTOR_SPECS
    expected = EXPECTED_HYPOTHESIS_IDS if expected_ids is None else expected_ids
    return _hypothesis_identities_for(specs, root=root, expected_ids=expected)


def factor_realization_hash(
    spec: FactorSpec, inputs: Mapping[str, Any]
) -> str:
    """The Phase-6 realization hash: ``evaluate_factor(spec, aliased).content_hash``.

    This is the former ``factor_provenance_hash`` with unchanged computation.
    Uses exactly the same D7c aliasing path PQA uses
    (``quarantine._alias_inputs``) and never inspects, logs or reports factor
    values. A computation failure raises the typed :class:`C1RealizationError`.
    """
    try:
        aliased = _pqa._alias_inputs(spec, inputs)
        return _evaluate_factor(spec, aliased).content_hash
    except C1RealizationError:
        raise
    except Exception as exc:
        raise C1RealizationError(
            f"factor realization failed for {spec.id}: {exc}"
        ) from exc


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
    """Verify the frozen Knowledge-PIT ordering (C1-LX section 9)."""
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
        (str(rec.payload.get("hypothesis_id")), str(rec.payload.get("factor_spec_hash")))
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


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "value") and not isinstance(value, str):
        return value.value
    return value


# ---------------------------------------------------------------------------
# Declarations and the sealed-fold firewall
# ---------------------------------------------------------------------------


def assert_not_sealed_fold(fold: Fold) -> None:
    """The Campaign firewall: never construct a sealed-fold declaration."""
    if fold.role is FoldRole.HOLDOUT:
        raise C1SealedFoldError(
            "DEVELOPMENT_SEALED_FOLD is never read by Campaign-1"
        )
    if "holdout" in _fold_key(fold):
        raise C1SealedFoldError("holdout-role folds are sealed")


def build_declaration(
    *,
    spec: FactorSpec,
    panel: pd.DataFrame,
    fold: Fold,
    universe: str,
    bindings: CampaignBindings,
    screen_id: str,
    screen_policy_hash: str,
) -> CampaignEvaluationDeclaration:
    """Build one D7b declaration, refusing the sealed fold first."""
    assert_not_sealed_fold(fold)
    return CampaignEvaluationDeclaration(
        precommitment_ref=(
            "docs/campaign1_precommitment.md@" + PRECOMMITMENT_SHA256
        ),
        factor_spec_version=spec.version,
        factor_panel_sha256=_canonical_factor_hash(panel),
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
    bindings: CampaignBindings,
) -> _pqb.PQEvaluationDeclaration:
    """Wrap a D7b declaration with the admitted dataset/overlay/rule bindings."""
    return _pqb.PQEvaluationDeclaration(
        base=declaration,
        dataset_id=bindings.dataset_id,
        dataset_identity_hash=bindings.dataset_identity_hash,
        amendment_sha256=bindings.amendment_sha256,
        quarantine_rule_hash=bindings.quarantine_rule_hash,
        pq_id=bindings.pq_id,
        pq_hash=bindings.pq_hash,
    )


# ---------------------------------------------------------------------------
# Screen outcomes and disposition
# ---------------------------------------------------------------------------


def is_assessed(cell: _pqb.PQCell | None) -> bool:
    """E2.1: a required cell is assessed iff ``PQCell.status is None``.

    This is identical to ``classify_pq``'s own predicate and is PQB's encoding
    of ``GapAwareHACResult.status == "OK"``.
    """
    if cell is None:
        return False
    return cell.status is None


def _screen_cell(cell: _pqb.PQCell | None, fold: FoldRole) -> bool | None:
    if not is_assessed(cell):
        return None
    assert cell is not None
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


def screen_outcomes(
    diagnostics: _pqb.PQDiagnostics,
    fold: FoldRole,
    *,
    screen_id: str = SCREEN_ID,
    screen_policy_hash: str = SCREEN_POLICY_SHA256,
) -> tuple[_pqb.PQScreenOutcome, ...]:
    """One hash-bound :class:`PQScreenOutcome` per PQB cell, IS or OOS."""
    if diagnostics.orientation_rule != "PASSTHROUGH":
        raise C1BindingError(
            f"sign +1 must give the identity orientation, got "
            f"{diagnostics.orientation_rule!r}"
        )
    outcomes: list[_pqb.PQScreenOutcome] = []
    for name in ("B-T0", "B-TS", "A-T0", "A-TS"):
        cell = diagnostics.cell(name)
        if cell is None:
            raise C1BindingError(f"diagnostics missing required cell {name}")
        outcomes.append(
            _pqb.PQScreenOutcome(
                cell=name,
                cell_hash=cell.cell_hash,
                screen_id=screen_id,
                screen_policy_hash=screen_policy_hash,
                declaration_hash=diagnostics.declaration_hash,
                passed=_screen_cell(cell, fold),
            )
        )
    return tuple(outcomes)


def _ordered_reasons(reasons: Sequence[str], canonical: Sequence[str]) -> list[str]:
    return [reason for reason in canonical if reason in set(reasons)]


def attempt_disposition(
    *,
    diagnostics_is: _pqb.PQDiagnostics,
    diagnostics_oos: _pqb.PQDiagnostics,
    screens_is: Sequence[_pqb.PQScreenOutcome],
    screens_oos: Sequence[_pqb.PQScreenOutcome],
    classify_is: _pqb.PQDevelopmentSensitivityRecord,
    classify_oos: _pqb.PQDevelopmentSensitivityRecord,
    o2_triggered: bool = False,
) -> tuple[Disposition, list[str], bool]:
    """The frozen first-match disposition with every applicable reason."""
    collected: list[str] = []
    not_assessed = False
    if o2_triggered:
        collected.append("O2_ESCALATION")
        not_assessed = True
    if (
        diagnostics_is.not_assessed is not None
        or diagnostics_oos.not_assessed is not None
        or classify_is.status == _pqb.STATUS_NOT_ASSESSED
        or classify_oos.status == _pqb.STATUS_NOT_ASSESSED
    ):
        collected.append("PQ_NOT_ASSESSED")
        not_assessed = True
    if _has_none_screen(screens_is) or _has_none_screen(screens_oos):
        collected.append("SCREEN_VERDICT_NONE")
        not_assessed = True

    quarantine_sensitive = bool(
        classify_is.quarantine_sensitive or classify_oos.quarantine_sensitive
    )
    if not_assessed:
        return (
            Disposition.NOT_ASSESSED,
            _ordered_reasons(collected, NOT_ASSESSED_REASONS),
            quarantine_sensitive,
        )

    fail_reasons: list[str] = []
    if quarantine_sensitive:
        fail_reasons.append("QUARANTINE_SENSITIVE")
    if _delisting_sensitive(classify_is) or _delisting_sensitive(classify_oos):
        fail_reasons.append("DELISTING_SENSITIVE")
    if any(screen.passed is False for screen in screens_is):
        fail_reasons.append("IS_THRESHOLD_NOT_MET")
    if any(screen.passed is False for screen in screens_oos):
        fail_reasons.append("OOS_DIRECTION_NOT_MET")
    if fail_reasons:
        return (
            Disposition.DEVELOPMENT_SCREEN_FAIL,
            _ordered_reasons(fail_reasons, FAIL_REASONS),
            quarantine_sensitive,
        )
    return Disposition.DEVELOPMENT_SCREEN_PASS, [], quarantine_sensitive


def _has_none_screen(screens: Sequence[_pqb.PQScreenOutcome]) -> bool:
    return any(screen.passed is None for screen in screens)


def _delisting_sensitive(record: _pqb.PQDevelopmentSensitivityRecord) -> bool:
    not_sensitive = _pqb.DELISTING_NOT_SENSITIVE
    return record.delisting_a != not_sensitive or record.delisting_b != not_sensitive


# ---------------------------------------------------------------------------
# O2 guard
# ---------------------------------------------------------------------------


def o2_denominator_spec(spec: FactorSpec) -> FactorSpec:
    """The frozen O2 denominator sub-spec ``lag(rolling_std(ret, w), 21)``."""
    expression = spec.to_dict()["expression"]
    if not isinstance(expression, Mapping) or expression.get("op") != "div":
        raise C1BindingError("O2 denominator requires a division expression")
    operand = expression["right"]
    return FactorSpec(
        id=f"{spec.id}_o2_denominator",
        description="O2 denominator: lagged rolling std of daily total return",
        expression=operand,
        inputs=spec.inputs,
        frequency=spec.frequency,
        missing_policy=spec.missing_policy,
        hypothesis=spec.hypothesis,
        sign=1,
    )


def _wide_index_key(values: pd.DataFrame, formation: Any) -> Any | None:
    """Resolve a formation in a wide frame whose index is dates or Timestamps."""
    if formation in values.index:
        return formation
    stamp = pd.Timestamp(formation)
    if stamp in values.index:
        return stamp
    return None


def count_o2_cells(
    values: pd.DataFrame,
    formations: Sequence[date],
    members_by_formation: Mapping[date, Sequence[str]],
) -> int:
    """Count finite sub-threshold cells in the O2 denominator (integer only)."""
    count = 0
    for formation in formations:
        key = _wide_index_key(values, formation)
        if key is None:
            continue
        members = members_by_formation.get(formation, ())
        for member in members:
            if member not in values.columns:
                continue
            value = values.at[key, member]
            try:
                finite = math.isfinite(float(value))
            except (TypeError, ValueError):
                finite = False
            if finite and float(value) < O2_THRESHOLD:
                count += 1
    return count


def build_fold_panel(
    values: pd.DataFrame,
    formations: Sequence[date],
    members_by_formation: Mapping[date, Sequence[str]],
) -> pd.DataFrame:
    """The C1-LX section 3 fold panel: one row per (formation, member).

    ``values`` is the Phase-6 wide value frame (dates x stock ids); the cell
    value is the wide value at (formation, member) or NaN. The returned frame
    carries exactly ``date``/``stock_id``/``value`` and is built to satisfy
    both D7b ``_validate_factor_panel`` and PQB ``_factor_panel_matches``.
    """
    records: list[tuple[date, str, float]] = []
    for formation in formations:
        key = _wide_index_key(values, formation)
        for member in members_by_formation.get(formation, ()):
            value = float("nan")
            if key is not None and member in values.columns:
                raw = values.at[key, member]
                try:
                    number = float(raw)
                except (TypeError, ValueError):
                    number = float("nan")
                if math.isfinite(number):
                    value = number
            records.append((formation, str(member), value))
    panel = pd.DataFrame(records, columns=[DATE_COL, STOCK_COL, VALUE_COL])
    panel[DATE_COL] = pd.to_datetime(panel[DATE_COL])
    panel[STOCK_COL] = panel[STOCK_COL].astype("string")
    panel[VALUE_COL] = panel[VALUE_COL].astype("float64")
    return panel


def evaluate_o2_count(
    spec: FactorSpec,
    *,
    overlay: Any,
    inputs: Mapping[str, Any],
    formations: Sequence[date],
    members_by_formation: Mapping[date, Sequence[str]],
) -> int:
    """The C1-LX section 4 O2 count over the frozen denominator sub-spec."""
    states = _pqa.factor_input_states(
        o2_denominator_spec(spec), overlay=overlay, inputs=inputs
    )
    return count_o2_cells(
        states.attrs["values"], formations, members_by_formation
    )


def _placeholder_declaration(
    partition_id: str,
    universe: str,
    fold: Fold,
) -> CampaignEvaluationDeclaration:
    """A declaration used only to obtain D7b/D6 fold membership."""
    return CampaignEvaluationDeclaration(
        precommitment_ref=(
            "docs/campaign1_precommitment.md@" + PRECOMMITMENT_SHA256
        ),
        factor_spec_version=EXPECTED_FAMILY_ID,
        factor_panel_sha256=EXPECTED_FAMILY_ID,
        partition_id=partition_id,
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


def _design_declaration(
    *,
    spec: FactorSpec,
    fold: Fold,
    universe: str,
    bindings: CampaignBindings,
    screen_id: str,
    screen_policy_hash: str,
) -> CampaignEvaluationDeclaration:
    """A declaration used only to derive ``campaign_design_hash``.

    ``campaign_design_hash`` excludes ``fold_key`` and ``factor_panel_sha256``
    (C1-LX section 2.4), so this carries the real declaration fields with a
    placeholder panel hash. No factor is evaluated and no panel is built, so
    attempt identity establishment cannot depend on the realization (E4.2.3).
    """
    assert_not_sealed_fold(fold)
    return CampaignEvaluationDeclaration(
        precommitment_ref=(
            "docs/campaign1_precommitment.md@" + PRECOMMITMENT_SHA256
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
        screen_id=screen_id,
        screen_policy_hash=screen_policy_hash,
    )


class RealInputAdapter:
    """The executor's real-input default for realization, panels and O2 count.

    Inputs are the D7c ``TrustedInput`` mapping loaded lazily from ``root`` via
    the existing :func:`~smart_beta.dataset_v1.trusted_inputs.load_trusted_inputs`
    and fold membership comes from the existing D7b/D6 loader. Nothing here is
    read at construction; a test that injects callables never triggers a load.
    """

    def __init__(
        self,
        *,
        root: Path | str,
        partition: Partition,
        overlay: Any,
        partition_id: str = EXPECTED_PARTITION_ID,
        inputs: Mapping[str, Any] | None = None,
    ) -> None:
        self._root = Path(root)
        self._partition = partition
        self._overlay = overlay
        self._partition_id = partition_id
        self._inputs = inputs

    @property
    def inputs(self) -> Mapping[str, Any]:
        if self._inputs is None:
            self._inputs = load_trusted_inputs(self._root)
        return self._inputs

    def realization_of(self, spec: FactorSpec) -> str:
        return factor_realization_hash(spec, self.inputs)

    def panel_of(self, spec: FactorSpec, universe: str, fold: Fold) -> pd.DataFrame:
        loaded = self._load(universe, fold)
        states = _pqa.factor_input_states(
            spec, overlay=self._overlay, inputs=self.inputs
        )
        return build_fold_panel(
            states.attrs["values"],
            loaded.formations,
            loaded.universe_by_formation,
        )

    def o2_count_of(self, spec: FactorSpec) -> int:
        formations: list[date] = []
        members_by_formation: dict[date, Sequence[str]] = {}
        for role in (FoldRole.IS, FoldRole.OOS):
            loaded = self._load("TOP_500", _fold(self._partition, role))
            formations.extend(loaded.formations)
            members_by_formation.update(loaded.universe_by_formation)
        return evaluate_o2_count(
            spec,
            overlay=self._overlay,
            inputs=self.inputs,
            formations=formations,
            members_by_formation=members_by_formation,
        )

    def _load(self, universe: str, fold: Fold) -> Any:
        bindings = _d7b.DEFAULT_BINDINGS
        resolver = _d7b._Resolver(self._root, bindings)
        declaration = _placeholder_declaration(
            self._partition_id, universe, fold
        )
        return _d7b._load_d7a(
            resolver, bindings, declaration, self._partition, fold
        )


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


@dataclass
class CampaignRunResult:
    """The deterministic result of an executor run."""

    stop_reason: C1StopReason
    campaign1_id: str | None
    counters: dict[str, int]
    ledger_path: str


class CampaignExecutor:
    """The C1-LX Campaign development executor.

    All real factor/PQB/D7b dependencies are injected so the executor is
    exercised offline with synthetic fixtures. ``realization_of`` returns the
    Phase-6 ``EngineResult.content_hash`` for a spec; ``panel_of`` returns the
    fold/universe Phase-6 panel; ``o2_count_of`` returns the frozen O2 count.
    """

    def __init__(
        self,
        *,
        ledger: CampaignLedger,
        bindings: CampaignBindings,
        partition: Partition,
        factor_specs: Sequence[FactorSpec] | None = None,
        realization_of: Callable[[FactorSpec], str] | None = None,
        panel_of: Callable[[FactorSpec, str, Fold], pd.DataFrame] | None = None,
        overlay: Any = None,
        root: Path | str = ".",
        inputs_by_semantic: Mapping[str, Any] | None = None,
        o2_count_of: Callable[[FactorSpec], int] | None = None,
        expected_realizations: Mapping[str, str] | None = None,
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
        self.realization_of = realization_of
        self.panel_of = panel_of
        self.overlay = overlay
        self.root = Path(root)
        self.inputs_by_semantic = (
            None if inputs_by_semantic is None else dict(inputs_by_semantic)
        )
        self.o2_count_of = o2_count_of
        self.expected_realizations = (
            None if expected_realizations is None else dict(expected_realizations)
        )
        self.knowledge_log_path = (
            Path(knowledge_log_path) if knowledge_log_path is not None else None
        )
        self.labels = tuple(labels) if labels is not None else EXPECTED_LABELS
        self.require_kpit = require_kpit
        self.counters = BudgetCounters(limits=BudgetLimits(**BUDGET_LIMITS))
        self._attempt_ordinal = 0
        self._adapter: RealInputAdapter | None = None
        self._spec_by_label = {
            (spec.hypothesis or spec.id): spec for spec in self.factor_specs
        }

    def _ensure_adapters(self) -> None:
        """Fill any missing dependency with the real-input default adapter.

        A test that injects all three callables never constructs or touches
        :class:`RealInputAdapter`, so no D7c data is loaded by the tests.
        """
        if (
            self.realization_of is not None
            and self.panel_of is not None
            and self.o2_count_of is not None
        ):
            return
        if self._adapter is None:
            self._adapter = RealInputAdapter(
                root=self.root,
                partition=self.partition,
                overlay=self.overlay,
                partition_id=self.bindings.partition_id,
                inputs=self.inputs_by_semantic,
            )
        if self.realization_of is None:
            self.realization_of = self._adapter.realization_of
        if self.panel_of is None:
            self.panel_of = self._adapter.panel_of
        if self.o2_count_of is None:
            self.o2_count_of = self._adapter.o2_count_of
        if self.inputs_by_semantic is None:
            self.inputs_by_semantic = self._adapter.inputs

    # -- public ----------------------------------------------------------
    def run(self) -> CampaignRunResult:
        # E3.2: a ledger containing CAMPAIGN_STOP is terminal. Verify the
        # chain (read-only) and append nothing whatsoever.
        recorded_stop = self._existing_stop()
        if recorded_stop is not None:
            self.ledger.verify()
            self._replay_counters()
            return CampaignRunResult(
                stop_reason=recorded_stop,
                campaign1_id=self._existing_campaign1_id(),
                counters=self.counters.to_dict(),
                ledger_path=str(self.ledger.path),
            )

        self._replay_counters()
        self._ensure_adapters()

        # E3.1 pre-open Stage-A: no CAMPAIGN_OPEN yet, so write nothing.
        # E4.2.2: a missing or incomplete expected-realization mapping is a
        # pre-open Stage-A failure and writes nothing.
        if not self._expected_realizations_complete():
            return self._stage_a_result(
                C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
            )
        # E4.1: hypothesis identity is definition-based; no factor is
        # evaluated and no D7c parquet is read.
        try:
            identities = _hypothesis_identities_for(
                self.factor_specs,
                root=self.root,
                expected_ids=EXPECTED_HYPOTHESIS_IDS,
            )
        except C1BindingError:
            return self._stage_a_result(
                C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
            )

        # E5.4 fail-closed K-PIT preflight: a required K-PIT
        # (``require_kpit=True``, the production default) with no knowledge
        # log must refuse to open. The silent ``KpitPreflight(True, None, ())``
        # path is reachable only through the explicit test-only
        # ``require_kpit=False`` switch.
        if self.require_kpit:
            if self.knowledge_log_path is None:
                return self._stage_a_result(C1StopReason.GOVERNANCE_CONFLICT)
            preflight = verify_kpit_ordering(
                self.knowledge_log_path,
                policy=estimand_policy(),
                identities=identities,
            )
        else:
            preflight = KpitPreflight(True, None, ())
        if not preflight.ok:
            return self._stage_a_result(C1StopReason.GOVERNANCE_CONFLICT)

        try:
            campaign_id = self._open_campaign(preflight, identities)
        except C1GovernanceConflict:
            return self._stage_a_result(C1StopReason.GOVERNANCE_CONFLICT)
        except C1BindingError:
            return self._stage_a_result(
                C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
            )

        # Post-open: CAMPAIGN_OPEN is durably recorded, so a Stage-A failure
        # (and every other typed stop) appends exactly one CAMPAIGN_STOP.
        try:
            for label in self.labels:
                stop = self._process_label(label, campaign_id)
                if stop is None or stop == REPLAY_IDENTICAL:
                    continue
                return self._stop(stop, campaign_id)
        except _StageAFailure as failure:
            return self._stage_a_result(failure.reason)
        except _d7b.ReserveFirewallError:
            return self._stop(C1StopReason.RESERVE_OR_FIREWALL_VIOLATION, campaign_id)
        except C1SealedFoldError:
            return self._stop(C1StopReason.SEALED_FOLD_ACCESS_ATTEMPT, campaign_id)
        except C1GovernanceConflict:
            return self._stop(C1StopReason.GOVERNANCE_CONFLICT, campaign_id)
        except (C1BindingError, _d7b.D7bBindingError, _pqb.PQBindingError):
            return self._stop(
                C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE,
                campaign_id,
            )
        except C1Stop as stop:
            return self._stop(stop.reason, campaign_id)
        except C1ReplayMismatchError:
            # E3.2: the ledger is unchanged; the mismatch itself is fatal.
            raise
        except Exception:
            self._append_stop(
                C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
            )
            raise

        return self._stop(
            C1StopReason.ALL_PRECOMMITTED_HYPOTHESES_EVALUATED, campaign_id
        )

    def _ledger_is_post_open(self) -> bool:
        return bool(self.ledger.records("CAMPAIGN_OPEN"))

    def _expected_realizations_complete(self) -> bool:
        """E4.2.2: the executor needs one expected realization per C1-H1..H7."""
        mapping = self.expected_realizations
        if mapping is None:
            return False
        return all(
            isinstance(mapping.get(label), str) and mapping[label]
            for label in EXPECTED_LABELS
        )

    def _stage_a_result(self, reason: C1StopReason) -> CampaignRunResult:
        """E3.1: pre-open writes nothing; post-open appends one CAMPAIGN_STOP."""
        if self._ledger_is_post_open():
            self._append_stage_a_stop(reason)
        return CampaignRunResult(
            stop_reason=reason,
            campaign1_id=self._existing_campaign1_id(),
            counters=self.counters.to_dict(),
            ledger_path=str(self.ledger.path),
        )

    def _append_stage_a_stop(self, reason: C1StopReason) -> None:
        self._append_stop(reason)

    # -- campaign open / lock -------------------------------------------
    def _open_campaign(
        self,
        preflight: KpitPreflight,
        identities: Sequence[Mapping[str, str]],
    ) -> str:
        campaign_id = campaign1_id(self.bindings)
        record = {
            "record_type": "CAMPAIGN_OPEN",
            "program_id": PROGRAM_ID,
            "campaign1_id": campaign_id,
            "input_manifest": input_manifest(self.bindings),
            "search_policy": SEARCH_POLICY.to_dict(),
            "family_id": SEARCH_POLICY.family_id,
            "family_display_label": FAMILY_DISPLAY_LABEL,
            "budgets": dict(BUDGET_LIMITS),
            "critical_value": CRITICAL_VALUE,
            "critical_value_repr": repr(CRITICAL_VALUE),
            "estimand_policy": estimand_policy_content(),
            "expected_factor_realizations": dict(
                sorted((self.expected_realizations or {}).items())
            ),
            "kpit_preflight_record_hashes": list(preflight.record_hashes),
        }
        existing = self.ledger.records("CAMPAIGN_OPEN")
        if existing:
            if not _lock_matches(existing[0], record):
                raise C1GovernanceConflict(
                    "CAMPAIGN_OPEN family fields changed for an existing ledger"
                )
            return campaign_id
        self.ledger.append(_json_safe(record))
        return campaign_id

    # -- per-hypothesis --------------------------------------------------
    def _hypothesis_identity(
        self, spec: FactorSpec, label: str
    ) -> tuple[str, str]:
        """E4.1: the definition-based (definition_hash, hypothesis_id) pair."""
        definition = hypothesis_definition(spec, root=self.root)
        definition_hash = hypothesis_definition_hash(definition)
        hypothesis_id = campaign1_hypothesis_id(definition_hash)
        if EXPECTED_HYPOTHESIS_IDS.get(label) != hypothesis_id:
            raise C1BindingError(
                f"hypothesis_id {hypothesis_id} for {label} != bound "
                f"{EXPECTED_HYPOTHESIS_IDS.get(label)}"
            )
        return definition_hash, hypothesis_id

    def _process_label(
        self, label: str, campaign_id: str
    ) -> C1StopReason | str | None:
        if label not in self._spec_by_label:
            raise C1GovernanceConflict(f"proposal {label!r} is not precommitted")
        spec = self._spec_by_label[label]
        try:
            _, hypothesis_id = self._hypothesis_identity(spec, label)
        except C1BindingError as exc:
            raise _StageAFailure(
                C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
            ) from exc

        is_fold = _fold(self.partition, FoldRole.IS)
        oos_fold = _fold(self.partition, FoldRole.OOS)
        # The design hash excludes the factor panel hash (C1-LX section 2.4),
        # so it is computed from metadata only; no factor is evaluated and the
        # realization stays outside identity establishment (E4.2.3).
        declaration_is = _design_declaration(
            spec=spec,
            fold=is_fold,
            universe=PRIMARY_UNIVERSE,
            bindings=self.bindings,
            screen_id=SCREEN_ID,
            screen_policy_hash=SCREEN_POLICY_SHA256,
        )
        design_hash = campaign_design_hash(
            declaration_is,
            campaign1_id_value=campaign_id,
            pq_id=self.bindings.pq_id,
            pq_hash=self.bindings.pq_hash,
        )
        attempt_id = campaign_attempt_id(
            domain="campaign1-development-attempt",
            campaign1_id_value=campaign_id,
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
    ) -> C1StopReason | str | None:
        expected = (self.expected_realizations or {}).get(label)
        # E4.2.4: realization is computed after establishment and before any
        # PQB build. A mismatch or a typed realization failure is Stage B.
        try:
            realization = self.realization_of(spec)
        except C1RealizationError:
            return self._stage_b_attempt(
                existing=existing,
                label=label,
                hypothesis_id=hypothesis_id,
                design_hash=design_hash,
                attempt_id=attempt_id,
                slot_index=slot_index,
                factor_realization_hash=None,
                expected_factor_realization_hash=expected,
                failure_detail="REALIZATION_FAILURE",
            )
        if realization != expected:
            return self._stage_b_attempt(
                existing=existing,
                label=label,
                hypothesis_id=hypothesis_id,
                design_hash=design_hash,
                attempt_id=attempt_id,
                slot_index=slot_index,
                factor_realization_hash=realization,
                expected_factor_realization_hash=expected,
                failure_detail="REALIZATION_MISMATCH",
            )

        # The O2 guard (H6/H7) runs before any PQB build and is part of the
        # primary attempt; a typed binding failure here is Stage B too.
        if label in ("C1-H6", "C1-H7"):
            try:
                count = self._o2_count(spec)
            except (C1BindingError, _d7b.D7bBindingError, _pqb.PQBindingError):
                return self._stage_b_attempt(
                    existing=existing,
                    label=label,
                    hypothesis_id=hypothesis_id,
                    design_hash=design_hash,
                    attempt_id=attempt_id,
                    slot_index=slot_index,
                    factor_realization_hash=realization,
                    expected_factor_realization_hash=expected,
                    failure_detail=None,
                )
            guard = {
                "record_type": "O2_GUARD",
                "hypothesis_id": hypothesis_id,
                "label": label,
                "count": int(count),
                "threshold": O2_THRESHOLD,
                "triggered": bool(count > 0),
            }
            if existing is None:
                self.ledger.append(_json_safe(guard))
            if count > 0:
                return self._finish_o2(
                    existing, attempt_id, hypothesis_id, label, design_hash,
                    slot_index, campaign_id, realization=realization,
                )

        try:
            record = self._primary_payload(
                spec=spec,
                label=label,
                hypothesis_id=hypothesis_id,
                campaign_id=campaign_id,
                design_hash=design_hash,
                attempt_id=attempt_id,
                slot_index=slot_index,
                is_fold=is_fold,
                oos_fold=oos_fold,
                realization=realization,
            )
        except (C1BindingError, _d7b.D7bBindingError, _pqb.PQBindingError):
            return self._stage_b_attempt(
                existing=existing,
                label=label,
                hypothesis_id=hypothesis_id,
                design_hash=design_hash,
                attempt_id=attempt_id,
                slot_index=slot_index,
                factor_realization_hash=realization,
                expected_factor_realization_hash=expected,
                failure_detail=None,
            )

        if existing is not None:
            if canonical_json(existing) != canonical_json(_json_safe(record)):
                raise C1ReplayMismatchError(
                    "a re-run of a recorded attempt produced a different payload"
                )
            return REPLAY_IDENTICAL
        self.ledger.append(_json_safe(record))
        self.counters.consume_primary()
        self._run_diagnostics(
            spec, label, hypothesis_id, campaign_id, realization
        )
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
        factor_realization_hash: str | None,
        expected_factor_realization_hash: str | None,
        failure_detail: str | None,
    ) -> C1StopReason | str | None:
        record = self._stage_b_payload(
            label=label,
            hypothesis_id=hypothesis_id,
            design_hash=design_hash,
            attempt_id=attempt_id,
            slot_index=slot_index,
            factor_realization_hash=factor_realization_hash,
            expected_factor_realization_hash=expected_factor_realization_hash,
            failure_detail=failure_detail,
        )
        if existing is not None:
            if canonical_json(existing) != canonical_json(_json_safe(record)):
                raise C1ReplayMismatchError(
                    "stage-B replay produced a different record"
                )
            return C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
        self.ledger.append(_json_safe(record))
        self.counters.consume_primary()
        return C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE

    def _finish_o2(
        self,
        existing: Mapping[str, Any] | None,
        attempt_id: str,
        hypothesis_id: str,
        label: str,
        design_hash: str,
        slot_index: int,
        campaign_id: str,
        *,
        realization: str | None,
    ) -> C1StopReason | str | None:
        record = {
            "record_type": "PRIMARY_ATTEMPT",
            "campaign1_id": campaign_id,
            "campaign_attempt_id": attempt_id,
            "hypothesis_id": hypothesis_id,
            "label": label,
            "universe": PRIMARY_UNIVERSE,
            "slot_index": slot_index,
            "alpha": SEARCH_POLICY.alpha_per_test,
            "critical_value": CRITICAL_VALUE,
            "campaign_design_hash": design_hash,
            "factor_realization_hash": realization,
            "failure_detail": None,
            "declarations": {"is": None, "oos": None},
            "diagnostics": {"is": None, "oos": None},
            "screens": [],
            "classifications": {"is": None, "oos": None},
            "disposition": DISPOSITION_NOT_ASSESSED,
            "reasons": ["O2_ESCALATION"],
            "quarantine_sensitive": False,
            "delisting_statuses": {},
            "residual_cs_cells": {"is": None, "oos": None},
        }
        if existing is not None:
            if canonical_json(existing) != canonical_json(_json_safe(record)):
                raise C1ReplayMismatchError(
                    "an O2-escalated replay produced a different payload"
                )
            return REPLAY_IDENTICAL
        self.ledger.append(_json_safe(record))
        self.counters.consume_primary()
        return None

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
        realization: str | None,
    ) -> dict[str, Any]:
        panel_is = self.panel_of(spec, PRIMARY_UNIVERSE, is_fold)
        panel_oos = self.panel_of(spec, PRIMARY_UNIVERSE, oos_fold)
        decl_is = self._pq_declaration(
            spec, panel_is, is_fold, PRIMARY_UNIVERSE, SCREEN_ID,
            SCREEN_POLICY_SHA256,
        )
        decl_oos = self._pq_declaration(
            spec, panel_oos, oos_fold, PRIMARY_UNIVERSE, SCREEN_ID,
            SCREEN_POLICY_SHA256,
        )
        diag_is = self._build_diagnostics(spec, decl_is, panel_is)
        diag_oos = self._build_diagnostics(spec, decl_oos, panel_oos)
        screens_is = screen_outcomes(diag_is, FoldRole.IS)
        screens_oos = screen_outcomes(diag_oos, FoldRole.OOS)
        classify_is = classify_pq(diag_is, screens_is)
        classify_oos = classify_pq(diag_oos, screens_oos)
        disposition, reasons, quarantine_sensitive = attempt_disposition(
            diagnostics_is=diag_is,
            diagnostics_oos=diag_oos,
            screens_is=screens_is,
            screens_oos=screens_oos,
            classify_is=classify_is,
            classify_oos=classify_oos,
        )
        # E6.4 write-ahead: both artifacts are durable before the
        # PRIMARY_ATTEMPT record that references them is appended.
        self._persist_diagnostics(diag_is)
        self._persist_diagnostics(diag_oos)
        return {
            "record_type": "PRIMARY_ATTEMPT",
            "campaign1_id": campaign_id,
            "campaign_attempt_id": attempt_id,
            "hypothesis_id": hypothesis_id,
            "label": label,
            "universe": PRIMARY_UNIVERSE,
            "slot_index": slot_index,
            "alpha": SEARCH_POLICY.alpha_per_test,
            "critical_value": CRITICAL_VALUE,
            "campaign_design_hash": design_hash,
            "factor_realization_hash": realization,
            "failure_detail": None,
            "declarations": {
                "is": decl_is.declaration_hash,
                "oos": decl_oos.declaration_hash,
            },
            "diagnostics": {
                "is": diag_is.diagnostics_hash,
                "oos": diag_oos.diagnostics_hash,
            },
            "screens": [
                dict(screen.to_dict()) for screen in (*screens_is, *screens_oos)
            ],
            "classifications": {
                "is": classify_is.to_dict(),
                "oos": classify_oos.to_dict(),
            },
            "disposition": disposition.value,
            "reasons": reasons,
            "quarantine_sensitive": quarantine_sensitive,
            "delisting_statuses": {
                "is_a": classify_is.delisting_a,
                "is_b": classify_is.delisting_b,
                "oos_a": classify_oos.delisting_a,
                "oos_b": classify_oos.delisting_b,
            },
            "residual_cs_cells": {
                "is": diag_is.residual_cs_cells,
                "oos": diag_oos.residual_cs_cells,
            },
        }

    def _stage_b_payload(
        self,
        *,
        label: str,
        hypothesis_id: str,
        design_hash: str,
        attempt_id: str,
        slot_index: int,
        factor_realization_hash: str | None = None,
        expected_factor_realization_hash: str | None = None,
        failure_detail: str | None = None,
    ) -> dict[str, Any]:
        return {
            "record_type": "PRIMARY_ATTEMPT",
            "campaign1_id": None,
            "campaign_attempt_id": attempt_id,
            "hypothesis_id": hypothesis_id,
            "label": label,
            "universe": PRIMARY_UNIVERSE,
            "slot_index": slot_index,
            "alpha": SEARCH_POLICY.alpha_per_test,
            "critical_value": CRITICAL_VALUE,
            "campaign_design_hash": design_hash,
            "factor_realization_hash": factor_realization_hash,
            "expected_factor_realization_hash": expected_factor_realization_hash,
            "failure_detail": failure_detail,
            "declarations": {"is": None, "oos": None},
            "diagnostics": {"is": None, "oos": None},
            "screens": [],
            "classifications": {"is": None, "oos": None},
            "disposition": DISPOSITION_NOT_ASSESSED,
            "reasons": ["BINDING_OR_IDENTITY_FAILURE"],
            "quarantine_sensitive": False,
            "delisting_statuses": {},
            "residual_cs_cells": {"is": None, "oos": None},
        }

    def _pq_declaration(
        self,
        spec: FactorSpec,
        panel: pd.DataFrame,
        fold: Fold,
        universe: str,
        screen_id: str,
        screen_policy_hash: str,
    ) -> _pqb.PQEvaluationDeclaration:
        declaration = build_declaration(
            spec=spec,
            panel=panel,
            fold=fold,
            universe=universe,
            bindings=self.bindings,
            screen_id=screen_id,
            screen_policy_hash=screen_policy_hash,
        )
        return wrap_declaration(declaration, self.bindings)

    def _build_diagnostics(
        self,
        spec: FactorSpec,
        declaration: _pqb.PQEvaluationDeclaration,
        panel: pd.DataFrame,
    ) -> _pqb.PQDiagnostics:
        return build_pq_diagnostics(
            self.root,
            declaration,
            spec,
            panel,
            self.partition,
            overlay=self.overlay,
            inputs=self.inputs_by_semantic,
        )

    # -- E6 write-once diagnostics artifacts ----------------------------
    def _diagnostics_dir(self) -> Path:
        """``<ledger dir>/diagnostics`` (E6.2)."""
        return Path(self.ledger.path).parent / _store.DIAGNOSTICS_DIRNAME

    def _persist_diagnostics(self, diagnostics: Any) -> None:
        """Persist one PQDiagnostics and its attached D7b object (E6.1).

        Write-once and idempotent. A failure is a
        :class:`diagnostics_store.C1DiagnosticsError`, which follows the
        existing C1-LX section-8 untyped-failure path unchanged.
        """
        directory = self._diagnostics_dir()
        _store.persist(diagnostics, directory)
        attached = getattr(diagnostics, "b_diagnostics", None)
        if attached is not None:
            _store.persist(attached, directory)

    def _run_diagnostics(
        self,
        spec: FactorSpec,
        label: str,
        hypothesis_id: str,
        campaign_id: str,
        realization: str | None,
    ) -> None:
        for universe in DIAGNOSTIC_UNIVERSES:
            is_fold = _fold(self.partition, FoldRole.IS)
            oos_fold = _fold(self.partition, FoldRole.OOS)
            panel_is = self.panel_of(spec, universe, is_fold)
            panel_oos = self.panel_of(spec, universe, oos_fold)
            decl_is = self._pq_declaration(
                spec, panel_is, is_fold, universe, DIAG_SCREEN_ID,
                DIAG_SCREEN_POLICY_SHA256,
            )
            decl_oos = self._pq_declaration(
                spec, panel_oos, oos_fold, universe, DIAG_SCREEN_ID,
                DIAG_SCREEN_POLICY_SHA256,
            )
            design_hash = campaign_design_hash(
                decl_is.base,
                campaign1_id_value=campaign_id,
                pq_id=self.bindings.pq_id,
                pq_hash=self.bindings.pq_hash,
            )
            attempt_id = campaign_attempt_id(
                domain="campaign1-diagnostic-evaluation",
                campaign1_id_value=campaign_id,
                hypothesis_id=hypothesis_id,
                design_hash=design_hash,
            )
            existing = self.ledger.find_attempt(attempt_id)
            if existing is None:
                self.counters.check_diagnostic()
            try:
                diag_is = self._build_diagnostics(spec, decl_is, panel_is)
                diag_oos = self._build_diagnostics(spec, decl_oos, panel_oos)
                binding_failure = False
            except (C1BindingError, _d7b.D7bBindingError, _pqb.PQBindingError):
                diag_is = None
                diag_oos = None
                binding_failure = True
            # E6.4 write-ahead: persist before the DIAGNOSTIC_EVALUATION record
            # that references these hashes is appended.
            if diag_is is not None:
                self._persist_diagnostics(diag_is)
            if diag_oos is not None:
                self._persist_diagnostics(diag_oos)
            record = {
                "record_type": "DIAGNOSTIC_EVALUATION",
                "campaign1_id": campaign_id,
                "campaign_attempt_id": attempt_id,
                "hypothesis_id": hypothesis_id,
                "label": label,
                "universe": universe,
                "campaign_design_hash": design_hash,
                "factor_realization_hash": realization,
                "declarations": {
                    "is": decl_is.declaration_hash,
                    "oos": decl_oos.declaration_hash,
                },
                "diagnostics": {
                    "is": None if diag_is is None else diag_is.diagnostics_hash,
                    "oos": None if diag_oos is None else diag_oos.diagnostics_hash,
                },
                "binding_failure": binding_failure,
            }
            if existing is not None:
                if canonical_json(existing) != canonical_json(_json_safe(record)):
                    raise C1ReplayMismatchError(
                        "a diagnostic replay produced a different payload"
                    )
                continue
            self.ledger.append(_json_safe(record))
            self.counters.consume_diagnostic()
            if binding_failure:
                raise _pqb.PQBindingError(
                    "a diagnostic evaluation hit a binding failure"
                )

    # -- O2 --------------------------------------------------------------
    def _o2_count(self, spec: FactorSpec) -> int:
        if self.o2_count_of is None:
            raise C1BindingError("no O2 count provider was supplied")
        return int(self.o2_count_of(spec))

    # -- stop / state helpers -------------------------------------------
    def _existing_stop(self) -> C1StopReason | None:
        stops = self.ledger.records("CAMPAIGN_STOP")
        if not stops:
            return None
        value = stops[-1].get("stop_reason")
        try:
            return C1StopReason(value)
        except ValueError:  # pragma: no cover - defensive
            return C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE

    def _existing_campaign1_id(self) -> str | None:
        opens = self.ledger.records("CAMPAIGN_OPEN")
        if not opens:
            return None
        return opens[0].get("campaign1_id")

    def _replay_counters(self) -> None:
        self.counters.proposal_used = len(
            self.ledger.records("PRIMARY_ATTEMPT")
        )
        self.counters.experiment_used = self.counters.proposal_used
        self.counters.statistical_slots_used = self.counters.proposal_used
        self.counters.diagnostic_used = len(
            self.ledger.records("DIAGNOSTIC_EVALUATION")
        )
        opens = self.ledger.records("CAMPAIGN_OPEN")
        if opens:
            self._attempt_ordinal = 0

    def _append_stop(self, reason: C1StopReason) -> None:
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
        self, reason: C1StopReason, campaign_id: str
    ) -> CampaignRunResult:
        self._append_stop(reason)
        return CampaignRunResult(
            stop_reason=reason,
            campaign1_id=campaign_id,
            counters=self.counters.to_dict(),
            ledger_path=str(self.ledger.path),
        )


def _fold(partition: Partition, role: FoldRole) -> Fold:
    for fold in partition.folds:
        if fold.role is role:
            return fold
    raise C1BindingError(f"partition has no {role.value} fold")


def _lock_matches(existing: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    for key in (
        "search_policy",
        "family_id",
        "budgets",
        "critical_value_repr",
        "expected_factor_realizations",
        "program_id",
        "campaign1_id",
    ):
        if existing.get(key) != current.get(key):
            return False
    return True


def estimand_policy_content() -> dict[str, Any]:
    """The canonical ESTIMAND_POLICY content recorded at open."""
    return estimand_policy().to_content()


# ``build_pq_diagnostics`` / ``classify_pq`` are module-level bindings so the
# synthetic end-to-end tests can monkeypatch them (C1-LX section 11.14).
build_pq_diagnostics = _pqb.build_pq_diagnostics
classify_pq = _pqb.classify_pq


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
_DIAGNOSTIC_LABEL = "DESCRIPTIVE — NOT JUDGED"


def _diagnostics_detail(
    diagnostics_dir: Path,
    digest: str | None,
) -> dict[str, Any] | None:
    """Load, fully verify and flatten one diagnostics artifact (E6.5).

    ``load_verified`` fails closed on a missing, wrong-hash or non-canonical
    artifact. The D7b ``b_diagnostics_hash`` referenced from the verified
    payload is loaded and verified as well.
    """
    if digest is None:
        return None
    payload = _store.load_verified(diagnostics_dir, digest)
    b_digest = payload.get("b_diagnostics_hash")
    b_payload = (
        None
        if b_digest is None
        else _store.load_verified(diagnostics_dir, b_digest)
    )
    detail: dict[str, Any] = {
        "diagnostics_hash": digest,
        "b_diagnostics_hash": b_digest,
        "not_assessed": payload.get("not_assessed"),
        "support": payload.get("support"),
        "cs3": payload.get("cs3"),
        "cs3_status": payload.get("cs3_status"),
        "q_b": payload.get("q_b"),
        "q_s": payload.get("q_s"),
        "q_l": payload.get("q_l"),
        "n_flagged_formations": payload.get("n_flagged_formations"),
        "residual_cs_cells": payload.get("residual_cs_cells"),
        "liquidity_exposed": payload.get("liquidity_exposed"),
        "n_interior_b_missing": payload.get("n_interior_b_missing"),
        "cells": [
            {
                "cell": cell.get("cell"),
                "scenario": cell.get("scenario"),
                "estimand": cell.get("estimand"),
                "value": cell.get("value"),
                "raw_value": cell.get("raw_value"),
                "t_stat": cell.get("t_stat"),
                "status": cell.get("status"),
                "n_obs": cell.get("n_obs"),
                "n_index": cell.get("n_index"),
            }
            for cell in payload.get("cells", [])
        ],
    }
    detail["d7b"] = (
        None
        if b_payload is None
        else {
            "e_s": b_payload.get("e_s"),
            "b": b_payload.get("b"),
            "e_l": b_payload.get("e_l"),
            "n_b_formations": b_payload.get("n_b_formations"),
            "n_es_formations": b_payload.get("n_es_formations"),
            "n_el_formations": b_payload.get("n_el_formations"),
            "purged_cell_count": len(b_payload.get("purged_cells", [])),
            "t_t0": b_payload.get("t_t0"),
            "t_ts": b_payload.get("t_ts"),
            "n_obs_t0": b_payload.get("n_obs_t0"),
            "n_obs_ts": b_payload.get("n_obs_ts"),
        }
    )
    return detail


def render_report(
    ledger_path: Path | str,
    *,
    diagnostics_dir: Path | str | None = None,
) -> tuple[bytes, bytes]:
    """Render the deterministic (json, markdown) report from the ledger."""
    if diagnostics_dir is None:
        diagnostics_dir = (
            Path(ledger_path).parent / _store.DIAGNOSTICS_DIRNAME
        )
    diagnostics_dir = Path(diagnostics_dir)
    ledger = CampaignLedger(ledger_path)
    opens = ledger.records("CAMPAIGN_OPEN")
    attempts = ledger.records("PRIMARY_ATTEMPT")
    diagnostics = ledger.records("DIAGNOSTIC_EVALUATION")
    stops = ledger.records("CAMPAIGN_STOP")
    guards = ledger.records("O2_GUARD")

    detail_cache: dict[str, dict[str, Any]] = {}

    def detail_for(digest: str | None) -> dict[str, Any] | None:
        if digest is None:
            return None
        if digest not in detail_cache:
            detail_cache[digest] = _diagnostics_detail(diagnostics_dir, digest)
        return detail_cache[digest]

    # E6.5 fail closed: every non-null diagnostics hash referenced by a
    # PRIMARY_ATTEMPT or DIAGNOSTIC_EVALUATION record (and every referenced
    # b_diagnostics_hash) is loaded and verified before rendering.
    for record in (*attempts, *diagnostics):
        referenced = record.get("diagnostics")
        if not isinstance(referenced, Mapping):
            continue
        detail_for(referenced.get("is"))
        detail_for(referenced.get("oos"))

    by_label: dict[str, Mapping[str, Any]] = {
        str(record.get("label")): record for record in attempts
    }
    guard_by_label = {
        str(record.get("label")): record for record in guards
    }
    diag_by_label: dict[str, list[Mapping[str, Any]]] = {}
    for record in diagnostics:
        diag_by_label.setdefault(str(record.get("label")), []).append(record)

    hypotheses: list[dict[str, Any]] = []
    for spec in FACTOR_SPECS:
        label = spec.hypothesis or spec.id
        attempt = by_label.get(label)
        hypotheses.append(
            {
                "label": label,
                "hypothesis_id": None if attempt is None else attempt.get("hypothesis_id"),
                "factor_spec_hash": factor_spec_hash(spec),
                "expected_direction": "+1",
                "identity": {
                    "campaign1_id": (
                        None if not opens else opens[0].get("campaign1_id")
                    ),
                    "campaign_design_hash": (
                        None if attempt is None else attempt.get("campaign_design_hash")
                    ),
                    "dataset_id": (
                        None
                        if not opens
                        else opens[0].get("input_manifest", {}).get("dataset_id")
                    ),
                    "pq_id": (
                        None
                        if not opens
                        else opens[0].get("input_manifest", {}).get("pq_id")
                    ),
                    "pq_hash": (
                        None
                        if not opens
                        else opens[0].get("input_manifest", {}).get("pq_hash")
                    ),
                },
                "design": {
                    "universe": PRIMARY_UNIVERSE,
                    "partition_id": EXPECTED_PARTITION_ID,
                    "horizon": HORIZON,
                    "transaction_cost_bps": TRANSACTION_COST_BPS,
                    "slot_index": None if attempt is None else attempt.get("slot_index"),
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
                    "diagnostics_detail": (
                        None
                        if attempt is None
                        else {
                            "is": detail_for(
                                (attempt.get("diagnostics") or {}).get("is")
                            ),
                            "oos": detail_for(
                                (attempt.get("diagnostics") or {}).get("oos")
                            ),
                        }
                    ),
                    "screens": None if attempt is None else attempt.get("screens"),
                    "classifications": (
                        None if attempt is None else attempt.get("classifications")
                    ),
                    "residual_cs_cells": (
                        None if attempt is None else attempt.get("residual_cs_cells")
                    ),
                    "delisting_statuses": (
                        None
                        if attempt is None
                        else attempt.get("delisting_statuses")
                    ),
                },
                "outcome": {
                    "disposition": (
                        None if attempt is None else attempt.get("disposition")
                    ),
                    "reasons": [] if attempt is None else attempt.get("reasons"),
                    "quarantine_sensitive": (
                        None
                        if attempt is None
                        else attempt.get("quarantine_sensitive")
                    ),
                    "o2_guard_count": (
                        None
                        if label not in guard_by_label
                        else guard_by_label[label].get("count")
                    ),
                },
            }
        )

    payload = {
        "schema": "campaign1-development-report-v1",
        "campaign": {
            "program_id": PROGRAM_ID,
            "campaign1_id": None if not opens else opens[0].get("campaign1_id"),
            "family_id": None if not opens else opens[0].get("family_id"),
            "family_display_label": FAMILY_DISPLAY_LABEL,
            "critical_value": CRITICAL_VALUE,
            "budgets": dict(BUDGET_LIMITS),
            "partition_id": EXPECTED_PARTITION_ID,
        },
        "hypotheses": hypotheses,
        "diagnostics": {
            "label": _DIAGNOSTIC_LABEL,
            "record_type": "DIAGNOSTIC_EVALUATION",
            "evaluations": [
                {
                    **dict(record),
                    "judged": False,
                    "diagnostics_detail": {
                        "is": detail_for(
                            (record.get("diagnostics") or {}).get("is")
                        ),
                        "oos": detail_for(
                            (record.get("diagnostics") or {}).get("oos")
                        ),
                    },
                }
                for label in EXPECTED_LABELS
                for record in diag_by_label.get(label, [])
            ],
        },
        "stop_reason": None if not stops else stops[-1].get("stop_reason"),
        "claim_boundary": _CLAIM_BOUNDARY,
    }
    json_bytes = (canonical_json(_json_safe(payload)) + "\n").encode("utf-8")
    markdown_bytes = _render_markdown(payload).encode("utf-8")
    return json_bytes, markdown_bytes


def _render_markdown(payload: Mapping[str, Any]) -> str:
    lines: list[str] = []
    campaign = payload["campaign"]
    lines.append("# Campaign-1 development report")
    lines.append("")
    lines.append(f"- program_id: {campaign['program_id']}")
    lines.append(f"- campaign1_id: {campaign['campaign1_id']}")
    lines.append(f"- family_id: {campaign['family_id']}")
    lines.append(f"- critical_value: {campaign['critical_value']}")
    lines.append(f"- stop_reason: {payload['stop_reason']}")
    lines.append("")
    lines.append("## Hypotheses")
    for item in payload["hypotheses"]:
        outcome = item["outcome"]
        lines.append(
            f"### {item['label']} — {outcome['disposition']}"
            + (f"({', '.join(outcome['reasons'])})" if outcome["reasons"] else "")
        )
        lines.append(f"- hypothesis_id: {item['hypothesis_id']}")
        lines.append(f"- factor_spec_hash: {item['factor_spec_hash']}")
        lines.append(f"- expected_direction: {item['expected_direction']}")
        lines.append(f"- slot_index: {item['design']['slot_index']}")
        lines.append(f"- alpha: {item['design']['alpha']}")
        lines.append(f"- o2_guard_count: {outcome['o2_guard_count']}")
        lines.append("")
    lines.append(f"## Robustness diagnostics ({payload['diagnostics']['label']})")
    for record in payload["diagnostics"]["evaluations"]:
        lines.append(
            f"- {record.get('label')} {record.get('universe')}: "
            f"IS={record.get('diagnostics', {}).get('is')} "
            f"OOS={record.get('diagnostics', {}).get('oos')}"
        )
    lines.append("")
    lines.append("## Claim boundary")
    lines.append(payload["claim_boundary"])
    lines.append("")
    return "\n".join(lines)
