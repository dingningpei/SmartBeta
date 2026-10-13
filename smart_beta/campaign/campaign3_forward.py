"""C3-F -- the Campaign-3 forwarding step (Erratum P6-E6, implementing P5-E2).

Authority: the frozen ``docs/campaign3_p6_implementation_spec.md`` Erratum
P6-E6 and the frozen, separate ``docs/campaign3_p5_erratum_e2.md`` Erratum
P5-E2 (both APPROVED, binding; they supersede P5 D8/D10 and drop T-B).
P5-E2 was moved out of ``docs/campaign3_p5_precommitment.md`` (restored to
its bound content) into its own document; the FORWARDING record binds that
document's SHA-256 separately from the precommitment SHA-256.  Erratum P5-E2
item 2 replaces the D10 forwarding rule with:

    the forwarded hypothesis is **rank 1 of the D9 IS ranking table**
    (eligible = ASSESSED + CERTIFIED with an L envelope assessed in every
    scenario), with the positive control never eligible; no eligible
    candidate -> ``NO_RESERVE_USE``.

The D9 table is recomputed with the unchanged C3-S
:func:`smart_beta.campaign.campaign3_screen.rank_candidates` over the
persisted attempt-4 exports, and its ``content_hash`` **must equal** the
``ranking_table_hash`` recorded in the TB_SELECTION K-PIT record (seq 159 in
production).  A mismatch is a hard stop (:class:`ForwardingHashMismatch`).

The forward step owns exactly these steps (P6-E6):

1. rebuild the generation snapshot's ``ScreeningCandidate`` objects through
   the launcher cold path, using **cached exports only** -- if any candidate's
   ``pqv2_series`` export is missing from the screen cache the step stops with
   :class:`ForwardingError`; it never rebuilds one;
2. call the unchanged ``rank_candidates``;
3. verify the ranking table hash against the TB_SELECTION record;
4. compute the forwarded id (rank 1 eligible) or ``NO_RESERVE_USE``;
5. append one ``FORWARDING`` ``HUMAN_DECISION`` record (schema
   ``campaign3-forwarding-v1``), idempotently, bound to the **active** CAMPAIGN
   record, to the TB_SELECTION record, to the P5 precommitment document
   SHA-256 and to the separate P5-E2 document SHA-256;
6. write the JSON + Markdown report.

There is **no T-C computation**, **no ACCESS record** and **no reserve path**
anywhere in this module.  The report carries the full IS ranking table (IS is
the generator-visible tier; descriptive), the forwarded id or
``NO_RESERVE_USE``, the T-B infeasibility disclosure, the P5-E2 power statement
verbatim, the attempts 1-3 RUN_INCOMPLETE note and the D12 break-even
unavailable note.  ``--dry-run`` computes and prints the decision and the hash
verification without writing the K-PIT record or the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smart_beta.campaign import campaign3_launch as _launch
from smart_beta.campaign import campaign3_run as _run
from smart_beta.campaign import campaign3_screen as _screen
from smart_beta.science.contracts import content_hash
from smart_beta.science.knowledge import (
    Channel,
    KnowledgeLog,
    KnowledgeRecord,
    RecordKind,
)

__all__ = [
    # frozen literals
    "FORWARDING_SCHEMA",
    "FORWARDING_RECORD_TYPE",
    "FORWARDING_REPORT_SCHEMA",
    "DECISION_RULE",
    "NO_RESERVE_USE",
    "FORWARD",
    "REPORT_DIRNAME",
    "P5_PATH",
    "P5_E2_PATH",
    "P5_E2_DOCUMENT",
    "FORWARD_MODULE_PATH",
    "P5_E2_POWER_STATEMENT",
    "TB_INFEASIBILITY_DISCLOSURE",
    "ATTEMPTS_1_3_DISCLOSURE",
    "D12_UNAVAILABLE_DISCLOSURE",
    # errors
    "ForwardingError",
    "ForwardingHashMismatch",
    "ForwardingGovernanceConflict",
    "ForwardingConfigurationError",
    # value objects
    "ForwardingReport",
    "ForwardingResult",
    "ForwardingInputs",
    # functions
    "forward",
    "run_forward",
    "write_report",
    "render_markdown",
    "main",
]

# ---------------------------------------------------------------------------
# frozen literals
# ---------------------------------------------------------------------------

#: The P6-E6 FORWARDING K-PIT record schema.
FORWARDING_SCHEMA = "campaign3-forwarding-v1"

#: The ``record_type`` tag carried inside the forwarding payload.
FORWARDING_RECORD_TYPE = "FORWARDING"

#: The forwarding report schema.
FORWARDING_REPORT_SCHEMA = "campaign3-forwarding-report-v1"

#: Erratum P5-E2 item 2: the decision rule, recorded in K-PIT.  It cites the
#: separate P5-E2 document that the record and the report bind by SHA-256.
DECISION_RULE = (
    "P5-E2 (docs/campaign3_p5_erratum_e2.md): rank-1 of D9 IS ranking"
)

#: The literal forwarded id used when no candidate is eligible.
NO_RESERVE_USE = "NO_RESERVE_USE"

#: The positive forwarding decision.
FORWARD = "FORWARD"

#: The run-directory subdirectory the forwarding report is written to.
REPORT_DIRNAME = "forwarding"

#: The frozen P5 precommitment document (restored to its bound content).
P5_PATH = _run.PRECOMMITMENT_PATH

#: The separate P5-E2 amendment document (P5-E2 was moved out of the
#: precommitment file, which was restored to its bound content).
P5_E2_PATH = P5_PATH.parent / "campaign3_p5_erratum_e2.md"

#: The repo-relative P5-E2 document reference recorded in the record/report.
P5_E2_DOCUMENT = "docs/campaign3_p5_erratum_e2.md"

#: This module's own source file (its SHA-256 is bound into the record).
FORWARD_MODULE_PATH = Path(__file__).resolve()

#: Erratum P5-E2 item 5 (the power statement restated), reproduced verbatim.
P5_E2_POWER_STATEMENT = (
    "for a true annual SR of 1.0 the T-C power is about 0.50, given that the "
    "forwarded candidate is that factor (§2). IS selection is search-inflated, "
    "so the probability that the IS top-1 is a true strong factor is unknown "
    "and plausibly low."
)

#: Erratum P5-E2 item 1/6: the T-B infeasibility disclosure.
TB_INFEASIBILITY_DISCLOSURE = (
    "T-B is infeasible and was therefore dropped (P5-E2): the frozen "
    "partition's DEVELOPMENT_SEALED fold is the D7b final-holdout fold, so "
    "every holdout-fold T-B cell returns NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP "
    "with no values computed; OOS alone has 18 periods, below the admitted "
    "EWC_T23_ONE_SIDED minimum T >= 24, so every OOS-only test is INVALID. "
    "The partial OOS T-B diagnostics are not used for any decision."
)

#: Erratum P5-E2 item 6: the attempts 1-3 RUN_INCOMPLETE disclosure.
ATTEMPTS_1_3_DISCLOSURE = (
    "Attempts 1-3 are RUN_INCOMPLETE and are disclosed here: attempt 1 "
    "stopped on a P-Q realization invariant, attempt 2 on the "
    "EVENT_SIMPLE grid mismatch and attempt 3 on an early generation stop. "
    "No candidate from attempts 1-3 enters ranking, T-B or T-C, and none is "
    "used by this forwarding decision."
)

#: Erratum P5-E2 item 6: the D12 break-even unavailable disclosure.
D12_UNAVAILABLE_DISCLOSURE = (
    "The D12 break-even cost requires a valid T-B result; without T-B it is "
    "unavailable and is reported as such. It is not a gate."
)

#: The credential environment variables scrubbed from any error text.
_CREDENTIAL_ENV_VARS: tuple[str, ...] = (
    "TIINGO_API_KEY",
    "TUSHARE_PROXY_TOKEN",
    "TUSHARE_BASIC_PROXY_TOKEN",
    "TUSHARE_API_TOKEN",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "SEC_CONTACT",
)


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class ForwardingError(RuntimeError):
    """Base class for every C3-F fail-closed error."""


class ForwardingHashMismatch(ForwardingError):
    """The recomputed ranking hash differs from the TB_SELECTION record."""


class ForwardingGovernanceConflict(ForwardingError):
    """An existing FORWARDING record contradicts the one being written."""


class ForwardingConfigurationError(ForwardingError):
    """The caller supplied an inconsistent forwarding configuration."""


# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForwardingInputs:
    """The verified inputs the decision consumes (also the report body)."""

    campaign_record_hash: str
    tb_selection_record_hash: str
    tb_selection_ranking_table_hash: str
    run_dir: str
    p5_sha256: str
    p5_e2_sha256: str
    forward_module_sha256: str


@dataclass(frozen=True)
class ForwardingReport:
    """The C3-F report payload (JSON + Markdown)."""

    inputs: ForwardingInputs
    ranking: _screen.RankingTable
    forwarded_id: str
    decision: str
    dry_run: bool = False
    forwarding_record_hash: str | None = None

    @property
    def ranking_table_hash(self) -> str:
        return self.ranking.content_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": FORWARDING_REPORT_SCHEMA,
            "campaign_record_hash": self.inputs.campaign_record_hash,
            "tb_selection_record_hash": (
                self.inputs.tb_selection_record_hash
            ),
            "ranking_table_hash": self.ranking_table_hash,
            "tb_selection_ranking_table_hash": (
                self.inputs.tb_selection_ranking_table_hash
            ),
            "hash_verified": (
                self.ranking_table_hash
                == self.inputs.tb_selection_ranking_table_hash
            ),
            "forwarded_id": self.forwarded_id,
            "decision": self.decision,
            "decision_rule": DECISION_RULE,
            "p5_sha256": self.inputs.p5_sha256,
            "p5_e2_sha256": self.inputs.p5_e2_sha256,
            "p5_e2_document": P5_E2_DOCUMENT,
            "forward_module_sha256": self.inputs.forward_module_sha256,
            "dry_run": bool(self.dry_run),
            "forwarding_record_hash": self.forwarding_record_hash,
            "ranking": self.ranking.to_content(),
            "p5_e2_power_statement": P5_E2_POWER_STATEMENT,
            "tb_infeasibility": TB_INFEASIBILITY_DISCLOSURE,
            "attempts_1_3": ATTEMPTS_1_3_DISCLOSURE,
            "d12_break_even": D12_UNAVAILABLE_DISCLOSURE,
        }


@dataclass(frozen=True)
class ForwardingResult:
    """The deterministic result of one forwarding evaluation."""

    forwarded_id: str
    decision: str
    ranking_table_hash: str
    tb_selection_record_hash: str
    campaign_record_hash: str
    dry_run: bool
    report: ForwardingReport
    report_paths: tuple[Path, Path] | None = None
    forwarding_record_hash: str | None = None
    kpit_appended: bool = False


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _forward_module_sha256(path: Path | str = FORWARD_MODULE_PATH) -> str:
    return _sha256_file(path)


def _p5_sha256(path: Path | str = P5_PATH) -> str:
    return _sha256_file(path)


def _p5_e2_sha256(path: Path | str = P5_E2_PATH) -> str:
    """SHA-256 of the separate P5-E2 document; fail closed if it is missing.

    The FORWARDING record binds this document's SHA-256 (P6-E6), so a missing
    document is a hard stop -- never a silent skip and never an empty digest.
    """
    candidate = Path(path)
    if not candidate.is_file():
        raise ForwardingError(
            "the P5-E2 document is missing at "
            f"{candidate}; the forwarding record binds its SHA-256 "
            "(P6-E6), so forwarding is refused"
        )
    return _sha256_file(candidate)


def _refuse_reserve_boundary(target: Path | str) -> None:
    """Fail closed if ``target`` names the sealed reserve or the store.

    The forwarding step reads only the run directory and the canonical K-PIT;
    it must never resolve a ``reserve_sealed`` path.  This is the open-audit
    anchor mutation F-M4 edits.
    """
    text = str(target)
    if "reserve_sealed" in text:
        raise ForwardingError(
            f"the forwarding step refuses a reserve path: {text}"
        )


def _resolve_run_dir(run_root: Path | str, campaign_record_hash: str) -> Path:
    """The attempt-4 run directory bound to the active CAMPAIGN record.

    ``run_root`` may be either the run directory itself or the campaign3 run
    root holding one subdirectory per attempt.  Exactly one candidate carrying
    a ``generation.json`` bound to ``campaign_record_hash`` is accepted; none
    or several is a hard stop (never a silent pick).
    """
    root = Path(run_root)
    _refuse_reserve_boundary(root)
    if not root.exists():
        raise ForwardingError(f"the run root {root} does not exist")
    candidates: list[Path] = []
    if (root / "generation.json").is_file():
        candidates.append(root)
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "generation.json").is_file():
                candidates.append(child)
    matching: list[Path] = []
    for candidate in candidates:
        payload = json.loads(
            (candidate / "generation.json").read_text(encoding="utf-8")
        )
        if not isinstance(payload, Mapping):
            raise ForwardingError(
                f"the persisted generation state in {candidate} is malformed"
            )
        if payload.get("campaign_record_hash") == campaign_record_hash:
            matching.append(candidate)
    if not matching:
        raise ForwardingError(
            "no run directory is bound to the active CAMPAIGN record "
            f"{campaign_record_hash}; refusing to guess"
        )
    if len(matching) != 1:
        raise ForwardingError(
            "several run directories are bound to the active CAMPAIGN record "
            f"{campaign_record_hash}; refusing to pick one"
        )
    return matching[0]


def _load_generation(run_dir: Path) -> _run.GenerationSnapshot:
    path = run_dir / "generation.json"
    if not path.is_file():
        raise ForwardingError(f"the generation state {path} is missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ForwardingError(f"the generation state {path} is malformed")
    return _run.GenerationSnapshot.from_content(payload)


def _load_screen_export(cache_dir: Path | str, digest: str) -> Any:
    """Load one cached export; a miss is a hard stop (never a rebuild)."""
    series = _launch._load_screen_export(cache_dir, digest)
    if series is None:
        raise ForwardingError(
            "the screen export cache is missing the export for "
            f"diagnostics_hash {digest!r}; the forwarding step never rebuilds "
            "an export (P6-E6)"
        )
    return series


def _build_candidates_cached(
    assembly: Any, generation: _run.GenerationSnapshot
) -> tuple[tuple[Any, ...], Any]:
    """Rebuild the C3-S candidates through the launcher cold path.

    Every candidate's ``pqv2_series`` export must already be in the run's
    screen cache; a missing one stops the step before the launcher builder is
    ever called, so an export is never rebuilt (P6-E6 step 1).  The run
    directory's ``diagnostics``/``screen_cache`` directories are bound onto the
    assembly from the persisted generation snapshot, so the cold path resolves
    exactly the attempt's own artifacts.
    """
    diagnostics_dir = getattr(generation, "diagnostics_dir", None)
    if not diagnostics_dir:
        raise ForwardingError(
            "the generation snapshot carries no persisted diagnostics directory"
        )
    diagnostics_path = Path(diagnostics_dir)
    _refuse_reserve_boundary(diagnostics_path)
    if hasattr(assembly, "diagnostics_dir"):
        assembly.diagnostics_dir = diagnostics_path
    cache_dir = diagnostics_path.parent / _launch.SCREEN_CACHE_DIRNAME
    if hasattr(assembly, "screen_cache_dir"):
        assembly.screen_cache_dir = cache_dir
    for record in generation.candidates:
        digest = record.get("diagnostics_hash") if isinstance(record, Mapping) else None
        if not isinstance(digest, str) or not digest:
            raise ForwardingError(
                "a generation candidate carries no diagnostics_hash; the "
                "forwarding step cannot verify its cached export"
            )
        _load_screen_export(cache_dir, digest)
    builder = getattr(assembly, "screen_candidate_builder", None)
    if builder is None:
        raise ForwardingConfigurationError(
            "the assembly exposes no screen_candidate_builder"
        )
    candidates, control = builder(generation)
    for candidate in candidates:
        if getattr(candidate, "series_export", None) is None:
            raise ForwardingError(
                f"candidate {getattr(candidate, 'hypothesis_id', '?')!r} has no "
                "cached series export; the forwarding step never rebuilds one"
            )
    return tuple(candidates), control


def _default_rank(
    candidates: Sequence[Any], assembly: Any
) -> _screen.RankingTable:
    registry = getattr(assembly, "registry", None)
    if registry is None:
        raise ForwardingConfigurationError(
            "the assembly exposes no inference registry for the D9 ranking"
        )
    procedure_ref = getattr(assembly, "procedure_ref", None)
    return _screen.rank_candidates(
        candidates,
        registry=registry,
        admission_records=getattr(assembly, "admission_records", ()),
        procedure_ref=(
            dict(procedure_ref)
            if procedure_ref is not None
            else _screen.ADMITTED_PROCEDURE_REF
        ),
    )


def _forwarded_id(ranking: _screen.RankingTable) -> str:
    """Erratum P5-E2 item 2: rank 1 of the D9 table, or NO_RESERVE_USE."""
    return ranking.selected[0] if ranking.selected else NO_RESERVE_USE


def _assert_forwardable(forwarded_id: str, control_id: str) -> None:
    """D11: the positive control is a pipeline check, never forwarded."""
    if control_id and forwarded_id == control_id:
        raise ForwardingError(
            "the positive control must never be forwarded (D11)"
        )


# ---------------------------------------------------------------------------
# FORWARDING K-PIT record (idempotent, schema- and CAMPAIGN-scoped)
# ---------------------------------------------------------------------------


def _is_campaign3_forwarding_record(
    record: KnowledgeRecord,
    campaign_record: KnowledgeRecord | None = None,
) -> bool:
    """True only for the active campaign's FORWARDING HUMAN decision."""
    if record.kind is not RecordKind.HUMAN_DECISION:
        return False
    forwarding = record.payload.get("forwarding")
    if not isinstance(forwarding, Mapping):
        return False
    if forwarding.get("schema") != FORWARDING_SCHEMA:
        return False
    if campaign_record is not None:
        bound = forwarding.get("campaign_record_hash")
        if bound is not None and bound != campaign_record.record_hash:
            return False
    return True


