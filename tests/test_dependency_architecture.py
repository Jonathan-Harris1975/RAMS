from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify_dependency_lock.py"


def _load_verify_module():
    module_name = "verify_dependency_lock_under_test"
    spec = importlib.util.spec_from_file_location(module_name, VERIFY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_renovate_visible_dependency_architecture(monkeypatch) -> None:
    expected = {
        "requirements.in",
        "requirements-runtime.lock",
        "requirements-bootstrap.in",
        "requirements-bootstrap.txt",
        "requirements-build.in",
        "requirements-build.txt",
        "requirements-dev.in",
        "requirements-dev.txt",
    }
    assert all((ROOT / path).is_file() for path in expected)
    assert not (ROOT / "requirements.txt").exists()

    verifier = _load_verify_module()
    monkeypatch.setattr(sys, "argv", [str(VERIFY_SCRIPT)])
    verifier.main()


def test_dependency_verifier_rejects_stale_compiled_requirement(tmp_path) -> None:
    verifier = _load_verify_module()
    source = tmp_path / "requirements.in"
    lock = tmp_path / "requirements-runtime.lock"
    source.write_text("fastapi==0.141.1\n", encoding="utf-8")
    lock.write_text(
        "fastapi==0.140.0 --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="does not match"):
        verifier.verify_lock(source, lock)


def test_dependency_verifier_rejects_missing_hash(tmp_path) -> None:
    verifier = _load_verify_module()
    lock = tmp_path / "requirements-runtime.lock"
    lock.write_text("fastapi==0.141.1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="has no SHA-256 hashes"):
        verifier.read_hashed_lock(lock)
