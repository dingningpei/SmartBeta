"""C1-LX Campaign executor tests (synthetic fixtures only).

Authority: ``worker_tasks/campaign1/task-c1-ledger-executor.md`` (C1-LX) plus
errata C1-LX-E1, C1-LX-E2, C1-LX-E3 and C1-LX-E4, governed by
``docs/campaign1_identity_architecture_correction.md``. No provider/network
calls, no reserve reads, no real factor evaluation, no real PQB build, no
Knowledge-PIT append. Hypothesis identity is exercised with a synthetic D7c
manifest fixture (the real manifest is read only by
``tests/test_campaign1_identity.py``).
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smart_beta.campaign import campaign1 as C
from smart_beta.campaign import ledger as L
from smart_beta.dataset_v1 import pq_evaluation as PQ
from smart_beta.experiment.policy import SearchPolicy, SearchPolicyError
from smart_beta.experiment.registry import (
    canonical_json,
    content_hash,
    experiment_id_for,
    hypothesis_id_for,
)
from smart_beta.science.contracts import RecordKind
from smart_beta.science.knowledge import GENESIS_PREV_HASH, KnowledgeRecord
from smart_beta.spec.engine import EvidenceClass, TrustedInput
from smart_beta.spec.requirements import (
    DataCapability,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)

DECL = "d" * 64
ZERO = "0" * 64

_REPO_ROOT = Path(C.__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Synthetic D7c manifest fixture (E4 definition-based hypothesis identity)
# ---------------------------------------------------------------------------

_FIXTURE_MANIFEST: dict[str, str] = {
    "ti_id": "ti-0000000000000000",
    "ti_hash": "a" * 64,
    "note": "synthetic E4 identity fixture",
}
_FIXTURE_MANIFEST_BYTES = (
    canonical_json(_FIXTURE_MANIFEST) + "\n"
).encode("utf-8")
_FIXTURE_MANIFEST_SHA256 = hashlib.sha256(_FIXTURE_MANIFEST_BYTES).hexdigest()


def _fixture_engine_semantics() -> dict[str, str]:
    return {
        relative: hashlib.sha256((_REPO_ROOT / relative).read_bytes()).hexdigest()
        for relative in C.ENGINE_SEMANTICS_FILES
    }


def _fixture_definition(spec) -> dict:
    return {
        "schema": C.HYPOTHESIS_DEFINITION_SCHEMA,
        "factor_spec_hash": C.factor_spec_hash(spec),
        "trusted_inputs": {
            "ti_id": _FIXTURE_MANIFEST["ti_id"],
            "ti_hash": _FIXTURE_MANIFEST["ti_hash"],
            "manifest_sha256": _FIXTURE_MANIFEST_SHA256,
        },
        "engine_semantics": _fixture_engine_semantics(),
    }


def _fixture_hypothesis_ids() -> dict[str, str]:
    return {
        spec.hypothesis: C.campaign1_hypothesis_id(
            C.hypothesis_definition_hash(_fixture_definition(spec))
        )
        for spec in C.FACTOR_SPECS
    }


_FIXTURE_HYPOTHESIS_IDS = _fixture_hypothesis_ids()


def _fixture_identity_records() -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "label": spec.hypothesis,
            "factor_spec_hash": C.factor_spec_hash(spec),
            "hypothesis_definition_hash": C.hypothesis_definition_hash(
                _fixture_definition(spec)
            ),
            "hypothesis_id": _FIXTURE_HYPOTHESIS_IDS[spec.hypothesis],
        }
        for spec in C.FACTOR_SPECS
    )


def _write_fixture_manifest(root) -> None:
    path = Path(root) / C.TI_MANIFEST_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_FIXTURE_MANIFEST_BYTES)


# ---------------------------------------------------------------------------
# Synthetic PQB objects
# ---------------------------------------------------------------------------


def _cell(
    name: str,
    *,
    value: float | None,
    t_stat: float | None,
    n_obs: int,
    status: str | None = None,
    declaration_hash: str = DECL,
) -> PQ.PQCell:
    scenario = name.split("-")[1]
    estimand = "ORIGINAL" if name.startswith("B-") else "QUARANTINED"
    return PQ.PQCell(
        cell=name,
        scenario=scenario,
        estimand=estimand,
        value=value,
        raw_value=value,
        t_stat=t_stat,
        status=status,
        n_obs=n_obs,
        n_index=n_obs,
        availability_mask_hash="a" * 64,
        declaration_hash=declaration_hash,
    )


def _default_cells(declaration_hash: str = DECL) -> tuple[PQ.PQCell, ...]:
    return tuple(
        _cell(
            name,
            value=0.01,
            t_stat=3.0,
            n_obs=30,
            declaration_hash=declaration_hash,
        )
        for name in ("B-T0", "B-TS", "A-T0", "A-TS")
    )


def _diag(
    *,
    declaration_hash: str = DECL,
    not_assessed: str | None = None,
    cells: tuple[PQ.PQCell, ...] | None = None,
    orientation_rule: str = "PASSTHROUGH",
    screen_id: str = C.SCREEN_ID,
    screen_policy_hash: str = C.SCREEN_POLICY_SHA256,
) -> PQ.PQDiagnostics:
    if cells is None:
        cells = _default_cells(declaration_hash)
    return PQ.PQDiagnostics(
        not_assessed=not_assessed,
        declaration_hash=declaration_hash,
        primary_metric="turnover_cost_adjusted",
        basis="NET",
        sign=1,
        orientation_rule=orientation_rule,
        screen_id=screen_id,
        screen_policy_hash=screen_policy_hash,
        index=(),
        cells=cells,
        b_diagnostics_hash=None,
        e_s=1.0,
        b=1.0,
        q_b=0.0,
        q_s=0.0,
        q_l=0.0,
        n_flagged_formations=0,
        residual_cs_cells=0,
        liquidity_exposed=0,
        n_interior_b_missing=0,
        support=(),
        cs3=(),
        cs3_status=None,
        b_diagnostics=None,
    )


@dataclass
class _Classify:
    status: str = PQ.STATUS_NOT_SENSITIVE
    quarantine_sensitive: bool = False
    delisting_a: str = PQ.DELISTING_NOT_SENSITIVE
    delisting_b: str = PQ.DELISTING_NOT_SENSITIVE

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "quarantine_sensitive": self.quarantine_sensitive,
            "delisting_a": self.delisting_a,
            "delisting_b": self.delisting_b,
        }


# ---------------------------------------------------------------------------
# Synthetic executor inputs and stubs
# ---------------------------------------------------------------------------


def _bindings(**overrides) -> C.CampaignBindings:
    base = dict(
        dataset_id="discovery_dataset_v1_2",
        dataset_identity_hash="a" * 64,
        amendment_sha256="b" * 64,
        admission_sha256="c" * 64,
        pq_id="pq-test",
        pq_hash="d" * 64,
        quarantine_rule_hash="e" * 64,
        r3_identity_hash="f" * 64,
        pq_reporting_sha256="1" * 64,
        pq_evaluation_sha256="2" * 64,
        partition_id=C.EXPECTED_PARTITION_ID,
    )
    base.update(overrides)
    return C.CampaignBindings(**base)


def _panel(spec, universe, fold) -> pd.DataFrame:
    formation = pd.Timestamp(fold.start.date())
    return pd.DataFrame(
        {
            "date": [formation, formation],
            "stock_id": ["AAA", "BBB"],
            "value": [0.1, 0.2],
        }
    )


def _realization(spec) -> str:
    return hashlib.sha256(spec.id.encode("utf-8")).hexdigest()


def _make_executor(
    tmp_path,
    *,
    realization_of=_realization,
    panel_of=_panel,
    o2_count_of=None,
    bindings=None,
    labels=None,
    knowledge_log_path=None,
    expected_realizations=None,
):
    _write_fixture_manifest(tmp_path)
    ledger = L.CampaignLedger(Path(tmp_path) / "ledger.jsonl")
    _LEDGER_PATHS.append(ledger.path)
    if o2_count_of is None:
        o2_count_of = lambda spec: 0  # noqa: E731
    if expected_realizations is None:
        expected_realizations = {
            spec.hypothesis: realization_of(spec) for spec in C.FACTOR_SPECS
        }
    return C.CampaignExecutor(
        ledger=ledger,
        bindings=bindings or _bindings(),
        partition=C.build_partition(),
        factor_specs=C.FACTOR_SPECS,
        realization_of=realization_of,
        panel_of=panel_of,
        overlay=object(),
        root=Path(tmp_path),
        inputs_by_semantic={},
        o2_count_of=o2_count_of,
        expected_realizations=expected_realizations,
        knowledge_log_path=knowledge_log_path,
        labels=labels,
    )


_LEDGER_PATHS: list[Path] = []


def _assert_ledger_invariant(path: Path) -> None:
    """E3.3 test 5: OPEN first/once, STOP at most once and last."""
    entries = L.CampaignLedger(path).verify()
    types = [entry.record_type for entry in entries]
    if not types:
        return
    assert types[0] == "CAMPAIGN_OPEN", types
    assert types.count("CAMPAIGN_OPEN") == 1, types
    assert types.count("CAMPAIGN_STOP") <= 1, types
    if "CAMPAIGN_STOP" in types:
        assert types[-1] == "CAMPAIGN_STOP", types


@pytest.fixture(autouse=True)
def _ledger_invariant():
    _LEDGER_PATHS.clear()
    yield
    for path in _LEDGER_PATHS:
        _assert_ledger_invariant(path)


@pytest.fixture(autouse=True)
def _fixture_identity_domain(monkeypatch):
    """Pin the executor's expected hypothesis ids to the synthetic manifest."""
    monkeypatch.setattr(
        C, "EXPECTED_HYPOTHESIS_IDS", dict(_FIXTURE_HYPOTHESIS_IDS)
    )


def _install_stubs(
    monkeypatch,
    *,
    build=None,
    classify=None,
):
    if build is None:

        def build(root, declaration, spec, panel, partition, *, overlay, inputs, **kw):
            return _diag(declaration_hash=declaration.declaration_hash)

    if classify is None:

        def classify(diag, screens):
            return _Classify()

    monkeypatch.setattr(C, "build_pq_diagnostics", build)
    monkeypatch.setattr(C, "classify_pq", classify)


def _build_klog(
    path,
    *,
    identities,
    policy,
    program_id=C.PROGRAM_ID,
    order="valid",
    missing_influenced=False,
):
    records: list[KnowledgeRecord] = []

    def add(kind, payload, refs=None):
        record = KnowledgeRecord.build(
            seq=len(records),
            prev_hash=(
                records[-1].record_hash if records else GENESIS_PREV_HASH
            ),
            kind=kind,
            payload=payload,
            recorded_at="2026-01-01T00:00:00Z",
            program_id=program_id,
            refs=refs or {},
        )
        records.append(record)
        return record

    if order == "estimand_second":
        add(
            RecordKind.HUMAN_DECISION,
            {
                "decision_kind": "OTHER",
                "actor_role": "OPERATOR",
                "consulted_all_prior": True,
            },
        )
    estimand = add(
        RecordKind.HUMAN_DECISION,
        {
            "decision_kind": "ESTIMAND_POLICY",
            "actor_role": "OPERATOR",
            "consulted_all_prior": True,
            **policy.to_content(),
        },
    )
    if order == "hyp_before_freeze":
        for item in identities:
            add(
                RecordKind.HYPOTHESIS_FREEZE,
                {
                    "hypothesis_id": item["hypothesis_id"],
                    "factor_spec_hash": item["factor_spec_hash"],
                },
                refs={"influenced_by": (estimand.record_hash,)},
            )
        add(
            RecordKind.HUMAN_DECISION,
            {
                "decision_kind": "PROGRAM_FREEZE",
                "actor_role": "OPERATOR",
                "consulted_all_prior": True,
            },
        )
    else:
        freeze = add(
            RecordKind.HUMAN_DECISION,
            {
                "decision_kind": "PROGRAM_FREEZE",
                "actor_role": "OPERATOR",
                "consulted_all_prior": True,
            },
        )
        for item in identities:
            refs = {"influenced_by": (estimand.record_hash, freeze.record_hash)}
            if missing_influenced:
                refs = {"influenced_by": (estimand.record_hash,)}
            add(
                RecordKind.HYPOTHESIS_FREEZE,
                {
                    "hypothesis_id": item["hypothesis_id"],
                    "factor_spec_hash": item["factor_spec_hash"],
                },
                refs=refs,
            )
    path.write_text(
        "\n".join(canonical_json(record.to_dict()) for record in records) + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# 1-2. Constants, hashes and FactorSpec loading
# ---------------------------------------------------------------------------


def test_constants_and_hashes():
    assert C.PRECOMMITMENT_SHA256 == (
        "15f700606929889d4b9b64799e49603f0f5e84081df5557519d815cc556619d7"
    )
    assert C.verify_precommitment() == C.PRECOMMITMENT_SHA256
    assert C.FACTORSPECS_SHA256 == (
        "f93a218d1e8a5bc07a7e64b52e1ce66be09b6708479d5c76a5f9a2973d6d532c"
    )
    assert C.SCREEN_POLICY_SHA256 == (
        "b7414db54add862dc12711564cdf7e0f4dec1ec07a0eb5b2bff507b81d9e592e"
    )
    assert C.DIAG_SCREEN_POLICY_SHA256 == (
        "e6ed8b27390d399e2ecd5c0fd7dcb9d4f71095eedf1bdf44cfcd75c6d17ca7c7"
    )
    assert C.build_partition().partition_id == C.EXPECTED_PARTITION_ID
    assert abs(C.CRITICAL_VALUE - 2.44999766060273) < 1e-12
    assert C.SEARCH_POLICY.alpha_per_test == 0.05 / 7
    assert C.SEARCH_POLICY.family_budget_m == 7


def test_factor_spec_loading_and_hash_roundtrip():
    specs = C.load_factor_specs()
    assert [spec.hypothesis for spec in specs] == list(C.EXPECTED_LABELS)
    assert all(spec.sign == 1 for spec in specs)
    expected = [
        "9b27eb881a7b31bfe123866f82996638315be6257c333fcf04cfcae3a02f037c",
        "85a7166bf2953fd9240037dc5189cb63dd3ae93690b2574c2f6d0567136d54fc",
        "78a6d4547aecee3615841408be1df040fdb33037fe465b790bcd75c5d76fe99f",
        "54b7edf3c0f00e5c53c8d87eee7a9c9bfd26287e15b04dc8ac38f57d3e4f1a6f",
        "ee5b8ea8f57262945828d545910584b6b49546af344c9a2bfbd2164380564f26",
        "42605a13a41cf0c9a04b614424b4bee1643bd565b1ec5b67c58a4b6d6ed816a1",
        "32678dfda88647a666994e62697742955f7903b141431a628b3df98895be66b0",
    ]
    assert [spec.version for spec in specs] == expected


def test_precommitment_hash_check_is_enforced(tmp_path):
    fake = tmp_path / "precommitment.md"
    fake.write_text("not the frozen precommitment", encoding="utf-8")
    with pytest.raises(C.C1BindingError):
        C.verify_precommitment(fake)


# ---------------------------------------------------------------------------
# 3. E1 family identity
# ---------------------------------------------------------------------------


def test_family_id_from_frozen_inputs():
    policy = C.build_search_policy(C.FACTOR_SPECS)
    assert policy.family_id == C.EXPECTED_FAMILY_ID
    assert policy.family_id == (
        "2d2f16f56fe34ee5fecfb4613a6a96ecdc621f2836e5c2d6ea4fba5c77f8d1d0"
    )


def test_family_id_assertion_rejects_other_inputs():
    with pytest.raises(C.C1BindingError):
        C.build_search_policy(C.FACTOR_SPECS, expected_family_id=ZERO)


def test_search_policy_accepts_family_id_and_rejects_label():
    policy = C.build_search_policy(C.FACTOR_SPECS)
    assert isinstance(policy, SearchPolicy)
    with pytest.raises(SearchPolicyError):
        SearchPolicy(
            family_id="campaign1-family-1",
            family_budget_m=7,
            family_alpha=0.05,
            trial_unit=policy.trial_unit,
            procedure=policy.procedure,
            budget_exhaustion=policy.budget_exhaustion,
            replay_rule=policy.replay_rule,
        )


def test_alpha_comes_from_policy_and_no_local_division():
    source = Path(C.__file__).read_text(encoding="utf-8")
    assert "family_alpha /" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            names = {
                child.attr
                for child in ast.walk(node.left)
                if isinstance(child, ast.Attribute)
            }
            assert "family_alpha" not in names, "local family_alpha division"
    from scipy.stats import norm

    assert C.CRITICAL_VALUE == float(
        norm.ppf(1.0 - C.SEARCH_POLICY.alpha_per_test)
    )


def test_family_id_changes_with_identity_component():
    members = [spec.version for spec in C.FACTOR_SPECS]
    base = content_hash(C.family_declaration(members))
    changed_member = list(members)
    changed_member[0] = "f" * 64
    assert content_hash(C.family_declaration(changed_member)) != base
    assert content_hash(C.family_declaration(list(reversed(members)))) != base
    assert (
        content_hash(
            C.family_declaration(members, precommitment_sha256="a" * 64)
        )
        != base
    )
    assert (
        content_hash(
            C.family_declaration(members, factorspecs_sha256="b" * 64)
        )
        != base
    )
    lineage_changed = C.family_declaration(members)
    lineage_changed["lineage"] = "unknown"
    assert content_hash(lineage_changed) != base


def test_family_id_independent_of_campaign1_id():
    declaration = C.family_declaration([spec.version for spec in C.FACTOR_SPECS])
    assert "campaign1_id" not in declaration
    policy = C.build_search_policy(C.FACTOR_SPECS)
    assert policy.family_id == content_hash(declaration)


# ---------------------------------------------------------------------------
# 4. Identities / domain separation
# ---------------------------------------------------------------------------


def test_identity_determinism_and_domain_separation():
    bindings = _bindings()
    first = C.campaign1_id(bindings)
    assert first == C.campaign1_id(_bindings())
    assert first != C.campaign1_id(_bindings(pq_hash="9" * 64))
    # E4: Campaign hypothesis ids live in the definition domain, never the
    # Phase-8 factor-provenance domain.
    definition_hash = C.hypothesis_definition_hash(
        _fixture_definition(C.FACTOR_SPECS[0])
    )
    hypothesis_id = C.campaign1_hypothesis_id(definition_hash)
    assert hypothesis_id != hypothesis_id_for(definition_hash)
    assert hypothesis_id != hypothesis_id_for(hypothesis_id)
    design = "4" * 64
    attempt = C.campaign_attempt_id(
        domain="campaign1-development-attempt",
        campaign1_id_value=first,
        hypothesis_id=hypothesis_id,
        design_hash=design,
    )
    experiment = experiment_id_for(hypothesis_id, design)
    assert attempt != experiment
    assert C.campaign_attempt_id(
        domain="campaign1-diagnostic-evaluation",
        campaign1_id_value=first,
        hypothesis_id=hypothesis_id,
        design_hash=design,
    ) != attempt


# ---------------------------------------------------------------------------
# 5. Ledger replay / locking via the executor
# ---------------------------------------------------------------------------


def _run_full(tmp_path, monkeypatch, **kwargs):
    _install_stubs(monkeypatch)
    executor = _make_executor(tmp_path, **kwargs)
    return executor.run(), executor


def test_full_campaign_records_and_budgets(tmp_path, monkeypatch):
    result, executor = _run_full(tmp_path, monkeypatch)
    assert result.stop_reason == C.C1StopReason.ALL_PRECOMMITTED_HYPOTHESES_EVALUATED
    ledger = L.CampaignLedger(executor.ledger.path)
    assert len(ledger.records("CAMPAIGN_OPEN")) == 1
    assert len(ledger.records("PRIMARY_ATTEMPT")) == 7
    assert len(ledger.records("DIAGNOSTIC_EVALUATION")) == 14
    assert len(ledger.records("O2_GUARD")) == 2
    assert len(ledger.records("CAMPAIGN_STOP")) == 1
    assert result.counters == {
        "generation_used": 0,
        "proposal_used": 7,
        "experiment_used": 7,
        "diagnostic_used": 14,
        "statistical_slots_used": 7,
    }


def _open_ledger(executor):
    """Open a campaign without running it (OPEN and no STOP)."""
    executor._replay_counters()
    executor._ensure_adapters()
    return executor._open_campaign(C.KpitPreflight(True, None, ()), ())


def test_replay_identical_consumes_no_slot(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    first = _make_executor(tmp_path)
    first.run()
    records_before = len(L.CampaignLedger(first.ledger.path).records())
    second = _make_executor(tmp_path)
    result = second.run()
    records_after = len(L.CampaignLedger(second.ledger.path).records())
    assert records_after == records_before
    assert result.counters == {
        "generation_used": 0,
        "proposal_used": 7,
        "experiment_used": 7,
        "diagnostic_used": 14,
        "statistical_slots_used": 7,
    }


def test_replay_returns_replay_identical(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    first = _make_executor(tmp_path, labels=("C1-H1",))
    first.run()
    second = _make_executor(tmp_path, labels=("C1-H1",))
    second._replay_counters()
    stop = second._process_label("C1-H1", C.campaign1_id(second.bindings))
    assert stop == C.REPLAY_IDENTICAL
    assert second.counters.statistical_slots_used == 1
    assert second.counters.proposal_used == 1


def test_mismatched_replay_on_open_ledger_fails_closed(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    first = _make_executor(tmp_path, labels=("C1-H1",))
    campaign_id = _open_ledger(first)
    first._process_label("C1-H1", campaign_id)
    path = first.ledger.path
    before = path.read_bytes()

    def changed(spec, universe, fold):
        panel = _panel(spec, universe, fold)
        panel["value"] = panel["value"] + 1.0
        return panel

    second = _make_executor(tmp_path, labels=("C1-H1",), panel_of=changed)
    _open_ledger(second)
    with pytest.raises(L.C1ReplayMismatchError):
        second._process_label("C1-H1", campaign_id)
    assert path.read_bytes() == before


def test_lock_conflict_stops_with_governance_conflict(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    first = _make_executor(tmp_path)
    _open_ledger(first)  # OPEN with no STOP
    second = _make_executor(
        tmp_path, bindings=_bindings(pq_hash="9" * 64), labels=()
    )
    result = second.run()
    assert result.stop_reason == C.C1StopReason.GOVERNANCE_CONFLICT
    ledger = L.CampaignLedger(second.ledger.path)
    assert ledger.records("CAMPAIGN_STOP")[-1]["stop_reason"] == (
        "GOVERNANCE_CONFLICT"
    )


def test_lock_conflict_on_realizations_stops_with_governance_conflict(
    tmp_path, monkeypatch
):
    _install_stubs(monkeypatch)
    first = _make_executor(tmp_path)
    _open_ledger(first)  # OPEN with no STOP
    changed = {label: "e" * 64 for label in C.EXPECTED_LABELS}
    second = _make_executor(tmp_path, expected_realizations=changed, labels=())
    result = second.run()
    assert result.stop_reason == C.C1StopReason.GOVERNANCE_CONFLICT


# ---------------------------------------------------------------------------
# 6. Budgets (independent, non-substitutable)
# ---------------------------------------------------------------------------


def test_budget_each_counter_independently_exhausts():
    counters = C.BudgetCounters()
    counters.proposal_used = 7
    with pytest.raises(C.C1BudgetExhausted) as exc:
        counters.check_primary()
    assert exc.value.reason == C.C1StopReason.PROPOSAL_BUDGET_EXHAUSTED

    counters = C.BudgetCounters()
    counters.experiment_used = 7
    with pytest.raises(C.C1BudgetExhausted) as exc:
        counters.check_primary()
    assert exc.value.reason == C.C1StopReason.EXPERIMENT_BUDGET_EXHAUSTED

    counters = C.BudgetCounters()
    counters.statistical_slots_used = 7
    with pytest.raises(C.C1BudgetExhausted) as exc:
        counters.check_primary()
    assert exc.value.reason == C.C1StopReason.STATISTICAL_BUDGET_EXHAUSTED

    counters = C.BudgetCounters()
    counters.diagnostic_used = 14
    with pytest.raises(C.C1BudgetExhausted) as exc:
        counters.check_diagnostic()
    assert exc.value.reason == C.C1StopReason.GOVERNANCE_CONFLICT


def test_budget_no_cross_substitution():
    counters = C.BudgetCounters()
    counters.consume_diagnostic()
    assert counters.diagnostic_used == 1
    assert counters.statistical_slots_used == 0
    assert counters.proposal_used == 0
    assert counters.experiment_used == 0
    counters.consume_primary()
    assert counters.statistical_slots_used == 1
    assert counters.diagnostic_used == 1


# ---------------------------------------------------------------------------
# 7. Screen outcomes
# ---------------------------------------------------------------------------


def test_is_assessed_predicate():
    assert C.is_assessed(_cell("A-T0", value=0.1, t_stat=1.0, n_obs=30))
    assert not C.is_assessed(
        _cell("A-T0", value=None, t_stat=None, n_obs=0, status="INSUFFICIENT_OBSERVATIONS")
    )
    diag = _diag(
        cells=tuple(
            _cell(name, value=None, t_stat=None, n_obs=0, status="NONFINITE")
            if name == "A-T0"
            else _cell(name, value=0.01, t_stat=3.0, n_obs=30)
            for name in ("B-T0", "B-TS", "A-T0", "A-TS")
        )
    )
    outcomes = C.screen_outcomes(diag, C.FoldRole.IS)
    by_cell = {outcome.cell: outcome for outcome in outcomes}
    assert by_cell["A-T0"].passed is None


def test_is_screen_boundary_t_equals_c():
    critical = C.CRITICAL_VALUE
    passes = _diag(
        cells=tuple(
            _cell("B-T0", value=0.01, t_stat=critical, n_obs=30)
            if name == "B-T0"
            else _cell(name, value=0.01, t_stat=3.0, n_obs=30)
            for name in ("B-T0", "B-TS", "A-T0", "A-TS")
        )
    )
    outcomes = {o.cell: o for o in C.screen_outcomes(passes, C.FoldRole.IS)}
    assert outcomes["B-T0"].passed is True

    fails = _diag(
        cells=tuple(
            _cell(
                "B-T0",
                value=0.01,
                t_stat=math.nextafter(critical, -math.inf),
                n_obs=30,
            )
            if name == "B-T0"
            else _cell(name, value=0.01, t_stat=3.0, n_obs=30)
            for name in ("B-T0", "B-TS", "A-T0", "A-TS")
        )
    )
    outcomes = {o.cell: o for o in C.screen_outcomes(fails, C.FoldRole.IS)}
    assert outcomes["B-T0"].passed is False


def test_is_screen_nan_or_short_periods_is_none():
    nan_t = _diag(
        cells=tuple(
            _cell("B-T0", value=0.01, t_stat=float("nan"), n_obs=30)
            if name == "B-T0"
            else _cell(name, value=0.01, t_stat=3.0, n_obs=30)
            for name in ("B-T0", "B-TS", "A-T0", "A-TS")
        )
    )
    assert {o.cell: o for o in C.screen_outcomes(nan_t, C.FoldRole.IS)}["B-T0"].passed is None
    short = _diag(
        cells=tuple(
            _cell("B-T0", value=0.01, t_stat=3.0, n_obs=23)
            if name == "B-T0"
            else _cell(name, value=0.01, t_stat=3.0, n_obs=30)
            for name in ("B-T0", "B-TS", "A-T0", "A-TS")
        )
    )
    assert {o.cell: o for o in C.screen_outcomes(short, C.FoldRole.IS)}["B-T0"].passed is None


def test_oos_value_zero_fails():
    diag = _diag(
        cells=tuple(
            _cell(name, value=0.0, t_stat=3.0, n_obs=30)
            if name in ("B-T0", "A-T0")
            else _cell(name, value=0.01, t_stat=3.0, n_obs=30)
            for name in ("B-T0", "B-TS", "A-T0", "A-TS")
        )
    )
    outcomes = {o.cell: o for o in C.screen_outcomes(diag, C.FoldRole.OOS)}
    assert outcomes["B-T0"].passed is False
    assert outcomes["A-T0"].passed is False


# ---------------------------------------------------------------------------
# 8. Disposition precedence and reasons
# ---------------------------------------------------------------------------


def _classify_disposition(
    *,
    screens_is,
    screens_oos,
    classify_is=None,
    classify_oos=None,
    diag_is=None,
    diag_oos=None,
):
    return C.attempt_disposition(
        diagnostics_is=diag_is or _diag(),
        diagnostics_oos=diag_oos or _diag(),
        screens_is=screens_is,
        screens_oos=screens_oos,
        classify_is=classify_is or _Classify(),
        classify_oos=classify_oos or _Classify(),
    )


def _all_pass_screens():
    diag = _diag()
    return (
        C.screen_outcomes(diag, C.FoldRole.IS),
        C.screen_outcomes(diag, C.FoldRole.OOS),
    )


def test_disposition_pass_when_all_pass():
    screens_is, screens_oos = _all_pass_screens()
    disposition, reasons, _ = _classify_disposition(
        screens_is=screens_is, screens_oos=screens_oos
    )
    assert disposition is C.Disposition.DEVELOPMENT_SCREEN_PASS
    assert reasons == []


def test_disposition_none_verdict_is_not_assessed():
    screens_is, screens_oos = _all_pass_screens()
    screens_is = list(screens_is)
    screens_is[0] = PQ.PQScreenOutcome(
        cell=screens_is[0].cell,
        cell_hash=screens_is[0].cell_hash,
        screen_id=screens_is[0].screen_id,
        screen_policy_hash=screens_is[0].screen_policy_hash,
        declaration_hash=screens_is[0].declaration_hash,
        passed=None,
    )
    disposition, reasons, _ = _classify_disposition(
        screens_is=screens_is, screens_oos=screens_oos
    )
    assert disposition is C.Disposition.NOT_ASSESSED
    assert reasons == ["SCREEN_VERDICT_NONE"]


def test_disposition_quarantine_sensitive_blocks():
    screens_is, screens_oos = _all_pass_screens()
    disposition, reasons, quarantine = _classify_disposition(
        screens_is=screens_is,
        screens_oos=screens_oos,
        classify_is=_Classify(quarantine_sensitive=True),
    )
    assert disposition is C.Disposition.DEVELOPMENT_SCREEN_FAIL
    assert reasons == ["QUARANTINE_SENSITIVE"]
    assert quarantine is True


def test_disposition_delisting_sensitive_blocks():
    screens_is, screens_oos = _all_pass_screens()
    disposition, reasons, _ = _classify_disposition(
        screens_is=screens_is,
        screens_oos=screens_oos,
        classify_oos=_Classify(delisting_a=PQ.DELISTING_SENSITIVE),
    )
    assert disposition is C.Disposition.DEVELOPMENT_SCREEN_FAIL
    assert reasons == ["DELISTING_SENSITIVE"]


def test_disposition_precedence_and_reason_order():
    screens_is, screens_oos = _all_pass_screens()
    screens_is = list(screens_is)
    # An OOS failure plus a not-assessed IS verdict: NOT_ASSESSED wins and the
    # reasons are recorded in canonical order.
    screens_is[0] = PQ.PQScreenOutcome(
        cell=screens_is[0].cell,
        cell_hash=screens_is[0].cell_hash,
        screen_id=screens_is[0].screen_id,
        screen_policy_hash=screens_is[0].screen_policy_hash,
        declaration_hash=screens_is[0].declaration_hash,
        passed=None,
    )
    screens_oos = list(screens_oos)
    screens_oos[1] = PQ.PQScreenOutcome(
        cell=screens_oos[1].cell,
        cell_hash=screens_oos[1].cell_hash,
        screen_id=screens_oos[1].screen_id,
        screen_policy_hash=screens_oos[1].screen_policy_hash,
        declaration_hash=screens_oos[1].declaration_hash,
        passed=False,
    )
    disposition, reasons, _ = _classify_disposition(
        screens_is=screens_is, screens_oos=screens_oos
    )
    assert disposition is C.Disposition.NOT_ASSESSED
    assert reasons == ["SCREEN_VERDICT_NONE"]

    disposition, reasons, _ = _classify_disposition(
        screens_is=screens_is,
        screens_oos=screens_oos,
        diag_is=_diag(not_assessed="DATASET_IDENTITY_MISMATCH"),
    )
    assert disposition is C.Disposition.NOT_ASSESSED
    assert reasons == ["PQ_NOT_ASSESSED", "SCREEN_VERDICT_NONE"]

    pass_is, pass_oos = _all_pass_screens()
    screens_fail = list(pass_is)
    screens_fail[0] = PQ.PQScreenOutcome(
        cell=screens_fail[0].cell,
        cell_hash=screens_fail[0].cell_hash,
        screen_id=screens_fail[0].screen_id,
        screen_policy_hash=screens_fail[0].screen_policy_hash,
        declaration_hash=screens_fail[0].declaration_hash,
        passed=False,
    )
    disposition, reasons, _ = _classify_disposition(
        screens_is=screens_fail, screens_oos=pass_oos
    )
    assert disposition is C.Disposition.DEVELOPMENT_SCREEN_FAIL
    assert reasons == ["IS_THRESHOLD_NOT_MET"]


# ---------------------------------------------------------------------------
# 9. O2 guard and sealed fold
# ---------------------------------------------------------------------------


def test_o2_count_threshold():
    formation = pd.Timestamp("2016-01-29").date()
    values = pd.DataFrame(
        {"AAA": [1e-9, 1.0], "BBB": [5e-7, float("nan")]},
        index=[formation, pd.Timestamp("2016-02-29").date()],
    )
    members = {formation: ["AAA", "BBB"]}
    assert C.count_o2_cells(values, [formation], members) == 2


def test_o2_guard_skips_pqb_and_not_assessed(tmp_path, monkeypatch):
    calls = []
    _install_stubs(
        monkeypatch,
        build=lambda *a, **k: (_ for _ in ()).throw(AssertionError("PQB called")),
    )
    executor = _make_executor(
        tmp_path,
        labels=("C1-H6",),
        o2_count_of=lambda spec: 3,
    )
    result = executor.run()
    assert result.stop_reason == C.C1StopReason.ALL_PRECOMMITTED_HYPOTHESES_EVALUATED
    ledger = L.CampaignLedger(executor.ledger.path)
    attempts = ledger.records("PRIMARY_ATTEMPT")
    assert len(attempts) == 1
    assert attempts[0]["disposition"] == "NOT_ASSESSED"
    assert attempts[0]["reasons"] == ["O2_ESCALATION"]
    assert ledger.records("DIAGNOSTIC_EVALUATION") == ()
    assert result.counters["proposal_used"] == 1
    assert calls == []


def test_sealed_fold_is_refused_before_any_pqb(tmp_path, monkeypatch):
    calls = []
    _install_stubs(
        monkeypatch,
        build=lambda *a, **k: calls.append(a) or _diag(),
    )
    holdout = next(
        fold for fold in C.build_partition().folds if fold.role is C.FoldRole.HOLDOUT
    )
    with pytest.raises(C.C1SealedFoldError):
        C.build_declaration(
            spec=C.FACTOR_SPECS[0],
            panel=_panel(C.FACTOR_SPECS[0], C.PRIMARY_UNIVERSE, holdout),
            fold=holdout,
            universe=C.PRIMARY_UNIVERSE,
            bindings=_bindings(),
            screen_id=C.SCREEN_ID,
            screen_policy_hash=C.SCREEN_POLICY_SHA256,
        )
    executor = _make_executor(tmp_path)
    with pytest.raises(C.C1SealedFoldError):
        executor._pq_declaration(
            C.FACTOR_SPECS[0],
            _panel(C.FACTOR_SPECS[0], C.PRIMARY_UNIVERSE, holdout),
            holdout,
            C.PRIMARY_UNIVERSE,
            C.SCREEN_ID,
            C.SCREEN_POLICY_SHA256,
        )
    assert calls == []


# ---------------------------------------------------------------------------
# 10. Diagnostics are never screened or classified
# ---------------------------------------------------------------------------


def test_diagnostics_never_screened_or_classified(tmp_path, monkeypatch):
    classify_calls = []
    _install_stubs(
        monkeypatch,
        classify=lambda diag, screens: classify_calls.append(1) or _Classify(),
    )
    executor = _make_executor(tmp_path, labels=("C1-H1",))
    executor.run()
    ledger = L.CampaignLedger(executor.ledger.path)
    diagnostics = ledger.records("DIAGNOSTIC_EVALUATION")
    assert len(diagnostics) == 2
    for record in diagnostics:
        assert "disposition" not in record
        assert "screens" not in record
        assert "classifications" not in record
    # Two classify calls only: the primary IS and OOS folds.
    assert len(classify_calls) == 2
    assert executor.counters.diagnostic_used == 2
    assert executor.counters.statistical_slots_used == 1


# ---------------------------------------------------------------------------
# 11. K-PIT preflight
# ---------------------------------------------------------------------------


def _identities():
    return _fixture_identity_records()


def test_kpit_valid_order_accepted(tmp_path):
    path = _build_klog(
        tmp_path / "k.jsonl",
        identities=_identities(),
        policy=C.estimand_policy(),
    )
    result = C.verify_kpit_ordering(
        path, policy=C.estimand_policy(), identities=_identities()
    )
    assert result.ok is True
    assert len(result.record_hashes) == 9


def test_kpit_rejects_record_before_estimand(tmp_path):
    path = _build_klog(
        tmp_path / "k.jsonl",
        identities=_identities(),
        policy=C.estimand_policy(),
        order="estimand_second",
    )
    assert C.verify_kpit_ordering(path, identities=_identities()).ok is False


def test_kpit_rejects_hypothesis_before_program_freeze(tmp_path):
    path = _build_klog(
        tmp_path / "k.jsonl",
        identities=_identities(),
        policy=C.estimand_policy(),
        order="hyp_before_freeze",
    )
    result = C.verify_kpit_ordering(path, identities=_identities())
    assert result.ok is False


def test_kpit_rejects_missing_influenced_by(tmp_path):
    path = _build_klog(
        tmp_path / "k.jsonl",
        identities=_identities(),
        policy=C.estimand_policy(),
        missing_influenced=True,
    )
    assert C.verify_kpit_ordering(path, identities=_identities()).ok is False


def test_kpit_rejects_wrong_identity(tmp_path):
    identities = list(_identities())
    identities[0]["factor_spec_hash"] = "f" * 64
    path = _build_klog(
        tmp_path / "k.jsonl",
        identities=_identities(),
        policy=C.estimand_policy(),
    )
    assert C.verify_kpit_ordering(path, identities=identities).ok is False


def test_executor_refuses_to_open_on_kpit_failure(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    wrong = list(_identities())
    wrong[0]["factor_spec_hash"] = "f" * 64
    path = _build_klog(
        tmp_path / "k.jsonl",
        identities=wrong,
        policy=C.estimand_policy(),
    )
    executor = _make_executor(tmp_path, knowledge_log_path=path)
    result = executor.run()
    assert result.stop_reason == C.C1StopReason.GOVERNANCE_CONFLICT
    assert L.CampaignLedger(executor.ledger.path).records() == ()


def test_executor_records_kpit_hashes_on_open(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    path = _build_klog(
        tmp_path / "k.jsonl",
        identities=_identities(),
        policy=C.estimand_policy(),
    )
    executor = _make_executor(tmp_path, labels=("C1-H1",), knowledge_log_path=path)
    executor.run()
    opens = L.CampaignLedger(executor.ledger.path).records("CAMPAIGN_OPEN")
    assert len(opens[0]["kpit_preflight_record_hashes"]) == 9


# ---------------------------------------------------------------------------
# 12. E2 Stage A / B / C
# ---------------------------------------------------------------------------


def test_stage_a_preopen_provenance_failure_writes_nothing(tmp_path, monkeypatch):
    # E4.2.2: the pre-open Stage-A failure is now a missing expected-
    # realization mapping; factor provenance is no longer part of identity.
    _install_stubs(monkeypatch)
    executor = _make_executor(tmp_path, expected_realizations={})
    result = executor.run()
    assert result.stop_reason == (
        C.C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
    )
    path = executor.ledger.path
    assert (not path.exists()) or path.read_bytes() == b""
    assert L.CampaignLedger(path).records() == ()
    assert result.counters["proposal_used"] == 0


def test_stage_a_postopen_identity_failure_appends_one_stop(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    calls: dict[str, int] = {}
    real_definition = C.hypothesis_definition

    def failing(spec, *, root):
        # Succeed in the up-front identity pass, then fail while
        # re-establishing the hypothesis identity inside _process_label.
        label = spec.hypothesis or spec.id
        calls[label] = calls.get(label, 0) + 1
        if label == "C1-H1" and calls[label] >= 2:
            raise C.C1BindingError("identity failure")
        return real_definition(spec, root=root)

    monkeypatch.setattr(C, "hypothesis_definition", failing)
    executor = _make_executor(tmp_path, labels=("C1-H1",))
    result = executor.run()
    assert result.stop_reason == (
        C.C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
    )
    ledger = L.CampaignLedger(executor.ledger.path)
    assert ledger.records("PRIMARY_ATTEMPT") == ()
    assert result.counters["proposal_used"] == 0
    assert result.counters["statistical_slots_used"] == 0
    stops = ledger.records("CAMPAIGN_STOP")
    assert len(stops) == 1
    assert stops[-1]["stop_reason"] == (
        "INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE"
    )


def test_stage_b_binding_failure_is_accounted(tmp_path, monkeypatch):
    def raising(*args, **kwargs):
        raise PQ.PQBindingError("pinned code mismatch")

    _install_stubs(monkeypatch, build=raising)
    executor = _make_executor(tmp_path, labels=("C1-H1",))
    result = executor.run()
    assert result.stop_reason == (
        C.C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
    )
    ledger = L.CampaignLedger(executor.ledger.path)
    attempts = ledger.records("PRIMARY_ATTEMPT")
    assert len(attempts) == 1
    assert attempts[0]["disposition"] == "NOT_ASSESSED"
    assert attempts[0]["reasons"] == ["BINDING_OR_IDENTITY_FAILURE"]
    assert result.counters["proposal_used"] == 1
    assert result.counters["experiment_used"] == 1
    assert result.counters["statistical_slots_used"] == 1
    assert ledger.find_attempt(attempts[0]["campaign_attempt_id"]) is not None
    assert ledger.records("CAMPAIGN_STOP")[-1]["stop_reason"] == (
        "INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE"
    )


def test_primary_attempt_records_realization(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    executor = _make_executor(tmp_path, labels=("C1-H1",))
    executor.run()
    ledger = L.CampaignLedger(executor.ledger.path)
    attempt = ledger.records("PRIMARY_ATTEMPT")[0]
    assert attempt["factor_realization_hash"] == _realization(C.FACTOR_SPECS[0])
    assert attempt["failure_detail"] is None
    diagnostics = ledger.records("DIAGNOSTIC_EVALUATION")
    assert diagnostics
    assert all(
        record["factor_realization_hash"] == _realization(C.FACTOR_SPECS[0])
        for record in diagnostics
    )


def test_realization_mismatch_is_stage_b_and_consumes_slot(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)

    def mismatching(spec):
        if spec.hypothesis == "C1-H1":
            return "9" * 64
        return _realization(spec)

    expected = {spec.hypothesis: _realization(spec) for spec in C.FACTOR_SPECS}
    executor = _make_executor(
        tmp_path,
        labels=("C1-H1",),
        realization_of=mismatching,
        expected_realizations=expected,
    )
    result = executor.run()
    assert result.stop_reason == (
        C.C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
    )
    ledger = L.CampaignLedger(executor.ledger.path)
    attempts = ledger.records("PRIMARY_ATTEMPT")
    assert len(attempts) == 1
    assert attempts[0]["disposition"] == "NOT_ASSESSED"
    assert attempts[0]["reasons"] == ["BINDING_OR_IDENTITY_FAILURE"]
    assert attempts[0]["failure_detail"] == "REALIZATION_MISMATCH"
    assert attempts[0]["factor_realization_hash"] == "9" * 64
    assert attempts[0]["expected_factor_realization_hash"] == expected["C1-H1"]
    assert result.counters["proposal_used"] == 1
    assert result.counters["statistical_slots_used"] == 1
    assert ledger.records("DIAGNOSTIC_EVALUATION") == ()
    assert ledger.records("CAMPAIGN_STOP")[-1]["stop_reason"] == (
        "INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE"
    )


def test_realization_failure_is_stage_b(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)

    def failing(spec):
        if spec.hypothesis == "C1-H1":
            raise C.C1RealizationError("realization boom")
        return _realization(spec)

    expected = {label: "e" * 64 for label in C.EXPECTED_LABELS}
    executor = _make_executor(
        tmp_path,
        labels=("C1-H1",),
        realization_of=failing,
        expected_realizations=expected,
    )
    result = executor.run()
    assert result.stop_reason == (
        C.C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
    )
    ledger = L.CampaignLedger(executor.ledger.path)
    attempts = ledger.records("PRIMARY_ATTEMPT")
    assert len(attempts) == 1
    assert attempts[0]["reasons"] == ["BINDING_OR_IDENTITY_FAILURE"]
    assert attempts[0]["failure_detail"] == "REALIZATION_FAILURE"
    assert attempts[0]["factor_realization_hash"] is None
    assert attempts[0]["expected_factor_realization_hash"] == "e" * 64
    assert result.counters["statistical_slots_used"] == 1


def test_realization_mismatch_never_creates_a_new_hypothesis(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)

    def mismatching(spec):
        return "9" * 64

    expected = {spec.hypothesis: _realization(spec) for spec in C.FACTOR_SPECS}
    executor = _make_executor(
        tmp_path,
        labels=("C1-H1",),
        realization_of=mismatching,
        expected_realizations=expected,
    )
    executor.run()
    ledger = L.CampaignLedger(executor.ledger.path)
    attempts = ledger.records("PRIMARY_ATTEMPT")
    assert len(attempts) == 1
    # The recorded hypothesis id is the frozen definition-domain id, not a
    # fresh one derived from the mismatched realization.
    assert attempts[0]["hypothesis_id"] == _FIXTURE_HYPOTHESIS_IDS["C1-H1"]
    open_record = ledger.records("CAMPAIGN_OPEN")[0]
    assert open_record["expected_factor_realizations"] == {
        spec.hypothesis: expected[spec.hypothesis] for spec in C.FACTOR_SPECS
    }


def test_stage_c_returned_not_assessed_continues(tmp_path, monkeypatch):
    _install_stubs(
        monkeypatch,
        build=lambda root, declaration, spec, panel, partition, **kw: _diag(
            declaration_hash=declaration.declaration_hash,
            not_assessed="DATASET_IDENTITY_MISMATCH",
        ),
    )
    executor = _make_executor(tmp_path, labels=("C1-H1", "C1-H2"))
    result = executor.run()
    assert result.stop_reason == C.C1StopReason.ALL_PRECOMMITTED_HYPOTHESES_EVALUATED
    ledger = L.CampaignLedger(executor.ledger.path)
    attempts = ledger.records("PRIMARY_ATTEMPT")
    assert len(attempts) == 2
    assert all(record["reasons"] == ["PQ_NOT_ASSESSED"] for record in attempts)
    assert result.counters["statistical_slots_used"] == 2


def test_terminal_ledger_after_stage_b_appends_nothing(tmp_path, monkeypatch):
    def raising(*args, **kwargs):
        raise PQ.PQBindingError("pinned code mismatch")

    _install_stubs(monkeypatch, build=raising)
    first = _make_executor(tmp_path, labels=("C1-H1",))
    first.run()
    path = first.ledger.path
    before = path.read_bytes()
    assert before

    _install_stubs(monkeypatch)  # cause removed
    second = _make_executor(tmp_path, labels=("C1-H1",))
    result = second.run()
    assert path.read_bytes() == before
    assert result.stop_reason == (
        C.C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
    )


def test_terminal_ledger_after_untyped_stop_appends_nothing(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)

    def exploding(spec):
        if spec.hypothesis == "C1-H1":
            raise RuntimeError("boom")
        return _realization(spec)

    expected = {label: "e" * 64 for label in C.EXPECTED_LABELS}
    first = _make_executor(
        tmp_path,
        realization_of=exploding,
        expected_realizations=expected,
        labels=("C1-H1",),
    )
    with pytest.raises(RuntimeError):
        first.run()
    path = first.ledger.path
    before = path.read_bytes()
    assert before
    assert L.CampaignLedger(path).records("CAMPAIGN_STOP")

    second = _make_executor(tmp_path, labels=("C1-H1",))
    result = second.run()
    assert path.read_bytes() == before
    assert result.stop_reason == (
        C.C1StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
    )


def test_terminal_ledger_after_normal_close_appends_nothing(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    first = _make_executor(tmp_path, labels=("C1-H1",))
    first_result = first.run()
    path = first.ledger.path
    before = path.read_bytes()

    second = _make_executor(tmp_path, labels=("C1-H1",))
    second_result = second.run()
    assert path.read_bytes() == before
    assert first_result.stop_reason == (
        C.C1StopReason.ALL_PRECOMMITTED_HYPOTHESES_EVALUATED
    )
    assert second_result.stop_reason == first_result.stop_reason


# ---------------------------------------------------------------------------
# 13. Forbidden vocabulary and AST import/call bans
# ---------------------------------------------------------------------------


def test_forbidden_vocabulary_grep(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    executor = _make_executor(tmp_path, labels=("C1-H1",))
    executor.run()
    forbidden = ("ACCEPT", "REJECT", "DEFER", "EvaluationRecord", "DecisionRecord")
    for module in (C, L):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{token} in {module.__name__}"
    for record in L.CampaignLedger(executor.ledger.path).records():
        text = canonical_json(record)
        for token in forbidden:
            assert token not in text, f"{token} in ledger record"


def test_ast_import_and_call_bans():
    forbidden_names = {
        "ExperimentRegistry",
        "ExperimentEntry",
        "DecisionEntry",
        "RegistrySnapshot",
        "experiment_id_for",
        "hypothesis_id_for",
        "hypothesis_id_for_record",
        "experiment_id_for_record",
        "SearchLedger",
        "adjudicate_search_attempt",
        "EvaluationRecord",
        "DecisionRecord",
        "append_estimand_policy",
        "record_program_freeze",
        "record_hypothesis_freeze",
    }
    forbidden_modules = {"smart_beta.evaluation.engine", "smart_beta.experiment.search"}
    for module in (C, L):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "hypothesis_id_for" not in source, module.__name__
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in forbidden_modules
                for alias in node.names:
                    assert alias.name not in forbidden_names, alias.name
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden_modules
            if isinstance(node, ast.Attribute):
                assert node.attr not in {
                    "append_estimand_policy",
                    "record_program_freeze",
                    "record_hypothesis_freeze",
                    "evaluate",
                    "hypothesis_id_for",
                }


def test_registry_imports_are_exactly_permitted():
    tree = ast.parse(Path(C.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == (
            "smart_beta.experiment.registry"
        ):
            names.update(alias.name for alias in node.names)
    assert names <= {"content_hash", "canonical_json"}


# ---------------------------------------------------------------------------
# 14. Report
# ---------------------------------------------------------------------------


def test_report_determinism_and_claim_boundary(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    executor = _make_executor(tmp_path, labels=("C1-H1",))
    executor.run()
    json_bytes, markdown_bytes = C.render_report(executor.ledger.path)
    json_again, markdown_again = C.render_report(executor.ledger.path)
    assert json_bytes == json_again
    assert markdown_bytes == markdown_again
    payload = json.loads(json_bytes.decode("utf-8"))
    assert payload["campaign"]["campaign1_id"]
    assert payload["diagnostics"]["label"] == "DESCRIPTIVE — NOT JUDGED"
    assert "DEVELOPMENT_SCREEN_PASS" in payload["claim_boundary"]
    assert "not confirmation" in payload["claim_boundary"]
    assert len(payload["hypotheses"]) == 7
    assert b"DESCRIPTIVE" in markdown_bytes


def test_report_of_full_campaign_lists_every_hypothesis(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    executor = _make_executor(tmp_path)
    executor.run()
    json_bytes, _ = C.render_report(executor.ledger.path)
    payload = json.loads(json_bytes.decode("utf-8"))
    labels = [item["label"] for item in payload["hypotheses"]]
    assert labels == list(C.EXPECTED_LABELS)
    assert all(
        item["outcome"]["disposition"] == "DEVELOPMENT_SCREEN_PASS"
        for item in payload["hypotheses"]
    )
    assert payload["stop_reason"] == "ALL_PRECOMMITTED_HYPOTHESES_EVALUATED"


# ---------------------------------------------------------------------------
# 15. Phase-9 stop-reason mapping
# ---------------------------------------------------------------------------


def test_phase9_stop_reason_mapping_members_exist():
    from smart_beta.research.policy import StopReason

    assert C.PHASE9_STOP_REASON_MAP[
        C.C1StopReason.PROPOSAL_BUDGET_EXHAUSTED
    ] is StopReason.PROPOSAL_BUDGET_EXHAUSTED
    assert C.PHASE9_STOP_REASON_MAP[
        C.C1StopReason.STATISTICAL_BUDGET_EXHAUSTED
    ] is StopReason.STATISTICAL_BUDGET_EXHAUSTED
    assert C.PHASE9_STOP_REASON_MAP[
        C.C1StopReason.GOVERNANCE_CONFLICT
    ] is StopReason.GOVERNANCE_CONFLICT
    assert C.PHASE9_STOP_REASON_MAP[
        C.C1StopReason.SEALED_FOLD_ACCESS_ATTEMPT
    ] is StopReason.HOLDOUT_FIREWALL_VIOLATION


def test_cell_mapping_matches_dataset_v1_2():
    assert C.CELL_MAPPING == {
        "B-T0": "ORIGINAL-T0",
        "B-TS": "ORIGINAL-TS",
        "A-T0": "QUARANTINED-T0",
        "A-TS": "QUARANTINED-TS",
    }


# ---------------------------------------------------------------------------
# 16. F3 real-input adapters (synthetic TrustedInput fixtures only)
# ---------------------------------------------------------------------------


def _simple_spec():
    return C._spec_from_dict(
        {
            "id": "synth_lag_tri",
            "description": "synthetic lagged total return index",
            "hypothesis": "SYNTH-H1",
            "expression": {
                "op": "lag",
                "operand": {"op": "field", "role": "tri"},
                "periods": 1,
            },
            "inputs": [
                {
                    "alias": "tri",
                    "requirement": {
                        "frequency": "daily",
                        "lookback": 0,
                        "observation_period": "instant",
                        "require_knowledge_date": True,
                        "require_positive_vintage_identity": False,
                        "revision_policy": "point_in_time",
                        "semantic_id": "total_return_index",
                        "units": "ratio",
                    },
                }
            ],
            "frequency": "daily",
            "missing_policy": "propagate",
            "sign": 1,
        }
    )


def _o2_spec():
    return C._spec_from_dict(
        {
            "id": "synth_o2",
            "description": "synthetic O2 denominator spec",
            "hypothesis": "SYNTH-H6",
            "expression": {
                "op": "div",
                "left": {"op": "const", "value": 1.0},
                "right": {
                    "op": "lag",
                    "operand": {
                        "fn": "std",
                        "op": "rolling",
                        "operand": {"op": "field", "role": "ret"},
                        "window": 2,
                    },
                    "periods": 1,
                },
            },
            "inputs": [
                {
                    "alias": "ret",
                    "requirement": {
                        "frequency": "daily",
                        "lookback": 0,
                        "observation_period": "period",
                        "require_knowledge_date": True,
                        "require_positive_vintage_identity": False,
                        "revision_policy": "point_in_time",
                        "semantic_id": "daily_total_return",
                        "units": None,
                    },
                }
            ],
            "frequency": "daily",
            "missing_policy": "propagate",
            "sign": 1,
        }
    )


def _capability(semantic: str, *, period, units, history: int):
    return DataCapability(
        semantic_id=semantic,
        observation_period=period,
        units=units,
        frequency=Frequency.DAILY,
        history=history,
        has_knowledge_date=True,
        has_positive_vintage_identity=False,
        revision_policies=frozenset({RevisionPolicy.POINT_IN_TIME}),
    )


def _tri_inputs():
    frame = pd.DataFrame(
        {
            "AAA": [100.0, 101.0, 102.0, 103.0],
            "BBB": [50.0, 51.0, 50.0, 52.0],
        },
        index=pd.to_datetime(
            ["2016-01-04", "2016-01-05", "2016-01-06", "2016-01-07"]
        ),
    )
    return {
        "total_return_index": TrustedInput(
            values=frame,
            capability=_capability(
                "total_return_index",
                period=ObservationPeriod.INSTANT,
                units=Unit.RATIO,
                history=4,
            ),
            evidence_class=EvidenceClass.LIVE_RECORDED,
        )
    }


def _ret_inputs():
    frame = pd.DataFrame(
        {
            "AAA": [0.0, 0.0, 0.0, 0.0],
            "BBB": [0.0, 0.0, 0.0, 0.0],
        },
        index=pd.to_datetime(
            ["2016-01-04", "2016-01-05", "2016-01-06", "2016-01-07"]
        ),
    )
    return {
        "daily_total_return": TrustedInput(
            values=frame,
            capability=_capability(
                "daily_total_return",
                period=ObservationPeriod.PERIOD,
                units=None,
                history=4,
            ),
            evidence_class=EvidenceClass.LIVE_RECORDED,
        )
    }


def _overlay():
    empty = pd.DataFrame()
    return C._pqa.Overlay(
        flags=empty,
        primitive_states=empty,
        input_states=empty,
        label_states=empty,
        liquidity_exposure=empty,
        identity={},
        pq_id="pq-test",
        pq_hash="d" * 64,
    )


def test_factor_realization_hash_matches_phase6():
    spec = _simple_spec()
    inputs = _tri_inputs()
    expected = C._evaluate_factor(
        spec, C._pqa._alias_inputs(spec, inputs)
    ).content_hash
    assert C.factor_realization_hash(spec, inputs) == expected
    assert len(expected) == 64


def test_realization_failure_is_typed(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(C, "_evaluate_factor", boom)
    with pytest.raises(C.C1RealizationError):
        C.factor_realization_hash(_simple_spec(), _tri_inputs())


def test_hypothesis_identities_from_fixture_manifest(tmp_path):
    _write_fixture_manifest(tmp_path)
    identities = C.hypothesis_identities(
        tmp_path,
        factor_specs=C.FACTOR_SPECS,
        expected_ids=_FIXTURE_HYPOTHESIS_IDS,
    )
    assert len(identities) == 7
    for item in identities:
        assert item["label"] in _FIXTURE_HYPOTHESIS_IDS
        assert item["hypothesis_id"] == _FIXTURE_HYPOTHESIS_IDS[item["label"]]
        assert "hypothesis_definition_hash" in item
        # E4: the realization is no longer part of the identity tuple.
        assert "factor_realization_hash" not in item


def test_build_fold_panel_and_pqb_match():
    values = pd.DataFrame(
        {"AAA": [1.0, 2.0], "BBB": [float("nan"), 4.0]},
        index=pd.to_datetime(["2016-01-04", "2016-01-05"]),
    )
    formations = [d.date() for d in values.index]
    members = {
        formations[0]: ["AAA", "BBB", "CCC"],
        formations[1]: ["AAA", "BBB"],
    }
    panel = C.build_fold_panel(values, formations, members)
    assert list(panel.columns) == ["date", "stock_id", "value"]
    assert len(panel) == 5
    assert C._pqb._factor_panel_matches(panel, values, formations)
    missing = panel[panel["stock_id"] == "CCC"]
    assert missing["value"].isna().all()


def test_build_fold_panel_satisfies_d7b_validate():
    values = pd.DataFrame(
        {"AAA": [1.0, 2.0]},
        index=pd.to_datetime(["2016-01-04", "2016-01-05"]),
    )
    formations = [d.date() for d in values.index]
    members = {formations[0]: ["AAA"], formations[1]: ["AAA"]}
    panel = C.build_fold_panel(values, formations, members)
    declaration = C.build_declaration(
        spec=_simple_spec(),
        panel=panel,
        fold=C.build_partition().folds[0],
        universe="TOP_500",
        bindings=_bindings(),
        screen_id=C.SCREEN_ID,
        screen_policy_hash=C.SCREEN_POLICY_SHA256,
    )
    validated = C._d7b._validate_factor_panel(
        panel,
        declaration=declaration,
        formations=formations,
        universe_by_formation=members,
    )
    assert len(validated) == 2


def test_o2_denominator_spec_extracts_right_operand():
    spec = _o2_spec()
    sub = C.o2_denominator_spec(spec)
    assert sub.to_dict()["expression"] == spec.to_dict()["expression"]["right"]
    assert sub.sign == 1


def test_evaluate_o2_count_over_synthetic_inputs():
    spec = _o2_spec()
    inputs = _ret_inputs()
    formations = [pd.Timestamp("2016-01-07").date()]
    members = {formations[0]: ["AAA", "BBB"]}
    count = C.evaluate_o2_count(
        spec,
        overlay=_overlay(),
        inputs=inputs,
        formations=formations,
        members_by_formation=members,
    )
    assert count == 2
