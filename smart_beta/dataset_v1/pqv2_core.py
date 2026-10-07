"""P-Q v2/v2.1 core: frozen identities, S2 correction sets, typed statuses and combinatorics.

This module is the PQ2-A deliverable of the frozen P-Q v2 implementation plan
(``docs/discovery_dataset_v1_2_pq_v2_implementation_plan.md``) and its P-Q v2.1
amendment (``docs/discovery_dataset_v1_2_pq_v2_1_implementation_plan.md``).  It
implements the core pieces of the frozen methodology
(``docs/discovery_dataset_v1_2_pq_v2_1_methodology.md``):

* the versioned authorities (:class:`MethodologyAuthority`, :data:`AUTHORITY_V2`,
  :data:`AUTHORITY_V2_1`, :data:`CURRENT_AUTHORITY`), the four frozen identities
  (:data:`METHODOLOGY_SHA256`, :data:`CS_ID`, :data:`ED_ID`, :data:`CE_ID`),
  :func:`verify_authority` and :func:`require_current_authority`;
* the correction-sensitivity set S2 (M§3) as :class:`PositiveInterval`
  arithmetic with :func:`s2_absolute` / :func:`s2_relative` (no global K);
* the exhaustive typed status vocabularies (M§14, M§15) plus the M§8A source
  states and deterministic cause model (:class:`SourceState`,
  :class:`CauseSet`, :func:`cause_for_source_state`,
  :func:`label_status_for_causes`);
* the source dataclasses handed from PQ2-B to PQ2-C/D/E/F;
* the exact leg-extremum combinatorics of M§12 Prop. 7
  (:func:`leg_min_mean` / :func:`leg_max_mean`);
* the M§11 deterministic winsorization-block sufficient check
  (:func:`winsor_block_ok`);
* the bucket sizes implied by ``portfolio_sort.assign_groups``
  (:func:`bucket_sizes`).

Nothing here reads a dataset artifact, the reserve, holdout, ``raw/`` or
``dsc_evidence``.  :func:`verify_authority` reads only the frozen methodology
document and the pinned reused production files.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Iterable, Mapping, Sequence

__all__ = [
    # identities / constants
    "MethodologyAuthority",
    "AUTHORITY_V2",
    "AUTHORITY_V2_1",
    "CURRENT_AUTHORITY",
    "METHODOLOGY_SHA256",
    "CS_ID",
    "ED_ID",
    "CE_ID",
    "PINNED_CODE",
    "S2_LO",
    "S2_HI",
    "DEVELOPMENT_END",
    "RESERVE_START",
    "MISDATED_WINDOW",
    "MISDATED_ACTION_TYPES",
    # errors
    "PQv2Error",
    "PQv2BindingError",
    "PQv2InputError",
    "PQv2InvariantError",
    # authority
    "verify_authority",
    "require_current_authority",
    "pqv2_code_sha256",
    "pqv2_code_manifest",
    "identity_lines",
    "recompute_identity_hashes",
    "methodology_path",
    # intervals / S2
    "PositiveInterval",
    "UNIT",
    "s2_absolute",
    "s2_relative",
    # typed statuses
    "LabelStatus",
    "NotAssessedReason",
    "AssignmentRule",
    "ClassificationStatus",
    "SourceState",
    "CauseSet",
    "EMPTY_CAUSES",
    "PERIOD_CAUSE_PRECEDENCE",
    "UNKNOWN_CAUSES",
    "cause_for_source_state",
    "label_status_for_causes",
    "must_short_circuit",
    "LABEL_STATUSES",
    "NOT_ASSESSED_REASONS",
    "ASSIGNMENT_RULES",
    "CLASSIFICATION_STATUSES",
    # dataclasses
    "NotAssessed",
    "UnresolvedFlag",
    "UncertainTransition",
    "MisdatedCandidate",
    "SourceSet",
    # combinatorics
    "leg_min_mean",
    "leg_max_mean",
    "winsor_block_ok",
    "bucket_sizes",
]


# ---------------------------------------------------------------------------
# Frozen identity constants (plan header; M§3, M§15)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MethodologyAuthority:
    """A frozen, versioned methodology identity bundle.

    Historical authorities stay reconstructable: v2 remains bound to its own
    preserved (byte-identical) document and its old ED-ID, while
    :data:`CURRENT_AUTHORITY` names the authority a fresh execution must bind.
    """

    version: str
    methodology_relative: str
    methodology_sha256: str
    cs_id: str
    ed_id: str
    ce_id: str


#: Historical P-Q v2 authority (document preserved byte-identical, M§17).
AUTHORITY_V2 = MethodologyAuthority(
    version="P-Q v2",
    methodology_relative="docs/discovery_dataset_v1_2_pq_v2_methodology.md",
    methodology_sha256=(
        "a31ed9c49cd3a03208ae28539a8b5f5531916e31fac32bbd60e2af07746fa56d"
    ),
    cs_id="432de09746bbc4c16c332d662c24d6d7369787d55e8b704e3a521494b73bdae2",
    ed_id="e979924ee4efc365155d8a79089fb940a28ebede7099f67373f0d654bd479aeb",
    ce_id="605fec93812d4165b6bf455a9fb453cd7c0c6c4a9f5a2a0fc418b53b0d0dbaaf",
)

#: Current P-Q v2.1 authority (M§8A, M§15, M§17).
AUTHORITY_V2_1 = MethodologyAuthority(
    version="P-Q v2.1",
    methodology_relative="docs/discovery_dataset_v1_2_pq_v2_1_methodology.md",
    methodology_sha256=(
        "66772bb1b12a134ca35a3ff5e3d475e00c998b95d3cdcb1e3223fd5f012d5d1e"
    ),
    cs_id="432de09746bbc4c16c332d662c24d6d7369787d55e8b704e3a521494b73bdae2",
    ed_id="65bd7d1341e70674132bc7729870876adf5457fe9ce4c277d0f1148df343a4d6",
    ce_id="605fec93812d4165b6bf455a9fb453cd7c0c6c4a9f5a2a0fc418b53b0d0dbaaf",
)

#: The authority a fresh evaluation must bind.
CURRENT_AUTHORITY = AUTHORITY_V2_1

#: SHA-256 of the current methodology document.
METHODOLOGY_SHA256 = CURRENT_AUTHORITY.methodology_sha256
#: SHA-256 of the canonical correction-set line (M§3, unchanged in v2.1).
CS_ID = CURRENT_AUTHORITY.cs_id
#: SHA-256 of the canonical exact-dependency line (M§15, new in v2.1).
ED_ID = CURRENT_AUTHORITY.ed_id
#: SHA-256 of the canonical certified-expression line (M§15, unchanged).
CE_ID = CURRENT_AUTHORITY.ce_id

#: The frozen S2 window endpoints (M§3).  There is deliberately no global K.
S2_LO = 0.5
S2_HI = 1.5

#: Development/reserve boundary (M§8 firewall).
DEVELOPMENT_END = date(2023, 6, 30)
RESERVE_START = date(2023, 7, 3)

#: MISDATED candidate window, in trusted sessions (M§7).
MISDATED_WINDOW = 5
#: Recorded corporate-action types eligible as MISDATED candidates (M§7).
MISDATED_ACTION_TYPES = ("split", "dividend")

#: Pinned reused production files (plan §5); SHA-256 computed at the wave-1 base
#: commit ``b2db30ba6aee428dd450adeda7cf96d50b81bedf``.
PINNED_CODE: Mapping[str, str] = MappingProxyType(
    {
        "smart_beta/campaign/diagnostics_store.py": (
            "f8c833d60e0d1e69f8318ac699f37a288d0de2feb1df0575a98c00f37c0f34b3"
        ),
        "smart_beta/dataset_v1/delisting_bridge.py": (
            "125893bec3eb9db61d6678aaabb0d9e16bce5b03a576eb3a718d18c20aaf2efc"
        ),
        "smart_beta/dataset_v1/quarantine.py": (
            "3d3bff02294b452d797c262f8232a4abaa51d55d05b521f2635156dec3323a1c"
        ),
        "smart_beta/dataset_v1/returns.py": (
            "f417ecd1211ee5a99b3b3d8cfec1766f8a99c3723ee4b918b660ff488cf300d3"
        ),
        "smart_beta/dataset_v1/trusted_inputs.py": (
            "c5e30824f8323e0d9a9622f41f7f53861cda02f515a04d562090fd8306b30b74"
        ),
        "smart_beta/engines/portfolio_sort.py": (
            "d9776fdca3186795b87b3825f2c43b77e1ed79c66bf4ec4e05cb2a00f2178b57"
        ),
        "smart_beta/evaluation/engine.py": (
            "4ef0caa7d453a27a35c23fc3c7d7ece4b17c132cff651efa6dd79cb2a39d31c8"
        ),
        "smart_beta/evaluation/metrics.py": (
            "747282a2ee7cc7b63d0511d29ab3bdf8c3c9aceea9fb965dd64de543d76831a8"
        ),
        "smart_beta/evaluation/portfolio.py": (
            "5dea8adc581ae1bfe2bc7b771db37f36a71c2331cf702d9c8b7174039c3ed2dc"
        ),
        "smart_beta/spec/evaluator.py": (
            "aecdeba642ca23f3285b8f55565afb6edaf255322783345e22a50c20c405bcce"
        ),
        "smart_beta/spec/transforms.py": (
            "15708149406017a6c970d42aebef7b0e2ea9ed6b0be48182f7769006a9ff5a66"
        ),
    }
)

_METHODOLOGY_RELATIVE = CURRENT_AUTHORITY.methodology_relative

#: Historical methodologies the current authority must keep byte-identical
#: (M§17: the P-Q v2 document and its evidence are preserved, not rewritten).
#: The current-authority check additionally binds their preservation because the
#: v2 authority is the historical evidence a v2.1 evaluation supersedes.
PRESERVED_AUTHORITIES: tuple[MethodologyAuthority, ...] = (AUTHORITY_V2,)


#: The three identity headers whose first following fenced code block is hashed.
IDENTITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("CS", "Correction-set identity (CS-ID)"),
    ("ED", "Exact-dependency identity (ED-ID)"),
    ("CE", "Certified-expression identity (CE-ID)"),
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PQv2Error(Exception):
    """Base class for every P-Q v2 error."""


class PQv2BindingError(PQv2Error):
    """A frozen identity or pinned-code hash did not match (fails closed)."""


class PQv2InputError(PQv2Error):
    """An input violated a frozen domain restriction."""


class PQv2InvariantError(PQv2Error):
    """A frozen invariant was observed to be violated."""


# ---------------------------------------------------------------------------
# Authority binding
# ---------------------------------------------------------------------------


def _repo_root(repo_root: Path | str | None) -> Path:
    if repo_root is None:
        return Path(__file__).resolve().parents[2]
    return Path(repo_root)


def methodology_path(
    repo_root: Path | str | None = None,
    authority: MethodologyAuthority = CURRENT_AUTHORITY,
) -> Path:
    """Return *authority*'s methodology document path under ``repo_root``."""

    return _repo_root(repo_root) / authority.methodology_relative


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _extract_code_block_after(lines: Sequence[str], header: str) -> str:
    """Return the first fenced block content after ``header`` (fences stripped).

    The returned string is exactly the block content without the fence lines and
    without a trailing newline (``"\\n".join(block_lines)``), which are the exact
    bytes that produced the frozen identity hashes.
    """

    header_index = None
    for index, line in enumerate(lines):
        if header in line:
            header_index = index
            break
    if header_index is None:
        raise PQv2BindingError(f"identity header not found: {header!r}")

    opening = header_index + 1
    while opening < len(lines) and not lines[opening].lstrip().startswith("```"):
        opening += 1
    if opening >= len(lines):
        raise PQv2BindingError(f"no fenced block follows {header!r}")

    block: list[str] = []
    cursor = opening + 1
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("```"):
        block.append(lines[cursor])
        cursor += 1
    if cursor >= len(lines):
        raise PQv2BindingError(f"unterminated fenced block after {header!r}")
    return "\n".join(block)


