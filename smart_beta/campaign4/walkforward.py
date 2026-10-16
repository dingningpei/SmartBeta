"""Campaign-4 M1 -- the walk-forward harness (task C4M1-2, section 3).

Authority: ``worker_tasks/campaign4_m1/campaign4-m1-plan.md`` and
``worker_tasks/campaign4_m1/task-c4m1-2-walkforward-harness.md`` on top of the
``docs/campaign4_m1a_contract_calibration_draft.md`` decisions Q5--Q7.

Origins
-------

``W1`` trains on the IS formations strictly before ``2018-07-31`` (30) and
tests on the rest (24); ``W2`` trains before ``2019-07-31`` (42) and tests on
the rest (12).  The cut is a formation date.  :func:`assert_origin_counts`
fails closed on any other split of the 54 persisted IS formations.

Evaluation cache
----------------

Every certified, gate-admitted candidate is evaluated **once** on the full IS
fold: the v2.5 diagnostics (``build_pqv25_diagnostics``, ``d == -0.55``), the
base T0/TS export and the TD export.  :class:`EvaluationCache` keys those by
``factor_spec_hash`` and is shared across all runs of one invocation; it also
writes a JSON summary per hash under the run root for audit.

Selection precedes the test computation
---------------------------------------

For each run the m = 1 rule is applied first (the admitted candidate with
status ``VALID`` and the highest training-window rank statistic; ties go to
the earlier proposal), its ``selection_<arm>_<origin>.json`` is written and
hashed, and only then are the test-window statistics computed.

Nothing here reads a real dataset, opens a network path or writes the
Knowledge-PIT.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from smart_beta.campaign4.feedback import CONTROL_PROMPT_TEMPLATE_HASH, PROMPT_TEMPLATE_C4_HASH
from smart_beta.campaign4.generation import (
    ARMS,
    CAMPAIGN4_EXECUTOR_SCHEMA,
    DEFAULT_FAMILY_CAP,
    DEFAULT_MAX_LLM_COST_BUDGET,
    DEFAULT_MAX_LLM_TOKEN_BUDGET,
    DEFAULT_MAX_PROPOSAL_BUDGET,
    CONTROL,
    TREATMENT,
    CandidateEvaluation,
    GenerationEntry,
    GenerationRunResult,
    build_campaign4_policy,
    run_generation,
)
from smart_beta.campaign.campaign3_generation import GenerationJournal
from smart_beta.campaign4.ranking import VALID, test_statistics
from smart_beta.spec.factor_spec import factor_spec_hash

__all__ = [
    "W1_CUT",
    "W2_CUT",
    "ORIGINS",
    "EXPECTED_TRAIN_TEST",
    "EXPECTED_FORMATIONS",
    "WalkforwardError",
    "WalkforwardDeps",
    "EvaluationCache",
    "walkforward_split",
    "assert_origin_counts",
    "select_candidate",
    "run_walkforward",
]

#: The two frozen origins (calibration draft section 2).
W1_CUT = date(2018, 7, 31)
W2_CUT = date(2019, 7, 31)

ORIGINS: dict[str, date] = {"W1": W1_CUT, "W2": W2_CUT}

#: The frozen (train, test) formation counts.
EXPECTED_TRAIN_TEST: dict[str, tuple[int, int]] = {"W1": (30, 24), "W2": (42, 12)}

#: The number of persisted IS formations (calibration draft section 2).
EXPECTED_FORMATIONS = 54

#: Every C4 federation status the trial accounting reports.
_STATUSES = (
    "EVALUATED",
    "NOT_ASSESSED",
    "NOT_CERTIFIED",
    "NEAR_DUPLICATE",
    "FAMILY_CAP",
    "A2_DECLARATION_INVALID",
    "SCHEMA_REJECTED",
)


class WalkforwardError(RuntimeError):
    """The walk-forward contract is malformed (fail closed)."""


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return value


# ---------------------------------------------------------------------------
# origins
# ---------------------------------------------------------------------------


def walkforward_split(
    formations: Sequence[date | str | datetime], cut: date | str | datetime
) -> tuple[tuple[date, ...], tuple[date, ...]]:
    """Split the formations into (training ``< cut``, test ``>= cut``)."""

    boundary = _as_date(cut)
    dates = tuple(sorted(_as_date(item) for item in formations))
    training = tuple(item for item in dates if item < boundary)
    test = tuple(item for item in dates if item >= boundary)
    return training, test


def assert_origin_counts(
    formations: Sequence[date | str | datetime], origin: str
) -> tuple[tuple[date, ...], tuple[date, ...]]:
    """Fail closed unless the formation fold has the frozen origin split."""

    if origin not in ORIGINS:
        raise WalkforwardError(f"unknown origin {origin!r}; expected {sorted(ORIGINS)}")
    dates = tuple(sorted(_as_date(item) for item in formations))
    if len(dates) != EXPECTED_FORMATIONS:
        raise WalkforwardError(
            f"the IS fold carries {len(dates)} formations, expected "
            f"{EXPECTED_FORMATIONS}"
        )
    training, test = walkforward_split(dates, ORIGINS[origin])
    expected = EXPECTED_TRAIN_TEST[origin]
    if (len(training), len(test)) != expected:
        raise WalkforwardError(
            f"origin {origin} splits into {len(training)}/{len(test)} "
            f"training/test formations, expected {expected[0]}/{expected[1]}"
        )
    return training, test


# ---------------------------------------------------------------------------
# evaluation cache
# ---------------------------------------------------------------------------


@dataclass
class EvaluationCache:
    """The full-IS evaluation cache, keyed by ``factor_spec_hash``.

    The cache is shared across every run of one invocation.  ``run_root`` is
    optional; when supplied the cache writes a deterministic JSON summary per
    evaluated spec hash for audit (the evaluation itself is never reloaded from
    disk, because the v2.5 exports are not a persisted interchange format).
    """

    builder: Callable[[Any], CandidateEvaluation]
    run_root: Path | str | None = None
    _cache: dict[str, CandidateEvaluation] | None = None
    calls: list[str] | None = None

    def __post_init__(self) -> None:
        self._cache = {}
        self.calls = []
        if self.run_root is not None:
            self.run_root = Path(self.run_root)

    def get(self, spec: Any) -> CandidateEvaluation:
        key = factor_spec_hash(spec)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        evaluation = self.builder(spec)
        self._cache[key] = evaluation
        self.calls.append(key)
        self._persist(key, evaluation)
        return evaluation

    def lookup(self, factor_spec_hash_value: str) -> CandidateEvaluation | None:
        return self._cache.get(factor_spec_hash_value)

    def _persist(self, key: str, evaluation: CandidateEvaluation) -> None:
        if self.run_root is None:
            return
        directory = Path(self.run_root) / "evaluation_cache"
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "factor_spec_hash": key,
            "declaration_hash": evaluation.declaration_hash,
            "diagnostics_hash": evaluation.diagnostics_hash,
            "not_assessed": bool(evaluation.not_assessed),
            "not_assessed_reason": evaluation.not_assessed_reason,
        }
        path = directory / f"{key}.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def _ranked_valid(entries: Sequence[GenerationEntry]) -> list[GenerationEntry]:
    valid = [
        item
        for item in entries
        if item.rank_stat.status == VALID and item.rank_stat.value is not None
    ]
    return sorted(valid, key=lambda item: -float(item.rank_stat.value))


def select_candidate(
    entries: Sequence[GenerationEntry],
) -> GenerationEntry | None:
    """The m = 1 rule: the VALID candidate with the highest rank statistic.

    Ties go to the earlier proposal.  ``entries`` must already be in proposal
    order.
    """

    ranked = _ranked_valid(entries)
    if not ranked:
        return None
    best_value = float(ranked[0].rank_stat.value)
    for item in entries:
        if item.rank_stat.status != VALID or item.rank_stat.value is None:
            continue
        if float(item.rank_stat.value) == best_value:
            return item
    return ranked[0]


# ---------------------------------------------------------------------------
# report helpers
# ---------------------------------------------------------------------------


def _canonical_hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def write_selection_file(path: Path, payload: Mapping[str, Any]) -> Path:
    """Write ``selection_<arm>_<origin>.json`` and return its path.

    Kept as a module-level function so the ordering test can observe that the
    selection is written before any test statistic is computed.
    """

    _atomic_write_json(path, dict(payload))
    return path


def _assert_not_in_data_store(path: Path | str) -> None:
    resolved = Path(path).resolve()
    for parent in (resolved, *resolved.parents):
        if parent.name == "data_store":
            raise WalkforwardError(
                f"{resolved} is inside the repository data_store; C4 outputs "
                "and run roots must live outside it"
            )


# ---------------------------------------------------------------------------
# the harness
# ---------------------------------------------------------------------------


@dataclass
class WalkforwardDeps:
    """The injected inputs of one walk-forward invocation.

    ``evaluate`` is the full-IS builder (the production script wires the real
    v2.5 path; tests inject a synthetic one); ``panel_builder`` builds the
    candidate panel for the A4/A6 gate; ``make_llm(arm)`` returns the arm's
    rendered-prompt completer.
    """

    is_formations: tuple[date, ...]
    evaluate: Callable[[Any], CandidateEvaluation]
    panel_builder: Callable[[Any], Any]
    make_llm: Callable[[str], Callable[[str, str], Any]]
    driver_panels: Mapping[str, Any] | None = None
    price_table: Any | None = None


def _arm_template_hash(arm: str) -> str:
    if arm == TREATMENT:
        return PROMPT_TEMPLATE_C4_HASH
    if arm == CONTROL:
        return CONTROL_PROMPT_TEMPLATE_HASH
    raise WalkforwardError(f"unknown arm {arm!r}")


def _run_one(
    *,
    arm: str,
    origin: str,
    deps: WalkforwardDeps,
    cache: EvaluationCache,
    run_root: Path,
    output_dir: Path,
    seed: int,
    max_proposal_budget: int,
    max_llm_token_budget: int,
    max_llm_cost_budget: float,
    family_cap: int,
) -> dict[str, Any]:
    cut = ORIGINS[origin]
    policy = build_campaign4_policy(
        prompt_template_hash=_arm_template_hash(arm),
        seed=seed,
        max_proposal_budget=max_proposal_budget,
        max_llm_token_budget=max_llm_token_budget,
        max_llm_cost_budget=max_llm_cost_budget,
    )
    run_dir = run_root / f"{arm}_{origin}"
    result: GenerationRunResult = run_generation(
        arm=arm,
        policy=policy,
        window_cut=cut,
        llm=deps.make_llm(arm),
        evaluate=cache.get,
        panel_builder=deps.panel_builder,
        driver_panels=deps.driver_panels,
        price_table=deps.price_table,
        journal=GenerationJournal(run_dir),
        family_cap=family_cap,
    )

    ordered = list(result.entries)
    ranked = _ranked_valid(ordered)
    selection = select_candidate(ordered)

    top5 = ranked[:5]
    selection_payload: dict[str, Any] = {
        "arm": arm,
        "origin": origin,
        "window_cut": cut.isoformat(),
        "selection": None,
        "training_ranks_1_5": [
            {
                "rank": index + 1,
                "proposal_id": item.proposal_id,
                "factor_spec_hash": item.factor_spec_hash,
                "rank_stat": item.rank_stat.value,
                "status": item.status,
            }
            for index, item in enumerate(top5)
        ],
    }
    if selection is not None:
        selection_payload["selection"] = {
            "proposal_id": selection.proposal_id,
            "factor_spec_hash": selection.factor_spec_hash,
            "rank_stat": selection.rank_stat.value,
        }
    selection_payload["selection_sha256"] = _canonical_hash(selection_payload)
    write_selection_file(
        output_dir / f"selection_{arm}_{origin}.json", selection_payload
    )

    # Only now compute the test statistics, for training ranks 1-5.
    top5_test: list[dict[str, Any]] = []
    for index, item in enumerate(top5):
        evaluation = cache.lookup(item.factor_spec_hash)
        if evaluation is None:
            continue
        stats = test_statistics(evaluation.td_export, cut)
        top5_test.append(
            {
                "rank": index + 1,
                "proposal_id": item.proposal_id,
                "factor_spec_hash": item.factor_spec_hash,
                "training_rank_stat": item.rank_stat.value,
                "test_statistics": stats.to_dict(),
                "descriptive_only": stats.ewc_t is None,
            }
        )

    counts = {status: 0 for status in _STATUSES}
    for item in ordered:
        counts[item.status] = counts.get(item.status, 0) + 1

    return {
        "arm": arm,
        "origin": origin,
        "window_cut": cut.isoformat(),
        "selection": selection_payload["selection"],
        "selection_sha256": selection_payload["selection_sha256"],
        "test_statistics_top5": top5_test,
        "counts": counts,
        "n_entries": result.n_entries,
        "n_evaluated": result.n_evaluated,
        "n_budget_truncated": result.n_budget_truncated,
        "tokens_used": result.tokens_used,
        "cost_used": result.cost_used,
        "stop_reason": result.stop_reason,
    }


def run_walkforward(
    *,
    deps: WalkforwardDeps,
    run_root: Path | str,
    output_dir: Path | str,
    arms: Sequence[str] = ARMS,
    origins: Sequence[str] = tuple(ORIGINS),
    seed: int = 20261016,
    max_proposal_budget: int = DEFAULT_MAX_PROPOSAL_BUDGET,
    max_llm_token_budget: int = DEFAULT_MAX_LLM_TOKEN_BUDGET,
    max_llm_cost_budget: float = DEFAULT_MAX_LLM_COST_BUDGET,
    family_cap: int = DEFAULT_FAMILY_CAP,
) -> dict[str, Any]:
    """Run the four (arm x origin) C4 walk-forward runs and return the report.

    The evaluation cache is created once and shared across every run; the
    selection is written and hashed before any test statistic is computed.
    """

    run_root = Path(run_root)
    output_dir = Path(output_dir)
    _assert_not_in_data_store(run_root)
    _assert_not_in_data_store(output_dir)
    run_root.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    for origin in origins:
        assert_origin_counts(deps.is_formations, origin)

    cache = EvaluationCache(deps.evaluate, run_root=run_root)

    runs: list[dict[str, Any]] = []
    for origin in origins:
        for arm in arms:
            runs.append(
                _run_one(
                    arm=arm,
                    origin=origin,
                    deps=deps,
                    cache=cache,
                    run_root=run_root,
                    output_dir=output_dir,
                    seed=seed,
                    max_proposal_budget=max_proposal_budget,
                    max_llm_token_budget=max_llm_token_budget,
                    max_llm_cost_budget=max_llm_cost_budget,
                    family_cap=family_cap,
                )
            )

    # Per-arm status shares and the trial accounting total.
    arm_shares: dict[str, Any] = {}
    for arm in arms:
        arm_runs = [run for run in runs if run["arm"] == arm]
        charged = sum(run["n_entries"] for run in arm_runs)
        counts = {status: 0 for status in _STATUSES}
        for run in arm_runs:
            for status, value in run["counts"].items():
                counts[status] = counts.get(status, 0) + value
        tallied = sum(counts.values())
        arm_shares[arm] = {
            "n_entries": charged,
            "n_tallied": tallied,
            "counts": counts,
            "shares": {
                status: (counts[status] / tallied if tallied else 0.0)
                for status in _STATUSES
            },
        }

    trial_total = sum(run["n_entries"] for run in runs)
    body: dict[str, Any] = {
        "campaign": "campaign4-m1-walkforward",
        "origins": {name: ORIGINS[name].isoformat() for name in origins},
        "arms": list(arms),
        "runs": runs,
        "arm_shares": arm_shares,
        "trial_accounting": {
            "total": trial_total,
            "by_arm": {arm: arm_shares[arm]["n_entries"] for arm in arms},
            "n_evaluated_unique": len(cache.calls or []),
        },
        "schema": CAMPAIGN4_EXECUTOR_SCHEMA,
    }
    canonical = dict(body)
    content_hash = _canonical_hash(canonical)
    report = {"content_sha256": content_hash, **body}

    _atomic_write_json(output_dir / "c4_walkforward_report.json", report)
    (output_dir / "c4_walkforward_report.md").write_text(
        render_report_markdown(report), encoding="utf-8"
    )
    return report


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def render_report_markdown(report: Mapping[str, Any]) -> str:
    """A deterministic human-readable rendering of the walk-forward report."""

    lines: list[str] = [
        "# Campaign-4 M1 walk-forward report",
        "",
        f"- Content SHA-256: `{report['content_sha256']}`",
        f"- Trial accounting total: {report['trial_accounting']['total']}",
        f"- Unique full-IS evaluations: {report['trial_accounting']['n_evaluated_unique']}",
        "",
    ]
    for run in report["runs"]:
        selection = run["selection"]
        lines.append(
            f"## {run['arm']} x {run['origin']} (cut {run['window_cut']})"
        )
        if selection is None:
            lines.append("- Selection: none (no VALID candidate)")
        else:
            lines.append(
                "- Selection: `{pid}` rank_stat={stat} hash=`{h}`".format(
                    pid=selection["proposal_id"],
                    stat=_fmt(selection["rank_stat"]),
                    h=selection["factor_spec_hash"][:12],
                )
            )
        lines.append(
            "- Counts: "
            + ", ".join(
                f"{status}={value}" for status, value in sorted(run["counts"].items())
            )
        )
        lines.append(
            f"- Tokens: {run['tokens_used']}; cost: {_fmt(run['cost_used'])}; "
            f"stop: {run['stop_reason']}"
        )
        for row in run["test_statistics_top5"]:
            stats = row["test_statistics"]
            label = "descriptive only" if row["descriptive_only"] else f"ewc_t={_fmt(stats['ewc_t'])}"
            lines.append(
                f"  - rank {row['rank']} `{row['proposal_id'][:12]}`: {label}; "
                f"mean={_fmt(stats['net_mean'])}, sign={stats['net_sign']}, "
                f"pos_share={_fmt(stats['positive_share'])}, n={stats['n']}"
            )
        lines.append("")
    lines.append("## Per-arm status shares")
    lines.append("")
    for arm, info in sorted(report["arm_shares"].items()):
        shares = ", ".join(
            f"{status}={info['shares'][status]:.3f}"
            for status in _STATUSES
            if info["counts"].get(status)
        )
        lines.append(f"- {arm}: n={info['n_entries']}; {shares}")
    lines.append("")
    return "\n".join(lines)
