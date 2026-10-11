"""C3-L — Campaign-3 production launcher and real-data preflight.

Authority: the frozen ``docs/campaign3_p6_implementation_spec.md`` (task row
C3-L, section 1 row C3-L and section 2 mutations L-M1..L-M4) as amended by
**Erratum P6-E2** and **Erratum P6-E3** (APPROVED; the latter plus its
2026-10-10 correction), on top of the frozen
``docs/campaign3_p5_precommitment.md`` (+E1),
``docs/campaign3_p4_amendment_a2_deepseek.md`` (+A2-E1) and
``docs/campaign3_kpit_dataset_reference_note.md``.

What this module owns
---------------------

C3-R's :class:`~smart_beta.campaign.campaign3_run.Campaign3Run` takes **every**
dependency by injection; nothing assembled those dependencies from the real
Dataset v1.2 / overlay / D7c / v1.3 event-layer stores.  This module fills that
gap and adds the two CLI modes:

* :func:`assemble_production` builds the whole dependency set from the real
  stores (by default) or from caller-supplied loaders (the offline tests):
  the frozen partition folds (IS for T-A; OOS + DEVELOPMENT_SEALED for T-B),
  the Campaign-1 production ``panel_of`` adapter (no new factor math), the
  ``AUTHORITY_V2_3`` declarations, the ``PRODUCTION_REGISTRY`` with the
  admitted ``EWC_T23_ONE_SIDED`` and its K-PIT ``PROCEDURE_ADMISSION`` records,
  the real T-A/T-B evaluators, the C3-S candidate builder and the DeepSeek
  generator.
* the per-candidate **panel builder** (P6-E3) takes the Campaign-1 adapter's
  frozen ``(formation, universe member)`` row grid only and fills the values
  from the Phase-6 specification evaluator over exactly
  ``pqv2_evaluation._alias_frames(spec, inputs, overlay)`` (the sealed
  ``_scenario_panel`` realization).  A cross-sectional candidate's declared
  panel is therefore byte-identical to the one P-Q checks in
  ``_realize_std_x_rank``.
* ``--preflight`` runs on a **copy** of the production K-PIT, in a scratch run
  directory.  The real T-A evaluator evaluates the ten fixed **P6-E4** probes
  (the EVENT_SIMPLE positive control, SIMPLE momentum/dv-ratio/ret-std probes,
  the certified STD_X_RANK probes and the mixed EVENT_SIMPLE probes) and the
  ``pqv2_series`` export is bound for each; every probe first asserts its
  planner-verified ``certify_candidate`` class (a mismatch is a spec defect).
  Only ``status`` / ``assessed`` / ``NOT_ASSESSED`` reason / ``binding_ok`` /
  per-scenario period counts are printed or written; a probe that raises is
  reported as ``FAILED`` with its exception type and **no** marker is written.
  On success it writes a preflight marker (master SHA + bindings hash + K-PIT
  source hash) next to the scratch run directory.
* ``--production`` requires a matching preflight marker and then performs the
  authorized real run.  **T-C is never started** (the reserve fold is
  structurally absent).

Firewalls / evidence rules
--------------------------

* The DeepSeek credential is read **only** by the generator class itself
  (``Campaign3DeepSeekGenerator`` when ``client is None``); this module never
  reads the environment or a file for a credential.
* The preflight emits no mean, t, p, return or ranking value anywhere outside
  its scratch directory; :func:`assert_statistic_free` is the fail-closed
  guard and mutation L-M2's target.
* The T-B folds are exactly the frozen partition's OOS + HOLDOUT
  (DEVELOPMENT_SEALED) folds; a reserve/``HOLDOUT``-named T-C fold is never
  constructed (mutation L-M4's target).

Tests are offline and synthetic: every loader is injected, no production
artifact, ``data_store``, reserve, holdout or network path is touched, and the
K-PIT is a disposable copy.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smart_beta.campaign import campaign3_eval as _eval
from smart_beta.campaign import campaign3_run as _run
from smart_beta.evaluation.partition import Fold, FoldRole, Partition
from smart_beta.research.policy import ResearchPolicy
from smart_beta.research.generator import RawArtifact
from smart_beta.science.contracts import content_hash
from smart_beta.science.knowledge import (
    Channel,
    KnowledgeLog,
    KnowledgeSnapshot,
    RecordKind,
    read_records,
)
from smart_beta.spec.factor_spec import FactorSpec, factor_spec_hash
from smart_beta.spec.factor_spec import from_dict as factor_spec_from_dict
from smart_beta.spec.factor_spec import to_dict as factor_spec_to_dict

__all__ = [
    "LAUNCH_SCHEMA",
    "PREFLIGHT_MARKER_NAME",
    "PREFLIGHT_STATUS_OK",
    "PREFLIGHT_RECORD_SCHEMA",
    "LaunchError",
    "PreflightRequired",
    "PreflightError",
    "PreflightProbeFailure",
    "StatisticLeakError",
    "control_factor_spec",
    "control_raw_artifact",
    "simple_factor_spec",
    "std_x_rank_factor_spec",
    "PreflightProbe",
    "preflight_probes",
    "FixedArtifactGenerator",
    "build_production_policy",
    "production_partition_folds",
    "compute_attempt_id",
    "persist_ta_provenance",
    "ProductionAssembly",
    "assemble_production",
    "preflight_summary",
    "assert_statistic_free",
    "run_preflight",
    "run_production",
    "main",
]

#: The frozen CLI/marker schema tags.
LAUNCH_SCHEMA = "campaign3-launch-v1"
PREFLIGHT_MARKER_NAME = "campaign3_preflight.json"
PREFLIGHT_STATUS_OK = "PREFLIGHT_OK"
PREFLIGHT_RECORD_SCHEMA = "campaign3-preflight-probe-v1"

#: The C3-L-R3 persisted T-A provenance sidecar (P6-E5 item 3).  The JSON
#: generation snapshot does not carry the FactorSpec, so a cold-resume screen
#: builder cannot rebuild the ``pqv2_series`` export without it.  The record is
#: written write-once next to the content-addressed diagnostics artifact and is
#: keyed by ``diagnostics_hash``.
TA_PROVENANCE_SCHEMA = "campaign3-ta-provenance-v1"
TA_PROVENANCE_SUFFIX = ".provenance.json"

#: The single frozen DeepSeek prompt-template hash (A2-E2).  Only consulted
#: when a generator exposes no ``prompt_template_hash``.
_FALLBACK_PROMPT_TEMPLATE_HASH: str | None = None


class LaunchError(RuntimeError):
    """Base class for every C3-L fail-closed error."""


class PreflightRequired(LaunchError):
    """``--production`` was requested without a matching preflight marker."""


class PreflightError(LaunchError):
    """The preflight itself failed or an invariant was violated."""


class PreflightProbeFailure(PreflightError):
    """One or more fixed P6-E4 preflight probes raised during evaluation.

    Carries the statistic-free summary (including each ``FAILED`` probe's
    exception type) so the caller can report it.  No preflight marker is
    ever written when this is raised.
    """

    def __init__(
        self,
        summary: Mapping[str, Any],
        failures: Sequence[tuple[str, str]],
    ) -> None:
        self.summary = dict(summary)
        self.failures = tuple(failures)
        detail = ", ".join(f"{name} ({kind})" for name, kind in self.failures)
        super().__init__(f"preflight probe(s) FAILED: {detail}")


class StatisticLeakError(LaunchError):
    """A forbidden statistic was printed or written outside the scratch dir."""


# ---------------------------------------------------------------------------
# the positive control FactorSpec / fixed raw artifact
# ---------------------------------------------------------------------------


def _control_requirement(control: _run.ControlHypothesis) -> dict[str, Any]:
    """The v1.3 event-factor requirement of the frozen control.

    Identical in shape to the PQ23-H fixture's event input: ``event_ear_k_kp1``
    is a *period* fraction, every other event role a count instant.  The
    frozen control is always the ``event_ear_k_kp1`` primitive, so the period
    fraction branch is the only one reached.
    """
    from smart_beta.dataset_v1 import pqv2_events as _events

    semantic_id = control.to_content()["semantic_id"]
    period = (
        "period"
        if semantic_id == _events.EVENT_EA_KKP1
        else "instant"
    )
    units = "fraction" if semantic_id == _events.EVENT_EA_KKP1 else "count"
    return {
        "semantic_id": semantic_id,
        "frequency": "daily",
        "observation_period": period,
        "units": units,
        "lookback": 0,
        "revision_policy": "point_in_time",
        "require_knowledge_date": True,
        "require_positive_vintage_identity": False,
    }


def control_factor_spec(control: _run.ControlHypothesis = _run.CONTROL) -> FactorSpec:
    """Build the frozen control's :class:`FactorSpec`.

    The control is the ``event_ear_k_kp1`` field held for one month
    (``ControlHypothesis.hold_months == 1``, which is the declaration's
    ``h = 21`` horizon) with ``sign = +1``.  The spec is built through the
    frozen ``factor_spec_from_dict`` parser — the same path a generator
    candidate takes — so it can never drift from the admission contract.
    """
    semantic_id = control.to_content()["semantic_id"]
    payload = {
        "id": f"{semantic_id}_hold_{int(control.hold_months)}m",
        "description": (
            "frozen P4/P5 positive control: event_ear_k_kp1 held one month"
        ),
        "hypothesis": semantic_id,
        "expression": {"op": "field", "role": semantic_id},
        "inputs": [
            {"alias": semantic_id, "requirement": _control_requirement(control)}
        ],
        "frequency": "daily",
        "missing_policy": "propagate",
        "sign": int(control.sign),
    }
    return factor_spec_from_dict(payload)


def control_raw_artifact(
    control: _run.ControlHypothesis = _run.CONTROL,
) -> RawArtifact:
    """The fixed raw artifact carrying **only** the control's FactorSpec.

    The candidate is in the frozen ``campaign3-candidates-v1`` schema, so the
    frozen normalization/certification path accepts it exactly as it would a
    generator's candidate.
    """
    from smart_beta.spec.factor_spec import to_dict as _spec_to_dict

    candidate = {
        "factor_spec": _spec_to_dict(control_factor_spec(control)),
        "research_question": (
            "Frozen P4/P5 pipeline control: does the EA event primitive "
            "predict the cross-section?"
        ),
        "economic_rationale": (
            "Frozen, non-empirical positive control; it measures the pipeline "
            "and power, never alpha."
        ),
    }
    payload = {
        "schema_version": "campaign3-candidates-v1",
        "candidates": [candidate],
    }
    content = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return RawArtifact(content=content)


# ---------------------------------------------------------------------------
# the fixed P6-E4 preflight battery
# ---------------------------------------------------------------------------

#: The certifiable A§15A.2 STD_X_RANK shape (P6-E3 *correction*).  The erratum's
#: original ``standardize(rolling_mean(ret,20)) x rank(rolling_mean(dv,60))``
#: is ``NOT_CERTIFIED`` and never reaches ``_realize_std_x_rank``; the planner
#: replaced it after the C3-L-R1 blocker.
_PREFLIGHT_SIMPLE_ID = "campaign3_preflight_simple_momentum"
_PREFLIGHT_STD_X_RANK_ID = "campaign3_preflight_std_x_rank"


def _factor_requirement(
    semantic_id: str, *, observation_period: str, units: str
) -> dict[str, Any]:
    """A v2-prompt-table semantic requirement for the D7c roles."""
    return {
        "semantic_id": semantic_id,
        "frequency": "daily",
        "observation_period": observation_period,
        "units": units,
        "lookback": 0,
        "revision_policy": "point_in_time",
        "require_knowledge_date": True,
        "require_positive_vintage_identity": False,
    }


def simple_factor_spec() -> FactorSpec:
    """The frozen SIMPLE preflight probe: ``tri/lag(tri,20) - 1``."""
    payload = {
        "id": _PREFLIGHT_SIMPLE_ID,
        "description": "P6-E3 preflight SIMPLE probe: tri/lag(tri,20) - 1",
        "hypothesis": "preflight_simple",
        "expression": {
            "op": "sub",
            "left": {
                "op": "div",
                "left": {"op": "field", "role": "tri"},
                "right": {
                    "op": "lag",
                    "operand": {"op": "field", "role": "tri"},
                    "periods": 20,
                },
            },
            "right": {"op": "const", "value": 1.0},
        },
        "inputs": [
            {
                "alias": "tri",
                "requirement": _factor_requirement(
                    "total_return_index",
                    observation_period="instant",
                    units="ratio",
                ),
            }
        ],
        "frequency": "daily",
        "missing_policy": "propagate",
        "sign": 1,
    }
    return factor_spec_from_dict(payload)


def std_x_rank_factor_spec() -> FactorSpec:
    """The frozen STD_X_RANK preflight probe (P6-E3 correction).

    ``standardize(lag(tri,20)/lag(tri,60)) * rank(rolling_mean(dv,20)/
    rolling_mean(dv,60))`` — the certifiable A§15A.2 shape, built through the
    frozen ``factor_spec_from_dict`` parser so it cannot drift from the
    admission contract.
    """
    payload = {
        "id": _PREFLIGHT_STD_X_RANK_ID,
        "description": (
            "P6-E3 preflight STD_X_RANK probe: standardize(lag(tri,20)/"
            "lag(tri,60)) * rank(rolling_mean(dv,20)/rolling_mean(dv,60))"
        ),
        "hypothesis": "preflight_std_x_rank",
        "expression": {
            "op": "mul",
            "left": {
                "op": "cross_section",
                "fn": "standardize",
                "operand": {
                    "op": "div",
                    "left": {
                        "op": "lag",
                        "operand": {"op": "field", "role": "tri"},
                        "periods": 20,
                    },
                    "right": {
                        "op": "lag",
                        "operand": {"op": "field", "role": "tri"},
                        "periods": 60,
                    },
                },
            },
            "right": {
                "op": "cross_section",
                "fn": "rank",
                "operand": {
                    "op": "div",
                    "left": {
                        "op": "rolling",
                        "fn": "mean",
                        "operand": {"op": "field", "role": "dv"},
                        "window": 20,
                    },
                    "right": {
                        "op": "rolling",
                        "fn": "mean",
                        "operand": {"op": "field", "role": "dv"},
                        "window": 60,
                    },
                },
            },
        },
        "inputs": [
            {
                "alias": "tri",
                "requirement": _factor_requirement(
                    "total_return_index",
                    observation_period="instant",
                    units="ratio",
                ),
            },
            {
                "alias": "dv",
                "requirement": _factor_requirement(
                    "dollar_volume",
                    observation_period="period",
                    units="currency",
                ),
            },
        ],
        "frequency": "daily",
        "missing_policy": "propagate",
        "sign": 1,
    }
    return factor_spec_from_dict(payload)


def _preflight_probe_spec(
    *,
    probe_id: str,
    description: str,
    expression: Mapping[str, Any],
    inputs: Sequence[tuple[str, str, str, str]],
) -> FactorSpec:
    """Build one benign P6-E4 probe through the frozen FactorSpec parser.

    ``inputs`` is ``(alias, semantic_id, observation_period, units)`` per
    declared role.  Building through :func:`factor_spec_from_dict` (never a
    hand-rolled ``FactorSpec``) keeps every probe on the same admission path
    a generated candidate takes.
    """
    payload = {
        "id": probe_id,
        "description": description,
        "hypothesis": probe_id,
        "expression": dict(expression),
        "inputs": [
            {
                "alias": alias,
                "requirement": _factor_requirement(
                    semantic_id, observation_period=period, units=units
                ),
            }
            for alias, semantic_id, period, units in inputs
        ],
        "frequency": "daily",
        "missing_policy": "propagate",
        "sign": 1,
    }
    return factor_spec_from_dict(payload)


@dataclass(frozen=True)
class PreflightProbe:
    """One fixed P6-E4 preflight probe.

    ``certified_class`` is the planner-verified ``certify_candidate`` class
    the probe must certify as **before** it is evaluated; a mismatch is a
    specification defect that fails the whole preflight closed.
    """

    name: str
    certified_class: str
    spec: FactorSpec


def preflight_probes() -> tuple[PreflightProbe, ...]:
    """The ten fixed P6-E4 preflight probes, in evaluation order.

    The battery covers every certified class (EVENT_SIMPLE, SIMPLE,
    STD_X_RANK) and both grammar families.  Every probe is built through the
    frozen ``factor_spec_from_dict`` parser and certifies in exactly its
    ``certified_class``; the planner verified each pairing with
    :func:`campaign3_eval.certify_candidate`.  The probe names deliberately
    carry no :data:`_FORBIDDEN_KEY_MARKERS` token, so the statistic-free
    guard applies to the whole summary uniformly.
    """
    return (
        PreflightProbe("control", "EVENT_SIMPLE", control_factor_spec()),
        PreflightProbe("simple", "SIMPLE", simple_factor_spec()),
        PreflightProbe("cross_sectional", "STD_X_RANK", std_x_rank_factor_spec()),
        PreflightProbe(
            "event_times_ret",
            "EVENT_SIMPLE",
            _preflight_probe_spec(
                probe_id="campaign3_preflight_event_times_ret",
                description=(
                    "P6-E4 EVENT_SIMPLE probe: "
                    "event_ear_k_kp1 * rolling_mean(ret,20)"
                ),
                expression={
                    "op": "mul",
                    "left": {"op": "field", "role": "event_ear_k_kp1"},
                    "right": {
                        "op": "rolling",
                        "fn": "mean",
                        "operand": {"op": "field", "role": "ret"},
                        "window": 20,
                    },
                },
                inputs=(
                    (
                        "event_ear_k_kp1",
                        "event_ear_k_kp1",
                        "period",
                        "fraction",
                    ),
                    ("ret", "daily_total_return", "period", "fraction"),
                ),
            ),
        ),
        PreflightProbe(
            "event_recent_momentum",
            "EVENT_SIMPLE",
            _preflight_probe_spec(
                probe_id="campaign3_preflight_event_recent_momentum",
                description=(
                    "P6-E4 EVENT_SIMPLE probe: "
                    "event_recent_ea_w20 * (tri/lag(tri,5) - 1)"
                ),
                expression={
                    "op": "mul",
                    "left": {"op": "field", "role": "event_recent_ea_w20"},
                    "right": {
                        "op": "sub",
                        "left": {
                            "op": "div",
                            "left": {"op": "field", "role": "tri"},
                            "right": {
                                "op": "lag",
                                "operand": {"op": "field", "role": "tri"},
                                "periods": 5,
                            },
                        },
                        "right": {"op": "const", "value": 1.0},
                    },
                },
                inputs=(
                    (
                        "event_recent_ea_w20",
                        "event_recent_ea_w20",
                        "instant",
                        "count",
                    ),
                    ("tri", "total_return_index", "instant", "ratio"),
                ),
            ),
        ),
        PreflightProbe(
            "event_days_since_ea",
            "EVENT_SIMPLE",
            _preflight_probe_spec(
                probe_id="campaign3_preflight_event_days_since_ea",
                description="P6-E4 EVENT_SIMPLE probe: event_days_since_ea",
                expression={"op": "field", "role": "event_days_since_ea"},
                inputs=(
                    (
                        "event_days_since_ea",
                        "event_days_since_ea",
                        "instant",
                        "count",
                    ),
                ),
            ),
        ),
        PreflightProbe(
            "dv_ratio",
            "SIMPLE",
            _preflight_probe_spec(
                probe_id="campaign3_preflight_dv_ratio",
                description=(
                    "P6-E4 SIMPLE probe: "
                    "rolling_mean(dv,20)/rolling_mean(dv,120)"
                ),
                expression={
                    "op": "div",
                    "left": {
                        "op": "rolling",
                        "fn": "mean",
                        "operand": {"op": "field", "role": "dv"},
                        "window": 20,
                    },
                    "right": {
                        "op": "rolling",
                        "fn": "mean",
                        "operand": {"op": "field", "role": "dv"},
                        "window": 120,
                    },
                },
                inputs=(("dv", "dollar_volume", "period", "currency"),),
            ),
        ),
        PreflightProbe(
            "ret_std",
            "SIMPLE",
            _preflight_probe_spec(
                probe_id="campaign3_preflight_ret_std",
                description="P6-E4 SIMPLE probe: rolling_std(ret,60)",
                expression={
                    "op": "rolling",
                    "fn": "std",
                    "operand": {"op": "field", "role": "ret"},
                    "window": 60,
                },
                inputs=(("ret", "daily_total_return", "period", "fraction"),),
            ),
        ),
        PreflightProbe(
            "momentum",
            "SIMPLE",
            _preflight_probe_spec(
                probe_id="campaign3_preflight_momentum",
                description="P6-E4 SIMPLE probe: lag(tri,21)/lag(tri,252) - 1",
                expression={
                    "op": "sub",
                    "left": {
                        "op": "div",
                        "left": {
                            "op": "lag",
                            "operand": {"op": "field", "role": "tri"},
                            "periods": 21,
                        },
                        "right": {
                            "op": "lag",
                            "operand": {"op": "field", "role": "tri"},
                            "periods": 252,
                        },
                    },
                    "right": {"op": "const", "value": 1.0},
                },
                inputs=(("tri", "total_return_index", "instant", "ratio"),),
            ),
        ),
        PreflightProbe(
            "cross_sectional_21_252",
            "STD_X_RANK",
            _preflight_probe_spec(
                probe_id="campaign3_preflight_std_x_rank_21_252",
                description=(
                    "P6-E4 STD_X_RANK probe: standardize(lag(tri,21)/"
                    "lag(tri,252)) * rank(rolling_mean(dv,20)/"
                    "rolling_mean(dv,120))"
                ),
                expression={
                    "op": "mul",
                    "left": {
                        "op": "cross_section",
                        "fn": "standardize",
                        "operand": {
                            "op": "div",
                            "left": {
                                "op": "lag",
                                "operand": {"op": "field", "role": "tri"},
                                "periods": 21,
                            },
                            "right": {
                                "op": "lag",
                                "operand": {"op": "field", "role": "tri"},
                                "periods": 252,
                            },
                        },
                    },
                    "right": {
                        "op": "cross_section",
                        "fn": "rank",
                        "operand": {
                            "op": "div",
                            "left": {
                                "op": "rolling",
                                "fn": "mean",
                                "operand": {"op": "field", "role": "dv"},
                                "window": 20,
                            },
                            "right": {
                                "op": "rolling",
                                "fn": "mean",
                                "operand": {"op": "field", "role": "dv"},
                                "window": 120,
                            },
                        },
                    },
                },
                inputs=(
                    ("tri", "total_return_index", "instant", "ratio"),
                    ("dv", "dollar_volume", "period", "currency"),
                ),
            ),
        ),
    )


@dataclass
class FixedArtifactGenerator:
    """A generator that always returns the fixed control raw artifact.

    It is the ``--preflight`` replacement for the DeepSeek client: exactly one
    artifact (only the control FactorSpec), so the real T-A evaluator can be
    exercised without a network call or a credential.
    """

    artifact: RawArtifact
    prompt_template_hash: str
    tokens_used: int = 0
    calls: int = 0

    def __call__(self, visible_history: Any, feedback: Any) -> Any:
        from smart_beta.campaign import campaign3_generation as _gen

        self.calls += 1
        return _gen.GenerationOutput(
            raw_artifact=self.artifact, tokens_used=self.tokens_used
        )


def _prompt_template_hash(generator: Any) -> str:
    """The A2-E2 prompt-template hash carried by ``generator``."""
    value = getattr(generator, "prompt_template_hash", None)
    if callable(value):
        value = value()
    if isinstance(value, str) and value:
        return value
    global _FALLBACK_PROMPT_TEMPLATE_HASH
    if _FALLBACK_PROMPT_TEMPLATE_HASH is None:
        from smart_beta.research.llm_generator_deepseek import (
            prompt_template_v2_hash,
        )

        _FALLBACK_PROMPT_TEMPLATE_HASH = prompt_template_v2_hash()
    return _FALLBACK_PROMPT_TEMPLATE_HASH


def build_production_policy(
    generator: Any,
    *,
    dry_run: bool = False,
    max_proposal_budget: int | None = None,
) -> ResearchPolicy:
    """Build the frozen C3-X policy bound to the injected generator."""
    from smart_beta.campaign import campaign3_generation as _gen

    budget = (
        _gen.DEFAULT_MAX_PROPOSAL_BUDGET
        if max_proposal_budget is None
        else int(max_proposal_budget)
    )
    return _gen.build_campaign3_policy(
        prompt_template_hash=_prompt_template_hash(generator),
        dry_run=dry_run,
        max_proposal_budget=budget,
    )


# ---------------------------------------------------------------------------
# default (real) loader seams
# ---------------------------------------------------------------------------


def default_bindings(
    dataset_root: Path | str, knowledge_log_path: Path | str
) -> _run.Campaign3Bindings:
    """Derive the frozen bindings from the real stores (metadata only)."""
    return _run.production_bindings(
        dataset_root, knowledge_log_path=knowledge_log_path
    )


def default_partition() -> Partition:
    """The frozen Campaign-1/2 partition (``b4b56dbf…``)."""
    from smart_beta.campaign.campaign1 import build_partition

    return build_partition()


def default_overlay(dataset_root: Path | str) -> Any:
    """Read-only Dataset v1.2 overlay (delegates to ``runner``)."""
    return _run.load_overlay(dataset_root)


def default_inputs(dataset_root: Path | str) -> Mapping[str, Any]:
    """The D7c trusted inputs."""
    from smart_beta.dataset_v1.trusted_inputs import load_trusted_inputs

    return load_trusted_inputs(dataset_root)


def default_event_layer(
    dataset_root: Path | str, *, acq_id: str, ev_id: str, fold: Any = None
) -> Any:
    """Read-only v1.3 EDGAR event layer (development fold only)."""
    return _run.load_event_layer(
        dataset_root, acq_id=acq_id, ev_id=ev_id, fold=fold
    )


def default_adapter(
    *,
    dataset_root: Path | str,
    partition: Partition,
    overlay: Any,
    bindings: _run.Campaign3Bindings,
    inputs: Mapping[str, Any],
) -> Any:
    """The Campaign-1 production adapter (its ``panel_of`` is reused)."""
    from smart_beta.campaign.campaign1 import RealInputAdapter

    return RealInputAdapter(
        root=dataset_root,
        partition=partition,
        overlay=overlay,
        partition_id=bindings.partition_id,
        inputs=inputs,
    )


def default_registry_loader(
    knowledge_log_path: Path | str,
) -> tuple[Any, tuple[Mapping[str, Any], ...]]:
    """The production registry + the K-PIT ``PROCEDURE_ADMISSION`` records.

    ``inference.PRODUCTION_REGISTRY`` is used and the admitted
    ``EWC_T23_ONE_SIDED`` v1.0.0 procedure is registered into it (idempotently;
    the callable procedure ships ``test_only=False``).  The admission records
    are every HUMAN_DECISION record in the K-PIT, converted to the plain
    mapping shape :func:`smart_beta.science.inference.run_inference` consumes.
    The K-PIT is read only via the chain-verifying ``read_records``.
    """
    from smart_beta.science import inference as _inference
    from smart_beta.science.procedures.ewc_t23_one_sided import (
        PROCEDURE_ID,
        VERSION,
        register as _register_ewc,
    )

    registry = _inference.PRODUCTION_REGISTRY
    if (PROCEDURE_ID, VERSION) not in registry:
        _register_ewc(registry)
    records: list[Mapping[str, Any]] = []
    path = Path(knowledge_log_path)
    if path.is_file():
        for record in read_records(path):
            if record.kind is RecordKind.HUMAN_DECISION:
                records.append(record.to_dict())
    return registry, tuple(records)


def default_diagnostics_builder() -> Callable[..., Any]:
    from smart_beta.dataset_v1 import pqv2_evaluation as _pe

    return _pe.build_pqv2_diagnostics


def default_export_series_fn() -> Callable[..., Any]:
    from smart_beta.dataset_v1 import pqv2_series as _series

    return _series.export_series


# ---------------------------------------------------------------------------
# folds
# ---------------------------------------------------------------------------


def production_partition_folds(
    partition: Partition,
) -> tuple[Fold, Fold, Fold]:
    """The frozen (IS, OOS, DEVELOPMENT_SEALED) folds.

    ``DEVELOPMENT_SEALED`` is the partition's ``HOLDOUT`` fold — Campaign-1's
    sealed role (``campaign1.assert_not_sealed_fold``).  Any reserve/HOLDOUT
    ambiguity fails closed: there is exactly one IS, one OOS and one HOLDOUT
    fold in the frozen partition, and this function never synthesizes a T-C
    fold.
    """
    if not isinstance(partition, Partition):
        raise LaunchError("partition must be a Partition")
    is_folds = partition.is_folds
    oos_folds = partition.oos_folds
    holdout = partition.holdout_fold
    if len(is_folds) != 1 or len(oos_folds) != 1 or holdout is None:
        raise LaunchError(
            "the frozen partition must carry exactly one IS, one OOS and one "
            "HOLDOUT fold"
        )
    return is_folds[0], oos_folds[0], holdout


def _pqv2_fold(partition_fold: Fold) -> Any:
    """The P-Q development ``Fold`` for a development-window partition fold.

    T-A and T-B are entirely inside the development window (before the
    ``2023-07-03`` reserve boundary), so the frozen development ``Fold`` is
    the correct P-Q evaluation fold.  A partition fold reaching the reserve
    window fails closed (C3-L never evaluates T-C).
    """
    from datetime import date as _date

    from smart_beta.dataset_v1 import pqv2_core as _core

    if partition_fold.end.date() > _date(2023, 7, 3):
        raise LaunchError(
            "refusing a P-Q fold past the 2023-07-03 reserve boundary "
            f"({partition_fold.role.value} {partition_fold.end.date()})"
        )
    return _core.DEVELOPMENT_FOLD


# ---------------------------------------------------------------------------
# persisted T-A provenance (P6-E5 C3-L-R3)
# ---------------------------------------------------------------------------


def _ta_provenance_path(diagnostics_dir: Path | str, digest: str) -> Path:
    """``<diagnostics_dir>/<diagnostics_hash>.provenance.json`` (P6-E5)."""
    return Path(diagnostics_dir) / f"{digest}{TA_PROVENANCE_SUFFIX}"


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Atomically create ``path`` (temp file + fsync + ``os.replace``)."""
    tmp = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:  # pragma: no cover - already removed
            pass
        raise


