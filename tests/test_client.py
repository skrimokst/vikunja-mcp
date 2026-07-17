"""Tests for VikunjaClient — pure helpers plus REST logic via httpx.MockTransport.

No live server or Python-on-the-host beyond the venv is needed. Run with `uv run pytest`.
"""

from __future__ import annotations

import json

import httpx
import pytest

from vikunja_mcp.client import (
    VikunjaClient,
    VikunjaError,
    check_priority,
    to_vk_date,
    to_vk_html,
)


# --- pure helpers -----------------------------------------------------------
def test_to_vk_date():
    assert to_vk_date("2026-07-01") == "2026-07-01T00:00:00Z"
    assert to_vk_date("") is None
    assert to_vk_date(None) is None
    assert to_vk_date("2026-07-01T09:30:00Z") == "2026-07-01T09:30:00Z"  # passthrough


def test_to_vk_html():
    assert "<strong>hi</strong>" in to_vk_html("**hi**")
    assert to_vk_html("") == ""
    assert to_vk_html(None) == ""


def test_check_priority():
    for ok in (None, 0, 3, 5):
        check_priority(ok)
    for bad in (-1, 6, 99):
        with pytest.raises(VikunjaError):
            check_priority(bad)


# --- helpers for transport-backed tests -------------------------------------
def make_client(handler):
    return VikunjaClient("https://vk.test", "tok", transport=httpx.MockTransport(handler))


def body_of(request: httpx.Request) -> dict:
    return json.loads(request.content)


# --- project resolution -----------------------------------------------------
def test_resolve_project_by_name_case_insensitive():
    def handler(req):
        assert req.url.path == "/api/v1/projects"
        return httpx.Response(200, json=[{"id": 3, "title": "Home"}, {"id": 7, "title": "Work"}])

    c = make_client(handler)
    assert c.resolve_project_id("work") == 7


def test_resolve_project_id_passthrough_no_http():
    def handler(req):  # should never be called for an int id
        raise AssertionError("no HTTP expected when an id is given")

    assert make_client(handler).resolve_project_id(5) == 5


def test_resolve_project_not_found():
    def handler(req):
        return httpx.Response(200, json=[{"id": 1, "title": "Other"}])

    with pytest.raises(VikunjaError):
        make_client(handler).resolve_project_id("missing")


# --- task fetch: view selection, pagination, dedupe -------------------------
def test_get_project_tasks_pagination_and_dedupe():
    seen_pages = []

    def handler(req):
        p = req.url.path
        if p == "/api/v1/projects/7/views":
            return httpx.Response(200, json=[{"id": 11, "view_kind": "list"}, {"id": 12, "view_kind": "table"}])
        if p == "/api/v1/projects/7/views/11/tasks":
            page = int(req.url.params.get("page"))
            seen_pages.append(page)
            if page == 1:
                return httpx.Response(200, json=[{"id": i} for i in range(1, 51)])  # full page -> continue
            if page == 2:
                return httpx.Response(200, json=[{"id": 50}, {"id": 51}])  # overlap id 50, short -> stop
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected {p}")

    c = make_client(handler)
    tasks = c.get_project_tasks(7, include_done=False)
    assert [t["id"] for t in tasks] == list(range(1, 52))  # 1..51, deduped
    assert seen_pages == [1, 2]  # stopped after the short page


def test_include_done_uses_table_view():
    def handler(req):
        p = req.url.path
        if p == "/api/v1/projects/7/views":
            return httpx.Response(200, json=[{"id": 11, "view_kind": "list"}, {"id": 12, "view_kind": "table"}])
        if p == "/api/v1/projects/7/views/12/tasks":
            return httpx.Response(200, json=[{"id": 1, "done": True}])
        raise AssertionError(f"unexpected {p}")

    c = make_client(handler)
    assert [t["id"] for t in c.get_project_tasks(7, include_done=True)] == [1]


# --- writes -----------------------------------------------------------------
def test_add_task_builds_body_and_attaches_labels():
    captured = {}

    def handler(req):
        m, p = req.method, req.url.path
        if m == "PUT" and p == "/api/v1/projects/7/tasks":
            captured["task"] = body_of(req)
            return httpx.Response(200, json={"id": 42, **captured["task"]})
        if m == "GET" and p == "/api/v1/labels":
            return httpx.Response(200, json=[{"id": 1, "title": "docs"}])  # 'release' is missing
        if m == "PUT" and p == "/api/v1/labels":
            return httpx.Response(200, json={"id": 2, **body_of(req)})
        if m == "PUT" and p == "/api/v1/tasks/42/labels":
            captured.setdefault("attached", []).append(body_of(req)["label_id"])
            return httpx.Response(200, json={})
        if m == "GET" and p == "/api/v1/tasks/42":  # add_task re-reads once labels are attached
            return httpx.Response(200, json={"id": 42, **captured["task"], "labels": [
                {"id": i, "title": n} for i, n in zip(captured["attached"], ("docs", "release"))
            ]})
        raise AssertionError(f"unexpected {m} {p}")

    c = make_client(handler)
    t = c.add_task(7, "Write the docs", description="**hi**", priority=4, due="2026-07-01", labels=["docs", "release"])
    assert t["id"] == 42
    assert [lbl["title"] for lbl in t["labels"]] == ["docs", "release"]
    assert captured["task"]["title"] == "Write the docs"
    assert captured["task"]["priority"] == 4
    assert captured["task"]["due_date"] == "2026-07-01T00:00:00Z"
    assert "<strong>hi</strong>" in captured["task"]["description"]
    assert captured["attached"] == [1, 2]  # existing docs(1), then created release(2)


