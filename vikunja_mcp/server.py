"""FastMCP stdio server exposing Vikunja task tools. Write-only — no delete tool by design.

Config comes from the environment (see ``config.py``); the API token is read from the process
env only. TLS verification uses the machine's OS trust store (``truststore``), so an internal CA
that is already installed on the machine just works — the server manages no cert material.
"""

from __future__ import annotations

import sys

import truststore
from mcp.server.fastmcp import FastMCP

from .client import VikunjaClient, VikunjaError, from_vk_html
from .config import TOKEN_SETUP_HELP, Config, config_issues, load_config

INSTRUCTIONS = """Manage tasks in a Vikunja instance: list, get, add, update, complete, reopen.
Write-only — there is NO delete tool, by design. Configuration comes from the environment
(VIKUNJA_URL, VIKUNJA_API_TOKEN, and OPTIONALLY a default project via VIKUNJA_PROJECT_ID or
VIKUNJA_PROJECT). If a tool reports the token is missing, it is never stored in config: the operator
must set it in the shell they launch Claude from, then relaunch. Relay the prompt-for-it command in
that error verbatim — never tell them to type the token inline, which would leak it to shell history.

A default project is often deliberately NOT set, because one machine works across several projects.
So list_tasks and add_task take a project_id, which overrides any default. If a tool says it needs a
project, ASK the user which Vikunja project to use (prefer the numeric project id) and pass it as
project_id — do not guess, and do not carry a project id over from an earlier, unrelated request.
check_connection takes an OPTIONAL project_id: call it with no project to list the projects the token
can see (a good way to discover which id to pass), or with one to verify a task read against it.
get_task, update_task, complete_task and reopen_task take no project: a task id is global, and
identifies the task on its own.

When you MENTION a task to the user, name it by its `identifier` (the per-project ref the Vikunja UI
shows, e.g. "HL-12") — that is the only number they can see. The global `id` is a tool handle: pass
it as task_id, but keep it out of your prose. Fall back to the id only if identifier is empty.

Descriptions are markdown in BOTH directions. You write markdown (add_task/update_task convert it to
the HTML Vikunja stores) and you read markdown (get_task converts the stored HTML back). So do NOT
hand-write HTML tags into a description, and do NOT expect tags out of get_task — just read and write
markdown. Passing a description straight back from get_task into update_task is safe. The conversion
back is a rendering convenience, so exotic editor markup (a table, a checkbox) may come out as
slightly rough markdown — describe what it says, do not treat rough markdown as an error."""

mcp = FastMCP("vikunja", instructions=INSTRUCTIONS)


def _client(cfg: Config) -> VikunjaClient:
    return VikunjaClient(cfg.base_url, cfg.token)


def _require_ready(cfg: Config) -> None:
    issues = config_issues(cfg)
    if issues:
        raise VikunjaError("not configured: " + " ".join(issues))


def _resolve_project(c: VikunjaClient, project_id: int | None, cfg: Config) -> int:
    """Resolve the project to act on. When neither an explicit project_id nor a configured
    default exists, raise a message that directs Claude to ASK the user rather than guess —
    the client surfaces the tool error, and Claude acts on it."""
    target = project_id if project_id else cfg.default_project
    if not target:
        raise VikunjaError(
            "No Vikunja project was given and none is configured (VIKUNJA_PROJECT_ID / "
            "VIKUNJA_PROJECT are unset). Ask the user which project to use — prefer the numeric "
            "project id — then call this tool again with project_id set. Do not guess a project."
        )
    return c.resolve_project_id(target)


def _fmt_date(value: object, *, date_only: bool = False) -> str:
    """Vikunja spells "unset" as the zero time ``0001-01-01T00:00:00Z``. Return "" for that, so
    nothing downstream renders a year-1 timestamp as a real date. Otherwise pass the RFC3339
    string through, or trim it to ``yyyy-MM-dd`` for the fields we keep date-only."""
    s = str(value or "")
    if not s or s.startswith("0001"):
        return ""
    return s[:10] if date_only else s


