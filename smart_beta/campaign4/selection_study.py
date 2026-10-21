"""Campaign-4 M1-s -- the selection-rule study (task C4M1-3).

Authority
---------

``worker_tasks/campaign4_m1/campaign4-m1-plan.md`` (binding constraints),
``worker_tasks/campaign4_m1/task-c4m1-3-selection-study.md`` (this task) and
``docs/campaign4_m1s_selection_study_draft.md`` sections 1--4 (frozen
2026-10-19, user Q9/Q10).

Question
--------

M1-e failed out of window.  M1-s asks whether the failure came from the
**selection rule** (m = 1 by the training-window EWC t) or from the
**candidate pool** itself.  Nothing is regenerated here: the four M1-e pools
are read back from their ``executor_state.json``, every eligible candidate is
re-evaluated once on the full IS fold, and the four pre-registered rules
``S0``--``S3`` are applied to that pool.

Pool reconstruction (which state fields mean EVALUATED)
------------------------------------------------------

An M1-e run labels a proposal ``EVALUATED`` when it is certified, admitted by
the A4 gate (treatment arm), actually evaluated, assessed, and carries a
**VALID** training rank statistic.  Four conjuncts are read straight from the
persisted write-ahead state and the fifth is recomputed:

* **certified** -- ``state["certified"][proposal_id] is True`` (the executor's
  own ``is_certified`` result; a proposal absent from the map, or ``False``, is
  ``NOT_CERTIFIED`` and is never evaluated);
* **evaluated** -- ``proposal_id in state["evaluated"]`` (the executor records
  one ``TaEvaluation`` per candidate it handed to the evaluator);
* **assessed** -- ``state["evaluated"][proposal_id]["assessed"] is True``;
* **not labelled NOT_ASSESSED** --
  ``state["evaluated"][proposal_id]["not_assessed_reason"] is None``.  The C4
  evaluator writes ``status = "EVALUATED" if rank.status == VALID else
  "NOT_ASSESSED"`` together with ``not_assessed_reason = None`` only for the
  ``VALID`` branch, so this pair is the persisted image of that verdict;
* **A4-admitted (treatment)** and **A2 valid** follow from the *assessed*
  conjunct: :meth:`Campaign4Evaluator._reject` is the only path that refuses a
  proposal before the real evaluation, and it always records
  ``assessed=False`` with a typed reason (``NEAR_DUPLICATE``, ``FAMILY_CAP``,
  ``A2_DECLARATION_INVALID``, ``SCHEMA_REJECTED``).  Those codes are visible in
  ``not_assessed_reason`` and are reported as such, never silently pooled;
* **VALID training rank statistic** is *not* persisted (the state stores no
  rank statistic), so it is reconstructed by re-evaluating the candidate and
  recomputing :func:`~smart_beta.campaign4.ranking.rank_statistic` under the
  run's own window cut.  Review R3: an M1-e run decides NOT_ASSESSED from that
  **recomputed** statistic rather than from a state field, so a candidate can
  carry ``assessed``/``not_assessed_reason`` as EVALUATED in the state and
  still be NOT_ASSESSED under the P-Q rule.  Such a candidate is therefore
  *excluded* and labelled ``NOT_ASSESSED`` with the rank statistic's own
  reason (:func:`filter_valid_candidates`); it does **not** raise.  A raise is
  kept only for a genuine inconsistency -- a spec-hash mismatch or a missing
  cached series.  Because the VALID filter can only run once the series are
  loaded, the frozen EVALUATED-count hard check is made *after* it and
  *before* any selection file is written (review R3).

Ordering is the persisted registry order (``state["proposals"]``), which is the
response order the run counted candidates in; ``proposal_order`` is that
0-based index and is the tie-break key ("ties go to the earlier proposal").

Firewall
--------

Constraint 2 of the plan applies to **selection**: every rule value is computed
through :func:`_training_basis`, which windows by ``formation date < cut`` and
then re-asserts that no used period is at/after the cut
(:class:`~smart_beta.campaign4.contract.C4FirewallError`).  Test-window values
are computed only after every selection file has been written and hashed.

Import-only
-----------

Nothing here reads ``data_store``, the reserve/holdout/sealed folds, the
Knowledge-PIT or the network, and no sealed module is modified.  The EWC
procedure, the series windowing and the EWC aggregation are **imported** from
:mod:`smart_beta.campaign4.ranking` (which itself calls the frozen
:func:`smart_beta.diagnostics.known_factors._ewc_statistic`) so this module can
never drift from the C4M1-1 statistic it must reproduce bitwise.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from smart_beta.campaign4.contract import C4FirewallError
from smart_beta.campaign4.generation import (
    CAMPAIGN4_EXECUTOR_SCHEMA,
    CONTROL,
    TREATMENT,
)
from smart_beta.campaign4.ranking import (
    DEFAULT_BOUND_ALPHA,
    INSUFFICIENT_SAMPLE,
    MIN_EWC_PERIODS,
    NOT_ASSESSED,
    VALID,
    RankStatistic,
)

# Private imports of the frozen C4M1-1 statistic internals.  They are
# deliberate and recorded here (task spec: "reuses the EWC procedure exactly as
# campaign4/ranking.py does"): re-implementing the windowing, the EWC call or
# the min-over-scenarios aggregation would be a copy that could drift from the
# statistic S0 must reproduce bitwise.
from smart_beta.campaign4.ranking import (
    TRAINING,
    _aggregate as _ranking_aggregate,
    _ewc as _ranking_ewc,
    rank_statistic,
    test_statistics,
    window_series,
)
from smart_beta.campaign4.walkforward import ORIGINS, write_selection_file
from smart_beta.spec.factor_spec import FactorSpec, factor_spec_hash

__all__ = [
    "EVALUATED",
    "SELECTED",
    "NO_SELECTION",
    "ERROR",
    "RULES",
    "S1_LAST_N",
    "S3_SIZE",
    "SERIES_CACHE_SCHEMA",
    "SelectionStudyError",
    "PoolCandidate",
    "RunPool",
    "CellSeries",
    "CandidateSeries",
    "CompositeSeries",
    "SeriesCache",
    "RankedCandidate",
    "RuleSelection",
    "SelectionStudyDeps",
    "load_run_pool",
    "filter_valid_candidates",
    "expected_counts_from_reports",
    "assert_training_periods",
    "s0_stat",
    "s1_stat",
    "halves_positive",
    "composite_series",
    "select_rule",
    "spearman_rank_correlation",
    "run_selection_study",
    "assert_outside_repository",
    "assert_not_inside",
]

#: The four pre-registered rules (draft section 2).
RULES: tuple[str, ...] = ("S0", "S1", "S2", "S3")

#: S1's recency window (the EWC ``MIN_PERIODS``; draft section 2).
S1_LAST_N = int(MIN_EWC_PERIODS)

#: S3's composite size.
S3_SIZE = 5

#: The persisted series-cache document schema.
SERIES_CACHE_SCHEMA = "campaign4-m1s-series-cache-v1"

#: The frozen EVALUATED definition (draft section 1; review R3).  Four persisted
#: state conjuncts plus the recomputed training rank statistic.
EVALUATED_DEFINITION = (
    "state['certified'][pid] is True AND pid in state['evaluated'] AND "
    "state['evaluated'][pid]['assessed'] is True AND "
    "state['evaluated'][pid]['not_assessed_reason'] is None (A4/A2/schema "
    "refusals all record assessed=False), AND the recomputed training "
    "rank statistic at the run's window cut is VALID (a candidate that fails "
    "only this last conjunct is labelled NOT_ASSESSED and excluded, never "
    "raised)"
)

#: What the training values are computed from (a report field).
_SELECTION_BASIS: dict[str, Any] = {
    "series": "the TD record's long-side (l_series) per-period series",
    "note": (
        "the frozen rank/test path consumes l_series; the export's "
        "net_series is cached alongside for audit"
    ),
    "training_only": True,
}

#: Pool / selection status vocabulary.
EVALUATED = "EVALUATED"
NO_SELECTION = "NO_SELECTION"
SELECTED = "SELECTED"
ERROR = "ERROR"

#: The arm names the C4 walk-forward uses.
ARMS: tuple[str, ...] = (TREATMENT, CONTROL)


class SelectionStudyError(RuntimeError):
    """The selection-study contract is malformed (fail closed)."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise SelectionStudyError(f"unparsable date value {value!r}")


def _canonical_json(obj: Any) -> str:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _canonical_hash(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _atomic_write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def _atomic_write_json(path: Path, payload: Any) -> Path:
    return _atomic_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
    )


# ---------------------------------------------------------------------------
# path refusals (task spec section 2: "Refusals")
# ---------------------------------------------------------------------------

#: The repository root of this checkout (``scripts/run_c4_selection_study.py``
#: and every output of this study must live outside it).
REPO_ROOT = Path(__file__).resolve().parents[2]


def _parents(path: Path) -> tuple[Path, ...]:
    return (path, *path.parents)