def identity_lines(
    repo_root: Path | str | None = None,
    authority: MethodologyAuthority = CURRENT_AUTHORITY,
) -> dict[str, str]:
    """Return the canonical identity lines keyed ``"CS"``, ``"ED"``, ``"CE"``."""

    text = methodology_path(repo_root, authority).read_text(encoding="utf-8")
    lines = text.splitlines()
    return {
        key: _extract_code_block_after(lines, header)
        for key, header in IDENTITY_HEADERS
    }


def recompute_identity_hashes(
    repo_root: Path | str | None = None,
    authority: MethodologyAuthority = CURRENT_AUTHORITY,
) -> dict[str, str]:
    """Recompute the four frozen identities from *authority*'s document."""

    root = _repo_root(repo_root)
    lines = identity_lines(root, authority)
    return {
        "methodology": _sha256_file(methodology_path(root, authority)),
        "CS": _sha256_bytes(lines["CS"].encode("utf-8")),
        "ED": _sha256_bytes(lines["ED"].encode("utf-8")),
        "CE": _sha256_bytes(lines["CE"].encode("utf-8")),
    }


def verify_authority(
    repo_root: Path | str | None = None,
    authority: MethodologyAuthority = CURRENT_AUTHORITY,
) -> None:
    """Verify the plan §2 (a)-(c) authority bindings; raise on any mismatch.

    (a) The methodology document SHA-256 equals ``authority.methodology_sha256``.
    (b) The three canonical identity lines' SHA-256 values equal ``authority``'s
        CS/ED/CE ids.
    (c) Every file in :data:`PINNED_CODE` hashes to its pinned value.

    The historical :data:`AUTHORITY_V2` still verifies on master because the v2
    document is preserved byte-identical.
    """

    root = _repo_root(repo_root)

    document = methodology_path(root, authority)
    if not document.is_file():
        raise PQv2BindingError(f"methodology document missing: {document}")
    actual_methodology = _sha256_file(document)
    if actual_methodology != authority.methodology_sha256:
        raise PQv2BindingError(
            "methodology document SHA-256 mismatch: "
            f"{actual_methodology} != {authority.methodology_sha256}"
        )

    if authority is CURRENT_AUTHORITY:
        for preserved in PRESERVED_AUTHORITIES:
            preserved_path = root / preserved.methodology_relative
            if not preserved_path.is_file():
                continue
            digest = _sha256_file(preserved_path)
            if digest != preserved.methodology_sha256:
                raise PQv2BindingError(
                    f"preserved {preserved.version} document changed: "
                    f"{preserved.methodology_relative} ({digest} != "
                    f"{preserved.methodology_sha256})"
                )

    lines = identity_lines(root, authority)
    expected = {"CS": authority.cs_id, "ED": authority.ed_id, "CE": authority.ce_id}
    for key, _header in IDENTITY_HEADERS:
        digest = _sha256_bytes(lines[key].encode("utf-8"))
        if digest != expected[key]:
            raise PQv2BindingError(
                f"{key}-ID mismatch: {digest} != {expected[key]}"
            )

    for relative, pinned in sorted(PINNED_CODE.items()):
        path = root / relative
        if not path.is_file():
            raise PQv2BindingError(f"pinned file missing: {relative}")
        digest = _sha256_file(path)
        if digest != pinned:
            raise PQv2BindingError(
                f"pinned file changed: {relative} ({digest} != {pinned})"
            )


