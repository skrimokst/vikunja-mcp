"""Configuration for the Vikunja MCP server — read from environment variables only.

Secrets rule (matches the vikunja-tasks skill): the API token is read from the process
environment ONLY (inherited from the shell that launched the MCP client). It is never read
from a file and never written anywhere. TLS uses the machine's OS trust store (see
``server.main``), so there is deliberately no CA-bundle or skip-verify option here.
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


def config_issues(cfg: Config) -> list[str]:
    """Human-readable setup problems; empty means it's worth trying the API."""
    issues: list[str] = []
    if not cfg.token:
        issues.append(
            "VIKUNJA_API_TOKEN is not set in this environment — set it in the shell you launch "
            "Claude from (e.g. export VIKUNJA_API_TOKEN=...), then relaunch. It is never stored in config."
        )
    if not (cfg.base_url.startswith("http://") or cfg.base_url.startswith("https://")):
        issues.append("VIKUNJA_URL is not set or not http(s) — set it to your Vikunja base URL.")
    return issues