def _fmt_related(related: object) -> dict:
    """Trim ``related_tasks`` to references.

    Vikunja nests a WHOLE task under each relation kind — description included, and its own
    ``related_tasks`` besides. Passing that through would put a task's full body (and, here,
    megabyte descriptions) inside every other task that links to it. Keep only enough to name
    the task and fetch it with get_task. ``identifier`` is usually "" on these nested copies,
    which is why ``id`` is the reliable handle."""
    out: dict[str, list[dict]] = {}
    for kind, tasks in (related or {}).items():  # type: ignore[union-attr]
        refs = [
            {
                "id": rt.get("id"),
                "identifier": rt.get("identifier") or "",
                "title": rt.get("title"),
                "done": bool(rt.get("done")),
            }
            for rt in (tasks or [])
        ]
        if refs:  # drop empty kinds; Vikunja sends {} when there are no relations at all
            out[str(kind)] = refs
    return out


def _fmt_task(t: dict) -> dict:
    """Trim a raw Vikunja task to the fields worth returning.

    ``id`` is global and is what every tool's ``task_id`` takes. ``index``/``identifier`` are the
    per-project number the UI shows (e.g. 12 / "HL-12") — display only; they are NOT interchangeable
    with ``id`` and passing one as ``task_id`` silently addresses a different task.

    Everything here is already in the payload Vikunja sends, so none of it costs an extra request.
    The raw task's remaining fields are deliberately dropped: bucket_id/position/
    cover_image_attachment_id/reactions are kanban and UI state with nothing in them for a caller."""
    return {
        "id": t.get("id"),
        "index": t.get("index"),
        "identifier": t.get("identifier"),
        "project_id": t.get("project_id"),
        "title": t.get("title"),
        "done": bool(t.get("done")),
        "done_at": _fmt_date(t.get("done_at")),
        "priority": t.get("priority") or 0,
        "due": _fmt_date(t.get("due_date"), date_only=True),
        "start_date": _fmt_date(t.get("start_date")),
        "end_date": _fmt_date(t.get("end_date")),
        "labels": [lbl.get("title") for lbl in (t.get("labels") or [])],
        # Usernames, not ids: this token cannot resolve a user (GET /user is 401), so an id here
        # would be a handle nothing can look up. username is always set; name is often "".
        "assignees": [a.get("username") or a.get("name") or "" for a in (t.get("assignees") or [])],
        "related_tasks": _fmt_related(t.get("related_tasks")),
        # A reminder is EITHER absolute (reminder set, relative_to "") or relative to another date
        # field (relative_to + relative_period seconds, and reminder left at the zero time until
        # Vikunja resolves it). Keep all three or the difference is invisible.
        "reminders": [
            {
                "reminder": _fmt_date(r.get("reminder")),
                "relative_to": r.get("relative_to") or "",
                "relative_period": r.get("relative_period") or 0,
            }
            for r in (t.get("reminders") or [])
        ],
        "repeat_after": t.get("repeat_after") or 0,
        "repeat_mode": t.get("repeat_mode") or 0,
        "created": _fmt_date(t.get("created")),
        "updated": _fmt_date(t.get("updated")),
    }


