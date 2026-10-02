"""C1-LX — Campaign-1 development ledger and executor.

Effective authority (in order): ``docs/campaign1_precommitment.md`` (copied
verbatim here where frozen), the original frozen task
``worker_tasks/campaign1/task-c1-ledger-executor.md`` (C1-LX), C1-LX-E1
(Phase-8 ``family_id`` identity and the αᵢ source) and C1-LX-E2 (PQCell
``status is None`` assessed predicate, registry-import boundary, and the
Stage A / Stage B / Stage C binding-failure handling).

This module reuses the frozen existing value objects (Phase-8
``SearchPolicy``/``hypothesis_id_for``/``registry.content_hash``, PQB
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
    hypothesis_id_for,
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
    "CELL_MAPPING",
    "REPLAY_IDENTICAL",
    "PHASE9_STOP_REASON_MAP",
    # errors
    "C1CampaignError",
    "C1BindingError",
    "C1GovernanceConflict",
    "C1SealedFoldError",
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
    "hypothesis_identities",
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


def hypothesis_identities(
    factor_specs: Sequence[FactorSpec],
    provenance_by_label: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    """The C1-H1..H7 identity tuple (label, hashes, hypothesis id)."""
    identities: list[dict[str, str]] = []
    for spec in factor_specs:
        label = spec.hypothesis or spec.id
        provenance = provenance_by_label[label]
        identities.append(
            {
                "label": label,
                "factor_spec_hash": factor_spec_hash(spec),
                "factor_provenance_hash": provenance,
                "hypothesis_id": hypothesis_id_for(provenance),
            }
        )
    return tuple(identities)


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


def count_o2_cells(
    values: pd.DataFrame,
    formations: Sequence[date],
    members_by_formation: Mapping[date, Sequence[str]],
) -> int:
    """Count finite sub-threshold cells in the O2 denominator (integer only)."""
    count = 0
    for formation in formations:
        if formation not in values.index:
            continue
        members = members_by_formation.get(formation, ())
        for member in members:
            if member not in values.columns:
                continue
            value = values.at[formation, member]
            try:
                finite = math.isfinite(float(value))
            except (TypeError, ValueError):
                finite = False
            if finite and float(value) < O2_THRESHOLD:
                count += 1
    return count


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
    exercised offline with synthetic fixtures. ``provenance_of`` returns the
    Phase-6 ``EngineResult.content_hash`` for a spec; ``panel_of`` returns the
    fold/universe Phase-6 panel; ``o2_count_of`` returns the frozen O2 count.
    """

    def __init__(
        self,
        *,
        ledger: CampaignLedger,
        bindings: CampaignBindings,
        partition: Partition,
        factor_specs: Sequence[FactorSpec],
        provenance_of: Callable[[FactorSpec], str],
        panel_of: Callable[[FactorSpec, str, Fold], pd.DataFrame],
        overlay: Any,
        root: Path | str,
        inputs_by_semantic: Mapping[str, Any] | None = None,
        o2_count_of: Callable[[FactorSpec], int] | None = None,
        knowledge_log_path: Path | str | None = None,
        labels: Sequence[str] | None = None,
        require_kpit: bool = True,
    ) -> None:
        self.ledger = ledger
        self.bindings = bindings
        self.partition = partition
        self.factor_specs = tuple(factor_specs)
        self.provenance_of = provenance_of
        self.panel_of = panel_of
        self.overlay = overlay
        self.root = Path(root)
        self.inputs_by_semantic = dict(inputs_by_semantic or {})
        self.o2_count_of = o2_count_of
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
    def run(self) -> CampaignRunResult:
        recorded_stop = self._existing_stop()
        self._replay_counters()

        def terminal(reason: C1StopReason) -> CampaignRunResult:
            return self._terminal(reason, recorded_stop)

        try:
            provenance = {
                label: self.provenance_of(self._spec_by_label[label])
                for label in EXPECTED_LABELS
                if label in self._spec_by_label
            }
        except C1BindingError:
            return self._fatal(
                C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
            )
        identities = hypothesis_identities(self.factor_specs, provenance)

        if self.require_kpit and self.knowledge_log_path is not None:
            preflight = verify_kpit_ordering(
                self.knowledge_log_path,
                policy=estimand_policy(),
                identities=identities,
            )
        else:
            preflight = KpitPreflight(True, None, ())
        if not preflight.ok:
            self.ledger.verify()
            return CampaignRunResult(
                stop_reason=C1StopReason.GOVERNANCE_CONFLICT,
                campaign1_id=None,
                counters=self.counters.to_dict(),
                ledger_path=str(self.ledger.path),
            )

        try:
            campaign_id = self._open_campaign(preflight, identities)
        except C1GovernanceConflict:
            return CampaignRunResult(
                stop_reason=C1StopReason.GOVERNANCE_CONFLICT,
                campaign1_id=self._existing_campaign1_id(),
                counters=self.counters.to_dict(),
                ledger_path=str(self.ledger.path),
            )
        except C1BindingError:
            return terminal(
                C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
            )

        try:
            for label in self.labels:
                stop = self._process_label(label, campaign_id)
                if stop is None or stop == REPLAY_IDENTICAL:
                    continue
                return terminal(stop)
        except _d7b.ReserveFirewallError:
            return terminal(C1StopReason.RESERVE_OR_FIREWALL_VIOLATION)
        except C1SealedFoldError:
            return terminal(C1StopReason.SEALED_FOLD_ACCESS_ATTEMPT)
        except C1GovernanceConflict:
            return terminal(C1StopReason.GOVERNANCE_CONFLICT)
        except (C1BindingError, _d7b.D7bBindingError, _pqb.PQBindingError):
            return terminal(
                C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
            )
        except C1Stop as stop:
            return terminal(stop.reason)
        except C1ReplayMismatchError:
            # E2.3: the ledger is unchanged; the mismatch itself is fatal.
            raise
        except Exception:
            if recorded_stop is None:
                self.ledger.append(
                    _json_safe(
                        {
                            "record_type": "CAMPAIGN_STOP",
                            "stop_reason": (
                                C1StopReason
                                .INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE.value
                            ),
                            "budget_counters": self.counters.to_dict(),
                        }
                    )
                )
            raise

        return terminal(C1StopReason.ALL_PRECOMMITTED_HYPOTHESES_EVALUATED)

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
    def _process_label(
        self, label: str, campaign_id: str
    ) -> C1StopReason | str | None:
        if label not in self._spec_by_label:
            raise C1GovernanceConflict(f"proposal {label!r} is not precommitted")
        spec = self._spec_by_label[label]
        try:
            provenance = self.provenance_of(spec)
            hypothesis_id = hypothesis_id_for(provenance)
        except C1BindingError:
            return C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE

        is_fold = _fold(self.partition, FoldRole.IS)
        oos_fold = _fold(self.partition, FoldRole.OOS)
        panel_is = self.panel_of(spec, PRIMARY_UNIVERSE, is_fold)
        declaration_is = build_declaration(
            spec=spec,
            panel=panel_is,
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

        if label in ("C1-H6", "C1-H7"):
            count = self._o2_count(spec)
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
                    slot_index, campaign_id,
                )

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
            )
        except (C1BindingError, _d7b.D7bBindingError, _pqb.PQBindingError):
            record = self._stage_b_payload(
                label=label,
                hypothesis_id=hypothesis_id,
                design_hash=design_hash,
                attempt_id=attempt_id,
                slot_index=slot_index,
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

        if existing is not None:
            if canonical_json(existing) != canonical_json(_json_safe(record)):
                raise C1ReplayMismatchError(
                    "a re-run of a recorded attempt produced a different payload"
                )
            return REPLAY_IDENTICAL
        self.ledger.append(_json_safe(record))
        self.counters.consume_primary()
        self._run_diagnostics(spec, label, hypothesis_id, campaign_id)
        return None

    def _finish_o2(
        self,
        existing: Mapping[str, Any] | None,
        attempt_id: str,
        hypothesis_id: str,
        label: str,
        design_hash: str,
        slot_index: int,
        campaign_id: str,
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

    def _run_diagnostics(
        self,
        spec: FactorSpec,
        label: str,
        hypothesis_id: str,
        campaign_id: str,
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
            record = {
                "record_type": "DIAGNOSTIC_EVALUATION",
                "campaign1_id": campaign_id,
                "campaign_attempt_id": attempt_id,
                "hypothesis_id": hypothesis_id,
                "label": label,
                "universe": universe,
                "campaign_design_hash": design_hash,
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

    def _terminal(
        self, reason: C1StopReason, recorded_stop: C1StopReason | None
    ) -> CampaignRunResult:
        """Return the terminal result, never appending a second CAMPAIGN_STOP."""
        if recorded_stop is not None:
            return CampaignRunResult(
                stop_reason=recorded_stop,
                campaign1_id=self._existing_campaign1_id(),
                counters=self.counters.to_dict(),
                ledger_path=str(self.ledger.path),
            )
        self.ledger.append(
            _json_safe(
                {
                    "record_type": "CAMPAIGN_STOP",
                    "stop_reason": reason.value,
                    "budget_counters": self.counters.to_dict(),
                }
            )
        )
        return CampaignRunResult(
            stop_reason=reason,
            campaign1_id=self._existing_campaign1_id(),
            counters=self.counters.to_dict(),
            ledger_path=str(self.ledger.path),
        )

    def _fatal(
        self,
        reason: C1StopReason,
        *,
        campaign_id: str | None = None,
    ) -> CampaignRunResult:
        self.ledger.append(
            _json_safe(
                {
                    "record_type": "CAMPAIGN_STOP",
                    "stop_reason": reason.value,
                    "budget_counters": self.counters.to_dict(),
                }
            )
        )
        return CampaignRunResult(
            stop_reason=reason,
            campaign1_id=campaign_id,
            counters=self.counters.to_dict(),
            ledger_path=str(self.ledger.path),
        )

    def _stop(
        self, reason: C1StopReason, campaign_id: str
    ) -> CampaignRunResult:
        self.ledger.append(
            _json_safe(
                {
                    "record_type": "CAMPAIGN_STOP",
                    "stop_reason": reason.value,
                    "budget_counters": self.counters.to_dict(),
                }
            )
        )
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


def render_report(
    ledger_path: Path | str,
) -> tuple[bytes, bytes]:
    """Render the deterministic (json, markdown) report from the ledger."""
    ledger = CampaignLedger(ledger_path)
    opens = ledger.records("CAMPAIGN_OPEN")
    attempts = ledger.records("PRIMARY_ATTEMPT")
    diagnostics = ledger.records("DIAGNOSTIC_EVALUATION")
    stops = ledger.records("CAMPAIGN_STOP")
    guards = ledger.records("O2_GUARD")

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
                dict(record)
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