def persist_ta_provenance(
    diagnostics_dir: Path | str,
    digest: str,
    spec: FactorSpec,
    partition_fold: Fold,
) -> Path:
    """Persist the FactorSpec provenance of one T-A diagnostics hash.

    Write-once: an identical existing record is verified and reused; a
    conflicting one fails closed.  Written by the T-A evaluator's diagnostics
    sink next to the content-addressed diagnostics artifact, and read by the
    cold-resume screen candidate builder (P6-E5 item 3).
    """
    payload = json.dumps(
        {
            "schema": TA_PROVENANCE_SCHEMA,
            "diagnostics_hash": str(digest),
            "factor_spec_hash": factor_spec_hash(spec),
            "factor_spec": factor_spec_to_dict(spec),
            "partition_fold": partition_fold.to_dict(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    path = _ta_provenance_path(diagnostics_dir, str(digest))
    if path.exists():
        if path.read_bytes() != payload:
            raise LaunchError(
                f"existing T-A provenance {path.name} conflicts with the "
                "diagnostics being persisted"
            )
        return path
    _atomic_write_bytes(path, payload)
    return path


# ---------------------------------------------------------------------------
# attempt identity (mirrors Campaign3Run.compute_attempt_id)
# ---------------------------------------------------------------------------


def compute_attempt_id(
    *,
    bindings: _run.Campaign3Bindings,
    policy: ResearchPolicy,
    control: _run.ControlHypothesis = _run.CONTROL,
    precommitment_path: Path | str = _run.PRECOMMITMENT_PATH,
) -> str:
    """The deterministic attempt id, byte-identical to the run driver's."""
    precommitment = _run.verify_precommitment(precommitment_path)
    return content_hash(
        {
            "domain": "campaign3-p6-attempt",
            "precommitment_sha256": precommitment,
            "bindings_hash": bindings.content_hash,
            "control_factor_hash": _run.control_factor_hash(control),
            "policy_hash": policy.content_hash,
        }
    )


# ---------------------------------------------------------------------------
# the production assembly
# ---------------------------------------------------------------------------


@dataclass
class ProductionAssembly:
    """Every dependency of :class:`Campaign3Run`, assembled from the stores."""

    dataset_root: Path
    knowledge_log_path: Path
    run_root: Path
    generator: Any
    bindings: _run.Campaign3Bindings
    partition: Partition
    is_fold: Fold
    oos_fold: Fold
    sealed_fold: Fold
    overlay: Any
    inputs: Mapping[str, Any]
    event_layer: Any
    policy: ResearchPolicy
    registry: Any
    admission_records: tuple[Mapping[str, Any], ...]
    procedure_ref: Mapping[str, str]
    panel_of: Callable[[Any, str, Fold], Any]
    ta_evaluator: Any
    declaration_builder: Callable[..., Any]
    diagnostics_builder: Callable[..., Any]
    export_series_fn: Callable[..., Any]
    adapter: Any = None
    #: The run directory's persisted T-A diagnostics store (``<run_dir>/diagnostics``);
    #: the cold-resume screen builder loads ``diagnostics_hash``-keyed artifacts
    #: and their provenance sidecars from here (P6-E5 C3-L-R3).
    diagnostics_dir: Path | None = None
    ta_cache: dict[str, Any] = field(default_factory=dict, repr=False)
    proposal_lookup: dict[str, Any] = field(default_factory=dict, repr=False)
    attempt_id: str = ""
    preflight_marker: Path | None = None

    @property
    def tb_folds(self) -> tuple[Fold, Fold]:
        return (self.oos_fold, self.sealed_fold)

    def panel_for(self, spec: FactorSpec, fold: Fold) -> Any:
        """The declared factor panel for ``spec``/``fold`` (P6-E3 correction).

        The panel *values* come from the Phase-6 specification evaluator over
        the **exact** frames P-Q realizes against —
        ``pqv2_evaluation._alias_frames(spec, inputs, overlay)`` (read-only
        import) — so a cross-sectional candidate's panel is byte-identical to
        the one :func:`pqv2_evaluation.build_pqv2_diagnostics` checks in
        ``_realize_std_x_rank``.  The Campaign-1 ``panel_of`` only supplies the
        frozen ``(formation, universe member)`` row grid that D7b
        ``_validate_factor_panel`` expects; its values are discarded.  The
        evaluator call is the sealed ``_scenario_panel`` realization (no new
        math), so the declared panel and the K1 re-realization cannot drift.

        An ``EVENT_SIMPLE`` candidate needs the frozen PQ23-B K0 event inputs
        mixed into the D7c inputs before either step.
        """
        from smart_beta.campaign import campaign3_eval as _eval
        from smart_beta.dataset_v1 import pqv2_evaluation as _pe

        roles = _eval.event_roles(spec) if isinstance(spec, FactorSpec) else ()
        base = dict(self.inputs)
        merged = base
        if roles:
            if self.event_layer is None:
                raise LaunchError(
                    "an event candidate requires the bound event_layer"
                )
            from smart_beta.dataset_v1 import pqv2_events as _events
            from smart_beta.dataset_v1.pqv2_evaluation import (
                _sessions_from_inputs,
            )

            tri = base["total_return_index"].values
            sessions = _sessions_from_inputs(base)
            built = _events.build_event_inputs(
                self.event_layer,
                tri,
                sessions,
                scenario=_events.SCENARIO_K0,
                roles=roles,
            )
            merged = {
                key: value for key, value in base.items() if key not in set(roles)
            }
            merged.update(built)
        swapped = self.adapter is not None and hasattr(self.adapter, "_inputs")
        previous = getattr(self.adapter, "_inputs", None)
        if swapped:
            self.adapter._inputs = merged
        try:
            template = self.panel_of(spec, _run.C3_UNIVERSE, fold)
        finally:
            if swapped:
                self.adapter._inputs = previous
        return _pe._scenario_panel(template, spec, merged, self.overlay)

    def screen_candidate_builder(self, snapshot: Any) -> tuple[tuple[Any, ...], Any]:
        """Build the C3-S candidates + control from the generation snapshot.

        The T-A IS diagnostics and their ``pqv2_series`` export are captured
        in memory by :attr:`ta_cache` during generation (the C3-R driver does
        not put them in the JSON snapshot).  A snapshot entry with no cached
        diagnostics is **resumed from disk** (P6-E5 item 3): the persisted
        diagnostics is loaded and verified from the run directory's
        ``diagnostics_store`` by ``diagnostics_hash``, and the
        ``pqv2_series`` export is rebuilt deterministically from the persisted
        FactorSpec provenance with the same panel/declaration builders.  A
        missing/corrupt artifact, a mismatched provenance, or a rebuilt
        diagnostics hash that differs from the persisted one fails closed.
        """
        from smart_beta.campaign import campaign3_screen as _screen

        candidates: list[Any] = []
        for record in getattr(snapshot, "candidates", ()):
            digest = record.get("diagnostics_hash")
            entry = self.ta_cache.get(str(digest))
            if entry is None:
                entry = self._load_persisted_entry(snapshot, record, str(digest))
                self.ta_cache[str(digest)] = entry
            series = self._export_entry(entry)
            hypothesis_id = str(record["hypothesis_id"])
            self.proposal_lookup[hypothesis_id] = _ProposalView(
                proposal_id=str(record.get("proposal_id", hypothesis_id)),
                proposed_factor_spec=entry["provenance"]["spec"],
                proposed_factor_spec_hash=str(record.get("factor_spec_hash", "")),
            )
            candidates.append(
                _screen.ScreeningCandidate(
                    hypothesis_id=hypothesis_id,
                    canonical_key=str(record["canonical_key"]),
                    sign=int(record.get("sign", 1)),
                    diagnostics=entry["diagnostics"],
                    series_export=series,
                )
            )
        control = _screen.ScreeningCandidate(
            hypothesis_id=_run.CONTROL_HYPOTHESIS_ID,
            canonical_key=_run.CONTROL_HYPOTHESIS_ID,
            sign=int(_run.CONTROL_SIGN),
            diagnostics=None,
            series_export=None,
        )
        return tuple(candidates), control

    def _diagnostics_dir(self, snapshot: Any) -> Path:
        """The run directory's persisted T-A diagnostics store (P6-E5)."""
        if self.diagnostics_dir is not None:
            return Path(self.diagnostics_dir)
        value = getattr(snapshot, "diagnostics_dir", None)
        if not value:
            raise LaunchError(
                "the assembly has no persisted T-A diagnostics store bound for "
                "a cold screen resume"
            )
        return Path(value)

    def _load_persisted_provenance(
        self, diagnostics_dir: Path, digest: str, record: Mapping[str, Any]
    ) -> FactorSpec:
        """Load and verify the persisted FactorSpec provenance (P6-E5).

        The record is written write-once next to the diagnostics artifact and
        keyed by ``diagnostics_hash``.  A missing, malformed, mislabeled or
        conflicting record fails closed.
        """
        path = _ta_provenance_path(diagnostics_dir, digest)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LaunchError(
                "no readable persisted T-A provenance for snapshot candidate "
                f"{record.get('hypothesis_id')!r} (diagnostics_hash "
                f"{digest!r})"
            ) from exc
        if not isinstance(payload, Mapping):
            raise LaunchError(
                f"the persisted T-A provenance for {digest!r} is malformed"
            )
        if payload.get("schema") != TA_PROVENANCE_SCHEMA:
            raise LaunchError(
                f"the persisted T-A provenance for {digest!r} has schema "
                f"{payload.get('schema')!r}, not {TA_PROVENANCE_SCHEMA!r}"
            )
        if str(payload.get("diagnostics_hash", "")) != digest:
            raise LaunchError(
                f"the persisted T-A provenance names diagnostics_hash "
                f"{payload.get('diagnostics_hash')!r}, not {digest!r}"
            )
        try:
            spec = factor_spec_from_dict(payload.get("factor_spec"))
        except Exception as exc:  # noqa: BLE001 - re-typed, fail closed
            raise LaunchError(
                "the persisted T-A provenance is not a valid FactorSpec "
                f"({type(exc).__name__})"
            ) from exc
        spec_hash = str(payload.get("factor_spec_hash", ""))
        if factor_spec_hash(spec) != spec_hash:
            raise LaunchError(
                f"the persisted T-A provenance for {digest!r} declares "
                f"factor_spec_hash {spec_hash!r}, which does not match its "
                "FactorSpec"
            )
        record_hash = str(record.get("factor_spec_hash", "") or "")
        if record_hash and record_hash != spec_hash:
            raise LaunchError(
                "the persisted T-A provenance for snapshot candidate "
                f"{record.get('hypothesis_id')!r} names factor_spec_hash "
                f"{spec_hash!r}, not the candidate's {record_hash!r}"
            )
        if payload.get("partition_fold") != self.is_fold.to_dict():
            raise LaunchError(
                f"the persisted T-A provenance for {digest!r} is not bound to "
                "the assembly's IS fold"
            )
        return spec

    def _load_persisted_entry(
        self, snapshot: Any, record: Mapping[str, Any], digest: str
    ) -> dict[str, Any]:
        """Rebuild one cache entry from the run directory's persisted state.

        The content-addressed diagnostics is loaded and fully verified from
        ``diagnostics_store`` (missing/corrupt fails closed); its persisted
        FactorSpec provenance drives the same ``panel_for`` / declaration /
        diagnostics builders the warm path used; and the rebuilt diagnostics
        must hash to exactly the persisted ``diagnostics_hash``.
        """
        from smart_beta.campaign import diagnostics_store as _store

        diagnostics_dir = self._diagnostics_dir(snapshot)
        try:
            _store.load_verified(diagnostics_dir, digest)
        except _store.C1DiagnosticsError as exc:
            raise LaunchError(
                "the persisted T-A IS diagnostics for snapshot candidate "
                f"{record.get('hypothesis_id')!r} is missing or corrupt "
                f"(diagnostics_hash {digest!r})"
            ) from exc
        spec = self._load_persisted_provenance(diagnostics_dir, digest, record)
        panel = self.panel_for(spec, self.is_fold)
        declaration = self.declaration_builder(spec, panel, self.is_fold)
        diagnostics = self.diagnostics_builder(
            self.dataset_root,
            declaration,
            spec,
            panel,
            self.partition,
            fold=_pqv2_fold(self.is_fold),
            overlay=self.overlay,
            inputs=self.inputs,
            event_layer=self.event_layer,
        )
        rebuilt = str(getattr(diagnostics, "diagnostics_hash", ""))
        if rebuilt != digest:
            raise LaunchError(
                "the rebuilt T-A IS diagnostics for snapshot candidate "
                f"{record.get('hypothesis_id')!r} hashes to {rebuilt!r}, not "
                f"the persisted {digest!r}"
            )
        return {
            "diagnostics": diagnostics,
            "provenance": {
                "spec": spec,
                "panel": panel,
                "declaration": declaration,
                "partition_fold": self.is_fold,
            },
        }

    def _export_entry(self, entry: Mapping[str, Any]) -> Any:
        from smart_beta.campaign import campaign3_screen as _screen

        provenance = dict(entry["provenance"])
        series = self.export_series_fn(
            self.dataset_root,
            provenance["declaration"],
            provenance["spec"],
            provenance["panel"],
            self.partition,
            diagnostics=entry["diagnostics"],
            fold=_pqv2_fold(provenance["partition_fold"]),
            overlay=self.overlay,
            inputs=self.inputs,
            event_layer=self.event_layer,
        )
        _screen._bind_export(entry["diagnostics"], series)
        return series

    def tb_evaluator(self, hypothesis_id: str) -> Any:
        """The C3-S T-B evaluation of one hypothesis (pooled 36 formations).

        Each T-B fold is evaluated by the frozen single-fold
        :func:`~smart_beta.campaign.campaign3_run.build_tb_evaluator` (its own
        per-fold panel and its own ``build_pqv2_diagnostics`` +
        ``pqv2_series.export_series`` binding); the per-cell series are then
        pooled in fold order (OOS then DEVELOPMENT_SEALED).
        ``cell.period_values`` is never read.
        """
        from smart_beta.campaign import campaign3_screen as _screen

        proposal = self.proposal_lookup.get(hypothesis_id)
        if proposal is None:
            raise LaunchError(
                f"no proposal is available for T-B hypothesis {hypothesis_id!r}"
            )
        per_fold: list[tuple[Fold, Any, Any]] = []
        for fold in self.tb_folds:
            evaluator = self._single_fold_tb_evaluator(fold)
            evaluation = evaluator(hypothesis_id)
            fold_evaluation = evaluation.folds[0]
            _screen._bind_export(
                fold_evaluation.diagnostics, fold_evaluation.series
            )
            per_fold.append(
                (
                    fold,
                    fold_evaluation.diagnostics,
                    fold_evaluation.series,
                )
            )
        pooled_diagnostics, pooled_series = _pool_tb_folds(hypothesis_id, per_fold)
        return _screen.TbDiagnostics(
            hypothesis_id=hypothesis_id,
            diagnostics=pooled_diagnostics,
            series_export=pooled_series,
        )

    def _single_fold_tb_evaluator(self, fold: Fold) -> Any:
        """The frozen one-fold T-B evaluator, bound to this assembly."""

        def diagnostics_builder(
            root: Any, declaration: Any, spec: Any, panel: Any, partition: Any,
            *, fold: Fold, **kwargs: Any,
        ) -> Any:
            return self.diagnostics_builder(
                root, declaration, spec, panel, partition,
                fold=_pqv2_fold(fold), **kwargs,
            )

        def export_series_fn(
            root: Any, declaration: Any, spec: Any, panel: Any, partition: Any,
            *, diagnostics: Any, fold: Fold, **kwargs: Any,
        ) -> Any:
            return self.export_series_fn(
                root, declaration, spec, panel, partition,
                diagnostics=diagnostics, fold=_pqv2_fold(fold), **kwargs,
            )

        return _run.build_tb_evaluator(
            root=self.dataset_root,
            partition=self.partition,
            folds=(fold,),
            overlay=self.overlay,
            inputs=self.inputs,
            declaration_builder=lambda spec, panel, f: self.declaration_builder(
                spec, panel, f
            ),
            panel_builder=lambda spec, fold=fold: self.panel_for(spec, fold),
            proposal_lookup=self.proposal_lookup,
            persist_dir=self.run_root / self.attempt_id / "tb" / fold.role.value,
            diagnostics_builder=diagnostics_builder,
            export_series_fn=export_series_fn,
            event_layer=self.event_layer,
        )

    @property
    def run_kwargs(self) -> dict[str, Any]:
        return {
            "bindings": self.bindings,
            "policy": self.policy,
            "generator": self.generator,
            "ta_evaluator": self.ta_evaluator,
            "knowledge_log": KnowledgeLog(self.knowledge_log_path),
            "run_root": self.run_root,
            "screen_runner": None,
            "screen_candidate_builder": self.screen_candidate_builder,
            "tb_evaluator": self.tb_evaluator,
            "registry": self.registry,
            "admission_records": self.admission_records,
            "procedure_ref": dict(self.procedure_ref),
            "attempt_id": self.attempt_id,
            "launch_terminal": None,
        }


def _pool_tb_folds(
    hypothesis_id: str, per_fold: Sequence[tuple[Fold, Any, Any]]
) -> tuple[Any, Any]:
    """Pool the per-fold diagnostics/series into one C3-S ``TbDiagnostics``."""
    from smart_beta.dataset_v1 import pqv2_series as _series

    if not per_fold:
        raise LaunchError("the T-B evaluation produced no folds")
    diagnostics_hashes = tuple(str(item[1].diagnostics_hash) for item in per_fold)
    declaration_hashes = tuple(str(item[1].declaration_hash) for item in per_fold)
    pooled_diagnostics_hash = content_hash(
        {"domain": "campaign3-tb-pooled-diagnostics", "folds": list(diagnostics_hashes)}
    )
    pooled_declaration_hash = content_hash(
        {"domain": "campaign3-tb-pooled-declaration", "folds": list(declaration_hashes)}
    )
    first = per_fold[0][1]
    not_assessed = next(
        (item[1].not_assessed for item in per_fold if item[1].not_assessed is not None),
        None,
    )
    cells_by_name: dict[str, Any] = {}
    for _, diagnostics, _ in per_fold:
        for cell in getattr(diagnostics, "cells", ()) or ():
            cells_by_name.setdefault(str(cell.cell), cell)
    pooled_diagnostics = _PooledTbDiagnostics(
        declaration_hash=pooled_declaration_hash,
        diagnostics_hash=pooled_diagnostics_hash,
        metric_basis=str(getattr(first, "metric_basis", "")),
        not_assessed=not_assessed,
        certified_class=getattr(first, "certified_class", None),
        certified_hypothesis=hypothesis_id,
        event_scenarios=tuple(getattr(first, "event_scenarios", ()) or ()),
        cells=tuple(cells_by_name.values()),
        estimator_identity=getattr(first, "estimator_identity", None),
    )
    records: list[Any] = []
    seen: list[str] = []
    for _, _, export in per_fold:
        for record in getattr(export, "records", ()) or ():
            if record.cell not in seen:
                seen.append(record.cell)
    for cell in seen:
        parts = [
            export.get(cell)
            for _, _, export in per_fold
        ]
        parts = [part for part in parts if part is not None]
        periods: tuple[Any, ...] = ()
        b_series: tuple[float, ...] = ()
        l_series: tuple[float, ...] = ()
        gross_series: tuple[float, ...] = ()
        net_series: tuple[float, ...] = ()
        status = None
        reason = None
        estimand = parts[0].estimand if parts else ""
        scenario = parts[0].scenario if parts else ""
        event_scenario = parts[0].event_scenario if parts else None
        for part in parts:
            periods += tuple(part.periods)
            b_series += tuple(part.b_series)
            l_series += tuple(part.l_series)
            gross_series += tuple(part.gross_series)
            net_series += tuple(part.net_series)
            if not part.assessed and status is None:
                status = part.status
                reason = part.reason
        records.append(
            _series.PQv2SeriesRecord(
                cell=cell,
                estimand=estimand,
                scenario=scenario,
                event_scenario=event_scenario,
                periods=periods,
                b_series=b_series,
                l_series=l_series,
                gross_series=gross_series,
                net_series=net_series,
                status=status,
                reason=reason,
                declaration_hash=pooled_declaration_hash,
            )
        )
    pooled_series = _series.PQv2SeriesExport(
        declaration_hash=pooled_declaration_hash,
        diagnostics_hash=pooled_diagnostics_hash,
        estimator=str(getattr(per_fold[0][2], "estimator", "")),
        records=tuple(records),
    )
    return pooled_diagnostics, pooled_series


@dataclass(frozen=True)
class _PooledTbDiagnostics:
    """The pooled T-B view the C3-S consumption contract reads."""

    declaration_hash: str
    diagnostics_hash: str
    metric_basis: str
    not_assessed: str | None
    certified_class: str | None
    certified_hypothesis: str | None
    event_scenarios: tuple[str, ...]
    cells: tuple[Any, ...]
    estimator_identity: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "declaration_hash": self.declaration_hash,
            "diagnostics_hash": self.diagnostics_hash,
            "metric_basis": self.metric_basis,
            "not_assessed": self.not_assessed,
            "certified_class": self.certified_class,
            "certified_hypothesis": self.certified_hypothesis,
            "event_scenarios": list(self.event_scenarios),
            "cells": [str(cell.cell) for cell in self.cells],
            "estimator_identity": self.estimator_identity,
        }


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


def assemble_production(
    dataset_root: Path | str,
    knowledge_log_path: Path | str,
    run_root: Path | str,
    generator: Any,
    *,
    dry_run: bool = False,
    max_proposal_budget: int | None = None,
    attempt_id: str | None = None,
    bindings_loader: Callable[..., Any] | None = None,
    partition_loader: Callable[[], Partition] | None = None,
    overlay_loader: Callable[[Path | str], Any] | None = None,
    inputs_loader: Callable[[Path | str], Mapping[str, Any]] | None = None,
    event_layer_loader: Callable[..., Any] | None = None,
    adapter_factory: Callable[..., Any] | None = None,
    registry_loader: Callable[..., Any] | None = None,
    diagnostics_builder: Callable[..., Any] | None = None,
    export_series_fn: Callable[..., Any] | None = None,
    procedure_ref: Mapping[str, str] | None = None,
) -> ProductionAssembly:
    """Build every dependency of ``Campaign3Run`` from the real stores.

    Every loader is injectable so the offline tests exercise the whole
    assembly with fakes and never touch ``data_store``.
    """
    root = Path(dataset_root)
    kpit = Path(knowledge_log_path)
    runs = Path(run_root)

    load_bindings = bindings_loader or default_bindings
    load_partition = partition_loader or default_partition
    load_overlay = overlay_loader or default_overlay
    load_inputs = inputs_loader or default_inputs
    load_event = event_layer_loader or default_event_layer
    make_adapter = adapter_factory or default_adapter
    load_registry = registry_loader or default_registry_loader
    build_diagnostics = diagnostics_builder or default_diagnostics_builder()
    export_series = export_series_fn or default_export_series_fn()

    bindings = _run.verify_bindings(load_bindings(root, kpit))
    partition = load_partition()
    if partition.partition_id != bindings.partition_id:
        raise LaunchError(
            f"partition id {partition.partition_id} != bound "
            f"{bindings.partition_id}"
        )
    is_fold, oos_fold, sealed_fold = production_partition_folds(partition)
    overlay = load_overlay(root)
    inputs = load_inputs(root)
    event_layer = load_event(
        root, acq_id=bindings.acq_id, ev_id=bindings.ev_id, fold=None
    )
    adapter = make_adapter(
        dataset_root=root,
        partition=partition,
        overlay=overlay,
        bindings=bindings,
        inputs=inputs,
    )
    policy = build_production_policy(
        generator, dry_run=dry_run, max_proposal_budget=max_proposal_budget
    )
    registry, admission_records = load_registry(kpit)
    if procedure_ref is None:
        from smart_beta.campaign.campaign3_screen import (
            ADMITTED_PROCEDURE_REF,
        )

        procedure_ref = dict(ADMITTED_PROCEDURE_REF)

    identity = attempt_id or compute_attempt_id(
        bindings=bindings, policy=policy
    )
    diagnostics_dir = runs / identity / "diagnostics"
    assembly = ProductionAssembly(
        dataset_root=root,
        knowledge_log_path=kpit,
        run_root=runs,
        generator=generator,
        bindings=bindings,
        partition=partition,
        is_fold=is_fold,
        oos_fold=oos_fold,
        sealed_fold=sealed_fold,
        overlay=overlay,
        inputs=dict(inputs),
        event_layer=event_layer,
        policy=policy,
        registry=registry,
        admission_records=tuple(admission_records),
        procedure_ref=dict(procedure_ref),
        panel_of=adapter.panel_of,
        ta_evaluator=None,
        declaration_builder=None,
        diagnostics_builder=build_diagnostics,
        export_series_fn=export_series,
        adapter=adapter,
        diagnostics_dir=diagnostics_dir,
        attempt_id=identity,
    )
    assembly.declaration_builder = _make_declaration_builder(assembly)
    assembly.ta_evaluator = _make_ta_evaluator(assembly, diagnostics_dir)
    assembly.preflight_marker = runs / PREFLIGHT_MARKER_NAME
    return assembly


def _make_declaration_builder(assembly: ProductionAssembly) -> Callable[..., Any]:
    def build(spec: Any, panel: Any, fold: Fold) -> Any:
        return _run.build_declaration(
            spec=spec, panel=panel, fold=fold, bindings=assembly.bindings
        )

    return build


def _make_ta_evaluator(
    assembly: ProductionAssembly, diagnostics_dir: Path
) -> Any:
    """The real T-A IS evaluator, recording diagnostics for the screen."""
    from smart_beta.campaign.campaign3_eval import TaEvaluator

    cache = assembly.ta_cache

    def panel_builder(spec: FactorSpec) -> Any:
        panel = assembly.panel_for(spec, assembly.is_fold)
        return panel

    def declaration_builder(spec: Any, panel: Any) -> Any:
        return assembly.declaration_builder(spec, panel, assembly.is_fold)

    def diagnostics_sink(diagnostics: Any) -> None:
        # The C3-R screen needs the IS diagnostics + its export, which the JSON
        # generation snapshot does not carry.  The snapshot candidate's
        # ``diagnostics_hash`` is the key; the provenance is the *current*
        # declaration (the executor processes one candidate at a time).
        digest = str(diagnostics.diagnostics_hash)
        entry = cache.setdefault(digest, {"diagnostics": diagnostics})
        entry["diagnostics"] = diagnostics
        provenance = dict(_LAST_DECLARATION)
        entry["provenance"] = provenance
        # P6-E5 C3-L-R3: persist the FactorSpec provenance write-once next to
        # the content-addressed diagnostics, so a cold (resumed) screen can
        # rebuild the ``pqv2_series`` export without the in-process cache.
        if "spec" not in provenance or "partition_fold" not in provenance:
            raise LaunchError(
                "the T-A evaluator did not record the declaration provenance "
                "required to persist the diagnostics sidecar"
            )
        persist_ta_provenance(
            diagnostics_dir,
            digest,
            provenance["spec"],
            provenance["partition_fold"],
        )

    return TaEvaluator(
        root=assembly.dataset_root,
        partition=assembly.partition,
        fold=assembly.is_fold,
        overlay=assembly.overlay,
        inputs=assembly.inputs,
        declaration_builder=lambda spec, panel: _record_declaration(
            declaration_builder, spec, panel
        ),
        panel_builder=lambda spec: _record_panel(
            panel_builder, spec, assembly.is_fold
        ),
        persist_dir=diagnostics_dir,
        diagnostics_sink=diagnostics_sink,
        event_layer=assembly.event_layer,
    )


#: The declaration/panel provenance of the most recent T-A evaluation.
#: The executor evaluates candidates strictly one at a time, so a module-level
#: holder is exact for this synchronous path.
_LAST_DECLARATION: dict[str, Any] = {}


def _record_panel(
    panel_builder: Callable[..., Any], spec: FactorSpec, partition_fold: Fold
) -> Any:
    panel = panel_builder(spec)
    _LAST_DECLARATION["spec"] = spec
    _LAST_DECLARATION["panel"] = panel
    _LAST_DECLARATION["partition_fold"] = partition_fold
    return panel


def _record_declaration(
    declaration_builder: Callable[..., Any], spec: Any, panel: Any
) -> Any:
    declaration = declaration_builder(spec, panel)
    _LAST_DECLARATION["declaration"] = declaration
    return declaration


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


#: The only keys a preflight summary may carry.  A mean/t/p/return/rank field
#: is structurally impossible.
_PREFLIGHT_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "status",
        "assessed",
        "not_assessed_reason",
        "binding_ok",
        "periods",
        "error",
    }
)

