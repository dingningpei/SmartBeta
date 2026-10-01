"""PQA — Dataset-v1.2 P-Q quarantine overlay, state calculus and R3 identity.

This module owns **only** the Dataset-v1.2 P-Q quarantine layer frozen by
``worker_tasks/dataset_v1/task-pqa-quarantine-overlay.md`` (authority: the
normative rules ``docs/discovery_dataset_v1_2_pq_quarantine_rules.md``
§§Q1–Q4, Q12, Q13 and the amendment §§1, 2, 4, 6–8):

1. the static quarantine overlay (suspect source sets, primitive states,
   lagged input-state panels, label states and report-only liquidity
   exposure);
2. the pure factor-input state calculus
   :func:`factor_input_states` with an independent AST oracle;
3. the Dataset-v1.2 identity record and read-only verifier (R3).

It computes **no** return statistic, factor value summary, group assignment,
metric, screen or estimand, and defines no Campaign declaration.  Every test
and every build here uses synthetic fixtures only: no provider/network call,
no reserve read, and no Knowledge-PIT append.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa

from smart_beta.dataset_v1.reserve_firewall import ReserveFirewallError
from smart_beta.science import knowledge as _knowledge
from smart_beta.spec.engine import TrustedInput, evaluate_factor
from smart_beta.spec.expression import (
    Add,
    CrossSectional,
    Div,
    Expr,
    Field,
    Lag,
    Literal,
    Mul,
    Rolling,
    Sub,
)
from smart_beta.spec.factor_spec import FactorSpec

__all__ = [
    # constants
    "SCHEMA",
    "RESERVE_START",
    "DEVELOPMENT_END",
    "LAST_SOURCE_DATE",
    "STATE_CLEAN",
    "STATE_QUARANTINED",
    "STATE_PROPAGATED",
    "STATE_NOT_APPLICABLE",
    "INPUT_NAMES",
    "DATASET_ID",
    "PARENT_DATASET_ID",
    "QUARANTINE_RULE_HASH",
    "D8_GENESIS_RECORD_HASH",
    "D8_SOURCE_LABEL",
    "INHERITED_ARTIFACTS",
    # errors
    "QuarantineError",
    "QuarantineBindingError",
    "QuarantineInputError",
    "QuarantineConsistencyError",
    "UnsupportedQuarantineNode",
    "DatasetIdentityError",
    "NondeterministicOverlayError",
    "ReserveFirewallError",
    # binding / resolver
    "FileBinding",
    "StageBinding",
    "HashBinding",
    "Bindings",
    "default_bindings",
    "QuarantineResolver",
    "canonical_json",
    # overlay
    "Overlay",
    "compute_flag_panel",
    "compute_primitive_states",
    "compute_input_states",
    "compute_label_states",
    "compute_liquidity_exposure",
    "build_overlay",
    "verify_overlay",
    # calculus
    "factor_input_states",
    "oracle_factor_input_states",
    # identity
    "build_identity_payload",
    "compute_identity_hash",
    "verify_dataset_v1_2_identity",
    # cli
    "main",
]


# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

SCHEMA = "discovery-dataset-v1-2-quarantine-overlay-v1"

RESERVE_START = date(2023, 7, 3)
DEVELOPMENT_END = date(2023, 6, 30)
LAST_SOURCE_DATE = date(2023, 6, 29)

STATE_CLEAN = 0
STATE_QUARANTINED = 1
STATE_PROPAGATED = 2
STATE_NOT_APPLICABLE = 3

INPUT_DAILY_TOTAL_RETURN = "daily_total_return"
INPUT_TOTAL_RETURN_INDEX = "total_return_index"
INPUT_DOLLAR_VOLUME = "dollar_volume"
INPUT_NAMES = (
    INPUT_DAILY_TOTAL_RETURN,
    INPUT_TOTAL_RETURN_INDEX,
    INPUT_DOLLAR_VOLUME,
)

DATASET_ID = "discovery_dataset_v1_2"
PARENT_DATASET_ID = "discovery_dataset_v1_1"
QUARANTINE_RULE_HASH = (
    "df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886"
)
AMENDMENT_PATH = "docs/discovery_dataset_v1_2_pq_amendment.md"
AMENDMENT_SHA256 = (
    "9261a55b9b82a5708dded65bfd3083d5a960cfa4375681b1b23390da62fb3ed0"
)
RULES_PATH = "docs/discovery_dataset_v1_2_pq_quarantine_rules.md"
D8_GENESIS_SEQ = 0
D8_GENESIS_RECORD_HASH = (
    "984d7bec4fbb159b3eba3385b2a0af24a54c2c707478af91b5ba461684da4eea"
)
D8_SOURCE_LABEL = "discovery_dataset_v1:reserve:n-f874d3ecb61374d4"
KNOWLEDGE_LOG_RELATIVE = "knowledge_pit/knowledge.jsonl"


def _default_knowledge_log(root: Path) -> Path:
    """The D8 K log lives at ``<root>/../knowledge_pit/knowledge.jsonl``."""
    return Path(root).resolve().parent / "knowledge_pit" / "knowledge.jsonl"

#: Every parent authority file of amendment §1 (path -> SHA-256).
PARENT_AUTHORITY: tuple[tuple[str, str], ...] = (
    ("docs/discovery_dataset_v1_contract.md",
     "d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3"),
    ("docs/discovery_dataset_v1_1_classification_amendment.md",
     "f89a7128bb8113c6978fc6bca8bcaf6048cd0c7db1fe5ac2aaffa1c7d519c623"),
    ("docs/discovery_dataset_v1_1_identity_binding_clarification.md",
     "57da1603ff0c77f19460293802d34f2b2383800e0528746dcb8fbb6b9ceedfa9"),
    ("docs/discovery_dataset_v1_1_d6_clarification.md",
     "e3cb52a892f533c41c5c0d1d927cd54d7e200ef2a28e56e76efdf01227aa17ea"),
    ("docs/discovery_dataset_v1_1_d6_duplicate_key_clarification.md",
     "7d34c6037040f8998966221fd1a68db1e9f2f9e74004d746081464035ebb22a0"),
    ("docs/discovery_dataset_v1_1_d7_clarification.md",
     "bc8e6920d31dd429beb62030123a721a7293558ba57fe8e3d4d6cd6368a96946"),
    ("docs/discovery_dataset_v1_1_d7b_clarification.md",
     "7cf2f702982bf24179010b6adcdc37f867d0c70803afe4fc7809898f22324bad"),
    ("docs/discovery_dataset_v1_1_d7c_clarification.md",
     "508d5f1ac3da3c68233c72b4b31ea75d0ec1d88be9241e6dd018cd54467b4a89"),
    ("docs/discovery_dataset_v1_1_d7c_clarification_addendum.md",
     "75806604cfb10bc9790126c5e833255497c5883c4370490ca593039595aaf05d"),
    ("docs/discovery_dataset_v1_1_d8_clarification.md",
     "9a3f218d67c7e7456b2a5c9439a2d1fffe202e5beca3299afb31439b919b24c0"),
    ("docs/discovery_dataset_v1_1_d8_clarification_addendum.md",
     "d46b9ef6a59a059dc17f2134835da884e8753cc7f7089c434e7ec725fcfddbcf"),
    ("docs/discovery_dataset_v1_1_gate6_audit_clarification.md",
     "0b529dca209b4fa9f54f9662bb3d9205f07b0056232532b2a529a3a513f43789"),
    ("docs/discovery_dataset_v1_1_gate6_audit_clarification_addendum.md",
     "9960d3e19f2966526c82767cfb15fd7e4a50383b2c42f83dcc9de41b130dcfa6"),
    ("docs/discovery_dataset_v1_1_gate6_audit_result.md",
     "8d91f58ebac8cf0483fa9bee473f032541c631d51f16946c0aef09fb8d5c2995"),
    ("docs/discovery_dataset_v1_1_gate6_v2b_evidence_protocol.md",
     "f04d2253747e2b9fdebad3681ac93832b3d452d5463c009d17c5a16607b36c27"),
    ("docs/discovery_dataset_v1_1_gate6_v2b_protocol_addendum.md",
     "f88404d9a2553f37d5bc348762303ed9e78a369801facc0351f8948a0172b54f"),
    ("docs/discovery_dataset_v1_1_gate6_path_comparison.md",
     "9006f0a74fc8223ca03f1ce13440fce4848347e7f860a5f308d3363e84568c84"),
    ("docs/discovery_dataset_v1_1_pq_contract_amendment_draft.md",
     "786c9f142f41fb2221e52aeab24fc3d5b0971e5a1888f3a50f8d8cbee5e1e543"),
    ("docs/discovery_dataset_v1_1_pq_open_question_resolution.md",
     "a6abdc054eafcd8d81a1c32b0ef31d08ea6b698c6a0986d680f2ab4bc7321667"),
)

#: The nine inherited artifacts of amendment §2 / task spec §8.
INHERITED_ARTIFACTS: dict[str, str | None] = {
    "b-c21b48190918f21f": None,
    "n-f874d3ecb61374d4": (
        "f93093a8b8fa07521191d687560c2c26ab16630ebeed35d68aa12867def47fb3"
    ),
    "i-a21f1e46621dee94": None,
    "q-5d1028655f8c82e1": (
        "3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3"
    ),
    "c-27b6dabd0db0cb15": (
        "2ee4940c14a85048fddd9e3e675c3e9a6f40a11c8887192a5e61939adfc62b60"
    ),
    "u-6150bc6ac2866b7f": (
        "6e8539b7acb4ed4559846234b0b26e6f1d4e6d34f7e0a5d1e2290134d563ed87"
    ),
    "r-34565f074d64ccda": (
        "4c487e47fd613f3f272109d14b43a27e061f9cc31e5b6d6bcefd4abc97994861"
    ),
    "ti-aa23fabd3db4e595": (
        "30ee0c51602cf792e8cb7654c2501f52c7d664675dbf78845c903df6683c2ae6"
    ),
    "g6-2264dbcac2a09a3d": None,
}

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class QuarantineError(Exception):
    """Base class for every PQA fail-closed error."""


class QuarantineBindingError(QuarantineError, ValueError):
    """A frozen document, code or artifact hash does not match its binding."""


class QuarantineInputError(QuarantineError, ValueError):
    """A required input is missing, unreadable or malformed."""


class QuarantineConsistencyError(QuarantineError):
    """A frozen quarantine invariant is violated."""


class UnsupportedQuarantineNode(QuarantineError):
    """An expression node outside the Q3.3 closed set was encountered."""


class DatasetIdentityError(QuarantineError):
    """The Dataset-v1.2 identity record failed verification."""


class NondeterministicOverlayError(QuarantineError):
    """An existing overlay directory holds different bytes for the same pq_id."""


# ---------------------------------------------------------------------------
# Canonical JSON and hashing
# ---------------------------------------------------------------------------


def canonical_json(obj: object) -> str:
    """The frozen canonical JSON spelling: sorted keys, tight separators."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _canonical_bytes(obj: object) -> bytes:
    return (canonical_json(obj) + "\n").encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_hex(Path(path).read_bytes())


