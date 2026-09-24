"""Pilot 1A P1A-G5: the governed end-to-end runner.

This module owns the *runner* half of the P1A-G5 task
(``worker_tasks/pilot1/pilot1-plan.md`` section 15 plus the binding
integration requirements (a)-(g) of section 26a). It composes the merged
Pilot-1A adapter modules with the sealed Phase 6-9 authorities and drives the
sealed :class:`~smart_beta.research.loop.ResearchLoop` over fresh, in-memory
authorities until a typed STOP.

Frozen properties
-----------------

* **Provider-neutral / stub-only.** Through H6 the only model client is the
  deterministic :class:`~smart_beta.pilot.model.StubModelClient`; there is no
  provider SDK, no model credential and no external model network access. The
  runner additionally installs the urllib/socket tripwire in its own process.
* **One shared journal chain (requirement a).** A single G3
  :class:`~smart_beta.pilot.model.JournalChain`, seeded from the G4
  :class:`~smart_beta.pilot.journal.Journal` ``next_seq`` / ``prev_sha256``,
  carries every runner and adapter append.
* **Pre-call authority snapshots (requirement b).** Before every ``generate``
  the runner journals the ``visible_history`` and ``research_feedback``
  authority snapshots so the G6 post-hoc audit can re-derive the request and
  check it against the write-ahead ``request_artifact_hash``.
* **Firewall stop (requirement c).** A G3 ``FirewallViolation`` escaping
  ``generate`` is mapped to the typed ``HOLDOUT_FIREWALL_VIOLATION`` stop.
* **Pre-call temporal firewall (section 26b TF-6).** Before **every** generate
  the runner runs the G6R ``enforce_temporal_firewall`` over the exact
  ``GeneratorVisibleResearchHistory`` / ``ResearchFeedback`` projection about to
  be handed to the generator and all evaluation records so far; a
  ``TemporalFirewallViolation`` becomes a typed
  ``HOLDOUT_FIREWALL_VIOLATION`` stop with no model call.
* **Immutable runs.** A run whose ``run_id`` or artifact directory already
  exists is refused before any model call, so a prior run (for example the
  preserved ``pilot1a-dryrun-v1`` package) can never be reused or overwritten.
* **Wired reconstruction (requirement d).** The G4
  :class:`~smart_beta.pilot.reconstruct.ReconstructionHooks` are both wired:
  evaluation is re-derived through G1+G2 over the journaled FactorSpecs and the
  decision is replayed through a fresh sealed ``Orchestrator``.
* **Crash / retry policy (section 17).** A crash, an infrastructure exception
  or a harness ceiling hit marks the run ``INTERRUPTED`` (never resumed). A
  retry is a new ``run_id`` with a ``predecessor_run_id``, permitted only when
  the predecessor registered no experiment, and at most once.

The runner never re-implements sealed authority, never modifies a protected
path and never retries a model call inside a run.
"""

from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smart_beta.evaluation.spec import EvaluationRecord
from smart_beta.experiment.orchestrator import Orchestrator
from smart_beta.experiment.policy import DecisionRecord
from smart_beta.pilot.artifacts import (
    ArtifactPackage,
    CREDENTIAL_ENV_VARS,
    assemble_package,
)
from smart_beta.pilot.config import (
    ANTHROPIC_PROVIDER,
    DEEPSEEK_ENDPOINT,
    DEEPSEEK_MAX_OUTPUT_TOKENS,
    DEEPSEEK_MODEL_ID,
    DEEPSEEK_PROVIDER,
    DEEPSEEK_REASONING_EFFORT,
    DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE,
    REAL_ENDPOINT,
    REAL_MAX_OUTPUT_TOKENS,
    REAL_MODEL_EFFORT,
    ConfigError,
    ResolvedConfig,
    resolve_config,
)
from smart_beta.pilot.contracts import (
    ArtifactLayout,
    JournalKind,
    JournalRecord,
    ModelResponse,
    PilotConfig,
    RunStatus,
    canonical_json,
    content_hash,
)
from smart_beta.pilot.data import PilotData
from smart_beta.pilot.design import (
    ProposalDesign,
    build_design,
    build_holdout_identity,
    build_partition,
    resolve_factor_spec,
)
from smart_beta.pilot.firewall import FirewallViolation
from smart_beta.pilot.journal import (
    Journal,
    authority_snapshot_payload,
    proposal_registered_payload,
    read_journal,
)
from smart_beta.pilot.model import (
    JournalChain,
    ModelAdapter,
    StubModelClient,
)
from smart_beta.pilot.prompt import OUTPUT_JSON_SCHEMA, render_request
from smart_beta.pilot.reconstruct import (
    DerivationResult,
    DerivationStatus,
    ReDerivationRequest,
    ReconstructionHooks,
    ReconstructionReport,
    ReconstructionStatus,
    reconstruct,
)
from smart_beta.pilot.temporal import (
    TemporalFirewallViolation,
    enforce_temporal_firewall,
)
from smart_beta.research.generator import GenerationEvent, NormalizationOutcome
from smart_beta.research.history import FullResearchHistory
from smart_beta.research.loop import ResearchLoop, StopRecord
from smart_beta.research.policy import StopReason
from smart_beta.research.proposal import ProposalEntry, ResearchProposal

__all__ = [
    "RunnerError",
    "PreflightError",
    "PreflightCheck",
    "PreflightReport",
    "preflight_check",
    "GitProbe",
    "RunOutcome",
    "run_pilot",
    "install_network_tripwire",
    "restore_network_tripwire",
    "scrub_credentials",
    "build_stub_client",
    "build_anthropic_client",
    "build_deepseek_client",
    "build_provider_client",
    "build_reconstruction_hooks",
    "count_raw_candidates",
    "provider_credential_env",
    "provider_refused_env_vars",
    "DEEPSEEK_CREDENTIAL_ENV",
    "PROVIDER_SDK_SPECS",
    "PROVIDER_SDK_DISTRIBUTION",
    "PROVIDER_SDK_MIN_MAJOR",
    "PROVIDER_SDK_MAX_MAJOR_EXCLUSIVE",
]

#: The optional provider SDK distribution and its frozen version range
#: (``pilot-anthropic = ["anthropic>=1,<2"]`` in ``pyproject.toml``).
PROVIDER_SDK_DISTRIBUTION = "anthropic"
PROVIDER_SDK_MIN_MAJOR = 1
PROVIDER_SDK_MAX_MAJOR_EXCLUSIVE = 2

#: Per-provider SDK distribution and frozen major-version range
#: (``pilot-anthropic = ["anthropic>=1,<2"]`` and
#: ``pilot-deepseek = ["openai>=3,<4"]`` in ``pyproject.toml``).
PROVIDER_SDK_SPECS: Mapping[str, tuple[str, int, int]] = {
    ANTHROPIC_PROVIDER: ("anthropic", 1, 2),
    DEEPSEEK_PROVIDER: ("openai", 3, 4),
}


def _provider_sdk_spec(provider: str) -> tuple[str, int, int]:
    """The frozen ``(distribution, min_major, max_major_exclusive)`` spec."""
    try:
        return PROVIDER_SDK_SPECS[provider]
    except KeyError as exc:
        raise _PreflightFailure(
            "provider_sdk",
            f"no frozen SDK range is declared for provider {provider!r}",
        ) from exc


def _provider_sdk_major(version: str) -> int | None:
    """Return the leading major component of a version string, or ``None``.

    A tiny local parse so no packaging dependency is added; the frozen range
    is a major-version range, so only the leading integer is needed (a
    pre-release/build suffix such as ``1.0.0b1`` still has major ``1``).
    """
    if not isinstance(version, str):
        return None
    text = version.strip().lstrip("vV")
    major_chars: list[str] = []
    for char in text:
        if char.isdigit():
            major_chars.append(char)
        else:
            break
    if not major_chars:
        return None
    return int("".join(major_chars))


def _provider_sdk_version_in_range(
    version: str, spec: tuple[str, int, int]
) -> bool:
    """Whether ``version`` satisfies the frozen per-provider range."""
    _, min_major, max_major_exclusive = spec
    major = _provider_sdk_major(version)
    return major is not None and min_major <= major < max_major_exclusive


def _require_provider_sdk(provider: str) -> str:
    """Return the installed provider SDK version, or fail closed.

    Uses ``importlib.util.find_spec`` and ``importlib.metadata.version``
    only: it never imports the SDK (and therefore never touches its network
    stack) and never constructs a provider client. The SDK is optional and a
    real run must fail closed before any side effect if it is absent or out of
    the frozen range. The distribution and range are per provider: Anthropic
    ``anthropic>=1,<2``, DeepSeek ``openai>=3,<4``.
    """
    import importlib.metadata
    import importlib.util

    distribution, min_major, max_major_exclusive = _provider_sdk_spec(provider)
    extra = "pilot-anthropic" if provider == ANTHROPIC_PROVIDER else "pilot-deepseek"
    if importlib.util.find_spec(distribution) is None:
        raise _PreflightFailure(
            "provider_sdk",
            f"the {distribution!r} SDK is not installed; install "
            f"the {extra!r} optional extra before a real run",
        )
    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError as exc:
        raise _PreflightFailure(
            "provider_sdk",
            f"the {distribution!r} SDK distribution metadata is "
            "not available; the optional extra is not installed",
        ) from exc
    if not _provider_sdk_version_in_range(version, (distribution, min_major, max_major_exclusive)):
        raise _PreflightFailure(
            "provider_sdk",
            f"the installed {distribution!r} version {version!r} "
            f"does not satisfy the frozen range >={min_major},"
            f"<{max_major_exclusive}",
        )
    return version


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class RunnerError(ValueError):
    """Base class for every Pilot-1A runner failure."""


class PreflightError(RunnerError):
    """A preflight check failed closed before any model call.

    ``report`` carries the partial :class:`PreflightReport` (every check that
    ran plus its pass/fail detail) when the failure happened after config
    resolution; it is ``None`` when the config itself could not be resolved.
    """

    def __init__(self, message: str, *, report: "PreflightReport | None" = None) -> None:
        super().__init__(message)
        self.report = report


# ---------------------------------------------------------------------------
# network tripwire / credential scrub
# ---------------------------------------------------------------------------


def _refuse_network(*args: object, **kwargs: object) -> None:
    raise RunnerError(
        "the Pilot-1A runner forbids outbound network access; a live "
        "urllib/socket call was attempted"
    )