#: Substrings that always indicate a leaked statistic value.
_FORBIDDEN_KEY_MARKERS: tuple[str, ...] = (
    "mean",
    "t_stat",
    "p_value",
    "p_one_sided",
    "return",
    "rank",
    "statistic",
    "sharpe",
    "min_t",
    "coefficient",
)


def assert_statistic_free(payload: Any, *, context: str = "preflight") -> None:
    """Fail closed if ``payload`` contains a forbidden statistic field.

    A key whose lowercase form contains any :data:`_FORBIDDEN_KEY_MARKERS`
    token, or any float value at all (period counts are integers), raises
    :class:`StatisticLeakError`.  Mutation L-M2's target.
    """
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _FORBIDDEN_KEY_MARKERS):
                raise StatisticLeakError(
                    f"{context}: forbidden statistic field {key!r}"
                )
            assert_statistic_free(value, context=f"{context}.{key}")
        return
    if isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            assert_statistic_free(item, context=f"{context}[{index}]")
        return
    if isinstance(payload, float):
        raise StatisticLeakError(
            f"{context}: a float value ({payload!r}) is not a period count"
        )


def preflight_summary(diagnostics: Any, series: Any) -> dict[str, Any]:
    """The statistic-free preflight summary (the only thing ever printed).

    It carries ``status`` / ``assessed`` / ``not_assessed_reason`` /
    ``binding_ok`` and the per-scenario period counts, and nothing else.
    """
    assessed = getattr(diagnostics, "not_assessed", None) is None
    periods: dict[str, int] = {}
    for record in getattr(series, "records", ()) or ():
        key = str(record.cell)
        periods[key] = len(tuple(record.periods))
    summary = {
        "status": "ASSESSED" if assessed else "NOT_ASSESSED",
        "assessed": bool(assessed),
        "not_assessed_reason": (
            None if assessed else str(diagnostics.not_assessed)
        ),
        "binding_ok": bool(
            getattr(series, "diagnostics_hash", None)
            == getattr(diagnostics, "diagnostics_hash", None)
        ),
        "periods": periods,
    }
    assert_statistic_free(summary)
    return summary


