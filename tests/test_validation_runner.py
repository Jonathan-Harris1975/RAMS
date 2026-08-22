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


def test_output_is_bounded_by_lines_and_bytes(tmp_path: Path) -> None:
    result = run_commands(
        ["python -c \"print('x' * 1000); [print(i) for i in range(50)]\""],
        cwd=tmp_path,
        max_output_lines=5,
        max_output_bytes=128,
    )
    assert result.passed is True
    assert len(result.output_tail.encode()) <= 128
    assert len(result.output_tail.splitlines()) <= 5


def test_timeout_terminates_command(tmp_path: Path) -> None:
    result = run_commands(
        ['python -c "import time; time.sleep(5)"'],
        cwd=tmp_path,
        timeout_seconds=1,
    )
    assert result.passed is False
    assert result.return_code == 124
    assert "TIMEOUT" in result.output_tail


def test_shell_operators_are_rejected_without_execution(tmp_path: Path) -> None:
    sentinel = tmp_path / "should-not-exist"
    result = run_commands([f"printf ok > {sentinel}"], cwd=tmp_path)
    assert result.passed is False
    assert result.return_code == 2
    assert "shell operators are not allowed" in result.output_tail
    assert not sentinel.exists()


def test_command_substitution_is_rejected(tmp_path: Path) -> None:
    result = run_commands(["printf $(whoami)"], cwd=tmp_path)
    assert result.passed is False
    assert result.return_code == 2
    assert "shell control syntax" in result.output_tail
