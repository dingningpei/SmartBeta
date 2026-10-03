"""C1-LX-E4 definition-based hypothesis identity tests.

Authority: ``worker_tasks/campaign1/task-c1-ledger-executor-erratum-e4.md``
governed by ``docs/campaign1_identity_architecture_correction.md``.

Tests 1 and 7 of C1-LX-E4 section 4.4 read the real D7c ``trusted_inputs.json``
manifest bytes and the repository engine-semantics code files only. They read
no parquet and no data value, evaluate no factor, write no production
Knowledge-PIT record and make no network call.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from smart_beta.campaign import campaign1 as C
from smart_beta.experiment.registry import (
    canonical_json,
    content_hash,
    hypothesis_id_for,
)
from smart_beta.science.contracts import RecordKind
from smart_beta.science.knowledge import GENESIS_PREV_HASH, KnowledgeRecord

#: ``smart_beta/dataset_v1/acquisition.py`` DEFAULT_ROOT.
_DATASET_ROOT = Path(
    os.path.expanduser(
        "~/Developer/personal/smart_beta/data_store/discovery_dataset_v1"
    )
)
_MANIFEST_PATH = _DATASET_ROOT / C.TI_MANIFEST_RELATIVE

EXPECTED_MANIFEST_SHA256 = (
    "d8d40099ba978604727a7d316274a2d3e7f8210baf8cb35e4ca687435fd8f8f0"
)
EXPECTED_TI_ID = "ti-aa23fabd3db4e595"
EXPECTED_TI_HASH = (
    "30ee0c51602cf792e8cb7654c2501f52c7d664675dbf78845c903df6683c2ae6"
)

_FACTOR_SPEC_HASHES = (
    "9b27eb881a7b31bfe123866f82996638315be6257c333fcf04cfcae3a02f037c",
    "85a7166bf2953fd9240037dc5189cb63dd3ae93690b2574c2f6d0567136d54fc",
    "78a6d4547aecee3615841408be1df040fdb33037fe465b790bcd75c5d76fe99f",
    "54b7edf3c0f00e5c53c8d87eee7a9c9bfd26287e15b04dc8ac38f57d3e4f1a6f",
    "ee5b8ea8f57262945828d545910584b6b49546af344c9a2bfbd2164380564f26",
    "42605a13a41cf0c9a04b614424b4bee1643bd565b1ec5b67c58a4b6d6ed816a1",
    "32678dfda88647a666994e62697742955f7903b141431a628b3df98895be66b0",
)
_DEFINITION_HASHES = (
    "ce26a34da8a62912f10a50ad9c25447b62ecb1b56e2d5a031913691785420e14",
    "e2503e5240cce5b38071af96d19d11e118ec78dd84ae4152415a6ed6ce70fce9",
    "6bd890da620263418709623fc23963cb82e258b2b2bfc8a01f600e132be15da2",
    "095b126b5227c180bd49568daa02222c3e77ee460917b5e2f4d263ab1e2cc1d0",
    "e859f7aa604b3fca1a19b2ea16e87ec5d056064a6e15c4206ebba2727a8b176a",
    "ba7714468f9b3641e249764e1b076491b32531b16a2bfd5da51e2c29b6dad7ac",
    "efd345b3c280ca81905b7a6e655e2e30ff4c4e8b01173fa949fea4dddfaba8d3",
)
_HYPOTHESIS_IDS = tuple(C.EXPECTED_HYPOTHESIS_IDS[label] for label in C.EXPECTED_LABELS)

#: The disclosed OLD-rule C1-H1 id (Correction section 4), not an identity.
LEGACY_C1_H1_ID = (
    "09bb4ecffce522163709df816ef2a8bdbe8bbb2414a6c27c1d8baa9b80b038b1"
)


def _real_definition(spec) -> dict:
    return C.hypothesis_definition(spec, root=_DATASET_ROOT)


# ---------------------------------------------------------------------------
# 1. The 7 definitions and ids reproduce the Correction exactly
# ---------------------------------------------------------------------------


def test_definitions_and_ids_reproduce_the_correction():
    manifest_bytes = _MANIFEST_PATH.read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    assert manifest_sha256 == EXPECTED_MANIFEST_SHA256
    manifest = json.loads(manifest_bytes)
    assert manifest["ti_id"] == EXPECTED_TI_ID
    assert manifest["ti_hash"] == EXPECTED_TI_HASH

    campaign_level = {
        "program_id",
        "campaign1_id",
        "estimand",
        "estimand_policy",
        "universe",
        "screen_id",
        "screen_policy_hash",
        "search_policy",
        "budgets",
    }
    for spec, spec_hash, definition_hash, hypothesis_id in zip(
        C.FACTOR_SPECS, _FACTOR_SPEC_HASHES, _DEFINITION_HASHES, _HYPOTHESIS_IDS
    ):
        definition = _real_definition(spec)
        assert set(definition) == {
            "schema",
            "factor_spec_hash",
            "trusted_inputs",
            "engine_semantics",
        }
        assert set(definition).isdisjoint(campaign_level)
        assert definition["schema"] == "campaign1-hypothesis-definition-v1"
        assert set(definition["trusted_inputs"]) == {
            "ti_id",
            "ti_hash",
            "manifest_sha256",
        }
        assert definition["trusted_inputs"] == {
            "ti_id": EXPECTED_TI_ID,
            "ti_hash": EXPECTED_TI_HASH,
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        }
        assert tuple(definition["engine_semantics"]) == C.ENGINE_SEMANTICS_FILES
        assert len(definition["engine_semantics"]) == 10
        assert definition["factor_spec_hash"] == spec_hash
        assert C.hypothesis_definition_hash(definition) == definition_hash
        assert C.campaign1_hypothesis_id(definition_hash) == hypothesis_id

    identities = C.hypothesis_identities(_DATASET_ROOT)
    assert [item["label"] for item in identities] == list(C.EXPECTED_LABELS)
    assert [item["factor_spec_hash"] for item in identities] == list(
        _FACTOR_SPEC_HASHES
    )
    assert [item["hypothesis_definition_hash"] for item in identities] == list(
        _DEFINITION_HASHES
    )
    assert [item["hypothesis_id"] for item in identities] == list(_HYPOTHESIS_IDS)


def test_expected_hypothesis_ids_match_the_correction():
    assert C.EXPECTED_HYPOTHESIS_IDS == dict(zip(C.EXPECTED_LABELS, _HYPOTHESIS_IDS))
    assert len(set(_HYPOTHESIS_IDS)) == 7
    assert LEGACY_C1_H1_ID not in _HYPOTHESIS_IDS


def test_hypothesis_identities_rejects_a_wrong_expected_id():
    wrong = dict(C.EXPECTED_HYPOTHESIS_IDS)
    wrong["C1-H1"] = "0" * 64
    with pytest.raises(C.C1BindingError):
        C.hypothesis_identities(_DATASET_ROOT, expected_ids=wrong)


# ---------------------------------------------------------------------------
# 2. Component sensitivity (fixture-based; the frozen inputs are not mutated)
# ---------------------------------------------------------------------------


def _base_definition() -> dict:
    return {
        "schema": C.HYPOTHESIS_DEFINITION_SCHEMA,
        "factor_spec_hash": _FACTOR_SPEC_HASHES[0],
        "trusted_inputs": {
            "ti_id": EXPECTED_TI_ID,
            "ti_hash": EXPECTED_TI_HASH,
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        },
        "engine_semantics": {
            relative: "c" * 64 for relative in C.ENGINE_SEMANTICS_FILES
        },
    }


def _variant(base: dict) -> dict:
    return json.loads(json.dumps(base))


def test_changing_each_definition_component_changes_the_id():
    base = _base_definition()
    base_hash = C.hypothesis_definition_hash(base)
    base_id = C.campaign1_hypothesis_id(base_hash)

    variants: list[dict] = []

    changed = _variant(base)
    changed["factor_spec_hash"] = "f" * 64
    variants.append(changed)

    changed = _variant(base)
    changed["trusted_inputs"]["ti_id"] = "ti-0000000000000000"
    variants.append(changed)

    changed = _variant(base)
    changed["trusted_inputs"]["ti_hash"] = "d" * 64
    variants.append(changed)

    changed = _variant(base)
    changed["trusted_inputs"]["manifest_sha256"] = "e" * 64
    variants.append(changed)

    changed = _variant(base)
    changed["engine_semantics"][C.ENGINE_SEMANTICS_FILES[0]] = "9" * 64
    variants.append(changed)

    changed = _variant(base)
    del changed["engine_semantics"][C.ENGINE_SEMANTICS_FILES[0]]
    variants.append(changed)

    changed = _variant(base)
    changed["engine_semantics"]["smart_beta/extra.py"] = "8" * 64
    variants.append(changed)

    changed = _variant(base)
    changed["schema"] = "campaign1-hypothesis-definition-v2"
    variants.append(changed)

    for variant in variants:
        assert C.hypothesis_definition_hash(variant) != base_hash

    # The domain tag separates ids for the same definition hash.
    assert (
        content_hash(
            {
                "domain": "not-campaign1-hypothesis-id-v1",
                "hypothesis_definition_hash": base_hash,
            }
        )
        != base_id
    )


# ---------------------------------------------------------------------------
# 3. Zero factor evaluation
# ---------------------------------------------------------------------------


def test_hypothesis_identities_performs_no_factor_evaluation(monkeypatch):
    calls = {"evaluate": 0, "load": 0, "parquet": 0}

    def spy_evaluate(*args, **kwargs):
        calls["evaluate"] += 1
        raise AssertionError("evaluate_factor was called")

    def spy_load(*args, **kwargs):
        calls["load"] += 1
        raise AssertionError("load_trusted_inputs was called")

    def spy_parquet(*args, **kwargs):
        calls["parquet"] += 1
        raise AssertionError("read_parquet was called")

    import pandas as pd

    monkeypatch.setattr(C, "_evaluate_factor", spy_evaluate)
    monkeypatch.setattr(C, "load_trusted_inputs", spy_load)
    monkeypatch.setattr(pd, "read_parquet", spy_parquet)

    identities = C.hypothesis_identities(_DATASET_ROOT)
    assert [item["hypothesis_id"] for item in identities] == list(_HYPOTHESIS_IDS)
    assert calls == {"evaluate": 0, "load": 0, "parquet": 0}


# ---------------------------------------------------------------------------
# 4. Domain separation
# ---------------------------------------------------------------------------


def test_no_campaign_id_is_a_phase8_hypothesis_id():
    for spec in C.FACTOR_SPECS:
        definition = _real_definition(spec)
        definition_hash = C.hypothesis_definition_hash(definition)
        hypothesis_id = C.campaign1_hypothesis_id(definition_hash)
        assert hypothesis_id != hypothesis_id_for(definition_hash)
        assert hypothesis_id != hypothesis_id_for(hypothesis_id)


def test_campaign_package_has_no_hypothesis_id_for(tmp_path):
    package = Path(C.__file__).resolve().parent
    for path in sorted(package.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "hypothesis_id_for" not in source, path


# ---------------------------------------------------------------------------
# 7. K-PIT preflight accepts the new ids and rejects the legacy id
# ---------------------------------------------------------------------------


def _build_klog(path, *, identities) -> Path:
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
            program_id=C.PROGRAM_ID,
            refs=refs or {},
        )
        records.append(record)
        return record

    policy = C.estimand_policy()
    estimand = add(
        RecordKind.HUMAN_DECISION,
        {
            "decision_kind": "ESTIMAND_POLICY",
            "actor_role": "OPERATOR",
            "consulted_all_prior": True,
            **policy.to_content(),
        },
    )
    freeze = add(
        RecordKind.HUMAN_DECISION,
        {
            "decision_kind": "PROGRAM_FREEZE",
            "actor_role": "OPERATOR",
            "consulted_all_prior": True,
        },
    )
    for item in identities:
        add(
            RecordKind.HYPOTHESIS_FREEZE,
            {
                "hypothesis_id": item["hypothesis_id"],
                "factor_spec_hash": item["factor_spec_hash"],
            },
            refs={"influenced_by": (estimand.record_hash, freeze.record_hash)},
        )
    path.write_text(
        "\n".join(canonical_json(record.to_dict()) for record in records) + "\n",
        encoding="utf-8",
    )
    return path


def test_kpit_preflight_accepts_new_ids_and_rejects_legacy(tmp_path):
    identities = C.hypothesis_identities(_DATASET_ROOT)
    good = _build_klog(tmp_path / "good.jsonl", identities=identities)
    result = C.verify_kpit_ordering(good, identities=identities)
    assert result.ok is True
    assert len(result.record_hashes) == 9

    legacy = [dict(item) for item in identities]
    legacy[0]["hypothesis_id"] = LEGACY_C1_H1_ID
    bad = _build_klog(tmp_path / "bad.jsonl", identities=legacy)
    assert C.verify_kpit_ordering(bad, identities=identities).ok is False