def _preflight_proposal(spec: FactorSpec) -> Any:
    """A minimal proposal-like view of one fixed preflight spec."""
    return _ProposalView(
        proposal_id=content_hash(
            {"domain": "campaign3:preflight-proposal", "spec": spec.version}
        ),
        proposed_factor_spec=spec,
        proposed_factor_spec_hash=spec.version,
    )


@dataclass(frozen=True)
class _ProposalView:
    """The minimal proposal surface the evaluator / screen read."""

    proposal_id: str
    proposed_factor_spec: FactorSpec
    proposed_factor_spec_hash: str


def evaluate_spec(
    assembly: ProductionAssembly, spec: FactorSpec
) -> tuple[Any, Any]:
    """Evaluate one fixed preflight ``spec`` over the real T-A IS fold.

    It drives the assembly's real C3-X :class:`TaEvaluator` (the same one the
    production generation stage uses), so an ``EVENT_SIMPLE`` spec is evaluated
    under K0 + K1 exactly as a generated event candidate would be.  The
    captured IS diagnostics are then exported with
    ``pqv2_series.export_series``, whose binding check runs inside the
    exporter.
    """
    evaluation = assembly.ta_evaluator(_preflight_proposal(spec))
    entry = assembly.ta_cache.get(str(evaluation.diagnostics_hash))
    if entry is None or "provenance" not in entry:
        raise PreflightError(
            f"the real T-A evaluator did not record the preflight diagnostics "
            f"for {spec.id!r}"
        )
    provenance = dict(entry["provenance"])
    diagnostics = entry["diagnostics"]
    series = assembly.export_series_fn(
        assembly.dataset_root,
        provenance["declaration"],
        provenance["spec"],
        provenance["panel"],
        assembly.partition,
        diagnostics=diagnostics,
        fold=_pqv2_fold(provenance["partition_fold"]),
        overlay=assembly.overlay,
        inputs=assembly.inputs,
        event_layer=assembly.event_layer,
    )
    return diagnostics, series