def _existing_forwarding_record(
    log: KnowledgeLog,
    campaign_record: KnowledgeRecord | None = None,
) -> KnowledgeRecord | None:
    for record in log.read():
        if _is_campaign3_forwarding_record(record, campaign_record):
            return record
    return None


def _forwarding_payload(
    *,
    campaign_record_hash: str,
    tb_selection_record_hash: str,
    ranking_table_hash: str,
    forwarded_id: str,
    p5_sha256: str,
    p5_e2_sha256: str,
    forward_module_sha256: str,
) -> dict[str, Any]:
    return {
        "decision_kind": "OTHER",
        "actor_role": "OPERATOR",
        "consulted_all_prior": True,
        "forwarding": {
            "schema": FORWARDING_SCHEMA,
            "record_type": FORWARDING_RECORD_TYPE,
            "campaign_record_hash": campaign_record_hash,
            "tb_selection_record_hash": tb_selection_record_hash,
            "ranking_table_hash": ranking_table_hash,
            "forwarded_id": forwarded_id,
            "p5_sha256": p5_sha256,
            "p5_e2_sha256": p5_e2_sha256,
            "p5_e2_document": P5_E2_DOCUMENT,
            "forward_module_sha256": forward_module_sha256,
            "decision_rule": DECISION_RULE,
        },
    }


