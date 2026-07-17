"""Thin Vikunja REST client — only the endpoints the task tools need.

Write-only by design: no delete. The quirks this handles, each forced by Vikunja's API:
view-based paginated fetch, label create-then-attach, read-modify-write updates, date
coercion, markdown->HTML descriptions.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import markdown as _markdown

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UNSET: Any = object()  # sentinel: "argument not provided" vs. an explicit "" (which clears)


class VikunjaError(RuntimeError):
    """A Vikunja API/transport failure. ``status`` is the HTTP code when there was a response."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def to_vk_date(d: str | None) -> str | None:
    """Bare ``yyyy-MM-dd`` -> RFC3339 midnight UTC; passthrough otherwise; None/'' -> None."""
    if not d:
        return None
    if _DATE_RE.match(d):
        return f"{d}T00:00:00Z"
    return d


def to_vk_html(md: str | None) -> str:
    """Markdown -> HTML for Vikunja's WYSIWYG description field (empty stays empty)."""
    if not md:
        return ""
    return _markdown.markdown(md, extensions=["fenced_code", "tables"])


def check_priority(priority: int | None) -> None:
    if priority is not None and not (0 <= priority <= 5):
        raise VikunjaError(
            "priority must be 0..5 (0=Unset 1=Low 2=Medium 3=High 4=Urgent 5=DO NOW)"
        )


