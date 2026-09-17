"""Verify RAMS uses deterministic production and CI dependency manifests."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^;\s]+)$")


def normalise_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def exact_pin(value: str) -> tuple[str, str]:
    match = PIN_RE.fullmatch(value.strip())
    if not match:
        raise AssertionError(f"dependency is not an exact pin: {value}")
    return normalise_name(match.group(1)), match.group(2)


def read_exact_requirements(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        try:
            name, version = exact_pin(line)
        except AssertionError as exc:
            raise AssertionError(f"{path.name}:{line_number}: {exc}") from exc
        if name in pins:
            raise AssertionError(f"{path.name} contains duplicate package: {name}")
        pins[name] = version
    return pins


def verify_sources() -> None:
    if (ROOT / "requirements.lock").exists():
        raise AssertionError("requirements.lock must not be reintroduced; use requirements.txt")

    direct = read_exact_requirements(ROOT / "requirements.in")
    compiled = read_exact_requirements(ROOT / "requirements.txt")
    mismatched = {name: version for name, version in direct.items() if compiled.get(name) != version}
    if mismatched:
        details = ", ".join(f"{name}=={version}" for name, version in sorted(mismatched.items()))
        raise AssertionError(f"requirements.txt does not match direct production pins: {details}")

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dynamic = pyproject.get("tool", {}).get("setuptools", {}).get("dynamic", {}).get("dependencies", {})
    if dynamic.get("file") != ["requirements.in"]:
        raise AssertionError("pyproject.toml must source project dependencies only from requirements.in")

    dev_manifest = read_exact_requirements(ROOT / "requirements-dev.txt")
    dev_extra = pyproject.get("project", {}).get("optional-dependencies", {}).get("dev", [])
    for entry in dev_extra:
        name, version = exact_pin(entry)
        if dev_manifest.get(name) != version:
            raise AssertionError(f"requirements-dev.txt does not match pyproject dev pin: {name}=={version}")

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    if "requirements.txt" not in dockerfile or "pip install" not in dockerfile:
        raise AssertionError("Dockerfile must install the compiled requirements.txt lock")

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    if "requirements-dev.txt" not in ci:
        raise AssertionError("CI must install the deterministic dev manifest")


def verify_compiled_lock() -> None:
    committed = read_exact_requirements(ROOT / "requirements.txt")
    with tempfile.TemporaryDirectory(prefix="rams-lock-") as tmp:
        generated_path = Path(tmp) / "requirements.txt"
        shutil.copy2(ROOT / "requirements.txt", generated_path)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "piptools",
                "compile",
                "--quiet",
                "--output-file",
                str(generated_path),
                str(ROOT / "requirements.in"),
            ],
            cwd=ROOT,
            check=True,
        )
        generated = read_exact_requirements(generated_path)
    if generated != committed:
        changed = sorted(set(generated) | set(committed))
        differences = [
            f"{name}: committed={committed.get(name)!r}, compiled={generated.get(name)!r}"
            for name in changed
            if committed.get(name) != generated.get(name)
        ]
        raise AssertionError("compiled production lock differs:\n" + "\n".join(differences))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compile", action="store_true", help="re-run pip-compile and compare every production pin")
    args = parser.parse_args()
    verify_sources()
    if args.compile:
        verify_compiled_lock()
    print("RAMS dependency architecture is consistent")


if __name__ == "__main__":
    main()
