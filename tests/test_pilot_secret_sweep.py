"""P10-V (P1A-SV): full-package secret-value sweep.

Frozen contract: ``worker_tasks/pilot1/pilot1-plan.md`` section 26g and
``worker_tasks/phase10/phase10-plan.md`` section 16 (the P10-V row).

The sweep must:

* run only after the final package is fully assembled;
* cover every file recursively and fail closed on an unreadable file or a
  file-count mismatch;
* detect the captured credential value raw **and** in its common serializer
  encodings (JSON-escaped, URL-encoded, base64);
* carry only the variable name, the checked file count and a boolean.

Every secret here is a synthetic sentinel. No real credential value is read,
written or printed, and the shared ``offline_guard`` fixture blocks all
network egress and scrubs every credential environment variable.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.parse

import pytest
from pilot_support import offline_guard  # noqa: F401

from smart_beta.pilot.artifacts import (
    CREDENTIAL_ENV_VARS,
    credential_value_encodings,
    expected_package_file_count,
    secret_sweep,
)
from smart_beta.pilot.runner import (
    CredentialValueSweepResult,
    credential_value_sweep,
)

from test_pilot_artifacts import _assemble, _clean_run

pytestmark = pytest.mark.usefixtures("offline_guard")

#: A synthetic sentinel whose JSON, URL and base64 encodings all differ from
#: the raw bytes (so each artifact class is exercised independently).
SENTINEL = 'sk-p10v-synthetic/"\\+?=& sentinel'

#: The captured provider credential name this task adds beyond the fixed list.
CAPTURED_ENV_VAR = "DEEPSEEK_API_KEY"


def _encoded_forms(value: str) -> dict[str, str]:
    """The four artifact classes a serializer could emit for ``value``."""
    return {
        "raw": value,
        "json": json.dumps(value)[1:-1],
        "url": urllib.parse.quote(value, safe=""),
        "base64": base64.b64encode(value.encode("utf-8")).decode("ascii"),
    }


def _clean_journal_package(tmp_path):
    """A frozen, fully assembled clean package (via the P1A-G6 helpers)."""
    journal = _clean_run(tmp_path)
    return _assemble(tmp_path, journal)


# ---------------------------------------------------------------------------
# encoding coverage
# ---------------------------------------------------------------------------


def test_credential_value_encodings_cover_each_serializer_form():
    forms = _encoded_forms(SENTINEL)
    candidates = credential_value_encodings(SENTINEL)
    for label, encoded in forms.items():
        assert encoded in candidates, label
    # Distinct, non-empty candidates only.
    assert all(candidates)
    assert len(set(candidates)) == len(candidates)


@pytest.mark.parametrize("form", ["raw", "json", "url", "base64"])
def test_value_sweep_detects_sentinel_in_each_artifact_class(tmp_path, form):
    encoded = _encoded_forms(SENTINEL)[form]
    (tmp_path / f"leak.{form}").write_text(encoded + "\n", encoding="utf-8")

    result = credential_value_sweep(
        tmp_path, env_var=CAPTURED_ENV_VAR, value=SENTINEL
    )

    assert isinstance(result, CredentialValueSweepResult)
    assert result.passed is False
    assert result.env_var == CAPTURED_ENV_VAR
    assert result.files_checked == 1
    assert SENTINEL not in json.dumps(result.to_dict())


# ---------------------------------------------------------------------------
# clean package / file-count assertion
# ---------------------------------------------------------------------------


def test_value_sweep_passes_a_clean_package_and_asserts_the_count(tmp_path):
    package = _clean_journal_package(tmp_path)
    expected = expected_package_file_count(package.layout)

    result = credential_value_sweep(
        package.directory,
        env_var=CAPTURED_ENV_VAR,
        value=SENTINEL,
        expected_file_count=expected,
    )

    assert result.passed is True
    assert result.files_checked == expected
    assert result.to_dict() == {
        "env_var": CAPTURED_ENV_VAR,
        "passed": True,
        "files_checked": expected,
    }
    # The synthetic sentinel is never written into the package.
    assert all(
        SENTINEL.encode("utf-8") not in path.read_bytes()
        for path in package.directory.rglob("*")
        if path.is_file()
    )


def test_value_sweep_fails_closed_on_a_count_mismatch(tmp_path):
    package = _clean_journal_package(tmp_path)
    expected = expected_package_file_count(package.layout)

    (package.directory / "unexpected-extra.txt").write_text(
        "not a package file\n", encoding="utf-8"
    )

    result = credential_value_sweep(
        package.directory,
        env_var=CAPTURED_ENV_VAR,
        value=SENTINEL,
        expected_file_count=expected,
    )

    assert result.passed is False
    assert result.files_checked == expected + 1
    assert SENTINEL not in json.dumps(result.to_dict())


def test_value_sweep_fails_closed_on_an_unreadable_file(tmp_path):
    if hasattr(os, "geteuid") and os.geteuid() == 0:  # pragma: no cover - root
        pytest.skip("file permissions do not block reads as root")
    locked = tmp_path / "locked.txt"
    locked.write_text("clean\n", encoding="utf-8")
    locked.chmod(0)
    try:
        result = credential_value_sweep(
            tmp_path, env_var=CAPTURED_ENV_VAR, value=SENTINEL
        )
    finally:
        locked.chmod(0o600)

    assert result.passed is False
    assert result.files_checked == 1


# ---------------------------------------------------------------------------
# captured credential name (not only the fixed CREDENTIAL_ENV_VARS list)
# ---------------------------------------------------------------------------


def test_artifact_sweep_value_checks_the_captured_provider_name(tmp_path):
    assert CAPTURED_ENV_VAR not in CREDENTIAL_ENV_VARS
    encoded = _encoded_forms(SENTINEL)["base64"]
    (tmp_path / "manifest.json").write_text(encoded + "\n", encoding="utf-8")

    report = secret_sweep(
        [tmp_path],
        environ={},
        credentials={CAPTURED_ENV_VAR: SENTINEL},
    )

    assert report.status.value == "FAIL"
    assert report.checked_env_vars == (CAPTURED_ENV_VAR,)
    assert any(
        finding.kind == "live_credential_value"
        and finding.env_var == CAPTURED_ENV_VAR
        for finding in report.findings
    )
    assert SENTINEL not in json.dumps(report.to_dict())


# ---------------------------------------------------------------------------
# the value is never printed
# ---------------------------------------------------------------------------


def test_value_sweep_never_prints_the_value(tmp_path, capsys):
    (tmp_path / "leak.txt").write_text(SENTINEL + "\n", encoding="utf-8")

    result = credential_value_sweep(
        tmp_path, env_var=CAPTURED_ENV_VAR, value=SENTINEL
    )
    captured = capsys.readouterr()

    assert result.passed is False
    assert SENTINEL not in captured.out
    assert SENTINEL not in captured.err
    assert SENTINEL not in repr(result)
    assert SENTINEL not in json.dumps(result.to_dict())
