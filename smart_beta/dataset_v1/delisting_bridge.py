"""Discovery Dataset v1.1 — D7b development delisting-sensitivity bridge.

D7b is a **development-only diagnostic bridge**.  It turns bound D7a observed
return evidence, a hash-bound :class:`CampaignEvaluationDeclaration`, a
:class:`~smart_beta.spec.factor_spec.FactorSpec` and a factor panel into
T0/TS development diagnostics and then into a contract-§9.6 classification.

It is **not** ``engine.evaluate`` and never builds an ``EvaluationRecord`` or a
``DecisionRecord``.  It calls no Phase-8/9/10 module and no vendor module.  It
consumes, reads and evaluates **no** final-holdout observation and **no**
Dataset-v1 reserve observation: final-holdout consumption is exactly zero.

Authority (frozen), in precedence order: ``docs/
discovery_dataset_v1_contract.md`` §§9.2, 9.4, 9.5, 9.6, 10, 11.2; the Dataset
v1.1 D7b and D7 clarification documents; ``worker_tasks/dataset_v1/
task-d7-forward-returns.md`` (the D7a schema); ``worker_tasks/phase7/
phase7-plan.md`` §§5, 6.1, 8.1; and ``worker_tasks/phase8/phase8-plan.md``
§§7.3, 11.

The module reuses existing Phase-7 lower-level primitives only and never
reimplements their mathematics.  No provider or network call is ever made and
no credential value is read.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from smart_beta.data.schema import DATE_COL, FACTOR_PANEL_SCHEMA, STOCK_COL, VALUE_COL
from smart_beta.evaluation import engine as engine_mod
from smart_beta.evaluation import forward_returns as fr
from smart_beta.evaluation import metrics as metrics_mod
from smart_beta.evaluation import partition as partition_mod
from smart_beta.evaluation import portfolio as portfolio_mod
from smart_beta.evaluation import robustness as robustness_mod
from smart_beta.evaluation import spec as spec_mod
from smart_beta.engines import portfolio_sort as portfolio_sort_mod
from smart_beta.spec.factor_spec import FactorSpec

__all__ = [
    "D7A_ARTIFACT_ID",
    "D7A_RETURNS_HASH",
    "D7A_MANIFEST_SHA256",
    "D7A_FILE_SHA256",
    "CODE_SHA256",
    "DOCUMENT_SHA256",
    "RESERVE_START",
    "DEVELOPMENT_END",
    "HMAX",
    "SCOPE",
    "SCENARIO_T0",
    "SCENARIO_TS",
    "STATUS_NOT_SENSITIVE",
    "STATUS_DELISTING_SENSITIVE",
    "STATUS_NOT_ASSESSED",
    "NOT_ASSESSED_DECLARATION_MISSING",
    "NOT_ASSESSED_DECLARATION_INCONSISTENT",
    "NOT_ASSESSED_VALUE_WEIGHT_UNSUPPORTED",
    "NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP",
    "NOT_ASSESSED_NOT_DEVELOPMENT",
    "NOT_ASSESSED_NO_BENCHMARK_AUTHORITY",
    "NOT_ASSESSED_UNSUPPORTED_ORIENTATION",
    "NOT_ASSESSED_SCREEN_MISMATCH",
    "NOT_ASSESSED_SCREEN_MISSING",
    "ADMISSIBLE",
    "ADMISSIBLE_TERMINAL",
    "ADMISSIBLE_TERMINAL_EMPTY",
    "TERMINAL_OBSERVED_PATH_INCOMPLETE",
    "OBSERVED_PATH_INCOMPLETE",
    "FACTOR_VALUE_MISSING",
    "PURGED_RESERVED_OUTCOME",
    "PURGED_FOLD_BOUNDARY",
    "DELISTING_RETURN_NOT_CERTIFIED",
    "FileBinding",
    "DocumentBinding",
    "D7aBinding",
    "Bindings",
    "D7bError",
    "D7bInputError",
    "D7bBindingError",
    "ReserveFirewallError",
    "FinalHoldoutFirewallError",
    "D7bConsistencyError",
    "default_bindings",
    "fold_key",
    "canonical_json",
    "content_hash",
    "CampaignEvaluationDeclaration",
    "ScreenOutcome",
    "PurgedCell",
    "FormationAccounting",
    "GroupAccounting",
    "AccountingRecord",
    "FactorIndependentAccounting",
    "DevelopmentDiagnostics",
    "D7bDevelopmentSensitivityRecord",
    "compute_factor_independent_accounting",
    "build_development_diagnostics",
    "classify_delisting_sensitivity",
]


# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

RESERVE_START = date(2023, 7, 3)
DEVELOPMENT_END = date(2023, 6, 30)
HMAX = 63

SCOPE = "DEVELOPMENT_ONLY"
SCENARIO_T0 = "T0"
SCENARIO_TS = "TS"

STATUS_NOT_SENSITIVE = "NOT_SENSITIVE"
STATUS_DELISTING_SENSITIVE = "DELISTING_SENSITIVE"
STATUS_NOT_ASSESSED = "NOT_ASSESSED"

NOT_ASSESSED_DECLARATION_MISSING = "NOT_ASSESSED_DECLARATION_MISSING"
NOT_ASSESSED_DECLARATION_INCONSISTENT = "NOT_ASSESSED_DECLARATION_INCONSISTENT"
NOT_ASSESSED_VALUE_WEIGHT_UNSUPPORTED = "NOT_ASSESSED_VALUE_WEIGHT_UNSUPPORTED"
NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP = "NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP"
NOT_ASSESSED_NOT_DEVELOPMENT = "NOT_ASSESSED_NOT_DEVELOPMENT"
NOT_ASSESSED_NO_BENCHMARK_AUTHORITY = "NOT_ASSESSED_NO_BENCHMARK_AUTHORITY"
NOT_ASSESSED_UNSUPPORTED_ORIENTATION = "NOT_ASSESSED_UNSUPPORTED_ORIENTATION"
NOT_ASSESSED_SCREEN_MISMATCH = "NOT_ASSESSED_SCREEN_MISMATCH"
NOT_ASSESSED_SCREEN_MISSING = "NOT_ASSESSED_SCREEN_MISSING"

DELISTING_RETURN_NOT_CERTIFIED = "DELISTING_RETURN_NOT_CERTIFIED"

ADMISSIBLE = "ADMISSIBLE"
ADMISSIBLE_TERMINAL = "ADMISSIBLE_TERMINAL"
ADMISSIBLE_TERMINAL_EMPTY = "ADMISSIBLE_TERMINAL_EMPTY"
TERMINAL_OBSERVED_PATH_INCOMPLETE = "TERMINAL_OBSERVED_PATH_INCOMPLETE"
OBSERVED_PATH_INCOMPLETE = "OBSERVED_PATH_INCOMPLETE"
FACTOR_VALUE_MISSING = "FACTOR_VALUE_MISSING"

ADMISSIBLE_STATES = frozenset(
    {ADMISSIBLE, ADMISSIBLE_TERMINAL, ADMISSIBLE_TERMINAL_EMPTY}
)
INADMISSIBLE_STATES = frozenset(
    {TERMINAL_OBSERVED_PATH_INCOMPLETE, OBSERVED_PATH_INCOMPLETE}
)

PURGED_RESERVED_OUTCOME = "PURGED_RESERVED_OUTCOME"
PURGED_FOLD_BOUNDARY = "PURGED_FOLD_BOUNDARY"

ECONOMIC_LONG = "LONG"
ECONOMIC_SHORT = "SHORT"
ECONOMIC_MIDDLE = "MIDDLE"
ECONOMIC_NONE = "NONE"

SIGN_MINUS_ONE_ADMITTED: tuple[spec_mod.MetricKey, ...] = (
    spec_mod.MetricKey.IC,
    spec_mod.MetricKey.RANK_IC,
    spec_mod.MetricKey.LONG_SHORT,
    spec_mod.MetricKey.SHARPE,
)

_BASIS_BY_KEY: dict[spec_mod.MetricKey, str] = {
    spec_mod.MetricKey.IC: "NONE",
    spec_mod.MetricKey.RANK_IC: "NONE",
    spec_mod.MetricKey.LONG_SHORT: "GROSS",
    spec_mod.MetricKey.SHARPE: "GROSS",
    spec_mod.MetricKey.MAX_DRAWDOWN: "GROSS",
    spec_mod.MetricKey.TURNOVER_COST_ADJUSTED: "NET",
    spec_mod.MetricKey.BENCHMARK_RELATIVE: "NONE",
}

# --- frozen, hash-bound production bindings --------------------------------

D7A_ARTIFACT_ID = "r-34565f074d64ccda"
D7A_RETURNS_HASH = (
    "4c487e47fd613f3f272109d14b43a27e061f9cc31e5b6d6bcefd4abc97994861"
)
D7A_MANIFEST_RELATIVE_PATH = f"derived/returns/{D7A_ARTIFACT_ID}/returns.json"
D7A_MANIFEST_SHA256 = (
    "6093bec9730e5bd0a6e9e1410db165fca148e3be1b01ef2534db12e34370886e"
)
D7A_FILE_SHA256: dict[str, str] = {
    f"derived/returns/{D7A_ARTIFACT_ID}/members.parquet": (
        "fcb2c9df2918a7bd43399b220f9595fc94d8a5dc49542219698ecafb7c6b4488"
    ),
    f"derived/returns/{D7A_ARTIFACT_ID}/horizons.parquet": (
        "61a2ac310021f153676445de7f0122ef74f67910e6506a6e5ef1e286f3d1ccbb"
    ),
    f"derived/returns/{D7A_ARTIFACT_ID}/labels.parquet": (
        "262b8b1d445dfb329d33359966a9deba67bd5999ccdc01dd8183eda6f4f2645e"
    ),
    f"derived/returns/{D7A_ARTIFACT_ID}/daily_returns.parquet": (
        "d4286d0e48ca5fa86d3631b5c2d5f1bb04fb18accbffd95a61fe6694276b7911"
    ),
}

CODE_SHA256: dict[str, str] = {
    "smart_beta/evaluation/forward_returns.py": (
        "e7d261a4096ac84f9ea3a522d82e3bf48fd84c3111894c28bb94bf5beebd45de"
    ),
    "smart_beta/evaluation/portfolio.py": (
        "5dea8adc581ae1bfe2bc7b771db37f36a71c2331cf702d9c8b7174039c3ed2dc"
    ),
    "smart_beta/evaluation/metrics.py": (
        "747282a2ee7cc7b63d0511d29ab3bdf8c3c9aceea9fb965dd64de543d76831a8"
    ),
    "smart_beta/evaluation/engine.py": (
        "4ef0caa7d453a27a35c23fc3c7d7ece4b17c132cff651efa6dd79cb2a39d31c8"
    ),
    "smart_beta/evaluation/robustness.py": (
        "86f8be60303d0fe9618d0c153036732315e557f5ec648406e306752876090abc"
    ),
    "smart_beta/evaluation/partition.py": (
        "36bb418b6c5662c671c6a869ec9b015df75e47a994395a530addd0b53783be72"
    ),
    "smart_beta/evaluation/spec.py": (
        "a0e151356a3435c2a3ce98bb4d4cfce25a78111c561419aefb2c1cb77f2ae4de"
    ),
    "smart_beta/engines/portfolio_sort.py": (
        "d9776fdca3186795b87b3825f2c43b77e1ed79c66bf4ec4e05cb2a00f2178b57"
    ),
    "smart_beta/spec/factor_spec.py": (
        "ab6c65d312d6fe1e421c489e6ad8045a50e265c8f73dbe6a07738e1bf5458c56"
    ),
}

DOCUMENT_SHA256: dict[str, tuple[str, str]] = {
    "contract": (
        "docs/discovery_dataset_v1_contract.md",
        "d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3",
    ),
    "d7b_clarification": (
        "docs/discovery_dataset_v1_1_d7b_clarification.md",
        "7cf2f702982bf24179010b6adcdc37f867d0c70803afe4fc7809898f22324bad",
    ),
    "d7_clarification": (
        "docs/discovery_dataset_v1_1_d7_clarification.md",
        "bc8e6920d31dd429beb62030123a721a7293558ba57fe8e3d4d6cd6368a96946",
    ),
    "d7a_task": (
        "worker_tasks/dataset_v1/task-d7-forward-returns.md",
        "f3f3c99ad1a03833be49737c6a591a7337ee25b92b5e6029801c54d1da4cfb44",
    ),
    "phase7_plan": (
        "worker_tasks/phase7/phase7-plan.md",
        "8b2009533ee00e8a04e0c65b6853711f01387592e42f60c0b97170d23183a13e",
    ),
    "phase8_plan": (
        "worker_tasks/phase8/phase8-plan.md",
        "755f57182310d40d5ba3f095bac7e92a9297e62ddc5bf38bb34617cb40f9e32f",
    ),
}

_MEMBERS_COLUMNS = (
    "formation_date",
    "security_key",
    "ticker",
    "rank",
    "top_200",
    "top_500",
    "top_1000",
    "listed_end_dev",
    "listing_status",
    "terminal_evidence_source",
)
_LABELS_COLUMNS = (
    "formation_date",
    "security_key",
    "h",
    "endpoint",
    "label_state",
    "terminal_affected",
    "observed_path_complete",
    "n_observed_sessions",
    "n_post_terminal_sessions",
    "n_invalid_sessions",
    "observed_segment_end",
    "first_invalid_session",
    "first_invalid_status",
)
_HORIZONS_COLUMNS = ("formation_date", "h", "endpoint", "crosses_reserve")
_DAILY_COLUMNS = (
    "security_key",
    "date",
    "prev_session",
    "session_status",
    "daily_total_return",
    "ca_event_count",
    "corp_action_suspect",
)

_VALID = "VALID"

_UNIVERSE_FLAG: dict[str, str] = {
    "TOP_200": "top_200",
    "TOP_500": "top_500",
    "TOP_1000": "top_1000",
}

_RESERVE_COMPONENTS = frozenset({"reserve_sealed", "raw", "dsc_evidence"})

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Canonical JSON / content hashing
# ---------------------------------------------------------------------------


def _jsonable(obj: object) -> object:
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, int) and not isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, np.floating):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Mapping):
        return {str(key): _jsonable(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(value) for value in obj]
    if hasattr(obj, "to_dict"):
        return _jsonable(obj.to_dict())
    raise TypeError(f"cannot canonicalize {type(obj).__name__}")


def canonical_json(obj: object) -> str:
    """Deterministic canonical JSON (sorted keys, no whitespace, ASCII)."""
    return json.dumps(
        _jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def content_hash(obj: object) -> str:
    """SHA-256 of :func:`canonical_json`."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class D7bError(Exception):
    """Base class for every D7b fail-closed error."""


