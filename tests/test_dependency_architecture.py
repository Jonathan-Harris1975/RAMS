from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify_dependency_lock.py"


def _load_verify_module():
    spec = importlib.util.spec_from_file_location("verify_dependency_lock", VERIFY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dependabot_visible_dependency_architecture() -> None:
    assert (ROOT / "pyproject.toml").is_file()
    assert (ROOT / "requirements.in").is_file()
    assert (ROOT / "requirements.txt").is_file()
    assert not (ROOT / "requirements.lock").exists()

    verifier = _load_verify_module()
    verifier.main()


def test_dependency_verifier_rejects_stale_compiled_requirements(tmp_path, monkeypatch) -> None:
    verifier = _load_verify_module()
    monkeypatch.setattr(verifier, "ROOT", tmp_path)

    (tmp_path / "pyproject.toml").write_text(
        """[project]
name = \"test\"
version = \"0.0.0\"
dynamic = [\"dependencies\"]

[tool.setuptools.dynamic]
dependencies = {file = [\"requirements.in\"]}
""",
        encoding="utf-8",
    )
    (tmp_path / "requirements.in").write_text("fastapi==0.141.1\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("fastapi==0.140.0\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text(
        "COPY pyproject.toml requirements.in requirements.txt .\n"
        "RUN pip install -r requirements.txt && pip install --no-deps .\n",
        encoding="utf-8",
    )
    workflow = tmp_path / ".github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / "ci.yml").write_text("pip install -c requirements.txt -e .\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="compiled production requirements are stale"):
        verifier.main()