@mcp.tool()
def check_connection(project_id: int | None = None) -> dict:
    """Verify the server can reach Vikunja with the configured URL + token.

    project_id (which overrides any configured default) picks HOW the token is proven:
      - WITH a project — task READ against it, using only project-scoped permissions. Prefer this;
        it verifies exactly the scope list_tasks/add_task need.
      - WITHOUT a project — lists the projects the token can see (GET /projects). This proves the
        token AND returns the projects to choose from, so this is what to run when the user has not
        named a project yet. It needs the 'read all projects' scope; a token scoped to specific
        projects gets a 403 here, reported as 'pass a project_id instead' — not a failure of setup.

    Returns {ready: true, url, project|projects, ...} on success, else {ready: false, issues: [...]}
    with the specific fix (token/URL/scope). Run this first if anything seems off."""
    cfg = load_config()
    issues = config_issues(cfg)
    if issues:
        return {"ready": False, "issues": issues}
    target = project_id if project_id else cfg.default_project
    try:
        with _client(cfg) as c:
            if target:
                info = c.probe(target)
                return {
                    "ready": True,
                    "url": cfg.base_url,
                    "project": info["project_id"],
                    "note": "task READ verified; WRITE scope (add/update/complete) is only exercised on an actual write.",
                }
            projects = c.list_projects()
            return {
                "ready": True,
                "url": cfg.base_url,
                "projects": projects,
                "note": "token verified by listing projects; no default is configured, so pass one of "
                "these ids as project_id to list_tasks/add_task.",
            }
    except VikunjaError as e:
        hint = ""
        if e.status == 401:
            hint = " (token invalid or expired — reset VIKUNJA_API_TOKEN and relaunch)"
        elif e.status == 403 and target:
            hint = " (token lacks scope for this route; with a project id you do NOT need 'read all projects')"
        elif e.status == 403:
            hint = " (token cannot 'read all projects' — pass a project_id to verify against one project instead)"
        return {"ready": False, "issues": [str(e) + hint]}


@mcp.tool()
def list_tasks(project_id: int | None = None, include_done: bool = False) -> list[dict]:
    """List tasks in a Vikunja project (open only unless include_done=true).

    Uses VIKUNJA_PROJECT_ID/VIKUNJA_PROJECT when project_id is omitted. Sorted open-first,
    then priority (high first), then id. Returns the same fields as get_task EXCEPT
    `description` — list a project to find tasks, then get_task the one you want to read."""
    cfg = load_config()
    _require_ready(cfg)
    with _client(cfg) as c:
        pid = _resolve_project(c, project_id, cfg)
        tasks = c.get_project_tasks(pid, include_done=include_done)
    if not include_done:
        tasks = [t for t in tasks if not t.get("done")]
    tasks.sort(key=lambda t: (bool(t.get("done")), -(t.get("priority") or 0), t.get("id") or 0))
    return [_fmt_task(t) for t in tasks]


@mcp.tool()
def get_task(task_id: int) -> dict:
    """Get a single task by its global id, including its description.

    `description` comes back as **markdown**: Vikunja stores it as HTML (its editor is WYSIWYG) and
    this converts it back for you, so writes and reads are both markdown. To edit it, send markdown
    to update_task; sending this description straight back is safe. The back-conversion is a
    rendering convenience — unusual editor markup (tables, checkboxes) may read as rough markdown.

    task_id is the `id` field, NOT the `index`/`identifier` shown in the UI. To act on a task the
    user named by its UI number (e.g. "HL-12"), list the project and match on identifier/index first,
    then pass that task's `id` here.

    Fields that are READ-ONLY here — no tool writes them, so report them, do not promise to change
    them: `assignees` (usernames), `related_tasks`, `reminders`, `repeat_after`/`repeat_mode`,
    `done_at`, `start_date`/`end_date`, `created`/`updated`, `project_id`.

    `project_id` says which project the task is in — useful because this tool takes no project, so
    it is otherwise unknowable from a task id alone. `related_tasks` maps a relation kind
    ("subtask", "parenttask", "related", ...) to task REFERENCES; use each `id` with get_task to
    read one, since their `identifier` is usually empty. A `reminder` with `relative_to` set fires
    relative to that field (`relative_period` seconds, negative = before) and its `reminder` may be
    "" until Vikunja resolves it; an absolute reminder has `reminder` set and `relative_to` "".
    `repeat_after` is seconds (0 = does not repeat). Empty string means unset for every date."""
    cfg = load_config()
    _require_ready(cfg)
    with _client(cfg) as c:
        t = c.get_task(task_id)
    out = _fmt_task(t)
    out["description"] = from_vk_html(t.get("description"))
    return out