class D7bInputError(D7bError, ValueError):
    """A required input is missing, unreadable or malformed."""


class D7bBindingError(D7bError, ValueError):
    """A hash binding, code-authority hash or document hash mismatches."""


class ReserveFirewallError(D7bError, ValueError):
    """A Dataset-v1 reserve path or date was encountered."""


class FinalHoldoutFirewallError(D7bError, ValueError):
    """A final-holdout path or date was encountered."""


class D7bConsistencyError(D7bError, ValueError):
    """Internal inconsistency detected by the reconciliation checks."""


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileBinding:
    relative_path: str
    sha256: str


@dataclass(frozen=True)
class DocumentBinding:
    name: str
    path: str
    sha256: str


@dataclass(frozen=True)
class D7aBinding:
    artifact_id: str
    returns_hash: str
    manifest_relative_path: str
    manifest_sha256: str
    files: tuple[FileBinding, ...]

    def allowlist(self) -> frozenset[str]:
        allowed = {self.manifest_relative_path}
        allowed.update(binding.relative_path for binding in self.files)
        return frozenset(allowed)

    def file_binding(self, relative_path: str) -> FileBinding | None:
        for binding in self.files:
            if binding.relative_path == relative_path:
                return binding
        return None


@dataclass(frozen=True)
class Bindings:
    d7a: D7aBinding
    code_sha256: tuple[tuple[str, str], ...]
    documents: tuple[DocumentBinding, ...]

    def allowlist(self) -> frozenset[str]:
        return self.d7a.allowlist()

    def code_map(self) -> dict[str, str]:
        return dict(self.code_sha256)


def default_bindings() -> Bindings:
    """The frozen production bindings (task spec §2, exact values)."""
    return Bindings(
        d7a=D7aBinding(
            artifact_id=D7A_ARTIFACT_ID,
            returns_hash=D7A_RETURNS_HASH,
            manifest_relative_path=D7A_MANIFEST_RELATIVE_PATH,
            manifest_sha256=D7A_MANIFEST_SHA256,
            files=tuple(
                FileBinding(path, digest)
                for path, digest in sorted(D7A_FILE_SHA256.items())
            ),
        ),
        code_sha256=tuple(sorted(CODE_SHA256.items())),
        documents=tuple(
            DocumentBinding(name, str(_REPO_ROOT / relative), digest)
            for name, (relative, digest) in sorted(DOCUMENT_SHA256.items())
        ),
    )


DEFAULT_BINDINGS = default_bindings()


# ---------------------------------------------------------------------------
# Guarded resolver
# ---------------------------------------------------------------------------


class _Resolver:
    def __init__(self, root: Path | str, bindings: Bindings):
        self._root = Path(root)
        self._allow = bindings.allowlist()

    @property
    def allowlist(self) -> frozenset[str]:
        return self._allow

    def resolve(self, relative_path: str) -> Path:
        raw = str(relative_path)
        pure = PurePosixPath(raw)
        if pure.is_absolute() or ".." in pure.parts:
            raise D7bInputError(f"path {raw!r} escapes <root>")
        if any(part in _RESERVE_COMPONENTS for part in pure.parts):
            raise ReserveFirewallError(
                f"refusing reserve path component in {raw!r}"
            )
        if raw not in self._allow:
            raise ReserveFirewallError(f"path {raw!r} is not allowlisted")
        return self._root / pure

    def read_bytes(self, relative_path: str) -> bytes:
        path = self.resolve(relative_path)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise D7bInputError(f"cannot read {path}: {exc}") from exc

    def read_parquet(
        self,
        relative_path: str,
        *,
        columns: Sequence[str],
        filters: Sequence[tuple[str, str, Any]] | None = None,
    ) -> pd.DataFrame:
        path = self.resolve(relative_path)
        try:
            table = pq.read_table(
                str(path), columns=list(columns), filters=list(filters or [])
            )
        except OSError as exc:
            raise D7bInputError(f"cannot read {path}: {exc}") from exc
        return table.to_pandas()


# ---------------------------------------------------------------------------
# Binding verification
# ---------------------------------------------------------------------------


def _verify_code(bindings: Bindings) -> dict[str, str]:
    code = bindings.code_map()
    if set(code) != set(CODE_SHA256):
        raise D7bBindingError(
            "code-authority binding must name exactly the frozen Phase-7 "
            f"files, got {sorted(code)}"
        )
    verified: dict[str, str] = {}
    for relative, expected in sorted(code.items()):
        path = _REPO_ROOT / relative
        try:
            actual = _sha256_file(path)
        except OSError as exc:
            raise D7bBindingError(
                f"cannot read code-authority file {relative}: {exc}"
            ) from exc
        if actual != expected:
            raise D7bBindingError(
                f"code-authority {relative} hash {actual} != bound {expected}"
            )
        verified[relative] = actual
    return verified


def _verify_documents(bindings: Bindings) -> dict[str, str]:
    if {doc.name for doc in bindings.documents} != set(DOCUMENT_SHA256):
        raise D7bBindingError(
            "document binding must name exactly the frozen authority "
            f"documents, got {sorted(doc.name for doc in bindings.documents)}"
        )
    verified: dict[str, str] = {}
    for doc in sorted(bindings.documents, key=lambda item: item.name):
        try:
            actual = _sha256_file(Path(doc.path))
        except OSError as exc:
            raise D7bBindingError(f"cannot read document {doc.name}: {exc}") from exc
        if actual != doc.sha256:
            raise D7bBindingError(
                f"document {doc.name} hash {actual} != bound {doc.sha256}"
            )
        verified[doc.name] = actual
    return verified