def _sha256_bytes(data: bytes) -> str:
    return _sha256_hex(data)


# ---------------------------------------------------------------------------
# Bindings and guarded resolver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileBinding:
    """One allowlisted, hash-pinned data file relative to ``<root>``."""

    relative_path: str
    sha256: str


@dataclass(frozen=True)
class StageBinding:
    """One upstream stage's manifest binding and its allowlisted data files."""

    stage: str
    artifact_id: str
    manifest_relative_path: str
    manifest_hash_field: str
    manifest_hash: str
    manifest_sha256: str
    files: tuple[FileBinding, ...]


@dataclass(frozen=True)
class HashBinding:
    """One normative document or pinned source file bound by SHA-256."""

    name: str
    path: str
    sha256: str


@dataclass(frozen=True)
class Bindings:
    """The frozen PQA bindings; production defaults, tests inject."""

    d4: StageBinding
    d5: StageBinding
    d6: StageBinding
    d7a: StageBinding
    d7c: StageBinding
    documents: tuple[HashBinding, ...]
    pinned_code: tuple[HashBinding, ...]
    artifact_manifests: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    expected_flag_count: int | None = 17469

    def stages(self) -> tuple[StageBinding, ...]:
        return (self.d4, self.d5, self.d6, self.d7a, self.d7c)

    def stage(self, name: str) -> StageBinding:
        for candidate in self.stages():
            if candidate.stage == name:
                return candidate
        raise QuarantineInputError(f"no bound stage {name!r}")

    def allowlist(self) -> frozenset[str]:
        allowed: set[str] = set()
        for stage in self.stages():
            allowed.add(stage.manifest_relative_path)
            for binding in stage.files:
                allowed.add(binding.relative_path)
        return frozenset(allowed)


# -- pinned read-only code (task spec §2) -----------------------------------

_PINNED_CODE: tuple[tuple[str, str], ...] = (
    ("docs/discovery_dataset_v1_2_pq_amendment.md",
     "9261a55b9b82a5708dded65bfd3083d5a960cfa4375681b1b23390da62fb3ed0"),
    ("docs/discovery_dataset_v1_2_pq_quarantine_rules.md", QUARANTINE_RULE_HASH),
    ("smart_beta/dataset_v1/trusted_inputs.py",
     "c5e30824f8323e0d9a9622f41f7f53861cda02f515a04d562090fd8306b30b74"),
    ("smart_beta/dataset_v1/reserve_firewall.py",
     "4b8a74b5f950bd3bdf58155bbfae32401753f2bed45d5031860d6edb1d493e15"),
    ("smart_beta/spec/engine.py",
     "eea3bc30dd50121865009d0bab4999dc76068c1e2fbf9f7c965413c2a443b46a"),
    ("smart_beta/spec/evaluator.py",
     "aecdeba642ca23f3285b8f55565afb6edaf255322783345e22a50c20c405bcce"),
    ("smart_beta/spec/expression.py",
     "9b027c5da83f0778d72c236327c09967172a5314d60c47bd54ea6337fe46d6f4"),
    ("smart_beta/spec/transforms.py",
     "15708149406017a6c970d42aebef7b0e2ea9ed6b0be48182f7769006a9ff5a66"),
    ("smart_beta/spec/factor_spec.py",
     "ab6c65d312d6fe1e421c489e6ad8045a50e265c8f73dbe6a07738e1bf5458c56"),
    ("smart_beta/science/knowledge.py",
     "7a4ab759ab20d8acf945a1de3246653a572069696a2f2bf791d155c6b6932fec"),
    ("smart_beta/vendors/tiingo/calendar_source.py",
     "79bca35ddd46f87102e90f6ce0c2d51a8b0d16f1fe9caf56b7674b43fce8e23f"),
)


def _documents() -> tuple[HashBinding, ...]:
    return tuple(HashBinding(name, path, sha) for name, path, sha in ())


def _pinned_code() -> tuple[HashBinding, ...]:
    return tuple(
        HashBinding(Path(path).name, path, sha) for path, sha in _PINNED_CODE
    )


# -- default production bindings --------------------------------------------

_D5_FLAGS = "derived/qc/q-5d1028655f8c82e1/day_flags.parquet"
_D4_IDENT = "derived/identity/i-a21f1e46621dee94/identity_assignment.parquet"
_D7C_BASE = "derived/trusted_inputs/ti-aa23fabd3db4e595"
_D7A_BASE = "derived/returns/r-34565f074d64ccda"
_D6_BASE = "derived/universe/u-6150bc6ac2866b7f"