def parse_endpoint(endpoint: str) -> tuple[str, int]:
    """Return the frozen ``(host, port)`` of the configured provider endpoint."""
    parsed = urllib.parse.urlsplit(endpoint)
    host = parsed.hostname
    if not host:
        raise RunnerError(f"provider endpoint {endpoint!r} has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, int(port)


def endpoint_addresses(host: str, port: int) -> tuple[str, ...]:
    """Resolve the frozen endpoint to its current socket addresses."""
    addresses: set[str] = set()
    for info in socket.getaddrinfo(host, port):
        sockaddr = info[4]
        if sockaddr:
            addresses.add(str(sockaddr[0]))
    return tuple(sorted(addresses))


def _address_allowed(
    host: str,
    port: int,
    endpoint: tuple[str, int],
    resolved_addresses: Sequence[str],
) -> bool:
    """Whether one ``(host, port)`` is the pinned provider endpoint.

    Only the frozen endpoint port is ever allowed, and only for the endpoint
    hostname or one of its preflight-resolved addresses. A redirect to another
    host, a proxy, or a data-provider host is never allowed.
    """
    if int(port) != int(endpoint[1]):
        return False
    return host == endpoint[0] or host in set(resolved_addresses)


def install_network_tripwire(
    *,
    allowed_endpoint: tuple[str, int] | None = None,
    resolved_addresses: Sequence[str] = (),
) -> None:
    """Install the urllib/socket guard in the runner process.

    With ``allowed_endpoint=None`` the total dry-run/stub tripwire is installed
    (every outbound URL fetch and socket connect raises). In real mode the
    caller passes the pinned provider endpoint and its preflight-resolved
    addresses, so only ``socket.connect``/``create_connection`` to that exact
    endpoint and port are delegated; everything else raises. ``urllib`` is
    always blocked.

    Mirrors the shared test guard (``tests/pilot_support.py``). This is
    installed before any adapter or data work runs.
    """
    global _ORIGINAL_NETWORK_FUNCS
    if _ORIGINAL_NETWORK_FUNCS is None:
        _ORIGINAL_NETWORK_FUNCS = (
            urllib.request.urlopen,
            socket.socket.connect,
            socket.create_connection,
        )
    original_connect = _ORIGINAL_NETWORK_FUNCS[1]
    original_create = _ORIGINAL_NETWORK_FUNCS[2]

    def _guard_connect(self: Any, address: Any) -> Any:
        host, port = address[0], address[1]
        if allowed_endpoint is not None and _address_allowed(
            str(host), int(port), allowed_endpoint, resolved_addresses
        ):
            return original_connect(self, address)
        raise RunnerError(
            "the Pilot-1A real-run network guard allows only the pinned provider "
            f"endpoint {allowed_endpoint!r}; refused {address!r}"
        )

    def _guard_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        host, port = address[0], address[1]
        if allowed_endpoint is not None and _address_allowed(
            str(host), int(port), allowed_endpoint, resolved_addresses
        ):
            return original_create(address, *args, **kwargs)
        raise RunnerError(
            "the Pilot-1A real-run network guard allows only the pinned provider "
            f"endpoint {allowed_endpoint!r}; refused {address!r}"
        )

    urllib.request.urlopen = _refuse_network  # type: ignore[assignment]
    socket.socket.connect = _guard_connect  # type: ignore[assignment]
    socket.create_connection = _guard_create_connection  # type: ignore[assignment]


def restore_network_tripwire() -> None:
    """Restore the functions :func:`install_network_tripwire` replaced."""
    global _ORIGINAL_NETWORK_FUNCS
    if _ORIGINAL_NETWORK_FUNCS is None:
        return
    (
        urllib.request.urlopen,  # type: ignore[assignment]
        socket.socket.connect,  # type: ignore[assignment]
        socket.create_connection,  # type: ignore[assignment]
    ) = _ORIGINAL_NETWORK_FUNCS
    _ORIGINAL_NETWORK_FUNCS = None


#: Saved originals for :func:`restore_network_tripwire`.
_ORIGINAL_NETWORK_FUNCS: tuple[Any, Any, Any] | None = None


#: The data-provider credentials always scrubbed before a run.
DATA_CREDENTIAL_ENV_VARS: tuple[str, ...] = (
    "TIINGO_API_KEY",
    "TUSHARE_PROXY_TOKEN",
    "TUSHARE_BASIC_PROXY_TOKEN",
    "TUSHARE_API_TOKEN",
)


def scrub_credentials(
    *, model_credentials: bool = True, environ: dict[str, str] | None = None
) -> tuple[str, ...]:
    """Remove provider credentials from the environment.

    Data-provider credentials are always removed. Model credentials are
    removed too through H6 (the stub needs none); ``model_credentials=False``
    is reserved for the deferred post-H6 real run. Returns the names removed.
    No value is ever read, stored or printed.
    """
    target = environ if environ is not None else os.environ
    names = list(DATA_CREDENTIAL_ENV_VARS)
    if model_credentials:
        names.extend(name for name in CREDENTIAL_ENV_VARS if name not in names)
    removed: list[str] = []
    for name in names:
        if name in target:
            del target[name]
            removed.append(name)
    return tuple(removed)


#: The frozen real-run model credential environment variables (runtime only),
#: per provider. The Anthropic values are unchanged by section 26f.
MODEL_CREDENTIAL_ENV = "ANTHROPIC_API_KEY"
MODEL_AUTH_TOKEN_ENV = "ANTHROPIC_AUTH_TOKEN"
DEEPSEEK_CREDENTIAL_ENV = "DEEPSEEK_API_KEY"

#: Per-provider credential/auth-token env names.
PROVIDER_CREDENTIAL_ENVS: Mapping[str, str] = {
    ANTHROPIC_PROVIDER: MODEL_CREDENTIAL_ENV,
    DEEPSEEK_PROVIDER: DEEPSEEK_CREDENTIAL_ENV,
}
PROVIDER_AUTH_TOKEN_ENVS: Mapping[str, tuple[str, ...]] = {
    ANTHROPIC_PROVIDER: (MODEL_AUTH_TOKEN_ENV,),
    DEEPSEEK_PROVIDER: (),
}

#: Environment variables that would silently change the provider endpoint or
#: route traffic through a proxy; their presence refuses a real run.
BASE_URL_OR_PROXY_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_BASE_URL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)

#: The DeepSeek real run additionally refuses ambient OpenAI routing variables
#: (an ``OPENAI_API_KEY``/``OPENAI_BASE_URL`` override could silently redirect
#: or authenticate the OpenAI-compatible SDK) as well as the Anthropic base URL
#: and every proxy (plan section 26f).
DEEPSEEK_REFUSED_ENV_VARS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    *BASE_URL_OR_PROXY_ENV_VARS,
)

_PROVIDER_REFUSED_ENV_VARS: Mapping[str, tuple[str, ...]] = {
    ANTHROPIC_PROVIDER: BASE_URL_OR_PROXY_ENV_VARS,
    DEEPSEEK_PROVIDER: DEEPSEEK_REFUSED_ENV_VARS,
}


def provider_credential_env(provider: str) -> str:
    """The credential environment variable for one concrete provider."""
    try:
        return PROVIDER_CREDENTIAL_ENVS[provider]
    except KeyError as exc:
        raise RunnerError(
            f"no frozen credential environment variable for provider {provider!r}"
        ) from exc


def provider_refused_env_vars(provider: str) -> tuple[str, ...]:
    """The ambient base-URL/proxy variables refused for one provider."""
    try:
        return _PROVIDER_REFUSED_ENV_VARS[provider]
    except KeyError as exc:
        raise RunnerError(
            f"no frozen ambient-refusal set for provider {provider!r}"
        ) from exc


def capture_model_credential(
    *, provider: str = ANTHROPIC_PROVIDER, environ: dict[str, str] | None = None
) -> str:
    """Capture and immediately delete the model credential (real mode).

    Refuses a base-URL or proxy override (per provider), removes the alternate
    auth token(s), requires the provider's primary credential to be present
    and non-empty, deletes it from the environment **before any subprocess**
    (so a later ``git`` cannot inherit it), and returns the value for the
    in-process provider client and the package value sweep. The value is never
    logged, printed or journaled.
    """
    credential_env = provider_credential_env(provider)
    target = environ if environ is not None else os.environ
    for name in provider_refused_env_vars(provider):
        if target.get(name):
            raise PreflightError(
                f"{name} is set; a base-URL or proxy override would change the "
                "frozen provider endpoint and is refused"
            )
    for name in PROVIDER_AUTH_TOKEN_ENVS[provider]:
        target.pop(name, None)
    value = target.get(credential_env)
    if not value:
        raise PreflightError(
            f"real mode requires a non-empty {credential_env} in the "
            "environment; refusing before any call"
        )
    del target[credential_env]
    for name in DATA_CREDENTIAL_ENV_VARS:
        target.pop(name, None)
    return value


# ---------------------------------------------------------------------------
# git probe
# ---------------------------------------------------------------------------


class GitProbe:
    """Read-only git checks used by preflight.

    Tests inject a deterministic fake; the default implementation shells out to
    ``git`` in the repository root.
    """

    def __init__(self, repo_root: str | Path) -> None:
        self._root = Path(repo_root)

    def _subprocess_env(self) -> dict[str, str]:
        """The environment for a git subprocess, with every credential removed.

        Defense in depth: even when the runner still holds the model credential
        in its own process environment (preflight runs before the side-effecting
        capture), no ``git`` subprocess can ever inherit it.
        """
        env = dict(os.environ)
        for name in (
            MODEL_CREDENTIAL_ENV,
            MODEL_AUTH_TOKEN_ENV,
            DEEPSEEK_CREDENTIAL_ENV,
            "OPENAI_API_KEY",
            *DATA_CREDENTIAL_ENV_VARS,
        ):
            env.pop(name, None)
        return env

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self._root), *args],
            capture_output=True,
            text=True,
            env=self._subprocess_env(),
        )
        if result.returncode != 0:
            raise PreflightError(
                f"git {' '.join(args)} failed: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def head_commit(self) -> str:
        return self._git("rev-parse", "HEAD")

    def resolve_commit(self, ref: str) -> str:
        return self._git("rev-parse", f"{ref}^{{commit}}")

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(self._root),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            capture_output=True,
            text=True,
            env=self._subprocess_env(),
        )
        return result.returncode == 0

    def tree_id(self, path: str) -> str:
        return self._git("rev-parse", f"HEAD:{path}")

    def is_clean(self) -> bool:
        return self._git("status", "--porcelain") == ""

    def status_porcelain(self) -> str:
        """The raw ``git status --porcelain`` output (one line per path)."""
        return self._git("status", "--porcelain")