def _verify_d7a_files(
    resolver: _Resolver, bindings: Bindings
) -> dict[str, str]:
    d7a = bindings.d7a
    manifest_blob = resolver.read_bytes(d7a.manifest_relative_path)
    actual_manifest = _sha256_bytes(manifest_blob)
    if actual_manifest != d7a.manifest_sha256:
        raise D7bBindingError(
            f"D7a manifest hash {actual_manifest} != bound {d7a.manifest_sha256}"
        )
    try:
        manifest = json.loads(manifest_blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise D7bBindingError(f"D7a manifest is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise D7bBindingError("D7a manifest is not a JSON object")
    if manifest.get("returns_id") not in (None, d7a.artifact_id):
        raise D7bBindingError(
            f"D7a manifest returns_id {manifest.get('returns_id')!r} != "
            f"bound artifact {d7a.artifact_id!r}"
        )
    if manifest.get("returns_hash") != d7a.returns_hash:
        raise D7bBindingError(
            f"D7a returns_hash {manifest.get('returns_hash')!r} != bound "
            f"{d7a.returns_hash!r}"
        )
    files_sha256 = manifest.get("files_sha256")
    if not isinstance(files_sha256, dict):
        raise D7bBindingError("D7a manifest lacks files_sha256")
    verified: dict[str, str] = {}
    for binding in d7a.files:
        declared = files_sha256.get(binding.relative_path)
        if declared != binding.sha256:
            raise D7bBindingError(
                f"D7a manifest binds {binding.relative_path} to {declared!r}, "
                f"expected {binding.sha256!r}"
            )
        actual = _sha256_bytes(resolver.read_bytes(binding.relative_path))
        if actual != binding.sha256:
            raise D7bBindingError(
                f"D7a file {binding.relative_path} hash {actual} != bound "
                f"{binding.sha256}"
            )
        verified[binding.relative_path] = actual
    return verified


# ---------------------------------------------------------------------------
# Partition fold key
# ---------------------------------------------------------------------------


def fold_key(fold: partition_mod.Fold) -> str:
    index = "" if fold.fold_index is None else f":{fold.fold_index}"
    return (
        f"{fold.role.value}{index}:"
        f"{fold.start.date().isoformat()}:{fold.end.date().isoformat()}"
    )


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CampaignEvaluationDeclaration:
    """A caller-supplied, immutable, hash-bound evaluation declaration."""

    precommitment_ref: str
    factor_spec_version: str
    factor_panel_sha256: str
    partition_id: str
    fold_key: str
    universe: str
    horizon: int
    n_groups: int
    winsorization: float
    transaction_cost_bps: float
    periods_per_year: int
    weighting: str
    primary_metric: spec_mod.MetricKey
    basis: str
    screen_id: str
    screen_policy_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.primary_metric, spec_mod.MetricKey):
            try:
                object.__setattr__(
                    self, "primary_metric", spec_mod.MetricKey(self.primary_metric)
                )
            except (ValueError, KeyError) as exc:
                raise D7bInputError(
                    f"primary_metric {self.primary_metric!r} is not a MetricKey"
                ) from exc
        for attribute in (
            "precommitment_ref",
            "factor_spec_version",
            "factor_panel_sha256",
            "partition_id",
            "fold_key",
            "screen_id",
            "screen_policy_hash",
        ):
            value = getattr(self, attribute)
            if not isinstance(value, str) or not value:
                raise D7bInputError(f"{attribute} must be a non-empty string")
        if self.universe not in _UNIVERSE_FLAG:
            raise D7bInputError(
                f"universe must be one of {sorted(_UNIVERSE_FLAG)}, got "
                f"{self.universe!r}"
            )
        if self.weighting not in ("EQUAL", "VALUE"):
            raise D7bInputError(
                f"weighting must be EQUAL or VALUE, got {self.weighting!r}"
            )
        if self.basis not in ("NONE", "GROSS", "NET"):
            raise D7bInputError(
                f"basis must be NONE, GROSS or NET, got {self.basis!r}"
            )
        if isinstance(self.horizon, bool) or not (1 <= int(self.horizon) <= HMAX):
            raise D7bInputError(
                f"horizon must be an int in 1..{HMAX}, got {self.horizon!r}"
            )
        if isinstance(self.n_groups, bool) or int(self.n_groups) < 2:
            raise D7bInputError(
                f"n_groups must be an int >= 2, got {self.n_groups!r}"
            )
        if isinstance(self.periods_per_year, bool) or int(self.periods_per_year) < 1:
            raise D7bInputError(
                f"periods_per_year must be an int >= 1, got {self.periods_per_year!r}"
            )
        for attribute in ("winsorization", "transaction_cost_bps"):
            value = getattr(self, attribute)
            if isinstance(value, bool) or not isinstance(
                value, (int, float, np.integer, np.floating)
            ):
                raise D7bInputError(f"{attribute} must be a finite number")
            if not math.isfinite(float(value)):
                raise D7bInputError(f"{attribute} must be finite")

    def to_dict(self) -> dict[str, Any]:
        return {
            "precommitment_ref": self.precommitment_ref,
            "factor_spec_version": self.factor_spec_version,
            "factor_panel_sha256": self.factor_panel_sha256,
            "partition_id": self.partition_id,
            "fold_key": self.fold_key,
            "universe": self.universe,
            "horizon": int(self.horizon),
            "n_groups": int(self.n_groups),
            "winsorization": float(self.winsorization),
            "transaction_cost_bps": float(self.transaction_cost_bps),
            "periods_per_year": int(self.periods_per_year),
            "weighting": self.weighting,
            "primary_metric": self.primary_metric.value,
            "basis": self.basis,
            "screen_id": self.screen_id,
            "screen_policy_hash": self.screen_policy_hash,
        }

    @property
    def declaration_hash(self) -> str:
        return content_hash(self.to_dict())


@dataclass(frozen=True)
class ScreenOutcome:
    """One hash-bound Campaign/Phase-8 screen verdict for T0 or TS."""

    screen_id: str
    screen_policy_hash: str
    declaration_hash: str
    diagnostics_hash: str
    scenario: str
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_id": self.screen_id,
            "screen_policy_hash": self.screen_policy_hash,
            "declaration_hash": self.declaration_hash,
            "diagnostics_hash": self.diagnostics_hash,
            "scenario": self.scenario,
            "passed": bool(self.passed),
        }


@dataclass(frozen=True)
class PurgedCell:
    formation_date: date
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"formation_date": self.formation_date, "reason": self.reason}


@dataclass(frozen=True)
class GroupAccounting:
    group: int
    n_stocks: int
    n_returns: int
    n_affected: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": int(self.group),
            "n_stocks": int(self.n_stocks),
            "n_returns": int(self.n_returns),
            "n_affected": int(self.n_affected),
        }


@dataclass(frozen=True)
class FormationAccounting:
    formation_date: date
    n_universe: int
    n_affected: int
    n_short: int
    n_affected_short: int
    n_long: int
    n_affected_long: int
    n_admissible: int
    n_admissible_terminal: int
    n_admissible_terminal_empty: int
    n_terminal_incomplete: int
    n_observed_incomplete: int
    n_factor_missing: int
    groups: tuple[GroupAccounting, ...]

    @property
    def affected_short_fraction(self) -> float | None:
        if self.n_short == 0:
            return None
        return self.n_affected_short / self.n_short

    @property
    def affected_long_fraction(self) -> float | None:
        if self.n_long == 0:
            return None
        return self.n_affected_long / self.n_long

    def to_dict(self) -> dict[str, Any]:
        return {
            "formation_date": self.formation_date,
            "n_universe": int(self.n_universe),
            "n_affected": int(self.n_affected),
            "n_short": int(self.n_short),
            "n_affected_short": int(self.n_affected_short),
            "n_long": int(self.n_long),
            "n_affected_long": int(self.n_affected_long),
            "affected_short_fraction": self.affected_short_fraction,
            "affected_long_fraction": self.affected_long_fraction,
            "n_admissible": int(self.n_admissible),
            "n_admissible_terminal": int(self.n_admissible_terminal),
            "n_admissible_terminal_empty": int(self.n_admissible_terminal_empty),
            "n_terminal_incomplete": int(self.n_terminal_incomplete),
            "n_observed_incomplete": int(self.n_observed_incomplete),
            "n_factor_missing": int(self.n_factor_missing),
            "groups": [group.to_dict() for group in self.groups],
        }


@dataclass(frozen=True)
class AccountingRecord:
    formation_date: date
    stock_id: str
    horizon: int
    scenario: str
    group: int | None
    economic_leg: str
    admissibility: str
    terminal_affected: bool
    label_state: str
    expected_admissible: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "formation_date": self.formation_date,
            "stock_id": self.stock_id,
            "horizon": int(self.horizon),
            "scenario": self.scenario,
            "group": None if self.group is None else int(self.group),
            "economic_leg": self.economic_leg,
            "admissibility": self.admissibility,
            "terminal_affected": bool(self.terminal_affected),
            "label_state": self.label_state,
            "expected_admissible": bool(self.expected_admissible),
        }


@dataclass(frozen=True)
class FactorIndependentAccounting:
    """§5.1–§5.2 plus B and per-reason counts; computes no return value."""

    not_assessed: str | None
    declaration_hash: str | None
    factor_spec_version: str | None
    partition_id: str | None
    fold_key: str | None
    fold_role: str | None
    fold_start: date | None
    fold_end: date | None
    universe: str | None
    horizon: int | None
    d7a_artifact_id: str | None
    d7a_returns_hash: str | None
    d7a_file_hashes: tuple[tuple[str, str], ...]
    code_sha256: tuple[tuple[str, str], ...]
    document_hashes: tuple[tuple[str, str], ...]
    formations: tuple[date, ...]
    purged_cells: tuple[PurgedCell, ...]
    formation_accounting: tuple[FormationAccounting, ...]
    affected_reason_counts: tuple[tuple[str, int], ...]
    b: float | None
    n_b_formations: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "not_assessed": self.not_assessed,
            "declaration_hash": self.declaration_hash,
            "factor_spec_version": self.factor_spec_version,
            "partition_id": self.partition_id,
            "fold_key": self.fold_key,
            "fold_role": self.fold_role,
            "fold_start": self.fold_start,
            "fold_end": self.fold_end,
            "universe": self.universe,
            "horizon": self.horizon,
            "d7a_artifact_id": self.d7a_artifact_id,
            "d7a_returns_hash": self.d7a_returns_hash,
            "d7a_file_hashes": list(self.d7a_file_hashes),
            "code_sha256": list(self.code_sha256),
            "document_hashes": list(self.document_hashes),
            "formations": list(self.formations),
            "purged_cells": [cell.to_dict() for cell in self.purged_cells],
            "formation_accounting": [
                item.to_dict() for item in self.formation_accounting
            ],
            "affected_reason_counts": list(self.affected_reason_counts),
            "b": self.b,
            "n_b_formations": int(self.n_b_formations),
        }

    @property
    def content_hash(self) -> str:
        return content_hash(self.to_dict())


@dataclass(frozen=True)
class DevelopmentDiagnostics:
    """The in-memory T0/TS development diagnostics (no scenario panel is kept)."""

    not_assessed: str | None
    declaration_hash: str
    factor_spec_version: str | None
    factor_panel_sha256: str | None
    partition_id: str
    fold_key: str
    fold_role: str
    fold_start: date
    fold_end: date
    universe: str
    horizon: int
    n_groups: int
    winsorization: float
    transaction_cost_bps: float
    periods_per_year: int
    primary_metric: str
    basis: str
    sign: int
    orientation_rule: str
    screen_id: str
    screen_policy_hash: str
    d7a_artifact_id: str
    d7a_returns_hash: str
    d7a_file_hashes: tuple[tuple[str, str], ...]
    code_sha256: tuple[tuple[str, str], ...]
    document_hashes: tuple[tuple[str, str], ...]
    formations: tuple[date, ...]
    purged_cells: tuple[PurgedCell, ...]
    formation_accounting: tuple[FormationAccounting, ...]
    b: float | None
    e_s: float | None
    e_l: float | None
    n_b_formations: int
    n_es_formations: int
    n_el_formations: int
    value_t0: float | None
    value_ts: float | None
    t_t0: float | None
    t_ts: float | None
    n_obs_t0: int | None
    n_obs_ts: int | None
    economic_t0: float | None
    economic_ts: float | None
    reconciliation: tuple[tuple[str, bool], ...]
    accounting_records_hash: str
    accounting_records: tuple[AccountingRecord, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "not_assessed": self.not_assessed,
            "declaration_hash": self.declaration_hash,
            "factor_spec_version": self.factor_spec_version,
            "factor_panel_sha256": self.factor_panel_sha256,
            "partition_id": self.partition_id,
            "fold_key": self.fold_key,
            "fold_role": self.fold_role,
            "fold_start": self.fold_start,
            "fold_end": self.fold_end,
            "universe": self.universe,
            "horizon": int(self.horizon),
            "n_groups": int(self.n_groups),
            "winsorization": self.winsorization,
            "transaction_cost_bps": self.transaction_cost_bps,
            "periods_per_year": int(self.periods_per_year),
            "primary_metric": self.primary_metric,
            "basis": self.basis,
            "sign": int(self.sign),
            "orientation_rule": self.orientation_rule,
            "screen_id": self.screen_id,
            "screen_policy_hash": self.screen_policy_hash,
            "d7a_artifact_id": self.d7a_artifact_id,
            "d7a_returns_hash": self.d7a_returns_hash,
            "d7a_file_hashes": list(self.d7a_file_hashes),
            "code_sha256": list(self.code_sha256),
            "document_hashes": list(self.document_hashes),
            "formations": list(self.formations),
            "purged_cells": [cell.to_dict() for cell in self.purged_cells],
            "formation_accounting": [
                item.to_dict() for item in self.formation_accounting
            ],
            "b": self.b,
            "e_s": self.e_s,
            "e_l": self.e_l,
            "n_b_formations": int(self.n_b_formations),
            "n_es_formations": int(self.n_es_formations),
            "n_el_formations": int(self.n_el_formations),
            "value_t0": self.value_t0,
            "value_ts": self.value_ts,
            "t_t0": self.t_t0,
            "t_ts": self.t_ts,
            "n_obs_t0": self.n_obs_t0,
            "n_obs_ts": self.n_obs_ts,
            "economic_t0": self.economic_t0,
            "economic_ts": self.economic_ts,
            "reconciliation": [
                {"check": name, "passed": bool(passed)}
                for name, passed in self.reconciliation
            ],
            "accounting_records_hash": self.accounting_records_hash,
            "accounting_record_count": len(self.accounting_records),
        }

    @property
    def diagnostics_hash(self) -> str:
        return content_hash(self.to_dict())