def evaluate_control(
    assembly: ProductionAssembly,
    *,
    control: _run.ControlHypothesis = _run.CONTROL,
) -> tuple[Any, Any]:
    """Evaluate the fixed control FactorSpec over the real T-A IS fold."""
    return evaluate_spec(assembly, control_factor_spec(control))


def _copy_kpit(source: Path | str, scratch_dir: Path) -> Path:
    """Copy the source K-PIT to the scratch directory (the only K-PIT used)."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    copy_path = scratch_dir / "knowledge.jsonl"
    shutil.copyfile(Path(source), copy_path)
    return copy_path


def _probe_copy(copy_path: Path) -> Any:
    """Append the preflight probe record to the **copy** only.

    The probe is a disposable evidence record; it proves the write path
    targets the copy.  Mutation L-M1's target is the copy-path selection in
    :func:`run_preflight`.
    """
    log = KnowledgeLog(copy_path)
    return log.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "OPERATOR",
            "consulted_all_prior": True,
            "preflight_probe": {"schema": PREFLIGHT_RECORD_SCHEMA},
        },
    )


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_preflight(
    *,
    kpit_copy: Path | str,
    run_root: Path | str,
    assembly: ProductionAssembly,
    master_sha: str,
    evaluate: Callable[[ProductionAssembly, FactorSpec], tuple[Any, Any]]
    | None = None,
    print_summary: bool = True,
    writer: Any = None,
    specs: Sequence[PreflightProbe] | None = None,
) -> dict[str, Any]:
    """The ``--preflight`` mode (statistic-free, scratch-only).

    ``kpit_copy`` is the **source** K-PIT to copy; ``run_root`` is the scratch
    directory.  The source is hash-checked before and after, so a write that
    escaped to the production K-PIT fails closed (mutation L-M1).

    Each fixed P6-E4 probe first asserts its planner-verified
    ``certify_candidate`` class; a mismatch raises :class:`PreflightError` (a
    specification defect).  A probe whose evaluation raises is recorded as
    ``FAILED`` with its exception type and :class:`PreflightProbeFailure` is
    raised **without writing a marker**.
    """
    source = Path(kpit_copy)
    scratch = Path(run_root) / "preflight"
    if not source.is_file():
        raise PreflightError(f"the source K-PIT {source} does not exist")
    before = _sha256_file(source)

    copy_path = _copy_kpit(source, scratch)
    # The preflight K-PIT is the copy, and only the copy.
    _run.verify_kpit_terminal(copy_path, _run.pin_kpit_terminal(copy_path))
    _probe_copy(copy_path)

    evaluate_fn = evaluate or evaluate_spec
    probe_list = tuple(specs) if specs is not None else preflight_probes()
    summary: dict[str, Any] = {}
    failures: list[tuple[str, str]] = []
    for probe in probe_list:
        # Assert the planner-verified class *before* evaluation.  A mismatch
        # is a specification defect, never an implementation result.
        classification = _eval.certify_candidate(probe.spec)
        if (
            not _eval.is_certified(classification)
            or str(classification) != probe.certified_class
        ):
            raise PreflightError(
                f"preflight probe {probe.name!r} certifies as "
                f"{classification!s} but the fixed battery expects "
                f"{probe.certified_class!r} (planner spec defect)"
            )
        try:
            diagnostics, series = evaluate_fn(assembly, probe.spec)
        except Exception as exc:  # noqa: BLE001 - the type is the evidence
            summary[probe.name] = {
                "status": "FAILED",
                "error": type(exc).__name__,
            }
            failures.append((probe.name, type(exc).__name__))
            continue
        summary[probe.name] = preflight_summary(diagnostics, series)

    assert_statistic_free(summary)

    after = _sha256_file(source)
    if before != after:
        raise PreflightError(
            "the production K-PIT changed during the preflight; the copy was "
            "not used (mutation L-M1)"
        )

    if failures:
        # A FAILED probe is reported, and the preflight writes **no** marker.
        if print_summary:
            stream = writer if writer is not None else sys.stdout
            stream.write(
                json.dumps(summary, sort_keys=True, ensure_ascii=True) + "\n"
            )
        raise PreflightProbeFailure(summary, failures)

    marker = {
        "schema": LAUNCH_SCHEMA,
        "status": PREFLIGHT_STATUS_OK,
        "master_sha": str(master_sha),
        "bindings_hash": assembly.bindings.content_hash,
        "precommitment_sha256": _run.verify_precommitment(),
        "kpit_source_sha256": before,
        "attempt_id": assembly.attempt_id,
        "summary": summary,
    }
    assert_statistic_free(marker)
    marker_path = Path(run_root) / PREFLIGHT_MARKER_NAME
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        json.dumps(marker, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    assembly.preflight_marker = marker_path
    if print_summary:
        stream = writer if writer is not None else sys.stdout
        stream.write(json.dumps(summary, sort_keys=True, ensure_ascii=True) + "\n")
    return {
        "summary": summary,
        "kpit_copy": str(copy_path),
        "marker": marker,
        "marker_path": str(marker_path),
    }


# ---------------------------------------------------------------------------
# production
# ---------------------------------------------------------------------------


def load_preflight_marker(run_root: Path | str) -> Mapping[str, Any]:
    """Load the preflight marker or fail closed."""
    path = Path(run_root) / PREFLIGHT_MARKER_NAME
    if not path.is_file():
        raise PreflightRequired(
            f"no preflight marker at {path}; run --preflight first"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def require_preflight(
    assembly: ProductionAssembly, *, master_sha: str
) -> Mapping[str, Any]:
    """Require a passing preflight marker for this master SHA + bindings."""
    marker = load_preflight_marker(assembly.run_root)
    if marker.get("status") != PREFLIGHT_STATUS_OK:
        raise PreflightRequired(
            f"the preflight marker status is {marker.get('status')!r}"
        )
    if marker.get("master_sha") != str(master_sha):
        raise PreflightRequired(
            "the preflight marker master SHA does not match the current master "
            f"({marker.get('master_sha')!r} != {master_sha!r})"
        )
    if marker.get("bindings_hash") != assembly.bindings.content_hash:
        raise PreflightRequired(
            "the preflight marker bindings hash does not match the assembled "
            "bindings"
        )
    return marker


def run_production(
    assembly: ProductionAssembly,
    *,
    master_sha: str,
    runner: Any = None,
    launch_terminal: KnowledgeSnapshot | None = None,
) -> Any:
    """The ``--production`` mode: preflight-gated real run (never T-C)."""
    require_preflight(assembly, master_sha=master_sha)
    kwargs = dict(assembly.run_kwargs)
    kwargs["launch_terminal"] = (
        launch_terminal
        if launch_terminal is not None
        else _run.pin_kpit_terminal(assembly.knowledge_log_path)
    )
    campaign = runner if runner is not None else _run.Campaign3Run
    return campaign(**kwargs).run()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _current_master_sha(repo_root: Path | str = _run.REPO_ROOT) -> str:
    """The repository HEAD SHA (the preflight's master identity)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LaunchError(f"cannot determine the master SHA: {exc}") from exc
    return result.stdout.strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m smart_beta.campaign.campaign3_launch",
        description="Campaign-3 production launcher (preflight / production).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--production", action="store_true")
    parser.add_argument("--kpit-copy", default=None)
    parser.add_argument("--knowledge-log", default=str(_run.KNOWLEDGE_LOG))
    parser.add_argument("--run-root", default=str(_run.RUN_ROOT))
    parser.add_argument("--dataset-root", default=None)
    parser.add_argument("--master-sha", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """The CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    master_sha = args.master_sha or _current_master_sha()
    dataset_root = (
        Path(args.dataset_root)
        if args.dataset_root
        else _run.REPO_ROOT.parent / "data_store" / "discovery_dataset_v1"
    )
    run_root = Path(args.run_root)

    if args.preflight:
        if not args.kpit_copy:
            parser.error("--preflight requires --kpit-copy <path>")
        # The preflight uses the fixed control artifact instead of DeepSeek.
        generator = FixedArtifactGenerator(
            artifact=control_raw_artifact(),
            prompt_template_hash=_prompt_template_hash(None),
        )
        assembly = assemble_production(
            dataset_root,
            args.kpit_copy,
            run_root,
            generator,
            attempt_id="preflight",
        )
        run_preflight(
            kpit_copy=args.kpit_copy,
            run_root=run_root,
            assembly=assembly,
            master_sha=master_sha,
        )
        return 0

    # --production: the real DeepSeek generator reads its own credential.
    from smart_beta.research.llm_generator_deepseek import (
        Campaign3DeepSeekGenerator,
    )

    generator = Campaign3DeepSeekGenerator()
    assembly = assemble_production(
        dataset_root, args.knowledge_log, run_root, generator
    )
    run_production(assembly, master_sha=master_sha)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the CLI test
    raise SystemExit(main())