def require_current_authority(
    methodology_sha256: str,
    cs_id: str,
    ed_id: str,
    ce_id: str,
) -> None:
    """Fail closed unless all four identities bind :data:`CURRENT_AUTHORITY`.

    A stale v2 methodology hash or the stale v2 ED-ID is rejected with
    :class:`PQv2BindingError`.
    """

    expected = CURRENT_AUTHORITY
    actual = (methodology_sha256, cs_id, ed_id, ce_id)
    frozen = (
        expected.methodology_sha256,
        expected.cs_id,
        expected.ed_id,
        expected.ce_id,
    )
    if actual != frozen:
        raise PQv2BindingError(
            f"declaration does not bind the current authority {expected.version!r}: "
            f"{actual!r} != {frozen!r}"
        )


def pqv2_code_manifest(repo_root: Path | str | None = None) -> tuple[tuple[str, str], ...]:
    """Return sorted ``(repo-relative posix path, sha256)`` pairs for ``pqv2_*.py``.

    The set is every ``smart_beta/dataset_v1/pqv2_*.py`` module present under
    ``repo_root``.  It grows as PQ2-B…H are implemented; the identity is defined
    over the files present at evaluation time.
    """

    root = _repo_root(repo_root)
    base = root / "smart_beta" / "dataset_v1"
    pairs = []
    for path in sorted(base.glob("pqv2_*.py"), key=lambda item: item.name):
        relative = path.relative_to(root).as_posix()
        pairs.append((relative, _sha256_file(path)))
    return tuple(pairs)


