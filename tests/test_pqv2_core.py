"""PQ2-A — P-Q v2 core tests (synthetic / authority-document only).

Every test here is offline and synthetic: an autouse socket guard and credential
sentinel mirror the D7a/D7b/PQB/PQA/PQC suites.  The only repository files read
are the frozen methodology document and the pinned reused production modules
(via :func:`pqv2_core.verify_authority`); no dataset artifact, reserve, holdout,
``raw/`` or ``dsc_evidence`` path is ever opened, and no network call is made.

The mutation fixture (``tests/fixtures/pqv2/core/mutations.json``) targets one
named test per mutation; the mapping is checked mechanically by
``test_mutation_fixture_contract``.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
import shutil
import socket
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.engines.portfolio_sort import assign_groups

REPO_ROOT = Path(__file__).resolve().parents[1]
METHODOLOGY_REL = Path("docs") / "discovery_dataset_v1_2_pq_v2_methodology.md"
CREDENTIAL_SENTINEL = "pqv2-core-credential-sentinel-9f31"

_CREDENTIAL_KEYS = (
    "TIINGO_API_KEY",
    "TUSHARE_PROXY_TOKEN",
    "TUSHARE_BASIC_PROXY_TOKEN",
    "TUSHARE_API_TOKEN",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
)

_EXPECTED_CS_LINE = (
    '{"correction_set":"pq-v2-S2","form":"[min(lo,m_obs),max(hi,m_obs)]",'
    '"hi":1.5,"lo":0.5,"model":"PREDECLARED_CORRECTION_SENSITIVITY"}'
)
_EXPECTED_ED_LINE = (
    '{"rules":["tri_log_linear_prefix_cancellation","lag_shift",'
    '"rolling_window_union_numerical","arithmetic_conservative_union",'
    '"cross_sectional_whole_row","label_candidate_e_[f,next(e)]",'
    '"dtr_{prev,s,next}","dv_{prev,s}","misdated_recorded_pm5",'
    '"reserve_unknown_not_assessed"],"version":"pq-v2-ed-1"}'
)
_EXPECTED_CE_LINE = (
    '{"classes":{"simple_exact":["C1-H1","C1-H2","C1-H3","C1-H6","C1-H7"],'
    '"std_x_rank_sandwich":["C1-H4","C1-H5"]},'
    '"other":"NOT_ASSESSED_UNLESS_SEPARATELY_FROZEN","version":"pq-v2-ce-1"}'
)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQ2-A tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.setenv(key, CREDENTIAL_SENTINEL)
    yield


def test_credential_sentinel_never_leaks() -> None:
    assert C.METHODOLOGY_SHA256 != CREDENTIAL_SENTINEL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _copy_authority_tree(tmp_path: Path) -> Path:
    """Copy the methodology document and every pinned file into a temp root."""

    root = tmp_path / "repo"
    doc = root / METHODOLOGY_REL
    doc.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / METHODOLOGY_REL, doc)
    for relative in C.PINNED_CODE:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, destination)
    return root


def _brute_leg(
    forced: list[float],
    pool: list[float],
    n_take: int,
    *,
    take_max: bool,
) -> float | None:
    """Brute-force the exact leg extremum; ``None`` means empty-leg (M§13)."""

    means: list[float] = []
    for chosen in itertools.combinations(range(len(pool)), n_take):
        values = list(forced) + [pool[index] for index in chosen]
        finite = [value for value in values if math.isfinite(value)]
        if not finite:
            return None
        means.append(math.fsum(finite) / len(finite))
    return max(means) if take_max else min(means)


# ---------------------------------------------------------------------------
# Identity binding (plan §2; M§15)
# ---------------------------------------------------------------------------


def test_identity_constants_match_document() -> None:
    lines = C.identity_lines(REPO_ROOT)
    assert lines["CS"] == _EXPECTED_CS_LINE
    assert lines["ED"] == _EXPECTED_ED_LINE
    assert lines["CE"] == _EXPECTED_CE_LINE
    for key, line in lines.items():
        assert not line.endswith("\n")
        assert "\n" not in line

    assert hashlib.sha256(lines["CS"].encode("utf-8")).hexdigest() == C.CS_ID
    assert hashlib.sha256(lines["ED"].encode("utf-8")).hexdigest() == C.ED_ID
    assert hashlib.sha256(lines["CE"].encode("utf-8")).hexdigest() == C.CE_ID

    hashes = C.recompute_identity_hashes(REPO_ROOT)
    assert hashes["methodology"] == C.METHODOLOGY_SHA256
    assert hashes["CS"] == C.CS_ID
    assert hashes["ED"] == C.ED_ID
    assert hashes["CE"] == C.CE_ID

    assert len(C.METHODOLOGY_SHA256) == 64
    assert C.METHODOLOGY_SHA256 == (
        "a31ed9c49cd3a03208ae28539a8b5f5531916e31fac32bbd60e2af07746fa56d"
    )


def test_verify_authority_passes_on_repository() -> None:
    assert C.verify_authority(REPO_ROOT) is None


def test_verify_authority_rejects_tampered_document(tmp_path: Path) -> None:
    root = _copy_authority_tree(tmp_path)
    assert C.verify_authority(root) is None
    doc = root / METHODOLOGY_REL
    text = doc.read_text(encoding="utf-8")
    doc.write_text("# tampered title line\n" + text.split("\n", 1)[1], encoding="utf-8")
    with pytest.raises(C.PQv2BindingError):
        C.verify_authority(root)


def test_verify_authority_rejects_wrong_document_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(C, "METHODOLOGY_SHA256", "0" * 64)
    with pytest.raises(C.PQv2BindingError):
        C.verify_authority(REPO_ROOT)


def test_verify_authority_rejects_tampered_identity_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _copy_authority_tree(tmp_path)
    doc = root / METHODOLOGY_REL
    text = doc.read_text(encoding="utf-8")
    text = text.replace('"correction_set":"pq-v2-S2"', '"correction_set":"pq-v2-S1"')
    doc.write_text(text, encoding="utf-8")
    # Step (a) would fire first; pin the tampered document's hash so the test
    # isolates the identity-line check (b).
    monkeypatch.setattr(
        C, "METHODOLOGY_SHA256", hashlib.sha256(doc.read_bytes()).hexdigest()
    )
    with pytest.raises(C.PQv2BindingError):
        C.verify_authority(root)


def test_verify_authority_rejects_tampered_pinned_file(tmp_path: Path) -> None:
    root = _copy_authority_tree(tmp_path)
    target = root / "smart_beta/dataset_v1/returns.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(C.PQv2BindingError):
        C.verify_authority(root)


def test_verify_authority_rejects_missing_pinned_file(tmp_path: Path) -> None:
    root = _copy_authority_tree(tmp_path)
    (root / "smart_beta/dataset_v1/returns.py").unlink()
    with pytest.raises(C.PQv2BindingError):
        C.verify_authority(root)


def test_pqv2_code_sha256_is_deterministic_and_sensitive(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    module_dir = root / "smart_beta" / "dataset_v1"
    module_dir.mkdir(parents=True)
    core_path = module_dir / "pqv2_core.py"
    core_path.write_text("x = 1\n", encoding="utf-8")

    first = C.pqv2_code_sha256(root)
    assert len(first) == 64
    assert C.pqv2_code_sha256(root) == first
    assert C.pqv2_code_manifest(root) == (
        (
            "smart_beta/dataset_v1/pqv2_core.py",
            hashlib.sha256(b"x = 1\n").hexdigest(),
        ),
    )

    (module_dir / "pqv2_extra.py").write_text("y = 2\n", encoding="utf-8")
    assert C.pqv2_code_sha256(root) != first

    (module_dir / "pqv2_extra.py").write_text("y = 3\n", encoding="utf-8")
    assert C.pqv2_code_sha256(root) != first

    real_manifest = dict(C.pqv2_code_manifest(REPO_ROOT))
    assert "smart_beta/dataset_v1/pqv2_core.py" in real_manifest
    assert real_manifest["smart_beta/dataset_v1/pqv2_core.py"] == hashlib.sha256(
        (REPO_ROOT / "smart_beta/dataset_v1/pqv2_core.py").read_bytes()
    ).hexdigest()


# ---------------------------------------------------------------------------
# S2 correction set and interval arithmetic (M§3)
# ---------------------------------------------------------------------------


def test_s2_absolute_and_relative_values() -> None:
    expected = {
        0.2: (0.2, 1.5),
        0.5: (0.5, 1.5),
        1.0: (0.5, 1.5),
        1.5: (0.5, 1.5),
        4.0: (0.5, 4.0),
    }
    for m, (lo, hi) in expected.items():
        absolute = C.s2_absolute(m)
        assert absolute.lo == pytest.approx(lo)
        assert absolute.hi == pytest.approx(hi)
        assert absolute.contains(lo)
        assert absolute.contains(hi)
        assert absolute.contains((lo + hi) / 2.0)
        assert not absolute.contains(lo - 1e-9)
        assert not absolute.contains(hi + 1e-9)
        assert not absolute.contains(float("nan"))

        relative = C.s2_relative(m)
        assert relative.lo == pytest.approx(lo / m)
        assert relative.hi == pytest.approx(hi / m)
        assert relative.contains(1.0)
        assert relative.lo <= 1.0 <= relative.hi

    assert C.s2_absolute(1.0) == C.PositiveInterval(0.5, 1.5)
    assert C.s2_relative(1.0) == C.PositiveInterval(0.5, 1.5)
    assert not C.s2_absolute(1.0).is_unit
    assert C.UNIT.is_unit
    assert C.PositiveInterval(1.0, 1.0).is_unit


def test_s2_contains_partial_correction_not_in_s1() -> None:
    s2 = C.s2_absolute(4.0)
    assert s2.contains(2.0)

    def in_s1(value: float) -> bool:
        return value == 4.0 or (0.5 <= value <= 1.5)

    assert not in_s1(2.0)

    s2_low = C.s2_absolute(0.2)
    assert s2_low.contains(0.35)

    def in_s1_low(value: float) -> bool:
        return value == 0.2 or (0.5 <= value <= 1.5)

    assert not in_s1_low(0.35)


def test_s2_rejects_nonpositive_or_nonfinite() -> None:
    for bad in (0.0, -1.0, float("nan"), float("inf"), -float("inf")):
        with pytest.raises(C.PQv2InputError):
            C.s2_absolute(bad)
        with pytest.raises(C.PQv2InputError):
            C.s2_relative(bad)
    for bad in (None, "not-a-number", object()):
        with pytest.raises(C.PQv2InputError):
            C.s2_absolute(bad)
        with pytest.raises(C.PQv2InputError):
            C.s2_relative(bad)


def test_positive_interval_mul_inv_hull() -> None:
    a = C.PositiveInterval(0.5, 2.0)
    b = C.PositiveInterval(1.0, 3.0)

    assert a.mul(b) == C.PositiveInterval(0.5, 6.0)
    assert a.mul(b) == b.mul(a)
    assert a.mul(C.UNIT) == a
    assert a.mul(C.PositiveInterval(2.0, 2.0)) == C.PositiveInterval(1.0, 4.0)

    assert a.inv() == C.PositiveInterval(0.5, 2.0)
    assert a.inv().inv() == a
    assert a.mul(a.inv()).contains(1.0)
    assert C.PositiveInterval(2.0, 4.0).inv() == C.PositiveInterval(0.25, 0.5)

    hulled = a.hull(b)
    assert hulled == C.PositiveInterval(0.5, 3.0)
    assert hulled.contains(a.lo) and hulled.contains(a.hi)
    assert hulled.contains(b.lo) and hulled.contains(b.hi)
    assert C.PositiveInterval(2.0, 2.0).hull() == C.PositiveInterval(2.0, 2.0)

    assert C.UNIT.inv() == C.UNIT
    assert C.UNIT.mul(a) == a


def test_positive_interval_rejects_invalid() -> None:
    invalid = [
        (0.0, 1.0),
        (-1.0, 1.0),
        (1.0, 0.5),
        (float("nan"), 1.0),
        (1.0, float("nan")),
        (1.0, float("inf")),
        (float("-inf"), 1.0),
    ]
    for lo, hi in invalid:
        with pytest.raises(C.PQv2InputError):
            C.PositiveInterval(lo, hi)


# ---------------------------------------------------------------------------
# Leg-extremum combinatorics (M§12 Prop. 7)
# ---------------------------------------------------------------------------


def test_leg_extremum_matches_brute_force() -> None:
    rng = random.Random(20240617)

    def value() -> float:
        if rng.random() < 0.30:
            return float("nan")
        return round(rng.uniform(-8.0, 8.0), 3)

    for _ in range(400):
        forced = [value() for _ in range(rng.randint(0, 3))]
        pool = [value() for _ in range(rng.randint(0, 8))]
        n_take = rng.randint(0, len(pool))
        for take_max, function in ((False, C.leg_min_mean), (True, C.leg_max_mean)):
            expected = _brute_leg(forced, pool, n_take, take_max=take_max)
            result = function(forced, pool, n_take)
            if expected is None:
                assert isinstance(result, C.NotAssessed)
                assert result.reason == C.PQV2_EMPTY_LEG_CONFIGURATION
            else:
                assert isinstance(result, float)
                assert result == pytest.approx(expected, rel=1e-12, abs=1e-15)


def test_leg_extremum_counts_nan_filled_choices() -> None:
    # Choosing the NaN pool member lowers the denominator, so the lowest leg mean
    # is the NaN-filled choice (10.0), not the finite-filled choice (55.0).
    assert C.leg_min_mean([10.0], [float("nan"), 100.0], 1) == pytest.approx(10.0)
    assert C.leg_max_mean([10.0], [float("nan"), 100.0], 1) == pytest.approx(55.0)
    assert C.leg_min_mean([10.0], [float("nan"), 100.0], 0) == pytest.approx(10.0)
    # A larger NaN fill dominates when the forced mean is above the pool mean.
    result = C.leg_min_mean([100.0], [float("nan"), 1.0], 1)
    assert result == pytest.approx(50.5)
    result_nan_first = C.leg_min_mean([100.0], [float("nan"), 1.0], 2)
    assert result_nan_first == pytest.approx((100.0 + 1.0) / 2.0)


def test_leg_extremum_empty_configuration() -> None:
    assert C.leg_min_mean([1.0], [float("nan")], 0) == pytest.approx(1.0)
    empty = C.leg_min_mean([], [float("nan")], 0)
    assert isinstance(empty, C.NotAssessed)
    assert empty.reason == C.PQV2_EMPTY_LEG_CONFIGURATION

    empty_max = C.leg_max_mean([float("nan")], [float("nan"), float("nan")], 2)
    assert isinstance(empty_max, C.NotAssessed)
    assert empty_max.reason == C.PQV2_EMPTY_LEG_CONFIGURATION

    with pytest.raises(C.PQv2InputError):
        C.leg_min_mean([], [], 1)
    with pytest.raises(C.PQv2InputError):
        C.leg_min_mean([], [], -1)


# ---------------------------------------------------------------------------
# Winsorization block check (M§11)
# ---------------------------------------------------------------------------


def test_winsor_block_hand_computed() -> None:
    # A clean passing case.
    assert C.winsor_block_ok(
        [1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 2.0, 3.0, 4.0, 5.0], 0.0, 2, 2
    )
    # Low side fails.
    assert not C.winsor_block_ok(
        [1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 2.0, 3.0, 4.0, 5.0], 0.0, 1, 2
    )
    # High side fails.
    assert not C.winsor_block_ok(
        [1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 2.0, 3.0, 4.0, 5.0], 0.0, 2, 1
    )
    # A cap-tied block that fits exactly.
    assert C.winsor_block_ok(
        [1.0, 2.0, 2.0, 2.0, 5.0], [1.0, 2.0, 2.0, 2.0, 5.0], 0.0, 4, 4
    )
    # The same cap-tied block one member too wide fails.
    assert not C.winsor_block_ok(
        [1.0, 2.0, 2.0, 2.0, 5.0], [1.0, 2.0, 2.0, 2.0, 5.0], 0.0, 3, 4
    )
    # Infinite bounds are admissible.
    assert C.winsor_block_ok(
        [float("-inf"), 1.0, 2.0], [1.0, 2.0, float("inf")], 0.0, 3, 3
    )
    # n < floor(h)+2 fails closed.
    assert not C.winsor_block_ok([1.0, 2.0], [1.0, 2.0], 1.0, 2, 2)
    # Mismatched lengths and negative bucket sizes are input errors.
    with pytest.raises(C.PQv2InputError):
        C.winsor_block_ok([1.0], [1.0, 2.0], 0.0, 1, 1)
    with pytest.raises(C.PQv2InputError):
        C.winsor_block_ok([1.0, 2.0], [1.0, 2.0], 0.0, -1, 1)


def test_winsor_block_uses_floor_h_plus_two_position() -> None:
    # n=3, q=0 => floor(h)+2 == 2.  The correct check returns False (the 2nd
    # smallest upper bound is 20 and three lower bounds are <= 20).  The
    # off-by-one mutation (position 1) would return True here.
    assert not C.winsor_block_ok([0.0, 15.0, 0.0], [10.0, 20.0, 30.0], 0.0, 2, 3)
    # A second, high-side off-by-one case: the correct check returns False while
    # position 1 would pass.  Here the 2nd largest lower bound is 0 and all three
    # upper bounds are >= 0 (3 > s_high=2).
    assert not C.winsor_block_ok([15.0, 0.0, 0.0], [10.0, 20.0, 30.0], 0.0, 3, 2)


# ---------------------------------------------------------------------------
# Bucket sizes (M§2)
# ---------------------------------------------------------------------------


def test_bucket_sizes_matches_assign_groups() -> None:
    for n in range(1, 61):
        series = pd.Series(np.arange(n, dtype="float64"))
        for groups in (2, 3, 5):
            assigned = assign_groups(series, groups)
            counts = tuple(
                int((assigned == group).sum()) for group in range(1, groups + 1)
            )
            assert counts == C.bucket_sizes(n, groups), (n, groups)
            assert sum(counts) == n

    empty = assign_groups(pd.Series([], dtype="float64"), 5)
    empty_counts = tuple(int((empty == group).sum()) for group in range(1, 6))
    assert empty_counts == C.bucket_sizes(0, 5) == (0, 0, 0, 0, 0)

    with pytest.raises(C.PQv2InputError):
        C.bucket_sizes(-1, 5)
    with pytest.raises(C.PQv2InputError):
        C.bucket_sizes(3, 0)


# ---------------------------------------------------------------------------
# Mutation fixture contract
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    path = REPO_ROOT / "tests/fixtures/pqv2/core/mutations.json"
    mutations = json.loads(path.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        "CONTROL",
        "V2-M05",
        "V2-M15",
        "V2-M19",
        "A-M1",
        "A-M2",
        "A-M3",
        "A-M4",
        "A-M5",
    ]
    source = (REPO_ROOT / "smart_beta/dataset_v1/pqv2_core.py").read_text(
        encoding="utf-8"
    )
    this_module = sys.modules[__name__]
    for mutation in mutations:
        assert mutation["file"] == "smart_beta/dataset_v1/pqv2_core.py"
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name == "tests/test_pqv2_core.py"
        assert hasattr(this_module, test_name), mutation["id"]
        if mutation["id"] == "CONTROL":
            assert mutation["edits"] == []
            continue
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (mutation["id"], edit["old"])
            assert edit["old"] != edit["new"], mutation["id"]