@dataclass(frozen=True)
class D7bDevelopmentSensitivityRecord:
    """The §9.6 development sensitivity classification (no verdict)."""

    diagnostics_hash: str
    declaration_hash: str
    screen_id: str
    screen_policy_hash: str
    clause_a: bool
    clause_b: bool
    clause_c: bool
    status: str
    not_assessed_reason: str | None
    economic_t0: float | None
    economic_ts: float | None
    e_s: float | None
    b: float | None
    advancement_blocked: bool
    holdout_authorized: bool
    final_holdout_consumed: bool
    confirmation_evidence: bool
    scope: str
    delisting_return_status: str = DELISTING_RETURN_NOT_CERTIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnostics_hash": self.diagnostics_hash,
            "declaration_hash": self.declaration_hash,
            "screen_id": self.screen_id,
            "screen_policy_hash": self.screen_policy_hash,
            "clause_a": bool(self.clause_a),
            "clause_b": bool(self.clause_b),
            "clause_c": bool(self.clause_c),
            "status": self.status,
            "not_assessed_reason": self.not_assessed_reason,
            "economic_t0": self.economic_t0,
            "economic_ts": self.economic_ts,
            "e_s": self.e_s,
            "b": self.b,
            "advancement_blocked": bool(self.advancement_blocked),
            "holdout_authorized": bool(self.holdout_authorized),
            "final_holdout_consumed": bool(self.final_holdout_consumed),
            "confirmation_evidence": bool(self.confirmation_evidence),
            "scope": self.scope,
            "delisting_return_status": self.delisting_return_status,
        }

    @property
    def record_hash(self) -> str:
        return content_hash(self.to_dict())


# ---------------------------------------------------------------------------
# Internal loading
# ---------------------------------------------------------------------------


@dataclass
class _Loaded:
    formations: tuple[date, ...]
    purged_cells: tuple[PurgedCell, ...]
    members: pd.DataFrame
    labels: pd.DataFrame
    daily: pd.DataFrame
    universe_by_formation: dict[date, tuple[str, ...]]
    label_by_key: dict[tuple[date, str], dict[str, Any]]
    endpoint_by_formation: dict[date, date]


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _as_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(value)


def _d7a_path(bindings: Bindings, name: str) -> str:
    return f"derived/returns/{bindings.d7a.artifact_id}/{name}"


def _load_d7a(
    resolver: _Resolver,
    bindings: Bindings,
    declaration: CampaignEvaluationDeclaration,
    partition: partition_mod.Partition,
    fold: partition_mod.Fold,
) -> _Loaded:
    horizon = declaration.horizon
    start = fold.start.date()
    end = fold.end.date()

    candidate_frame = resolver.read_parquet(
        _d7a_path(bindings, "members.parquet"),
        columns=["formation_date"],
        filters=[("formation_date", ">=", start), ("formation_date", "<", end)],
    )
    candidate = sorted(
        {
            parsed
            for parsed in (
                _as_date(value) for value in candidate_frame["formation_date"].tolist()
            )
            if parsed is not None
        }
    )

    horizons = resolver.read_parquet(
        _d7a_path(bindings, "horizons.parquet"),
        columns=list(_HORIZONS_COLUMNS),
        filters=[
            ("formation_date", ">=", start),
            ("formation_date", "<", end),
            ("h", "==", horizon),
        ],
    )
    horizon_by_formation: dict[date, dict[str, Any]] = {}
    for row in horizons.to_dict("records"):
        formation = _as_date(row["formation_date"])
        if formation is not None:
            horizon_by_formation[formation] = row

    formations: list[date] = []
    purged: list[PurgedCell] = []
    endpoint_by_formation: dict[date, date] = {}
    for formation in candidate:
        row = horizon_by_formation.get(formation)
        if row is None:
            raise D7bConsistencyError(
                f"no horizons row for formation {formation} and h={horizon}"
            )
        endpoint = _as_date(row["endpoint"])
        if endpoint is None:
            raise D7bConsistencyError(
                f"horizons endpoint missing for {formation} h={horizon}"
            )
        if _as_bool(row["crosses_reserve"]):
            purged.append(PurgedCell(formation, PURGED_RESERVED_OUTCOME))
            continue
        if endpoint >= RESERVE_START:
            raise ReserveFirewallError(
                f"cell ({formation}, h={horizon}) endpoint {endpoint} is in "
                "the reserve without crosses_reserve"
            )
        if not fold.contains_interval(
            pd.Timestamp(formation), pd.Timestamp(endpoint)
        ):
            purged.append(PurgedCell(formation, PURGED_FOLD_BOUNDARY))
            continue
        formations.append(formation)
        endpoint_by_formation[formation] = endpoint

    purged_sorted = tuple(
        sorted(purged, key=lambda cell: (cell.formation_date, cell.reason))
    )
    if not formations:
        raise D7bInputError("the declared fold has no evaluable formations")
    formation_list = tuple(formations)

    members = resolver.read_parquet(
        _d7a_path(bindings, "members.parquet"),
        columns=list(_MEMBERS_COLUMNS),
        filters=[
            ("formation_date", "in", [pd.Timestamp(f) for f in formation_list])
        ],
    )
    labels = resolver.read_parquet(
        _d7a_path(bindings, "labels.parquet"),
        columns=list(_LABELS_COLUMNS),
        filters=[
            ("formation_date", "in", [pd.Timestamp(f) for f in formation_list]),
            ("h", "==", horizon),
        ],
    )
    min_formation = min(formation_list)
    universe_keys = sorted({str(key) for key in members["security_key"].tolist()})
    daily = resolver.read_parquet(
        _d7a_path(bindings, "daily_returns.parquet"),
        columns=list(_DAILY_COLUMNS),
        filters=[
            ("date", ">", pd.Timestamp(min_formation)),
            ("date", "<", pd.Timestamp(end)),
            ("security_key", "in", universe_keys),
        ],
    )

    _post_load_assertions(members, labels, daily, start, end)

    flag = _UNIVERSE_FLAG[declaration.universe]
    members_by_formation: dict[date, list[str]] = {
        formation: [] for formation in formation_list
    }
    for row in members.to_dict("records"):
        formation = _as_date(row["formation_date"])
        if formation is None or not _as_bool(row.get(flag)):
            continue
        members_by_formation.setdefault(formation, []).append(
            str(row["security_key"])
        )
    universe_by_formation = {
        formation: tuple(sorted(members_by_formation.get(formation, ())))
        for formation in formation_list
    }

    label_by_key: dict[tuple[date, str], dict[str, Any]] = {}
    for row in labels.to_dict("records"):
        formation = _as_date(row["formation_date"])
        if formation is None:
            continue
        label_by_key[(formation, str(row["security_key"]))] = row

    return _Loaded(
        formations=formation_list,
        purged_cells=purged_sorted,
        members=members,
        labels=labels,
        daily=daily,
        universe_by_formation=universe_by_formation,
        label_by_key=label_by_key,
        endpoint_by_formation=endpoint_by_formation,
    )


def _post_load_assertions(
    members: pd.DataFrame,
    labels: pd.DataFrame,
    daily: pd.DataFrame,
    start: date,
    end: date,
) -> None:
    for value in daily["date"].tolist():
        parsed = _as_date(value)
        if parsed is None:
            continue
        if parsed >= RESERVE_START:
            raise ReserveFirewallError(
                f"daily_returns.date holds a reserve date {parsed}"
            )
        if not (start <= parsed < end):
            raise FinalHoldoutFirewallError(
                f"daily_returns.date {parsed} is outside the fold"
            )
    for column in ("endpoint", "observed_segment_end", "first_invalid_session"):
        if column not in labels:
            continue
        for value in labels[column].tolist():
            parsed = _as_date(value)
            if parsed is None:
                continue
            if parsed >= RESERVE_START:
                raise ReserveFirewallError(
                    f"labels.{column} holds a reserve date {parsed}"
                )
            if not (start <= parsed < end):
                raise FinalHoldoutFirewallError(
                    f"labels.{column} date {parsed} is outside the fold"
                )
    if "listed_end_dev" in members:
        for value in members["listed_end_dev"].tolist():
            parsed = _as_date(value)
            if parsed is not None and parsed >= RESERVE_START:
                raise ReserveFirewallError(
                    f"members.listed_end_dev holds a reserve date {parsed}"
                )


# ---------------------------------------------------------------------------
# Declaration / domain validation
# ---------------------------------------------------------------------------


def _resolve_fold(
    declaration: CampaignEvaluationDeclaration,
    partition: partition_mod.Partition,
) -> partition_mod.Fold:
    if not isinstance(partition, partition_mod.Partition):
        raise D7bInputError("partition must be a Partition value")
    if partition.partition_id != declaration.partition_id:
        raise D7bBindingError(
            f"partition_id {partition.partition_id} != declared "
            f"{declaration.partition_id}"
        )
    for fold in partition.folds:
        if fold_key(fold) == declaration.fold_key:
            return fold
    raise D7bBindingError(f"unknown fold_key {declaration.fold_key!r} in partition")


def _fold_overlaps_holdout(
    fold: partition_mod.Fold, holdout: partition_mod.Fold
) -> bool:
    return bool(fold.start < holdout.end and holdout.start < fold.end)


def _domain_reason(
    fold: partition_mod.Fold, partition: partition_mod.Partition
) -> str | None:
    if fold.role.value == "holdout":
        return NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP
    holdout = partition.holdout_fold
    if holdout is None:
        return NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP
    if _fold_overlaps_holdout(fold, holdout):
        return NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP
    if fold.end.date() > DEVELOPMENT_END:
        return NOT_ASSESSED_NOT_DEVELOPMENT
    return None


def _metric_reason(
    declaration: CampaignEvaluationDeclaration, sign: int
) -> str | None:
    metric = declaration.primary_metric
    if declaration.weighting != "EQUAL":
        return NOT_ASSESSED_VALUE_WEIGHT_UNSUPPORTED
    if metric not in set(engine_mod.PRIMARY_METRIC_KEYS):
        return STATUS_NOT_ASSESSED
    expected = _BASIS_BY_KEY.get(metric)
    if expected is not None and declaration.basis != expected:
        return NOT_ASSESSED_DECLARATION_INCONSISTENT
    if metric is spec_mod.MetricKey.BENCHMARK_RELATIVE:
        return NOT_ASSESSED_NO_BENCHMARK_AUTHORITY
    if sign == -1 and metric not in SIGN_MINUS_ONE_ADMITTED:
        return NOT_ASSESSED_UNSUPPORTED_ORIENTATION
    return None


def _orientation_rule(sign: int, metric: spec_mod.MetricKey) -> str:
    if sign == 1:
        return "PASSTHROUGH"
    if metric in SIGN_MINUS_ONE_ADMITTED:
        return "NEGATED"
    return "UNSUPPORTED"


# ---------------------------------------------------------------------------
# Admissibility
# ---------------------------------------------------------------------------


def _admissibility(label: Mapping[str, Any]) -> str:
    state = str(label.get("label_state"))
    affected = _as_bool(label.get("terminal_affected"))
    if state == "COMPLETE" and not affected:
        return ADMISSIBLE
    if state == "COMPLETE" and affected:
        return ADMISSIBLE_TERMINAL
    if state == "EMPTY_OBSERVED_PATH_IDENTITY":
        return ADMISSIBLE_TERMINAL_EMPTY
    if state == "INCOMPLETE" and affected:
        return TERMINAL_OBSERVED_PATH_INCOMPLETE
    if state == "INCOMPLETE" and not affected:
        return OBSERVED_PATH_INCOMPLETE
    raise D7bConsistencyError(
        f"unexpected D7a label state {state!r} (terminal_affected={affected})"
    )


# ---------------------------------------------------------------------------
# Factor panel / grouping
# ---------------------------------------------------------------------------


