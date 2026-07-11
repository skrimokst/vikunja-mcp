# CLAUDE.md

Guidance for Claude Code working in this repo.

## What this is

`vikunja-mcp` — a local **stdio MCP server** (Python + FastMCP) that exposes Vikunja task
operations as typed tools: list / get / add / update / complete / reopen, plus `check_connection`.
**Write-only — there is no delete tool, by design.** It mirrors the separate `vikunja-tasks` Claude
skill; keep the two behaviourally in step (same actions, same env vars, same semantics).

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
- **Config via env**: `VIKUNJA_URL` (required http(s) base URL), and a default project via
  `VIKUNJA_PROJECT_ID` (preferred) or `VIKUNJA_PROJECT`. A tool's `project_id` argument overrides it.
- **Write-only**: never add a delete tool.
- **Python stays LF** (pinned in `.gitattributes`). Commit `uv.lock` for reproducible installs.

## Git

- **Commit automatically** once a piece of work is complete — no need to ask. Do **not** add a
  `Co-Authored-By` / "Generated with Claude Code" trailer.
- **Pushing and pulling are operator-only.**
