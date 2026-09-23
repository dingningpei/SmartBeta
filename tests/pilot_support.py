"""Shared offline guard for the Pilot-1A test suite (owned by P1A-C).

The protected ``tests/conftest.py`` is deliberately **not** modified (plan
section 20). Instead this module provides the ``offline_guard`` fixture that
every ``tests/test_pilot_*.py`` module imports and applies with::

    from pilot_support import offline_guard

    pytestmark = pytest.mark.usefixtures("offline_guard")

The guard is the test-process enforcement of the plan's credential and
network boundaries (sections 19 and 21): it makes any outbound network access
fail loudly and removes every data-provider and model credential from the
environment before the test body runs. It never reads, prints or stores a
credential value.
"""

from __future__ import annotations

import socket
import urllib.request

import pytest

__all__ = ["CREDENTIAL_ENV_VARS", "offline_guard"]

#: Environment variables scrubbed for every Pilot-1A test (plan section 9).
CREDENTIAL_ENV_VARS: tuple[str, ...] = (
    "TIINGO_API_KEY",
    "TUSHARE_PROXY_TOKEN",
    "TUSHARE_BASIC_PROXY_TOKEN",
    "TUSHARE_API_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
)


def _refuse_offline(*args: object, **kwargs: object) -> None:
    raise RuntimeError(
        "offline_guard: outbound network access is forbidden in Pilot-1A tests"
    )


@pytest.fixture
def offline_guard(monkeypatch: pytest.MonkeyPatch):
    """Block network egress and scrub credentials for the duration of a test.

    Replaces ``urllib.request.urlopen``, ``socket.socket.connect`` and
    ``socket.create_connection`` with a raiser, and deletes every
    :data:`CREDENTIAL_ENV_VARS` entry from the environment.
    """
    monkeypatch.setattr(urllib.request, "urlopen", _refuse_offline)
    monkeypatch.setattr(socket.socket, "connect", _refuse_offline)
    monkeypatch.setattr(socket, "create_connection", _refuse_offline)
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield
