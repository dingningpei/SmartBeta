"""Pilot 1A P1A-G5: config + runner + integration unit tests.

Every test is offline: the shared ``offline_guard`` fixture blocks outbound
network access and scrubs every provider credential, and the runner installs
its own urllib/socket tripwire. The only model client is the deterministic
stub; no provider SDK, credential or model call is involved.

The constructed fixture directories this module builds are synthetic Tiingo
recording directories written under ``tmp_path`` (section 26a requirement (e));
they are loaded through the same P1A-G1 path as the frozen Gate-B fixtures.
``tests/fixtures`` is never modified.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from pilot_support import offline_guard

from smart_beta.pilot import config as config_mod
from smart_beta.pilot.config import (
    ConfigError,
    ConfigNotApprovedError,
    ConfigPlaceholderError,
    ConfigValidationError,
    load_config,
    load_config_dict,
    resolve_config,
)
from smart_beta.pilot.contracts import (
    JournalKind,
    RunStatus,
)
from smart_beta.pilot.data import compute_fixture_hashes
from smart_beta.pilot.journal import read_journal
from smart_beta.pilot.model import ModelResponse, StubModelClient
from smart_beta.pilot.reconstruct import reconstruct
from smart_beta.pilot.runner import (
    GitProbe,
    build_reconstruction_hooks,
    run_pilot,
)
from smart_beta.research.policy import StopReason
from smart_beta.spec.factor_spec import (
    FactorInput,
    FactorSpec,
    MissingPolicy,
    to_dict as factor_spec_to_dict,
)
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)

pytestmark = pytest.mark.usefixtures("offline_guard")

_REPO_ROOT = Path(__file__).resolve().parent.parent

DRY_RUN_CONFIG = _REPO_ROOT / "pilot_configs" / "pilot1a-dryrun-v2.json"
TEMPLATE_CONFIG = _REPO_ROOT / "pilot_configs" / "pilot1a-v2.template.json"
#: The superseded v1 config files. They are preserved byte-for-byte and are
#: refused by the corrected preflight (section 26b).
LEGACY_DRY_RUN_CONFIG = _REPO_ROOT / "pilot_configs" / "pilot1a-dryrun.json"
LEGACY_TEMPLATE_CONFIG = _REPO_ROOT / "pilot_configs" / "pilot1a.template.json"

#: The byte-for-byte frozen SHA-256 of the superseded v1 config files. If either
#: changes, the H6-v1 failure record is no longer preserved.
LEGACY_DRY_RUN_SHA256 = (
    "34f434c6a2a63462db93f0ea6c8886e9744c49af2e7ccc5ba4238cb6c87a94b4"
)
LEGACY_TEMPLATE_SHA256 = (
    "1c6b37cd3f1b86ba4ae6dfcecc07a7976182f9836a4a9c2f1571663e2b7da180"
)

CONSTRUCTED_TICKERS = ("AAA", "BBB", "CCC")
CONSTRUCTED_START = "2025-09-05"
CONSTRUCTED_END = "2026-06-30"
CONSTRUCTED_TREE_ID = "a" * 40


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _weekdays(start: str, end: str) -> list[dt.date]:
    current = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end)
    days: list[dt.date] = []
    while current <= last:
        if current.weekday() < 5:
            days.append(current)
        current += dt.timedelta(days=1)
    return days


def _write_synthetic_fixture(
    root: Path, tickers: tuple[str, ...], start: str, end: str
) -> Path:
    """Write a synthetic Tiingo-format fixture directory with a manifest."""
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {"recordings": {}}
    dates = _weekdays(start, end)
    for index, ticker in enumerate(tickers):
        meta = {"ticker": ticker, "name": f"{ticker} Inc", "exchangeCode": "NYSE"}
        meta_name = f"{ticker.lower()}_meta.json"
        (root / meta_name).write_text(json.dumps(meta), encoding="utf-8")
        manifest["recordings"][meta_name] = {  # type: ignore[index]
            "url_path": f"/tiingo/daily/{ticker}",
            "params": {},
            "status_code": 200,
        }
        rows = []
        price = 100.0 + index * 10.0
        for day_index, day in enumerate(dates):
            price *= 1.0 + 0.0005 * ((day_index % 5) - 2)
            rows.append(
                {
                    "date": day.isoformat() + "T00:00:00.000Z",
                    "close": round(price, 4),
                    "high": round(price * 1.01, 4),
                    "low": round(price * 0.99, 4),
                    "open": round(price * 0.995, 4),
                    "volume": 1_000_000 + day_index,
                    "adjClose": round(price, 4),
                    "adjHigh": round(price * 1.01, 4),
                    "adjLow": round(price * 0.99, 4),
                    "adjOpen": round(price * 0.995, 4),
                    "adjVolume": 1_000_000 + day_index,
                    "divCash": 0.0,
                    "splitFactor": 1.0,
                }
            )
        prices_name = f"{ticker.lower()}_eod.json"
        (root / prices_name).write_text(json.dumps(rows), encoding="utf-8")
        manifest["recordings"][prices_name] = {  # type: ignore[index]
            "url_path": f"/tiingo/daily/{ticker}/prices",
            "params": {"startDate": start, "endDate": end},
            "status_code": 200,
        }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _deep_merge(base: dict, overrides: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _candidate(
    *,
    factor_id: str,
    expression: str,
    lookback: int,
    intended_family_id: str | None = None,
) -> dict:
    requirement = DataRequirement(
        semantic_id="daily_total_return",
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.FRACTION,
        lookback=lookback,
        revision_policy=RevisionPolicy.POINT_IN_TIME,
        require_knowledge_date=True,
        require_positive_vintage_identity=False,
    )
    payload = factor_spec_to_dict(
        FactorSpec(
            id=factor_id,
            description=f"{factor_id} description",
            expression=expression,
            inputs=(FactorInput(alias="ret", requirement=requirement),),
            frequency=Frequency.DAILY,
            missing_policy=MissingPolicy.PROPAGATE,
        )
    )
    payload.pop("data_requirements", None)
    payload.pop("version", None)
    candidate: dict = {
        "factor_spec": payload,
        "research_question": f"Is {factor_id} informative?",
        "economic_rationale": "constructed harness fixture",
    }
    if intended_family_id is not None:
        candidate["intended_family_id"] = intended_family_id
    return candidate


def _default_script() -> list[dict]:
    """Three distinct, valid candidates (the same shape as the H6 script)."""
    return config_mod.build_stub_script()


def _constructed_config(
    tmp_path: Path,
    *,
    run_id: str,
    responses: list[dict] | None = None,
    budgets: dict | None = None,
) -> dict:
    fixture_dir = tmp_path / "constructed-fixture"
    if not fixture_dir.exists():
        _write_synthetic_fixture(
            fixture_dir, CONSTRUCTED_TICKERS, CONSTRUCTED_START, CONSTRUCTED_END
        )
    hashes = compute_fixture_hashes(fixture_dir)
    base = config_mod.build_dry_run_config_dict()
    overrides: dict = {
        "run_id": run_id,
        "dataset": {
            "fixture_dir": str(fixture_dir),
            "fixture_tree_id": CONSTRUCTED_TREE_ID,
            "file_hashes": [digest.to_dict() for digest in hashes],
            "universe": list(CONSTRUCTED_TICKERS),
            "start": CONSTRUCTED_START,
            "end": CONSTRUCTED_END,
            "date_cap": config_mod.DRY_RUN_DATE_CAP,
        },
        "artifact_destination": str(tmp_path / f"run-{run_id}"),
        "model": {
            "stub_responses": responses if responses is not None else _default_script()
        },
    }
    merged = _deep_merge(base, overrides)
    if budgets is not None:
        merged["budgets"] = _deep_merge(merged["budgets"], budgets)
        merged["research_policy"]["max_llm_token_budget"] = merged["budgets"][
            "llm_tokens"
        ]
        merged["research_policy"]["max_llm_cost_budget"] = merged["budgets"]["llm_cost"]
        # The serialized policy carries its computed content hash; drop it so
        # the sealed constructor recomputes it from the edited fields.
        merged["research_policy"].pop("content_hash", None)
    return merged


class FakeGit(GitProbe):
    """A deterministic git probe for offline tests."""

    def __init__(
        self,
        *,
        head: str = "f" * 40,
        ancestor: bool = True,
        clean: bool = True,
        tree_id: str = CONSTRUCTED_TREE_ID,
    ) -> None:
        super().__init__(".")
        self._head = head
        self._ancestor = ancestor
        self._clean = clean
        self._tree = tree_id

    def head_commit(self) -> str:
        return self._head

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return self._ancestor

    def is_clean(self) -> bool:
        return self._clean

    def tree_id(self, path: str) -> str:
        return self._tree


def _run(
    config_dict: dict,
    tmp_path: Path,
    *,
    git: GitProbe | None = None,
    approved_hash: str | None = None,
    client=None,
    **kwargs,
):
    config = load_config_dict(config_dict)
    return run_pilot(
        config,
        approved_config_hash=(
            config.config_hash() if approved_hash is None else approved_hash
        ),
        repo=tmp_path,
        git=git if git is not None else FakeGit(),
        client=client,
        **kwargs,
    )


class ExplodingClient:
    """A stub-shaped client that fails after the durable write-ahead intent."""

    def complete(self, request):  # noqa: ANN001
        raise RuntimeError("constructed infrastructure failure")


def _crashed_predecessor(tmp_path: Path, run_id: str):
    """Run one experiment-free INTERRUPTED predecessor journal."""
    config_dict = _constructed_config(tmp_path, run_id=run_id)
    outcome = _run(config_dict, tmp_path, client=ExplodingClient())
    assert outcome.status is RunStatus.INTERRUPTED
    return config_dict, outcome


# ---------------------------------------------------------------------------
# config file integrity and resolution
# ---------------------------------------------------------------------------


def test_committed_dry_run_config_matches_the_builder():
    on_disk = json.loads(DRY_RUN_CONFIG.read_text(encoding="utf-8"))
    assert on_disk == config_mod.build_dry_run_config_dict()


def test_committed_template_matches_the_builder():
    on_disk = json.loads(TEMPLATE_CONFIG.read_text(encoding="utf-8"))
    assert on_disk == config_mod.build_real_run_template_dict()


def test_dry_run_config_resolves_to_the_frozen_variant():
    config = load_config(DRY_RUN_CONFIG)
    resolved = resolve_config(config)
    assert resolved.run_id == "pilot1a-dryrun-v2"
    assert resolved.run_mode == "dry_run"
    assert resolved.model.provider == config_mod.STUB_PROVIDER
    assert resolved.model.model_id == "pilot1a-stub-v1"
    assert resolved.partition_dates == config_mod.DRY_RUN_PARTITION
    assert resolved.dataset.date_cap == config_mod.DRY_RUN_DATE_CAP
    assert resolved.research_policy.family_id == config_mod.DRY_RUN_FAMILY_ID
    assert resolved.evaluation_spec_template.cost_model.transaction_cost_bps == 10.0
    assert resolved.prompt_template_hash == config.prompt_template_hash
    assert resolved.budgets.invocation_ceiling == 5
    # Section-26b correction 1/2/3: no parameter sensitivity, a single primary
    # parameter point and the config's own subperiod boundaries.
    from smart_beta.evaluation.spec import MetricKey

    assert MetricKey.PARAMETER_SENSITIVITY not in (
        resolved.evaluation_spec_template.metrics
    )
    assert len(resolved.evaluation_spec_template.parameter_grid) == 1
    boundaries = resolved.evaluation_spec_template.subperiod_rule.boundaries
    assert boundaries[-1] == resolved.partition_dates.holdout_start
    from smart_beta.experiment.policy import EvidenceSection

    assert EvidenceSection.PARAMETER_SENSITIVITY_TABLE not in (
        resolved.decision_policy.required_evidence
    )


def test_real_run_template_is_refused_by_placeholders():
    config = load_config(TEMPLATE_CONFIG)
    with pytest.raises(ConfigPlaceholderError) as excinfo:
        resolve_config(config)
    assert len(excinfo.value.paths) >= 5
    assert config.to_dict()["security"]["approved"] is False


def test_unapproved_config_is_refused():
    payload = config_mod.build_dry_run_config_dict()
    payload["security"]["approved"] = False
    config = load_config_dict(payload)
    with pytest.raises(ConfigNotApprovedError):
        resolve_config(config)


def test_family_identity_mismatch_is_refused():
    payload = config_mod.build_dry_run_config_dict()
    payload["family_id"] = "f" * 64
    config = load_config_dict(payload)
    with pytest.raises(ConfigValidationError):
        resolve_config(config)


def test_non_stub_provider_is_refused():
    payload = config_mod.build_dry_run_config_dict()
    payload["model"]["provider"] = "anthropic"
    payload["model"]["id"] = "claude-x"
    payload["research_policy"]["generator_identity"] = "claude-x"
    config = load_config_dict(payload)
    with pytest.raises(ConfigValidationError):
        resolve_config(config)


def test_evaluation_template_tampering_is_refused():
    payload = config_mod.build_dry_run_config_dict()
    payload["evaluation_spec_template"]["cost_model"]["transaction_cost_bps"] = 25.0
    # Recompute the spec_hash so only the semantic tamper remains.
    from smart_beta.pilot.design import build_frozen_evaluation_spec_template

    tampered = payload["evaluation_spec_template"]
    tampered.pop("spec_hash", None)
    config = load_config_dict(payload)
    with pytest.raises(ConfigValidationError):
        resolve_config(config)
    # The frozen template is untouched.
    assert (
        build_frozen_evaluation_spec_template(
            config_mod.DRY_RUN_PARTITION
        ).cost_model.transaction_cost_bps
        == 10.0
    )


def test_legacy_v1_configs_are_byte_for_byte_unchanged():
    """The H6-v1 failure record is preserved: the v1 files never change."""
    assert hashlib.sha256(LEGACY_DRY_RUN_CONFIG.read_bytes()).hexdigest() == (
        LEGACY_DRY_RUN_SHA256
    )
    assert hashlib.sha256(LEGACY_TEMPLATE_CONFIG.read_bytes()).hexdigest() == (
        LEGACY_TEMPLATE_SHA256
    )


def test_legacy_v1_dry_run_is_refused_for_the_superseded_template():
    """The corrected preflight refuses the v1 dry run, not silently upgrades it."""
    config = load_config(LEGACY_DRY_RUN_CONFIG)
    payload = config.to_dict()
    # The v1 file genuinely carries the section-26b defects.
    assert "parameter_sensitivity" in payload["evaluation_spec_template"]["metrics"]
    assert (
        payload["evaluation_spec_template"]["subperiod_rule"]["boundaries"][-1]
        == "2026-07-01"
    )
    with pytest.raises(ConfigValidationError) as excinfo:
        resolve_config(config)
    assert "parameter_sensitivity" in str(excinfo.value)


def test_legacy_v1_template_is_refused():
    config = load_config(LEGACY_TEMPLATE_CONFIG)
    with pytest.raises(ConfigError):
        resolve_config(config)


def test_legacy_v1_configs_fail_preflight_with_zero_model_calls(tmp_path):
    client = StubModelClient(ModelResponse("{}", "m", "end_turn", 1, 1))
    for path in (LEGACY_DRY_RUN_CONFIG, LEGACY_TEMPLATE_CONFIG):
        config = load_config(path)
        outcome = run_pilot(
            config,
            approved_config_hash=config.config_hash(),
            repo=tmp_path,
            git=FakeGit(),
            client=client,
        )
        assert outcome.status is RunStatus.FAILED_PREFLIGHT
        assert outcome.journal_path is None
    assert client.calls == ()


def test_parameter_sensitivity_required_evidence_is_refused():
    payload = config_mod.build_dry_run_config_dict()
    payload["decision_policy"]["required_evidence"].append(
        "parameter_sensitivity_table"
    )
    payload["decision_policy"].pop("content_hash", None)
    config = load_config_dict(payload)
    with pytest.raises(ConfigValidationError) as excinfo:
        resolve_config(config)
    assert "parameter_sensitivity" in str(excinfo.value)


# ---------------------------------------------------------------------------
# preflight refusals (zero model calls)
# ---------------------------------------------------------------------------


def test_config_hash_mismatch_refuses_before_any_model_call(tmp_path):
    client = StubModelClient(ModelResponse("{}", "m", "end_turn", 1, 1))
    config_dict = _constructed_config(tmp_path, run_id="preflight-hash")
    outcome = _run(
        config_dict, tmp_path, approved_hash="0" * 64, client=client
    )
    assert outcome.status is RunStatus.FAILED_PREFLIGHT
    assert outcome.journal_path is None
    assert client.calls == ()


def test_wrong_baseline_refuses(tmp_path):
    config_dict = _constructed_config(tmp_path, run_id="preflight-baseline")
    outcome = _run(config_dict, tmp_path, git=FakeGit(ancestor=False))
    assert outcome.status is RunStatus.FAILED_PREFLIGHT
    assert "descend" in outcome.detail


def test_dirty_tree_refuses(tmp_path):
    config_dict = _constructed_config(tmp_path, run_id="preflight-dirty")
    outcome = _run(config_dict, tmp_path, git=FakeGit(clean=False))
    assert outcome.status is RunStatus.FAILED_PREFLIGHT
    assert "dirty" in outcome.detail


def test_tree_id_mismatch_refuses(tmp_path):
    config_dict = _constructed_config(tmp_path, run_id="preflight-tree")
    outcome = _run(config_dict, tmp_path, git=FakeGit(tree_id="b" * 40))
    assert outcome.status is RunStatus.FAILED_PREFLIGHT
    assert "tree id" in outcome.detail


def test_placeholder_config_refuses_before_any_model_call(tmp_path):
    client = StubModelClient(ModelResponse("{}", "m", "end_turn", 1, 1))
    config = load_config(TEMPLATE_CONFIG)
    outcome = run_pilot(
        config,
        approved_config_hash="0" * 64,
        repo=tmp_path,
        git=FakeGit(),
        client=client,
    )
    assert outcome.status is RunStatus.FAILED_PREFLIGHT
    assert client.calls == ()


def test_existing_artifact_directory_is_refused_untouched(tmp_path):
    """A run_id/artifact directory that already exists is never reused."""
    config_dict = _constructed_config(tmp_path, run_id="existing-run")
    first = _run(config_dict, tmp_path)
    assert first.status is RunStatus.COMPLETED_STOP
    journal_before = read_journal(first.journal_path).records
    client = StubModelClient(ModelResponse("{}", "m", "end_turn", 1, 1))
    second = _run(config_dict, tmp_path, client=client)
    assert second.status is RunStatus.FAILED_PREFLIGHT
    assert "already exists" in second.detail
    assert client.calls == ()
    assert read_journal(first.journal_path).records == journal_before


def test_existing_canonical_run_id_directory_is_refused(tmp_path):
    """The canonical ``pilot_runs/pilot1a/<run_id>`` is reserved even off-path."""
    config_dict = _constructed_config(tmp_path, run_id="canonical-existing")
    (
        tmp_path / "pilot_runs" / "pilot1a" / "canonical-existing"
    ).mkdir(parents=True)
    client = StubModelClient(ModelResponse("{}", "m", "end_turn", 1, 1))
    outcome = _run(config_dict, tmp_path, client=client)
    assert outcome.status is RunStatus.FAILED_PREFLIGHT
    assert "already exists" in outcome.detail
    assert client.calls == ()


# ---------------------------------------------------------------------------
# the typed STOP path
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def completed_run(tmp_path_factory):
    base = tmp_path_factory.mktemp("runner-completed")
    config_dict = _constructed_config(base, run_id="runner-completed")
    outcome = _run(config_dict, base)
    return base, config_dict, outcome


def test_full_run_reaches_the_frozen_proposal_budget_stop(completed_run):
    _, _, outcome = completed_run
    assert outcome.status is RunStatus.COMPLETED_STOP
    assert outcome.stop_reason == StopReason.PROPOSAL_BUDGET_EXHAUSTED.value
    assert outcome.invocation_count == 3
    assert outcome.reconstruction_status == "RECONSTRUCTION_EXACT"
    assert outcome.firewall_audit_status == "PASS"
    assert outcome.secret_sweep_status == "PASS"
    assert outcome.package_error is None


def test_completed_run_package_is_complete(completed_run):
    from smart_beta.pilot.artifacts import verify_package

    base, _, outcome = completed_run
    assert outcome.artifact_directory is not None
    verify_package(outcome.artifact_directory)
    manifest = json.loads(
        (outcome.artifact_directory / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == RunStatus.COMPLETED_STOP.value
    assert manifest["run_id"] == "runner-completed"


def test_shared_journal_chain_is_contiguous(completed_run):
    _, _, outcome = completed_run
    read = read_journal(outcome.journal_path)
    assert read.run_id == "runner-completed"
    assert not read.truncated
    for index, record in enumerate(read.records):
        assert record.seq == index
    assert read.records[0].kind is JournalKind.RUN_STARTED
    assert read.records[-1].kind is JournalKind.RUN_CLOSED


def test_precall_snapshots_precede_every_generation(completed_run):
    _, _, outcome = completed_run
    read = read_journal(outcome.journal_path)
    seen: dict[str, set[str]] = {"visible_history": set(), "research_feedback": set()}
    for record in read.records:
        payload = record.to_dict()["payload"]
        if record.kind is JournalKind.AUTHORITY_SNAPSHOT:
            name = payload.get("name")
            if name in seen:
                snapshot = payload["snapshot"]
                declared = snapshot.get("content_hash") or snapshot.get("snapshot_hash")
                seen[name].add(str(declared))
        elif record.kind is JournalKind.INVOCATION_INTENT:
            intent = payload
            assert intent["visible_history_hash"] in seen["visible_history"]
            assert intent["research_feedback_hash"] in seen["research_feedback"]


def test_normalization_reconstructs_without_unwired_hooks(completed_run):
    base, config_dict, outcome = completed_run
    config = load_config_dict(config_dict)
    resolved = resolve_config(config)
    from smart_beta.pilot.config import load_pilot_data

    pilot_data = load_pilot_data(resolved, repo=base)
    hooks = build_reconstruction_hooks(resolved, pilot_data)
    report = reconstruct(outcome.journal_path, hooks=hooks)
    assert report.status.value == "RECONSTRUCTION_EXACT"
    assert not any("not wired" in note for note in report.notes)
    derivations = {result.stage for result in report.derivations}
    assert "evaluation" in derivations
    assert "decision" in derivations


def test_holdout_is_consumed_once_and_later_experiments_defer(completed_run):
    from smart_beta.experiment.policy import DecisionRecord

    _, _, outcome = completed_run
    read = read_journal(outcome.journal_path)
    decisions = [
        DecisionRecord.from_dict(record.to_dict()["payload"]["decision_record"])
        for record in read.records
        if record.kind is JournalKind.ORCHESTRATION_OUTCOME
    ]
    assert len(decisions) == 3
    assert decisions[0].holdout_governance.prior_consumption is not None
    assert decisions[1].decision.value == "defer"
    assert "holdout_previously_consumed" in {
        code.value for code in decisions[1].reason_codes
    }
    assert decisions[2].decision.value == "defer"


# ---------------------------------------------------------------------------
# ceilings and interruptions
# ---------------------------------------------------------------------------


def test_invocation_ceiling_interrupts(tmp_path):
    config_dict = _constructed_config(
        tmp_path, run_id="ceiling-invocation", budgets={"invocation_ceiling": 1}
    )
    outcome = _run(config_dict, tmp_path)
    assert outcome.status is RunStatus.INTERRUPTED
    assert "invocation ceiling" in outcome.detail
    assert outcome.invocation_count == 1
    read = read_journal(outcome.journal_path)
    assert read.records[-1].kind is JournalKind.INTERRUPTED
    assert outcome.reconstruction_status == "RECONSTRUCTION_EXACT"


def test_wall_clock_ceiling_interrupts_before_any_generate(tmp_path):
    config_dict = _constructed_config(
        tmp_path, run_id="ceiling-wallclock", budgets={"wall_clock_seconds": 0.0}
    )
    outcome = _run(config_dict, tmp_path)
    assert outcome.status is RunStatus.INTERRUPTED
    assert "wall-clock" in outcome.detail
    assert outcome.invocation_count == 0
    assert outcome.reconstruction_status == "RECONSTRUCTION_EXACT"


def test_llm_token_ceiling_interrupts(tmp_path):
    config_dict = _constructed_config(
        tmp_path, run_id="ceiling-tokens", budgets={"llm_tokens": 1}
    )
    outcome = _run(config_dict, tmp_path)
    assert outcome.status is RunStatus.INTERRUPTED
    assert "LLM token ceiling" in outcome.detail
    assert outcome.invocation_count == 1
    assert outcome.reconstruction_status == "RECONSTRUCTION_EXACT"


def test_injected_infrastructure_exception_yields_interrupted(tmp_path):
    config_dict = _constructed_config(tmp_path, run_id="crash-infra")
    outcome = _run(config_dict, tmp_path, client=ExplodingClient())
    assert outcome.status is RunStatus.INTERRUPTED
    assert "RuntimeError" in outcome.detail
    read = read_journal(outcome.journal_path)
    kinds = [record.kind for record in read.records]
    assert kinds[-1] is JournalKind.INTERRUPTED
    assert JournalKind.INVOCATION_INTENT in kinds
    assert kinds.count(JournalKind.INVOCATION_RESULT) == 0
    assert outcome.reconstruction_status == "RECONSTRUCTION_EXACT"


def test_firewall_violation_escaping_generate_is_a_typed_stop(tmp_path, monkeypatch):
    from smart_beta.pilot import model as model_mod
    from smart_beta.pilot.firewall import FirewallViolation

    def _violate(*args, **kwargs):  # noqa: ANN002, ANN003
        raise FirewallViolation("constructed firewall violation")

    monkeypatch.setattr(model_mod, "audit_generator_inputs", _violate)
    config_dict = _constructed_config(tmp_path, run_id="firewall-violation")
    outcome = _run(config_dict, tmp_path)
    assert outcome.status is RunStatus.COMPLETED_STOP
    assert outcome.stop_reason == StopReason.HOLDOUT_FIREWALL_VIOLATION.value
    assert outcome.invocation_count == 0


def _constructed_requirement() -> DataRequirement:
    return DataRequirement(
        semantic_id="daily_total_return",
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.FRACTION,
        lookback=0,
        revision_policy=RevisionPolicy.POINT_IN_TIME,
        require_knowledge_date=True,
        require_positive_vintage_identity=False,
    )


def _holdout_overlapping_record(tmp_path: Path):
    """A real sealed ``EvaluationRecord`` whose subperiod crosses the holdout.

    The subperiod rule is extended one calendar day past the dry-run holdout
    start, so the first subperiod row ``[2026-01-02, 2026-05-02)`` overlaps the
    reserved holdout fold ``[2026-05-01, ...)``. This is exactly the class of
    leak the section-26b temporal firewall must refuse.
    """
    from smart_beta.evaluation.engine import evaluate
    from smart_beta.evaluation.spec import SubperiodRule
    from smart_beta.pilot.data import (
        DAILY_TOTAL_RETURN_REQUIREMENT,
        load_pit_inputs,
    )
    from smart_beta.pilot.design import (
        PILOT_PERIODS_PER_YEAR,
        build_frozen_evaluation_spec_template,
        build_partition,
    )
    from smart_beta.spec.engine import evaluate_factor

    fixture_dir = tmp_path / "tf-overlap-fixture"
    if not fixture_dir.exists():
        _write_synthetic_fixture(
            fixture_dir, CONSTRUCTED_TICKERS, CONSTRUCTED_START, CONSTRUCTED_END
        )
    pilot_data = load_pit_inputs(
        fixture_dir=fixture_dir,
        expected_fixture_hashes=compute_fixture_hashes(fixture_dir),
        universe=CONSTRUCTED_TICKERS,
        start=CONSTRUCTED_START,
        end=CONSTRUCTED_END,
        requirement=DAILY_TOTAL_RETURN_REQUIREMENT,
        fixture_tree_id=CONSTRUCTED_TREE_ID,
        date_cap=config_mod.DRY_RUN_DATE_CAP,
    )
    dates = config_mod.DRY_RUN_PARTITION
    template = build_frozen_evaluation_spec_template(dates)
    overlap = dates.holdout_start + dt.timedelta(days=1)
    spec_template = dataclasses.replace(
        template,
        subperiod_rule=SubperiodRule(
            boundaries=(dates.is_start, dt.date(2026, 1, 2), overlap)
        ),
    )
    factor_spec = FactorSpec(
        id="tf_overlap_identity",
        description="identity of the admitted daily total return",
        expression="ret",
        inputs=(FactorInput(alias="ret", requirement=_constructed_requirement()),),
        frequency=Frequency.DAILY,
        missing_policy=MissingPolicy.PROPAGATE,
    )
    inputs = {
        alias: pilot_data.trusted_input for alias in factor_spec.referenced_roles
    }
    engine_result = evaluate_factor(factor_spec, inputs)
    spec = dataclasses.replace(
        spec_template, factor_provenance_hash=engine_result.content_hash
    )
    return evaluate(
        engine_result.evaluation.panel,
        spec,
        pilot_data.realized_returns,
        build_partition(dates),
        periods_per_year=PILOT_PERIODS_PER_YEAR,
    )


def _visible_from_record(record) -> object:
    from smart_beta.research.history import (
        DevelopmentEvidenceRecord,
        GeneratorVisibleResearchHistory,
        VisibleExperiment,
        VisibleFamily,
    )

    evidence = DevelopmentEvidenceRecord.from_evaluation_record(
        record, experiment_id="e" * 64
    )
    experiment = VisibleExperiment(
        experiment_id="e" * 64,
        hypothesis_id="b" * 64,
        family_id=config_mod.DRY_RUN_FAMILY_ID,
        factor_provenance_hash=record.factor_provenance_hash,
        evaluation_spec_hash=record.spec_hash,
        fold_evidence=evidence.fold_evidence,
        redundancy=evidence.redundancy,
        robustness_tables=evidence.robustness_tables,
    )
    return GeneratorVisibleResearchHistory(
        experiments=(experiment,),
        families=(
            VisibleFamily(
                family_id=config_mod.DRY_RUN_FAMILY_ID, consumed_slots=1
            ),
        ),
    )


def test_pre_call_temporal_firewall_stops_on_holdout_overlap(tmp_path, monkeypatch):
    """A holdout-overlapping subperiod in the pre-call history is a typed stop.

    The real G6R firewall is exercised: the test poisons the exact pre-call
    projection with a subperiod backed by a real sealed ``EvaluationRecord``
    that crosses the dry-run holdout start, and injects that record as the
    temporal-coverage authority. The runner must map the violation to the typed
    ``HOLDOUT_FIREWALL_VIOLATION`` stop with zero model calls.
    """
    from smart_beta.pilot import runner as runner_mod
    from smart_beta.pilot import temporal as temporal_mod
    from smart_beta.research.history import ResearchFeedback
    from smart_beta.research.loop import ResearchLoop

    record = _holdout_overlapping_record(tmp_path)
    poisoned_visible = _visible_from_record(record)
    poisoned_feedback = ResearchFeedback()

    original_snapshot = ResearchLoop.snapshot_history

    def poisoned_snapshot(self, full_history, **kwargs):
        original_snapshot(self, full_history, **kwargs)
        self._visible = poisoned_visible
        self._feedback = poisoned_feedback
        return poisoned_visible

    monkeypatch.setattr(ResearchLoop, "snapshot_history", poisoned_snapshot)

    captured: list = []
    real_enforce = temporal_mod.enforce_temporal_firewall

    def injecting(visible, feedback, records, *, is_start, holdout_start):
        try:
            return real_enforce(
                visible,
                feedback,
                [record],
                is_start=is_start,
                holdout_start=holdout_start,
            )
        except temporal_mod.TemporalFirewallViolation as exc:
            captured.append(exc)
            raise

    monkeypatch.setattr(runner_mod, "enforce_temporal_firewall", injecting)

    config_dict = _constructed_config(tmp_path, run_id="tf-overlap")
    client = StubModelClient(ModelResponse("{}", "m", "end_turn", 1, 1))
    outcome = _run(config_dict, tmp_path, client=client)

    assert outcome.status is RunStatus.COMPLETED_STOP
    assert outcome.stop_reason == StopReason.HOLDOUT_FIREWALL_VIOLATION.value
    assert outcome.invocation_count == 0
    assert client.calls == ()
    # The real firewall raised with a subperiod row crossing the holdout start.
    assert captured, "the real temporal firewall must have raised"
    audit = captured[0].audit
    assert audit is not None
    holdout_iso = config_mod.DRY_RUN_PARTITION.holdout_start.isoformat()
    overlap_rows = [
        row
        for row in audit.rows
        if row.category == "subperiod"
        and row.source_end_exclusive is not None
        and row.source_end_exclusive > holdout_iso
    ]
    assert overlap_rows, [row.to_dict() for row in audit.rows]
    # The poisoned snapshot is journaled, so the post-hoc audit also refuses it.
    assert outcome.package_error is not None
    assert "temporal" in outcome.package_error.lower()


def test_no_hidden_retry(completed_run):
    _, _, outcome = completed_run
    read = read_journal(outcome.journal_path)
    intents = [
        record for record in read.records if record.kind is JournalKind.INVOCATION_INTENT
    ]
    assert len(intents) == outcome.invocation_count == 3
    ordinals = [record.to_dict()["payload"]["ordinal"] for record in intents]
    assert ordinals == [0, 1, 2]
    invocation_ids = {
        record.to_dict()["payload"]["invocation_id"] for record in intents
    }
    assert len(invocation_ids) == len(intents)


# ---------------------------------------------------------------------------
# section-17 retry rule
# ---------------------------------------------------------------------------


def test_retry_after_an_experiment_free_predecessor_is_allowed(tmp_path):
    _, predecessor = _crashed_predecessor(tmp_path, "retry-predecessor")
    assert predecessor.status is RunStatus.INTERRUPTED
    # The predecessor registered no experiment.
    read = read_journal(predecessor.journal_path)
    assert not any(
        record.kind is JournalKind.ORCHESTRATION_OUTCOME for record in read.records
    )

    retry_dict = _constructed_config(tmp_path, run_id="retry-successor")
    retry = _run(
        retry_dict,
        tmp_path,
        predecessor_run_id="retry-predecessor",
        predecessor_journal_path=predecessor.journal_path,
    )
    assert retry.status is RunStatus.COMPLETED_STOP
    retry_read = read_journal(retry.journal_path)
    started = next(
        record for record in retry_read.records if record.kind is JournalKind.RUN_STARTED
    )
    assert started.to_dict()["payload"]["predecessor_run_id"] == "retry-predecessor"


def test_retry_after_an_experiment_is_refused(tmp_path, completed_run):
    _, _, predecessor = completed_run
    retry_dict = _constructed_config(tmp_path, run_id="retry-forbidden")
    outcome = _run(
        retry_dict,
        tmp_path,
        predecessor_run_id="runner-completed",
        predecessor_journal_path=predecessor.journal_path,
    )
    assert outcome.status is RunStatus.FAILED_PREFLIGHT
    assert "retry" in outcome.detail.lower()


def test_retry_of_a_retry_is_refused(tmp_path):
    _, first = _crashed_predecessor(tmp_path, "lineage-a")
    second_dict = _constructed_config(tmp_path, run_id="lineage-b")
    second = _run(
        second_dict,
        tmp_path,
        client=ExplodingClient(),
        predecessor_run_id="lineage-a",
        predecessor_journal_path=first.journal_path,
    )
    assert second.status is RunStatus.INTERRUPTED
    third_dict = _constructed_config(tmp_path, run_id="lineage-c")
    third = _run(
        third_dict,
        tmp_path,
        predecessor_run_id="lineage-b",
        predecessor_journal_path=second.journal_path,
    )
    assert third.status is RunStatus.FAILED_PREFLIGHT
    assert "at most one retry" in third.detail


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_help_lists_config_and_approved_hash():
    result = subprocess.run(
        [sys.executable, "scripts/run_pilot1a.py", "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--config" in result.stdout
    assert "--approved-config-hash" in result.stdout


def test_cli_refuses_the_unfilled_template(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_pilot1a.py",
            "--config",
            str(TEMPLATE_CONFIG),
            "--approved-config-hash",
            hashlib.sha256(b"nothing").hexdigest(),
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode != 0
    assert "placeholder" in result.stdout.lower() or "placeholder" in result.stderr.lower()