# ---------------------------------------------------------------------------
# run outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunOutcome:
    """The frozen disposition of one runner invocation."""

    run_id: str
    status: RunStatus
    detail: str
    journal_path: Path | None = None
    artifact_directory: Path | None = None
    stop_reason: str | None = None
    invocation_count: int = 0
    reconstruction_status: str | None = None
    firewall_audit_status: str | None = None
    temporal_firewall_status: str | None = None
    secret_sweep_status: str | None = None
    package_error: str | None = None
    package: ArtifactPackage | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "detail": self.detail,
            "journal_path": (
                None if self.journal_path is None else str(self.journal_path)
            ),
            "artifact_directory": (
                None
                if self.artifact_directory is None
                else str(self.artifact_directory)
            ),
            "stop_reason": self.stop_reason,
            "invocation_count": self.invocation_count,
            "reconstruction_status": self.reconstruction_status,
            "firewall_audit_status": self.firewall_audit_status,
            "temporal_firewall_status": self.temporal_firewall_status,
            "secret_sweep_status": self.secret_sweep_status,
            "package_error": self.package_error,
        }


# ---------------------------------------------------------------------------
# stub client
# ---------------------------------------------------------------------------


def build_stub_client(resolved: ResolvedConfig) -> StubModelClient:
    """Build the deterministic stub client from the frozen config script.

    Each script entry is a JSON object serialized canonically; the stub echoes
    the configured model id and reports fixed token usage. Nothing here reads a
    credential or performs I/O.
    """
    responses = [
        ModelResponse(
            text=canonical_json(entry),
            model_id=resolved.model.model_id,
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        )
        for entry in resolved.model.stub_responses
    ]
    if not responses:
        raise RunnerError("the stub model script is empty")
    return StubModelClient(responses)


def build_anthropic_client(resolved: ResolvedConfig, *, credential: str) -> Any:
    """Build the real Anthropic provider client from the frozen config.

    The credential is passed explicitly (never read from the environment here)
    and the provider is imported lazily, so the module imports without the SDK
    installed.
    """
    from smart_beta.pilot.provider_anthropic import AnthropicModelClient

    settings = dict(resolved.model.settings)
    effort = settings.get("effort", REAL_MODEL_EFFORT)
    return AnthropicModelClient(
        model_id=resolved.model.model_id,
        api_key=credential,
        effort=str(effort),
        max_output_tokens=resolved.model.max_output_tokens,
        timeout_seconds=resolved.model.timeout_seconds,
        base_url=resolved.model.endpoint,
    )


def build_deepseek_client(resolved: ResolvedConfig, *, credential: str) -> Any:
    """Build the real DeepSeek provider client from the frozen config.

    The credential is passed explicitly (never read from the environment here)
    and the provider is imported lazily, so the module imports without the SDK
    installed. The frozen transport system message is read from the model
    settings (so it is bound into the intent provenance).
    """
    from smart_beta.pilot.provider_deepseek import DeepSeekModelClient

    settings = dict(resolved.model.settings)
    effort = settings.get("reasoning_effort", DEEPSEEK_REASONING_EFFORT)
    system_message = settings.get(
        "transport_system_message", DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE
    )
    return DeepSeekModelClient(
        model_id=resolved.model.model_id,
        api_key=credential,
        reasoning_effort=str(effort),
        max_output_tokens=resolved.model.max_output_tokens,
        timeout_seconds=resolved.model.timeout_seconds,
        base_url=resolved.model.endpoint,
        system_message=str(system_message),
    )


def build_provider_client(resolved: ResolvedConfig, *, credential: str) -> Any:
    """Build the concrete provider client for the resolved config's provider.

    Only the two frozen real providers are admitted; the config validator
    already refuses anything else, so an unknown provider here is a defensive
    fail-closed error.
    """
    if resolved.model.provider == ANTHROPIC_PROVIDER:
        return build_anthropic_client(resolved, credential=credential)
    if resolved.model.provider == DEEPSEEK_PROVIDER:
        return build_deepseek_client(resolved, credential=credential)
    raise RunnerError(
        f"no concrete provider client is declared for provider "
        f"{resolved.model.provider!r}"
    )


def count_raw_candidates(content: str) -> int | None:
    """Deterministically count a raw artifact's candidates, or ``None``.

    Accepts the frozen raw-artifact shapes (a ``{"candidates": [...]}`` object
    or a bare list) and returns the number of entries. Any other shape, a
    non-list ``candidates`` value or an unparseable document returns ``None``,
    which the runner treats as a generator failure (a count != 1).
    """
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except ValueError:
        return None
    if isinstance(payload, Mapping):
        candidates = payload.get("candidates")
    elif isinstance(payload, list):
        candidates = payload
    else:
        return None
    if not isinstance(candidates, list):
        return None
    return len(candidates)


def cumulative_invocation_usage(
    journal_path: str | Path,
) -> tuple[int, int, float]:
    """Sum the journaled invocation input/output tokens and cost.

    Read from the durable journal (every result is flushed before the next
    generate), so the conservative pre-call budget rule sees the authenticated
    per-direction usage rather than a mutable in-memory counter.
    """
    read = read_journal(journal_path)
    input_tokens = output_tokens = 0
    cost = 0.0
    for record in read.records:
        if record.kind is not JournalKind.INVOCATION_RESULT:
            continue
        payload = record.to_dict()["payload"]
        input_tokens += int(payload["input_tokens"])
        output_tokens += int(payload["output_tokens"])
        cost += float(payload["cost"])
    return input_tokens, output_tokens, cost


def _projected_input_tokens(resolved: ResolvedConfig, prompt: str) -> int:
    """The conservative projected input-token count for the next invocation.

    Anthropic (section 26e) counts the rendered prompt plus the separately sent
    output JSON schema. DeepSeek (section 26f) sends the schema inside the user
    prompt and prepends the fixed transport system message, so it counts the
    system message plus the user prompt. Both round up at 2 bytes/token.
    """
    if resolved.model.provider == DEEPSEEK_PROVIDER:
        system_message = str(
            resolved.model.settings.get(
                "transport_system_message", DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE
            )
        )
        raw = (system_message + prompt).encode("utf-8")
    else:
        schema_bytes = canonical_json(OUTPUT_JSON_SCHEMA).encode("utf-8")
        raw = prompt.encode("utf-8") + schema_bytes
    return math.ceil(len(raw) / 2)


def _provider_invocation_metadata(
    client: Any,
) -> Mapping[str, Any] | None:
    """The provider usage/complete-request metadata, or ``None``.

    A concrete provider client (DeepSeek) exposes ``last_usage`` and
    ``last_request_hash``; the deterministic stub and the Anthropic adapter do
    not, so they return ``None`` and add no record.
    """
    usage = getattr(client, "last_usage", None)
    request_hash = getattr(client, "last_request_hash", None)
    payload: dict[str, Any] = {}
    if usage is not None:
        to_dict = getattr(usage, "to_dict", None)
        if callable(to_dict):
            payload["provider_usage"] = dict(to_dict())
        elif isinstance(usage, Mapping):
            payload["provider_usage"] = dict(usage)
    if isinstance(request_hash, str) and request_hash:
        payload["provider_request_hash"] = request_hash
    return payload or None


def _provider_usage_anomaly(client: Any, max_output_tokens: int) -> str | None:
    """The provider post-call anomaly reason, or ``None``.

    Prefers the provider's own :meth:`anomaly` method (so the DeepSeek contract
    owns the rule) and falls back to the same two checks over a mapping.
    """
    usage = getattr(client, "last_usage", None)
    if usage is None:
        return None
    anomaly = getattr(usage, "anomaly", None)
    if callable(anomaly):
        return anomaly(int(max_output_tokens))
    if isinstance(usage, Mapping):
        try:
            completion = int(usage.get("completion_tokens", 0) or 0)
            reasoning = int(usage.get("reasoning_tokens", 0) or 0)
        except (TypeError, ValueError):
            return None
        if completion > int(max_output_tokens):
            return "completion_tokens_exceeds_max_tokens"
        if reasoning > completion:
            return "reasoning_tokens_exceeds_completion_tokens"
    return None


# ---------------------------------------------------------------------------
# reconstruction hooks (requirement d)
# ---------------------------------------------------------------------------


def _rederive_designs(
    request: ReDerivationRequest,
    resolved: ResolvedConfig,
    pilot_data: PilotData,
) -> dict[str, ProposalDesign]:
    """Re-derive each journaled proposal's sealed design through G1+G2."""
    designs: dict[str, ProposalDesign] = {}
    for record in request.records:
        if record.kind is not JournalKind.PROPOSAL_REGISTERED:
            continue
        payload = record.to_dict()["payload"]
        proposal_payload = payload.get("proposal")
        if not isinstance(proposal_payload, Mapping):
            continue
        proposal = ResearchProposal.from_dict(proposal_payload)
        factor_spec = resolve_factor_spec(proposal)
        design = build_design(
            factor_spec=factor_spec,
            pilot_data=pilot_data,
            partition_dates=resolved.partition_dates,
            decision_policy=resolved.decision_policy,
            search_policy=resolved.search_policy,
            evaluation_spec_template=resolved.evaluation_spec_template,
        )
        if design.experiment_design is not None:
            designs[design.experiment_design.record.content_hash] = design
    return designs


def _evaluation_hook(
    request: ReDerivationRequest,
    resolved: ResolvedConfig,
    pilot_data: PilotData,
) -> Sequence[DerivationResult]:
    """Re-derive every journaled ``EvaluationRecord`` from the journaled factors."""
    journaled: dict[str, EvaluationRecord] = {}
    for record in request.records:
        if record.kind is not JournalKind.EVALUATION_RECORD:
            continue
        parsed = EvaluationRecord.from_dict(record.to_dict()["payload"])
        journaled[parsed.content_hash] = parsed
    designs = _rederive_designs(request, resolved, pilot_data)
    results: list[DerivationResult] = []
    for content_hash in journaled:
        design = designs.get(content_hash)
        if design is None:
            results.append(
                DerivationResult(
                    "evaluation",
                    content_hash,
                    DerivationStatus.MISMATCH,
                    declared_hash=content_hash,
                    detail="no journaled proposal reproduced this EvaluationRecord",
                )
            )
            continue
        results.append(
            DerivationResult(
                "evaluation",
                content_hash,
                DerivationStatus.MATCH,
                declared_hash=content_hash,
                rebuilt_hash=design.experiment_design.record.content_hash,
                detail="",
            )
        )
    return tuple(results)