def assert_outside_repository(path: Path | str) -> Path:
    """Refuse an output/cache directory inside the repository or data_store."""

    resolved = Path(path).expanduser().resolve()
    root = REPO_ROOT.resolve()
    for parent in _parents(resolved):
        if parent == root:
            raise SelectionStudyError(
                f"{resolved} is inside the repository ({root}); C4M1-3 outputs "
                "and caches must live outside it"
            )
        if parent.name == "data_store":
            raise SelectionStudyError(
                f"{resolved} is inside a data_store; C4M1-3 outputs and caches "
                "must live outside it"
            )
    return resolved


def assert_not_inside(path: Path | str, forbidden: Path | str, *, what: str) -> Path:
    """Refuse ``path`` when it is inside (or equal to) ``forbidden``."""

    resolved = Path(path).expanduser().resolve()
    other = Path(forbidden).expanduser().resolve()
    for parent in _parents(resolved):
        if parent == other:
            raise SelectionStudyError(
                f"{what} {resolved} is inside {other}; that tree is read-only "
                "for C4M1-3"
            )
    return resolved


# ---------------------------------------------------------------------------
# the cached series protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CellSeries:
    """One TD cell's cached per-period series.

    ``l_series`` is the record's long-side series: the exact series the frozen
    C4M1-1 :func:`~smart_beta.campaign4.ranking.rank_statistic` and
    :func:`~smart_beta.campaign4.ranking.test_statistics` consume (so a cached
    candidate reproduces the live statistic bitwise).  ``net_series`` is the
    export record's observed net D7b series, cached alongside for audit.  On an
    unassessed cell both are empty and ``reason`` carries the typed reason.

    The object duck-types the parts of ``PQv2SeriesRecord`` the C4 ranking /
    test path uses (``periods``, ``l_series``, ``assessed``, ``reason``).
    """

    cell: str
    periods: tuple[date, ...] = ()
    l_series: tuple[float, ...] = ()
    net_series: tuple[float, ...] = ()
    assessed: bool = True
    reason: str | None = None

    @property
    def values(self) -> tuple[float, ...]:
        """The per-period series every statistic in this study uses."""

        return self.l_series

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "periods": [item.isoformat() for item in self.periods],
            "l_series": [float(value) for value in self.l_series],
            "net_series": [float(value) for value in self.net_series],
            "assessed": bool(self.assessed),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CellSeries":
        return cls(
            cell=str(payload["cell"]),
            periods=tuple(_as_date(item) for item in payload.get("periods", ())),
            l_series=tuple(float(item) for item in payload.get("l_series", ())),
            net_series=tuple(float(item) for item in payload.get("net_series", ())),
            assessed=bool(payload.get("assessed", True)),
            reason=payload.get("reason"),
        )


@dataclass(frozen=True)
class CandidateSeries:
    """One candidate's cached TD series (every TD scenario, all IS periods)."""

    factor_spec_hash: str
    cells: tuple[CellSeries, ...]
    declaration_hash: str | None = None
    diagnostics_hash: str | None = None
    not_assessed: bool = False
    not_assessed_reason: str | None = None

    def get(self, cell: str) -> CellSeries | None:
        for item in self.cells:
            if item.cell == cell:
                return item
        return None

    @property
    def cell_names(self) -> tuple[str, ...]:
        return tuple(item.cell for item in self.cells)

    @property
    def periods(self) -> tuple[date, ...]:
        for item in self.cells:
            if item.periods:
                return item.periods
        return ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SERIES_CACHE_SCHEMA,
            "factor_spec_hash": self.factor_spec_hash,
            "declaration_hash": self.declaration_hash,
            "diagnostics_hash": self.diagnostics_hash,
            "not_assessed": bool(self.not_assessed),
            "not_assessed_reason": self.not_assessed_reason,
            "cells": [item.to_dict() for item in self.cells],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CandidateSeries":
        schema = payload.get("schema")
        if schema != SERIES_CACHE_SCHEMA:
            raise SelectionStudyError(
                f"series cache document schema {schema!r} is not "
                f"{SERIES_CACHE_SCHEMA!r}"
            )
        return cls(
            factor_spec_hash=str(payload["factor_spec_hash"]),
            cells=tuple(CellSeries.from_dict(item) for item in payload["cells"]),
            declaration_hash=payload.get("declaration_hash"),
            diagnostics_hash=payload.get("diagnostics_hash"),
            not_assessed=bool(payload.get("not_assessed", False)),
            not_assessed_reason=payload.get("not_assessed_reason"),
        )


@dataclass(frozen=True)
class CompositeSeries:
    """The S3 composite: an equal-weight period-by-period mean of 5 series.

    ``get("L-TD")`` returns the composite as a single-cell series view so the
    frozen :func:`~smart_beta.campaign4.ranking.test_statistics` consumes it
    exactly as it consumes a candidate's ``L-TD`` cell (W1 EWC t / W2
    descriptive statistics of the composite itself).
    """

    members: tuple[tuple[str, str], ...]
    periods: tuple[date, ...]
    values: tuple[float, ...]

    def get(self, cell: str) -> CellSeries | None:
        if cell != "L-TD":
            return None
        return CellSeries(
            cell="L-TD",
            periods=self.periods,
            l_series=self.values,
            net_series=self.values,
            assessed=True,
            reason=None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "members": [
                {"factor_spec_hash": key, "cell": cell} for key, cell in self.members
            ],
            "periods": [item.isoformat() for item in self.periods],
            "values": [float(value) for value in self.values],
        }


#: The two shapes a "series" argument may take.
SeriesLike = CandidateSeries | CompositeSeries


# ---------------------------------------------------------------------------
# the series cache
# ---------------------------------------------------------------------------


def _td_cell_names(export: Any) -> tuple[str, ...]:
    """The ``L-TD`` cell names of an export, in export order."""

    names: list[str] = []
    for name in ("L-TD", "L-TD-K0", "L-TD-K1"):
        if export.get(name) is not None:
            names.append(name)
    return tuple(names)


def series_from_evaluation(
    factor_spec_hash_value: str, evaluation: Any
) -> CandidateSeries:
    """The cacheable series of one live full-IS evaluation."""

    td_export = getattr(evaluation, "td_export", None)
    if td_export is None or not hasattr(td_export, "get"):
        raise SelectionStudyError(
            "the evaluation carries no TD export; the selection study needs the "
            "per-period L-TD series"
        )
    cells: list[CellSeries] = []
    for name in _td_cell_names(td_export):
        record = td_export.get(name)
        if record is None:
            continue
        cells.append(
            CellSeries(
                cell=name,
                periods=tuple(record.periods),
                l_series=tuple(float(value) for value in record.l_series),
                net_series=tuple(
                    float(value) for value in getattr(record, "net_series", ()) or ()
                ),
                assessed=bool(record.assessed),
                reason=getattr(record, "reason", None),
            )
        )
    if not cells:
        raise SelectionStudyError(
            "the TD export carries no L-TD cell (neither L-TD nor the event "
            "K0/K1 pair); the selection study cannot cache it"
        )
    return CandidateSeries(
        factor_spec_hash=factor_spec_hash_value,
        cells=tuple(cells),
        declaration_hash=getattr(evaluation, "declaration_hash", None),
        diagnostics_hash=getattr(evaluation, "diagnostics_hash", None),
        not_assessed=bool(getattr(evaluation, "not_assessed", False)),
        not_assessed_reason=getattr(evaluation, "not_assessed_reason", None),
    )


@dataclass
class SeriesCache:
    """The on-disk per-spec series cache (one canonical JSON file per hash).

    A hit is loaded from disk and never re-evaluated; a miss goes through the
    injected ``evaluate`` builder exactly once and is written atomically.  Two
    processes never write the same file (each worker owns its own specs), and a
    file is either absent or complete.
    """

    directory: Path | str
    evaluate: Callable[[Any], Any] | None = None
    calls: list[str] = field(default_factory=list)
    hits: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)
        self.calls = list(self.calls or [])
        self.hits = list(self.hits or [])

    # -- paths -----------------------------------------------------------
    def path_for(self, factor_spec_hash_value: str) -> Path:
        key = str(factor_spec_hash_value)
        if not key or "/" in key or key.startswith("."):
            raise SelectionStudyError(f"refusing an unsafe cache key {key!r}")
        return self.directory / f"{key}.json"

    def has(self, factor_spec_hash_value: str) -> bool:
        return self.path_for(factor_spec_hash_value).is_file()

    # -- read / write ----------------------------------------------------
    def series_for_hash(self, factor_spec_hash_value: str) -> CandidateSeries:
        path = self.path_for(factor_spec_hash_value)
        if not path.is_file():
            raise SelectionStudyError(
                f"the series cache has no entry for {factor_spec_hash_value}; "
                "warm the cache before running the study"
            )
        series = CandidateSeries.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )
        if series.factor_spec_hash != factor_spec_hash_value:
            raise SelectionStudyError(
                f"cache file {path} holds {series.factor_spec_hash}, not "
                f"{factor_spec_hash_value}"
            )
        return series

    def store(self, series: CandidateSeries) -> Path:
        return _atomic_write_text(
            self.path_for(series.factor_spec_hash),
            _canonical_json(series.to_dict()) + "\n",
        )

    def get(
        self,
        spec: Any,
        evaluate: Callable[[Any], Any] | None = None,
    ) -> CandidateSeries:
        key = factor_spec_hash(spec)
        if self.has(key):
            self.hits.append(key)
            return self.series_for_hash(key)
        builder = evaluate if evaluate is not None else self.evaluate
        if builder is None:
            raise SelectionStudyError(
                f"the series cache misses {key} and no evaluator was injected; "
                "warm the cache first"
            )
        series = series_from_evaluation(key, builder(spec))
        self.store(series)
        self.calls.append(key)
        return series

    # -- audit -----------------------------------------------------------
    def metrics(self) -> dict[str, int]:
        return {"evaluator_calls": len(self.calls), "cache_hits": len(self.hits)}


