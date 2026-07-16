"""Configuration for the Vikunja MCP server — read from environment variables only.

Secrets rule: the API token is read from the process environment ONLY (inherited from the shell
that launched the MCP client). It is never read from a file and never written anywhere. TLS uses
the machine's OS trust store (see ``server.main``), so there is deliberately no CA-bundle or
skip-verify option here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    base_url: str
    token: str
    project_id: int | None
    project_name: str | None

    @property
    def default_project(self) -> int | str | None:
        """Preferred default project: numeric id if set, else the name, else None."""
        if self.project_id:
            return self.project_id
        return self.project_name


def _int_or_none(value: str | None) -> int | None:
    if value and value.strip().isdigit():
        n = int(value)
        return n if n > 0 else None
    return None


def load_config() -> Config:
    return Config(
        base_url=(os.environ.get("VIKUNJA_URL") or "").strip().rstrip("/"),
        token=os.environ.get("VIKUNJA_API_TOKEN") or "",
        project_id=_int_or_none(os.environ.get("VIKUNJA_PROJECT_ID")),
        project_name=(os.environ.get("VIKUNJA_PROJECT") or "").strip() or None,
    )


# The operator-facing fix for a missing token. Kept as a constant so it is the ONE source of the
# command text — `config_issues` embeds it, and `vikunja-mcp --token-help` reprints it verbatim.
# Prompt for the token rather than typing it inline, so it stays out of shell history.
TOKEN_SETUP_HELP = (
    "VIKUNJA_API_TOKEN is not set in this environment — set it in the shell you launch "
    "Claude from, then relaunch. It is never stored in config. Prompt for it rather than "
    "typing it inline, so it stays out of shell history — give the operator the command "
    "for their shell, verbatim:\n"
    "  PowerShell 7+:  $env:VIKUNJA_API_TOKEN = Read-Host -AsSecureString 'Vikunja API token'"
    " | ConvertFrom-SecureString -AsPlainText\n"
    "  bash:           read -rs -p 'Vikunja API token: ' VIKUNJA_API_TOKEN"
    " && export VIKUNJA_API_TOKEN\n"
    "  zsh:            read -rs 'VIKUNJA_API_TOKEN?Vikunja API token: '"
    " && export VIKUNJA_API_TOKEN"
)

# Appended to the missing-token issue so an agent leaves a durable, per-project reminder instead of
# re-diagnosing a missing token by reading the server's source (the failure mode that motivated
# this). The token lives in the session env and is gone in every new shell, so the check belongs at
# the start of each session — recorded in the CONSUMING project's CLAUDE.md, which the MCP cannot
# write itself but can instruct the agent to write.
SESSION_SELF_DOC_HINT = (
    "To stop a future session from rediscovering this by debugging the MCP: add a short note to "
    "THIS project's CLAUDE.md (create it if absent) — that the project's tasks live in Vikunja via "
    "the `vikunja` MCP, and that at the start of each session you must verify VIKUNJA_API_TOKEN is "
    "set (check presence WITHOUT printing the value — PowerShell: `Test-Path env:VIKUNJA_API_TOKEN`; "
    "bash/zsh: `[ -n \"$VIKUNJA_API_TOKEN\" ]`) and, if it is unset, ask the operator to set it with "
    "the command above before doing anything else. A missing token is an operator setup step, never "
    "an MCP bug to investigate."
)


def config_issues(cfg: Config) -> list[str]:
    """Human-readable setup problems; empty means it's worth trying the API."""
    issues: list[str] = []
    if not cfg.token:
        issues.append(TOKEN_SETUP_HELP + "\n\n" + SESSION_SELF_DOC_HINT)
    if not (cfg.base_url.startswith("http://") or cfg.base_url.startswith("https://")):
        issues.append("VIKUNJA_URL is not set or not http(s) — set it to your Vikunja base URL.")
    return issues