def _decision_hook(
    request: ReDerivationRequest,
    resolved: ResolvedConfig,
    pilot_data: PilotData,
) -> Sequence[DerivationResult]:
    """Replay a fresh sealed ``Orchestrator`` over the journaled experiments."""
    designs = _rederive_designs(request, resolved, pilot_data)
    orchestrator = Orchestrator()
    results: list[DerivationResult] = []
    for record in request.records:
        if record.kind is not JournalKind.ORCHESTRATION_OUTCOME:
            continue
        payload = record.to_dict()["payload"]
        decision_payload = payload.get("decision_record")
        if not isinstance(decision_payload, Mapping):
            results.append(
                DerivationResult(
                    "decision",
                    f"seq:{record.seq}",
                    DerivationStatus.MISMATCH,
                    detail="orchestration_outcome has no decision_record",
                )
            )
            continue
        journaled = DecisionRecord.from_dict(decision_payload)
        design = designs.get(journaled.evaluation_record_hash)
        if design is None:
            results.append(
                DerivationResult(
                    "decision",
                    journaled.experiment_id,
                    DerivationStatus.MISMATCH,
                    declared_hash=journaled.content_hash,
                    detail="no journaled proposal reproduced this decision's evidence",
                )
            )
            continue
        outcome = orchestrator.run(
            evaluation_spec=design.evaluation_spec,
            decision_policy=resolved.decision_policy,
            search_policy=resolved.search_policy,
            record=design.experiment_design.record,
            holdout_identity=design.holdout_identity,
        )
        rebuilt = outcome.decision_record.content_hash
        status = (
            DerivationStatus.MATCH
            if rebuilt == journaled.content_hash
            else DerivationStatus.MISMATCH
        )
        results.append(
            DerivationResult(
                "decision",
                journaled.experiment_id,
                status,
                declared_hash=journaled.content_hash,
                rebuilt_hash=rebuilt,
                detail=(
                    "" if status is DerivationStatus.MATCH else "decision hash differs"
                ),
            )
        )
    return tuple(results)


def build_reconstruction_hooks(
    resolved: ResolvedConfig, pilot_data: PilotData
) -> ReconstructionHooks:
    """Wire both G4 re-derivation hooks (requirement d)."""

    def evaluation(request: ReDerivationRequest) -> Sequence[DerivationResult]:
        return _evaluation_hook(request, resolved, pilot_data)

    def decision(request: ReDerivationRequest) -> Sequence[DerivationResult]:
        return _decision_hook(request, resolved, pilot_data)

    return ReconstructionHooks(
        research_policy=resolved.research_policy,
        evaluation=evaluation,
        decision=decision,
        metadata={"run_id": resolved.run_id},
    )


# ---------------------------------------------------------------------------
# internal run context
# ---------------------------------------------------------------------------


@dataclass
class _RunContext:
    resolved: ResolvedConfig
    pilot_data: PilotData
    repo: Path
    journal: Journal
    chain: JournalChain
    journal_path: Path
    artifact_directory: Path
    git_head: str
    predecessor_run_id: str | None = None
    predecessor_journal_path: Path | None = None
    credential: str | None = None
    config_path: Path | None = None

    def append(self, kind: JournalKind, payload: Mapping[str, Any]) -> JournalRecord:
        record = self.chain.build(kind, payload)
        self.journal.append(record)
        self.journal.flush_durable()
        return record


def _full_history(
    loop: ResearchLoop, evaluation_records: Sequence[EvaluationRecord]
) -> FullResearchHistory:
    return FullResearchHistory.from_authorities(
        proposals=loop.proposal_registry,
        experiments=loop.orchestrator.registry,
        ledger=loop.orchestrator.search_ledger,
        decisions=loop.audit_decisions,
        evaluation_records=evaluation_records,
    )


def _snapshot_payloads(
    loop: ResearchLoop, full_history: FullResearchHistory, *, all_slots: bool
) -> list[tuple[str, Mapping[str, Any]]]:
    payloads: list[tuple[str, Mapping[str, Any]]] = [
        ("visible_history", loop.visible_history.to_dict()),
        ("research_feedback", loop.feedback.to_dict()),
    ]
    if all_slots:
        payloads.extend(
            [
                ("proposal_snapshot", loop.proposal_registry.snapshot().to_dict()),
                (
                    "generation_event_registry",
                    loop.generation_boundary.registry.to_dict(),
                ),
                ("registry_snapshot", loop.orchestrator.registry.snapshot().to_dict()),
                ("search_ledger", loop.orchestrator.search_ledger.to_dict()),
                (
                    "holdout_governance",
                    loop.orchestrator.holdout_governance.to_dict(),
                ),
                ("stop_ledger", loop.stop_ledger.to_dict()),
                ("lifecycle_ledger", loop.lifecycle_ledger.to_dict()),
                ("full_research_history", full_history.to_dict()),
            ]
        )
    return payloads


def _journal_snapshots(
    context: _RunContext,
    loop: ResearchLoop,
    full_history: FullResearchHistory,
    *,
    all_slots: bool,
) -> None:
    for name, snapshot in _snapshot_payloads(loop, full_history, all_slots=all_slots):
        context.append(
            JournalKind.AUTHORITY_SNAPSHOT,
            authority_snapshot_payload(name, snapshot),
        )


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


def _resolve_predecessor(
    repo: Path,
    predecessor_run_id: str,
    predecessor_journal_path: Path | None,
) -> Path:
    path = predecessor_journal_path
    if path is None:
        path = (
            repo
            / "pilot_runs"
            / "pilot1a"
            / predecessor_run_id
            / ArtifactLayout().journal
        )
    if not path.is_file():
        raise PreflightError(
            f"the predecessor journal {path} does not exist; a retry must cite "
            "an auditable predecessor"
        )
    return path


def _check_retry_rule(
    repo: Path,
    run_id: str,
    predecessor_run_id: str | None,
    predecessor_journal_path: Path | None,
) -> Path | None:
    """Enforce the section-17 retry rule; return the predecessor journal path."""
    if predecessor_run_id is None:
        return None
    if run_id == predecessor_run_id:
        raise PreflightError("a retry must use a new run_id")
    path = _resolve_predecessor(repo, predecessor_run_id, predecessor_journal_path)
    read = read_journal(path)
    if any(
        record.kind is JournalKind.ORCHESTRATION_OUTCOME for record in read.records
    ):
        raise PreflightError(
            "the predecessor registered an experiment; the section-17 retry rule "
            "forbids a retry after any holdout consumption"
        )
    lineage = [
        record.to_dict()["payload"].get("predecessor_run_id")
        for record in read.records
        if record.kind is JournalKind.RUN_STARTED
    ]
    if any(value is not None for value in lineage):
        raise PreflightError(
            "the predecessor was itself a retry; at most one retry is allowed"
        )
    return path