def default_bindings() -> Bindings:
    """The frozen production bindings (tests inject synthetic ones)."""
    d5 = StageBinding(
        stage="D5",
        artifact_id="q-5d1028655f8c82e1",
        manifest_relative_path="derived/qc/q-5d1028655f8c82e1/qc_report.json",
        manifest_hash_field="qc_hash",
        manifest_hash=(
            "3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3"
        ),
        manifest_sha256="",
        files=(FileBinding(_D5_FLAGS, ""),),
    )
    d4 = StageBinding(
        stage="D4",
        artifact_id="i-a21f1e46621dee94",
        manifest_relative_path=(
            "derived/identity/i-a21f1e46621dee94/identity.json"
        ),
        manifest_hash_field="identity_hash",
        manifest_hash=(
            "b8ed3d23979f8cbb9f320fae96a8cea968b16332fd1000fa1cdedca15df43aa4"
        ),
        manifest_sha256="",
        files=(FileBinding(_D4_IDENT, ""),),
    )
    d7c = StageBinding(
        stage="D7C",
        artifact_id="ti-aa23fabd3db4e595",
        manifest_relative_path=f"{_D7C_BASE}/trusted_inputs.json",
        manifest_hash_field="ti_hash",
        manifest_hash=(
            "30ee0c51602cf792e8cb7654c2501f52c7d664675dbf78845c903df6683c2ae6"
        ),
        manifest_sha256="",
        files=(
            FileBinding(f"{_D7C_BASE}/daily_total_return.parquet",
                        "511bcf4ac4ea2bd02158438732e0fdf19c04d783b88ca7a01ff5a364ec8e688c"),
            FileBinding(f"{_D7C_BASE}/dollar_volume.parquet",
                        "c8a632ef455ecec5f3caa95816ec0bb71789e3ed51b9447765021c697fa27780"),
            FileBinding(f"{_D7C_BASE}/total_return_index.parquet",
                        "53a48a65a70d16b80374b926b56c50922ebfc521d3f8c1677380343a8ccd5e8e"),
            FileBinding(f"{_D7C_BASE}/segments.parquet",
                        "4ea4df04c3e581f1750229c255ed1f5c5ec5055ad6fe954483cc7ae5b76cc384"),
        ),
    )
    d7a = StageBinding(
        stage="D7A",
        artifact_id="r-34565f074d64ccda",
        manifest_relative_path=f"{_D7A_BASE}/returns.json",
        manifest_hash_field="returns_hash",
        manifest_hash=(
            "4c487e47fd613f3f272109d14b43a27e061f9cc31e5b6d6bcefd4abc97994861"
        ),
        manifest_sha256="",
        files=(
            FileBinding(f"{_D7A_BASE}/labels.parquet",
                        "262b8b1d445dfb329d33359966a9deba67bd5999ccdc01dd8183eda6f4f2645e"),
            FileBinding(f"{_D7A_BASE}/members.parquet",
                        "fcb2c9df2918a7bd43399b220f9595fc94d8a5dc49542219698ecafb7c6b4488"),
            FileBinding(f"{_D7A_BASE}/horizons.parquet",
                        "61a2ac310021f153676445de7f0122ef74f67910e6506a6e5ef1e286f3d1ccbb"),
        ),
    )
    d6 = StageBinding(
        stage="D6",
        artifact_id="u-6150bc6ac2866b7f",
        manifest_relative_path=f"{_D6_BASE}/universe.json",
        manifest_hash_field="universe_hash",
        manifest_hash=(
            "6e8539b7acb4ed4559846234b0b26e6f1d4e6d34f7e0a5d1e2290134d563ed87"
        ),
        manifest_sha256="",
        files=(
            FileBinding(f"{_D6_BASE}/universe_development.parquet",
                        "f933a67ff8c83ad9f6c7937a76136d93419cedb0ec79cec617df8205b3d38d8b"),
        ),
    )
    artifacts = {
        "n-f874d3ecb61374d4": (
            "normalized/n-f874d3ecb61374d4/build.json", "normalized_hash"
        ),
        "q-5d1028655f8c82e1": (
            "derived/qc/q-5d1028655f8c82e1/qc_report.json", "qc_hash"
        ),
        "c-27b6dabd0db0cb15": (
            "derived/classification/c-27b6dabd0db0cb15/dsc.json", "dsc_hash"
        ),
        "u-6150bc6ac2866b7f": (
            "derived/universe/u-6150bc6ac2866b7f/universe.json", "universe_hash"
        ),
        "r-34565f074d64ccda": (
            "derived/returns/r-34565f074d64ccda/returns.json", "returns_hash"
        ),
        "ti-aa23fabd3db4e595": (
            "derived/trusted_inputs/ti-aa23fabd3db4e595/trusted_inputs.json",
            "ti_hash",
        ),
    }
    return Bindings(
        d4=d4,
        d5=d5,
        d6=d6,
        d7a=d7a,
        d7c=d7c,
        documents=_documents(),
        pinned_code=_pinned_code(),
        artifact_manifests=artifacts,
    )


DEFAULT_BINDINGS = default_bindings()


class QuarantineResolver:
    """The single guarded gateway for every PQA data-file open."""

    def __init__(self, root: Path | str, bindings: Bindings):
        self._root = Path(root)
        self._bindings = bindings
        self._allow = bindings.allowlist()
        self._real_root = Path(os.path.realpath(self._root))

    @property
    def allowlist(self) -> frozenset[str]:
        return self._allow

    def resolve(self, relative_path: str) -> Path:
        raw = str(relative_path)
        pure = PurePosixPath(raw)
        if pure.is_absolute() or ".." in pure.parts:
            raise ReserveFirewallError(f"path {raw!r} escapes <root>")
        if any(
            part in {"reserve_sealed", "raw", "dsc_evidence"} for part in pure.parts
        ):
            raise ReserveFirewallError(
                f"refusing reserve path component in {raw!r}"
            )
        if raw not in self._allow:
            raise ReserveFirewallError(f"path {raw!r} is not allowlisted")
        candidate = self._root / pure
        real = Path(os.path.realpath(candidate))
        if not _is_relative_to(real, self._real_root):
            raise ReserveFirewallError(f"path {raw!r} escapes <root> via symlink")
        return candidate

    def read_bytes(self, relative_path: str) -> bytes:
        path = self.resolve(relative_path)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise QuarantineInputError(f"cannot read {path}: {exc}") from exc

    def read_parquet(
        self, relative_path: str, *, columns: Sequence[str]
    ) -> pd.DataFrame:
        path = self.resolve(relative_path)
        try:
            frame = pd.read_parquet(path, columns=list(columns))
        except OSError as exc:
            raise QuarantineInputError(f"cannot read {path}: {exc}") from exc
        return frame


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Date and frame helpers
# ---------------------------------------------------------------------------


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.Timestamp(value)
    if pd.isna(parsed):
        return None
    return parsed.date()


def _dates(series: pd.Series) -> list[date | None]:
    return [_to_date(value) for value in series.tolist()]


def _assert_dates_development(frame: pd.DataFrame, columns: Sequence[str]) -> None:
    for column in columns:
        if column not in frame:
            continue
        for value in _dates(frame[column]):
            if value is not None and value >= RESERVE_START:
                raise ReserveFirewallError(
                    f"{column} holds a reserve date {value}"
                )


# ---------------------------------------------------------------------------
# Verification of bindings
# ---------------------------------------------------------------------------


def _verify_documents(bindings: Bindings, repo_root: Path) -> None:
    for doc in bindings.documents:
        path = repo_root / doc.path
        try:
            actual = _sha256_file(path)
        except OSError as exc:
            raise QuarantineInputError(
                f"cannot read authority document {doc.path}: {exc}"
            ) from exc
        if actual != doc.sha256:
            raise QuarantineBindingError(
                f"document {doc.path} hash {actual} != bound {doc.sha256}"
            )


def _verify_parent_authority(repo_root: Path) -> None:
    for path, expected in PARENT_AUTHORITY:
        try:
            actual = _sha256_file(repo_root / path)
        except OSError as exc:
            raise QuarantineBindingError(
                f"cannot read parent authority {path}: {exc}"
            ) from exc
        if actual != expected:
            raise QuarantineBindingError(
                f"parent authority {path} hash {actual} != bound {expected}"
            )


def _verify_pinned_code(bindings: Bindings, repo_root: Path) -> None:
    for code in bindings.pinned_code:
        path = repo_root / code.path
        try:
            actual = _sha256_file(path)
        except OSError as exc:
            raise QuarantineInputError(
                f"cannot read pinned code {code.path}: {exc}"
            ) from exc
        if actual != code.sha256:
            raise QuarantineBindingError(
                f"pinned code {code.path} hash {actual} != bound {code.sha256}"
            )