# ---------------------------------------------------------------------------
# the run pool
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolCandidate:
    """One eligible proposal of an M1-e run."""

    proposal_id: str
    proposal_order: int
    factor_spec: FactorSpec
    factor_spec_hash: str


@dataclass(frozen=True)
class RunPool:
    """One M1-e run's eligible pool, rebuilt from its executor state."""

    directory: Path
    arm: str
    origin: str
    window_cut: date
    proposals: tuple[PoolCandidate, ...]
    statuses: tuple[tuple[str, str, str | None], ...] = ()
    expected_evaluated: int | None = None

    @property
    def n_eligible(self) -> int:
        return len(self.proposals)

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _pid, status, _reason in self.statuses:
            counts[status] = counts.get(status, 0) + 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.directory.name,
            "arm": self.arm,
            "origin": self.origin,
            "window_cut": self.window_cut.isoformat(),
            "n_eligible": self.n_eligible,
            "expected_evaluated": self.expected_evaluated,
            "status_counts": self.status_counts(),
            "proposals": [
                {
                    "proposal_id": item.proposal_id,
                    "proposal_order": item.proposal_order,
                    "factor_spec_hash": item.factor_spec_hash,
                    "factor_spec_id": item.factor_spec.id,
                }
                for item in self.proposals
            ],
        }


def _run_arm_origin(name: str) -> tuple[str, str]:
    """Split an M1-e run directory name ``<arm>_<origin>``."""

    parts = str(name).rsplit("_", 1)
    if len(parts) != 2:
        raise SelectionStudyError(
            f"cannot read an arm/origin pair from the run directory name {name!r}; "
            "the M1-e layout is <arm>_<origin>"
        )
    arm, origin = parts[0], parts[1]
    if arm not in ARMS:
        raise SelectionStudyError(f"unknown arm {arm!r} in the run name {name!r}")
    if origin not in ORIGINS:
        raise SelectionStudyError(f"unknown origin {origin!r} in the run name {name!r}")
    return arm, origin


def _proposal_spec(
    payload: Mapping[str, Any], *, state_path: Path, order: int
) -> FactorSpec:
    """The frozen FactorSpec of one persisted proposal (both JSON forms)."""

    if payload.get("kind") == "factor_spec" and isinstance(
        payload.get("spec"), Mapping
    ):
        payload = payload["spec"]
    elif payload.get("kind") is not None:
        raise SelectionStudyError(
            f"{state_path}: proposal #{order} carries a "
            f"{payload.get('kind')!r} proposed_factor_spec; the selection study "
            "needs a frozen FactorSpec"
        )
    try:
        return FactorSpec.from_dict(payload)
    except Exception as exc:  # noqa: BLE001 - fail closed with the context
        raise SelectionStudyError(
            f"{state_path}: proposal #{order} has an unreadable "
            f"proposed_factor_spec ({type(exc).__name__}: {exc})"
        ) from exc


def _state_status(
    proposal_id: str, state: Mapping[str, Any]
) -> tuple[str, str | None]:
    """The M1-e status this state records for one proposal, plus its reason.

    ``status`` is :data:`EVALUATED` exactly for the four persisted conjuncts
    documented in the module docstring (the fifth -- a VALID training rank
    statistic -- is recomputed by :func:`filter_valid_candidates`, review R3).
    Every other proposal is labelled with the refusal the state makes
    recoverable:

    * ``NOT_CERTIFIED`` -- the executor never certified the proposal;
    * ``NOT_EVALUATED`` -- certified, but no evaluation was recorded;
    * ``NEAR_DUPLICATE`` / ``FAMILY_CAP`` / ``SCHEMA_REJECTED`` /
      ``A2_DECLARATION_INVALID`` -- a pre-evaluation refusal whose reason code
      the evaluator persisted;
    * ``NOT_ASSESSED`` -- an evaluation the run labelled NOT_ASSESSED (its
      v2.5 diagnostics did not assess the cells).

    The one refusal kind the state does **not** carry is a FAMILY_CAP rejection
    whose recorded reason is the family name (the gate writes
    ``decision.family`` as the reason); it is therefore labelled
    ``NOT_ASSESSED`` with that reason preserved.  The label only affects
    reporting -- both are excluded from the pool either way.
    """

    certified = state.get("certified") or {}
    evaluated = state.get("evaluated") or {}
    if certified.get(proposal_id) is not True:
        return "NOT_CERTIFIED", None
    entry = evaluated.get(proposal_id)
    if entry is None:
        return "NOT_EVALUATED", None
    reason = entry.get("not_assessed_reason")
    if reason is not None:
        return _refusal_status(str(reason)), reason
    if not entry.get("assessed"):
        return "NOT_ASSESSED", reason
    return EVALUATED, None


#: A2 sidecar refusal reasons (``contract.validate_a2_entry``) plus the
#: evaluator's missing-sidecar code.
_A2_REASONS = (
    "no-A2-sidecar",
    "predicted_relation",
    "direction_check",
    "family-",
)


def _refusal_status(reason: str) -> str:
    """Map a persisted refusal reason to the run-level status it came from."""

    code = reason.split(":", 1)[0]
    if code == "NEAR_DUPLICATE":
        return "NEAR_DUPLICATE"
    if code == "FAMILY_CAP":
        return "FAMILY_CAP"
    if code in ("SCHEMA_REJECTED", "MALFORMED_RESPONSE"):
        # The C4 evaluator records the MALFORMED_RESPONSE reason for a
        # SCHEMA_REJECTED refusal; the run counts it as SCHEMA_REJECTED.
        return "SCHEMA_REJECTED"
    if any(code.startswith(prefix) for prefix in _A2_REASONS):
        return "A2_DECLARATION_INVALID"
    return "NOT_ASSESSED"


def load_run_pool(
    run_dir: Path | str, *, expected_evaluated: int | None = None
) -> RunPool:
    """Rebuild one M1-e run's eligible pool from its ``executor_state.json``.

    Read-only.  This is the *state-level* pool: certified, evaluated, assessed
    and with no refusal reason.  The fifth EVALUATED conjunct (a VALID training
    rank statistic) is applied afterwards by :func:`filter_valid_candidates`,
    because it needs the cached series (review R3).

    Raises :class:`SelectionStudyError` when the state cannot identify
    ``EVALUATED`` unambiguously (task stop rule 1): a missing/foreign schema, an
    unparsable run name, a proposal whose persisted spec hash does not match the
    recomputed one, or a ``certified``/``evaluated`` record whose shape is not
    the C4 one.

    ``expected_evaluated`` is only recorded on the returned pool for the report;
    the hard comparison against the M1-e count is made *after* the VALID filter,
    before any selection file is written (draft section 1; review R3).
    """

    directory = Path(run_dir)
    state_path = (
        directory / "executor_state.json" if directory.is_dir() else directory
    )
    if not state_path.is_file():
        raise SelectionStudyError(
            f"{state_path} does not exist; an M1-e run record is required"
        )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(state, Mapping):
        raise SelectionStudyError(f"{state_path} is not a JSON object")
    schema = state.get("schema")
    if schema != CAMPAIGN4_EXECUTOR_SCHEMA:
        raise SelectionStudyError(
            f"{state_path} declares schema {schema!r}, not the C4 write-ahead "
            f"schema {CAMPAIGN4_EXECUTOR_SCHEMA!r}; this is not an M1-e run"
        )
    for key in ("certified", "evaluated", "proposals"):
        if key not in state:
            raise SelectionStudyError(
                f"{state_path} has no {key!r} field; EVALUATED cannot be "
                "reconstructed from it"
            )

    arm, origin = _run_arm_origin(state_path.parent.name)
    window_cut = ORIGINS[origin]

    proposals: list[PoolCandidate] = []
    statuses: list[tuple[str, str, str | None]] = []
    for order, payload in enumerate(state["proposals"]):
        if not isinstance(payload, Mapping):
            raise SelectionStudyError(
                f"{state_path}: proposal #{order} is not a JSON object"
            )
        proposal_id = str(payload.get("proposal_id") or "")
        spec_payload = payload.get("proposed_factor_spec")
        if not proposal_id or not isinstance(spec_payload, Mapping):
            raise SelectionStudyError(
                f"{state_path}: proposal #{order} has no proposal_id/"
                "proposed_factor_spec"
            )
        spec = _proposal_spec(spec_payload, state_path=state_path, order=order)
        key = factor_spec_hash(spec)
        declared = payload.get("proposed_factor_spec_hash")
        if declared is not None and str(declared) != key:
            raise SelectionStudyError(
                f"{state_path}: proposal {proposal_id} declares spec hash "
                f"{declared}, but the persisted spec hashes to {key}"
            )
        status, refusal_reason = _state_status(proposal_id, state)
        statuses.append((proposal_id, status, refusal_reason))
        if status != EVALUATED:
            continue
        proposals.append(
            PoolCandidate(
                proposal_id=proposal_id,
                proposal_order=order,
                factor_spec=spec,
                factor_spec_hash=key,
            )
        )

    pool = RunPool(
        directory=state_path.parent,
        arm=arm,
        origin=origin,
        window_cut=window_cut,
        proposals=tuple(proposals),
        statuses=tuple(statuses),
        expected_evaluated=expected_evaluated,
    )
    return pool


