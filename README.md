# vikunja-mcp

An **MCP server** that exposes tasks in a [Vikunja](https://vikunja.io) instance as typed tools —
list / get / add / update / complete / reopen — for Claude Code, Claude Desktop, or any MCP client.
Runs locally over **stdio** (the client launches it as a subprocess). **Write-only — there is no
delete tool, by design.**

The API **token comes only from the session environment** (never a config file) and the **project
is mandatory** (a default via env, or passed per-call).

## Prerequisites

Installation is **manual** — there's no installer. You need three things:

1. **[uv](https://docs.astral.sh/uv/)** — the only hard dependency. It provisions a matching Python
   (`requires-python >=3.11`) and installs the deps (`mcp`, `httpx`, `truststore`, `markdown`)
   itself, so a system Python/pip is optional. Install it once:
   ```powershell
   winget install astral-sh.uv          # Windows  (or:  irm https://astral.sh/uv/install.ps1 | iex)
   ```
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh    # Linux / macOS
   ```
2. **An MCP client** to register the server with — Claude Code or Claude Desktop.
3. **A Vikunja `service`-account API token** (scoped minimally: task read/create/update,
   project-views + view-tasks read, label read/create) plus your instance URL. With a **project ID**
   the token needs no 'read all projects' scope. Share the target project with that account.

The first `uv sync` needs network access (it fetches Python + the packages).

## Install

```bash
cd vikunja-mcp
uv sync            # creates the venv, provisions Python if needed, installs deps
uv run pytest      # optional: run the client tests
```

## Configure (environment only)

| Setting | Env var | Notes |
| --- | --- | --- |
| Instance URL | `VIKUNJA_URL` | **required**; the http(s) base URL of your instance |
| Default project by **ID** | `VIKUNJA_PROJECT_ID` | optional, preferred; no `/projects` lookup → minimal token |
| Default project by name | `VIKUNJA_PROJECT` | optional; its name→ID lookup needs 'read all projects' |
| Token | `VIKUNJA_API_TOKEN` | **secret — session env only**, see below |

- **Token — session env only, never persisted.** Set it in the shell/session you launch the MCP
  client from; the server the client spawns inherits it. **Do not put it in `.mcp.json`.** If it's
  missing, `check_connection` (and every write) reports the fix and stops.
  ```powershell
  $env:VIKUNJA_API_TOKEN = Read-Host -AsSecureString "Vikunja API token" | ConvertFrom-SecureString -AsPlainText   # PowerShell 7+
  ```
  ```bash
  read -rs -p "Vikunja API token: " VIKUNJA_API_TOKEN && export VIKUNJA_API_TOKEN   # bash
  read -rs "VIKUNJA_API_TOKEN?Vikunja API token: " && export VIKUNJA_API_TOKEN      # zsh (its -p means coprocess)
  ```
  Each prompts with echo off, so the token never reaches the command line or your shell history.
  A running client captured its environment at launch, so after setting it you must **relaunch** it.
- **Default project** is optional; a tool's `project_id` argument overrides it. Leave it unset if
  you work across **several projects** on this machine — then `check_connection`, `list_tasks` and
  `add_task` take the project per call, and **ask you which project to use** (prefer the numeric id)
  when you haven't said. Set a default only if one project dominates, to skip that prompt.
  `get_task`, `update_task`, `complete_task` and `reopen_task` need no project at all: a task id is
  global and identifies the task on its own.

## Register with an MCP client

**Claude Code** — either the CLI:

```bash
# --env VIKUNJA_PROJECT_ID is optional — omit it and the tools ask you which project per session
claude mcp add vikunja --scope user --env VIKUNJA_URL=https://your-vikunja-host --env VIKUNJA_PROJECT_ID=7 -- uv run --directory /abs/path/to/vikunja-mcp vikunja-mcp
```

…or copy [`.mcp.json.example`](.mcp.json.example) to `.mcp.json` (project scope, committable) and
edit the path/URL/project. **The token is deliberately absent from that file** — set it in your
shell (above). Then relaunch Claude and run `/mcp` (or `claude mcp list`) to confirm `vikunja` is
connected. For **Claude Desktop**, add the same `mcpServers` block to its config.

## Verify

Ask Claude to run `check_connection` — it returns `{ ready: true, url, project, ... }` once a task
**read** succeeds, or `{ ready: false, issues: [...] }` with the specific cause (token / URL /
project / 401 bad-token / 403 missing-scope). With no default project configured, name one
("check the Vikunja connection for project 7") — proving the token works means reading something,
and the project-less alternatives (`/projects`, `/tasks/all`) would demand a token scope the task
tools themselves never need. Then "list my open Vikunja tasks".

## Tools

| Tool | Does |
| --- | --- |
| `check_connection(project_id?)` | readiness probe (token + project reachable, task read verified) |
| `list_tasks(project_id?, include_done=false)` | open tasks (or all), sorted open→priority→id |
| `get_task(task_id)` | one task, including its description |
| `add_task(title, project_id?, description?, priority?, due?, labels?)` | create (priority 0..5; `due` = `yyyy-MM-dd`; `description` markdown; labels created-if-missing) |
| `update_task(task_id, title?, description?, priority?, due?, labels?)` | change only the passed fields; `due=""`/`description=""` clear |
| `complete_task(task_id)` / `reopen_task(task_id)` | mark done / not done |

Tasks are returned as structured JSON (id, title, done, priority, due, labels). Priority is
`0..5`: `0`=Unset `1`=Low `2`=Medium `3`=High `4`=Urgent `5`=DO NOW.

---

This MCP server was built with [Claude Code](https://claude.com/claude-code).