def _write_forwarding_record(
    log: KnowledgeLog,
    *,
    campaign_record: KnowledgeRecord,
    tb_selection_record: KnowledgeRecord,
    payload: Mapping[str, Any],
) -> tuple[KnowledgeRecord, bool]:
    """Append the FORWARDING record idempotently; return (record, appended)."""
    existing = _existing_forwarding_record(log, campaign_record)
    if existing is not None:
        if _run._jsonable(dict(existing.payload)) != _run._jsonable(
            dict(payload)
        ):
            raise ForwardingGovernanceConflict(
                "an existing FORWARDING record for the active CAMPAIGN record "
                "has different content"
            )
        return existing, False
    record = log.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        program_id="campaign3",
        payload=dict(payload),
        refs={
            "influenced_by": (
                campaign_record.record_hash,
                tb_selection_record.record_hash,
            )
        },
    )
    return record, True


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _fmt(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}"


def render_markdown(report: ForwardingReport) -> str:
    """Render the C3-F report as Markdown."""
    lines: list[str] = []
    lines.append("# Campaign-3 P6 C3-F forwarding report")
    lines.append("")
    lines.append(f"- decision: **{report.decision}**")
    lines.append(f"- forwarded id: `{report.forwarded_id}`")
    lines.append(f"- decision rule: {DECISION_RULE}")
    lines.append(f"- ranking table hash: `{report.ranking_table_hash}`")
    lines.append(
        "- TB_SELECTION ranking table hash: "
        f"`{report.inputs.tb_selection_ranking_table_hash}`"
    )
    lines.append(
        "- hash verified: "
        f"**{report.ranking_table_hash == report.inputs.tb_selection_ranking_table_hash}**"
    )
    lines.append(f"- P5 precommitment SHA-256: `{report.inputs.p5_sha256}`")
    lines.append(f"- P5-E2 document: `{P5_E2_DOCUMENT}`")
    lines.append(f"- P5-E2 document SHA-256: `{report.inputs.p5_e2_sha256}`")
    lines.append("")
    lines.append("## IS ranking (D9, descriptive; IS is the generator-visible tier)")
    lines.append("")
    lines.append("| rank | hypothesis_id | eligible | min L t | sign | selected |")
    lines.append("|---|---|---|---|---|---|")
    rank = 0
    for row in report.ranking.rows:
        if row.eligible:
            rank += 1
            rank_label = str(rank)
        else:
            rank_label = "-"
        lines.append(
            f"| {rank_label} | {row.hypothesis_id} | {row.eligible} | "
            f"{_fmt(row.min_t)} | {row.sign} | {row.selected} |"
        )
    lines.append("")
    lines.append("## T-B infeasibility (P5-E2)")
    lines.append("")
    lines.append(TB_INFEASIBILITY_DISCLOSURE)
    lines.append("")
    lines.append("## Power statement (P5-E2 item 5, verbatim)")
    lines.append("")
    lines.append(P5_E2_POWER_STATEMENT)
    lines.append("")
    lines.append("## Attempts 1-3 (RUN_INCOMPLETE)")
    lines.append("")
    lines.append(ATTEMPTS_1_3_DISCLOSURE)
    lines.append("")
    lines.append("## D12 break-even cost")
    lines.append("")
    lines.append(D12_UNAVAILABLE_DISCLOSURE)
    lines.append("")
    return "\n".join(lines)


