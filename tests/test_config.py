"""Tests for config parsing and the setup-guidance messages (no live server needed)."""

from __future__ import annotations

from vikunja_mcp.config import (
    SESSION_SELF_DOC_HINT,
    TOKEN_SETUP_HELP,
    Config,
    config_issues,
    load_config,
)


def _cfg(token="tok", url="https://vk.test", pid=7, name=None) -> Config:
    return Config(base_url=url, token=token, project_id=pid, project_name=name)


def test_config_issues_empty_when_ready():
    assert config_issues(_cfg()) == []


def test_missing_token_carries_setup_command_and_self_doc_hint():
    issues = config_issues(_cfg(token=""))
    assert len(issues) == 1
    msg = issues[0]
    assert TOKEN_SETUP_HELP in msg          # the operator-facing command
    assert SESSION_SELF_DOC_HINT in msg     # the "record it in CLAUDE.md" directive


def test_self_doc_hint_steers_to_claude_md_not_debugging():
    # The whole point: a missing token must read as an operator setup step, not an MCP bug to
    # investigate by reading the server source.
    assert "CLAUDE.md" in SESSION_SELF_DOC_HINT
    assert "MCP bug" in SESSION_SELF_DOC_HINT
    # presence check must not print the secret's value
    assert "Test-Path env:VIKUNJA_API_TOKEN" in SESSION_SELF_DOC_HINT


def test_token_setup_help_covers_each_shell():
    for shell in ("PowerShell", "bash", "zsh"):
        assert shell in TOKEN_SETUP_HELP
    assert "VIKUNJA_API_TOKEN" in TOKEN_SETUP_HELP


def test_missing_url_reported_independently():
    issues = config_issues(_cfg(url="ftp://nope"))
    assert any("VIKUNJA_URL" in i for i in issues)


def test_load_config_parses_env(monkeypatch):
    monkeypatch.setenv("VIKUNJA_URL", "https://vk.test/")  # trailing slash trimmed
    monkeypatch.setenv("VIKUNJA_API_TOKEN", "secret")
    monkeypatch.setenv("VIKUNJA_PROJECT_ID", "7")
    monkeypatch.delenv("VIKUNJA_PROJECT", raising=False)
    cfg = load_config()
    assert cfg.base_url == "https://vk.test"
    assert cfg.token == "secret"
    assert cfg.project_id == 7
    assert cfg.default_project == 7