def test_add_task_returns_the_labels_it_attached():
    """Vikunja ignores labels on create, so the create response always shows none. add_task must
    re-read, or it reports labels:[] for labels it successfully attached (the bug this guards)."""
    def handler(req):
        m, p = req.method, req.url.path
        if m == "PUT" and p == "/api/v1/projects/7/tasks":
            return httpx.Response(200, json={"id": 42, "title": "t"})  # note: no labels echoed
        if m == "GET" and p == "/api/v1/labels":
            return httpx.Response(200, json=[{"id": 1, "title": "docs"}])
        if m == "PUT" and p == "/api/v1/tasks/42/labels":
            return httpx.Response(200, json={})
        if m == "GET" and p == "/api/v1/tasks/42":  # the re-read, post-attach
            return httpx.Response(200, json={"id": 42, "title": "t", "labels": [{"id": 1, "title": "docs"}]})
        raise AssertionError(f"unexpected {m} {p}")

    t = make_client(handler).add_task(7, "t", labels=["docs"])
    assert [lbl["title"] for lbl in t["labels"]] == ["docs"]


def test_add_task_without_labels_does_not_re_read():
    """The re-read costs a request; it must only happen when there were labels to attach."""
    def handler(req):
        m, p = req.method, req.url.path
        if m == "PUT" and p == "/api/v1/projects/7/tasks":
            return httpx.Response(200, json={"id": 42, "title": "t"})
        raise AssertionError(f"unexpected extra request: {m} {p}")

    assert make_client(handler).add_task(7, "t")["id"] == 42


def test_attach_labels_swallows_duplicate_but_raises_real_failures():
    """400 means 'already on the task' — benign. Anything else is a real failure and must surface."""
    def handler_for(status, message):
        def handler(req):
            m, p = req.method, req.url.path
            if m == "PUT" and p == "/api/v1/projects/7/tasks":
                return httpx.Response(200, json={"id": 42, "title": "t"})
            if m == "GET" and p == "/api/v1/labels":
                return httpx.Response(200, json=[{"id": 1, "title": "docs"}])
            if m == "PUT" and p == "/api/v1/tasks/42/labels":
                return httpx.Response(status, json={"message": message})
            if m == "GET" and p == "/api/v1/tasks/42":
                return httpx.Response(200, json={"id": 42, "title": "t", "labels": []})
            raise AssertionError(f"unexpected {m} {p}")
        return handler

    # duplicate -> swallowed, add_task still succeeds
    c = make_client(handler_for(400, "This label already exists on the task."))
    assert c.add_task(7, "t", labels=["docs"])["id"] == 42

    # missing label / no scope -> must NOT be hidden
    for status in (403, 404, 500):
        c = make_client(handler_for(status, "nope"))
        with pytest.raises(VikunjaError) as ei:
            c.add_task(7, "t", labels=["docs"])
        assert ei.value.status == status


def test_update_is_read_modify_write_and_clears_due():
    posted = {}

    def handler(req):
        m, p = req.method, req.url.path
        if m == "GET" and p == "/api/v1/tasks/42":
            return httpx.Response(200, json={
                "id": 42, "title": "old", "description": "<p>x</p>",
                "priority": 2, "due_date": "2026-01-01T00:00:00Z", "done": False,
            })
        if m == "POST" and p == "/api/v1/tasks/42":
            posted.update(body_of(req))
            return httpx.Response(200, json=posted)
        raise AssertionError(f"unexpected {m} {p}")

    c = make_client(handler)
    c.update_task(42, priority=5, due="")  # bump priority, clear due, leave title/description
    assert posted["title"] == "old"        # untouched
    assert posted["description"] == "<p>x</p>"  # untouched
    assert posted["priority"] == 5         # changed
    assert posted["due_date"] is None      # cleared


def test_set_done_reads_then_posts():
    posted = {}

    def handler(req):
        m, p = req.method, req.url.path
        if m == "GET" and p == "/api/v1/tasks/42":
            return httpx.Response(200, json={"id": 42, "title": "t", "done": False})
        if m == "POST" and p == "/api/v1/tasks/42":
            posted.update(body_of(req))
            return httpx.Response(200, json=posted)
        raise AssertionError(f"unexpected {m} {p}")

    make_client(handler).set_done(42, True)
    assert posted["done"] is True


# --- error surfacing --------------------------------------------------------
def test_http_error_carries_status():
    def handler(req):
        return httpx.Response(403, json={"message": "forbidden"})

    with pytest.raises(VikunjaError) as ei:
        make_client(handler).get_task(1)
    assert ei.value.status == 403
