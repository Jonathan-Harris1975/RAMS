from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify_dependency_lock.py"


def _load_verify_module():
    spec = importlib.util.spec_from_file_location("verify_dependency_lock", VERIFY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pyproject_is_the_only_dependency_declaration_manifest() -> None:
    assert (ROOT / "pyproject.toml").is_file()
    assert (ROOT / "requirements.lock").is_file()
    assert not (ROOT / "requirements.in").exists()
    assert not (ROOT / "requirements.txt").exists()


def test_dependency_verifier_rejects_reintroduced_legacy_manifest(tmp_path, monkeypatch) -> None:
    verifier = _load_verify_module()
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    (tmp_path / "requirements.txt").write_text("fastapi==0.141.1\n", encoding="utf-8")

    try:
        verifier.verify_single_dependency_source()
    except AssertionError as exc:
        assert "requirements.txt" in str(exc)
    else:
        raise AssertionError("legacy dependency manifest was not rejected")
