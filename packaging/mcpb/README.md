# Vikunja MCP — Claude Desktop / cowork bundle

`manifest.json` here builds an **[MCP bundle](https://github.com/modelcontextprotocol/mcpb)**
(`.mcpb`) — the one-file install format Claude **Desktop** and **cowork** (local mode) use in place
of the CLI's `claude mcp add`. It is a *thin* wrapper: it launches the `vikunja-mcp` server you
already installed as a standalone tool (`uv tool install …`, see the [top-level README](../../README.md))
and collects the instance URL and API token from the user at install time.

**Why a bundle at all:** Claude Desktop has no launching shell to inherit `VIKUNJA_API_TOKEN` from,
the way the CLI does. The `vikunja_api_token` field is marked `"sensitive": true`, so Desktop stores
it in the OS keychain (Windows Credential Manager / macOS Keychain / libsecret) and injects it into
the server's environment only at launch. The token never lands in a config file — keeping to this
project's token rule on a client that can't use the shell-env approach.

## Build

Prereqs: the `vikunja-mcp` launcher on `PATH` (`uv tool install …`, then `uv tool update-shell`, then
relaunch) and Node, for the official packer.

```bash
npm install -g @anthropic-ai/mcpb      # Anthropic's official MCP-bundle CLI
mcpb validate manifest.json            # schema-check against the CLI's shipped manifest schema
mcpb pack .                            # -> vikunja-mcp-<version>.mcpb
```

**Always build with `mcpb pack`, never a hand-rolled zip.** `pack` validates the manifest against the
schema the CLI ships before archiving, so a bad manifest fails loudly instead of installing broken.
(An `.mcpb` *is* just a zip, but hand-zipping skips that check.)

## Install

Drag the resulting `.mcpb` onto **Claude Desktop → Settings → Extensions** (or *Advanced → Install
from file*; unsigned personal bundles are allowed). Enter the URL and token when prompted, then
enable it. **cowork** picks it up in **local** mode, since it shares Desktop's config.

Project selection is per call, as everywhere else in this server: name the project in the request
("add this to Vikunja project 11"). No default is baked into the bundle, on purpose — one machine
spans several projects, and a bundle-level default would be a single global one.

## Notes / gotchas

- **`command` is the bare name `vikunja-mcp`**, which resolves only if the tool bin dir is on `PATH`
  (what `uv tool update-shell` sets up). A GUI-launched Desktop inherits the user `PATH`, so this
  works *after* update-shell + a relaunch. If it can't find the launcher, replace `command` with the
  absolute path the installer printed (e.g. `C:\Users\<you>\.local\bin\vikunja-mcp.exe`) before
  packing — but keep the *committed* manifest on the bare name, since an absolute path is
  machine-specific.
- **`server.type` is `binary`** pointing at an external launcher rather than a file inside the bundle.
  This passes `mcpb validate`. If a future Desktop runtime insists a `binary` bundle's `entry_point`
  be bundled, switch to a self-contained Python bundle (vendor the source + deps) — more work, and
  Desktop ships no Python runtime, so it would rely on a system/uv Python being present.
- **cowork remote can't use this.** Remote sessions run in an Anthropic sandbox with neither your
  launcher nor a route to a LAN Vikunja URL. Local mode only; packaging does not change networking.
- Keep `version` in step with `pyproject.toml`.