@mcp.tool()
def add_task(
    title: str,
    project_id: int | None = None,
    description: str | None = None,
    priority: int | None = None,
    due: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    """Create a task in a Vikunja project. Returns the created task, labels included.

    priority is 0..5 (0=Unset 1=Low 2=Medium 3=High 4=Urgent 5=DO NOW). due is `yyyy-MM-dd`.

    `labels` are plain names, matched case-insensitively against existing labels and CREATED if
    missing, then attached — so a typo silently makes a new label rather than failing. Vikunja
    ignores labels on create, so they are attached in a second step and the task is re-read: the
    labels you get back are what is really on the task. The task is created BEFORE the labels are
    attached, so if label attachment errors the task still exists — do not retry the whole create,
    or you will get a duplicate task; use update_task(labels=[...]) to finish the job.

    Pass `description` as **markdown** — it is converted to the HTML Vikunja's description field
    stores. Do not hand-write HTML tags: you would be writing them into a markdown input. Reads
    (get_task) convert the stored HTML back to markdown, so descriptions are markdown both ways.

    Description length: neither this server nor Vikunja imposes a practical limit (~1MB is fine).
    The real ceiling is YOUR OWN output budget for one tool call — the description is text you have
    to emit, and an over-long call is truncated before it ever reaches this server, so the failure
    looks like the tool erroring rather than a task being rejected. Writing the text to a file first
    does not help: that costs the same tokens. If you have a lot of material, summarize it and link
    to the source rather than pasting it in wholesale."""
    cfg = load_config()
    _require_ready(cfg)
    with _client(cfg) as c:
        pid = _resolve_project(c, project_id, cfg)
        t = c.add_task(pid, title, description=description, priority=priority, due=due, labels=labels)
    return _fmt_task(t)


@mcp.tool()
def update_task(
    task_id: int,
    title: str | None = None,
    description: str | None = None,
    description_append: str | None = None,
    priority: int | None = None,
    due: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    """Update a task; only the fields you pass change (read-modify-write). Pass due="" to clear
    the due date, description="" to clear the description. Returns the updated task.

    Pass `description` as **markdown** — it is converted to the HTML Vikunja stores. Handing back
    the markdown that get_task returned is safe (it just converts to HTML again), so you can read a
    description, tweak it, and write it back. Descriptions are markdown in both directions.

    `description_append` adds markdown to the END of the description instead of replacing it, and
    is how you write a description too long to fit in one tool call: send the first part via
    add_task/update_task, then append the rest across as many calls as you need. Each call only
    costs the chunk you send, so the description can grow far past what you could emit at once.
    Passing both `description` and `description_append` is an error — pick replace or grow.

    Split chunks on BLOCK boundaries (between paragraphs, list items, whole code fences). Each
    chunk is converted as standalone markdown, so a chunk cut mid-block — half a fenced code
    block, a table split across two calls — converts wrongly and cannot be repaired by the next
    chunk. `labels` are additive: they are attached, never removed."""
    cfg = load_config()
    _require_ready(cfg)
    changed: dict = {}
    if title is not None:
        changed["title"] = title
    if description is not None:
        changed["description"] = description
    if description_append is not None:
        changed["description_append"] = description_append
    if due is not None:
        changed["due"] = due
    with _client(cfg) as c:
        t = c.update_task(task_id, priority=priority, labels=labels, **changed)
    return _fmt_task(t)


@mcp.tool()
def complete_task(task_id: int) -> dict:
    """Mark a task done. Returns the updated task."""
    cfg = load_config()
    _require_ready(cfg)
    with _client(cfg) as c:
        return _fmt_task(c.set_done(task_id, True))


@mcp.tool()
def reopen_task(task_id: int) -> dict:
    """Reopen a completed task (mark not done). Returns the updated task."""
    cfg = load_config()
    _require_ready(cfg)
    with _client(cfg) as c:
        return _fmt_task(c.set_done(task_id, False))


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if "--token-help" in args:
        # Reprint the setup command without starting the server — a stable way for a session-start
        # reminder to fetch the exact command. Prints to stdout, so it must run BEFORE mcp.run(),
        # which would otherwise claim stdout for the stdio protocol. Force UTF-8 first: the message
        # has em-dashes that a Windows console code page would mangle for a capturing agent.
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
        print(TOKEN_SETUP_HELP)
        return
    truststore.inject_into_ssl()  # verify TLS against the OS trust store (internal CA lives there)
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