# ---------------------------------------------------------------------------
# the training-window firewall + statistics
# ---------------------------------------------------------------------------


def assert_training_periods(
    periods: Sequence[Any], cut: date | str | datetime, *, what: str
) -> None:
    """Raise :class:`C4FirewallError` if any period is at/after the cut."""

    boundary = _as_date(cut)
    for period in periods:
        if _as_date(period) >= boundary:
            raise C4FirewallError(
                f"{what}: period {_as_date(period).isoformat()} is at/after the "
                f"training-window cut {boundary.isoformat()}"
            )


def _td_cells(series: SeriesLike) -> tuple[CellSeries, ...]:
    names = _td_cell_names(series)
    if not names:
        raise SelectionStudyError(
            f"{getattr(series, 'factor_spec_hash', '?')}: the cached series has "
            "no L-TD cell"
        )
    cells: list[CellSeries] = []
    for name in names:
        record = series.get(name)
        if record is None:  # pragma: no cover - _td_cell_names found it
            raise SelectionStudyError(f"missing TD cell {name!r}")
        cells.append(record)
    return tuple(cells)


def _training_basis(
    series: SeriesLike, cut: date | str | datetime
) -> tuple[tuple[CellSeries, tuple[date, ...], tuple[float, ...]], ...]:
    """Every TD scenario's training-only periods and values (firewall-checked)."""

    boundary = _as_date(cut)
    basis: list[tuple[CellSeries, tuple[date, ...], tuple[float, ...]]] = []
    for cell in _td_cells(series):
        periods, values = window_series(cell, boundary, TRAINING)
        assert_training_periods(
            periods,
            boundary,
            what=(
                f"{getattr(series, 'factor_spec_hash', '?')}:{cell.cell} "
                "training window"
            ),
        )
        if len(periods) != len(values):  # pragma: no cover - window_series guards
            raise SelectionStudyError(
                f"{cell.cell}: {len(periods)} periods but {len(values)} values"
            )
        basis.append((cell, tuple(periods), tuple(values)))
    return tuple(basis)


def _ewc_or_raise(
    periods: Sequence[date],
    values: Sequence[float],
    *,
    what: str,
    bound_alpha: float = DEFAULT_BOUND_ALPHA,
) -> float:
    t_stat, status, reason = _ranking_ewc(
        periods, values, bound_alpha=float(bound_alpha)
    )
    if status != VALID or t_stat is None:
        raise SelectionStudyError(
            f"{what}: the frozen EWC is {status} ({reason}); the statistic is "
            "not computable"
        )
    return float(t_stat)


def s0_stat(
    series: SeriesLike,
    cut: date | str | datetime,
    *,
    bound_alpha: float = DEFAULT_BOUND_ALPHA,
) -> float:
    """S0: the full-training-window rank statistic (min over TD scenarios).

    Identical to :func:`~smart_beta.campaign4.ranking.rank_statistic`'s
    ``value`` -- same windowing, same frozen EWC call, same aggregation -- so
    it reduces to it bitwise.  The training-window firewall re-asserts that no
    used period is at/after ``cut``.
    """

    boundary = _as_date(cut)
    results: list[tuple[str, float | None, str, str | None]] = []
    for cell, periods, values in _training_basis(series, boundary):
        if not cell.assessed:
            results.append((cell.cell, None, NOT_ASSESSED, cell.reason))
            continue
        if len(values) < MIN_EWC_PERIODS:
            results.append(
                (cell.cell, None, INSUFFICIENT_SAMPLE, "fewer-than-24-periods")
            )
            continue
        t_stat, status, reason = _ranking_ewc(
            periods, values, bound_alpha=float(bound_alpha)
        )
        results.append((cell.cell, t_stat, status, reason))
    status, value, reason = _ranking_aggregate(results)
    if status != VALID or value is None:
        raise SelectionStudyError(
            f"{getattr(series, 'factor_spec_hash', '?')}: the S0 statistic is "
            f"{status} ({reason})"
        )
    return float(value)


def s1_stat(
    series: SeriesLike,
    cut: date | str | datetime,
    last_n: int = S1_LAST_N,
    *,
    bound_alpha: float = DEFAULT_BOUND_ALPHA,
) -> float:
    """S1: the min over TD scenarios of the EWC t on the last ``last_n`` periods.

    With ``last_n`` equal to the training length this reduces to
    :func:`s0_stat` bitwise (the same periods, values, EWC call and min).
    """

    boundary = _as_date(cut)
    window = int(last_n)
    if window < MIN_EWC_PERIODS:
        raise SelectionStudyError(
            f"s1: last_n={window} is below the frozen EWC minimum "
            f"{MIN_EWC_PERIODS}; the statistic is not computable"
        )
    values_out: list[float] = []
    for cell, periods, values in _training_basis(series, boundary):
        if not cell.assessed:
            raise SelectionStudyError(
                f"{getattr(series, 'factor_spec_hash', '?')}:{cell.cell} is not "
                f"assessed ({cell.reason}); s1 cannot be computed"
            )
        if len(values) < window:
            raise SelectionStudyError(
                f"{getattr(series, 'factor_spec_hash', '?')}:{cell.cell} has "
                f"{len(values)} training periods, fewer than last_n={window}"
            )
        tail_periods = periods[-window:]
        tail_values = values[-window:]
        assert_training_periods(
            tail_periods,
            boundary,
            what=f"{getattr(series, 'factor_spec_hash', '?')}:{cell.cell} "
            f"last-{window} window",
        )
        values_out.append(
            _ewc_or_raise(
                tail_periods,
                tail_values,
                what=f"s1 {getattr(series, 'factor_spec_hash', '?')}:{cell.cell}",
                bound_alpha=bound_alpha,
            )
        )
    return min(values_out)


def halves_positive(
    series: SeriesLike,
    cut: date | str | datetime,
) -> bool:
    """S2's filter: the training mean is > 0 in both halves, in every scenario.

    The training periods split into the first and second half (30 -> 15 + 15,
    42 -> 21 + 21); an odd training count is an error.  The period means use the
    same per-period series the frozen path uses.
    """

    boundary = _as_date(cut)
    for cell, periods, values in _training_basis(series, boundary):
        if not cell.assessed:
            raise SelectionStudyError(
                f"{getattr(series, 'factor_spec_hash', '?')}:{cell.cell} is not "
                f"assessed ({cell.reason}); the S2 filter is undefined"
            )
        count = len(values)
        if count == 0:
            raise SelectionStudyError(f"{cell.cell}: the training window is empty")
        if count % 2:
            raise SelectionStudyError(
                f"{cell.cell}: the training window has an odd period count "
                f"({count}); the halves split is undefined"
            )
        half = count // 2
        first = values[:half]
        second = values[half:]
        if not first or not second:  # pragma: no cover - count == 0 guarded above
            raise SelectionStudyError(f"{cell.cell}: an empty half window")
        if sum(first) / len(first) <= 0.0:
            return False
        if sum(second) / len(second) <= 0.0:
            return False
    return True


def composite_series(top5: Sequence["RankedCandidate"]) -> CompositeSeries:
    """S3's composite: the equal-weight period-by-period mean of 5 argmin series.

    Each member contributes the TD scenario that attains its S0 value (a tie
    takes the first scenario in export order).  Fewer than :data:`S3_SIZE`
    members, a member without a scenario match, or members whose periods do not
    line up are errors.
    """

    members: list[RankedCandidate] = list(top5)
    if len(members) < S3_SIZE:
        raise SelectionStudyError(
            f"S3 needs {S3_SIZE} eligible candidates for the composite, got "
            f"{len(members)}"
        )
    members = members[:S3_SIZE]
    chosen: list[tuple[tuple[str, str], tuple[date, ...], tuple[float, ...]]] = []
    for item in members:
        stat = item.rank_stat
        best: tuple[str, float] | None = None
        for cell_name, value, status, _reason in stat.scenarios:
            if status != VALID or value is None:
                continue
            if best is None or float(value) < best[1]:
                best = (cell_name, float(value))
        if best is None:
            raise SelectionStudyError(
                f"{item.candidate.proposal_id}: the rank statistic carries no "
                "VALID scenario; S3 cannot pick its argmin series"
            )
        cell = item.series.get(best[0])
        if cell is None:
            raise SelectionStudyError(
                f"{item.candidate.proposal_id}: the argmin scenario {best[0]!r} "
                "is not in the cached series"
            )
        chosen.append(
            (
                (item.candidate.factor_spec_hash, best[0]),
                tuple(cell.periods),
                tuple(cell.values),
            )
        )

    reference = chosen[0][1]
    total = [0.0] * len(reference)
    for (_key, _cell), periods, values in chosen:
        if tuple(periods) != reference:
            raise SelectionStudyError(
                "S3 composite members do not share one period index; the "
                "equal-weight period-by-period mean is undefined"
            )
        if len(values) != len(reference):
            raise SelectionStudyError(
                "S3 composite member length differs from the shared period index"
            )
        for index, value in enumerate(values):
            total[index] += float(value)
    weight = float(len(chosen))
    return CompositeSeries(
        members=tuple(item for item, _periods, _values in chosen),
        periods=reference,
        values=tuple(value / weight for value in total),
    )