def _canonical_factor_hash(panel: pd.DataFrame) -> str:
    ordered = panel.sort_values([DATE_COL, STOCK_COL], kind="mergesort")
    rows: list[list[Any]] = []
    for row in ordered.itertuples(index=False):
        parsed = _as_date(row.date)
        value = row.value
        if value is None or (isinstance(value, float) and math.isnan(value)):
            encoded: str | None = None
        else:
            encoded = float(value).hex()
        rows.append([parsed.isoformat(), str(row.stock_id), encoded])
    return content_hash(rows)


def _validate_factor_panel(
    factor_panel: pd.DataFrame,
    *,
    declaration: CampaignEvaluationDeclaration,
    formations: Sequence[date],
    universe_by_formation: Mapping[date, tuple[str, ...]],
) -> pd.DataFrame:
    panel = factor_panel.copy(deep=True)
    panel[DATE_COL] = pd.to_datetime(panel[DATE_COL])
    panel[STOCK_COL] = panel[STOCK_COL].astype("string")
    panel[VALUE_COL] = panel[VALUE_COL].astype("float64")
    FACTOR_PANEL_SCHEMA.validate(panel, name="factor_panel")

    allowed_dates = set(formations)
    for value in panel[DATE_COL].tolist():
        parsed = _as_date(value)
        if parsed is None or parsed not in allowed_dates:
            raise FinalHoldoutFirewallError(
                f"factor panel date {parsed} is not in the evaluated fold"
            )

    universe_lookup = {
        formation: set(universe_by_formation.get(formation, ()))
        for formation in formations
    }
    for row in panel.itertuples(index=False):
        parsed = _as_date(row.date)
        if str(row.stock_id) not in universe_lookup.get(parsed, set()):
            raise D7bInputError(
                f"factor row for non-member {row.stock_id!r} at {parsed}"
            )

    digest = _canonical_factor_hash(panel)
    if digest != declaration.factor_panel_sha256:
        raise D7bBindingError(
            f"factor_panel_sha256 {digest} != declared "
            f"{declaration.factor_panel_sha256}"
        )
    return panel


def _base_panel(
    factor_panel: pd.DataFrame,
    *,
    formations: Sequence[date],
    universe_by_formation: Mapping[date, tuple[str, ...]],
) -> pd.DataFrame:
    value_lookup: dict[tuple[date, str], float] = {}
    for row in factor_panel.itertuples(index=False):
        value_lookup[(_as_date(row.date), str(row.stock_id))] = float(row.value)
    records: list[tuple[date, str, float]] = []
    for formation in formations:
        for stock in universe_by_formation.get(formation, ()):
            records.append(
                (formation, stock, value_lookup.get((formation, stock), float("nan")))
            )
    base = pd.DataFrame(records, columns=[DATE_COL, STOCK_COL, VALUE_COL])
    base[DATE_COL] = pd.to_datetime(base[DATE_COL])
    base[STOCK_COL] = base[STOCK_COL].astype("string")
    base[VALUE_COL] = base[VALUE_COL].astype("float64")
    return base.sort_values([DATE_COL, STOCK_COL], kind="mergesort").reset_index(
        drop=True
    )


def _winsorize(base: pd.DataFrame, winsorization: float) -> pd.DataFrame:
    return robustness_mod.winsorize_panel(
        base, winsorization, date_col=DATE_COL, value_col=VALUE_COL
    )


def _frozen_groups(
    winsorized: pd.DataFrame, n_groups: int
) -> dict[tuple[date, str], float]:
    groups: dict[tuple[date, str], float] = {}
    for formation, sub in winsorized.groupby(DATE_COL, sort=True):
        assigned = portfolio_sort_mod.assign_groups(sub[VALUE_COL], n_groups)
        for date_value, stock, group in zip(
            sub[DATE_COL], sub[STOCK_COL], assigned.to_numpy(dtype="float64")
        ):
            groups[(_as_date(date_value), str(stock))] = float(group)
    return groups


# ---------------------------------------------------------------------------
# Observed returns (P7-B)
# ---------------------------------------------------------------------------


def _observed_panel(daily: pd.DataFrame) -> pd.DataFrame:
    records: list[tuple[date, str, float]] = []
    for row in daily.itertuples(index=False):
        parsed = _as_date(row.date)
        if parsed is None:
            continue
        raw = row.daily_total_return
        if str(row.session_status) != _VALID:
            adj = float("nan")
        elif raw is None or (isinstance(raw, float) and math.isnan(raw)):
            adj = float("nan")
        else:
            adj = float(raw)
        records.append((parsed, str(row.security_key), adj))
    observed = pd.DataFrame(
        records, columns=[DATE_COL, STOCK_COL, fr.DEFAULT_RETURN_COL]
    )
    observed[DATE_COL] = pd.to_datetime(observed[DATE_COL])
    observed[STOCK_COL] = observed[STOCK_COL].astype("string")
    observed[fr.DEFAULT_RETURN_COL] = observed[fr.DEFAULT_RETURN_COL].astype(
        "float64"
    )
    return (
        observed.sort_values([DATE_COL, STOCK_COL], kind="mergesort")
        .drop_duplicates(subset=[DATE_COL, STOCK_COL], keep="first")
        .reset_index(drop=True)
    )


