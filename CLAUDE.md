# CLAUDE.md

Guidance for Claude Code working in this repo.

## What this is

`vikunja-mcp` — a local **stdio MCP server** (Python + FastMCP) that exposes Vikunja task
operations as typed tools: list / get / add / update / complete / reopen, plus `check_connection`.
**Write-only — there is no delete tool, by design.** This server is the only supported way to reach
Vikunja from Claude.

## Layout

- `vikunja_mcp/config.py` — environment parsing; the API token is read from the process env only.
- `vikunja_mcp/client.py` — `VikunjaClient`: the REST logic (view-based paginated fetch, label
  create-then-attach, read-modify-write updates, date coercion, markdown→HTML descriptions).
- `vikunja_mcp/server.py` — FastMCP instance, the `@mcp.tool` definitions, `main()`.
- `tests/test_client.py` — pure-function + `httpx.MockTransport` tests (no live server needed).

## Working on it

Provision and run with **uv** (it fetches a matching Python and the deps):

```bash
uv sync
uv run pytest
```

After changing the tools, confirm they still register with valid schemas:

```bash
uv run python -c "import asyncio; from vikunja_mcp import server; print([t.name for t in asyncio.run(server.mcp.list_tools())])"
```

## Conventions

- **Token: session env only** (`VIKUNJA_API_TOKEN`) — never a file, never `.mcp.json`. The server
  reads it from the process environment inherited from the shell that launched the MCP client.
- **Config via env**: `VIKUNJA_URL` (required http(s) base URL), and an *optional* default project
  via `VIKUNJA_PROJECT_ID` (preferred) or `VIKUNJA_PROJECT`. Assume there is **no default** — one
  machine often spans several projects — so every project-scoped tool (`check_connection`,
  `list_tasks`, `add_task`) takes a `project_id` that overrides the default, and errors asking the
  user to name a project when neither exists. The task-id tools (`get`/`update`/`complete`/`reopen`)
  hit `/tasks/{id}`, which is global: they take no project, and must not grow one.
- **Minimal token scope**: every call must work with a project-scoped token. Never add a code path
  that needs 'read all projects' (`GET /projects`) — the name→id lookup in `resolve_project_id` is
  the one grandfathered exception, which is why `VIKUNJA_PROJECT_ID` is preferred over the name.
- **Descriptions are HTML, not markdown.** Vikunja's `description` field holds the HTML its WYSIWYG
  editor produces — that is the storage format, and reads return it verbatim. Markdown is only ever
  an *input* convenience: `to_vk_html` converts it on the way in, and nothing converts back on the
  way out. So the tools are asymmetric by design (write markdown, read HTML) and the docstrings must
  keep saying so. Re-converting HTML is a no-op (python-markdown passes block-level HTML through
  untouched), which is what makes `update_task`'s read-modify-write safe — keep it that way.
- **Write-only**: never add a delete tool.
- **Python stays LF** (pinned in `.gitattributes`). Commit `uv.lock` for reproducible installs.

## Git

- **Commit automatically** once a piece of work is complete — no need to ask. Do **not** add a
  `Co-Authored-By` / "Generated with Claude Code" trailer.
- **Pushing and pulling are operator-only.**