def _verify_stage_manifest(
    root: Path, resolver: QuarantineResolver, stage: StageBinding
) -> dict[str, Any]:
    blob = resolver.read_bytes(stage.manifest_relative_path)
    if stage.manifest_sha256:
        actual = _sha256_hex(blob)
        if actual != stage.manifest_sha256:
            raise QuarantineBindingError(
                f"{stage.stage} manifest hash {actual} != "
                f"bound {stage.manifest_sha256}"
            )
    try:
        manifest = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuarantineInputError(
            f"{stage.stage} manifest is not valid JSON: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise QuarantineInputError(f"{stage.stage} manifest is not an object")
    if manifest.get(stage.manifest_hash_field) != stage.manifest_hash:
        raise QuarantineBindingError(
            f"{stage.stage} manifest {stage.manifest_hash_field} does not match "
            "its binding"
        )
    for binding in stage.files:
        path = root / binding.relative_path
        try:
            actual = _sha256_file(path)
        except OSError as exc:
            raise QuarantineInputError(
                f"cannot read {binding.relative_path}: {exc}"
            ) from exc
        if binding.sha256 and actual != binding.sha256:
            raise QuarantineBindingError(
                f"{binding.relative_path} hash {actual} != bound {binding.sha256}"
            )
    return manifest


# ---------------------------------------------------------------------------
# Overlay dataclass
# ---------------------------------------------------------------------------

_PRIMITIVE_DTYPES = {
    "primitive": "string",
    "security_key": "string",
    "session": "object",
    "state": "int8",
    "reason": "string",
}
_INPUT_STATE_COLUMNS = (
    "input",
    "row_date",
    "security_key",
    "source_session",
    "state",
)
_LABEL_STATE_COLUMNS = (
    "formation_date",
    "security_key",
    "h",
    "state",
    "reason",
)
_LIQUIDITY_COLUMNS = (
    "formation_date",
    "security_key",
    "liquidity_window_exposed",
)


@dataclass(frozen=True)
class Overlay:
    """The static P-Q quarantine overlay for one Dataset-v1.2 build."""

    flags: pd.DataFrame
    primitive_states: pd.DataFrame
    input_states: pd.DataFrame
    label_states: pd.DataFrame
    liquidity_exposure: pd.DataFrame
    identity: Mapping[str, Any]
    pq_id: str = ""
    pq_hash: str = ""
    code_sha256: str = ""

    def _lookup(self, input_name: str) -> dict[tuple[date, str], int]:
        frame = self.input_states
        if len(frame) == 0:
            return {}
        subset = frame[frame["input"] == input_name]
        return {
            (_to_date(row.row_date), str(row.security_key)): int(row.state)
            for row in subset.itertuples(index=False)
        }

    def state_matrix(
        self, input_name: str, index: pd.Index, columns: pd.Index
    ) -> pd.DataFrame:
        lookup = self._lookup(input_name)
        arr = np.zeros((len(index), len(columns)), dtype=np.int64)
        for i, row_date in enumerate(index):
            key_date = _to_date(row_date)
            for j, security_key in enumerate(columns):
                arr[i, j] = lookup.get((key_date, str(security_key)), 0)
        return pd.DataFrame(arr, index=index, columns=columns)

    def mask(self, input_name: str, frame: pd.DataFrame) -> pd.DataFrame:
        states = self.state_matrix(input_name, frame.index, frame.columns)
        return states.isin([STATE_QUARANTINED, STATE_PROPAGATED])


# ---------------------------------------------------------------------------
# Suspect flags and primitive states
# ---------------------------------------------------------------------------


def compute_flag_panel(
    day_flags: pd.DataFrame,
    identity_assignment: pd.DataFrame,
    sessions: Sequence[date],
) -> pd.DataFrame:
    """Build the suspect-flag panel with trusted prev/next sessions."""
    sessions = list(sessions)
    pos = {s: i for i, s in enumerate(sessions)}

    intervals: list[dict[str, Any]] = []
    for row in identity_assignment.itertuples(index=False):
        intervals.append(
            {
                "security_key": str(row.security_key),
                "ticker": str(row.ticker),
                "listed_start": _to_date(row.listed_start),
                "listed_end": _to_date(row.listed_end),
            }
        )
    intervals.sort(key=lambda item: (item["security_key"], item["listed_start"] or date.min))

    def _key_for(ticker: str, session: date) -> str | None:
        for item in intervals:
            if item["ticker"] != ticker:
                continue
            start = item["listed_start"]
            end = item["listed_end"]
            if start is not None and session < start:
                continue
            if end is not None and session > end:
                continue
            return item["security_key"]
        return None

    flags: list[tuple] = []
    suspect = day_flags[
        day_flags["class_or_flag"] == "CORP_ACTION_SUSPECT"
    ]
    for row in suspect.itertuples(index=False):
        session = _to_date(row.date)
        ticker = str(row.ticker)
        if session is None:
            continue
        security_key = _key_for(ticker, session)
        if security_key is None:
            continue
        start = None
        end = None
        for item in intervals:
            if item["security_key"] == security_key:
                start = item["listed_start"]
                end = item["listed_end"]
                break
        i = pos.get(session)
        prev_session = None
        next_session = None
        if i is not None and i > 0:
            candidate = sessions[i - 1]
            if (start is None or candidate >= start) and (
                end is None or candidate <= end
            ):
                prev_session = candidate
        if i is not None and i + 1 < len(sessions):
            candidate = sessions[i + 1]
            if (
                candidate <= DEVELOPMENT_END
                and (start is None or candidate >= start)
                and (end is None or candidate <= end)
            ):
                next_session = candidate
        beyond = bool(
            i is not None
            and i + 1 < len(sessions)
            and sessions[i + 1] >= RESERVE_START
        )
        flags.append(
            (security_key, session, prev_session, next_session, beyond)
        )
    frame = pd.DataFrame(
        flags,
        columns=[
            "security_key",
            "flag_session",
            "prev_session",
            "next_session",
            "next_beyond_development",
        ],
    )
    if len(frame):
        frame = frame.sort_values(
            ["security_key", "flag_session"], kind="mergesort"
        ).reset_index(drop=True)
    return frame


def compute_primitive_states(
    flags: pd.DataFrame,
    *,
    present: Mapping[str, set[tuple[str, date]]],
    tri_segments: Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    """Compute sparse primitive states in {QUARANTINED, PROPAGATED}."""
    records: dict[tuple[str, str, date], tuple[int, str]] = {}

    def _mark(primitive: str, key: str, session: date, state: int, reason: str) -> None:
        if (key, session) not in present.get(primitive, set()):
            return
        current = records.get((primitive, key, session))
        if current is None or current[0] < state:
            records[(primitive, key, session)] = (state, reason)

    for row in flags.itertuples(index=False):
        key = str(row.security_key)
        session = _to_date(row.flag_session)
        prev_session = _to_date(row.prev_session)
        next_session = _to_date(row.next_session)
        for target in (prev_session, session, next_session):
            if target is not None:
                _mark(
                    INPUT_DAILY_TOTAL_RETURN, key, target,
                    STATE_QUARANTINED, "FLAG_ENDPOINT",
                )
        for target in (prev_session, session):
            if target is not None:
                _mark(
                    INPUT_DOLLAR_VOLUME, key, target,
                    STATE_QUARANTINED, "FLAG_ENDPOINT",
                )

    # TRI: structural propagation from an admitted segment anchor onward.
    quarantined_daily: dict[str, set[date]] = {}
    for (primitive, key, session), (state, _reason) in records.items():
        if primitive == INPUT_DAILY_TOTAL_RETURN:
            quarantined_daily.setdefault(key, set()).add(session)
    for segment in tri_segments:
        if not bool(segment.get("admitted", False)):
            continue
        key = str(segment["security_key"])
        anchor = _to_date(segment["anchor_session"])
        last = _to_date(segment["last_session"])
        if key not in quarantined_daily or anchor is None or last is None:
            continue
        hits = sorted(
            session
            for session in quarantined_daily[key]
            if anchor < session <= last
        )
        if not hits:
            continue
        for session in present.get(INPUT_TOTAL_RETURN_INDEX, set()):
            if session[0] != key:
                continue
            if anchor < session[1] <= last and session[1] >= hits[0]:
                records[(INPUT_TOTAL_RETURN_INDEX, key, session[1])] = (
                    STATE_PROPAGATED,
                    "TRI_SEGMENT_PROPAGATION",
                )

    rows = [
        (primitive, key, session, state, reason)
        for (primitive, key, session), (state, reason) in records.items()
    ]
    frame = pd.DataFrame(
        rows,
        columns=["primitive", "security_key", "session", "state", "reason"],
    )
    if len(frame):
        frame = frame.sort_values(
            ["primitive", "security_key", "session"], kind="mergesort"
        ).reset_index(drop=True)
    return frame


def compute_input_states(
    flags: pd.DataFrame,
    primitive_states: pd.DataFrame,
    *,
    long_rows: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Build the sparse lagged input-state panels (state at ``source_session``)."""
    lookup = {
        (str(row.primitive), str(row.security_key), _to_date(row.session)): int(
            row.state
        )
        for row in primitive_states.itertuples(index=False)
    }
    records: list[tuple] = []
    for input_name, long in long_rows.items():
        if len(long) == 0:
            continue
        for row in long.itertuples(index=False):
            value = row.value
            if value is None or (isinstance(value, float) and np.isnan(value)):
                continue
            source = _to_date(row.source_session)
            key = str(row.security_key)
            state = lookup.get((input_name, key, source))
            if state in (STATE_QUARANTINED, STATE_PROPAGATED):
                records.append(
                    (input_name, _to_date(row.row_date), key, source, state)
                )
    frame = pd.DataFrame(records, columns=list(_INPUT_STATE_COLUMNS))
    if len(frame):
        frame = frame.sort_values(
            ["input", "row_date", "security_key"], kind="mergesort"
        ).reset_index(drop=True)
    return frame


def compute_label_states(
    labels: pd.DataFrame,
    horizons: pd.DataFrame,
    flags: pd.DataFrame,
    *,
    sessions: Sequence[date],
) -> pd.DataFrame:
    """Compute sparse label states in {QUARANTINE_PROPAGATED}."""
    session_list = list(sessions)
    crosses: dict[tuple[date, int], bool] = {}
    for row in horizons.itertuples(index=False):
        crosses[(_to_date(row.formation_date), int(row.h))] = bool(
            row.crosses_reserve
        )
    flag_by_key: dict[str, list[tuple[date, date | None]]] = {}
    for row in flags.itertuples(index=False):
        key = str(row.security_key)
        flag_session = _to_date(row.flag_session)
        prev_session = _to_date(row.prev_session)
        if flag_session is not None:
            flag_by_key.setdefault(key, []).append((flag_session, prev_session))
    records: list[tuple] = []
    for row in labels.itertuples(index=False):
        formation = _to_date(row.formation_date)
        key = str(row.security_key)
        h = int(row.h)
        if crosses.get((formation, h), False):
            continue
        observed_end = _to_date(getattr(row, "observed_segment_end", None))
        label_state = str(getattr(row, "label_state", ""))
        endpoint = _to_date(getattr(row, "endpoint", None))
        listed_end = _to_date(getattr(row, "listed_end", None))
        if label_state == "EMPTY_OBSERVED_PATH_IDENTITY":
            effective_end = formation
        elif observed_end is not None:
            effective_end = observed_end
        else:
            effective_end = endpoint
            if listed_end is not None and effective_end is not None:
                effective_end = min(effective_end, listed_end)
        if formation is None or effective_end is None:
            continue
        reason: str | None = None
        if effective_end == DEVELOPMENT_END:
            reason = "UNDETERMINABLE_RESERVE_BOUNDARY"
        else:
            for flag_session, prev_session in flag_by_key.get(key, ()):
                if formation < flag_session <= effective_end:
                    reason = "FLAG_IN_PATH"
                    break
                if prev_session is not None and formation <= prev_session <= effective_end:
                    reason = "ENDPOINT_OR_FORMATION_CLOSE"
                    break
        if reason is not None:
            records.append((formation, key, h, STATE_PROPAGATED, reason))
    frame = pd.DataFrame(records, columns=list(_LABEL_STATE_COLUMNS))
    if len(frame):
        frame = frame.sort_values(
            ["formation_date", "security_key", "h"], kind="mergesort"
        ).reset_index(drop=True)
    return frame


def compute_liquidity_exposure(
    members: pd.DataFrame,
    flags: pd.DataFrame,
    *,
    session_pos: Mapping[date, int],
    sessions: Sequence[date],
) -> pd.DataFrame:
    """Report-only liquidity-window exposure for every D6 member row."""
    session_list = list(sessions)
    exposed_keys: dict[tuple[str, date], bool] = {}
    for row in flags.itertuples(index=False):
        key = str(row.security_key)
        session = _to_date(row.flag_session)
        prev_session = _to_date(row.prev_session)
        for target in (session, prev_session):
            if target is not None:
                exposed_keys[(key, target)] = True
    records: list[tuple] = []
    for row in members.itertuples(index=False):
        formation = _to_date(row.formation_date)
        key = str(row.security_key)
        exposed = False
        if formation is not None:
            i = session_pos.get(formation)
            if i is not None:
                window = session_list[max(0, i - 63):i]
                exposed = any((key, s) in exposed_keys for s in window)
        records.append((formation, key, bool(exposed)))
    frame = pd.DataFrame(records, columns=list(_LIQUIDITY_COLUMNS))
    if len(frame):
        frame = frame.sort_values(
            ["formation_date", "security_key"], kind="mergesort"
        ).reset_index(drop=True)
    return frame


# ---------------------------------------------------------------------------
# Factor-input state calculus (Q3.3 NaN-shadow) and oracle
# ---------------------------------------------------------------------------


def _alias_inputs(spec: FactorSpec, inputs: Mapping[str, TrustedInput]) -> dict[str, TrustedInput]:
    mapping: dict[str, TrustedInput] = {}
    for item in spec.inputs:
        semantic = item.requirement.semantic_id
        if semantic not in inputs:
            raise QuarantineInputError(
                f"factor spec references {semantic!r} but it is not supplied"
            )
        mapping[item.alias] = inputs[semantic]
    return mapping


def _evaluate_wide(spec: FactorSpec, inputs: Mapping[str, TrustedInput]) -> pd.DataFrame:
    result = evaluate_factor(spec, _alias_inputs(spec, inputs))
    panel = result.evaluation.panel
    if len(panel) == 0:
        return pd.DataFrame()
    wide = (
        panel.pivot(index="date", columns="stock_id", values="value")
        .sort_index()
        .sort_index(axis=1)
    )
    return wide


def factor_input_states(
    factor_spec: FactorSpec,
    *,
    overlay: Overlay,
    inputs: Mapping[str, TrustedInput],
) -> pd.DataFrame:
    """The pure Q3.3 factor-input state calculus (NaN-shadow method)."""
    values = _evaluate_wide(factor_spec, inputs)
    shadow_inputs: dict[str, TrustedInput] = {}
    for name, trusted in inputs.items():
        mask = overlay.mask(name, trusted.values)
        shadow_inputs[name] = TrustedInput(
            values=trusted.values.mask(mask),
            capability=trusted.capability,
            evidence_class=trusted.evidence_class,
            vintage_evidence=trusted.vintage_evidence,
        )
    shadow = _evaluate_wide(factor_spec, shadow_inputs)
    values, shadow = values.align(shadow, join="outer")

    if values.shape == (0, 0):
        return pd.DataFrame(columns=["date", "security_key", "state"])

    state = np.full(values.shape, STATE_CLEAN, dtype=np.int64)
    state[values.isna().to_numpy()] = STATE_NOT_APPLICABLE
    finite_mask = values.notna().to_numpy()
    shadow_nan = shadow.isna().to_numpy()
    state[finite_mask & shadow_nan] = STATE_PROPAGATED
    residual = (
        (finite_mask & (~shadow_nan) & (values.to_numpy() != shadow.to_numpy()))
        .sum(axis=1)
    )

    long_rows: list[tuple] = []
    for i, row_date in enumerate(values.index):
        for j, security_key in enumerate(values.columns):
            long_rows.append(
                (pd.Timestamp(row_date).date(), str(security_key), int(state[i, j]))
            )
    frame = pd.DataFrame(long_rows, columns=["date", "security_key", "state"])
    frame = frame.sort_values(["date", "security_key"], kind="mergesort").reset_index(
        drop=True
    )
    frame.attrs["residual_cs_cells"] = {
        pd.Timestamp(d).date(): int(v) for d, v in zip(values.index, residual)
    }
    frame.attrs["values"] = values
    frame.attrs["shadow_values"] = shadow
    return frame


def _input_name_for_role(spec: FactorSpec, role: str) -> str:
    for item in spec.inputs:
        if item.alias == role:
            semantic = item.requirement.semantic_id
            if semantic not in INPUT_NAMES:
                raise UnsupportedQuarantineNode(
                    f"role {role!r} binds unknown input {semantic!r}"
                )
            return semantic
    raise UnsupportedQuarantineNode(f"role {role!r} is not declared")


def _zeros(template: pd.DataFrame) -> np.ndarray:
    return np.zeros(template.shape, dtype=np.int64)


def _oracle_node(
    node: Expr,
    spec: FactorSpec,
    overlay: Overlay,
    inputs: Mapping[str, TrustedInput],
    template: pd.DataFrame,
) -> np.ndarray:
    if isinstance(node, Literal):
        return _zeros(template)
    if isinstance(node, Field):
        name = _input_name_for_role(spec, node.role)
        frame = inputs[name].values.reindex(
            index=template.index, columns=template.columns
        )
        lagged = overlay.state_matrix(name, template.index, template.columns)
        state = lagged.to_numpy().astype(np.int64)
        state[frame.isna().to_numpy()] = STATE_NOT_APPLICABLE
        return state
    if isinstance(node, (Add, Sub, Mul, Div)):
        left = _oracle_node(node.left, spec, overlay, inputs, template)
        right = _oracle_node(node.right, spec, overlay, inputs, template)
        combined = np.where(
            (left == STATE_NOT_APPLICABLE) | (right == STATE_NOT_APPLICABLE),
            STATE_NOT_APPLICABLE,
            np.where(
                (left > 0) | (right > 0),
                STATE_PROPAGATED,
                STATE_CLEAN,
            ),
        )
        return combined
    if isinstance(node, Lag):
        child = _oracle_node(node.operand, spec, overlay, inputs, template)
        shifted = np.full_like(child, STATE_NOT_APPLICABLE)
        if node.periods == 0:
            return child
        if node.periods < child.shape[0]:
            shifted[node.periods:] = child[: -node.periods]
        return shifted
    if isinstance(node, Rolling):
        child = _oracle_node(node.operand, spec, overlay, inputs, template)
        priority = np.where(
            child == STATE_NOT_APPLICABLE,
            STATE_NOT_APPLICABLE,
            np.where(child > 0, STATE_PROPAGATED, STATE_CLEAN),
        )
        out = np.full_like(child, STATE_NOT_APPLICABLE)
        window = int(node.window)
        for i in range(child.shape[0]):
            start = i - window + 1
            if start < 0:
                continue
            block = priority[start : i + 1]
            out[i] = block.max(axis=0)
        return out
    if isinstance(node, CrossSectional):
        child = _oracle_node(node.operand, spec, overlay, inputs, template)
        return np.where(
            child == STATE_QUARANTINED,
            STATE_PROPAGATED,
            child,
        )
    raise UnsupportedQuarantineNode(
        f"node type {type(node).__name__} is outside the Q3.3 closed set"
    )


def oracle_factor_input_states(
    factor_spec: FactorSpec,
    *,
    overlay: Overlay,
    inputs: Mapping[str, TrustedInput],
) -> pd.DataFrame:
    """Independent AST-walk oracle for :func:`factor_input_states` (tests only)."""
    values = _evaluate_wide(factor_spec, inputs)
    if values.shape == (0, 0):
        return pd.DataFrame(columns=["date", "security_key", "state"])
    template = pd.DataFrame(
        np.nan, index=values.index, columns=values.columns
    )
    states = _oracle_node(factor_spec.expression, factor_spec, overlay, inputs, template)
    states = np.where(states == STATE_QUARANTINED, STATE_PROPAGATED, states)
    long_rows: list[tuple] = []
    for i, row_date in enumerate(values.index):
        for j, security_key in enumerate(values.columns):
            long_rows.append(
                (pd.Timestamp(row_date).date(), str(security_key), int(states[i, j]))
            )
    frame = pd.DataFrame(long_rows, columns=["date", "security_key", "state"])
    return frame.sort_values(["date", "security_key"], kind="mergesort").reset_index(
        drop=True
    )


# ---------------------------------------------------------------------------
# Identity (R3)
# ---------------------------------------------------------------------------


def build_identity_payload(
    *,
    repo_root: Path | str | None = None,
    parent_authority: Sequence[tuple[str, str]] | None = None,
    inherited_artifacts: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Build the canonical ``dataset_identity.json`` payload."""
    parent_authority = parent_authority or PARENT_AUTHORITY
    inherited = dict(inherited_artifacts or INHERITED_ARTIFACTS)
    return {
        "dataset_id": DATASET_ID,
        "amendment": {"path": AMENDMENT_PATH, "sha256": AMENDMENT_SHA256},
        "quarantine_rule_hash": QUARANTINE_RULE_HASH,
        "parent_dataset_id": PARENT_DATASET_ID,
        "parent_authority": {path: sha for path, sha in parent_authority},
        "inherited_artifacts": inherited,
        "d8": {
            "genesis_seq": D8_GENESIS_SEQ,
            "genesis_record_hash": D8_GENESIS_RECORD_HASH,
            "source_label": D8_SOURCE_LABEL,
            "semantics": "INHERITED_NOT_REREGISTERED",
        },
        "knowledge_pit": {
            "v1_2_records": "NONE_AUTHORIZED",
            "rule": (
                "development P-Q artifacts need no Knowledge-PIT record (D7c "
                "clarification \u00a76 precedent); any future Phase-10 record "
                "citing Dataset-v1.2 evidence must commit to identity_hash, "
                "under a separate authorization"
            ),
        },
        "scope": "DEVELOPMENT_ONLY",
    }


def compute_identity_hash(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON bytes of an identity payload."""
    return _sha256_hex(canonical_json(payload).encode("utf-8"))


def _verify_identity_fields(
    payload: Mapping[str, Any],
    *,
    repo_root: Path,
    root: Path,
    bindings: Bindings,
    read_records_fn: Any,
    knowledge_log_path: Path | None,
) -> str:
    if payload.get("dataset_id") != DATASET_ID:
        raise DatasetIdentityError("identity dataset_id mismatch")
    if payload.get("quarantine_rule_hash") != QUARANTINE_RULE_HASH:
        raise DatasetIdentityError("identity quarantine_rule_hash mismatch")
    if payload.get("parent_dataset_id") != PARENT_DATASET_ID:
        raise DatasetIdentityError("identity parent_dataset_id mismatch")

    amendment = payload.get("amendment", {})
    if amendment.get("path") != AMENDMENT_PATH:
        raise DatasetIdentityError("identity amendment path mismatch")
    if amendment.get("sha256") != AMENDMENT_SHA256:
        raise DatasetIdentityError("identity amendment hash mismatch")
    amendment_file = repo_root / AMENDMENT_PATH
    if _sha256_file(amendment_file) != AMENDMENT_SHA256:
        raise DatasetIdentityError("amendment bytes do not match the binding")

    rules_file = repo_root / RULES_PATH
    if _sha256_file(rules_file) != QUARANTINE_RULE_HASH:
        raise DatasetIdentityError("quarantine rules bytes do not match")

    parent = payload.get("parent_authority", {})
    for path, expected in PARENT_AUTHORITY:
        if parent.get(path) != expected:
            raise DatasetIdentityError(f"parent authority hash mismatch: {path}")
        if _sha256_file(repo_root / path) != expected:
            raise DatasetIdentityError(f"parent authority bytes mismatch: {path}")

    inherited = payload.get("inherited_artifacts", {})
    if set(inherited) != set(INHERITED_ARTIFACTS):
        raise DatasetIdentityError("inherited artifact id set mismatch")
    for artifact_id, expected in inherited.items():
        if expected is None:
            continue
        binding = bindings.artifact_manifests.get(artifact_id)
        if binding is None:
            raise DatasetIdentityError(
                f"no manifest binding for inherited artifact {artifact_id}"
            )
        relative, field = binding
        manifest_path = root / relative
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DatasetIdentityError(
                f"cannot read manifest for {artifact_id}: {exc}"
            ) from exc
        if manifest.get(field) != expected:
            raise DatasetIdentityError(
                f"inherited artifact {artifact_id} manifest {field} mismatch"
            )

    d8 = payload.get("d8", {})
    if d8.get("genesis_seq") != D8_GENESIS_SEQ:
        raise DatasetIdentityError("D8 genesis seq mismatch")
    if d8.get("genesis_record_hash") != D8_GENESIS_RECORD_HASH:
        raise DatasetIdentityError("D8 genesis record hash mismatch")
    if d8.get("source_label") != D8_SOURCE_LABEL:
        raise DatasetIdentityError("D8 source_label mismatch")
    if d8.get("semantics") != "INHERITED_NOT_REREGISTERED":
        raise DatasetIdentityError("D8 semantics mismatch")

    knowledge_pit = payload.get("knowledge_pit", {})
    if knowledge_pit.get("v1_2_records") != "NONE_AUTHORIZED":
        raise DatasetIdentityError("knowledge_pit v1_2_records mismatch")

    log_path = knowledge_log_path or _default_knowledge_log(root)
    records = read_records_fn(log_path)
    if not records:
        raise DatasetIdentityError("Knowledge-PIT log has no genesis record")
    genesis = records[0]
    if int(genesis.seq) != D8_GENESIS_SEQ:
        raise DatasetIdentityError("Knowledge-PIT genesis seq mismatch")
    if genesis.kind.value != "ARTIFACT":
        raise DatasetIdentityError("Knowledge-PIT genesis is not ARTIFACT")
    if genesis.record_hash != D8_GENESIS_RECORD_HASH:
        raise DatasetIdentityError("Knowledge-PIT genesis record_hash mismatch")
    if genesis.payload.get("source_label") != D8_SOURCE_LABEL:
        raise DatasetIdentityError("Knowledge-PIT genesis source_label mismatch")
    for record in records:
        blob = canonical_json(record.to_dict())
        if DATASET_ID in blob:
            raise DatasetIdentityError(
                "a Knowledge-PIT record references discovery_dataset_v1_2"
            )
    return compute_identity_hash(payload)


def verify_dataset_v1_2_identity(
    root: Path | str,
    *,
    bindings: Bindings | None = None,
    repo_root: Path | str | None = None,
    knowledge_log_path: Path | str | None = None,
    read_records_fn: Any | None = None,
) -> str:
    """Verify the Dataset-v1.2 identity read-only; return the identity hash."""
    bindings = bindings if bindings is not None else DEFAULT_BINDINGS
    repo_root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    read_records_fn = read_records_fn or _knowledge.read_records
    root = Path(root)
    pq_dir = _overlay_directory(root, bindings)
    identity_path = pq_dir / "dataset_identity.json"
    try:
        payload = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetIdentityError(
            f"cannot read dataset_identity.json: {exc}"
        ) from exc
    return _verify_identity_fields(
        payload,
        repo_root=repo_root,
        root=root,
        bindings=bindings,
        read_records_fn=read_records_fn,
        knowledge_log_path=Path(knowledge_log_path) if knowledge_log_path else None,
    )


# ---------------------------------------------------------------------------
# pq_id / pq_hash and serialization
# ---------------------------------------------------------------------------


def _bindings_payload(bindings: Bindings) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for stage in bindings.stages():
        payload[stage.stage] = {
            "artifact_id": stage.artifact_id,
            "manifest_relative_path": stage.manifest_relative_path,
            "manifest_hash_field": stage.manifest_hash_field,
            "manifest_hash": stage.manifest_hash,
            "files": {b.relative_path: b.sha256 for b in stage.files},
        }
    return payload


def compute_pq_id(bindings: Bindings, code_sha256: str) -> str:
    payload = {
        "bindings": _bindings_payload(bindings),
        "authority": {path: sha for path, sha in PARENT_AUTHORITY},
        "amendment_sha256": AMENDMENT_SHA256,
        "quarantine_rule_hash": QUARANTINE_RULE_HASH,
        "quarantine_py_sha256": code_sha256,
    }
    return "pq-" + _sha256_hex(canonical_json(payload).encode("utf-8"))[:16]


def _overlay_directory(root: Path, bindings: Bindings) -> Path:
    code_sha = _sha256_file(Path(__file__))
    return Path(root) / "derived" / "quarantine" / compute_pq_id(bindings, code_sha)


_FLAGS_SCHEMA = pa.schema(
    [
        ("security_key", pa.string()),
        ("flag_session", pa.date32()),
        ("prev_session", pa.date32()),
        ("next_session", pa.date32()),
        ("next_beyond_development", pa.bool_()),
    ]
)
_PRIMITIVE_SCHEMA = pa.schema(
    [
        ("primitive", pa.string()),
        ("security_key", pa.string()),
        ("session", pa.date32()),
        ("state", pa.int8()),
        ("reason", pa.string()),
    ]
)
_INPUT_SCHEMA = pa.schema(
    [
        ("input", pa.string()),
        ("row_date", pa.date32()),
        ("security_key", pa.string()),
        ("source_session", pa.date32()),
        ("state", pa.int8()),
    ]
)
_LABEL_SCHEMA = pa.schema(
    [
        ("formation_date", pa.date32()),
        ("security_key", pa.string()),
        ("h", pa.int64()),
        ("state", pa.int8()),
        ("reason", pa.string()),
    ]
)
_LIQUIDITY_SCHEMA = pa.schema(
    [
        ("formation_date", pa.date32()),
        ("security_key", pa.string()),
        ("liquidity_window_exposed", pa.bool_()),
    ]
)


def _serialize(frame: pd.DataFrame, schema: pa.Schema) -> pd.DataFrame:
    columns = [field.name for field in schema]
    if len(frame) == 0:
        return pd.DataFrame({name: pd.Series([], dtype="object") for name in columns})
    return frame.loc[:, columns].copy()


def _write_frame(path: Path, frame: pd.DataFrame, schema: pa.Schema) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _serialize(frame, schema)
    table = pa.Table.from_pandas(data, schema=schema, preserve_index=False)
    import pyarrow.parquet as pq

    pq.write_table(table, path, compression="zstd")
    return _sha256_file(path)


def _counts(
    flags: pd.DataFrame,
    primitive: pd.DataFrame,
    input_states: pd.DataFrame,
    labels: pd.DataFrame,
    liquidity: pd.DataFrame,
) -> dict[str, Any]:
    by_primitive: dict[str, dict[str, int]] = {}
    for row in primitive.itertuples(index=False):
        by_primitive.setdefault(str(row.primitive), {})
        key = str(int(row.state))
        by_primitive[str(row.primitive)][key] = (
            by_primitive[str(row.primitive)].get(key, 0) + 1
        )
    by_reason: dict[str, int] = {}
    for row in labels.itertuples(index=False):
        by_reason[str(row.reason)] = by_reason.get(str(row.reason), 0) + 1
    label_state_counts: dict[str, dict[str, int]] = {}
    for row in labels.itertuples(index=False):
        bucket = label_state_counts.setdefault(str(int(row.h)), {})
        bucket[str(row.reason)] = bucket.get(str(row.reason), 0) + 1
    return {
        "flags": int(len(flags)),
        "q_elements": int(len(primitive)),
        "primitive_states": by_primitive,
        "input_states": int(len(input_states)),
        "labels_by_reason": by_reason,
        "label_state_counts": label_state_counts,
        "liquidity_exposed": int(
            liquidity["liquidity_window_exposed"].sum() if len(liquidity) else 0
        ),
    }


def build_overlay(
    root: Path | str,
    *,
    bindings: Bindings | None = None,
    calendar: Any | None = None,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    """Build (or verify) the deterministic development quarantine overlay."""
    bindings = bindings if bindings is not None else DEFAULT_BINDINGS
    repo_root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    root = Path(root)
    calendar = calendar if calendar is not None else _build_calendar()
    sessions = [ts.date() for ts in calendar.dates]
    code_sha = _sha256_file(Path(__file__))

    resolver = QuarantineResolver(root, bindings)
    _verify_parent_authority(repo_root)
    _verify_documents(bindings, repo_root)
    _verify_pinned_code(bindings, repo_root)
    for stage in bindings.stages():
        _verify_stage_manifest(root, resolver, stage)

    day_flags = resolver.read_parquet(
        _stage_file(bindings, "D5", 0),
        columns=["ticker", "date", "class_or_flag", "disposition"],
    )
    identity = resolver.read_parquet(
        _stage_file(bindings, "D4", 0),
        columns=["security_key", "ticker", "listed_start", "listed_end", "state"],
    )
    labels = resolver.read_parquet(
        _stage_file(bindings, "D7A", 0),
        columns=[
            "formation_date", "security_key", "h", "endpoint", "label_state",
            "observed_segment_end",
        ],
    )
    members = resolver.read_parquet(
        _stage_file(bindings, "D7A", 1),
        columns=["formation_date", "security_key", "listed_end_dev"],
    )
    horizons = resolver.read_parquet(
        _stage_file(bindings, "D7A", 2),
        columns=["formation_date", "h", "endpoint", "crosses_reserve"],
    )
    universe = resolver.read_parquet(
        _stage_file(bindings, "D6", 0),
        columns=["formation_date", "security_key"],
    )
    segments = resolver.read_parquet(
        _stage_file(bindings, "D7C", 3),
        columns=[
            "security_key", "segment_id", "anchor_session", "last_session",
            "admitted",
        ],
    )
    long_rows: dict[str, pd.DataFrame] = {}
    file_index = {
        INPUT_DAILY_TOTAL_RETURN: 0,
        INPUT_DOLLAR_VOLUME: 1,
        INPUT_TOTAL_RETURN_INDEX: 2,
    }
    for name in INPUT_NAMES:
        long_rows[name] = resolver.read_parquet(
            _stage_file(bindings, "D7C", file_index[name]),
            columns=["row_date", "security_key", "source_session", "value"],
        )

    for frame in (
        day_flags, identity, labels, members, horizons, universe, segments,
        *long_rows.values(),
    ):
        _assert_dates_development(
            frame,
            ("date", "listed_start", "listed_end", "formation_date", "endpoint",
             "observed_segment_end", "anchor_session", "last_session", "row_date",
             "source_session"),
        )

    session_pos = {s: i for i, s in enumerate(sessions)}
    for name in INPUT_NAMES:
        for row in long_rows[name].itertuples(index=False):
            row_date = _to_date(row.row_date)
            source_session = _to_date(row.source_session)
            index = session_pos.get(row_date)
            expected = sessions[index - 1] if index is not None and index > 0 else None
            if source_session != expected:
                raise QuarantineConsistencyError(
                    f"D7c {name} source_session {source_session} != "
                    f"prev(row_date) {expected}"
                )

    flags = compute_flag_panel(day_flags, identity, sessions)
    suspect_count = int(
        (day_flags["class_or_flag"] == "CORP_ACTION_SUSPECT").sum()
    )
    if (
        bindings.expected_flag_count is not None
        and suspect_count != bindings.expected_flag_count
    ):
        raise QuarantineInputError(
            f"CORP_ACTION_SUSPECT flag count {suspect_count} != "
            f"{bindings.expected_flag_count}"
        )
    present: dict[str, set[tuple[str, date]]] = {}
    for name, long in long_rows.items():
        cells: set[tuple[str, date]] = set()
        for row in long.itertuples(index=False):
            value = row.value
            if value is None or (isinstance(value, float) and np.isnan(value)):
                continue
            cells.add((str(row.security_key), _to_date(row.source_session)))
        present[name] = cells

    tri_segments = [
        {
            "security_key": str(row.security_key),
            "anchor_session": _to_date(row.anchor_session),
            "last_session": _to_date(row.last_session),
            "admitted": bool(row.admitted),
        }
        for row in segments.itertuples(index=False)
    ]
    primitive = compute_primitive_states(
        flags, present=present, tri_segments=tri_segments
    )
    input_states = compute_input_states(flags, primitive, long_rows=long_rows)
    label_states = compute_label_states(labels, horizons, flags, sessions=sessions)
    liquidity = compute_liquidity_exposure(
        universe,
        flags,
        session_pos={s: i for i, s in enumerate(sessions)},
        sessions=sessions,
    )

    identity_payload = build_identity_payload(repo_root=repo_root)
    identity_hash = compute_identity_hash(identity_payload)
    counts = _counts(flags, primitive, input_states, label_states, liquidity)

    pq_dir = _overlay_directory(root, bindings)
    files_sha256: dict[str, str] = {}
    row_counts: dict[str, int] = {}
    outputs = (
        ("flags.parquet", flags, _FLAGS_SCHEMA),
        ("primitive_states.parquet", primitive, _PRIMITIVE_SCHEMA),
        ("input_states.parquet", input_states, _INPUT_SCHEMA),
        ("label_states.parquet", label_states, _LABEL_SCHEMA),
        ("liquidity_exposure.parquet", liquidity, _LIQUIDITY_SCHEMA),
    )
    for filename, frame, schema in outputs:
        files_sha256[filename] = _write_frame(pq_dir / filename, frame, schema)
        row_counts[filename] = int(len(frame))
    identity_path = pq_dir / "dataset_identity.json"
    identity_path.write_bytes(_canonical_bytes(identity_payload))
    files_sha256["dataset_identity.json"] = _sha256_file(identity_path)
    row_counts["dataset_identity.json"] = 1

    manifest = {
        "schema": SCHEMA,
        "pq_id": pq_dir.name,
        "pq_hash": "",
        "bindings": _bindings_payload(bindings),
        "authority": {path: sha for path, sha in PARENT_AUTHORITY},
        "quarantine_rule_hash": QUARANTINE_RULE_HASH,
        "quarantine_py_sha256": code_sha,
        "counts": counts,
        "identity_hash": identity_hash,
        "files_sha256": files_sha256,
        "row_counts": row_counts,
        "scope": "DEVELOPMENT_ONLY",
    }
    pq_hash = _sha256_hex(
        canonical_json(
            {"files_sha256": files_sha256, "row_counts": row_counts, "counts": counts}
        ).encode("utf-8")
    )
    manifest["pq_hash"] = pq_hash
    manifest_path = pq_dir / "pq.json"
    new_bytes = _canonical_bytes(manifest)

    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("pq_hash") != pq_hash:
            raise NondeterministicOverlayError(
                "an existing overlay directory holds a different pq_hash"
            )
        existing_files = existing.get("files_sha256", {})
        for filename, expected in files_sha256.items():
            actual = _sha256_file(pq_dir / filename)
            if actual != expected or existing_files.get(filename) != expected:
                raise NondeterministicOverlayError(
                    f"overlay file {filename} differs from the manifest"
                )
        return manifest

    pq_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(new_bytes)
    return manifest


def _stage_file(bindings: Bindings, stage_name: str, index: int) -> str:
    stage = bindings.stage(stage_name)
    return stage.files[index].relative_path


def verify_overlay(
    root: Path | str,
    *,
    bindings: Bindings | None = None,
    calendar: Any | None = None,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    """Verify an existing overlay is deterministic and self-consistent."""
    bindings = bindings if bindings is not None else DEFAULT_BINDINGS
    root = Path(root)
    pq_dir = _overlay_directory(root, bindings)
    manifest_path = pq_dir / "pq.json"
    if not manifest_path.is_file():
        raise QuarantineInputError(f"no overlay at {pq_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for filename, expected in manifest.get("files_sha256", {}).items():
        actual = _sha256_file(pq_dir / filename)
        if actual != expected:
            raise QuarantineBindingError(
                f"overlay file {filename} hash {actual} != {expected}"
            )
    return manifest


def _build_calendar() -> Any:
    from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

    return build_nyse_calendar(date(2015, 1, 2), date(2027, 12, 31))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build or verify the Dataset-v1.2 P-Q quarantine overlay."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--root", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        manifest = build_overlay(args.root)
    else:
        manifest = verify_overlay(args.root)
    print(manifest["pq_id"])
    print(manifest["pq_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