def pqv2_code_sha256(repo_root: Path | str | None = None) -> str:
    """SHA-256 over the sorted ``(path, sha256)`` pairs of the v2 module files."""

    payload = json.dumps(
        [[path, digest] for path, digest in pqv2_code_manifest(repo_root)],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return _sha256_bytes(payload.encode("utf-8"))


# ---------------------------------------------------------------------------
# Positive intervals and the S2 correction set (M§3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositiveInterval:
    """A closed positive interval ``[lo, hi]`` with ``0 < lo <= hi < inf``."""

    lo: float
    hi: float

    UNIT: ClassVar["PositiveInterval"]

    def __post_init__(self) -> None:
        try:
            lo = float(self.lo)
            hi = float(self.hi)
        except (TypeError, ValueError) as exc:
            raise PQv2InputError(
                "PositiveInterval bounds must be real numbers"
            ) from exc
        if not (math.isfinite(lo) and math.isfinite(hi)):
            raise PQv2InputError("PositiveInterval bounds must be finite")
        if not (0.0 < lo <= hi):
            raise PQv2InputError(
                f"PositiveInterval requires 0 < lo <= hi, got [{lo}, {hi}]"
            )
        object.__setattr__(self, "lo", lo)
        object.__setattr__(self, "hi", hi)

    def mul(self, other: "PositiveInterval") -> "PositiveInterval":
        return PositiveInterval(self.lo * other.lo, self.hi * other.hi)

    def inv(self) -> "PositiveInterval":
        return PositiveInterval(1.0 / self.hi, 1.0 / self.lo)

    def hull(self, *others: "PositiveInterval") -> "PositiveInterval":
        lo = self.lo
        hi = self.hi
        for other in others:
            lo = min(lo, other.lo)
            hi = max(hi, other.hi)
        return PositiveInterval(lo, hi)

    def contains(self, value: float) -> bool:
        """Closed-interval membership; a NaN value is never contained."""

        return bool(self.lo <= value <= self.hi)

    @property
    def is_unit(self) -> bool:
        return self.lo == 1.0 and self.hi == 1.0


#: The neutral multiplier interval ``[1, 1]``.
UNIT = PositiveInterval(1.0, 1.0)
PositiveInterval.UNIT = UNIT


def _finite_positive(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PQv2InputError(f"{name} must be a real number, got {value!r}") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise PQv2InputError(f"{name} must be finite and positive, got {value!r}")
    return number


def s2_absolute(m_obs: float) -> PositiveInterval:
    """M§3 absolute correction set ``[min(0.5, m), max(1.5, m)]``."""

    m = _finite_positive(m_obs, name="m_obs")
    return PositiveInterval(min(S2_LO, m), max(S2_HI, m))


def s2_relative(m_obs: float) -> PositiveInterval:
    """M§3 relative correction set ``Q_s = S2(m_obs) / m_obs``."""

    m = _finite_positive(m_obs, name="m_obs")
    return PositiveInterval(min(S2_LO, m) / m, max(S2_HI, m) / m)


# ---------------------------------------------------------------------------
# Typed status vocabularies (plan §6.1; M§6, M§14, M§15)
# ---------------------------------------------------------------------------


class LabelStatus:
    """M§6/M§8 label-envelope statuses."""

    ENVELOPE = "ENVELOPE"
    EMPTY_NO_DEPENDENCY = "EMPTY_NO_DEPENDENCY"
    FACTUAL_MISSING = "FACTUAL_MISSING"
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    UNKNOWN_RESERVE = "UNKNOWN_RESERVE"
    ALL: ClassVar[tuple[str, ...]] = (
        ENVELOPE,
        EMPTY_NO_DEPENDENCY,
        FACTUAL_MISSING,
        UNKNOWN_SOURCE,
        UNKNOWN_RESERVE,
    )


class NotAssessedReason:
    """The exhaustive M§13/plan §6.1 NOT_ASSESSED reason vocabulary."""

    # binding and scope
    IDENTITY_MISMATCH = "PQV2_IDENTITY_MISMATCH"
    DATASET_IDENTITY_MISMATCH = "PQV2_DATASET_IDENTITY_MISMATCH"
    OVERLAY_MISMATCH = "PQV2_OVERLAY_MISMATCH"
    EXPRESSION_NOT_CERTIFIED = "PQV2_EXPRESSION_NOT_CERTIFIED"
    SIGN_NOT_CERTIFIED = "PQV2_SIGN_NOT_CERTIFIED"
    METRIC_NOT_CERTIFIED = "PQV2_METRIC_NOT_CERTIFIED"
    # period preconditions
    RESERVE_UNKNOWN = "PQV2_RESERVE_UNKNOWN"
    SOURCE_UNRESOLVED = "PQV2_SOURCE_UNRESOLVED"
    SIGMA_SUPPORT = "PQV2_SIGMA_SUPPORT"
    WINSOR_BLOCK = "PQV2_WINSOR_BLOCK"
    EMPTY_LEG_CONFIGURATION = "PQV2_EMPTY_LEG_CONFIGURATION"
    # statistics
    L_PERIOD_NOT_ASSESSED = "PQV2_L_PERIOD_NOT_ASSESSED"
    B_STATISTIC_UNAVAILABLE = "PQV2_B_STATISTIC_UNAVAILABLE"
    L_STATISTIC_UNAVAILABLE = "PQV2_L_STATISTIC_UNAVAILABLE"
    ALL: ClassVar[tuple[str, ...]] = (
        IDENTITY_MISMATCH,
        DATASET_IDENTITY_MISMATCH,
        OVERLAY_MISMATCH,
        EXPRESSION_NOT_CERTIFIED,
        SIGN_NOT_CERTIFIED,
        METRIC_NOT_CERTIFIED,
        RESERVE_UNKNOWN,
        SOURCE_UNRESOLVED,
        SIGMA_SUPPORT,
        WINSOR_BLOCK,
        EMPTY_LEG_CONFIGURATION,
        L_PERIOD_NOT_ASSESSED,
        B_STATISTIC_UNAVAILABLE,
        L_STATISTIC_UNAVAILABLE,
    )


class AssignmentRule:
    """M§11 assignment rules."""

    SIMPLE_EXACT = "SIMPLE_EXACT"
    SANDWICH_CONSERVATIVE_SUPERSET = "SANDWICH_CONSERVATIVE_SUPERSET"
    ALL: ClassVar[tuple[str, ...]] = (SIMPLE_EXACT, SANDWICH_CONSERVATIVE_SUPERSET)


class ClassificationStatus:
    """The M§14 classification statuses (never an ACCEPT/confirmation)."""

    CORRECTION_SENSITIVITY_SCREEN_PASS = "CORRECTION_SENSITIVITY_SCREEN_PASS"
    QUARANTINE_SENSITIVE = "QUARANTINE_SENSITIVE"
    INVARIANT_VIOLATION_FAIL_CLOSED = "INVARIANT_VIOLATION_FAIL_CLOSED"
    DEVELOPMENT_SCREEN_FAIL = "DEVELOPMENT_SCREEN_FAIL"
    NOT_ASSESSED = "NOT_ASSESSED"
    ALL: ClassVar[tuple[str, ...]] = (
        CORRECTION_SENSITIVITY_SCREEN_PASS,
        QUARANTINE_SENSITIVE,
        INVARIANT_VIOLATION_FAIL_CLOSED,
        DEVELOPMENT_SCREEN_FAIL,
        NOT_ASSESSED,
    )


# Module-level aliases and tuples (plan §6.1: "string constants and tuples").
ENVELOPE = LabelStatus.ENVELOPE
EMPTY_NO_DEPENDENCY = LabelStatus.EMPTY_NO_DEPENDENCY
FACTUAL_MISSING = LabelStatus.FACTUAL_MISSING
UNKNOWN_RESERVE = LabelStatus.UNKNOWN_RESERVE
UNKNOWN_SOURCE = LabelStatus.UNKNOWN_SOURCE
LABEL_STATUSES = LabelStatus.ALL

PQV2_IDENTITY_MISMATCH = NotAssessedReason.IDENTITY_MISMATCH
PQV2_DATASET_IDENTITY_MISMATCH = NotAssessedReason.DATASET_IDENTITY_MISMATCH
PQV2_OVERLAY_MISMATCH = NotAssessedReason.OVERLAY_MISMATCH
PQV2_EXPRESSION_NOT_CERTIFIED = NotAssessedReason.EXPRESSION_NOT_CERTIFIED
PQV2_SIGN_NOT_CERTIFIED = NotAssessedReason.SIGN_NOT_CERTIFIED
PQV2_METRIC_NOT_CERTIFIED = NotAssessedReason.METRIC_NOT_CERTIFIED
PQV2_RESERVE_UNKNOWN = NotAssessedReason.RESERVE_UNKNOWN
PQV2_SOURCE_UNRESOLVED = NotAssessedReason.SOURCE_UNRESOLVED
PQV2_SIGMA_SUPPORT = NotAssessedReason.SIGMA_SUPPORT
PQV2_WINSOR_BLOCK = NotAssessedReason.WINSOR_BLOCK
PQV2_EMPTY_LEG_CONFIGURATION = NotAssessedReason.EMPTY_LEG_CONFIGURATION
PQV2_L_PERIOD_NOT_ASSESSED = NotAssessedReason.L_PERIOD_NOT_ASSESSED
PQV2_B_STATISTIC_UNAVAILABLE = NotAssessedReason.B_STATISTIC_UNAVAILABLE
PQV2_L_STATISTIC_UNAVAILABLE = NotAssessedReason.L_STATISTIC_UNAVAILABLE
NOT_ASSESSED_REASONS = NotAssessedReason.ALL

SIMPLE_EXACT = AssignmentRule.SIMPLE_EXACT
SANDWICH_CONSERVATIVE_SUPERSET = AssignmentRule.SANDWICH_CONSERVATIVE_SUPERSET
ASSIGNMENT_RULES = AssignmentRule.ALL

CORRECTION_SENSITIVITY_SCREEN_PASS = ClassificationStatus.CORRECTION_SENSITIVITY_SCREEN_PASS
QUARANTINE_SENSITIVE = ClassificationStatus.QUARANTINE_SENSITIVE
INVARIANT_VIOLATION_FAIL_CLOSED = ClassificationStatus.INVARIANT_VIOLATION_FAIL_CLOSED
DEVELOPMENT_SCREEN_FAIL = ClassificationStatus.DEVELOPMENT_SCREEN_FAIL
NOT_ASSESSED = ClassificationStatus.NOT_ASSESSED
CLASSIFICATION_STATUSES = ClassificationStatus.ALL


# ---------------------------------------------------------------------------
# Source state and the period cause model (M§8A.1, M§8A.4)
# --------------------------------------------------------------------------

#: M§8A.4 period cause precedence, **highest priority first**.  Whole-evaluation
#: gates and statistic-level reasons are deliberately absent: they are not period
#: causes.
PERIOD_CAUSE_PRECEDENCE: tuple[str, ...] = (
    NotAssessedReason.RESERVE_UNKNOWN,
    NotAssessedReason.SOURCE_UNRESOLVED,
    NotAssessedReason.SIGMA_SUPPORT,
    NotAssessedReason.WINSOR_BLOCK,
    NotAssessedReason.EMPTY_LEG_CONFIGURATION,
)

#: The two UNKNOWN causes; a cell carrying either must short-circuit before any
#: resolved-state precondition (M§8A.4).
UNKNOWN_CAUSES: tuple[str, ...] = PERIOD_CAUSE_PRECEDENCE[:2]


def _canonical_period_causes(reasons: Iterable[str]) -> tuple[str, ...]:
    """Validate and canonicalize a period-cause collection (M§8A.4).

    Every element must be a member of :data:`PERIOD_CAUSE_PRECEDENCE`; the
    result is the sorted (canonical-string) tuple of the distinct reasons.
    """

    materialized = tuple(reasons)
    for reason in materialized:
        if reason not in PERIOD_CAUSE_PRECEDENCE:
            raise PQv2InputError(f"not a period cause: {reason!r}")
    return tuple(sorted(set(materialized)))


@dataclass(frozen=True)
class CauseSet:
    """The distinct period causes attached to a cell (M§8A.4).

    ``causes`` is always the canonical (ascending lexicographic) distinct tuple;
    construction from any order yields the identical value.  ``primary`` is a
    presentation/disposition field only and never replaces ``to_list()``.
    """

    causes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        canonical = _canonical_period_causes(self.causes)
        object.__setattr__(self, "causes", canonical)

    @classmethod
    def of(cls, *reasons: str) -> "CauseSet":
        """Build from reasons in any order; duplicates collapse."""

        return cls(tuple(reasons))

    @classmethod
    def from_iterable(cls, reasons: Iterable[str]) -> "CauseSet":
        """Build from an iterable of reasons in any order; duplicates collapse."""

        return cls(tuple(reasons))

    @property
    def is_empty(self) -> bool:
        return not self.causes

    @property
    def has_unknown(self) -> bool:
        return any(reason in self.causes for reason in UNKNOWN_CAUSES)

    @property
    def primary(self) -> str | None:
        """The highest-precedence cause, or ``None`` when empty."""

        for reason in PERIOD_CAUSE_PRECEDENCE:
            if reason in self.causes:
                return reason
        return None

    def union(self, other: "CauseSet") -> "CauseSet":
        return CauseSet(self.causes + tuple(other.causes))

    def to_list(self) -> list[str]:
        """The canonical-order list; the full persisted representation."""

        return list(self.causes)

    def __iter__(self):
        return iter(self.causes)

    def __len__(self) -> int:
        return len(self.causes)

    def __contains__(self, reason: object) -> bool:
        return reason in self.causes


#: The empty cause set: no cause, i.e. assessable as far as causes go.
EMPTY_CAUSES = CauseSet(())


def must_short_circuit(cause_set: CauseSet) -> bool:
    """True iff *cause_set* carries an UNKNOWN cause (M§8A.4).

    Downstream layers must return NOT_ASSESSED **before** any resolved-state
    precondition when this is True.
    """

    return cause_set.has_unknown


class SourceState:
    """Per-flag source resolvability (M§8A.1)."""

    RESOLVED = "RESOLVED"
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    UNKNOWN_RESERVE = "UNKNOWN_RESERVE"
    ALL: ClassVar[tuple[str, ...]] = (RESOLVED, UNKNOWN_SOURCE, UNKNOWN_RESERVE)
    UNKNOWN: ClassVar[tuple[str, ...]] = (UNKNOWN_SOURCE, UNKNOWN_RESERVE)


def cause_for_source_state(state: str) -> str:
    """Map an UNKNOWN source state to its durable period cause (M§8A.1).

    ``RESOLVED`` (and any other value) has no cause and is rejected.
    """

    if state == SourceState.UNKNOWN_SOURCE:
        return NotAssessedReason.SOURCE_UNRESOLVED
    if state == SourceState.UNKNOWN_RESERVE:
        return NotAssessedReason.RESERVE_UNKNOWN
    raise PQv2InputError(f"source state has no period cause: {state!r}")


def label_status_for_causes(cause_set: CauseSet) -> str:
    """The M§8A.4 label status for a non-empty cause set with an UNKNOWN cause."""

    if not isinstance(cause_set, CauseSet):
        raise PQv2InputError("cause_set must be a CauseSet")
    if cause_set.is_empty or not cause_set.has_unknown:
        raise PQv2InputError(
            "a label status requires a non-empty cause set with an UNKNOWN cause"
        )
    if NotAssessedReason.RESERVE_UNKNOWN in cause_set:
        return LabelStatus.UNKNOWN_RESERVE
    return LabelStatus.UNKNOWN_SOURCE


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NotAssessed:
    """A typed NOT_ASSESSED outcome.

    ``causes`` is optional for backward compatibility: ``NotAssessed(reason,
    detail)`` keeps working exactly as before.  When ``causes`` is supplied it
    must be a non-empty :class:`CauseSet` whose ``primary`` equals ``reason``.
    """

    reason: str
    detail: Any = None
    causes: CauseSet | None = None

    def __post_init__(self) -> None:
        if self.causes is None:
            return
        if not isinstance(self.causes, CauseSet):
            raise PQv2InvariantError("causes must be a CauseSet or None")
        if self.reason != self.causes.primary:
            raise PQv2InvariantError(
                f"reason {self.reason!r} != cause-set primary "
                f"{self.causes.primary!r}"
            )

    @classmethod
    def from_causes(
        cls, cause_set: CauseSet, detail: Any = None
    ) -> "NotAssessed":
        """Build from a non-empty :class:`CauseSet`, primary as ``reason``."""

        if not isinstance(cause_set, CauseSet) or cause_set.is_empty:
            raise PQv2InputError("from_causes requires a non-empty CauseSet")
        return cls(cause_set.primary, detail, cause_set)

    @property
    def cause_set(self) -> CauseSet | None:
        """The cause set: explicit ``causes``, else one derived from ``reason``."""

        if self.causes is not None:
            return self.causes
        if self.reason in PERIOD_CAUSE_PRECEDENCE:
            return CauseSet.of(self.reason)
        return None


@dataclass(frozen=True)
class UnresolvedFlag:
    """A D5 flag whose source state is UNKNOWN (M§8A.1, M§8A.2).

    It deliberately carries no ``m_obs`` and no correction set, so an unresolved
    flag can never carry a numerical correction.
    """

    security_key: str
    s: Any
    prev_s: Any
    state: str

    def __post_init__(self) -> None:
        if self.state not in SourceState.UNKNOWN:
            raise PQv2InputError(
                f"UnresolvedFlag requires an UNKNOWN state, got {self.state!r}"
            )

    @property
    def cause(self) -> str:
        return cause_for_source_state(self.state)


@dataclass(frozen=True)
class UncertainTransition:
    """A trusted flagged transition and its relative correction interval (M§2/M§3)."""

    security_key: str
    s: Any
    prev_s: Any
    next_s: Any
    m_obs: float
    rel: PositiveInterval


@dataclass(frozen=True)
class MisdatedCandidate:
    """A recorded split/dividend eligible as a MISDATED alternative (M§7)."""

    security_key: str
    s: Any
    d: Any
    phi_d: float


def _freeze_sorted(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    ordered: dict[str, Any] = {}
    for key in sorted(mapping):
        value = mapping[key]
        if isinstance(value, list):
            value = tuple(value)
        ordered[key] = value
    return MappingProxyType(ordered)


@dataclass(frozen=True)
class SourceSet:
    """The deterministic source set produced by PQ2-B and consumed downstream."""

    transitions_by_key: Mapping[str, tuple[UncertainTransition, ...]] = field(
        default_factory=dict
    )
    candidates_by_key: Mapping[str, tuple[MisdatedCandidate, ...]] = field(
        default_factory=dict
    )
    unknown_by_key: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "transitions_by_key", _freeze_sorted(self.transitions_by_key)
        )
        object.__setattr__(
            self, "candidates_by_key", _freeze_sorted(self.candidates_by_key)
        )
        object.__setattr__(
            self, "unknown_by_key", _freeze_sorted(self.unknown_by_key)
        )


# ---------------------------------------------------------------------------
# Leg-extremum combinatorics (M§12 Prop. 7)
# ---------------------------------------------------------------------------


def _float_sequence(values: Iterable[Any], *, name: str) -> list[float]:
    out: list[float] = []
    for value in values:
        try:
            out.append(float(value))
        except (TypeError, ValueError) as exc:
            raise PQv2InputError(
                f"{name} must be a sequence of real numbers, got {value!r}"
            ) from exc
    return out


def _leg_extremum(
    forced: Sequence[float],
    pool: Sequence[float],
    n_take: int,
    *,
    take_max: bool,
) -> float | NotAssessed:
    """Exact extremum of the mean over every admissible ``chosen`` subset.

    ``chosen`` is any subset of ``pool`` of size ``n_take``; NaN members are
    admissible and are excluded from both the numerator and the denominator by
    the frozen NaN-denominator rule.  If *any* admissible subset has no finite
    value the whole configuration is :class:`NotAssessed` (M§13 empty-leg
    precondition); otherwise the extremum is exact because the denominator
    ``|finite(forced)| + a`` depends only on the number ``a`` of finite pool
    values chosen, so a min takes the ``a`` lowest finite pool values and a max
    the ``a`` highest.
    """

    forced_values = _float_sequence(forced, name="forced")
    pool_values = _float_sequence(pool, name="pool")

    if isinstance(n_take, bool) or not isinstance(n_take, int):
        raise PQv2InputError(f"n_take must be an integer, got {n_take!r}")
    if n_take < 0 or n_take > len(pool_values):
        raise PQv2InputError(
            f"n_take={n_take} is outside [0, {len(pool_values)}]"
        )

    forced_finite = [value for value in forced_values if math.isfinite(value)]
    forced_sum = math.fsum(forced_finite)
    fixed = len(forced_finite)

    pool_finite = sorted(value for value in pool_values if math.isfinite(value))
    nan_pool = len(pool_values) - len(pool_finite)

    upper_a = min(n_take, len(pool_finite))
    lower_a = max(0, n_take - nan_pool)

    best: float | None = None
    for chosen_finite in range(lower_a, upper_a + 1):
        if fixed + chosen_finite == 0:
            return NotAssessed(
                NotAssessedReason.EMPTY_LEG_CONFIGURATION,
                "an admissible leg choice has no finite value",
            )
        if chosen_finite == 0:
            chosen_sum = 0.0
        elif take_max:
            chosen_sum = math.fsum(pool_finite[len(pool_finite) - chosen_finite :])
        else:
            chosen_sum = math.fsum(pool_finite[:chosen_finite])
        mean = (forced_sum + chosen_sum) / (fixed + chosen_finite)
        if best is None:
            best = mean
        elif take_max:
            best = max(best, mean)
        else:
            best = min(best, mean)

    if best is None:
        # Only reachable if the admissible range is empty, which the n_take
        # bound above rules out; fail closed rather than return a value.
        return NotAssessed(
            NotAssessedReason.EMPTY_LEG_CONFIGURATION,
            "no admissible leg choice",
        )
    return best


def leg_min_mean(
    forced: Sequence[float], pool: Sequence[float], n_take: int
) -> float | NotAssessed:
    """The lowest admissible leg mean (M§12 Prop. 7, long side)."""

    return _leg_extremum(forced, pool, n_take, take_max=False)


def leg_max_mean(
    forced: Sequence[float], pool: Sequence[float], n_take: int
) -> float | NotAssessed:
    """The highest admissible leg mean (M§12 Prop. 7, short side)."""

    return _leg_extremum(forced, pool, n_take, take_max=True)


# ---------------------------------------------------------------------------
# Winsorization block check (M§11)
# ---------------------------------------------------------------------------


def winsor_block_ok(
    lo: Sequence[float],
    hi: Sequence[float],
    q: float,
    s_low: int,
    s_high: int,
) -> bool:
    """M§11 deterministic sufficient winsorization-block condition.

    ``lo``/``hi`` are per-member lower/upper certified ranking-score bounds
    (``±inf`` allowed).  With ``h = q*(n-1)``:

    * ``kappa_lo`` is the ``(floor(h)+2)``-th smallest ``hi``;
    * ``kappa_hi`` is the ``(floor(h)+2)``-th largest ``lo``;

    and the check passes iff ``#{lo <= kappa_lo} <= s_low`` and
    ``#{hi >= kappa_hi} <= s_high``.  If ``n < floor(h)+2`` it returns False.
    """

    lower = _float_sequence(lo, name="lo")
    upper = _float_sequence(hi, name="hi")
    if len(lower) != len(upper):
        raise PQv2InputError("lo and hi must have equal length")

    if isinstance(s_low, bool) or not isinstance(s_low, int):
        raise PQv2InputError(f"s_low must be an integer, got {s_low!r}")
    if isinstance(s_high, bool) or not isinstance(s_high, int):
        raise PQv2InputError(f"s_high must be an integer, got {s_high!r}")
    if s_low < 0 or s_high < 0:
        raise PQv2InputError("bucket sizes must be non-negative")

    n = len(lower)
    h = float(q) * (n - 1)
    position = math.floor(h) + 2
    if n < position:
        return False

    kappa_lo = sorted(upper)[position - 1]
    kappa_hi = sorted(lower, reverse=True)[position - 1]

    count_low = sum(1 for value in lower if value <= kappa_lo)
    count_high = sum(1 for value in upper if value >= kappa_hi)
    return count_low <= s_low and count_high <= s_high


# ---------------------------------------------------------------------------
# Bucket sizes (M§2 grouping; cross-checked against assign_groups)
# ---------------------------------------------------------------------------


def bucket_sizes(n: int, G: int) -> tuple[int, ...]:
    """Bucket sizes implied by ``assign_groups`` over ranks ``1..n``.

    ``bucket(rank) = floor((rank-1)*G/n) + 1`` (capped at ``G``), so the sizes
    are a function of ``n`` and ``G`` alone.
    """

    if isinstance(n, bool) or not isinstance(n, int):
        raise PQv2InputError(f"n must be an integer, got {n!r}")
    if isinstance(G, bool) or not isinstance(G, int):
        raise PQv2InputError(f"G must be an integer, got {G!r}")
    if n < 0:
        raise PQv2InputError(f"n must be non-negative, got {n!r}")
    if G < 1:
        raise PQv2InputError(f"G must be >= 1, got {G!r}")
    if n == 0:
        return tuple(0 for _ in range(G))

    sizes = [0] * G
    for rank in range(1, n + 1):
        bucket = ((rank - 1) * G) // n + 1
        bucket = min(bucket, G)
        sizes[bucket - 1] += 1
    return tuple(sizes)