# ---------------------------------------------------------------------------
# the ranked pool + the rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankedCandidate:
    """One eligible candidate with every training-window quantity the rules use."""

    candidate: PoolCandidate
    series: CandidateSeries
    rank_stat: RankStatistic
    s0: float
    s1: float
    halves_positive: bool

    @property
    def order(self) -> int:
        return self.candidate.proposal_order


def filter_valid_candidates(
    pool: RunPool, cache: SeriesCache
) -> tuple[RunPool, tuple[RankedCandidate, ...], tuple[dict[str, Any], ...]]:
    """Apply EVALUATED's fifth conjunct: a VALID training rank statistic.

    Review R3 (the M1-e reality): NOT_ASSESSED is decided by the recomputed L-TD
    rank statistic, not by a state field, so a state-labelled ``EVALUATED``
    candidate whose statistic is ``NOT_ASSESSED``/``INSUFFICIENT_SAMPLE`` is
    *excluded* here and labelled ``NOT_ASSESSED`` with the statistic's own
    reason.  It never raises: only a genuine inconsistency (a cache miss, or an
    ``s0_stat`` that does not reproduce ``rank_statistic`` bitwise) does.

    Returns the filtered pool (its ``proposals`` are exactly the eligible
    candidates, its ``statuses`` carry the relabel), the ranked candidates in
    proposal order, and one exclusion record per dropped candidate.
    """

    kept: list[PoolCandidate] = []
    ranked: list[RankedCandidate] = []
    excluded: list[dict[str, Any]] = []
    relabel: dict[str, str | None] = {}
    for candidate in pool.proposals:
        series = cache.get(candidate.factor_spec)
        stat = rank_statistic(series, pool.window_cut)
        if stat.status != VALID or stat.value is None:
            relabel[candidate.proposal_id] = stat.reason
            excluded.append(
                {
                    "proposal_id": candidate.proposal_id,
                    "proposal_order": candidate.proposal_order,
                    "factor_spec_hash": candidate.factor_spec_hash,
                    "status": NOT_ASSESSED,
                    "reason": stat.reason,
                }
            )
            continue
        s0 = s0_stat(series, pool.window_cut)
        if s0 != float(stat.value):
            raise SelectionStudyError(
                f"{pool.directory.name}:{candidate.proposal_id}: s0_stat={s0!r} "
                f"is not bitwise equal to rank_statistic={stat.value!r}; the "
                "study cannot reproduce the M1-e rule"
            )
        ranked.append(
            RankedCandidate(
                candidate=candidate,
                series=series,
                rank_stat=stat,
                s0=s0,
                s1=s1_stat(series, pool.window_cut),
                halves_positive=halves_positive(series, pool.window_cut),
            )
        )
        kept.append(candidate)

    statuses = tuple(
        (proposal_id, NOT_ASSESSED, relabel[proposal_id])
        if proposal_id in relabel
        else (proposal_id, status, reason)
        for proposal_id, status, reason in pool.statuses
    )
    filtered = replace(pool, proposals=tuple(kept), statuses=statuses)
    return filtered, tuple(ranked), tuple(excluded)


@dataclass(frozen=True)
class RuleSelection:
    """One rule's selection on one run."""

    rule: str
    arm: str
    origin: str
    window_cut: date
    status: str
    pick: PoolCandidate | None = None
    training_value: float | None = None
    spec_hashes: tuple[str, ...] = ()
    proposal_ids: tuple[str, ...] = ()
    members: tuple[tuple[str, str], ...] = ()
    reason: str | None = None
    n_survivors: int | None = None

    @property
    def selected(self) -> bool:
        return self.status == SELECTED

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "campaign": "campaign4-m1s-selection",
            "rule": self.rule,
            "arm": self.arm,
            "origin": self.origin,
            "window_cut": self.window_cut.isoformat(),
            "status": self.status,
            "tie_break": "earlier-proposal",
            "training_value": self.training_value,
            "factor_spec_hashes": list(self.spec_hashes),
            "proposal_ids": list(self.proposal_ids),
            "members": [
                {"factor_spec_hash": key, "cell": cell} for key, cell in self.members
            ],
            "reason": self.reason,
        }
        if self.n_survivors is not None:
            payload["n_survivors"] = self.n_survivors
        if self.pick is not None:
            payload["pick"] = {
                "proposal_id": self.pick.proposal_id,
                "proposal_order": self.pick.proposal_order,
                "factor_spec_hash": self.pick.factor_spec_hash,
            }
        else:
            payload["pick"] = None
        return payload

    def file_payload(self) -> dict[str, Any]:
        """The selection file body: the payload plus its own canonical hash."""

        body = self.to_dict()
        return {"selection_sha256": _canonical_hash(body), **body}


def _ordered_candidates(ranked: Sequence[RankedCandidate]) -> list[RankedCandidate]:
    """Every candidate in proposal order."""

    return sorted(ranked, key=lambda item: item.order)


def _best(
    ranked: Sequence[RankedCandidate], value: Callable[[RankedCandidate], float]
) -> RankedCandidate:
    """The highest value; ties go to the earlier proposal."""

    best: RankedCandidate | None = None
    best_value = -math.inf
    for item in _ordered_candidates(ranked):
        current = float(value(item))
        if best is None or current > best_value:
            best = item
            best_value = current
    if best is None:  # pragma: no cover - guarded by the callers
        raise SelectionStudyError("no candidates to select from")
    return best


def select_rule(
    rule: str,
    ranked: Sequence[RankedCandidate],
    *,
    arm: str,
    origin: str,
    window_cut: date,
) -> RuleSelection:
    """Apply one pre-registered rule to one run's ranked pool."""

    if rule not in RULES:
        raise SelectionStudyError(f"unknown selection rule {rule!r}")

    def _pick(pick: RankedCandidate, value: float) -> RuleSelection:
        return RuleSelection(
            rule=rule,
            arm=arm,
            origin=origin,
            window_cut=window_cut,
            status=SELECTED,
            pick=pick.candidate,
            training_value=float(value),
            spec_hashes=(pick.candidate.factor_spec_hash,),
            proposal_ids=(pick.candidate.proposal_id,),
        )

    if rule == "S0":
        best = _best(ranked, lambda item: item.s0)
        return _pick(best, best.s0)

    if rule == "S1":
        best = _best(ranked, lambda item: item.s1)
        return _pick(best, best.s1)

    if rule == "S2":
        survivors = [item for item in _ordered_candidates(ranked) if item.halves_positive]
        if not survivors:
            return RuleSelection(
                rule=rule,
                arm=arm,
                origin=origin,
                window_cut=window_cut,
                status=NO_SELECTION,
                reason="empty-filter",
                n_survivors=0,
            )
        best = _best(survivors, lambda item: item.s0)
        return replace(_pick(best, best.s0), n_survivors=len(survivors))

    # S3
    top = [
        item
        for item in sorted(
            _ordered_candidates(ranked), key=lambda item: (-float(item.s0), item.order)
        )
    ]
    if len(top) < S3_SIZE:
        return RuleSelection(
            rule=rule,
            arm=arm,
            origin=origin,
            window_cut=window_cut,
            status=ERROR,
            reason=f"fewer-than-{S3_SIZE}-eligible",
            n_survivors=len(top),
        )
    try:
        composite = composite_series(top)
    except SelectionStudyError as exc:
        return RuleSelection(
            rule=rule,
            arm=arm,
            origin=origin,
            window_cut=window_cut,
            status=ERROR,
            reason=str(exc),
            n_survivors=len(top),
        )
    members = tuple(composite.members)
    training_value = s0_stat(composite, window_cut)
    return RuleSelection(
        rule=rule,
        arm=arm,
        origin=origin,
        window_cut=window_cut,
        status=SELECTED,
        pick=None,
        training_value=training_value,
        spec_hashes=tuple(key for key, _cell in members),
        proposal_ids=tuple(
            item.candidate.proposal_id for item in top[:S3_SIZE]
        ),
        members=members,
        n_survivors=len(top),
    )


# ---------------------------------------------------------------------------
# test statistics + the pool base rate
# ---------------------------------------------------------------------------


def _test_result(series: SeriesLike, cut: date, *, origin: str) -> dict[str, Any]:
    """The pre-declared test result of one series at one window.

    W1: the one-sided EWC t of the pick's series (min over TD scenarios for a
    candidate, the composite's own series for S3) plus the descriptive mean,
    sign, positive share and n.  W2: descriptive only -- no t (the window is
    shorter than the frozen EWC minimum, so the frozen path returns none).
    """

    stats = test_statistics(series, cut)
    window = "W1" if cut == ORIGINS["W1"] else "W2"
    if window != origin:
        raise SelectionStudyError(
            f"a {origin} run may only be read at its own window, got {window}"
        )
    payload: dict[str, Any] = {
        "window": window,
        "window_cut": cut.isoformat(),
        "status": stats.status,
        "test_statistic": "ewc_t" if window == "W1" else "mean",
        "ewc_t": stats.ewc_t if window == "W1" else None,
        "net_mean": stats.net_mean,
        "net_sign": stats.net_sign,
        "positive_share": stats.positive_share,
        "n": stats.n,
        "cells": [cell.to_dict() for cell in stats.cells],
    }
    if window == "W1":
        payload["descriptive_only"] = False
    else:
        payload["descriptive_only"] = True
        if stats.ewc_t is not None:  # pragma: no cover - 12 periods < MIN
            raise SelectionStudyError(
                "the W2 window yields an EWC t; the reading is descriptive only"
            )
    return payload