def _authenticated_run_started(
    journal_path: Path,
) -> tuple[Any, Mapping[str, Any]]:
    """Read and authenticate the first ``run_started`` record of a prior run."""
    try:
        read = read_journal(journal_path)
    except Exception as exc:  # noqa: BLE001 - any unreadable/corrupt journal fails closed
        raise PreflightError(
            f"prior run journal {journal_path} is not readable/authentic: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not read.records or read.records[0].kind is not JournalKind.RUN_STARTED:
        raise PreflightError(
            f"prior run journal {journal_path} does not start with run_started"
        )
    payload = read.records[0].to_dict()["payload"]
    mode = payload.get("run_mode")
    if mode not in ("dry_run", "real"):
        raise PreflightError(
            f"prior run journal {journal_path} has an ambiguous run_mode {mode!r}"
        )
    return read, payload


def _single_run_guard(
    run_root: Path,
    *,
    run_id: str,
    predecessor_run_id: str | None,
) -> None:
    """Enforce the section-26d single-run rule over ``pilot_runs/pilot1a/``.

    Every entry must be a run directory whose journal reads with a valid hash
    chain (a reported truncated tail is allowed) and starts with
    ``run_started``. A prior *real* journal that registered an experiment
    refuses permanently. Every experiment-free prior real attempt must be the
    single declared predecessor; a retry of a retry is refused. Any missing,
    corrupt or ambiguous entry fails closed before credential use or network.
    """
    if not run_root.exists():
        return
    entries = sorted(run_root.iterdir(), key=lambda path: path.name)
    real_attempts: list[tuple[str, str | None]] = []
    for entry in entries:
        if not entry.is_dir():
            raise PreflightError(
                f"pilot_runs/pilot1a/ contains a non-directory entry {entry.name!r}; "
                "refusing to run"
            )
        journal_path = entry / ArtifactLayout().journal
        if not journal_path.is_file():
            raise PreflightError(
                f"prior run directory {entry.name!r} has no journal.jsonl; "
                "refusing to run"
            )
        read, payload = _authenticated_run_started(journal_path)
        config_path = entry / ArtifactLayout().config
        if config_path.is_file():
            try:
                config_payload = json.loads(config_path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise PreflightError(
                    f"prior run config {config_path} is not valid JSON: {exc}"
                ) from exc
            if content_hash(config_payload) != payload.get("config_hash"):
                raise PreflightError(
                    f"prior run config {config_path} does not match the "
                    "journaled config_hash"
                )
            if config_payload.get("run_mode") != payload.get("run_mode"):
                raise PreflightError(
                    f"prior run config {config_path} run_mode does not match "
                    "the journaled run_mode"
                )
        if payload.get("run_mode") != "real":
            continue
        if any(
            record.kind is JournalKind.ORCHESTRATION_OUTCOME
            for record in read.records
        ):
            raise PreflightError(
                f"prior real run {entry.name!r} registered an experiment; the "
                "single-run rule forbids any further real run (no retry, no "
                "replacement run_id)"
            )
        if entry.name == run_id:
            raise PreflightError(
                f"the run id {run_id!r} already exists; runs are immutable"
            )
        real_attempts.append((entry.name, payload.get("predecessor_run_id")))
    if not real_attempts:
        return
    if predecessor_run_id is None:
        raise PreflightError(
            "a prior experiment-free real run exists; the new run must declare "
            "it as predecessor_run_id (at most one retry)"
        )
    names = {name for name, _ in real_attempts}
    if predecessor_run_id not in names:
        raise PreflightError(
            "the declared predecessor is not the prior experiment-free real "
            "attempt; refusing"
        )
    if len(real_attempts) > 1:
        raise PreflightError(
            "more than one prior experiment-free real attempt exists; at most "
            "one linked retry is allowed"
        )
    lineage = next(
        predecessor
        for name, predecessor in real_attempts
        if name == predecessor_run_id
    )
    if lineage is not None:
        raise PreflightError(
            "the declared predecessor was itself a retry; a retry of a retry "
            "is refused"
        )


def _check_exact_commit(
    git: GitProbe,
    resolved: ResolvedConfig,
    *,
    repo: Path,
    config_path: Path | None,
) -> str:
    """Require ``HEAD == bound_git_commit`` with the config the only untracked path."""
    bound = resolved.git_baseline.get("bound_git_commit")
    if not isinstance(bound, str) or not bound:
        raise PreflightError(
            "a real-run config must carry git_baseline.bound_git_commit"
        )
    head = git.head_commit()
    if head != bound:
        raise PreflightError(
            f"real-mode HEAD {head} is not the bound commit {bound}; the exact "
            "commit must match and any later change invalidates the approval"
        )
    status = git.status_porcelain()
    allowed: str | None = None
    if config_path is not None:
        try:
            relative = Path(config_path).resolve().relative_to(repo.resolve())
            allowed = f"?? {relative.as_posix()}"
        except ValueError:
            allowed = None
    offending = [
        line.strip()
        for line in status.splitlines()
        if line.strip() and line.strip() != allowed
    ]
    if offending:
        raise PreflightError(
            "real mode refuses tracked modifications and unexpected untracked "
            "paths; the only permitted untracked path is the config being run "
            f"(offending: {offending})"
        )
    return head


# ---------------------------------------------------------------------------
# side-effect-free preflight barrier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreflightCheck:
    """One named preflight check and its pass/fail disposition."""

    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class PreflightReport:
    """The side-effect-free result of :func:`preflight_check`.

    Carries every check (name + pass/fail + detail) plus the frozen identities
    and budgets a planner records at the preflight barrier. The live
    ``resolved`` config and ``pilot_data`` are attached so :func:`run_pilot`
    can reuse the exact validated objects (one source of truth); they are
    deliberately excluded from :meth:`to_dict` because they are not evidence.
    """

    checks: tuple[PreflightCheck, ...]
    approved_config_hash: str
    config_hash: str
    run_id: str
    run_mode: str
    git_head: str
    phase9_commit: str
    bound_git_commit: str | None
    prompt_template_hash: str
    prompt_template_path: str
    fixture_tree_id: str
    holdout_identity: str
    family_id: str
    model_id: str
    price_table_id: str | None
    budgets: Mapping[str, Any]
    resolved: ResolvedConfig = field(repr=False, compare=False)
    pilot_data: PilotData | None = field(default=None, repr=False, compare=False)
    provider_sdk_version: str | None = None

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def check(self, name: str) -> PreflightCheck | None:
        """Return the named check result, or ``None`` if it never ran."""
        for item in self.checks:
            if item.name == name:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [check.to_dict() for check in self.checks],
            "approved_config_hash": self.approved_config_hash,
            "config_hash": self.config_hash,
            "run_id": self.run_id,
            "run_mode": self.run_mode,
            "git_head": self.git_head,
            "phase9_commit": self.phase9_commit,
            "bound_git_commit": self.bound_git_commit,
            "prompt_template_hash": self.prompt_template_hash,
            "prompt_template_path": self.prompt_template_path,
            "fixture_tree_id": self.fixture_tree_id,
            "holdout_identity": self.holdout_identity,
            "family_id": self.family_id,
            "model_id": self.model_id,
            "price_table_id": self.price_table_id,
            "budgets": dict(self.budgets),
            "provider_sdk_version": self.provider_sdk_version,
        }


class _PreflightFailure(Exception):
    """Internal short-circuit carrier for one failed named check."""

    def __init__(self, name: str, detail: str) -> None:
        super().__init__(detail)
        self.name = name
        self.detail = detail


def _budget_identities(resolved: ResolvedConfig) -> dict[str, Any]:
    budgets = resolved.budgets
    return {
        "proposals": budgets.proposals,
        "statistical_m": budgets.statistical_m,
        "invocation_ceiling": budgets.invocation_ceiling,
        "llm_tokens": budgets.llm_tokens,
        "llm_input_tokens": budgets.llm_input_tokens,
        "llm_output_tokens": budgets.llm_output_tokens,
        "llm_cost": budgets.llm_cost,
        "wall_clock_seconds": budgets.wall_clock_seconds,
        "max_provider_retries": budgets.max_provider_retries,
        "max_output_tokens": resolved.model.max_output_tokens,
        "timeout_seconds": resolved.model.timeout_seconds,
    }


def _build_preflight_report(
    resolved: ResolvedConfig,
    pilot_data: PilotData | None,
    *,
    approved_config_hash: str,
    git_head: str,
    phase9_commit: str,
    holdout_identity: str,
    checks: Sequence[PreflightCheck],
    provider_sdk_version: str | None = None,
) -> PreflightReport:
    bound = resolved.git_baseline.get("bound_git_commit")
    price_table_id = resolved.model.settings.get("price_table_id")
    return PreflightReport(
        checks=tuple(checks),
        approved_config_hash=approved_config_hash,
        config_hash=resolved.config_hash,
        run_id=resolved.run_id,
        run_mode=resolved.run_mode,
        git_head=git_head,
        phase9_commit=phase9_commit,
        bound_git_commit=None if bound is None else str(bound),
        prompt_template_hash=resolved.prompt_template_hash,
        prompt_template_path=resolved.prompt_template_path,
        fixture_tree_id=resolved.dataset.fixture_tree_id,
        holdout_identity=holdout_identity,
        family_id=resolved.config.family_id,
        model_id=resolved.model.model_id,
        price_table_id=None if price_table_id is None else str(price_table_id),
        budgets=_budget_identities(resolved),
        provider_sdk_version=provider_sdk_version,
        resolved=resolved,
        pilot_data=pilot_data,
    )


def preflight_check(
    config: PilotConfig,
    *,
    approved_config_hash: str,
    repo: Path | str,
    config_path: Path | str | None = None,
    git: GitProbe | None = None,
    predecessor_run_id: str | None = None,
    predecessor_journal_path: Path | str | None = None,
    require_credential: bool = False,
) -> PreflightReport:
    """Run every Pilot-1A preflight check with **no** side effects.

    This is the single source of truth for preflight: :func:`run_pilot` calls
    it (with ``require_credential=True`` in real mode) before doing any
    side-effecting step. It resolves and validates the config, checks the
    approved hash, the sealed baseline, the real-mode exact-commit binding and
    the single permitted untracked config path, the fixture tree id and
    per-file hashes (through the offline G1 loader with the frozen cap), the
    section-17 retry rule and the section-26d single-run guard, the base-URL /
    proxy refusal, the provider/model/effort/price/ceiling/endpoint freeze, the
    endpoint parse (**without** DNS) and, only when ``require_credential`` is
    true, that the model credential is *present* (its value is never read back,
    returned, stored or logged).

    It runs under the total network tripwire. It creates no file or directory,
    writes no journal, performs no DNS lookup and makes no model call. On
    failure it raises :class:`PreflightError` (fail closed) whose ``report``
    carries the checks that ran.
    """
    resolved_repo = Path(repo)
    probe = git if git is not None else GitProbe(resolved_repo)
    resolved_path = None if config_path is None else Path(config_path)
    predecessor_path = (
        None
        if predecessor_journal_path is None
        else Path(predecessor_journal_path)
    )

    from smart_beta.pilot.config import load_pilot_data

    checks: list[PreflightCheck] = []
    resolved: ResolvedConfig | None = None
    pilot_data: PilotData | None = None
    head = ""
    phase9_commit = ""
    holdout_identity = ""
    provider_sdk_version: str | None = None

    install_network_tripwire()
    try:
        try:
            resolved = resolve_config(config)
        except ConfigError as exc:
            raise _PreflightFailure("config_resolution", str(exc)) from exc
        checks.append(PreflightCheck("config_resolution", True, ""))

        if not isinstance(approved_config_hash, str) or not approved_config_hash:
            raise _PreflightFailure(
                "approved_config_hash", "an approved config hash is required"
            )
        if resolved.config_hash != approved_config_hash:
            raise _PreflightFailure(
                "approved_config_hash",
                "the config hash does not match the approved hash "
                f"(computed {resolved.config_hash}, approved {approved_config_hash})",
            )
        checks.append(
            PreflightCheck("approved_config_hash", True, resolved.config_hash)
        )

        real_mode = resolved.run_mode == "real"
        refused_env_vars = (
            provider_refused_env_vars(resolved.model.provider)
            if resolved.model.provider in (ANTHROPIC_PROVIDER, DEEPSEEK_PROVIDER)
            else BASE_URL_OR_PROXY_ENV_VARS
        )

        for name in refused_env_vars:
            if os.environ.get(name):
                raise _PreflightFailure(
                    "base_url_proxy",
                    f"{name} is set; a base-URL or proxy override would change "
                    "the frozen provider endpoint and is refused",
                )
        checks.append(PreflightCheck("base_url_proxy", True, ""))

        if require_credential:
            credential_env = provider_credential_env(resolved.model.provider)
            if credential_env not in os.environ or not os.environ.get(credential_env):
                raise _PreflightFailure(
                    "credential_presence",
                    f"real mode requires a non-empty {credential_env} in "
                    "the environment; refusing before any call",
                )
            checks.append(PreflightCheck("credential_presence", True, "present"))
        else:
            checks.append(
                PreflightCheck("credential_presence", True, "not-required")
            )

        checks.append(
            PreflightCheck("provider_freeze", True, resolved.model.provider)
        )

        # Real mode requires the optional provider SDK to be installed at a
        # satisfying version, checked without importing it or building a
        # client. Dry-run/stub mode never requires it. The distribution and
        # frozen range are per provider (section 26f: Anthropic
        # ``anthropic>=1,<2``, DeepSeek ``openai>=3,<4``).
        if real_mode:
            provider_sdk_version = _require_provider_sdk(resolved.model.provider)
            checks.append(
                PreflightCheck("provider_sdk", True, provider_sdk_version)
            )
        else:
            checks.append(
                PreflightCheck("provider_sdk", True, "not-real-mode")
            )

        head = probe.head_commit()
        phase9_commit = str(resolved.git_baseline.get("phase9_complete", ""))
        if not phase9_commit:
            raise _PreflightFailure(
                "sealed_baseline", "config.git_baseline.phase9_complete is missing"
            )
        if not probe.is_ancestor(phase9_commit, head):
            raise _PreflightFailure(
                "sealed_baseline",
                f"HEAD {head} does not descend from the sealed baseline "
                f"{phase9_commit}",
            )
        checks.append(PreflightCheck("sealed_baseline", True, phase9_commit))

        if real_mode:
            try:
                head = _check_exact_commit(
                    probe, resolved, repo=resolved_repo, config_path=resolved_path
                )
            except PreflightError as exc:
                raise _PreflightFailure("exact_commit_binding", str(exc)) from exc
            checks.append(PreflightCheck("exact_commit_binding", True, head))
        else:
            if not probe.is_clean():
                raise _PreflightFailure(
                    "working_tree", "the working tree is dirty; refusing to run"
                )
            checks.append(PreflightCheck("working_tree", True, ""))

        tree_id = probe.tree_id(resolved.dataset.fixture_dir)
        if tree_id != resolved.dataset.fixture_tree_id:
            raise _PreflightFailure(
                "fixture_tree_id",
                "the fixture directory git tree id does not match the frozen "
                f"config (got {tree_id}, expected {resolved.dataset.fixture_tree_id})",
            )
        checks.append(PreflightCheck("fixture_tree_id", True, tree_id))

        try:
            _check_retry_rule(
                resolved_repo,
                resolved.run_id,
                predecessor_run_id,
                predecessor_path,
            )
        except PreflightError as exc:
            raise _PreflightFailure("retry_rule", str(exc)) from exc
        checks.append(PreflightCheck("retry_rule", True, ""))

        if real_mode:
            try:
                _single_run_guard(
                    resolved_repo / "pilot_runs" / "pilot1a",
                    run_id=resolved.run_id,
                    predecessor_run_id=predecessor_run_id,
                )
            except PreflightError as exc:
                raise _PreflightFailure("single_run_guard", str(exc)) from exc
            checks.append(PreflightCheck("single_run_guard", True, ""))
        else:
            checks.append(
                PreflightCheck("single_run_guard", True, "not-real-mode")
            )

        endpoint = parse_endpoint(resolved.model.endpoint)
        checks.append(
            PreflightCheck("endpoint_parse", True, f"{endpoint[0]}:{endpoint[1]}")
        )

        try:
            pilot_data = load_pilot_data(resolved, repo=resolved_repo)
        except Exception as exc:  # noqa: BLE001 - the G1 boundary fails closed
            raise _PreflightFailure(
                "fixture_integrity", f"fixture / PIT load failed: {exc}"
            ) from exc
        checks.append(PreflightCheck("fixture_integrity", True, ""))

        artifact_directory = resolved_repo / resolved.artifact_destination
        canonical_run_directory = (
            resolved_repo / "pilot_runs" / "pilot1a" / resolved.run_id
        )
        existing = sorted(
            {
                path
                for path in (artifact_directory, canonical_run_directory)
                if path.exists()
            },
            key=str,
        )
        if existing:
            raise _PreflightFailure(
                "artifact_directory_available",
                "the run_id or artifact directory already exists; runs are "
                "immutable and are never reused or overwritten: "
                + ", ".join(str(path) for path in existing),
            )
        journal_path = artifact_directory / ArtifactLayout().journal
        if journal_path.exists():
            raise _PreflightFailure(
                "artifact_directory_available",
                f"the run journal {journal_path} already exists; runs are "
                "immutable and are never continued in place",
            )
        checks.append(
            PreflightCheck(
                "artifact_directory_available", True, str(artifact_directory)
            )
        )

        try:
            holdout_identity = build_holdout_identity(
                partition=build_partition(resolved.partition_dates),
                pilot_data=pilot_data,
            ).holdout_id
        except Exception as exc:  # noqa: BLE001 - the pure identity fails closed
            raise _PreflightFailure("holdout_identity", str(exc)) from exc
        checks.append(
            PreflightCheck("holdout_identity", True, holdout_identity)
        )

        return _build_preflight_report(
            resolved,
            pilot_data,
            approved_config_hash=approved_config_hash,
            git_head=head,
            phase9_commit=phase9_commit,
            holdout_identity=holdout_identity,
            checks=checks,
            provider_sdk_version=provider_sdk_version,
        )
    except _PreflightFailure as failure:
        checks.append(PreflightCheck(failure.name, False, failure.detail))
        report = None
        if resolved is not None:
            report = _build_preflight_report(
                resolved,
                pilot_data,
                approved_config_hash=approved_config_hash,
                git_head=head,
                phase9_commit=phase9_commit,
                holdout_identity=holdout_identity,
                checks=checks,
                provider_sdk_version=provider_sdk_version,
            )
        raise PreflightError(
            f"preflight check {failure.name!r} failed: {failure.detail}",
            report=report,
        ) from failure
    except PreflightError as exc:
        # A helper raised a bare PreflightError; attach the checks that ran.
        if exc.report is None and resolved is not None:
            checks.append(PreflightCheck("preflight", False, str(exc)))
            report = _build_preflight_report(
                resolved,
                pilot_data,
                approved_config_hash=approved_config_hash,
                git_head=head,
                phase9_commit=phase9_commit,
                holdout_identity=holdout_identity,
                checks=checks,
                provider_sdk_version=provider_sdk_version,
            )
            raise PreflightError(str(exc), report=report) from exc
        raise
    finally:
        restore_network_tripwire()


def _preflight(
    config: PilotConfig,
    *,
    approved_config_hash: str,
    repo: Path,
    git: GitProbe,
    predecessor_run_id: str | None,
    predecessor_journal_path: Path | None,
    config_path: Path | None = None,
    endpoint_resolver: Callable[[str, int], Sequence[str]] | None = None,
) -> _RunContext:
    """Validate through :func:`preflight_check`, then do the side effects.

    All checks live in :func:`preflight_check` (one source of truth); this
    wrapper calls it with ``require_credential=True`` in real mode, then
    performs the only side-effecting steps: capture-then-delete the model
    credential, install the real endpoint allowlist (or the total tripwire in
    dry-run mode) and create the run directory and ``run_started`` journal.
    """
    try:
        probe_resolved = resolve_config(config)
    except ConfigError as exc:
        raise PreflightError(f"config resolution failed: {exc}") from exc
    real_mode = probe_resolved.run_mode == "real"

    report = preflight_check(
        config,
        approved_config_hash=approved_config_hash,
        repo=repo,
        config_path=config_path,
        git=git,
        predecessor_run_id=predecessor_run_id,
        predecessor_journal_path=predecessor_journal_path,
        require_credential=real_mode,
    )
    resolved = report.resolved
    assert report.pilot_data is not None  # a passing report always loaded it
    pilot_data = report.pilot_data
    head = report.git_head

    credential: str | None = None
    if real_mode:
        # Capture-then-delete before any later subprocess can inherit it. The
        # git probe additionally strips credentials from every subprocess env.
        credential = capture_model_credential(provider=resolved.model.provider)
    else:
        scrub_credentials(model_credentials=True)

    # Install the network guard before any adapter work: a total tripwire in
    # dry-run/stub mode, or the pinned-endpoint allowlist in real mode.
    # ``urllib`` stays blocked in both.
    if real_mode:
        endpoint = parse_endpoint(resolved.model.endpoint)
        resolver = (
            endpoint_resolver if endpoint_resolver is not None else endpoint_addresses
        )
        addresses = tuple(resolver(endpoint[0], endpoint[1]))
        if not addresses:
            raise PreflightError(
                f"the pinned provider endpoint {resolved.model.endpoint!r} did "
                "not resolve to any address"
            )
        install_network_tripwire(
            allowed_endpoint=endpoint, resolved_addresses=addresses
        )
    else:
        install_network_tripwire()

    predecessor_path = (
        None
        if predecessor_run_id is None
        else _resolve_predecessor(repo, predecessor_run_id, predecessor_journal_path)
    )

    artifact_directory = repo / resolved.artifact_destination
    canonical_run_directory = repo / "pilot_runs" / "pilot1a" / resolved.run_id
    existing = sorted(
        {path for path in (artifact_directory, canonical_run_directory) if path.exists()},
        key=str,
    )
    if existing:
        raise PreflightError(
            "the run_id or artifact directory already exists; runs are immutable "
            "and are never reused or overwritten: "
            + ", ".join(str(path) for path in existing)
        )
    journal_path = artifact_directory / ArtifactLayout().journal
    if journal_path.exists():
        raise PreflightError(
            f"the run journal {journal_path} already exists; runs are immutable "
            "and are never continued in place"
        )

    artifact_directory.mkdir(parents=True, exist_ok=True)
    journal = Journal(journal_path, run_id=resolved.run_id)
    chain = JournalChain(
        resolved.run_id,
        next_seq=journal.next_seq,
        prev_sha256=journal.prev_sha256,
    )
    context = _RunContext(
        resolved=resolved,
        pilot_data=pilot_data,
        repo=repo,
        journal=journal,
        chain=chain,
        journal_path=journal_path,
        artifact_directory=artifact_directory,
        git_head=head,
        predecessor_run_id=predecessor_run_id,
        predecessor_journal_path=predecessor_path,
        credential=credential,
        config_path=config_path,
    )
    context.append(
        JournalKind.RUN_STARTED,
        {
            "config_hash": resolved.config_hash,
            "run_id": resolved.run_id,
            "run_mode": resolved.run_mode,
            "git_head": head,
            "git_baseline": dict(resolved.git_baseline),
            "predecessor_run_id": predecessor_run_id,
            "prompt_template_hash": resolved.prompt_template_hash,
            "dataset_fixture_tree_id": resolved.dataset.fixture_tree_id,
            "research_policy": resolved.research_policy.to_dict(),
        },
    )
    return context


# ---------------------------------------------------------------------------
# the driving loop
# ---------------------------------------------------------------------------


def _close_typed_stop(
    context: _RunContext, stop: StopRecord, detail: str
) -> tuple[RunStatus, str]:
    context.append(JournalKind.STOP, stop.to_dict())
    context.append(
        JournalKind.RUN_CLOSED,
        {"status": RunStatus.COMPLETED_STOP.value, "detail": detail or stop.reason.value},
    )
    return RunStatus.COMPLETED_STOP, stop.reason.value


def _drive(
    context: _RunContext,
    *,
    client: Any | None,
    monotonic: Callable[[], float],
    utc_now: Callable[[], str],
) -> tuple[RunStatus, str, list[EvaluationRecord]]:
    """Drive the sealed loop until a typed stop, a ceiling, or a failure."""
    resolved = context.resolved
    loop = ResearchLoop(policy=resolved.research_policy)
    if client is not None:
        model_client = client
    elif resolved.run_mode == "real":
        if not context.credential:  # pragma: no cover - preflight guarantees it
            raise RunnerError("real mode reached the loop without a credential")
        model_client = build_provider_client(
            resolved, credential=context.credential
        )
    else:
        model_client = build_stub_client(resolved)
    adapter = ModelAdapter(
        run_id=resolved.run_id,
        research_policy=resolved.research_policy,
        client=model_client,
        journal=context.journal,
        chain=context.chain,
        model_provider=resolved.model.provider,
        model_id=resolved.model.model_id,
        price_table=resolved.model.price_table,
        settings=resolved.model.settings,
        max_provider_retries=resolved.model.max_provider_retries,
        template=resolved.prompt_template_text,
        clock=utc_now,
    )

    evaluation_records: list[EvaluationRecord] = []
    start = monotonic()

    while True:
        # Harness ceilings -- checked before each generate.
        if adapter.next_ordinal >= resolved.budgets.invocation_ceiling:
            context.append(
                JournalKind.INTERRUPTED,
                {
                    "status": RunStatus.INTERRUPTED.value,
                    "detail": "harness invocation ceiling reached",
                },
            )
            return (
                RunStatus.INTERRUPTED,
                "harness invocation ceiling reached",
                evaluation_records,
            )
        if monotonic() - start >= resolved.budgets.wall_clock_seconds:
            context.append(
                JournalKind.INTERRUPTED,
                {
                    "status": RunStatus.INTERRUPTED.value,
                    "detail": "harness wall-clock ceiling reached",
                },
            )
            return (
                RunStatus.INTERRUPTED,
                "harness wall-clock ceiling reached",
                evaluation_records,
            )
        if loop.tokens_used >= resolved.budgets.llm_tokens:
            context.append(
                JournalKind.INTERRUPTED,
                {
                    "status": RunStatus.INTERRUPTED.value,
                    "detail": "harness LLM token ceiling reached",
                },
            )
            return (
                RunStatus.INTERRUPTED,
                "harness LLM token ceiling reached",
                evaluation_records,
            )
        if loop.cost_used >= resolved.budgets.llm_cost:
            context.append(
                JournalKind.INTERRUPTED,
                {
                    "status": RunStatus.INTERRUPTED.value,
                    "detail": "harness LLM cost ceiling reached",
                },
            )
            return (
                RunStatus.INTERRUPTED,
                "harness LLM cost ceiling reached",
                evaluation_records,
            )

        full_history = _full_history(loop, evaluation_records)
        loop.snapshot_history(full_history)
        # Requirement (b): the pre-call authority snapshots.
        _journal_snapshots(context, loop, full_history, all_slots=False)

        # Section-26b TF-6: before *every* generate, audit the exact
        # generator-visible projection against the config's authorized
        # development interval D = [is_start, holdout_start). A violation means
        # no model call is made and the loop stops with the typed
        # HOLDOUT_FIREWALL_VIOLATION reason.
        try:
            enforce_temporal_firewall(
                loop.visible_history,
                loop.feedback,
                evaluation_records,
                is_start=resolved.partition_dates.is_start,
                holdout_start=resolved.partition_dates.holdout_start,
            )
        except TemporalFirewallViolation as exc:
            stop = loop.stop(StopReason.HOLDOUT_FIREWALL_VIOLATION, detail=str(exc))
            status, _ = _close_typed_stop(
                context, stop, "temporal holdout firewall violation"
            )
            return status, stop.reason.value, evaluation_records

        # Conservative pre-call budget rule (section 26e): project the next
        # invocation from the exact rendered prompt + schema and the frozen
        # per-invocation output ceiling, and refuse before any call when the
        # projected cumulative usage would exceed a ceiling. The dry-run config
        # omits the per-direction ceilings (``None``), so only the frozen
        # combined ``llm_tokens``/``llm_cost`` ceilings apply there and the
        # H6-v2 stub path is unchanged.
        rendered = render_request(
            loop.visible_history,
            loop.feedback,
            template=resolved.prompt_template_text,
        )
        projected_input = _projected_input_tokens(resolved, rendered.prompt)
        projected_output = resolved.model.max_output_tokens
        projected_cost = (
            projected_input * resolved.model.price_table.input_per_token
            + projected_output * resolved.model.price_table.output_per_token
        )
        cum_input, cum_output, cum_cost = cumulative_invocation_usage(
            context.journal_path
        )
        budget_detail: str | None = None
        if adapter.next_ordinal + 1 > resolved.budgets.invocation_ceiling:
            budget_detail = "harness invocation ceiling reached"
        elif (
            resolved.budgets.llm_input_tokens is not None
            and cum_input + projected_input > resolved.budgets.llm_input_tokens
        ):
            budget_detail = "harness cumulative input-token ceiling reached"
        elif (
            resolved.budgets.llm_output_tokens is not None
            and cum_output + projected_output > resolved.budgets.llm_output_tokens
        ):
            budget_detail = "harness cumulative output-token ceiling reached"
        elif cum_cost + projected_cost > resolved.budgets.llm_cost:
            budget_detail = "harness cumulative cost ceiling reached"
        elif monotonic() - start >= resolved.budgets.wall_clock_seconds:
            budget_detail = "harness wall-clock ceiling reached"
        if budget_detail is not None:
            context.append(
                JournalKind.INTERRUPTED,
                {"status": RunStatus.INTERRUPTED.value, "detail": budget_detail},
            )
            return RunStatus.INTERRUPTED, budget_detail, evaluation_records

        try:
            generated = loop.generate(adapter)
        except FirewallViolation as exc:
            stop = loop.stop(StopReason.HOLDOUT_FIREWALL_VIOLATION, detail=str(exc))
            status, _ = _close_typed_stop(context, stop, "holdout firewall violation")
            return status, "holdout firewall violation", evaluation_records
        if isinstance(generated, StopRecord):
            status, _ = _close_typed_stop(context, generated, "generator stop")
            return status, generated.reason.value, evaluation_records
        assert isinstance(generated, GenerationEvent)  # noqa: S101
        context.append(JournalKind.GENERATION_EVENT, generated.to_dict())

        # Provider usage + post-call anomaly (plan section 26f). When the
        # concrete provider exposes the captured usage and the complete-request
        # hash, record them durably; a completion/reasoning anomaly stops the
        # run before any further invocation (INTERRUPTED, resource). Injected
        # stub clients expose neither, so the dry-run/H4 path is unchanged.
        if resolved.run_mode == "real":
            metadata = _provider_invocation_metadata(model_client)
            if metadata is not None:
                context.append(JournalKind.LLM_USAGE, metadata)
            anomaly = _provider_usage_anomaly(
                model_client, resolved.model.max_output_tokens
            )
            if anomaly is not None:
                detail = f"provider usage anomaly: {anomaly}"
                context.append(
                    JournalKind.INTERRUPTED,
                    {"status": RunStatus.INTERRUPTED.value, "detail": detail},
                )
                return RunStatus.INTERRUPTED, detail, evaluation_records

        # Section-26e one-invocation -> at most one candidate guard. It is
        # applied on the real-model path: the frozen real prompt promises
        # exactly one candidate, and an unexpected multi-candidate response in
        # one invocation could exhaust the proposal budget (3) on a single
        # experiment. The deterministic stub script keeps the multi-candidate
        # shape the H4 adversarial integration test exercises through sealed
        # normalization.
        if resolved.run_mode == "real":
            candidate_count = count_raw_candidates(generated.raw_artifact.content)
            if candidate_count != 1:
                stop = loop.stop(
                    StopReason.GENERATOR_FAILURE,
                    detail=(
                        "one invocation must return exactly one candidate; got "
                        f"{candidate_count!r}"
                    ),
                )
                status, _ = _close_typed_stop(
                    context, stop, "generator candidate-count violation"
                )
                return status, stop.reason.value, evaluation_records

        normalized = loop.normalize_persisted()
        if isinstance(normalized, StopRecord):
            status, _ = _close_typed_stop(context, normalized, "normalization stop")
            return status, normalized.reason.value, evaluation_records
        assert isinstance(normalized, NormalizationOutcome)  # noqa: S101
        context.append(JournalKind.NORMALIZATION_OUTCOME, normalized.to_dict())

        registered = loop.register_proposals()
        if isinstance(registered, StopRecord):
            status, _ = _close_typed_stop(context, registered, "registration stop")
            return status, registered.reason.value, evaluation_records
        for index, entry in enumerate(registered):
            assert isinstance(entry, ProposalEntry)  # noqa: S101
            context.append(
                JournalKind.PROPOSAL_REGISTERED,
                proposal_registered_payload(entry.proposal.to_dict(), index),
            )

        admitted = loop.admit_factorspec()
        if isinstance(admitted, StopRecord):
            status, _ = _close_typed_stop(context, admitted, "admission stop")
            return status, admitted.reason.value, evaluation_records

        proposal = loop.current_proposal
        if not isinstance(proposal, ResearchProposal):  # pragma: no cover
            raise RunnerError("the loop admitted no proposal in flight")
        design = build_design(
            factor_spec=resolve_factor_spec(proposal),
            pilot_data=context.pilot_data,
            partition_dates=resolved.partition_dates,
            decision_policy=resolved.decision_policy,
            search_policy=resolved.search_policy,
            evaluation_spec_template=resolved.evaluation_spec_template,
        )
        context.append(JournalKind.ADMISSION_RESULT, design.admission_result.to_dict())
        if not design.required_data_certified:
            reasons = "; ".join(design.admission_reasons) or "no structured reason"
            stop = loop.stop(StopReason.DATA_NOT_PIT_CERTIFIED, detail=reasons)
            status, _ = _close_typed_stop(context, stop, "data admission failed")
            return status, stop.reason.value, evaluation_records

        assert design.evaluation_spec is not None  # noqa: S101
        assert design.experiment_design is not None  # noqa: S101
        context.append(JournalKind.EVALUATION_SPEC, design.evaluation_spec.to_dict())
        context.append(
            JournalKind.EVALUATION_RECORD, design.experiment_design.record.to_dict()
        )
        evaluation_records.append(design.experiment_design.record)

        outcome = loop.delegate_experiment(design.experiment_design)
        if isinstance(outcome, StopRecord):
            status, _ = _close_typed_stop(context, outcome, "experiment stop")
            return status, outcome.reason.value, evaluation_records
        context.append(
            JournalKind.ORCHESTRATION_OUTCOME,
            {
                "decision_record": outcome.decision_record.to_dict(),
                "search_decision": outcome.search_decision.to_dict(),
                "holdout_evidence": outcome.holdout_evidence.to_dict(),
            },
        )

        full_history = _full_history(loop, evaluation_records)
        loop.record_feedback(full_history)
        loop.next_proposal()
        context.append(
            JournalKind.LLM_USAGE,
            {
                "tokens_used": loop.tokens_used,
                "cost_used": loop.cost_used,
                "proposal_count": loop.proposal_count,
            },
        )
        _journal_snapshots(context, loop, full_history, all_slots=True)


# ---------------------------------------------------------------------------
# reconstruction / assembly
# ---------------------------------------------------------------------------


def _read_or_none(path: Path):
    try:
        return read_journal(path)
    except Exception:  # noqa: BLE001 - reported by the caller, never hidden
        return None


def _predecessor_report_section(context: _RunContext) -> str:
    """Render the section-17 retry-lineage disclosure for a retry run."""
    assert context.predecessor_run_id is not None  # noqa: S101
    lines = [
        "",
        "## Retry lineage (section 17)",
        "",
        f"- predecessor_run_id: {context.predecessor_run_id}",
    ]
    path = context.predecessor_journal_path
    read = None if path is None else _read_or_none(path)
    if read is not None:
        intents = sum(
            1
            for record in read.records
            if record.kind is JournalKind.INVOCATION_INTENT
        )
        proposals = sum(
            1
            for record in read.records
            if record.kind is JournalKind.PROPOSAL_REGISTERED
        )
        lines.append(f"- predecessor invocations: {intents}")
        lines.append(f"- predecessor proposals: {proposals}")
        lines.append(
            "- predecessor registered no experiment (section-17 retry is legal)."
        )
    lines.append("")
    return "\n".join(lines)


def _finalize(
    context: _RunContext,
    report: ReconstructionReport,
    status: RunStatus,
    detail: str,
    *,
    assemble: bool,
) -> RunOutcome:
    read = _read_or_none(context.journal_path)
    if read is None:
        return RunOutcome(
            run_id=context.resolved.run_id,
            status=status,
            detail=detail,
            journal_path=context.journal_path,
            artifact_directory=context.artifact_directory,
            reconstruction_status=report.status.value,
        )
    intents = sum(
        1 for record in read.records if record.kind is JournalKind.INVOCATION_INTENT
    )
    stop = next(
        (
            record.to_dict()["payload"].get("reason")
            for record in reversed(read.records)
            if record.kind is JournalKind.STOP
        ),
        None,
    )
    if not assemble:
        return RunOutcome(
            run_id=context.resolved.run_id,
            status=status,
            detail=detail,
            journal_path=context.journal_path,
            artifact_directory=context.artifact_directory,
            stop_reason=stop,
            invocation_count=intents,
            reconstruction_status=report.status.value,
            temporal_firewall_status=None,
        )

    report_markdown = None
    if context.predecessor_run_id is not None:
        from smart_beta.pilot.report import render_report

        report_markdown = (
            render_report(
                read.records,
                run_id=context.resolved.run_id,
                status=status.value,
                reconstruction_status=report.status.value,
            )
            + _predecessor_report_section(context)
        )

    try:
        sweep_environ = (
            None
            if context.credential is None
            else {
                provider_credential_env(
                    context.resolved.model.provider
                ): context.credential
            }
        )
        package = assemble_package(
            context.artifact_directory,
            config=context.resolved.config,
            prompt_template=context.resolved.prompt_template_text,
            journal_path=context.journal_path,
            reconstruction_report=report,
            git_head=context.git_head,
            phase9_target=str(
                context.resolved.git_baseline.get("phase9_complete", "")
            ),
            report_markdown=report_markdown,
            environ=sweep_environ,
        )
        package_error = None
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        package = None
        package_error = f"{type(exc).__name__}: {exc}"

    return RunOutcome(
        run_id=context.resolved.run_id,
        status=status,
        detail=detail,
        journal_path=context.journal_path,
        artifact_directory=context.artifact_directory,
        stop_reason=stop,
        invocation_count=intents,
        reconstruction_status=report.status.value,
        firewall_audit_status=(
            None if package is None else package.firewall_audit.status.value
        ),
        temporal_firewall_status=(
            None if package is None else package.temporal_firewall.status.value
        ),
        secret_sweep_status=(
            None if package is None else package.secret_sweep.status.value
        ),
        package_error=package_error,
        package=package,
    )


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------


def run_pilot(
    config: PilotConfig,
    *,
    approved_config_hash: str,
    repo: Path | str | None = None,
    git: GitProbe | None = None,
    client: Any | None = None,
    predecessor_run_id: str | None = None,
    predecessor_journal_path: Path | str | None = None,
    config_path: Path | str | None = None,
    endpoint_resolver: Callable[[str, int], Sequence[str]] | None = None,
    assemble: bool = True,
    monotonic: Callable[[], float] | None = None,
    utc_now: Callable[[], str] | None = None,
) -> RunOutcome:
    """Run one governed Pilot-1A loop end to end.

    Returns a :class:`RunOutcome`. Preflight failures return
    ``FAILED_PREFLIGHT`` with no model call and no journal. Everything else
    writes an immutable journal (ending in ``run_closed`` or ``interrupted``),
    reconstructs it offline with both hooks wired and (by default) assembles
    the G6 package.

    ``config_path`` is the on-disk path of the config being approved. In real
    mode it is the single permitted untracked path (the planner generates the
    real config untracked after the P1A-PA merge, so committing it cannot move
    HEAD). ``endpoint_resolver`` is injectable so offline tests can avoid DNS;
    it defaults to :func:`endpoint_addresses`.
    """
    from smart_beta.pilot.config import repo_root

    resolved_repo = Path(repo) if repo is not None else repo_root()
    probe = git if git is not None else GitProbe(resolved_repo)
    clock_monotonic = monotonic if monotonic is not None else time.monotonic
    clock_utc = utc_now if utc_now is not None else _utc_now_iso

    try:
        return _run_pilot_inner(
            config,
            approved_config_hash=approved_config_hash,
            resolved_repo=resolved_repo,
            probe=probe,
            client=client,
            predecessor_run_id=predecessor_run_id,
            predecessor_journal_path=predecessor_journal_path,
            config_path=None if config_path is None else Path(config_path),
            endpoint_resolver=endpoint_resolver,
            assemble=assemble,
            clock_monotonic=clock_monotonic,
            clock_utc=clock_utc,
        )
    finally:
        restore_network_tripwire()


def _run_pilot_inner(
    config: PilotConfig,
    *,
    approved_config_hash: str,
    resolved_repo: Path,
    probe: GitProbe,
    client: Any | None,
    predecessor_run_id: str | None,
    predecessor_journal_path: Path | str | None,
    config_path: Path | None,
    endpoint_resolver: Callable[[str, int], Sequence[str]] | None,
    assemble: bool,
    clock_monotonic: Callable[[], float],
    clock_utc: Callable[[], str],
) -> RunOutcome:
    try:
        context = _preflight(
            config,
            approved_config_hash=approved_config_hash,
            repo=resolved_repo,
            git=probe,
            predecessor_run_id=predecessor_run_id,
            predecessor_journal_path=(
                None
                if predecessor_journal_path is None
                else Path(predecessor_journal_path)
            ),
            config_path=config_path,
            endpoint_resolver=endpoint_resolver,
        )
    except Exception as exc:  # noqa: BLE001 - preflight fails closed
        return RunOutcome(
            run_id=config.run_id,
            status=RunStatus.FAILED_PREFLIGHT,
            detail=str(exc),
        )

    resolved = context.resolved
    try:
        status, detail, _ = _drive(
            context, client=client, monotonic=clock_monotonic, utc_now=clock_utc
        )
    except BaseException as exc:  # noqa: BLE001 - crash/infrastructure -> INTERRUPTED
        try:
            context.append(
                JournalKind.INTERRUPTED,
                {
                    "status": RunStatus.INTERRUPTED.value,
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            )
        except Exception:  # noqa: BLE001 - already interrupted
            pass
        report = _safe_reconstruct(context, resolved, context.pilot_data)
        return _finalize(
            context,
            report,
            RunStatus.INTERRUPTED,
            f"{type(exc).__name__}: {exc}",
            assemble=assemble,
        )

    try:
        context.journal.close()
    except Exception:  # noqa: BLE001 - best effort
        pass
    hooks = build_reconstruction_hooks(resolved, context.pilot_data)
    report = reconstruct(context.journal_path, hooks=hooks)
    return _finalize(context, report, status, detail, assemble=assemble)


def _safe_reconstruct(
    context: _RunContext, resolved: ResolvedConfig, pilot_data: PilotData
) -> ReconstructionReport:
    try:
        context.journal.close()
    except Exception:  # noqa: BLE001 - best effort on the crash path
        pass
    try:
        hooks = build_reconstruction_hooks(resolved, pilot_data)
        return reconstruct(context.journal_path, hooks=hooks)
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        return ReconstructionReport(
            status=ReconstructionStatus.RECONSTRUCTION_MISMATCH,
            run_id=resolved.run_id,
            record_count=0,
            truncated_tail=False,
            tail_byte_length=0,
            notes=(f"reconstruction raised {type(exc).__name__}: {exc}",),
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
