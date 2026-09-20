import tomllib
from pathlib import Path

from typer.testing import CliRunner

from repo_mgmt.cli import _validate_pipeline, app


def test_cli_accepts_content_pipeline() -> None:
    assert _validate_pipeline("content") == "content"


def test_cli_console_script_is_packaged() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"]["rms"] == "repo_mgmt.cli:app"


def test_cli_help_exposes_supported_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "dry-run" in result.stdout
    assert "run" in result.stdout