def _test_value(test: Mapping[str, Any], *, origin: str) -> float | None:
    if origin == "W1":
        return test.get("ewc_t")
    return test.get("net_mean")


def spearman_rank_correlation(
    left: Sequence[float], right: Sequence[float]
) -> float | None:
    """Spearman's rho with average ranks; ``None`` when it is undefined."""

    if len(left) != len(right):
        raise SelectionStudyError(
            f"spearman needs two equal-length samples, got {len(left)} and "
            f"{len(right)}"
        )
    if len(left) < 3:
        return None

    def _ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda index: values[index])
        ranks = [0.0] * len(values)
        position = 0
        while position < len(order):
            end = position
            while (
                end + 1 < len(order) and values[order[end + 1]] == values[order[position]]
            ):
                end += 1
            average = (position + end) / 2.0 + 1.0
            for slot in range(position, end + 1):
                ranks[order[slot]] = average
            position = end + 1
        return ranks

    ranks_left = _ranks(left)
    ranks_right = _ranks(right)
    count = float(len(left))
    mean_left = sum(ranks_left) / count
    mean_right = sum(ranks_right) / count
    numerator = sum(
        (a - mean_left) * (b - mean_right) for a, b in zip(ranks_left, ranks_right)
    )
    spread_left = math.sqrt(sum((a - mean_left) ** 2 for a in ranks_left))
    spread_right = math.sqrt(sum((b - mean_right) ** 2 for b in ranks_right))
    if spread_left == 0.0 or spread_right == 0.0:
        return None
    return float(numerator / (spread_left * spread_right))


def _base_rate(
    ranked: Sequence[RankedCandidate],
    tests: Mapping[str, Mapping[str, Any]],
    *,
    origin: str,
) -> dict[str, Any]:
    """The pool base rate of one run (draft section 3).

    For every eligible candidate, its test value: the min-scenario W1 EWC t, or
    the W2 test mean.  Reports the median, the share above zero, the maximum and
    the Spearman correlation between the S0 training value and the test value.
    """

    rows = [
        {
            "proposal_id": item.candidate.proposal_id,
            "proposal_order": item.order,
            "factor_spec_hash": item.candidate.factor_spec_hash,
            "training_value": float(item.s0),
            "test_value": _test_value(
                tests[item.candidate.factor_spec_hash], origin=origin
            ),
            "ewc_t": tests[item.candidate.factor_spec_hash].get("ewc_t"),
            "net_mean": tests[item.candidate.factor_spec_hash].get("net_mean"),
            "net_sign": tests[item.candidate.factor_spec_hash].get("net_sign"),
            "positive_share": tests[item.candidate.factor_spec_hash].get(
                "positive_share"
            ),
            "n": tests[item.candidate.factor_spec_hash].get("n"),
        }
        for item in _ordered_candidates(ranked)
    ]
    usable = [row for row in rows if row["test_value"] is not None]
    values = [float(row["test_value"]) for row in usable]
    payload: dict[str, Any] = {
        "statistic": "ewc_t" if origin == "W1" else "mean",
        "n": len(usable),
        "n_pool": len(rows),
        "median": None,
        "share_positive": None,
        "max": None,
        "spearman": None,
        "rows": rows,
    }
    if values:
        payload["median"] = float(statistics.median(values))
        payload["share_positive"] = float(
            sum(1 for value in values if value > 0.0) / len(values)
        )
        payload["max"] = float(max(values))
        correlation = spearman_rank_correlation(
            [float(row["training_value"]) for row in usable], values
        )
        payload["spearman"] = None if correlation is None else float(correlation)
    return payload


# ---------------------------------------------------------------------------
# the study
# ---------------------------------------------------------------------------


def _flag_promising(
    runs: Sequence[Mapping[str, Any]], rules: Sequence[str] = RULES
) -> dict[str, Any]:
    """PROMISING per rule: in both arms, W1 test t > 0 and W2 test mean > 0."""

    by_key = {(run["arm"], run["origin"]): run for run in runs}
    flags: dict[str, bool] = {}
    detail: dict[str, Any] = {}
    for rule in rules:
        per_arm: dict[str, Any] = {}
        for arm in ARMS:
            w1 = by_key.get((arm, "W1"))
            w2 = by_key.get((arm, "W2"))
            if w1 is None or w2 is None:
                per_arm[arm] = {"present": False}
                continue
            test_w1 = (w1["tests"].get(rule) or {}).get("ewc_t")
            test_w2 = (w2["tests"].get(rule) or {}).get("net_mean")
            per_arm[arm] = {
                "present": True,
                "w1_test_t": test_w1,
                "w2_test_mean": test_w2,
                "w1_positive": test_w1 is not None and float(test_w1) > 0.0,
                "w2_positive": test_w2 is not None and float(test_w2) > 0.0,
            }
        ok = all(
            item.get("present")
            and item.get("w1_positive")
            and item.get("w2_positive")
            for item in per_arm.values()
        )
        flags[rule] = bool(ok)
        detail[rule] = per_arm
    return {"promising": flags, "detail": detail}


def _flag_pool_limited(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """POOL-LIMITED per study (draft section 3, review R1).

    The frozen condition is the **W1** one, in both arms:

    * the pool's median W1 test EWC t is ``<= 0``; and
    * the Spearman correlation between the S0 training value and the W1 test
      t is ``<= 0``.

    Only the two ``*_W1`` pools are read.  The W2 base rates stay in the report
    as per-run descriptive values; they never enter this flag.  When a W1 run
    is absent the flag is ``None`` (undetermined) with the reason
    ``"W1-pool-missing"`` -- never ``False``.
    """

    by_key = {(run["arm"], run["origin"]): run for run in runs}
    detail: dict[str, Any] = {}
    missing: list[str] = []
    for arm in ARMS:
        run = by_key.get((arm, "W1"))
        if run is None:
            missing.append(f"{arm}_W1")
            continue
        rate = run["base_rate"]
        median = rate["median"]
        spearman = rate["spearman"]
        detail[run["run"]] = {
            "median": median,
            "spearman": spearman,
            "median_non_positive": median is not None and float(median) <= 0.0,
            "spearman_non_positive": spearman is not None and float(spearman) <= 0.0,
        }
    if missing:
        return {
            "pool_limited": None,
            "pool_limited_reason": "W1-pool-missing",
            "pool_limited_missing": missing,
            "pool_limited_detail": detail,
        }
    both = all(
        item["median_non_positive"] and item["spearman_non_positive"]
        for item in detail.values()
    )
    return {
        "pool_limited": bool(both),
        "pool_limited_reason": None,
        "pool_limited_missing": [],
        "pool_limited_detail": detail,
    }


def _selection_files_in_output(output_dir: Path) -> list[str]:
    return sorted(
        str(path.name) for path in output_dir.glob("selection_*.json")
    )


def _study_runs(
    *, run_root: Path, run_names: Sequence[str] | None
) -> list[tuple[str, Path]]:
    """The (name, directory) pairs of the M1-e runs to study."""

    if run_names:
        pairs: list[tuple[str, Path]] = []
        for name in run_names:
            directory = run_root / name
            if not (directory / "executor_state.json").is_file():
                raise SelectionStudyError(
                    f"the run {name!r} has no executor_state.json under {run_root}"
                )
            pairs.append((name, directory))
        return pairs
    discovered: list[tuple[str, Path]] = []
    for path in sorted(run_root.glob("*_*")):
        if (path / "executor_state.json").is_file():
            discovered.append((path.name, path))
    if not discovered:
        raise SelectionStudyError(f"no M1-e runs found under {run_root}")
    return discovered


@dataclass
class SelectionStudyDeps:
    """The injected inputs of one C4M1-3 invocation (no LLM, no new proposals)."""

    m1e_run_root: Path
    cache: SeriesCache
    output_dir: Path
    run_names: Sequence[str] | None = None
    m1e_reports: Sequence[Path] = ()
    expected_evaluated: Mapping[str, int] | None = None
    trial_accounting: Mapping[str, Any] | None = None


def _read_m1e_report(path: Path | str) -> tuple[Path, Mapping[str, Any]]:
    """One M1-e report document (a report directory or the JSON file itself)."""

    report_path = Path(path)
    if report_path.is_dir():
        report_path = report_path / "c4_walkforward_report.json"
    if not report_path.is_file():
        raise SelectionStudyError(f"the M1-e report {report_path} does not exist")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, Mapping) or not isinstance(report.get("runs"), list):
        raise SelectionStudyError(
            f"{report_path} carries no 'runs' list; it is not an M1-e report"
        )
    return report_path, report


