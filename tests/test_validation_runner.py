"""Tests for repo_mgmt.validation_runner."""

from pathlib import Path
from repo_mgmt.validation_runner import run_commands


def test_passing_command(tmp_path: Path) -> None:
    result = run_commands(["printf ok"], cwd=tmp_path)
    assert result.passed is True
    assert "ok" in result.output_tail


def test_failing_command(tmp_path: Path) -> None:
    result = run_commands(["false"], cwd=tmp_path)
    assert result.passed is False


def test_stops_on_first_failure(tmp_path: Path) -> None:
    sentinel = tmp_path / "sentinel.txt"
    commands = ["false", f"touch {sentinel}"]
    result = run_commands(commands, cwd=tmp_path)
    assert result.passed is False
    assert not sentinel.exists(), "Second command should not have run"


def test_output_tail_present(tmp_path: Path) -> None:
    result = run_commands(["printf 'hello from test'"], cwd=tmp_path)
    assert "hello from test" in result.output_tail


def test_empty_commands_passes(tmp_path: Path) -> None:
    result = run_commands([], cwd=tmp_path)
    assert result.passed is True
