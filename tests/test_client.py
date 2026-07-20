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
    from_vk_html,
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


def test_from_vk_html():
    """The read-side inverse: Vikunja's stored HTML back to markdown, empties preserved."""
    assert from_vk_html("<p>hello <strong>world</strong></p>") == "hello **world**"
    assert from_vk_html("<h1>Title</h1>") == "# Title"  # ATX heading, matches to_vk_html's input
    assert from_vk_html("") == ""
    assert from_vk_html(None) == ""


def test_description_markdown_round_trips_through_storage():
    """What add_task/update_task store (to_vk_html) is what get_task hands back (from_vk_html),
    for the common prose/emphasis case — so a caller can read, tweak, and write markdown."""
    assert from_vk_html(to_vk_html("hello **world**")) == "hello **world**"


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


# --- project listing --------------------------------------------------------
def test_list_projects_returns_id_title_pairs():
    def handler(req):
        assert req.url.path == "/api/v1/projects"
        return httpx.Response(200, json=[
            {"id": 11, "title": "Alpha", "description": "noise"},
            {"id": 7, "title": "Beta"},
            {"id": 0, "title": "pseudo -1 (falsy id, dropped)"},
        ])

    assert make_client(handler).list_projects() == [{"id": 11, "title": "Alpha"}, {"id": 7, "title": "Beta"}]


def test_list_projects_403_surfaces_for_check_connection_to_handle():
    def handler(req):
        return httpx.Response(403, json={"message": "forbidden"})

    with pytest.raises(VikunjaError) as ei:
        make_client(handler).list_projects()
    assert ei.value.status == 403


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


def _append_client(existing: str, posted: dict):
    def handler(req):
        m, p = req.method, req.url.path
        if m == "GET" and p == "/api/v1/tasks/42":
            return httpx.Response(200, json={"id": 42, "title": "t", "description": existing})
        if m == "POST" and p == "/api/v1/tasks/42":
            posted.update(body_of(req))
            return httpx.Response(200, json=posted)
        raise AssertionError(f"unexpected {m} {p}")

    return make_client(handler)


def test_description_append_concatenates_html_without_reconverting():
    posted: dict = {}
    _append_client("<p>first</p>", posted).update_task(42, description_append="**second**")
    # existing HTML kept verbatim, new chunk converted and appended
    assert posted["description"] == "<p>first</p><p><strong>second</strong></p>"


def test_description_append_onto_empty_description():
    posted: dict = {}
    _append_client("", posted).update_task(42, description_append="hello")
    assert posted["description"] == "<p>hello</p>"  # no stray leading whitespace/markup


def test_description_append_is_repeatable():
    """Appending twice must accumulate — the property that lets a long description be built up."""
    html = ""
    for chunk in ("one", "two", "three"):
        posted: dict = {}
        _append_client(html, posted).update_task(42, description_append=chunk)
        html = posted["description"]
    assert html == "<p>one</p><p>two</p><p>three</p>"


def test_description_and_append_together_is_an_error():
    posted: dict = {}
    with pytest.raises(VikunjaError, match="not both"):
        _append_client("<p>x</p>", posted).update_task(42, description="a", description_append="b")
    assert not posted  # nothing written


def test_description_append_leaves_other_fields_alone():
    posted: dict = {}
    _append_client("<p>x</p>", posted).update_task(42, description_append="y")
    assert posted["title"] == "t"


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
