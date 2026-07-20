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
  create-then-attach, read-modify-write updates, date coercion, and description conversion —
  `to_vk_html` markdown→HTML on the way in, `from_vk_html` HTML→markdown on the way out).
- `vikunja_mcp/server.py` — FastMCP instance, the `@mcp.tool` definitions, `main()`.
- `tests/` — no live server needed. `test_client.py` (pure functions + `httpx.MockTransport`),
  `test_config.py` (env parsing), `test_server.py` (tool layer + `_fmt_task` shaping).
- `packaging/mcpb/` — the Claude Desktop / cowork **MCP bundle** manifest (`.mcpb`). Build it with
  Anthropic's official `@anthropic-ai/mcpb` CLI (`mcpb validate` + `mcpb pack`), never a hand-zipped
  archive — `pack` schema-checks the manifest first. The token rides a `sensitive` `user_config`
  field (→ OS keychain), the one client where the shell-env token model doesn't apply.

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
  machine often spans several projects — so `list_tasks` and `add_task` take a `project_id` that
  overrides the default, and error asking the user to name a project when neither exists.
  `check_connection`'s `project_id` is **optional**: with one it does a task read against it; with
  none it lists the projects the token can see (see minimal-scope below). The task-id tools
  (`get`/`update`/`complete`/`reopen`) hit `/tasks/{id}`, which is global: they take no project, and
  must not grow one.
- **Minimal token scope**: the *task* tools must all work with a project-scoped token. Never add a
  code path in `list_tasks`/`add_task`/the task-id tools that needs 'read all projects'
  (`GET /projects`). There are exactly **two** sanctioned uses of that scope, both outside the task
  tools: the name→id lookup in `resolve_project_id` (why `VIKUNJA_PROJECT_ID` is preferred over the
  name), and `check_connection`'s no-project discovery path (`list_projects`). Both degrade
  gracefully — a token without the scope gets a 403 there and `check_connection` says "pass a
  project_id instead", so the minimal-token setup still works everywhere that matters.
- **Descriptions: markdown in BOTH directions.** Vikunja's `description` field stores the HTML its
  WYSIWYG editor produces — that is the storage format — but the tools present markdown at the
  boundary in both directions: `to_vk_html` converts markdown→HTML on writes, `from_vk_html`
  converts the stored HTML→markdown on reads (`get_task`). The docstrings must keep saying "markdown
  both ways". The back-conversion is a display convenience, so it may render exotic editor markup
  (tables, checkboxes) as rough markdown — that is expected, not a bug. Keep `update_task`'s
  read-modify-write on the **raw stored HTML** (it concatenates/replaces HTML directly and never
  re-reads through `from_vk_html`), so appends stay exact regardless of read-side conversion.
- **Write-only**: never add a delete tool.
- **Python stays LF** (pinned in `.gitattributes`). Commit `uv.lock` for reproducible installs.

## Git

- **Commit automatically** once a piece of work is complete — no need to ask. Do **not** add a
  `Co-Authored-By` / "Generated with Claude Code" trailer.
- **Anything that reaches the remote is operator-only** — not just push/pull, but `fetch`, `clone`,
  `ls-remote`, and anything that contacts `origin` *indirectly*: `uvx --from git+ssh://…`,
  `uv tool install` from a git URL, any command that clones behind your back. `origin` is SSH to a
  self-hosted Gitea behind a **hardware key**, so every remote call demands a physical touch from
  the operator — an agent cannot complete one, it can only interrupt them. A declined touch hangs
  until timeout and leaves a 0-byte `.git/FETCH_HEAD`, which is *not* a real sync: `origin/main`
  stays stale, so "ahead N" from `git status` can be measured against a ref that no longer matches
  the remote. Ask; never initiate. Local git (status, log, diff, commit, rebase) is unrestricted.