class VikunjaClient:
    """Minimal REST wrapper. Construct with a base URL + token; inject a transport in tests."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ):
        # Build full URLs ourselves (base + /api/v1 + path) rather than httpx base_url joining,
        # which would drop the /api/v1 prefix for leading-slash paths (RFC 3986 merge).
        self._base = base_url.rstrip("/")
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "VikunjaClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- low-level -----------------------------------------------------------
    def _request(self, method: str, path: str, *, params: dict | None = None, json: Any | None = None) -> Any:
        url = f"{self._base}/api/v1{path}"
        try:
            r = self._http.request(method, url, params=params, json=json)
        except httpx.HTTPError as e:
            raise VikunjaError(f"{method} {path} failed: {e}") from e
        if r.status_code >= 400:
            detail = ""
            try:
                detail = str(r.json().get("message", "")).strip()
            except Exception:
                detail = r.text[:200].strip()
            raise VikunjaError(f"{method} {path} -> HTTP {r.status_code} {detail}".rstrip(), status=r.status_code)
        if not r.content:
            return None
        return r.json()

    # --- projects ------------------------------------------------------------
    def resolve_project_id(self, project: int | str | None) -> int:
        """Return a numeric project id. An int is used directly (no /projects lookup, so the
        token needs no 'read all projects' scope); a name triggers the lookup."""
        if isinstance(project, int) and project > 0:
            return project
        if isinstance(project, str) and project.strip():
            projects = self._request("GET", "/projects") or []
            for p in projects:
                if str(p.get("title", "")).lower() == project.strip().lower():
                    return int(p["id"])
            names = ", ".join(str(p.get("title", "")) for p in projects)
            raise VikunjaError(
                f"project '{project}' not found. Available: {names}. Share it with the service account."
            )
        raise VikunjaError(
            "no project given — pass project_id/project or set VIKUNJA_PROJECT_ID / VIKUNJA_PROJECT."
        )

    # --- tasks: read ---------------------------------------------------------
    def get_project_tasks(self, project_id: int, include_done: bool = False) -> list[dict]:
        """Stable, fully-paginated fetch via the project's VIEW endpoint (the legacy
        /projects/{id}/tasks is unreliable in Vikunja 2.x). The List view carries a
        server-side done=false filter, so include_done uses the (unfiltered) Table view."""
        views = self._request("GET", f"/projects/{project_id}/views") or []
        want = "table" if include_done else "list"
        view_id = next((v.get("id") for v in views if str(v.get("view_kind")) == want), None)
        if view_id is None and views:
            view_id = views[0].get("id")
        if view_id is None:
            return []

        all_tasks: list[dict] = []
        page = 1
        while True:
            batch = self._request(
                "GET",
                f"/projects/{project_id}/views/{view_id}/tasks",
                params={"page": page, "per_page": 50},
            )
            batch = [t for t in (batch or []) if t]
            if not batch:
                break
            all_tasks.extend(batch)
            if len(batch) < 50:
                break
            page += 1

        seen: set = set()
        deduped: list[dict] = []
        for t in all_tasks:
            tid = t.get("id")
            if tid in seen:
                continue
            seen.add(tid)
            deduped.append(t)
        return deduped

    def get_task(self, task_id: int) -> dict:
        return self._request("GET", f"/tasks/{task_id}")

    # --- labels (global; create-if-missing, then attach) ---------------------
    def _resolve_label_id(self, title: str) -> int:
        labels = self._request("GET", "/labels", params={"per_page": 100}) or []
        for label in labels:
            if str(label.get("title", "")).lower() == title.lower():
                return int(label["id"])
        created = self._request("PUT", "/labels", json={"title": title})
        return int(created["id"])

    def _attach_labels(self, task_id: int, labels: list[str]) -> None:
        for name in labels:
            name = name.strip()
            if not name:
                continue
            lid = self._resolve_label_id(name)
            try:
                self._request("PUT", f"/tasks/{task_id}/labels", json={"label_id": lid})
            except VikunjaError as e:
                # 400 is Vikunja's "This label already exists on the task." — the one benign
                # outcome, since attaching is idempotent from the caller's point of view.
                # Everything else (403 missing label scope, 404, 5xx) is a REAL failure: let it
                # out. Swallowing those made a broken attach look identical to a working one.
                if e.status != 400:
                    raise

    # --- tasks: write --------------------------------------------------------
    def add_task(
        self,
        project_id: int,
        title: str,
        *,
        description: str | None = None,
        priority: int | None = None,
        due: str | None = None,
        labels: list[str] | None = None,
    ) -> dict:
        check_priority(priority)
        body: dict[str, Any] = {"title": title}
        if description:
            body["description"] = to_vk_html(description)
        if priority is not None:
            body["priority"] = priority
        due_rfc = to_vk_date(due)
        if due_rfc:
            body["due_date"] = due_rfc
        # Vikunja IGNORES `labels` in the create body, so they must be attached afterwards —
        # which means the create response predates them and always shows labels: []. Re-read
        # once so the caller sees what is actually on the task.
        task = self._request("PUT", f"/projects/{project_id}/tasks", json=body)
        if labels:
            tid = int(task["id"])
            self._attach_labels(tid, labels)
            task = self.get_task(tid)
        return task

    def update_task(
        self,
        task_id: int,
        *,
        title: Any = _UNSET,
        description: Any = _UNSET,
        priority: int | None = None,
        due: Any = _UNSET,
        labels: list[str] | None = None,
    ) -> dict:
        """Read-modify-write: only provided fields change. ``description=""`` clears the
        description; ``due=""`` clears the due date."""
        check_priority(priority)
        task = self.get_task(task_id)
        if title is not _UNSET:
            task["title"] = title
        if description is not _UNSET:
            task["description"] = to_vk_html(description) if description else ""
        if priority is not None:
            task["priority"] = priority
        if due is not _UNSET:
            task["due_date"] = to_vk_date(due)  # "" -> None -> clears
        result = self._request("POST", f"/tasks/{task_id}", json=task)
        if labels:
            self._attach_labels(task_id, labels)
            result = self.get_task(task_id)  # same reason as add_task: the POST predates the attach
        return result

    def set_done(self, task_id: int, done: bool) -> dict:
        task = self.get_task(task_id)
        task["done"] = done
        return self._request("POST", f"/tasks/{task_id}", json=task)

    # --- readiness probe -----------------------------------------------------
    def probe(self, project: int | str | None) -> dict:
        """Resolve the project and do a task READ (same calls `list` makes) to prove access."""
        pid = self.resolve_project_id(project)
        views = self._request("GET", f"/projects/{pid}/views") or []
        if views:
            vid = views[0].get("id")
            if vid is not None:
                self._request(
                    "GET",
                    f"/projects/{pid}/views/{vid}/tasks",
                    params={"page": 1, "per_page": 1},
                )
        return {"project_id": pid}
