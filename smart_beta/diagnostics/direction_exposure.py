"""C4M0-13 -- direction audit (M0-1) + common exposure (M0-3), IS only.

Authority: the frozen ``worker_tasks/campaign4_m0/task-c4m0-13-direction-exposure.md``
on top of the M0 plan ``worker_tasks/campaign4_m0/campaign4-m0-plan.md`` section
2 (binding constraints) and decision Q1 of
``docs/campaign4_m1a_generator_contract_design_draft.md``.

What this module owns
---------------------

The **orchestration and reporting** of two descriptive Campaign-3 diagnostics
over the IS fold.  It adds no factor mathematics and no P-Q rule: every input
is the frozen D7b / P-Q v2.3 realization.

* the factor panels come from ``ProductionAssembly.fast_panel_for`` (the
  C3-L-R6 production path, reused verbatim);
* the IS long-short / long series come from the persisted C3
  ``screen_cache/<diagnostics_hash>.export.json`` (:mod:`smart_beta.dataset_v1.pqv2_series`);
* the ``EWC_T23_ONE_SIDED`` t comes from the frozen procedure through
  :func:`smart_beta.diagnostics.known_factors._ewc_statistic` (the C4M0-2
  reuse), solely as a *descriptive* statistic -- this diagnostic makes no
  admission-gated claim;
* every cross-sectional exposure is the mean over IS formations of the
  cross-sectional Spearman correlation (``scipy.stats.spearmanr``), the exact
  A4 method of ``docs/campaign4_m1a_generator_contract_design_draft.md``.

Firewall
--------

IS only.  :func:`smart_beta.campaign.campaign3_eval.assert_is_fold` is called
**before** any persisted input is read or any panel is built, so a non-IS fold
raises :class:`~smart_beta.campaign.campaign3_eval.Campaign3FirewallError`
with no data access.  The module never persists a diagnostics artifact, never
appends to the Knowledge-PIT (diagnostics are not registrations) and never
opens a network path.  The coding-file SHA-256 is verified before **any** data
access at all (task test 5).  Outputs are written only to a caller-supplied
directory outside the repository data tiers.

The rationale-coding file (frozen format)
-----------------------------------------

The planner writes the blind coding file and passes its SHA-256 on the command
line.  The accepted format is::

    {
      "schema": "campaign4-m0-1-rationale-coding-v1",
      "coding": {
        "<proposal_id>": {"driver": "MOM", "relation": "+"},
        ...
      }
    }

``driver`` is one of ``MOM`` / ``REV`` / ``VOL`` / ``DVT`` / ``EAR`` / ``OTHER``
(``OTHER`` means uncoded) and ``relation`` is ``+`` / ``-`` / ``?`` (``?`` means
the rationale does not predict a sign).  A mapping keyed by ``proposal_id`` is
the canonical form; the loader also accepts the driver/relation tokens as
integers (``+1`` / ``-1`` / ``None``).  The SHA-256 is over the raw file bytes.

M0-1 reading rule (frozen)
--------------------------

With ``r = INVERTED / (CONSISTENT + INVERTED)``: ``DIRECTION_DEFECT`` if
``r >= 0.25``, ``DIRECTION_OK`` if ``r <= 0.10``, else ``MIXED``;
``UNDETERMINED`` if fewer than 20 candidates are coded.

M0-3 reading rule (frozen)
--------------------------

``CONCENTRATED`` if the return PC1 share ``>= 0.50`` **or** the A4 replay
rejects ``>= 30`` of 99; ``DIVERSE`` if PC1 share ``< 0.25`` **and** the A4
replay rejects ``< 10``; else ``MODERATE``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import scipy.stats

from smart_beta.campaign import campaign3_eval as _eval
from smart_beta.campaign import campaign3_launch as _launch
from smart_beta.diagnostics import known_factors as _known
from smart_beta.evaluation.partition import Fold
from smart_beta.spec.factor_spec import FactorSpec
from smart_beta.spec.factor_spec import factor_spec_hash as _factor_spec_hash
from smart_beta.spec.factor_spec import from_dict as _factor_spec_from_dict

__all__ = [
    "SCHEMA",
    "REPORT_JSON_NAME",
    "REPORT_MARKDOWN_NAME",
    "CODING_SCHEMA",
    "MIN_COMMON_MEMBERS",
    "CONSISTENCY_THRESHOLD",
    "RESTATEMENT_THRESHOLD",
    "A4_THRESHOLD",
    "NEAR_DUPLICATE_THRESHOLD",
    "DIRECTION_DEFECT_R",
    "DIRECTION_OK_R",
    "UNDETERMINED_MIN_CODED",
    "CONCENTRATED_PC1_SHARE",
    "DIVERSE_PC1_SHARE",
    "CONCENTRATED_A4_REJECTED",
    "DIVERSE_A4_REJECTED",
    "DEFAULT_BOUND_ALPHA",
    "DRIVER_IDS",
    "CODING_DRIVERS",
    "DirectionExposureError",
    "DirectionExposureSpecError",
    "CodingHashMismatch",
    "CodingEntry",
    "Driver",
    "Candidate",
    "build_driver_specs",
    "load_coding",
    "load_candidates",
    "classify_exposure",
    "spearman_exposure",
    "mean_spearman",
    "exposure_similarity_matrix",
    "pearson_correlation_matrix",
    "principal_components",
    "connected_components",
    "a4_replay",
    "run_direction_exposure",
    "write_report",
    "render_markdown",
]

#: The canonical output schema tag.
SCHEMA = "campaign4-m0-13-direction-exposure-v1"

#: The coding-file schema tag (frozen format, see the module docstring).
CODING_SCHEMA = "campaign4-m0-1-rationale-coding-v1"

#: The frozen output file names (task spec "Outputs").
REPORT_JSON_NAME = "m0_13_direction_exposure.json"
REPORT_MARKDOWN_NAME = "m0_13_direction_exposure.md"

#: The task-spec section 1.3 minimum common members per formation.
MIN_COMMON_MEMBERS = 30

#: The section 1.3 classification threshold (``|x| >= 0.20``).
CONSISTENCY_THRESHOLD = 0.20

#: A candidate is a textbook restatement when ``max |E| >= 0.70`` (section 2.2).
RESTATEMENT_THRESHOLD = 0.70

#: The frozen A4 near-duplicate threshold (strictly greater; section 2.2).
A4_THRESHOLD = 0.90

#: The section 2.2 near-duplicate report threshold (``|S| > 0.70``).
NEAR_DUPLICATE_THRESHOLD = 0.70

#: The section 1.4 reading thresholds.
DIRECTION_DEFECT_R = 0.25
DIRECTION_OK_R = 0.10
UNDETERMINED_MIN_CODED = 20

#: The section 2.3 reading thresholds.
CONCENTRATED_PC1_SHARE = 0.50
DIVERSE_PC1_SHARE = 0.25
CONCENTRATED_A4_REJECTED = 30
DIVERSE_A4_REJECTED = 10

#: The one-sided EWC bound alpha (matches C4M0-2; the ``t`` is unchanged).
DEFAULT_BOUND_ALPHA = 0.05

#: The five reference drivers, in the frozen section 1.1 order.
DRIVER_IDS = ("D-MOM", "D-REV", "D-VOL", "D-DVT", "D-EAR")

#: The coding tokens that map to a driver id; ``OTHER`` is uncoded.
CODING_DRIVERS = {
    "MOM": "D-MOM",
    "REV": "D-REV",
    "VOL": "D-VOL",
    "DVT": "D-DVT",
    "EAR": "D-EAR",
    "OTHER": "OTHER",
}

_RELATION_TOKENS = {
    "+": 1,
    "-": -1,
    "?": None,
    "+1": 1,
    "-1": -1,
}


class DirectionExposureError(RuntimeError):
    """Base class for every C4M0-13 fail-closed error."""


class DirectionExposureSpecError(DirectionExposureError):
    """A frozen spec, coding file or persisted input is missing/malformed."""


class CodingHashMismatch(DirectionExposureSpecError):
    """The coding file's SHA-256 does not match the one passed on the CLI."""


# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodingEntry:
    """One planner (blind) rationale code for a proposal."""

    proposal_id: str
    driver: str
    relation: int | None

    @property
    def coded(self) -> bool:
        return self.driver != "OTHER" and self.relation in (1, -1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "driver": self.driver,
            "relation": self.relation,
        }


@dataclass(frozen=True)
class Driver:
    """One reference driver and its certified P-Q v2.3 spec."""

    driver_id: str
    title: str
    spec: FactorSpec
    certified_class: str | None
    not_certified_reason: str | None

    @property
    def spec_sha256(self) -> str:
        return _factor_spec_hash(self.spec)

    def to_dict(self) -> dict[str, Any]:
        return {
            "driver_id": self.driver_id,
            "title": self.title,
            "spec_id": self.spec.id,
            "spec_sha256": self.spec_sha256,
            "certified_class": self.certified_class,
            "not_certified_reason": self.not_certified_reason,
        }


@dataclass(frozen=True)
class Candidate:
    """One persisted Campaign-3 candidate (generation + executor state)."""

    rank: int
    proposal_id: str
    factor_spec_hash: str
    diagnostics_hash: str
    sign: int
    spec: FactorSpec
    research_question: str
    economic_rationale: str
    generation_reason: str


# ---------------------------------------------------------------------------
# small numeric / date helpers
# ---------------------------------------------------------------------------


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _canonical_json(obj: Any) -> str:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _content_hash(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _date_key(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return pd.Timestamp(value).date()


def _is_sha256(value: str) -> bool:
    text = str(value).strip().lower()
    if len(text) != 64:
        return False
    try:
        int(text, 16)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# reference drivers (frozen literals, section 1.1)
# ---------------------------------------------------------------------------


def _requirement(
    semantic_id: str, *, observation_period: str, units: str
) -> dict[str, Any]:
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


def _spec_from_expression(
    *,
    spec_id: str,
    description: str,
    hypothesis: str,
    expression: Mapping[str, Any],
    inputs: Sequence[tuple[str, str, str, str]],
) -> FactorSpec:
    payload = {
        "id": spec_id,
        "description": description,
        "hypothesis": hypothesis,
        "expression": dict(expression),
        "inputs": [
            {
                "alias": alias,
                "requirement": _requirement(
                    semantic_id,
                    observation_period=period,
                    units=units,
                ),
            }
            for alias, semantic_id, period, units in inputs
        ],
        "frequency": "daily",
        "missing_policy": "propagate",
        "sign": 1,
    }
    return _factor_spec_from_dict(payload)


def _momentum_expression() -> dict[str, Any]:
    return {
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
    }


def build_driver_specs() -> tuple[Driver, ...]:
    """Build the five frozen section 1.1 reference drivers.

    D-MOM reuses the frozen ``docs/campaign1_factorspecs.json`` KF-MOM12 spec
    and D-EAR the Campaign-3 positive control, both unchanged.  D-REV / D-VOL /
    D-DVT are the *raw* expressions (``tri/lag(tri,21)``, ``rolling_std(ret,
    252)``, ``rolling_mean(dv,21)/rolling_mean(dv,252)``), so a high value is
    always a high economic variable.  Every spec is classified by
    :func:`~smart_beta.campaign.campaign3_eval.certify_candidate`; a
    ``NOT_CERTIFIED`` driver is returned untouched (and omitted by the run).
    """

    known = {item.factor_id: item for item in _known.build_known_factors()}
    raw_specs = [
        ("D-REV", "short-term reversal (raw tri/lag(tri,21))",
         _spec_from_expression(
             spec_id="c4_driver_rev_21",
             description="raw short-horizon reversal: tri / lag(tri, 21)",
             hypothesis="driver_reversal_raw",
             expression={
                 "op": "div",
                 "left": {"op": "field", "role": "tri"},
                 "right": {
                     "op": "lag",
                     "operand": {"op": "field", "role": "tri"},
                     "periods": 21,
                 },
             },
             inputs=(("tri", "total_return_index", "instant", "ratio"),),
         )),
        ("D-VOL", "volatility (raw rolling_std(ret,252))",
         _spec_from_expression(
             spec_id="c4_driver_vol_252",
             description="raw volatility: rolling_std(ret, 252)",
             hypothesis="driver_volatility_raw",
             expression={
                 "op": "rolling",
                 "fn": "std",
                 "operand": {"op": "field", "role": "ret"},
                 "window": 252,
             },
             inputs=(("ret", "daily_total_return", "period", "fraction"),),
         )),
        ("D-DVT", "dollar-volume trend (raw rolling_mean(dv,21)/rolling_mean(dv,252))",
         _spec_from_expression(
             spec_id="c4_driver_dvt_21_252",
             description=(
                 "raw dollar-volume trend: rolling_mean(dv, 21) / "
                 "rolling_mean(dv, 252)"
             ),
             hypothesis="driver_dollar_volume_raw",
             expression={
                 "op": "div",
                 "left": {
                     "op": "rolling",
                     "fn": "mean",
                     "operand": {"op": "field", "role": "dv"},
                     "window": 21,
                 },
                 "right": {
                     "op": "rolling",
                     "fn": "mean",
                     "operand": {"op": "field", "role": "dv"},
                     "window": 252,
                 },
             },
             inputs=(("dv", "dollar_volume", "period", "currency"),),
         )),
    ]

    merged: list[tuple[str, str, FactorSpec]] = [
        ("D-MOM", "12-1 momentum (raw lag(tri,21)/lag(tri,252))",
         known["KF-MOM12"].spec),
        *raw_specs,
        ("D-EAR", "earnings-announcement primitive (event_ear_k_kp1)",
         _launch.control_factor_spec()),
    ]
    # Keep the frozen section 1.1 order (D-EAR last).
    order = {driver_id: index for index, driver_id in enumerate(DRIVER_IDS)}
    merged.sort(key=lambda item: order[item[0]])

    drivers: list[Driver] = []
    for driver_id, title, spec in merged:
        classification = _eval.certify_candidate(spec)
        certified = _eval.is_certified(classification)
        drivers.append(
            Driver(
                driver_id=driver_id,
                title=title,
                spec=spec,
                certified_class=str(classification) if certified else None,
                not_certified_reason=None if certified else str(classification),
            )
        )
    return tuple(drivers)


# ---------------------------------------------------------------------------
# coding file (verified before any data access)
# ---------------------------------------------------------------------------


def _normalize_driver(value: Any) -> str:
    token = str(value).strip().upper().replace("_", "-")
    if token.startswith("D-"):
        token = token[2:]
    if token not in CODING_DRIVERS:
        raise DirectionExposureSpecError(
            f"unknown coding driver {value!r}; expected one of "
            f"{sorted(CODING_DRIVERS)}"
        )
    return CODING_DRIVERS[token]


def _normalize_relation(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise DirectionExposureSpecError("relation must not be a boolean")
    if isinstance(value, int):
        if value in (1, -1):
            return value
        raise DirectionExposureSpecError(
            f"integer relation {value!r} is not +1 or -1"
        )
    token = str(value).strip()
    if token in _RELATION_TOKENS:
        return _RELATION_TOKENS[token]
    raise DirectionExposureSpecError(
        f"unknown coding relation {value!r}; expected one of '+' / '-' / '?'"
    )


def load_coding(
    path: Path | str,
    expected_sha256: str,
) -> tuple[dict[str, CodingEntry], str]:
    """Verify the coding file SHA-256 and parse the blind codes.

    The hash check runs over the raw file bytes **before** the JSON is parsed
    and before any dataset path is opened (task test 5).  A mismatch raises
    :class:`CodingHashMismatch`; a malformed document raises
    :class:`DirectionExposureSpecError`.
    """

    expected = str(expected_sha256).strip().lower()
    if not _is_sha256(expected):
        raise DirectionExposureSpecError(
            f"--coding-sha256 must be a 64-char lowercase hex SHA-256, got "
            f"{expected_sha256!r}"
        )
    coding_path = Path(path)
    try:
        raw = coding_path.read_bytes()
    except OSError as exc:
        raise DirectionExposureSpecError(
            f"no readable rationale-coding file at {coding_path}"
        ) from exc
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise CodingHashMismatch(
            f"rationale-coding file {coding_path.name!r} has SHA-256 {actual}, "
            f"not the expected {expected}"
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DirectionExposureSpecError(
            f"rationale-coding file {coding_path.name!r} is not valid JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise DirectionExposureSpecError("rationale-coding file is not an object")
    if payload.get("schema") != CODING_SCHEMA:
        raise DirectionExposureSpecError(
            f"rationale-coding schema {payload.get('schema')!r} != "
            f"{CODING_SCHEMA!r}"
        )
    coding = payload.get("coding")
    if not isinstance(coding, Mapping):
        raise DirectionExposureSpecError(
            "rationale-coding file has no 'coding' object"
        )
    entries: dict[str, CodingEntry] = {}
    for proposal_id, record in coding.items():
        pid = str(proposal_id)
        if not _is_sha256(pid):
            raise DirectionExposureSpecError(
                f"coding key {pid!r} is not a 64-char lowercase hex id"
            )
        if not isinstance(record, Mapping):
            raise DirectionExposureSpecError(
                f"coding record for {pid!r} is not an object"
            )
        missing = {"driver", "relation"} - set(record)
        if missing:
            raise DirectionExposureSpecError(
                f"coding record for {pid!r} is missing {sorted(missing)}"
            )
        entries[pid] = CodingEntry(
            proposal_id=pid,
            driver=_normalize_driver(record["driver"]),
            relation=_normalize_relation(record["relation"]),
        )
    return entries, actual


# ---------------------------------------------------------------------------
# persisted candidates (generation + executor state)
# ---------------------------------------------------------------------------


def _load_json_object(path: Path | str, what: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DirectionExposureSpecError(
            f"no readable {what} at {path}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise DirectionExposureSpecError(f"{what} at {path} is not an object")
    return payload


def _proposal_spec(proposal: Mapping[str, Any]) -> FactorSpec:
    raw = proposal.get("proposed_factor_spec")
    if not isinstance(raw, Mapping):
        raise DirectionExposureSpecError(
            "proposal has no 'proposed_factor_spec' object"
        )
    spec_payload = raw.get("spec", raw)
    try:
        return _factor_spec_from_dict(spec_payload)
    except Exception as exc:  # noqa: BLE001 - re-typed, fail closed
        raise DirectionExposureSpecError(
            f"proposal FactorSpec is invalid ({type(exc).__name__}: {exc})"
        ) from exc


def load_candidates(
    generation_path: Path | str,
    executor_state_path: Path | str,
) -> tuple[Candidate, ...]:
    """Load the 99 candidates in their persisted generation order.

    The ``generation.json`` candidate order, the ``executor_state.json``
    proposal order and the manifest ``proposal_ids`` are the same frozen
    order (verified against the real Campaign-3 store).  Each candidate's
    persisted ``factor_spec_hash`` must reproduce its proposal's FactorSpec,
    and its ``sign`` must agree with that spec's ``sign``; otherwise this
    fails closed.
    """

    generation = _load_json_object(generation_path, "generation.json")
    state = _load_json_object(executor_state_path, "executor_state.json")
    raw_candidates = generation.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise DirectionExposureSpecError(
            "generation.json has no non-empty 'candidates' list"
        )
    raw_proposals = state.get("proposals")
    if not isinstance(raw_proposals, list):
        raise DirectionExposureSpecError(
            "executor_state.json has no 'proposals' list"
        )
    proposals = {
        str(proposal.get("proposal_id")): proposal
        for proposal in raw_proposals
        if isinstance(proposal, Mapping)
    }

    candidates: list[Candidate] = []
    for rank, raw in enumerate(raw_candidates, start=1):
        if not isinstance(raw, Mapping):
            raise DirectionExposureSpecError(
                f"generation candidate {rank} is not an object"
            )
        proposal_id = str(raw.get("proposal_id", ""))
        proposal = proposals.get(proposal_id)
        if proposal is None:
            raise DirectionExposureSpecError(
                f"generation candidate {rank} proposal {proposal_id!r} is "
                "absent from the executor state"
            )
        spec = _proposal_spec(proposal)
        declared_hash = str(raw.get("factor_spec_hash", ""))
        if _factor_spec_hash(spec) != declared_hash:
            raise DirectionExposureSpecError(
                f"candidate {rank} ({proposal_id!r}) factor_spec_hash "
                f"{declared_hash!r} does not match its proposal FactorSpec"
            )
        declared_sign = raw.get("sign", spec.sign)
        if int(declared_sign) != int(spec.sign):
            raise DirectionExposureSpecError(
                f"candidate {rank} ({proposal_id!r}) sign {declared_sign!r} "
                f"!= spec sign {spec.sign!r}"
            )
        candidates.append(
            Candidate(
                rank=rank,
                proposal_id=proposal_id,
                factor_spec_hash=declared_hash,
                diagnostics_hash=str(raw.get("diagnostics_hash", "")),
                sign=int(spec.sign),
                spec=spec,
                research_question=str(proposal.get("research_question", "")),
                economic_rationale=str(proposal.get("economic_rationale", "")),
                generation_reason=str(proposal.get("generation_reason", "")),
            )
        )
    return tuple(candidates)


# ---------------------------------------------------------------------------
# exposure arithmetic
# ---------------------------------------------------------------------------


def panel_values_by_date(
    panel: pd.DataFrame,
    *,
    sign: int = 1,
) -> dict[date, dict[str, float | None]]:
    """Bucket a ``(date, stock_id, value)`` panel into normalized per-date maps.

    ``sign`` orients the values (``value *= sign``); the drivers use ``+1`` and
    each candidate uses its spec's frozen ``sign``.
    """

    factor = float(int(sign))
    by_date: dict[date, dict[str, float | None]] = {}
    dates = panel["date"]
    stocks = panel["stock_id"]
    values = panel["value"]
    for raw_date, stock, value in zip(dates, stocks, values):
        key = _date_key(raw_date)
        parsed = _opt_float(value)
        by_date.setdefault(key, {})[str(stock)] = (
            None if parsed is None else factor * parsed
        )
    return by_date


def _spearman_statistic(
    xs: Sequence[float], ys: Sequence[float]
) -> float | None:
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    try:
        result = scipy.stats.spearmanr(xs, ys)
    except ValueError:
        return None
    return _opt_float(result.statistic)


def mean_spearman(
    candidate: Mapping[date, Mapping[str, float | None]],
    driver: Mapping[date, Mapping[str, float | None]],
    *,
    min_common: int = MIN_COMMON_MEMBERS,
) -> float | None:
    """The mean per-formation cross-sectional Spearman over common members.

    A formation with fewer than ``min_common`` common finite members is
    skipped; a formation whose values are constant in either series (Spearman
    undefined) is skipped.  Returns ``None`` when no formation qualifies.
    """

    threshold = int(min_common)
    stats: list[float] = []
    for formation in sorted(candidate):
        left = candidate[formation]
        right = driver.get(formation)
        if not right:
            continue
        xs: list[float] = []
        ys: list[float] = []
        for key, left_value in left.items():
            right_value = right.get(key)
            if left_value is None or right_value is None:
                continue
            xs.append(float(left_value))
            ys.append(float(right_value))
        if len(xs) < threshold:
            continue
        statistic = _spearman_statistic(xs, ys)
        if statistic is not None:
            stats.append(statistic)
    if not stats:
        return None
    return float(sum(stats) / len(stats))


def spearman_exposure(
    candidate_panel: pd.DataFrame,
    driver_panel: pd.DataFrame,
    *,
    candidate_sign: int = 1,
    min_common: int = MIN_COMMON_MEMBERS,
) -> float | None:
    """The section 1.3 exposure ``E`` of one candidate against one driver."""

    return mean_spearman(
        panel_values_by_date(candidate_panel, sign=candidate_sign),
        panel_values_by_date(driver_panel, sign=1),
        min_common=min_common,
    )


def classify_exposure(exposure: float | None, relation: int | None) -> str:
    """The section 1.3 class of one coded candidate's driver exposure.

    A non-finite exposure (no qualifying formation) is ``WEAK``.  The
    threshold is inclusive: ``|x| == 0.20`` is CONSISTENT / INVERTED.
    """

    if relation not in (1, -1):
        return "UNCODED"
    value = _opt_float(exposure)
    if value is None or abs(value) < CONSISTENCY_THRESHOLD:
        return "WEAK"
    sign = 1 if value > 0 else -1
    return "CONSISTENT" if sign == relation else "INVERTED"


# ---------------------------------------------------------------------------
# M0-3 matrices
# ---------------------------------------------------------------------------


def exposure_similarity_matrix(
    panels: Mapping[str, pd.DataFrame],
    *,
    signs: Mapping[str, int] | None = None,
    min_common: int = MIN_COMMON_MEMBERS,
) -> tuple[tuple[str, ...], tuple[tuple[float, ...], ...]]:
    """The 99 x 99 mean cross-sectional Spearman factor-value similarity matrix.

    ``S[i, i] = 1``; a pair with no qualifying formation is recorded as
    ``0.0`` (no measurable co-movement).
    """

    keys = tuple(panels)
    oriented = {
        key: panel_values_by_date(
            panels[key], sign=(1 if signs is None else int(signs[key]))
        )
        for key in keys
    }
    n = len(keys)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            value = mean_spearman(
                oriented[keys[i]], oriented[keys[j]], min_common=min_common
            )
            if value is None:
                value = 0.0
            matrix[i][j] = value
            matrix[j][i] = value
    return keys, tuple(tuple(row) for row in matrix)


def pearson_correlation_matrix(
    series: Mapping[str, Mapping[date, float | None]],
) -> tuple[tuple[str, ...], tuple[tuple[float, ...], ...]]:
    """The pairwise Pearson correlation matrix over common finite periods."""

    keys = tuple(series)
    n = len(keys)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            left = series[keys[i]]
            right = series[keys[j]]
            common = [
                key
                for key, value in left.items()
                if value is not None and right.get(key) is not None
            ]
            correlation = 0.0
            if len(common) >= 2:
                xs = np.asarray([left[key] for key in common], dtype="float64")
                ys = np.asarray([right[key] for key in common], dtype="float64")
                if xs.std() > 0.0 and ys.std() > 0.0:
                    correlation = float(np.corrcoef(xs, ys)[0, 1])
            matrix[i][j] = correlation
            matrix[j][i] = correlation
    return keys, tuple(tuple(row) for row in matrix)


def principal_components(
    correlation: Sequence[Sequence[float]],
) -> tuple[float, int, tuple[float, ...]]:
    """``(PC1 share, #PCs for 80%, eigenvalues desc)`` of a correlation matrix."""

    matrix = np.asarray(correlation, dtype="float64")
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise DirectionExposureSpecError("correlation matrix is not square")
    n = int(matrix.shape[0])
    if n == 0:
        return 0.0, 0, ()
    symmetric = (matrix + matrix.T) / 2.0
    eigenvalues = np.clip(np.linalg.eigvalsh(symmetric), 0.0, None)
    descending = np.sort(eigenvalues)[::-1]
    total = float(descending.sum())
    if total <= 0.0:
        return 0.0, 0, tuple(float(value) for value in descending)
    shares = descending / total
    pc1 = float(shares[0])
    cumulative = np.cumsum(shares)
    n80 = int(np.searchsorted(cumulative, 0.80, side="left") + 1)
    return pc1, n80, tuple(float(value) for value in descending)


def connected_components(
    keys: Sequence[str],
    similarity: Mapping[tuple[str, str], float],
    *,
    threshold: float = A4_THRESHOLD,
) -> tuple[tuple[str, ...], ...]:
    """The connected components of the graph ``|S| > threshold``."""

    parent = {key: key for key in keys}

    def find(item: str) -> str:
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != root:
            parent[item], item = root, parent[item]
        return root

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for (left, right), value in similarity.items():
        if abs(value) > threshold:
            union(left, right)
    groups: dict[str, list[str]] = defaultdict(list)
    for key in keys:
        groups[find(key)].append(key)
    return tuple(
        sorted(tuple(sorted(members)) for members in groups.values())
    )


def _similarity_lookup(
    keys: Sequence[str],
    matrix: Sequence[Sequence[float]],
) -> dict[tuple[str, str], float]:
    lookup: dict[tuple[str, str], float] = {}
    for i, left in enumerate(keys):
        for j in range(i + 1, len(keys)):
            lookup[(left, keys[j])] = float(matrix[i][j])
    return lookup


def a4_replay(
    order: Sequence[str],
    similarity: Mapping[tuple[str, str], float],
    *,
    threshold: float = A4_THRESHOLD,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Replay the frozen A4 gate in proposal order.

    A candidate is rejected when it exceeds ``threshold`` in absolute
    similarity with **any earlier admitted** candidate; otherwise it is
    admitted.  Returns ``(admitted, rejected)`` in order.
    """

    admitted: list[str] = []
    rejected: list[str] = []
    for candidate in order:
        near_duplicate = False
        for earlier in admitted:
            key = (
                (candidate, earlier)
                if (candidate, earlier) in similarity
                else (earlier, candidate)
            )
            value = similarity.get(key)
            if value is not None and abs(value) > threshold:
                near_duplicate = True
                break
        if near_duplicate:
            rejected.append(candidate)
        else:
            admitted.append(candidate)
    return tuple(admitted), tuple(rejected)


# ---------------------------------------------------------------------------
# persisted screen-cache exports
# ---------------------------------------------------------------------------


def _load_export(
    screen_cache_dir: Path | str,
    diagnostics_hash: str,
) -> Mapping[str, Any]:
    directory = Path(screen_cache_dir)
    path = directory / f"{diagnostics_hash}.export.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DirectionExposureSpecError(
            f"no readable screen-cache export at {path}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise DirectionExposureSpecError(
            f"screen-cache export {path.name!r} is not an object"
        )
    if str(payload.get("diagnostics_hash", "")) != diagnostics_hash:
        raise DirectionExposureSpecError(
            f"screen-cache export {path.name!r} names diagnostics_hash "
            f"{payload.get('diagnostics_hash')!r}, not {diagnostics_hash!r}"
        )
    return payload


def _choose_l_record(
    records: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    by_cell = {
        str(record.get("cell")): record
        for record in records
        if isinstance(record, Mapping)
    }
    for cell in ("L-T0-K0", "L-T0"):
        if cell in by_cell:
            return by_cell[cell]
    return None


def _series_by_date(
    periods: Sequence[Any],
    values: Sequence[Any],
) -> dict[date, float | None]:
    return {
        _date_key(period): _opt_float(value)
        for period, value in zip(periods, values)
    }


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def _class_summary(
    audits: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, int], ...]:
    counter = Counter(str(audit["classification"]) for audit in audits)
    return tuple(sorted(counter.items()))


def _ewc_by_class(
    audits: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for audit in audits:
        value = _opt_float(audit.get("l_t0_ewc_t"))
        if value is not None:
            grouped[str(audit["classification"])].append(value)
    return {
        label: {
            "n": len(values),
            "mean": float(sum(values) / len(values)) if values else None,
            "min": float(min(values)) if values else None,
            "max": float(max(values)) if values else None,
        }
        for label, values in sorted(grouped.items())
    }


def run_direction_exposure(
    assembly: Any,
    *,
    generation_path: Path | str,
    executor_state_path: Path | str,
    screen_cache_dir: Path | str,
    coding_path: Path | str,
    coding_sha256: str,
    output_dir: Path | str | None = None,
    fold: Fold | None = None,
    bound_alpha: float = DEFAULT_BOUND_ALPHA,
    min_common: int = MIN_COMMON_MEMBERS,
) -> dict[str, Any]:
    """Run C4M0-13 and (optionally) write the JSON + Markdown reports.

    Check order (fail closed): the coding-file SHA-256 is verified first (no
    data access), then the IS firewall is asserted (no panel), then the
    persisted inputs are read and the panels are built.
    """

    coding, verified_hash = load_coding(coding_path, coding_sha256)

    selected_fold = fold if fold is not None else assembly.is_fold
    # The IS firewall is asserted before any persisted read or panel build.
    _eval.assert_is_fold(selected_fold)

    drivers = build_driver_specs()
    certified_drivers = tuple(
        driver for driver in drivers if driver.certified_class is not None
    )
    candidates = load_candidates(generation_path, executor_state_path)

    # ---- panels (candidate and driver), cached by spec hash -----------------
    panel_cache: dict[str, pd.DataFrame] = {}

    def panel_for(spec: FactorSpec) -> pd.DataFrame:
        key = _factor_spec_hash(spec)
        if key not in panel_cache:
            panel_cache[key] = assembly.fast_panel_for(spec, selected_fold)
        return panel_cache[key]

    # ---- M0-1 exposures and classification ---------------------------------
    candidate_panels: dict[str, pd.DataFrame] = {
        candidate.proposal_id: panel_for(candidate.spec)
        for candidate in candidates
    }
    driver_frames: dict[str, Mapping[date, Mapping[str, float | None]]] = {}
    for driver in certified_drivers:
        driver_frames[driver.driver_id] = panel_values_by_date(
            panel_for(driver.spec), sign=1
        )
    candidate_frames = {
        candidate.proposal_id: panel_values_by_date(
            candidate_panels[candidate.proposal_id], sign=candidate.sign
        )
        for candidate in candidates
    }

    audits: list[dict[str, Any]] = []
    inversion_counts: Counter[str] = Counter()
    for candidate in candidates:
        entry = coding.get(candidate.proposal_id)
        driver_id = entry.driver if entry is not None else "OTHER"
        relation = entry.relation if entry is not None else None
        exposures: dict[str, float | None] = {}
        for driver in certified_drivers:
            exposures[driver.driver_id] = mean_spearman(
                candidate_frames[candidate.proposal_id],
                driver_frames[driver.driver_id],
                min_common=min_common,
            )
        coded_driver = (
            driver_id if driver_id in exposures else None
        )
        exposure = None if coded_driver is None else exposures[coded_driver]
        classification = (
            "UNCODED"
            if not (entry is not None and entry.coded)
            else classify_exposure(exposure, relation)
        )
        inversion_counts[classification] += 1

        export = _load_export(screen_cache_dir, candidate.diagnostics_hash)
        export_body = export.get("export")
        records = (
            export_body.get("records", [])
            if isinstance(export_body, Mapping)
            else []
        )
        record = _choose_l_record(records)
        ewc_t: float | None = None
        ewc_status = "NOT_ASSESSED"
        if record is not None and record.get("status") is None:
            periods = list(record.get("periods", []))
            values = list(record.get("l_series", []))
            if periods and len(periods) == len(values):
                parsed_periods = [_date_key(period) for period in periods]
                ewc_t, _p, ewc_status = _known._ewc_statistic(
                    parsed_periods,
                    [float(value) for value in values],
                    bound_alpha=float(bound_alpha),
                )
        finite_exposures = [
            abs(value)
            for value in exposures.values()
            if _opt_float(value) is not None
        ]
        max_abs = max(finite_exposures) if finite_exposures else None
        audits.append(
            {
                "rank": candidate.rank,
                "proposal_id": candidate.proposal_id,
                "factor_spec_hash": candidate.factor_spec_hash,
                "diagnostics_hash": candidate.diagnostics_hash,
                "driver": driver_id,
                "relation": relation,
                "exposures": {
                    driver_id_: _opt_float(value)
                    for driver_id_, value in exposures.items()
                },
                "exposure": _opt_float(exposure),
                "classification": classification,
                "l_t0_ewc_t": _opt_float(ewc_t),
                "l_t0_ewc_status": ewc_status,
                "max_abs_exposure": _opt_float(max_abs),
                "restatement": bool(
                    max_abs is not None and max_abs >= RESTATEMENT_THRESHOLD
                ),
            }
        )

    consistent = int(inversion_counts.get("CONSISTENT", 0))
    inverted = int(inversion_counts.get("INVERTED", 0))
    n_coded = consistent + inverted
    if n_coded < UNDETERMINED_MIN_CODED:
        direction_reading = "UNDETERMINED"
        inversion_rate = None
    else:
        inversion_rate = inverted / n_coded
        if inversion_rate >= DIRECTION_DEFECT_R:
            direction_reading = "DIRECTION_DEFECT"
        elif inversion_rate <= DIRECTION_OK_R:
            direction_reading = "DIRECTION_OK"
        else:
            direction_reading = "MIXED"

    direction_audit = {
        "candidates": audits,
        "counts": dict(_class_summary(audits)),
        "n_coded": n_coded,
        "n_consistent": consistent,
        "n_inverted": inverted,
        "inversion_rate": (
            None if inversion_rate is None else float(inversion_rate)
        ),
        "reading": direction_reading,
        "l_t0_ewc_by_class": _ewc_by_class(audits),
    }

    # ---- M0-3 common exposure ---------------------------------------------
    candidate_order = tuple(candidate.proposal_id for candidate in candidates)
    return_series: dict[str, dict[date, float | None]] = {}
    for candidate in candidates:
        export = _load_export(screen_cache_dir, candidate.diagnostics_hash)
        export_body = export.get("export")
        records = (
            export_body.get("records", [])
            if isinstance(export_body, Mapping)
            else []
        )
        record = _choose_l_record(records)
        if record is None:
            return_series[candidate.proposal_id] = {}
        else:
            return_series[candidate.proposal_id] = _series_by_date(
                list(record.get("periods", [])),
                list(record.get("net_series", [])),
            )

    _ret_keys, correlation = pearson_correlation_matrix(return_series)
    pc1_share, n_pcs_80, eigenvalues = principal_components(correlation)

    similarity_keys, similarity = exposure_similarity_matrix(
        candidate_panels,
        signs={candidate.proposal_id: candidate.sign for candidate in candidates},
        min_common=min_common,
    )
    lookup = _similarity_lookup(similarity_keys, similarity)
    pair_gt_090 = sum(
        1 for value in lookup.values() if abs(value) > A4_THRESHOLD
    )
    pair_gt_070 = sum(
        1 for value in lookup.values() if abs(value) > NEAR_DUPLICATE_THRESHOLD
    )
    components = connected_components(
        similarity_keys, lookup, threshold=A4_THRESHOLD
    )
    admitted, rejected = a4_replay(
        candidate_order, lookup, threshold=A4_THRESHOLD
    )
    restatement_count = sum(
        1 for audit in audits if audit["restatement"]
    )

    if (
        pc1_share >= CONCENTRATED_PC1_SHARE
        or len(rejected) >= CONCENTRATED_A4_REJECTED
    ):
        common_reading = "CONCENTRATED"
    elif (
        pc1_share < DIVERSE_PC1_SHARE
        and len(rejected) < DIVERSE_A4_REJECTED
    ):
        common_reading = "DIVERSE"
    else:
        common_reading = "MODERATE"

    common_exposure = {
        "n_candidates": len(candidates),
        "n_series": sum(1 for values in return_series.values() if values),
        "pc1_share": float(pc1_share),
        "n_pcs_80": int(n_pcs_80),
        "eigenvalues": [float(value) for value in eigenvalues],
        "return_correlation_matrix": [
            [float(value) for value in row] for row in correlation
        ],
        "return_correlation_keys": list(_ret_keys),
        "similarity_pair_counts": {
            "gt_0_90": int(pair_gt_090),
            "gt_0_70": int(pair_gt_070),
        },
        "components_gt_0_90": [list(group) for group in components],
        "n_components_gt_0_90": len(components),
        "a4_admitted": list(admitted),
        "a4_rejected": list(rejected),
        "n_a4_rejected": len(rejected),
        "restatement_count": int(restatement_count),
        "exposure_similarity_matrix": [
            [float(value) for value in row] for row in similarity
        ],
        "exposure_similarity_keys": list(similarity_keys),
        "reading": common_reading,
    }

    body = {
        "schema": SCHEMA,
        "fold": {
            "role": str(selected_fold.role.value),
            "start": selected_fold.start.strftime("%Y-%m-%d"),
            "end": selected_fold.end.strftime("%Y-%m-%d"),
        },
        "universe": str(_eval.TA_UNIVERSE),
        "coding": {
            "path": str(coding_path),
            "sha256": verified_hash,
            "n_entries": len(coding),
            "n_unknown": sum(
                1
                for proposal_id in coding
                if proposal_id not in candidate_order
            ),
        },
        "bound_alpha": float(bound_alpha),
        "min_common_members": int(min_common),
        "drivers": [driver.to_dict() for driver in drivers],
        "m0_1": direction_audit,
        "m0_3": common_exposure,
    }
    body["content_sha256"] = _content_hash(body)

    if output_dir is not None:
        write_report(body, output_dir)
    return body


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def write_report(
    report: Mapping[str, Any],
    output_dir: Path | str,
) -> tuple[Path, Path]:
    """Write the canonical JSON and the rendered Markdown into ``output_dir``."""

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / REPORT_JSON_NAME
    markdown_path = directory / REPORT_MARKDOWN_NAME
    json_path.write_text(_canonical_json(dict(report)) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _fmt(value: Any, digits: int = 4) -> str:
    number = _opt_float(value)
    if number is None:
        return "n/a"
    return f"{number:.{digits}f}"


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render the C4M0-13 report as Markdown."""

    fold = report["fold"]
    direction = report["m0_1"]
    common = report["m0_3"]
    lines: list[str] = [
        "# Campaign-4 M0-13 -- direction audit (M0-1) + common exposure (M0-3)",
        "",
        f"- schema: `{report['schema']}`",
        f"- fold: `{fold['role']}` [{fold['start']}, {fold['end']})",
        f"- universe: `{report['universe']}`",
        f"- coding SHA-256: `{report['coding']['sha256']}`",
        f"- content SHA-256: `{report['content_sha256']}`",
        "",
        "## M0-1 direction audit",
        "",
        f"- coded (CONSISTENT + INVERTED): {direction['n_coded']}",
        f"- inversion rate r: {_fmt(direction['inversion_rate'])}",
        f"- reading: **{direction['reading']}**",
        "",
        "| class | count |",
        "|---|---|",
    ]
    for label, count in sorted(direction["counts"].items()):
        lines.append(f"| {label} | {count} |")
    lines.extend(
        [
            "",
            "| rank | proposal | driver | relation | E | class | "
            "L-T0-K0 EWC t | max abs E |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for audit in direction["candidates"]:
        lines.append(
            "| {rank} | `{pid}` | {driver} | {relation} | {exposure} | "
            "{cls} | {ewc} | {maxe} |".format(
                rank=audit["rank"],
                pid=str(audit["proposal_id"])[:12],
                driver=audit["driver"],
                relation=(
                    "?"
                    if audit["relation"] is None
                    else ("+" if audit["relation"] == 1 else "-")
                ),
                exposure=_fmt(audit["exposure"]),
                cls=audit["classification"],
                ewc=_fmt(audit["l_t0_ewc_t"]),
                maxe=_fmt(audit["max_abs_exposure"]),
            )
        )
    lines.extend(
        [
            "",
            "## M0-3 common exposure",
            "",
            f"- candidates: {common['n_candidates']}, series: {common['n_series']}",
            f"- PC1 share: {_fmt(common['pc1_share'])}",
            f"- PCs for 80%: {common['n_pcs_80']}",
            f"- pairs |S| > 0.90: {common['similarity_pair_counts']['gt_0_90']}",
            f"- pairs |S| > 0.70: {common['similarity_pair_counts']['gt_0_70']}",
            f"- components |S| > 0.90: {common['n_components_gt_0_90']}",
            f"- A4 replay rejected: {common['n_a4_rejected']} of "
            f"{common['n_candidates']}",
            f"- textbook restatements (max |E| >= 0.70): "
            f"{common['restatement_count']}",
            f"- reading: **{common['reading']}**",
            "",
            "## Reference drivers",
            "",
            "| driver | expression spec | certified class |",
            "|---|---|---|",
        ]
    )
    for driver in report["drivers"]:
        lines.append(
            f"| {driver['driver_id']} | `{driver['spec_id']}` | "
            f"{driver['certified_class'] or driver['not_certified_reason']} |"
        )
    lines.append("")
    return "\n".join(lines)
