# vikunja-mcp

An **MCP server** that exposes tasks in a [Vikunja](https://vikunja.io) instance as typed tools —
list / get / add / update / complete / reopen — for Claude Code, Claude Desktop, or any MCP client.
Runs locally over **stdio** (the client launches it as a subprocess). **Write-only — there is no
delete tool, by design.**

The API **token comes only from the session environment** (never a config file) and the **project
is mandatory** (a default via env, or passed per-call).

## Prerequisites

Installation is a couple of `uv` commands — no OS installer, no background service (the MCP client
launches the server on demand). You need three things:

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

The first install (`uv tool install` or `uv sync`) needs network access — `uv` fetches a matching
Python and the packages; after that the server runs offline against your instance.

## Install

Two ways, by intent — *use* the server, or *work on* it.

### Use it: install as a standalone tool

`uv tool install` builds the package into an isolated, uv-managed environment and drops a
`vikunja-mcp` launcher on your tool bin dir (`%USERPROFILE%\.local\bin` on Windows, `~/.local/bin`
elsewhere). The source is disposable once installed — this is what keeps the server installed with
**no checkout on the machine**. Run `uv tool update-shell` **once** to add that bin dir to your
user `PATH` (a permanent, user-scope change), then relaunch your shell so it takes effect — after
that the `vikunja-mcp` launcher resolves by name, with no path needed anywhere.

Install from the git remote — `uv` clones to a temp dir, builds, and discards it, so the source
never lands on disk (note the url form is `host/path`, not the `host:path` an SSH remote prints):

```bash
uv tool install git+ssh://git@your-git-host/you/vikunja-mcp.git
uv tool upgrade vikunja-mcp                                        # update later (re-fetches the remote)
```

…or from a wheel you build yourself, if you'd rather not reach the remote to install or update:

```bash
uv build                                                          # -> dist/vikunja_mcp-<ver>-py3-none-any.whl
uv tool install ./dist/vikunja_mcp-<ver>-py3-none-any.whl
uv tool install --reinstall ./dist/vikunja_mcp-<newver>-py3-none-any.whl   # update later
```

Remove it with `uv tool uninstall vikunja-mcp`. Upgrades keep the launcher path stable, so the MCP
registration below never has to change — just relaunch the client.

### Work on it: dev checkout

For hacking on the code, keep the checkout and run out of it — no install, edits take effect live:

```bash
cd vikunja-mcp
uv sync            # creates the venv, provisions Python if needed, installs deps
uv run pytest      # optional: run the client tests
```

## Configure (environment only)

| Setting | Env var | Notes |
| --- | --- | --- |
| Instance URL | `VIKUNJA_URL` | **required**; the http(s) base URL of your instance |
| Default project by **ID** | `VIKUNJA_PROJECT_ID` | optional, preferred; set it **per repo** (below); no `/projects` lookup → minimal token |
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

Each setting has **one** right home, because each changes at a different rate:

| Setting | Where | Why there |
| --- | --- | --- |
| `VIKUNJA_API_TOKEN` | the **shell** you launch from | secret; never in any file |
| `VIKUNJA_URL` | **user** scope — register the server once | one instance per machine |
| `VIKUNJA_PROJECT_ID` | **per repo** — `.claude/settings.json` `env` | differs for every repo |

Register the server once, at user scope, with only the URL. For a standalone-tool install, register
the launcher **by name** — this works because `uv tool update-shell` (above) put its bin dir on
`PATH`; relaunch first if you haven't since. A later `uv tool upgrade` keeps the launcher name, so
this registration never changes on update:

```bash
claude mcp add vikunja --scope user --env VIKUNJA_URL=https://your-vikunja-host -- vikunja-mcp
```

From a dev checkout instead, run it out of the tree — no install needed, but the checkout must stay:

```bash
claude mcp add vikunja --scope user --env VIKUNJA_URL=https://your-vikunja-host -- uv run --directory /abs/path/to/vikunja-mcp vikunja-mcp
```

Then, in **each repo** whose tasks live in a Vikunja project, name that project in
`.claude/settings.json` — Claude Code applies its `env` to the MCP servers it spawns, so this adds
the default *without* redefining the server:

```json
{ "env": { "VIKUNJA_PROJECT_ID": "11" } }
```

Do **not** put `VIKUNJA_PROJECT_ID` at user scope: one machine spans several projects, and a global
default silently sends every repo's tasks to whichever project you named first. Omit it entirely and
the tools ask which project to use — the intended fallback, not a failure. Use
`.claude/settings.local.json` instead if the id shouldn't be committed (it is gitignored).

Prefer a **project-scope [`.mcp.json`](.mcp.json.example)** only if a repo needs a wholly different
server (a second instance, say). Scopes do **not** merge — Claude Code takes the entire entry from
the highest-precedence scope (local → project → user), so a project-scope entry must restate
`command`, `args` and `VIKUNJA_URL`, or it will lose them. **The token is deliberately absent from
that file** — set it in your shell (above).

Then relaunch Claude and run `/mcp` (or `claude mcp list`) to confirm `vikunja` is connected.

### Claude Desktop / cowork — the `.mcpb` bundle

Claude **Desktop** (and **cowork** in *local* mode, which shares Desktop's config) doesn't use
`claude mcp add`; it installs an **MCP bundle**. A ready-to-build manifest lives in
[`packaging/mcpb/`](packaging/mcpb/) — see its README for the full build + install steps. In short:

- Build the `.mcpb` with Anthropic's official **[`@anthropic-ai/mcpb`](https://www.npmjs.com/package/@anthropic-ai/mcpb)**
  CLI (`mcpb validate` + `mcpb pack`), so the manifest is schema-checked — not a hand-zipped archive.
- Drag it onto **Settings → Extensions**. It prompts for the URL and token at install; the token
  field is `sensitive`, so Desktop keeps it in the **OS keychain** and injects it at launch. This is
  the one place the token model differs from the CLI — Desktop has no launching shell to inherit it
  from — and it still never touches a config file.
- **cowork remote** can reach neither a LAN instance nor your local launcher, so the bundle only
  works in **local** mode on the machine where the server is installed.

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
| `get_task(task_id)` | one task, including its description (returned as **HTML** — see below) |
| `add_task(title, project_id?, description?, priority?, due?, labels?)` | create (priority 0..5; `due` = `yyyy-MM-dd`; `description` markdown; labels created-if-missing) |
| `update_task(task_id, title?, description?, description_append?, priority?, due?, labels?)` | change only the passed fields; `due=""`/`description=""` clear; `description_append` grows the description (see below) |
| `complete_task(task_id)` / `reopen_task(task_id)` | mark done / not done |

Tasks come back as structured JSON. Priority is `0..5`: `0`=Unset `1`=Low `2`=Medium `3`=High
`4`=Urgent `5`=DO NOW.

| Field | Notes |
| --- | --- |
| `id` | **global**; this is what every `task_id` argument takes |
| `index`, `identifier` | the per-project number the UI shows (`26`, `TEST-26`) — display only |
| `project_id` | which project the task is in; the task-id tools take no project, so this is the only way to tell |
| `title`, `done`, `priority`, `labels` | writable via `add_task`/`update_task` |
| `due` | `yyyy-MM-dd`; `""` when unset |
| `done_at`, `start_date`, `end_date`, `created`, `updated` | RFC3339; `""` when unset |
| `assignees` | **usernames**, read-only (see below) |
| `related_tasks` | `{kind: [{id, identifier, title, done}]}` — references only |
| `reminders` | `[{reminder, relative_to, relative_period}]` |
| `repeat_after`, `repeat_mode` | seconds; `0` = does not repeat |
| `description` | `get_task` only — HTML, see above |

Everything above rides in the payload Vikunja already sends, so none of it costs an extra request.
`bucket_id`, `position`, `cover_image_attachment_id` and `reactions` are dropped: kanban/UI state
with nothing in it for a caller. Unset dates arrive as `0001-01-01T00:00:00Z` and are normalized to
`""`, so nothing renders a year-1 timestamp as real.

Only `title`/`description`/`priority`/`due`/`labels` are **writable**; the rest are reported as-is.
`assignees` are usernames rather than ids because a project-scoped token gets **401 on `GET /user`**
— it can read the assignees embedded in a task, but cannot look a user up, so an id would be a
handle nothing here can resolve. `related_tasks` is trimmed to references on purpose: Vikunja nests
the *entire* related task, description included, which would put a task's whole body inside every
task linking to it.

**Descriptions: write markdown, read HTML.** Vikunja's description field stores the HTML its WYSIWYG
editor produces — HTML *is* the storage format. `add_task`/`update_task` take markdown and convert it
for you, but `get_task` returns Vikunja's HTML verbatim; nothing converts it back. The asymmetry is
deliberate. Feeding a description from `get_task` straight into `update_task` is safe — HTML passes
through the converter unchanged — so read-modify-write of a description won't mangle it.

**Long descriptions: `description_append`.** Neither this server nor Vikunja limits description
length in practice (~1MB round-trips fine). The real ceiling is the *calling agent's* output budget
for a single tool call: the description is text the model has to emit, and an over-long call is
truncated before it reaches this server — so the failure looks like the tool erroring, not Vikunja
rejecting anything. Writing the text to a file first does **not** help; that costs the same tokens.
Instead, send the first part, then grow it:

```text
add_task(title="Report", description="# Report\n\nOpening.")   -> id 42
update_task(42, description_append="## Findings\n\n...")
update_task(42, description_append="## Conclusion\n\n...")
```

Vikunja has no append endpoint, so this is a read-modify-write: the chunk is converted to HTML and
concatenated onto the stored HTML (which is never re-converted). Each call costs only its own chunk,
so a description can grow far past what one call could carry. **Split on block boundaries** — each
chunk is converted as standalone markdown, so a chunk cut mid-block (half a code fence, a split
table) converts wrongly and the next chunk cannot repair it.

---

This MCP server was built with [Claude Code](https://claude.com/claude-code).