def _align_subset(
    factor_rows: pd.DataFrame,
    observed: pd.DataFrame,
    horizon: int,
    fold: partition_mod.Fold,
) -> fr.ForwardReturnAlignment:
    def predicate(start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> bool:
        return not fold.contains_interval(start_ts, end_ts)

    return fr.align_forward_returns(
        factor_rows[[DATE_COL, STOCK_COL, VALUE_COL]],
        observed,
        horizon,
        return_col=fr.DEFAULT_RETURN_COL,
        partition_boundary=predicate,
    )


def _factor_rows_for(
    factor_panel: pd.DataFrame,
    members: Sequence[tuple[date, str, Mapping[str, Any]]],
) -> pd.DataFrame:
    keys = {(formation, stock) for formation, stock, _ in members}
    records = []
    for row in factor_panel.itertuples(index=False):
        parsed = _as_date(row.date)
        if (parsed, str(row.stock_id)) in keys:
            records.append((parsed, str(row.stock_id), float(row.value)))
    frame = pd.DataFrame(records, columns=[DATE_COL, STOCK_COL, VALUE_COL])
    frame[DATE_COL] = pd.to_datetime(frame[DATE_COL])
    frame[STOCK_COL] = frame[STOCK_COL].astype("string")
    frame[VALUE_COL] = frame[VALUE_COL].astype("float64")
    return frame


def _consume_alignment(
    aligned: fr.ForwardReturnAlignment,
    expected: Mapping[tuple[date, str], date | None],
    results: dict[tuple[date, str], float],
    what: str,
) -> None:
    if aligned.purged is not None and len(aligned.purged):
        raise D7bConsistencyError(
            f"{what}: P7-B purged {len(aligned.purged)} pre-purged cells"
        )
    for row in aligned.panel.itertuples(index=False):
        key = (_as_date(row.date), str(row.stock_id))
        if key not in expected:
            raise D7bConsistencyError(f"{what}: unexpected aligned row {key}")
        if str(row.status) != fr.STATUS_ALIGNED:
            raise D7bConsistencyError(
                f"{what}: {key} has P7-B status {row.status!r}, not aligned"
            )
        realization_end = _as_date(row.realization_end)
        if realization_end != expected[key]:
            raise D7bConsistencyError(
                f"{what}: {key} realization_end {realization_end} != "
                f"D7a {expected[key]}"
            )
        value = float(row.forward_return)
        if not math.isfinite(value):
            raise D7bConsistencyError(f"{what}: {key} is aligned but not finite")
        results[key] = value


def _admissible_returns(
    *,
    factor_panel: pd.DataFrame,
    observed: pd.DataFrame,
    fold: partition_mod.Fold,
    declaration: CampaignEvaluationDeclaration,
    loaded: _Loaded,
) -> dict[tuple[date, str], float]:
    """Return the P7-B compounded return for every non-terminal admissible row."""
    members: list[tuple[date, str, Mapping[str, Any]]] = []
    for formation in loaded.formations:
        for stock in loaded.universe_by_formation.get(formation, ()):
            label = loaded.label_by_key.get((formation, stock))
            if label is None:
                raise D7bConsistencyError(
                    f"missing D7a label for ({formation}, {stock}, "
                    f"h={declaration.horizon})"
                )
            if _admissibility(label) == ADMISSIBLE:
                members.append((formation, stock, label))
    if not members:
        return {}
    rows = _factor_rows_for(factor_panel, members)
    aligned = _align_subset(rows, observed, declaration.horizon, fold)
    expected = {
        (formation, stock): _as_date(label["endpoint"])
        for formation, stock, label in members
    }
    results: dict[tuple[date, str], float] = {}
    _consume_alignment(aligned, expected, results, "ADMISSIBLE")
    return results


def _terminal_obs_returns(
    *,
    factor_panel: pd.DataFrame,
    observed: pd.DataFrame,
    fold: partition_mod.Fold,
    declaration: CampaignEvaluationDeclaration,
    loaded: _Loaded,
) -> dict[tuple[date, str], float]:
    """Return R_obs for every non-empty terminal admissible row."""
    groups: dict[int, list[tuple[date, str, Mapping[str, Any]]]] = {}
    for formation in loaded.formations:
        for stock in loaded.universe_by_formation.get(formation, ()):
            label = loaded.label_by_key[(formation, stock)]
            if _admissibility(label) == ADMISSIBLE_TERMINAL:
                sessions = int(label["n_observed_sessions"])
                groups.setdefault(sessions, []).append((formation, stock, label))
    results: dict[tuple[date, str], float] = {}
    for sessions, members in sorted(groups.items()):
        rows = _factor_rows_for(factor_panel, members)
        aligned = _align_subset(rows, observed, sessions, fold)
        expected = {
            (formation, stock): _as_date(label["observed_segment_end"])
            for formation, stock, label in members
        }
        _consume_alignment(aligned, expected, results, "ADMISSIBLE_TERMINAL")
    return results


# ---------------------------------------------------------------------------
# Scenario panels
# ---------------------------------------------------------------------------


def _scenario_panels(
    *,
    winsorized: pd.DataFrame,
    groups: Mapping[tuple[date, str], float],
    r_obs: Mapping[tuple[date, str], float],
    observed_returns: Mapping[tuple[date, str], float],
    declaration: CampaignEvaluationDeclaration,
    loaded: _Loaded,
    sign: int,
) -> tuple[pd.DataFrame, pd.DataFrame, tuple[AccountingRecord, ...]]:
    n_groups = declaration.n_groups
    t0_values: list[float] = []
    ts_values: list[float] = []
    records: list[AccountingRecord] = []
    for row in winsorized.itertuples(index=False):
        key = (_as_date(row.date), str(row.stock_id))
        label = loaded.label_by_key.get(key)
        if label is None:
            raise D7bConsistencyError(f"missing label for {key}")
        state = _admissibility(label)
        group_value = groups.get(key)
        group = (
            None
            if group_value is None or math.isnan(group_value)
            else int(group_value)
        )
        affected = _as_bool(label.get("terminal_affected"))
        label_state = str(label.get("label_state"))
        if group is None:
            leg = ECONOMIC_NONE
        elif sign == 1:
            if group == n_groups:
                leg = ECONOMIC_LONG
            elif group == 1:
                leg = ECONOMIC_SHORT
            else:
                leg = ECONOMIC_MIDDLE
        else:
            if group == 1:
                leg = ECONOMIC_LONG
            elif group == n_groups:
                leg = ECONOMIC_SHORT
            else:
                leg = ECONOMIC_MIDDLE
        expected_admissible = group is not None and state in ADMISSIBLE_STATES
        record_state = state if group is not None else FACTOR_VALUE_MISSING
        t0_value = float("nan")
        ts_value = float("nan")
        if expected_admissible:
            if state == ADMISSIBLE:
                base = observed_returns.get(key)
                if base is None:
                    raise D7bConsistencyError(
                        f"{key} is admissible but has no P7-B return"
                    )
                t0_value = base
                ts_value = base
            else:
                r = 0.0 if state == ADMISSIBLE_TERMINAL_EMPTY else r_obs.get(key)
                if r is None:
                    raise D7bConsistencyError(
                        f"{key} terminal admissible but has no R_obs"
                    )
                t0_value = (1.0 + r) * (1.0 + 0.0) - 1.0
                tau = 0.0
                if leg == ECONOMIC_LONG:
                    tau = -1.0
                elif leg == ECONOMIC_SHORT:
                    tau = 1.0
                ts_value = (1.0 + r) * (1.0 + tau) - 1.0
            for value in (t0_value, ts_value):
                if not math.isfinite(value) or value < -1.0:
                    raise D7bConsistencyError(
                        f"{key} produced an inadmissible scenario value {value!r}"
                    )
        for scenario in (SCENARIO_T0, SCENARIO_TS):
            records.append(
                AccountingRecord(
                    formation_date=key[0],
                    stock_id=key[1],
                    horizon=declaration.horizon,
                    scenario=scenario,
                    group=group,
                    economic_leg=leg,
                    admissibility=record_state,
                    terminal_affected=affected,
                    label_state=label_state,
                    expected_admissible=expected_admissible,
                )
            )
        t0_values.append(t0_value)
        ts_values.append(ts_value)

    base = winsorized.copy().reset_index(drop=True)
    panel_t0 = base.copy()
    panel_t0[fr.FORWARD_RETURN_COL] = np.asarray(t0_values, dtype="float64")
    panel_ts = base.copy()
    panel_ts[fr.FORWARD_RETURN_COL] = np.asarray(ts_values, dtype="float64")
    panel_t0 = panel_t0.sort_values(
        [DATE_COL, STOCK_COL], kind="mergesort"
    ).reset_index(drop=True)
    panel_ts = panel_ts.sort_values(
        [DATE_COL, STOCK_COL], kind="mergesort"
    ).reset_index(drop=True)
    records.sort(
        key=lambda item: (
            item.scenario,
            item.formation_date,
            item.stock_id,
        )
    )
    return panel_t0, panel_ts, tuple(records)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def _finite_pairs(panel: pd.DataFrame) -> set[tuple[date, str]]:
    pairs: set[tuple[date, str]] = set()
    for row in panel.itertuples(index=False):
        value = row.value
        forward = getattr(row, fr.FORWARD_RETURN_COL)
        if (
            value is not None
            and not (isinstance(value, float) and math.isnan(value))
            and math.isfinite(float(value))
            and forward is not None
            and not (isinstance(forward, float) and math.isnan(forward))
            and math.isfinite(float(forward))
        ):
            pairs.add((_as_date(row.date), str(row.stock_id)))
    return pairs


def _reconcile(
    *,
    panel_t0: pd.DataFrame,
    panel_ts: pd.DataFrame,
    portfolio_t0: portfolio_mod.PortfolioEvaluation,
    portfolio_ts: portfolio_mod.PortfolioEvaluation,
    results_t0: tuple[float | None, float | None, int],
    results_ts: tuple[float | None, float | None, int],
    groups: Mapping[tuple[date, str], float],
    records: Sequence[AccountingRecord],
    declaration: CampaignEvaluationDeclaration,
) -> tuple[tuple[str, bool], ...]:
    checks: list[tuple[str, bool]] = []

    def fail(name: str, detail: str = "") -> None:
        raise D7bConsistencyError(
            f"reconciliation failed: {name}" + (f" ({detail})" if detail else "")
        )

    # 1. replay grouping equals G, identical under T0/TS.
    for scenario, panel in ((SCENARIO_T0, panel_t0), (SCENARIO_TS, panel_ts)):
        replay = _frozen_groups(panel, declaration.n_groups)
        if set(replay) != set(groups):
            fail("grouping_keys", scenario)
        for key, group in replay.items():
            expected = groups[key]
            same = group == expected or (
                math.isnan(group) and math.isnan(expected)
            )
            if not same:
                fail("grouping", f"{scenario} {key} {group} != {expected}")
    checks.append(("grouping_identity", True))

    # 2/3. sort_portfolios replay and exact gross spread.
    expected_pairs = {
        (record.formation_date, record.stock_id)
        for record in records
        if record.scenario == SCENARIO_T0
        and record.group is not None
        and record.expected_admissible
    }
    for scenario, panel, portfolio in (
        (SCENARIO_T0, panel_t0, portfolio_t0),
        (SCENARIO_TS, panel_ts, portfolio_ts),
    ):
        engine_input = panel.copy()
        engine_input["_unit"] = 1.0
        sorted_returns = portfolio_sort_mod.sort_portfolios(
            engine_input,
            char_col=VALUE_COL,
            ret_col=fr.FORWARD_RETURN_COL,
            weight_col="_unit",
            date_col=DATE_COL,
            n_groups=declaration.n_groups,
        )
        expected_stocks: dict[tuple[date, int], int] = {}
        expected_returns: dict[tuple[date, int], int] = {}
        for key, group in groups.items():
            if group is None or math.isnan(group):
                continue
            group_int = int(group)
            formation = key[0]
            expected_stocks[(formation, group_int)] = (
                expected_stocks.get((formation, group_int), 0) + 1
            )
            if key in expected_pairs:
                expected_returns[(formation, group_int)] = (
                    expected_returns.get((formation, group_int), 0) + 1
                )
        for row in sorted_returns.itertuples(index=False):
            parsed = _as_date(row.date)
            group_int = int(row.group)
            if int(row.n_stocks) != expected_stocks.get((parsed, group_int), 0):
                fail(
                    "n_stocks",
                    f"{scenario} {parsed} g{group_int} {row.n_stocks} != "
                    f"{expected_stocks.get((parsed, group_int), 0)}",
                )
            if int(row.n_returns) != expected_returns.get((parsed, group_int), 0):
                fail(
                    "n_returns",
                    f"{scenario} {parsed} g{group_int} {row.n_returns} != "
                    f"{expected_returns.get((parsed, group_int), 0)}",
                )
        spread = portfolio_sort_mod.long_short_return(
            sorted_returns,
            low_group=1,
            high_group=declaration.n_groups,
            measure=portfolio_sort_mod.EW_RETURN_COL,
            date_col=DATE_COL,
        )
        spread = spread.sort_index().astype("float64")
        gross = portfolio.gross_returns.astype("float64")
        if not np.array_equal(spread.to_numpy(), gross.to_numpy(), equal_nan=True):
            fail("gross_spread", scenario)
        members_by_date = {
            _as_date(item.date): int(item.n_members)
            for item in portfolio.accounting.itertuples(index=False)
        }
        stocks_by_date = {
            _as_date(item.date): int(item.n_stocks)
            for item in sorted_returns.groupby(DATE_COL)["n_stocks"].sum().reset_index().itertuples(index=False)
        }
        if members_by_date != stocks_by_date:
            fail("n_members", scenario)
        checks.append((f"sort_replay_{scenario}", True))

    # 4. portfolio accounting consistency.
    for scenario, panel, portfolio in (
        (SCENARIO_T0, panel_t0, portfolio_t0),
        (SCENARIO_TS, panel_ts, portfolio_ts),
    ):
        rows_by_date = {
            _as_date(item.date): int(item.n_obs)
            for item in panel.groupby(DATE_COL)
            .size()
            .reset_index(name="n_obs")
            .itertuples(index=False)
        }
        for row in portfolio.accounting.itertuples(index=False):
            parsed = _as_date(row.date)
            if int(row.n_obs) != rows_by_date.get(parsed, 0):
                fail("accounting_n_obs", scenario)
        checks.append((f"accounting_{scenario}", True))

    # 5. NaN/accounting consistency per scenario.
    for scenario, panel in ((SCENARIO_T0, panel_t0), (SCENARIO_TS, panel_ts)):
        expected = {
            (record.formation_date, record.stock_id): record
            for record in records
            if record.scenario == scenario
        }
        for row in panel.itertuples(index=False):
            key = (_as_date(row.date), str(row.stock_id))
            record = expected.get(key)
            if record is None:
                fail("unaccounted_row", f"{scenario} {key}")
            forward = getattr(row, fr.FORWARD_RETURN_COL)
            finite = forward is not None and not (
                isinstance(forward, float) and math.isnan(forward)
            )
            if finite and not record.expected_admissible:
                fail("unexpected_finite", f"{scenario} {key}")
            if not finite and record.expected_admissible:
                fail("unexpected_nan", f"{scenario} {key}")
    checks.append(("nan_accounting", True))

    # 6. NaN mask and admissible identity identical under T0/TS.
    mask_t0 = (~panel_t0[fr.FORWARD_RETURN_COL].isna()).to_numpy()
    mask_ts = (~panel_ts[fr.FORWARD_RETURN_COL].isna()).to_numpy()
    if not np.array_equal(mask_t0, mask_ts):
        fail("nan_mask")
    if _finite_pairs(panel_t0) != _finite_pairs(panel_ts):
        fail("admissible_identity")
    checks.append(("nan_mask_and_admissible_identity", True))

    # 7. IC pair identity and metric replay.
    for scenario, panel in ((SCENARIO_T0, panel_t0), (SCENARIO_TS, panel_ts)):
        if _finite_pairs(panel) != expected_pairs:
            fail("pair_identity", scenario)
    if declaration.primary_metric in (
        spec_mod.MetricKey.IC,
        spec_mod.MetricKey.RANK_IC,
    ):
        metric_fn = (
            metrics_mod.information_coefficient
            if declaration.primary_metric is spec_mod.MetricKey.IC
            else metrics_mod.rank_information_coefficient
        )
        for scenario, panel, expected_result in (
            (SCENARIO_T0, panel_t0, results_t0),
            (SCENARIO_TS, panel_ts, results_ts),
        ):
            pairs = _finite_pairs(panel)
            limited = panel.loc[
                [
                    (_as_date(row.date), str(row.stock_id)) in pairs
                    for row in panel.itertuples(index=False)
                ]
            ].reset_index(drop=True)
            full = metric_fn(panel)
            replay = metric_fn(limited)
            if full.to_metric_value(declaration.primary_metric).value != (
                replay.to_metric_value(declaration.primary_metric).value
            ):
                fail("ic_value_replay", scenario)
            if int(full.n_obs) != int(replay.n_obs):
                fail("ic_n_obs_replay", scenario)
            full_t = _finite_or_none(getattr(full, "t_stat", None))
            replay_t = _finite_or_none(getattr(replay, "t_stat", None))
            if full_t != replay_t:
                fail("ic_t_replay", scenario)
            if not _series_equal(full.per_date, replay.per_date):
                fail("ic_per_date_replay", scenario)
            value, t_stat, n_obs = expected_result
            if full.to_metric_value(declaration.primary_metric).value != value:
                fail("ic_engine_value", scenario)
            if full_t != t_stat:
                fail("ic_engine_t", scenario)
            if int(full.n_obs) != int(n_obs):
                fail("ic_engine_n_obs", scenario)
    checks.append(("ic_pairs", True))

    # 8. turnover and costs identical.
    if not np.array_equal(
        portfolio_t0.turnover.to_numpy(dtype="float64"),
        portfolio_ts.turnover.to_numpy(dtype="float64"),
        equal_nan=True,
    ):
        fail("turnover")
    if not np.array_equal(
        portfolio_t0.costs.to_numpy(dtype="float64"),
        portfolio_ts.costs.to_numpy(dtype="float64"),
        equal_nan=True,
    ):
        fail("costs")
    checks.append(("turnover_and_costs", True))
    return tuple(checks)


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _series_equal(left: pd.Series, right: pd.Series) -> bool:
    if list(left.index) != list(right.index):
        return False
    return bool(
        np.array_equal(
            left.to_numpy(dtype="float64"),
            right.to_numpy(dtype="float64"),
            equal_nan=True,
        )
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _evaluate_scenario(
    panel: pd.DataFrame,
    declaration: CampaignEvaluationDeclaration,
) -> tuple[portfolio_mod.PortfolioEvaluation, float | None, float | None, int]:
    portfolio = portfolio_mod.evaluate_portfolio(
        panel,
        declaration.n_groups,
        transaction_cost_bps=declaration.transaction_cost_bps,
        weight_col=None,
    )
    observations = engine_mod._metric_observations(
        panel,
        portfolio,
        None,
        declaration.periods_per_year,
        (declaration.primary_metric,),
    )
    _, metric_value, t_stat = observations[0]
    return portfolio, metric_value.value, t_stat, int(metric_value.n_obs)


# ---------------------------------------------------------------------------
# B, E_S, E_L
# ---------------------------------------------------------------------------


def _mean_ratio(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(math.fsum(values) / len(values))


def _formation_accounting_from_records(
    loaded: _Loaded,
    records: Sequence[AccountingRecord],
    declaration: CampaignEvaluationDeclaration,
) -> tuple[
    float | None,
    int,
    float | None,
    int,
    float | None,
    int,
    tuple[FormationAccounting, ...],
]:
    by_formation: dict[date, list[AccountingRecord]] = {
        formation: [] for formation in loaded.formations
    }
    for record in records:
        if record.scenario != SCENARIO_T0:
            continue
        by_formation.setdefault(record.formation_date, []).append(record)
    b_ratios: list[float] = []
    es_ratios: list[float] = []
    el_ratios: list[float] = []
    items: list[FormationAccounting] = []
    for formation in loaded.formations:
        rows = by_formation.get(formation, [])
        n_universe = len(rows)
        n_affected = sum(1 for row in rows if row.terminal_affected)
        short = [row for row in rows if row.economic_leg == ECONOMIC_SHORT]
        long = [row for row in rows if row.economic_leg == ECONOMIC_LONG]
        affected_short = sum(1 for row in short if row.terminal_affected)
        affected_long = sum(1 for row in long if row.terminal_affected)
        if n_universe > 0:
            b_ratios.append(n_affected / n_universe)
        if short:
            es_ratios.append(affected_short / len(short))
        if long:
            el_ratios.append(affected_long / len(long))
        counts = {
            ADMISSIBLE: 0,
            ADMISSIBLE_TERMINAL: 0,
            ADMISSIBLE_TERMINAL_EMPTY: 0,
            TERMINAL_OBSERVED_PATH_INCOMPLETE: 0,
            OBSERVED_PATH_INCOMPLETE: 0,
            FACTOR_VALUE_MISSING: 0,
        }
        group_stats: dict[int, list[int]] = {}
        for row in rows:
            counts[row.admissibility] = counts.get(row.admissibility, 0) + 1
            if row.group is None:
                continue
            stats = group_stats.setdefault(row.group, [0, 0, 0])
            stats[0] += 1
            if row.expected_admissible:
                stats[1] += 1
            if row.terminal_affected:
                stats[2] += 1
        groups = tuple(
            GroupAccounting(
                group=group,
                n_stocks=stats[0],
                n_returns=stats[1],
                n_affected=stats[2],
            )
            for group, stats in sorted(group_stats.items())
        )
        items.append(
            FormationAccounting(
                formation_date=formation,
                n_universe=n_universe,
                n_affected=n_affected,
                n_short=len(short),
                n_affected_short=affected_short,
                n_long=len(long),
                n_affected_long=affected_long,
                n_admissible=counts[ADMISSIBLE],
                n_admissible_terminal=counts[ADMISSIBLE_TERMINAL],
                n_admissible_terminal_empty=counts[ADMISSIBLE_TERMINAL_EMPTY],
                n_terminal_incomplete=counts[TERMINAL_OBSERVED_PATH_INCOMPLETE],
                n_observed_incomplete=counts[OBSERVED_PATH_INCOMPLETE],
                n_factor_missing=counts[FACTOR_VALUE_MISSING],
                groups=groups,
            )
        )
    return (
        _mean_ratio(b_ratios),
        len(b_ratios),
        _mean_ratio(es_ratios),
        len(es_ratios),
        _mean_ratio(el_ratios),
        len(el_ratios),
        tuple(items),
    )


# ---------------------------------------------------------------------------
# Blank outputs
# ---------------------------------------------------------------------------


def _missing_declaration() -> CampaignEvaluationDeclaration:
    return CampaignEvaluationDeclaration(
        precommitment_ref="MISSING",
        factor_spec_version="MISSING",
        factor_panel_sha256="0" * 64,
        partition_id="MISSING",
        fold_key="MISSING",
        universe="TOP_1000",
        horizon=1,
        n_groups=2,
        winsorization=0.0,
        transaction_cost_bps=0.0,
        periods_per_year=1,
        weighting="EQUAL",
        primary_metric=spec_mod.MetricKey.IC,
        basis="NONE",
        screen_id="MISSING",
        screen_policy_hash="MISSING",
    )


def _blank_diagnostics(
    *,
    reason: str,
    declaration: CampaignEvaluationDeclaration,
    bindings: Bindings,
    fold: partition_mod.Fold | None,
    file_hashes: Mapping[str, str],
    code_hashes: Mapping[str, str],
    doc_hashes: Mapping[str, str],
    factor_spec_version: str | None,
    factor_panel_sha256: str | None,
    sign: int,
) -> DevelopmentDiagnostics:
    d7a = bindings.d7a
    return DevelopmentDiagnostics(
        not_assessed=reason,
        declaration_hash=declaration.declaration_hash,
        factor_spec_version=factor_spec_version,
        factor_panel_sha256=factor_panel_sha256,
        partition_id=declaration.partition_id,
        fold_key=declaration.fold_key,
        fold_role="" if fold is None else fold.role.value,
        fold_start=date.min if fold is None else fold.start.date(),
        fold_end=date.min if fold is None else fold.end.date(),
        universe=declaration.universe,
        horizon=declaration.horizon,
        n_groups=declaration.n_groups,
        winsorization=float(declaration.winsorization),
        transaction_cost_bps=float(declaration.transaction_cost_bps),
        periods_per_year=declaration.periods_per_year,
        primary_metric=declaration.primary_metric.value,
        basis=declaration.basis,
        sign=sign,
        orientation_rule="UNSUPPORTED",
        screen_id=declaration.screen_id,
        screen_policy_hash=declaration.screen_policy_hash,
        d7a_artifact_id=d7a.artifact_id,
        d7a_returns_hash=d7a.returns_hash,
        d7a_file_hashes=tuple(sorted(file_hashes.items())),
        code_sha256=tuple(sorted(code_hashes.items())),
        document_hashes=tuple(sorted(doc_hashes.items())),
        formations=(),
        purged_cells=(),
        formation_accounting=(),
        b=None,
        e_s=None,
        e_l=None,
        n_b_formations=0,
        n_es_formations=0,
        n_el_formations=0,
        value_t0=None,
        value_ts=None,
        t_t0=None,
        t_ts=None,
        n_obs_t0=None,
        n_obs_ts=None,
        economic_t0=None,
        economic_ts=None,
        reconciliation=(),
        accounting_records_hash=content_hash([]),
        accounting_records=(),
    )


def _blank_accounting(
    *,
    reason: str,
    bindings: Bindings,
    file_hashes: Mapping[str, str],
    code_hashes: Mapping[str, str],
    doc_hashes: Mapping[str, str],
    declaration: CampaignEvaluationDeclaration | None,
    fold: partition_mod.Fold | None,
) -> FactorIndependentAccounting:
    return FactorIndependentAccounting(
        not_assessed=reason,
        declaration_hash=(
            None if declaration is None else declaration.declaration_hash
        ),
        factor_spec_version=(
            None if declaration is None else declaration.factor_spec_version
        ),
        partition_id=None if declaration is None else declaration.partition_id,
        fold_key=None if declaration is None else declaration.fold_key,
        fold_role=None if fold is None else fold.role.value,
        fold_start=None if fold is None else fold.start.date(),
        fold_end=None if fold is None else fold.end.date(),
        universe=None if declaration is None else declaration.universe,
        horizon=None if declaration is None else declaration.horizon,
        d7a_artifact_id=bindings.d7a.artifact_id,
        d7a_returns_hash=bindings.d7a.returns_hash,
        d7a_file_hashes=tuple(sorted(file_hashes.items())),
        code_sha256=tuple(sorted(code_hashes.items())),
        document_hashes=tuple(sorted(doc_hashes.items())),
        formations=(),
        purged_cells=(),
        formation_accounting=(),
        affected_reason_counts=(),
        b=None,
        n_b_formations=0,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_development_diagnostics(
    root: Path | str,
    declaration: CampaignEvaluationDeclaration | None,
    factor_spec: FactorSpec,
    factor_panel: pd.DataFrame,
    partition: partition_mod.Partition,
    *,
    bindings: Bindings | None = None,
) -> DevelopmentDiagnostics:
    """Build the T0/TS development diagnostics for one declared fold."""
    bindings = bindings if bindings is not None else DEFAULT_BINDINGS
    if not isinstance(bindings, Bindings):
        raise D7bInputError("bindings must be a Bindings value")
    if not isinstance(factor_spec, FactorSpec):
        raise D7bInputError("factor_spec must be a FactorSpec value")
    sign = int(factor_spec.sign)
    code_hashes = _verify_code(bindings)
    doc_hashes = _verify_documents(bindings)
    resolver = _Resolver(root, bindings)
    file_hashes = _verify_d7a_files(resolver, bindings)

    if declaration is None:
        return _blank_diagnostics(
            reason=NOT_ASSESSED_DECLARATION_MISSING,
            declaration=_missing_declaration(),
            bindings=bindings,
            fold=None,
            file_hashes=file_hashes,
            code_hashes=code_hashes,
            doc_hashes=doc_hashes,
            factor_spec_version=None,
            factor_panel_sha256=None,
            sign=sign,
        )
    if declaration.factor_spec_version != factor_spec.version:
        raise D7bBindingError(
            f"factor_spec_version {declaration.factor_spec_version} != "
            f"FactorSpec.version {factor_spec.version}"
        )
    fold = _resolve_fold(declaration, partition)
    domain = _domain_reason(fold, partition)

    def blank(reason: str) -> DevelopmentDiagnostics:
        return _blank_diagnostics(
            reason=reason,
            declaration=declaration,
            bindings=bindings,
            fold=fold,
            file_hashes=file_hashes,
            code_hashes=code_hashes,
            doc_hashes=doc_hashes,
            factor_spec_version=factor_spec.version,
            factor_panel_sha256=declaration.factor_panel_sha256,
            sign=sign,
        )

    if domain is not None:
        return blank(domain)
    metric_reason = _metric_reason(declaration, sign)
    if metric_reason is not None:
        return blank(metric_reason)

    loaded = _load_d7a(resolver, bindings, declaration, partition, fold)
    panel = _validate_factor_panel(
        factor_panel,
        declaration=declaration,
        formations=loaded.formations,
        universe_by_formation=loaded.universe_by_formation,
    )
    base = _base_panel(
        panel,
        formations=loaded.formations,
        universe_by_formation=loaded.universe_by_formation,
    )
    winsorized = _winsorize(base, declaration.winsorization)
    groups = _frozen_groups(winsorized, declaration.n_groups)
    observed = _observed_panel(loaded.daily)
    observed_returns = _admissible_returns(
        factor_panel=panel,
        observed=observed,
        fold=fold,
        declaration=declaration,
        loaded=loaded,
    )
    r_obs = _terminal_obs_returns(
        factor_panel=panel,
        observed=observed,
        fold=fold,
        declaration=declaration,
        loaded=loaded,
    )
    panel_t0, panel_ts, records = _scenario_panels(
        winsorized=winsorized,
        groups=groups,
        r_obs=r_obs,
        observed_returns=observed_returns,
        declaration=declaration,
        loaded=loaded,
        sign=sign,
    )
    portfolio_t0, value_t0, t_t0, n_obs_t0 = _evaluate_scenario(
        panel_t0, declaration
    )
    portfolio_ts, value_ts, t_ts, n_obs_ts = _evaluate_scenario(
        panel_ts, declaration
    )
    checks = _reconcile(
        panel_t0=panel_t0,
        panel_ts=panel_ts,
        portfolio_t0=portfolio_t0,
        portfolio_ts=portfolio_ts,
        results_t0=(value_t0, t_t0, n_obs_t0),
        results_ts=(value_ts, t_ts, n_obs_ts),
        groups=groups,
        records=records,
        declaration=declaration,
    )

    economic_t0 = value_t0
    economic_ts = value_ts
    if sign == -1:
        economic_t0 = None if value_t0 is None else -value_t0
        economic_ts = None if value_ts is None else -value_ts
        t_t0 = None if t_t0 is None else -t_t0
        t_ts = None if t_ts is None else -t_ts

    (
        b,
        n_b,
        e_s,
        n_es,
        e_l,
        n_el,
        formation_items,
    ) = _formation_accounting_from_records(loaded, records, declaration)

    return DevelopmentDiagnostics(
        not_assessed=None,
        declaration_hash=declaration.declaration_hash,
        factor_spec_version=factor_spec.version,
        factor_panel_sha256=declaration.factor_panel_sha256,
        partition_id=declaration.partition_id,
        fold_key=declaration.fold_key,
        fold_role=fold.role.value,
        fold_start=fold.start.date(),
        fold_end=fold.end.date(),
        universe=declaration.universe,
        horizon=declaration.horizon,
        n_groups=declaration.n_groups,
        winsorization=float(declaration.winsorization),
        transaction_cost_bps=float(declaration.transaction_cost_bps),
        periods_per_year=declaration.periods_per_year,
        primary_metric=declaration.primary_metric.value,
        basis=declaration.basis,
        sign=sign,
        orientation_rule=_orientation_rule(sign, declaration.primary_metric),
        screen_id=declaration.screen_id,
        screen_policy_hash=declaration.screen_policy_hash,
        d7a_artifact_id=bindings.d7a.artifact_id,
        d7a_returns_hash=bindings.d7a.returns_hash,
        d7a_file_hashes=tuple(sorted(file_hashes.items())),
        code_sha256=tuple(sorted(code_hashes.items())),
        document_hashes=tuple(sorted(doc_hashes.items())),
        formations=loaded.formations,
        purged_cells=loaded.purged_cells,
        formation_accounting=formation_items,
        b=b,
        e_s=e_s,
        e_l=e_l,
        n_b_formations=n_b,
        n_es_formations=n_es,
        n_el_formations=n_el,
        value_t0=value_t0,
        value_ts=value_ts,
        t_t0=t_t0,
        t_ts=t_ts,
        n_obs_t0=n_obs_t0,
        n_obs_ts=n_obs_ts,
        economic_t0=economic_t0,
        economic_ts=economic_ts,
        reconciliation=checks,
        accounting_records_hash=content_hash(
            [record.to_dict() for record in records]
        ),
        accounting_records=records,
    )


def compute_factor_independent_accounting(
    root: Path | str,
    declaration: CampaignEvaluationDeclaration | None,
    partition: partition_mod.Partition,
    *,
    bindings: Bindings | None = None,
) -> FactorIndependentAccounting:
    """§5.1–§5.2 plus B and per-reason counts; computes no return value."""
    bindings = bindings if bindings is not None else DEFAULT_BINDINGS
    if not isinstance(bindings, Bindings):
        raise D7bInputError("bindings must be a Bindings value")
    code_hashes = _verify_code(bindings)
    doc_hashes = _verify_documents(bindings)
    resolver = _Resolver(root, bindings)
    file_hashes = _verify_d7a_files(resolver, bindings)

    if declaration is None:
        return _blank_accounting(
            reason=NOT_ASSESSED_DECLARATION_MISSING,
            bindings=bindings,
            file_hashes=file_hashes,
            code_hashes=code_hashes,
            doc_hashes=doc_hashes,
            declaration=None,
            fold=None,
        )
    fold = _resolve_fold(declaration, partition)
    domain = _domain_reason(fold, partition)
    if domain is not None:
        return _blank_accounting(
            reason=domain,
            bindings=bindings,
            file_hashes=file_hashes,
            code_hashes=code_hashes,
            doc_hashes=doc_hashes,
            declaration=declaration,
            fold=fold,
        )
    loaded = _load_d7a(resolver, bindings, declaration, partition, fold)
    b_ratios: list[float] = []
    reason_counts: dict[str, int] = {}
    formation_items: list[FormationAccounting] = []
    for formation in loaded.formations:
        universe = loaded.universe_by_formation.get(formation, ())
        affected = 0
        counts = {
            ADMISSIBLE: 0,
            ADMISSIBLE_TERMINAL: 0,
            ADMISSIBLE_TERMINAL_EMPTY: 0,
            TERMINAL_OBSERVED_PATH_INCOMPLETE: 0,
            OBSERVED_PATH_INCOMPLETE: 0,
        }
        for stock in universe:
            label = loaded.label_by_key.get((formation, stock))
            if label is None:
                raise D7bConsistencyError(
                    f"missing label for ({formation}, {stock})"
                )
            state = _admissibility(label)
            counts[state] += 1
            reason_counts[state] = reason_counts.get(state, 0) + 1
            if _as_bool(label.get("terminal_affected")):
                affected += 1
        if len(universe) > 0:
            b_ratios.append(affected / len(universe))
        formation_items.append(
            FormationAccounting(
                formation_date=formation,
                n_universe=len(universe),
                n_affected=affected,
                n_short=0,
                n_affected_short=0,
                n_long=0,
                n_affected_long=0,
                n_admissible=counts[ADMISSIBLE],
                n_admissible_terminal=counts[ADMISSIBLE_TERMINAL],
                n_admissible_terminal_empty=counts[ADMISSIBLE_TERMINAL_EMPTY],
                n_terminal_incomplete=counts[TERMINAL_OBSERVED_PATH_INCOMPLETE],
                n_observed_incomplete=counts[OBSERVED_PATH_INCOMPLETE],
                n_factor_missing=0,
                groups=(),
            )
        )
    return FactorIndependentAccounting(
        not_assessed=None,
        declaration_hash=declaration.declaration_hash,
        factor_spec_version=declaration.factor_spec_version,
        partition_id=declaration.partition_id,
        fold_key=declaration.fold_key,
        fold_role=fold.role.value,
        fold_start=fold.start.date(),
        fold_end=fold.end.date(),
        universe=declaration.universe,
        horizon=declaration.horizon,
        d7a_artifact_id=bindings.d7a.artifact_id,
        d7a_returns_hash=bindings.d7a.returns_hash,
        d7a_file_hashes=tuple(sorted(file_hashes.items())),
        code_sha256=tuple(sorted(code_hashes.items())),
        document_hashes=tuple(sorted(doc_hashes.items())),
        formations=loaded.formations,
        purged_cells=loaded.purged_cells,
        formation_accounting=tuple(formation_items),
        affected_reason_counts=tuple(sorted(reason_counts.items())),
        b=_mean_ratio(b_ratios),
        n_b_formations=len(b_ratios),
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _sgn(value: float | None) -> int | None:
    if value is None or not math.isfinite(value):
        return None
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _screen_matches(
    outcome: ScreenOutcome,
    diagnostics: DevelopmentDiagnostics,
    scenario: str,
) -> bool:
    return (
        outcome.screen_id == diagnostics.screen_id
        and outcome.screen_policy_hash == diagnostics.screen_policy_hash
        and outcome.declaration_hash == diagnostics.declaration_hash
        and outcome.diagnostics_hash == diagnostics.diagnostics_hash
        and outcome.scenario == scenario
    )


def classify_delisting_sensitivity(
    diagnostics: DevelopmentDiagnostics,
    screen_t0: ScreenOutcome | None,
    screen_ts: ScreenOutcome | None,
) -> D7bDevelopmentSensitivityRecord:
    """Apply the §9.6 clauses and return the development classification."""
    common = dict(
        diagnostics_hash=diagnostics.diagnostics_hash,
        declaration_hash=diagnostics.declaration_hash,
        screen_id=diagnostics.screen_id,
        screen_policy_hash=diagnostics.screen_policy_hash,
        economic_t0=diagnostics.economic_t0,
        economic_ts=diagnostics.economic_ts,
        e_s=diagnostics.e_s,
        b=diagnostics.b,
    )

    def finish(
        status: str,
        reason: str | None,
        *,
        clause_a: bool = False,
        clause_b: bool = False,
        clause_c: bool = False,
    ) -> D7bDevelopmentSensitivityRecord:
        return D7bDevelopmentSensitivityRecord(
            clause_a=clause_a,
            clause_b=clause_b,
            clause_c=clause_c,
            status=status,
            not_assessed_reason=reason,
            advancement_blocked=status != STATUS_NOT_SENSITIVE,
            holdout_authorized=False,
            final_holdout_consumed=False,
            confirmation_evidence=False,
            scope=SCOPE,
            **common,
        )

    if diagnostics.not_assessed is not None:
        return finish(STATUS_NOT_ASSESSED, diagnostics.not_assessed)
    if screen_t0 is None or screen_ts is None:
        return finish(STATUS_NOT_ASSESSED, NOT_ASSESSED_SCREEN_MISSING)
    if not _screen_matches(
        screen_t0, diagnostics, SCENARIO_T0
    ) or not _screen_matches(screen_ts, diagnostics, SCENARIO_TS):
        return finish(STATUS_NOT_ASSESSED, NOT_ASSESSED_SCREEN_MISMATCH)
    if diagnostics.economic_t0 is None or diagnostics.economic_ts is None:
        return finish(STATUS_NOT_ASSESSED, STATUS_NOT_ASSESSED)
    sgn_t0 = _sgn(diagnostics.economic_t0)
    sgn_ts = _sgn(diagnostics.economic_ts)
    if sgn_t0 is None or sgn_ts is None:
        return finish(STATUS_NOT_ASSESSED, STATUS_NOT_ASSESSED)
    clause_a = sgn_t0 != sgn_ts
    clause_b = bool(screen_t0.passed) and not bool(screen_ts.passed)
    if diagnostics.e_s is None:
        return finish(STATUS_NOT_ASSESSED, STATUS_NOT_ASSESSED)
    b = diagnostics.b
    if b is not None and diagnostics.e_s > 2.0 * b:
        clause_c = True
    elif b == 0.0 and diagnostics.e_s > 0.0:
        clause_c = True
    else:
        clause_c = False
    if clause_a or clause_b or clause_c:
        return finish(
            STATUS_DELISTING_SENSITIVE,
            None,
            clause_a=clause_a,
            clause_b=clause_b,
            clause_c=clause_c,
        )
    return finish(
        STATUS_NOT_SENSITIVE,
        None,
        clause_a=clause_a,
        clause_b=clause_b,
        clause_c=clause_c,
    )