def expected_counts_from_reports(
    paths: Sequence[Path | str],
    *,
    run_names: Sequence[str] | None = None,
) -> dict[str, int]:
    """The hard ``counts["EVALUATED"]`` of every run an M1-e report records.

    Review R3: ``--m1e-reports`` supplies the report whose EVALUATED count is
    the frozen hard check (draft section 1: 50 / 41 / 62 / 73).  It is read
    here -- before any selection file is written -- and handed to
    :func:`run_selection_study` as the expected size of the *VALID-filtered*
    eligible pool.
    """

    wanted = None if run_names is None else set(str(name) for name in run_names)
    counts: dict[str, int] = {}
    for path in paths:
        report_path, report = _read_m1e_report(path)
        for entry in report["runs"]:
            name = f"{entry.get('arm')}_{entry.get('origin')}"
            if wanted is not None and name not in wanted:
                continue
            value = (entry.get("counts") or {}).get("EVALUATED")
            if value is None:
                raise SelectionStudyError(
                    f"{report_path}: the run {name} records no "
                    "counts['EVALUATED']; the hard count check cannot be made"
                )
            counts[name] = int(value)
    return counts


def _read_m1e_reports(
    paths: Sequence[Path], runs: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Compare S0 (and ranks 1-5) against the supplied M1-e reports.

    Every comparison is exact: the same spec hash, the same floating-point
    training rank statistic and the same test statistics.  A ``None``
    expectation (the M1-e report recorded no selection) must match ``None``.
    """

    checks: list[dict[str, Any]] = []
    by_key = {(run["arm"], run["origin"]): run for run in runs}

    def _record(name: str, run: str, expected: Any, actual: Any) -> None:
        checks.append(
            {
                "check": name,
                "run": run,
                "expected": expected,
                "actual": actual,
                "ok": expected == actual,
            }
        )

    for path in paths:
        report_path, report = _read_m1e_report(path)
        for entry in report["runs"]:
            arm = str(entry.get("arm"))
            origin = str(entry.get("origin"))
            label = f"{arm}_{origin}"
            run = by_key.get((arm, origin))
            if run is None:
                continue
            counts = entry.get("counts") or {}
            _record(
                "evaluated_count",
                label,
                counts.get("EVALUATED"),
                run["pool"]["n_eligible"],
            )
            expectation = entry.get("selection")
            s0 = run["selections"]["S0"]
            s0_pick = s0.get("pick")
            s0_pick_hash = None if s0_pick is None else s0_pick.get("factor_spec_hash")
            if expectation is None:
                _record("s0_pick_hash", label, None, s0_pick_hash)
                continue
            _record(
                "s0_pick_hash", label, expectation.get("factor_spec_hash"), s0_pick_hash
            )
            _record(
                "s0_training_value", label, expectation.get("rank_stat"), s0["training_value"]
            )
            expected_top = entry.get("test_statistics_top5") or []
            actual_top = [
                {
                    "rank": item["rank"],
                    "proposal_id": item["proposal_id"],
                    "factor_spec_hash": item["factor_spec_hash"],
                    "training_rank_stat": item["s0"],
                }
                for item in run["ranked"][:5]
            ]
            expected_top_key = [
                {
                    "rank": item.get("rank"),
                    "proposal_id": item.get("proposal_id"),
                    "factor_spec_hash": item.get("factor_spec_hash"),
                    "training_rank_stat": item.get("training_rank_stat"),
                }
                for item in expected_top
            ]
            _record("ranked_1_5", label, expected_top_key, actual_top)
            for expected_row in expected_top:
                rank = expected_row.get("rank")
                actual = run["pool_tests"].get(expected_row.get("factor_spec_hash"))
                stats = expected_row.get("test_statistics") or {}
                if actual is None:
                    _record(f"rank_{rank}_test", label, stats, None)
                    continue
                _record(
                    f"rank_{rank}_test_t",
                    label,
                    stats.get("ewc_t") if origin == "W1" else stats.get("net_mean"),
                    actual.get("ewc_t") if origin == "W1" else actual.get("net_mean"),
                )
                _record(
                    f"rank_{rank}_positive_share",
                    label,
                    stats.get("positive_share"),
                    actual.get("positive_share"),
                )
                _record(f"rank_{rank}_n", label, stats.get("n"), actual.get("n"))

    failed = [item for item in checks if not item["ok"]]
    return {
        "status": "REPRODUCTION_FAILED" if failed else "REPRODUCED",
        "n_checks": len(checks),
        "n_failed": len(failed),
        "checks": checks,
    }


def run_selection_study(*, deps: SelectionStudyDeps) -> tuple[dict[str, Any], int]:
    """Run the study and write the report.  Returns ``(report, exit_code)``.

    Phase A computes every training-window quantity, applies the four rules and
    writes and hashes every ``selection_<rule>_<arm>_<origin>.json``.  Phase B
    (only after phase A finished for the whole study) reads the test windows and
    computes the pre-declared descriptive statistics, the pool base rate and the
    flags.

    Review R3: the pool is the *VALID-filtered* one, and the frozen
    EVALUATED-count hard check (the M1-e report's ``counts["EVALUATED"]``) is
    applied right after that filter -- before phase A writes any selection
    file.  A mismatch writes the report only, marked ``REPRODUCTION_FAILED``,
    and returns exit code 1.
    """

    run_root = Path(deps.m1e_run_root)
    output_dir = assert_outside_repository(deps.output_dir)
    assert_outside_repository(deps.cache.directory)
    assert_not_inside(deps.cache.directory, run_root, what="the series cache")
    assert_not_inside(output_dir, run_root, what="the output directory")
    output_dir.mkdir(parents=True, exist_ok=True)

    pairs = _study_runs(run_root=run_root, run_names=deps.run_names)

    # -- the expected EVALUATED counts (draft section 1; review R3) ---------
    # ``--m1e-reports`` names the report whose ``counts["EVALUATED"]`` is the
    # frozen hard check (50 / 41 / 62 / 73).  It is read now, so the comparison
    # can happen after the VALID filter and before any selection file exists.
    expected: dict[str, int] = {}
    if deps.m1e_reports:
        expected.update(
            expected_counts_from_reports(
                deps.m1e_reports,
                run_names=[name for name, _directory in pairs],
            )
        )
    for name, value in dict(deps.expected_evaluated or {}).items():
        expected[str(name)] = int(value)

    trial = dict(
        deps.trial_accounting
        or {
            "selection_trials": len(pairs) * len(RULES),
            "m1e_proposal_trials": 400,
            "total_recorded": len(pairs) * len(RULES) + 400,
            "note": "recorded only; this study makes no new proposals and no LLM calls",
        }
    )

    # -- phase A, part 1: pools + EVALUATED's fifth (VALID) conjunct --------
    runs: list[dict[str, Any]] = []
    pools: dict[str, RunPool] = {}
    ranked_by_run: dict[str, list[RankedCandidate]] = {}
    composites: dict[tuple[str, str], CompositeSeries] = {}
    for name, directory in pairs:
        pool = load_run_pool(directory, expected_evaluated=expected.get(name))
        pool, ranked, excluded = filter_valid_candidates(pool, deps.cache)
        ordered = sorted(
            ranked, key=lambda item: (-float(item.s0), item.candidate.proposal_order)
        )
        pools[name] = pool
        ranked_by_run[name] = list(ranked)
        runs.append(
            {
                "run": name,
                "arm": pool.arm,
                "origin": pool.origin,
                "window_cut": pool.window_cut.isoformat(),
                "pool": pool.to_dict(),
                "pool_excluded": list(excluded),
                "ranked": [
                    {
                        "rank": index + 1,
                        "proposal_id": item.candidate.proposal_id,
                        "factor_spec_hash": item.candidate.factor_spec_hash,
                        "proposal_order": item.candidate.proposal_order,
                        "s0": float(item.s0),
                        "s1": float(item.s1),
                        "halves_positive": bool(item.halves_positive),
                    }
                    for index, item in enumerate(ordered)
                ],
                "selections": {},
            }
        )

    # -- the hard EVALUATED-count check: after the VALID filter, before any
    #    selection file is written (draft section 1; review R3) --------------
    count_checks: list[dict[str, Any]] = [
        {
            "check": "evaluated_count",
            "run": name,
            "expected": int(expected[name]),
            "actual": pools[name].n_eligible,
            "ok": pools[name].n_eligible == int(expected[name]),
        }
        for name, _directory in pairs
        if name in expected
    ]
    failed_counts = [item for item in count_checks if not item["ok"]]
    if failed_counts:
        # A mismatch writes the report only: no selection file, no test read.
        body: dict[str, Any] = {
            "campaign": "campaign4-m1s-selection-study",
            "design": "docs/campaign4_m1s_selection_study_draft.md",
            "task": "worker_tasks/campaign4_m1/task-c4m1-3-selection-study.md",
            "rules": list(RULES),
            "window_cuts": {key: ORIGINS[key].isoformat() for key in ORIGINS},
            "s1_last_n": S1_LAST_N,
            "s3_size": S3_SIZE,
            "evaluated_definition": EVALUATED_DEFINITION,
            "runs": [
                {
                    "run": run["run"],
                    "arm": run["arm"],
                    "origin": run["origin"],
                    "window_cut": run["window_cut"],
                    "pool": run["pool"],
                    "pool_excluded": run["pool_excluded"],
                    "ranked": run["ranked"],
                    "selections": {},
                    "tests": {},
                    "base_rate": None,
                }
                for run in runs
            ],
            "flags": {
                "promising": {},
                "promising_detail": {},
                "pool_limited": None,
                "pool_limited_reason": "reproduction-failed",
                "pool_limited_basis": "W1 pools only (treatment_W1, control_W1)",
                "pool_limited_missing": [],
                "pool_limited_detail": {},
            },
            "trial_accounting": trial,
            "reproduction": {
                "status": "REPRODUCTION_FAILED",
                "reason": "evaluated-count-mismatch",
                "n_checks": len(count_checks),
                "n_failed": len(failed_counts),
                "checks": count_checks,
                "selection_files_written": False,
            },
            "selection_basis": dict(_SELECTION_BASIS),
            "outputs": [],
        }
        report = {"content_sha256": _canonical_hash(body), **body}
        _atomic_write_json(output_dir / "c4_selection_study_report.json", report)
        _atomic_write_text(
            output_dir / "c4_selection_study_report.md",
            render_report_markdown(report),
        )
        return report, 1

    # -- phase A, part 2: the rules + the selection files --------------------
    for run in runs:
        name = run["run"]
        pool = pools[name]
        ranked = ranked_by_run[name]
        ordered = sorted(
            ranked, key=lambda item: (-float(item.s0), item.candidate.proposal_order)
        )
        run_selections: dict[str, dict[str, Any]] = {}
        for rule in RULES:
            selection = select_rule(
                rule,
                ranked,
                arm=pool.arm,
                origin=pool.origin,
                window_cut=pool.window_cut,
            )
            payload = selection.file_payload()
            filename = f"selection_{rule}_{pool.arm}_{pool.origin}.json"
            write_selection_file(output_dir / filename, payload)
            run_selections[rule] = {
                **payload,
                "file": filename,
            }
            if rule == "S3" and selection.status == SELECTED:
                top = ordered[:S3_SIZE]
                composites[(pool.arm, pool.origin)] = composite_series(top)
        run["selections"] = run_selections

    # -- phase B: the test windows (only now) ------------------------------
    for run in runs:
        name = run["run"]
        pool = pools[name]
        ranked = ranked_by_run[name]
        cut = pool.window_cut
        origin = pool.origin
        pool_tests = {
            item.candidate.factor_spec_hash: _test_result(
                item.series, cut, origin=origin
            )
            for item in ranked
        }
        tests: dict[str, Any] = {}
        for rule in RULES:
            selection = run["selections"][rule]
            if selection["status"] != SELECTED:
                tests[rule] = None
                continue
            if rule == "S3":
                composite = composites.get((pool.arm, pool.origin))
                if composite is None:  # pragma: no cover - written in phase A
                    raise SelectionStudyError(
                        f"{name}: the S3 composite was not built in phase A"
                    )
                tests[rule] = _test_result(composite, cut, origin=origin)
                tests[rule]["composite"] = composite.to_dict()
            else:
                key = selection["pick"]["factor_spec_hash"]
                tests[rule] = pool_tests[key]
        run["tests"] = tests
        run["pool_tests"] = pool_tests
        run["base_rate"] = _base_rate(ranked, pool_tests, origin=origin)

    # -- flags, reproduction, trial accounting -----------------------------
    promising = _flag_promising(runs)
    limited = _flag_pool_limited(runs)
    reproduction = (
        _read_m1e_reports(deps.m1e_reports, runs)
        if deps.m1e_reports
        else {"status": "NOT_CHECKED", "n_checks": 0, "n_failed": 0, "checks": []}
    )

    body = {
        "campaign": "campaign4-m1s-selection-study",
        "design": "docs/campaign4_m1s_selection_study_draft.md",
        "task": "worker_tasks/campaign4_m1/task-c4m1-3-selection-study.md",
        "rules": list(RULES),
        "window_cuts": {name: ORIGINS[name].isoformat() for name in ORIGINS},
        "s1_last_n": S1_LAST_N,
        "s3_size": S3_SIZE,
        "evaluated_definition": EVALUATED_DEFINITION,
        "runs": [
            {
                "run": run["run"],
                "arm": run["arm"],
                "origin": run["origin"],
                "window_cut": run["window_cut"],
                "pool": run["pool"],
                "pool_excluded": run["pool_excluded"],
                "ranked": run["ranked"],
                "selections": run["selections"],
                "tests": run["tests"],
                "base_rate": run["base_rate"],
            }
            for run in runs
        ],
        "flags": {
            "promising": promising["promising"],
            "promising_detail": promising["detail"],
            "pool_limited": limited["pool_limited"],
            "pool_limited_reason": limited["pool_limited_reason"],
            "pool_limited_basis": "W1 pools only (treatment_W1, control_W1)",
            "pool_limited_missing": limited["pool_limited_missing"],
            "pool_limited_detail": limited["pool_limited_detail"],
        },
        "trial_accounting": trial,
        "reproduction": reproduction,
        "selection_basis": dict(_SELECTION_BASIS),
        "outputs": _selection_files_in_output(output_dir),
    }
    content_hash = _canonical_hash(body)
    report = {"content_sha256": content_hash, **body}
    _atomic_write_json(output_dir / "c4_selection_study_report.json", report)
    _atomic_write_text(
        output_dir / "c4_selection_study_report.md", render_report_markdown(report)
    )
    exit_code = 1 if reproduction["status"] == "REPRODUCTION_FAILED" else 0
    return report, exit_code


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def render_report_markdown(report: Mapping[str, Any]) -> str:
    """A deterministic human-readable rendering of the study report."""

    lines: list[str] = [
        "# Campaign-4 M1-s -- selection-rule study",
        "",
        f"- Content SHA-256: `{report['content_sha256']}`",
        f"- Rules: {', '.join(report['rules'])}",
        f"- S1 recency window: last {report['s1_last_n']} training periods",
        f"- S3 composite size: {report['s3_size']}",
        f"- Reproduction: **{report['reproduction']['status']}** "
        f"({report['reproduction']['n_checks']} checks, "
        f"{report['reproduction']['n_failed']} failed)",
    ]
    if report["reproduction"].get("reason"):
        lines.append(f"- Reproduction reason: `{report['reproduction']['reason']}`")
    lines += [
        "",
        f"PROMISING: "
        + ", ".join(
            f"{rule}={_fmt(flag)}"
            for rule, flag in sorted(report["flags"]["promising"].items())
        ),
        "",
        "POOL-LIMITED (W1 pools only): "
        f"**{_fmt(report['flags']['pool_limited'])}**"
        + (
            f" ({report['flags'].get('pool_limited_reason')})"
            if report["flags"].get("pool_limited_reason")
            else ""
        ),
        "",
    ]
    for run in report["runs"]:
        lines.append(
            f"## {run['arm']} x {run['origin']} (cut {run['window_cut']}, "
            f"{run['pool']['n_eligible']} eligible)"
        )
        lines.append("")
        selections = run.get("selections") or {}
        if not selections:
            lines.append(
                "- no selection files written: "
                f"`{report['reproduction'].get('reason') or 'reproduction failed'}`"
            )
            lines.append("")
            continue
        tests = run.get("tests") or {}
        lines.append("| rule | status | pick | training value | test |")
        lines.append("|---|---|---|---|---|")
        for rule in report["rules"]:
            selection = selections[rule]
            test = tests.get(rule)
            if selection["pick"] is not None:
                pick = (
                    f"`{selection['pick']['proposal_id'][:24]}` "
                    f"({selection['pick']['factor_spec_hash'][:8]})"
                )
            elif selection["status"] == "SELECTED":
                pick = f"composite of {len(selection['members'])} series"
            else:
                pick = f"-- ({selection.get('reason') or selection['status']})"
            if test is None:
                result = "n/a"
            elif test["window"] == "W1":
                result = (
                    f"t={_fmt(test['ewc_t'])}; mean={_fmt(test['net_mean'])}, "
                    f"sign={test['net_sign']}, pos={_fmt(test['positive_share'])}, "
                    f"n={test['n']}"
                )
            else:
                result = (
                    f"mean={_fmt(test['net_mean'])}, sign={test['net_sign']}, "
                    f"pos={_fmt(test['positive_share'])}, n={test['n']} "
                    "(descriptive only)"
                )
            lines.append(
                f"| {rule} | {selection['status']} | {pick} | "
                f"{_fmt(selection['training_value'])} | {result} |"
            )
        rate = run.get("base_rate")
        lines.append("")
        if rate is None:
            lines.append("- Pool base rate: not computed (no selection file written)")
            lines.append("")
            continue
        lines.append(
            f"- Pool base rate ({rate['statistic']}, n={rate['n']}): "
            f"median={_fmt(rate['median'])}, "
            f"share>0={_fmt(rate['share_positive'])}, "
            f"max={_fmt(rate['max'])}, "
            f"spearman(training, test)={_fmt(rate['spearman'])}"
        )
        lines.append("")
    lines.append("## Trial accounting")
    lines.append("")
    for key, value in sorted(report["trial_accounting"].items()):
        lines.append(f"- {key}: {value}")
    lines.append("")
    return "\n".join(lines)
