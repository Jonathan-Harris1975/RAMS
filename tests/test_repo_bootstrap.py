"""Tests for runtime repository bootstrap helpers."""

from __future__ import annotations

from repo_mgmt.repo_bootstrap import _git_base_command, _safe_command_for_log


def test_git_base_command_uses_github_basic_auth_header() -> None:
    """GitHub HTTPS bootstrap should use an auth header, not URL credentials."""
    command = _git_base_command("ghp_example", "https://github.com/example/private.git")

    rendered = " ".join(command)

    assert "http.https://github.com/.extraheader=Authorization: Basic" in rendered
    assert "ghp_example" not in rendered


def test_git_base_command_omits_auth_for_non_github_urls() -> None:
    """Non-GitHub remotes should not receive GitHub-specific auth config."""
    command = _git_base_command("token", "https://gitlab.com/example/private.git")

    assert command == ["git"]


def test_safe_command_log_redacts_encoded_auth_header() -> None:
    """Encoded credentials must not appear in operator logs."""
    token = "ghp_example"
    command = _git_base_command(token, "https://github.com/example/private.git")

    rendered = _safe_command_for_log(command, token)

    assert "ghp_example" not in rendered
    assert "<redacted>" in rendered