def write_report(
    report: ForwardingReport, directory: Path | str
) -> tuple[Path, Path]:
    """Write ``campaign3_forwarding_report.json`` and ``.md``."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "campaign3_forwarding_report.json"
    md_path = root / "campaign3_forwarding_report.md"
    json_path.write_text(
        json.dumps(
            report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# production assembly seam
# ---------------------------------------------------------------------------


class _ForwardingGenerator:
    """A placeholder generator: the forwarding step never generates.

    ``assemble_production`` requires a generator to bind the policy's
    prompt-template hash.  The forwarding step never calls the generator and
    never reads a credential, so this carries only the frozen hash.
    """

    def __init__(self) -> None:
        from smart_beta.research.llm_generator_deepseek import (
            prompt_template_v2_hash,
        )

        self.prompt_template_hash = prompt_template_v2_hash()

    def __call__(self, visible: Any, feedback: Any) -> Any:
        raise ForwardingError(
            "the forwarding step never calls the generator (P6-E6)"
        )


def _default_assembly_factory(
    dataset_root: Path | str,
    knowledge_log_path: Path | str,
    run_root: Path | str,
) -> Any:
    generator = _ForwardingGenerator()
    return _launch.assemble_production(
        dataset_root,
        knowledge_log_path,
        run_root,
        generator,
        screen_workers=1,
    )


def _default_dataset_root() -> Path:
    return _run.REPO_ROOT.parent / "data_store" / "discovery_dataset_v1"


# ---------------------------------------------------------------------------
# the forwarding step
# ---------------------------------------------------------------------------


def forward(
    assembly: Any | None = None,
    *,
    knowledge_log_path: Path | str = _run.KNOWLEDGE_LOG,
    run_root: Path | str = _run.RUN_ROOT,
    knowledge_log: KnowledgeLog | None = None,
    dataset_root: Path | str | None = None,
    dry_run: bool = False,
    report_dir: Path | str | None = None,
    candidate_builder: Callable[..., Any] | None = None,
    rank_fn: Callable[..., Any] | None = None,
    assembly_factory: Callable[..., Any] | None = None,
    p5_path: Path | str = P5_PATH,
    p5_e2_path: Path | str = P5_E2_PATH,
    forward_module_path: Path | str = FORWARD_MODULE_PATH,
) -> ForwardingResult:
    """Execute C3-F (P6-E6) once, or reconcile an already-written decision."""
    log = knowledge_log or KnowledgeLog(Path(knowledge_log_path))

    campaign_record = _run._active_campaign_record(log)
    if campaign_record is None:
        raise ForwardingError(
            "no active Campaign-3 CAMPAIGN record in the K-PIT; refusing to "
            "forward"
        )
    tb_selection_record = _run._existing_tb_selection_record(
        log, campaign_record
    )
    if tb_selection_record is None:
        raise ForwardingError(
            "no TB_SELECTION record bound to the active CAMPAIGN record; "
            "refusing to forward"
        )
    tb_selection = tb_selection_record.payload.get("tb_selection")
    if not isinstance(tb_selection, Mapping):
        raise ForwardingError("the TB_SELECTION record is malformed")
    tb_ranking_hash = tb_selection.get("ranking_table_hash")
    if not isinstance(tb_ranking_hash, str) or not tb_ranking_hash:
        raise ForwardingError(
            "the TB_SELECTION record carries no ranking_table_hash"
        )

    run_dir = _resolve_run_dir(run_root, campaign_record.record_hash)
    generation = _load_generation(run_dir)

    if assembly is None:
        factory = assembly_factory or _default_assembly_factory
        assembly = factory(
            Path(dataset_root) if dataset_root else _default_dataset_root(),
            knowledge_log_path,
            run_root,
        )

    builder = candidate_builder or _build_candidates_cached
    candidates, control = builder(assembly, generation)

    if rank_fn is not None:
        ranking = rank_fn(
            candidates,
            registry=getattr(assembly, "registry", None),
            admission_records=getattr(assembly, "admission_records", ()),
            procedure_ref=getattr(assembly, "procedure_ref", None),
        )
    else:
        ranking = _default_rank(candidates, assembly)

    if ranking.content_hash != tb_ranking_hash:
        raise ForwardingHashMismatch(
            "the recomputed IS ranking table hash does not match the "
            "TB_SELECTION record: recomputed "
            f"{ranking.content_hash!r} != recorded {tb_ranking_hash!r}; "
            "STOP (P5-E2 item 2 / P6-E6 step 2)"
        )

    forwarded_id = _forwarded_id(ranking)
    control_id = str(getattr(control, "hypothesis_id", ""))
    _assert_forwardable(forwarded_id, control_id)

    inputs = ForwardingInputs(
        campaign_record_hash=campaign_record.record_hash,
        tb_selection_record_hash=tb_selection_record.record_hash,
        tb_selection_ranking_table_hash=tb_ranking_hash,
        run_dir=str(run_dir),
        p5_sha256=_p5_sha256(p5_path),
        p5_e2_sha256=_p5_e2_sha256(p5_e2_path),
        forward_module_sha256=_forward_module_sha256(forward_module_path),
    )

    record_hash: str | None = None
    appended = False
    report_paths: tuple[Path, Path] | None = None
    if not dry_run:
        payload = _forwarding_payload(
            campaign_record_hash=inputs.campaign_record_hash,
            tb_selection_record_hash=inputs.tb_selection_record_hash,
            ranking_table_hash=ranking.content_hash,
            forwarded_id=forwarded_id,
            p5_sha256=inputs.p5_sha256,
            p5_e2_sha256=inputs.p5_e2_sha256,
            forward_module_sha256=inputs.forward_module_sha256,
        )
        record, appended = _write_forwarding_record(
            log,
            campaign_record=campaign_record,
            tb_selection_record=tb_selection_record,
            payload=payload,
        )
        record_hash = record.record_hash

    report = ForwardingReport(
        inputs=inputs,
        ranking=ranking,
        forwarded_id=forwarded_id,
        decision=(
            NO_RESERVE_USE if forwarded_id == NO_RESERVE_USE else FORWARD
        ),
        dry_run=bool(dry_run),
        forwarding_record_hash=record_hash,
    )
    if not dry_run:
        out_dir = (
            Path(report_dir) if report_dir is not None else run_dir / REPORT_DIRNAME
        )
        report_paths = write_report(report, out_dir)

    return ForwardingResult(
        forwarded_id=forwarded_id,
        decision=report.decision,
        ranking_table_hash=ranking.content_hash,
        tb_selection_record_hash=inputs.tb_selection_record_hash,
        campaign_record_hash=inputs.campaign_record_hash,
        dry_run=bool(dry_run),
        report=report,
        report_paths=report_paths,
        forwarding_record_hash=record_hash,
        kpit_appended=appended,
    )


def run_forward(**kwargs: Any) -> ForwardingResult:
    """Convenience alias for :func:`forward`."""
    return forward(**kwargs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m smart_beta.campaign.campaign3_forward",
        description=(
            "Campaign-3 C3-F forwarding step (P6-E6 / P5-E2): rank 1 of the "
            "D9 IS ranking, or NO_RESERVE_USE. No T-C, no ACCESS, no reserve."
        ),
    )
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--knowledge-log", default=str(_run.KNOWLEDGE_LOG))
    parser.add_argument("--dataset-root", default=None)
    parser.add_argument("--report-dir", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "compute and print the forwarding decision and hash verification "
            "without writing the K-PIT record or the report"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """The CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = forward(
        knowledge_log_path=args.knowledge_log,
        run_root=args.run_root,
        dataset_root=args.dataset_root,
        dry_run=bool(args.dry_run),
        report_dir=args.report_dir,
    )
    payload = {
        "forwarded_id": result.forwarded_id,
        "decision": result.decision,
        "ranking_table_hash": result.ranking_table_hash,
        "tb_selection_ranking_table_hash": (
            result.report.inputs.tb_selection_ranking_table_hash
        ),
        "hash_verified": (
            result.ranking_table_hash
            == result.report.inputs.tb_selection_ranking_table_hash
        ),
        "dry_run": result.dry_run,
        "forwarding_record_hash": result.forwarding_record_hash,
    }
    sys.stdout.write(
        json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the CLI test
    raise SystemExit(main())
