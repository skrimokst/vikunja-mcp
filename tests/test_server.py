"""Tests for the tool layer — project resolution and readiness, which is where the multi-project
setup (no VIKUNJA_PROJECT_ID in the env, a project_id on every call) is easy to get wrong.

The env is patched per test; no live server is needed.
"""

from __future__ import annotations

import pytest

from vikunja_mcp import server

VIKUNJA_ENV = ("VIKUNJA_URL", "VIKUNJA_API_TOKEN", "VIKUNJA_PROJECT_ID", "VIKUNJA_PROJECT")


@pytest.fixture
def env(monkeypatch):
    """Start from a clean slate — the operator's own Vikunja env must not leak into the tests."""
    for name in VIKUNJA_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VIKUNJA_URL", "https://vk.test")
    monkeypatch.setenv("VIKUNJA_API_TOKEN", "tok")
    return monkeypatch


def no_http(*args, **kwargs):
    raise AssertionError("no HTTP expected")


def test_check_connection_without_any_project_asks_for_one(env, monkeypatch):
    """Token and URL are fine, but there is nothing to probe: say so instead of reporting ready,
    and do not touch the network."""
    monkeypatch.setattr(server, "_client", no_http)

    out = server.check_connection()

    assert out["ready"] is False
    assert "no project_id was passed" in " ".join(out["issues"])


def test_check_connection_probes_the_project_id_passed(env, monkeypatch):
    """An explicit project_id is what gets probed — a default is not required."""
    probed = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def probe(self, project):
            probed.append(project)
            return {"project_id": project}

    monkeypatch.setattr(server, "_client", lambda cfg: FakeClient())

    out = server.check_connection(project_id=7)

    assert probed == [7]
    assert out["ready"] is True
    assert out["project"] == 7


def test_check_connection_project_id_overrides_the_default(env, monkeypatch):
    env.setenv("VIKUNJA_PROJECT_ID", "3")
    probed = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def probe(self, project):
            probed.append(project)
            return {"project_id": project}

    monkeypatch.setattr(server, "_client", lambda cfg: FakeClient())

    assert server.check_connection(project_id=7)["project"] == 7
    assert server.check_connection()["project"] == 3  # falls back to the default when omitted
    assert probed == [7, 3]


def test_missing_token_is_reported_before_any_project_check(env, monkeypatch):
    env.delenv("VIKUNJA_API_TOKEN")
    monkeypatch.setattr(server, "_client", no_http)

    out = server.check_connection(project_id=7)

    assert out["ready"] is False
    assert any("VIKUNJA_API_TOKEN" in i for i in out["issues"])
