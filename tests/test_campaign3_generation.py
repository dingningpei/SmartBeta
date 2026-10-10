"""C3-X Campaign-3 generation executor tests (synthetic fixtures only).

Authority: the frozen ``docs/campaign3_p4_implementation_spec.md`` (task row
C3-X) with design
``docs/campaign3_p4_generator_integration_design.md``.

Every test is offline and synthetic. The pipeline tests inject a stub
generator and evaluator; the integration test drives the real P-Q v2.3
evaluation bridge over the existing PQ2-H synthetic dataset fixture. No
production artifact, ``data_store``, reserve, holdout or knowledge log is
read; no network call is made; no K-PIT record is written outside a
disposable test log.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from smart_beta.campaign import campaign3_eval as E
from smart_beta.campaign import campaign3_generation as G
from smart_beta.research.generator import GeneratorBoundary
from smart_beta.research.history import (
    DevelopmentFoldRole,
    FeedbackChannel,
    FoldEvidence,
    GeneratorVisibleResearchHistory,
    VisibleExperiment,
)
from smart_beta.evaluation.partition import Fold, FoldRole, Partition
from smart_beta.evaluation.spec import MetricValue
from smart_beta.research.policy import (
    ExpressionOperator,
    HoldoutVisibility,
)
from smart_beta.science.knowledge import Channel, KnowledgeLog, RecordKind

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SYNTHETIC_PATH = _REPO_ROOT / "tests" / "fixtures" / "campaign3" / "synthetic.py"
_MUTATIONS_PATH = _REPO_ROOT / "tests" / "fixtures" / "campaign3" / "mutations.json"

PROMPT_TEMPLATE_HASH = "a" * 64


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _load_synthetic():
    name = "c3_synthetic_fixture"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SYNTHETIC_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fx = _load_synthetic()


def _policy(**overrides):
    return G.build_campaign3_policy(
        prompt_template_hash=PROMPT_TEMPLATE_HASH, **overrides
    )


def _executor(
    *,
    generator,
    evaluator,
    policy=None,
    kpit=None,
    price_table=None,
    boundary=None,
    crash_hook=None,
):
    return G.Campaign3Executor(
        policy=policy or _policy(max_proposal_budget=1),
        generator=generator,
        evaluator=evaluator,
        boundary=boundary if boundary is not None else GeneratorBoundary(),
        kpit=kpit,
        price_table=price_table,
        crash_hook=crash_hook,
    )


def _factory_for_lags(fx_module):
    def factory(visible, feedback, index):
        return fx_module.generation_output(
            fx_module.candidate(fx_module.simple_spec_dict(numerator_lag=index))
        )

    return factory


class _Crash(Exception):
    """A test-local crash injected at a named executor step."""


def _crash_at(step):
    state = {"fired": False}

    def hook(name):
        if name == step and not state["fired"]:
            state["fired"] = True
            raise _Crash(name)

    return hook


class _SpyBoundary(GeneratorBoundary):
    def __init__(self) -> None:
        super().__init__()
        self.ops: list[tuple[str, str]] = []

    def persist(self, event):
        self.ops.append(("persist", event.event_id))
        return super().persist(event)

    def normalize(self, event_id, **kwargs):
        self.ops.append(("normalize", event_id))
        return super().normalize(event_id, **kwargs)


# ---------------------------------------------------------------------------
# 1. policy literals (spec section 4)
# ---------------------------------------------------------------------------


def test_policy_literals_frozen():
    policy = _policy()
    assert policy.generator_identity == "deepseek:deepseek-v4-flash"
    assert policy.seed == 20261012
    assert policy.max_proposal_budget == 100
    assert policy.feedback_channels == (FeedbackChannel.IS_METRICS,)
    assert policy.holdout_visibility is HoldoutVisibility.NONE
    assert policy.max_llm_token_budget == 2_000_000
    assert policy.max_llm_cost_budget == 150.0
    assert policy.prompt_template_hash == PROMPT_TEMPLATE_HASH
    assert _policy(dry_run=True).max_llm_cost_budget == 5.0


def test_policy_vocabulary_superset():
    policy = _policy()
    required = {
        ExpressionOperator.FIELD,
        ExpressionOperator.CONST,
        ExpressionOperator.ADD,
        ExpressionOperator.SUB,
        ExpressionOperator.MUL,
        ExpressionOperator.DIV,
        ExpressionOperator.LAG,
        ExpressionOperator.ROLLING_MEAN,
        ExpressionOperator.ROLLING_STD,
        ExpressionOperator.RANK,
        ExpressionOperator.STANDARDIZE,
    }
    assert required.issubset(set(policy.admissible_vocabulary))
    semantic = set(policy.admissible_semantic_inputs)
    assert {"total_return_index", "daily_total_return", "dollar_volume"}.issubset(
        semantic
    )
    assert {
        "event_ear_k_kp1",
        "event_days_since_ea",
        "event_recent_ea_w30",
    }.issubset(semantic)


def test_ta_evaluation_literals():
    assert E.TA_UNIVERSE == "TOP_1000"
    assert E.TA_N_GROUPS == 5
    assert E.TA_HORIZON == 21
    assert E.TA_COST_BPS == 10.0
    assert E.AUTHORITY.version == "P-Q v2.3"


# ---------------------------------------------------------------------------
# 2. the frozen section-1 order and the write-ahead invariant
# ---------------------------------------------------------------------------


def test_full_pipeline_order():
    gen = fx.ScriptedGenerator(
        outputs=[fx.generation_output(fx.candidate(fx.simple_spec_dict(numerator_lag=21)))]
    )
    ev = fx.StubEvaluator()
    boundary = _SpyBoundary()
    ex = _executor(generator=gen, evaluator=ev, boundary=boundary)
    ev.on_call = lambda proposal, n: pytest.fail("evaluation preceded registration") if (
        ex.registry.get(proposal.proposal_id) is None
    ) else None

    result = ex.run()

    assert ex.registry.get(result.manifest.proposal_ids[0]) is not None
    assert result.manifest.n_used == 1
    assert result.manifest.n_evaluated == 1
    # write-ahead: persist strictly precedes normalize
    names = [op[0] for op in boundary.ops]
    assert names[0] == "persist"
    assert "normalize" in names
    assert names.index("persist") < names.index("normalize")
    # the history holds exactly one IS experiment / evidence
    assert len(result.full_history.experiments) == 1
    assert len(result.full_history.evidence) == 1
    assert result.full_history.evidence[0].fold_evidence[0].role is (
        DevelopmentFoldRole.IS
    )


def test_write_ahead_event_persisted_before_normalization():
    gen = fx.ScriptedGenerator(
        outputs=[fx.generation_output(fx.candidate(fx.simple_spec_dict(numerator_lag=21)))]
    )
    boundary = _SpyBoundary()
    ex = _executor(generator=gen, evaluator=fx.StubEvaluator(), boundary=boundary)
    ex.run()
    names = [op[0] for op in boundary.ops]
    assert names == ["persist", "normalize"]


# ---------------------------------------------------------------------------
# 3. registration before evaluation (mutation X3-M1)
# ---------------------------------------------------------------------------


def test_registration_before_evaluation():
    gen = fx.ScriptedGenerator(
        outputs=[fx.generation_output(fx.candidate(fx.simple_spec_dict(numerator_lag=21)))]
    )
    seen: list[bool] = []
    ev = fx.StubEvaluator()
    ex = _executor(generator=gen, evaluator=ev)
    ev.on_call = lambda proposal, n: seen.append(
        ex.registry.get(proposal.proposal_id) is not None
    )
    ex.run()
    assert seen == [True]


# ---------------------------------------------------------------------------
# 4. budget exhaustion at exactly N (mutation X3-M6)
# ---------------------------------------------------------------------------


def test_budget_exhaustion_at_exactly_n():
    gen = fx.ScriptedGenerator(factory=_factory_for_lags(fx))
    ex = _executor(
        generator=gen,
        evaluator=fx.StubEvaluator(),
        policy=_policy(max_proposal_budget=3),
    )
    result = ex.run()
    assert result.manifest.n_used == 3
    assert ex.generator_calls == 3
    assert result.manifest.n_evaluated == 3
    assert result.manifest.stop_reason == (
        G.Campaign3StopReason.PROPOSAL_BUDGET_EXHAUSTED.value
    )


# ---------------------------------------------------------------------------
# 5. NOT_CERTIFIED counted (mutation X3-M2)
# ---------------------------------------------------------------------------


def test_not_certified_candidate_counted():
    gen = fx.ScriptedGenerator(
        outputs=[
            fx.generation_output(
                fx.candidate(fx.simple_spec_dict(numerator_lag=21, sign=-1))
            )
        ]
    )
    ev = fx.StubEvaluator()
    ex = _executor(generator=gen, evaluator=ev)
    result = ex.run()
    assert result.manifest.n_used == 1
    assert result.manifest.n_proposals == 1
    assert result.manifest.n_not_certified == 1
    assert result.manifest.n_certified == 0
    assert result.manifest.n_evaluated == 0
    assert ev.calls == []
    assert result.manifest.stop_reason == "proposal_budget_exhausted"


# ---------------------------------------------------------------------------
# 6. duplicate collapse
# ---------------------------------------------------------------------------


def test_duplicate_collapse():
    output = fx.generation_output(
        fx.candidate(fx.simple_spec_dict(numerator_lag=21))
    )
    gen = fx.ScriptedGenerator(outputs=[output, output])
    ex = _executor(
        generator=gen,
        evaluator=fx.StubEvaluator(),
        policy=_policy(max_proposal_budget=2),
    )
    result = ex.run()
    assert result.manifest.n_used == 2
    assert result.manifest.n_proposals == 1
    assert result.manifest.n_duplicates == 1
    assert result.manifest.n_evaluated == 1


def test_duplicate_within_one_event_collapses():
    payload = fx.raw_artifact(
        fx.candidate(fx.simple_spec_dict(numerator_lag=21)),
        fx.candidate(fx.simple_spec_dict(numerator_lag=21)),
    )
    gen = fx.ScriptedGenerator(
        outputs=[G.GenerationOutput(raw_artifact=payload, tokens_used=10)]
    )
    ex = _executor(generator=gen, evaluator=fx.StubEvaluator())
    result = ex.run()
    assert result.manifest.n_proposals == 1
    assert result.manifest.n_evaluated == 1


# ---------------------------------------------------------------------------
# 6b. malformed / rejected output is recorded and counted, never resampled
# ---------------------------------------------------------------------------


def test_malformed_raw_artifact_recorded_and_counted():
    from smart_beta.research.generator import RawArtifact

    gen = fx.ScriptedGenerator(
        outputs=[
            G.GenerationOutput(
                raw_artifact=RawArtifact(content="this is not json"),
                tokens_used=7,
            )
        ]
    )
    boundary = GeneratorBoundary()
    ex = _executor(generator=gen, evaluator=fx.StubEvaluator(), boundary=boundary)
    result = ex.run()
    assert result.manifest.n_used == 1
    assert result.manifest.n_proposals == 0
    assert result.manifest.n_evaluated == 0
    # the malformed event remains auditable (recorded, not discarded)
    event = ex.events[0]
    outcome = boundary.outcome_for(event.event_id)
    assert outcome is not None
    assert outcome.status.value == "malformed"


def test_budget_counts_not_certified_events():
    bad_one = fx.generation_output(
        fx.candidate(fx.simple_spec_dict(numerator_lag=21, sign=-1))
    )
    bad_two = fx.generation_output(
        fx.candidate(fx.simple_spec_dict(numerator_lag=22, sign=-1))
    )
    gen = fx.ScriptedGenerator(outputs=[bad_one, bad_two])
    ex = _executor(
        generator=gen,
        evaluator=fx.StubEvaluator(),
        policy=_policy(max_proposal_budget=2),
    )
    result = ex.run()
    assert result.manifest.n_used == 2
    assert result.manifest.n_proposals == 2
    assert result.manifest.n_not_certified == 2
    assert result.manifest.n_evaluated == 0
    assert ex.generator_calls == 2


# ---------------------------------------------------------------------------
# 7. crash at each step -> reconcile, no extra candidate
# ---------------------------------------------------------------------------


_POST_PERSIST_STEPS = (
    "after_persist_before_normalize",
    "after_normalize_before_register",
    "after_register_before_certify",
    "after_certify_before_freeze",
    "after_freeze_before_evaluate",
    "after_evaluate_before_history",
)


@pytest.mark.parametrize("step", _POST_PERSIST_STEPS)
def test_crash_after_persist_reconciles_without_regeneration(step):
    gen = fx.ScriptedGenerator(
        outputs=[fx.generation_output(fx.candidate(fx.simple_spec_dict(numerator_lag=21)))]
    )
    ev = fx.StubEvaluator()
    ex = _executor(generator=gen, evaluator=ev, crash_hook=_crash_at(step))
    with pytest.raises(_Crash):
        ex.run()
    result = ex.run()
    assert ex.generator_calls == 1
    assert result.manifest.n_used == 1
    assert result.manifest.n_evaluated == 1
    assert len(result.full_history.evidence) == 1
    assert result.manifest.stop_reason == "proposal_budget_exhausted"


def test_crash_reconcile_no_regeneration():
    """A dedicated (non-parametrized) contract for mutation X3-M4."""
    gen = fx.ScriptedGenerator(
        outputs=[fx.generation_output(fx.candidate(fx.simple_spec_dict(numerator_lag=21)))]
    )
    ex = _executor(
        generator=gen,
        evaluator=fx.StubEvaluator(),
        crash_hook=_crash_at("after_persist_before_normalize"),
    )
    with pytest.raises(_Crash):
        ex.run()
    result = ex.run()
    assert ex.generator_calls == 1
    assert result.manifest.n_evaluated == 1


def test_crash_before_persist_loses_only_the_unrecorded_output():
    gen = fx.ScriptedGenerator(
        outputs=[fx.generation_output(fx.candidate(fx.simple_spec_dict(numerator_lag=21)))]
    )
    ex = _executor(
        generator=gen,
        evaluator=fx.StubEvaluator(),
        crash_hook=_crash_at("after_generate_before_persist"),
    )
    with pytest.raises(_Crash):
        ex.run()
    # Nothing was written ahead, so the unrecorded output is simply regenerated.
    assert ex.events == ()
    result = ex.run()
    assert ex.generator_calls == 2
    assert result.manifest.n_used == 1
    assert result.manifest.n_evaluated == 1


# ---------------------------------------------------------------------------
# 8. the firewall (T-B / T-C never visible)
# ---------------------------------------------------------------------------


def test_assert_ta_only_refuses_non_is_fold():
    visible = GeneratorVisibleResearchHistory(
        experiments=(
            VisibleExperiment(
                experiment_id="1" * 64,
                hypothesis_id="2" * 64,
                family_id="3" * 64,
                fold_evidence=(
                    FoldEvidence(
                        fold_key="oos:2020",
                        role=DevelopmentFoldRole.OOS,
                        metrics=(MetricValue(name="x", value=1.0, n_obs=1),),
                    ),
                ),
            ),
        )
    )
    ex = _executor(generator=fx.ScriptedGenerator(factory=_factory_for_lags(fx)),
                   evaluator=fx.StubEvaluator())
    with pytest.raises(G.Campaign3FirewallError):
        ex._assert_ta_only(visible)


def test_eval_bridge_refuses_non_is_fold():
    partition = Partition(
        folds=(
            Fold(FoldRole.IS, date(2020, 1, 1), date(2020, 6, 1)),
            Fold(FoldRole.OOS, date(2020, 6, 2), date(2020, 12, 1)),
        ),
        split_rule="c3-firewall-test",
    )
    oos = partition.folds[1]
    with pytest.raises(E.Campaign3FirewallError):
        E.assert_is_fold(oos)


def test_feedback_is_is_only():
    gen = fx.ScriptedGenerator(factory=_factory_for_lags(fx))
    ex = _executor(
        generator=gen,
        evaluator=fx.StubEvaluator(),
        policy=_policy(max_proposal_budget=2),
    )
    ex.run()
    # The second generation sees the first candidate's IS evidence only.
    feedback = gen.calls[1][1]
    assert feedback.experiments
    assert feedback.experiments[0].is_folds
    for experiment in feedback.experiments:
        assert experiment.oos_folds == ()
        assert experiment.walk_forward_folds == ()


# ---------------------------------------------------------------------------
# 9. K-PIT GENERATOR record precedes the diagnostics (mutation X3-M5)
# ---------------------------------------------------------------------------


def test_kpit_freeze_precedes_evaluation(tmp_path):
    log = KnowledgeLog(tmp_path / "campaign3_test_kpit.jsonl")
    writer = G.KnowledgeLogKpitWriter(log)
    seen: dict[str, bool] = {"freeze_visible": False}
    ev = fx.StubEvaluator()

    gen = fx.ScriptedGenerator(
        outputs=[fx.generation_output(fx.candidate(fx.simple_spec_dict(numerator_lag=21)))]
    )
    ex = _executor(generator=gen, evaluator=ev, kpit=writer)

    def on_call(proposal, n):
        records = log.read()
        seen["freeze_visible"] = any(
            record.kind is RecordKind.HYPOTHESIS_FREEZE
            and record.payload.get("proposal_id") == proposal.proposal_id
            for record in records
        )
        assert any(record.channel is Channel.GENERATOR for record in records)

    ev.on_call = on_call
    result = ex.run()

    assert seen["freeze_visible"] is True
    records = log.read()
    kinds = [record.kind for record in records]
    assert kinds == [RecordKind.GENERATOR_INPUT, RecordKind.HYPOTHESIS_FREEZE]
    assert tuple(record.record_hash for record in records) == (
        result.manifest.kpit_record_hashes
    )


# ---------------------------------------------------------------------------
# 10. generator failure is recorded, never retried
# ---------------------------------------------------------------------------


def test_generator_failure_not_retried():
    gen = fx.ScriptedGenerator(
        outputs=[fx.generation_output(fx.candidate(fx.simple_spec_dict()))],
        raise_on={0},
    )
    ex = _executor(generator=gen, evaluator=fx.StubEvaluator())
    result = ex.run()
    assert ex.generator_calls == 1
    assert result.manifest.n_used == 0
    assert result.manifest.stop_reason == G.Campaign3StopReason.GENERATOR_FAILURE.value


# ---------------------------------------------------------------------------
# 11. determinism given the stub
# ---------------------------------------------------------------------------


def test_determinism_given_the_stub():
    def run_once():
        gen = fx.ScriptedGenerator(factory=_factory_for_lags(fx))
        ex = _executor(
            generator=gen,
            evaluator=fx.StubEvaluator(),
            policy=_policy(max_proposal_budget=3),
        )
        return ex.run().manifest

    first = run_once()
    second = run_once()
    assert first.history_hash == second.history_hash
    assert first.generation_event_hashes == second.generation_event_hashes
    assert first.proposal_ids == second.proposal_ids
    assert first.registry_snapshot_hash == second.registry_snapshot_hash


# ---------------------------------------------------------------------------
# 12. no production K-PIT write without an injected writer
# ---------------------------------------------------------------------------


def test_no_kpit_records_without_writer():
    gen = fx.ScriptedGenerator(factory=_factory_for_lags(fx))
    ex = _executor(
        generator=gen,
        evaluator=fx.StubEvaluator(),
        policy=_policy(max_proposal_budget=1),
    )
    result = ex.run()
    assert result.manifest.kpit_record_hashes == ()


# ---------------------------------------------------------------------------
# 13. source guards
# ---------------------------------------------------------------------------


def _module_tree(relative: str) -> ast.Module:
    return ast.parse((_REPO_ROOT / relative).read_text(encoding="utf-8"))


def test_no_research_loop_dependency():
    tree = _module_tree("smart_beta/campaign/campaign3_generation.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "smart_beta.research.loop"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "smart_beta.research.loop"
    assert "import ResearchLoop" not in (
        _REPO_ROOT / "smart_beta/campaign/campaign3_generation.py"
    ).read_text(encoding="utf-8")


def test_no_network_sdk_import():
    for relative in (
        "smart_beta/campaign/campaign3_generation.py",
        "smart_beta/campaign/campaign3_eval.py",
    ):
        tree = _module_tree(relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in ("anthropic", "requests", "urllib")
            if isinstance(node, ast.ImportFrom):
                assert node.module not in ("anthropic", "requests", "urllib.request")


# ---------------------------------------------------------------------------
# 14. mutation fixture contract (planner applies X3-M1..X3-M6)
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract():
    mutations = json.loads(_MUTATIONS_PATH.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        f"X3-M{i}" for i in range(1, 7)
    ]
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        assert mutation["file"] == "smart_beta/campaign/campaign3_generation.py"
        source = (_REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None, mutation["id"]
            assert source.count(edit["old"]) == 1, mutation["id"]
            assert edit["old"] != edit["new"], mutation["id"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert test_name, mutation["id"]
        module_source = (_REPO_ROOT / module_name).read_text(encoding="utf-8")
        assert f"def {test_name.split('[')[0]}" in module_source, mutation["id"]


# ---------------------------------------------------------------------------
# 15. integration with the real P-Q v2.3 evaluation bridge (PQ2-H fixture)
# ---------------------------------------------------------------------------


def _load_pq2h_fixture():
    name = "c3_pq2h_fixture"
    if name in sys.modules:
        return sys.modules[name]
    path = _REPO_ROOT / "tests" / "fixtures" / "pqv2" / "evaluation" / "fixture.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_integration_pqv2_v23_is_only(tmp_path):
    hf = _load_pq2h_fixture()
    store = hf.SyntheticStore(tmp_path, name="root")
    store.standard()
    store.build_d7b()
    store.build_sources()
    pq_bindings, identity_hash = store.build_identity()
    factor_spec = hf.load_campaign_spec("C1-H1")
    inputs = hf.build_inputs(store)
    partition, fold, _holdout = hf.partition()

    def declaration_builder(spec, panel):
        base = hf.base_declaration(spec, panel)
        return E.build_ta_declaration(
            base,
            dataset_id=hf.Q.DATASET_ID,
            dataset_identity_hash=identity_hash,
            pq_id="pqv2h",
            pq_hash="pqv2h-hash",
        )

    base0 = hf.base_declaration(
        factor_spec, hf.factor_panel(factor_spec, inputs, store)
    )
    declaration0 = E.build_ta_declaration(
        base0,
        dataset_id=hf.Q.DATASET_ID,
        dataset_identity_hash=identity_hash,
        pq_id="pqv2h",
        pq_hash="pqv2h-hash",
    )
    overlay = store.overlay(pq_id=declaration0.pq_id, pq_hash=declaration0.pq_hash)
    evaluator = E.TaEvaluator(
        root=store.root,
        partition=partition,
        fold=fold,
        overlay=overlay,
        inputs=inputs,
        declaration_builder=declaration_builder,
        panel_builder=lambda spec: hf.factor_panel(spec, inputs, store),
        bindings=store.d7b_bindings,
        source_bindings=store.source_bindings,
        pq_bindings=pq_bindings,
        pq_read_records_fn=lambda path: hf.genesis_records(),
        persist_dir=tmp_path / "diagnostics",
    )

    gen = fx.ScriptedGenerator(
        outputs=[fx.generation_output(fx.candidate(factor_spec.to_dict()))]
    )
    ex = _executor(generator=gen, evaluator=evaluator)
    result = ex.run()

    assert result.manifest.n_used == 1
    assert result.manifest.n_proposals == 1
    assert result.manifest.n_evaluated == 1
    evaluation = result.evaluations[0]
    assert evaluation.assessed is True
    assert evaluation.is_fold_key.startswith("is")
    names = {metric.name for metric in evaluation.metrics}
    assert "b-t0_net_long_short_mean" in names
    assert "b-t0_hac_t" in names
    assert "l-t0_net_long_short_mean" in names
    assert "is_assessable" in names
    # the diagnostics were persisted write-ahead at their content address
    artifact = tmp_path / "diagnostics" / f"{evaluation.diagnostics_hash}.json"
    assert artifact.is_file()
    # the built history carries exactly the IS evidence
    assert len(result.full_history.evidence) == 1
    fold_evidence = result.full_history.evidence[0].fold_evidence
    assert len(fold_evidence) == 1
    assert fold_evidence[0].role is DevelopmentFoldRole.IS
    assert result.manifest.diagnostics_hashes == (evaluation.diagnostics_hash,)
