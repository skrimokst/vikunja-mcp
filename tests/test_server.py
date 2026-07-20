"""Tests for the tool layer — project resolution and readiness, which is where the multi-project
setup (no VIKUNJA_PROJECT_ID in the env, a project_id on every call) is easy to get wrong — plus
the payload shaping in _fmt_task.

The env is patched per test; no live server is needed.
"""

from __future__ import annotations

import pytest

from vikunja_mcp import server
from vikunja_mcp.config import TOKEN_SETUP_HELP
from vikunja_mcp.server import _fmt_date, _fmt_related, _fmt_task

VIKUNJA_ENV = ("VIKUNJA_URL", "VIKUNJA_API_TOKEN", "VIKUNJA_PROJECT_ID", "VIKUNJA_PROJECT")

ZERO = "0001-01-01T00:00:00Z"  # Vikunja's "unset" for every date field


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


def test_check_connection_without_project_lists_available_projects(env, monkeypatch):
    """No project named: prove the token by listing projects and hand back the ids to choose from,
    instead of demanding one up front."""
    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def list_projects(self):
            return [{"id": 11, "title": "Alpha"}, {"id": 7, "title": "Beta"}]

    monkeypatch.setattr(server, "_client", lambda cfg: FakeClient())

    out = server.check_connection()

    assert out["ready"] is True
    assert out["projects"] == [{"id": 11, "title": "Alpha"}, {"id": 7, "title": "Beta"}]
    assert "project" not in out  # the singular key is only for the with-a-project path


def test_check_connection_no_project_and_no_list_scope_points_at_project_id(env, monkeypatch):
    """A token scoped to specific projects gets 403 on GET /projects — that is not a broken setup,
    so say 'pass a project_id' rather than reporting the raw scope error."""
    from vikunja_mcp.client import VikunjaError

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def list_projects(self):
            raise VikunjaError("GET /projects -> HTTP 403 forbidden", status=403)

    monkeypatch.setattr(server, "_client", lambda cfg: FakeClient())

    out = server.check_connection()

    assert out["ready"] is False
    assert "pass a project_id" in " ".join(out["issues"])


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


def test_token_help_flag_prints_command_and_does_not_start_server(capsys, monkeypatch):
    """`vikunja-mcp --token-help` reprints the setup command and returns before mcp.run()."""
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: pytest.fail("server must not start"))

    server.main(["--token-help"])

    out = capsys.readouterr().out
    assert out.strip() == TOKEN_SETUP_HELP
    assert "PowerShell" in out and "VIKUNJA_API_TOKEN" in out


def test_get_task_returns_description_as_markdown(env, monkeypatch):
    """Vikunja stores the description as HTML; get_task converts it back so reads are markdown too."""
    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def get_task(self, task_id):
            return _raw(id=task_id, description="<p>hello <strong>world</strong></p>")

    monkeypatch.setattr(server, "_client", lambda cfg: FakeClient())

    out = server.get_task(281)

    assert out["description"] == "hello **world**"


# --- payload shaping --------------------------------------------------------
# Fixtures below are trimmed copies of what a live Vikunja 2.3.0 actually returns.


def test_fmt_date_treats_the_zero_time_as_unset():
    assert _fmt_date(ZERO) == ""
    assert _fmt_date("0001-01-01T00:00:00+01:00") == ""
    assert _fmt_date(None) == ""
    assert _fmt_date("") == ""


def test_fmt_date_passes_real_dates_through():
    assert _fmt_date("2026-07-17T13:09:53+01:00") == "2026-07-17T13:09:53+01:00"
    assert _fmt_date("2026-07-17T13:09:53+01:00", date_only=True) == "2026-07-17"


def test_fmt_related_trims_nested_tasks_to_references():
    """Vikunja nests the WHOLE task under each kind. A 1MB description must not ride along."""
    raw = {
        "subtask": [
            {"id": 312, "identifier": "", "title": "child", "done": False,
             "description": "x" * 100_000, "related_tasks": {}, "labels": [{"title": "l"}]},
        ]
    }
    out = _fmt_related(raw)
    assert out == {"subtask": [{"id": 312, "identifier": "", "title": "child", "done": False}]}
    assert "description" not in out["subtask"][0]


def test_fmt_related_handles_no_relations():
    assert _fmt_related({}) == {}
    assert _fmt_related(None) == {}
    assert _fmt_related({"subtask": []}) == {}  # empty kinds dropped


def _raw(**over) -> dict:
    base = {
        "id": 306, "index": 26, "identifier": "TEST-26", "project_id": 11,
        "title": "t", "done": False, "done_at": ZERO, "priority": 0,
        "due_date": ZERO, "start_date": ZERO, "end_date": ZERO,
        "labels": None, "assignees": None, "related_tasks": {}, "reminders": None,
        "repeat_after": 0, "repeat_mode": 0,
        "created": "2026-07-17T13:09:53+01:00", "updated": "2026-07-17T13:09:53+01:00",
        # noise that must NOT surface
        "bucket_id": 4, "position": 99, "cover_image_attachment_id": 0, "reactions": None,
    }
    base.update(over)
    return base


def test_fmt_task_exposes_project_id():
    """The task-id tools take no project, so this is the only way to know where a task lives."""
    assert _fmt_task(_raw())["project_id"] == 11


def test_fmt_task_blanks_unset_dates_rather_than_showing_year_one():
    out = _fmt_task(_raw())
    for field in ("due", "done_at", "start_date", "end_date"):
        assert out[field] == "", field


def test_fmt_task_keeps_real_dates():
    out = _fmt_task(_raw(due_date="2026-08-01T00:00:00Z", done_at="2026-07-17T13:00:00Z"))
    assert out["due"] == "2026-08-01"                 # date-only
    assert out["done_at"] == "2026-07-17T13:00:00Z"   # full timestamp


def test_fmt_task_assignees_are_usernames_not_ids():
    """This token cannot resolve a user id (GET /user is 401), so an id would be a dead handle."""
    out = _fmt_task(_raw(assignees=[{"id": 3, "name": "", "username": "service"}]))
    assert out["assignees"] == ["service"]


def test_fmt_task_assignee_falls_back_to_name_when_username_missing():
    out = _fmt_task(_raw(assignees=[{"id": 3, "name": "Real Name", "username": ""}]))
    assert out["assignees"] == ["Real Name"]


def test_fmt_task_keeps_all_three_reminder_fields():
    """Absolute vs relative reminders are only distinguishable if all three survive."""
    out = _fmt_task(_raw(reminders=[
        {"reminder": "2026-08-01T09:00:00Z", "relative_period": 0, "relative_to": ""},
        {"reminder": ZERO, "relative_period": -3600, "relative_to": "due_date"},
    ]))
    assert out["reminders"] == [
        {"reminder": "2026-08-01T09:00:00Z", "relative_to": "", "relative_period": 0},
        {"reminder": "", "relative_to": "due_date", "relative_period": -3600},
    ]


def test_fmt_task_drops_kanban_and_ui_noise():
    out = _fmt_task(_raw())
    for junk in ("bucket_id", "position", "cover_image_attachment_id", "reactions"):
        assert junk not in out, junk


def test_fmt_task_tolerates_nulls_everywhere():
    """Vikunja sends null (not []) for empty labels/assignees/reminders."""
    out = _fmt_task(_raw())
    assert out["labels"] == []
    assert out["assignees"] == []
    assert out["reminders"] == []
    assert out["related_tasks"] == {}
