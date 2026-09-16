"""Verify RAMS uses one Dependabot-visible direct dependency source."""

from __future__ import annotations

import re
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
        if not line or line.startswith("#"):
            continue
        try:
            name, version = exact_pin(line)
        except AssertionError as exc:
            raise AssertionError(f"{path.name}:{line_number}: {exc}") from exc
        if name in pins:
            raise AssertionError(f"{path.name} contains duplicate package: {name}")
        pins[name] = version
    return pins


def main() -> None:
    if (ROOT / "requirements.lock").exists():
        raise AssertionError("requirements.lock must remain removed")

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dynamic = project.get("project", {}).get("dynamic", [])
    if "dependencies" not in dynamic:
        raise AssertionError("pyproject.toml must load project dependencies dynamically")

    dynamic_cfg = project.get("tool", {}).get("setuptools", {}).get("dynamic", {})
    dependency_file = dynamic_cfg.get("dependencies", {}).get("file")
    if dependency_file != ["requirements.in"]:
        raise AssertionError("pyproject.toml dependencies must come from requirements.in")

    declared = read_exact_requirements(ROOT / "requirements.in")
    locked = read_exact_requirements(ROOT / "requirements.txt")

    missing = sorted(name for name in declared if name not in locked)
    mismatched = sorted(
        f"{name}: requirements.in={declared[name]} requirements.txt={locked[name]}"
        for name in declared
        if name in locked and declared[name] != locked[name]
    )
    if missing or mismatched:
        detail = "; ".join([*(f"missing={','.join(missing)}" for _ in [0] if missing), *mismatched])
        raise AssertionError(f"compiled production requirements are stale: {detail}")

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    if "COPY pyproject.toml requirements.in requirements.txt ." not in dockerfile:
        raise AssertionError("Dockerfile must copy pyproject.toml, requirements.in and requirements.txt")
    if "-r requirements.txt" not in dockerfile:
        raise AssertionError("Dockerfile must install the compiled production requirements.txt")
    if "--no-deps ." not in dockerfile:
        raise AssertionError("Dockerfile must install the RAMS package without re-resolving dependencies")

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    if "requirements.lock" in ci:
        raise AssertionError("CI must not constrain installs from the removed requirements.lock")
    if "-c requirements.txt" not in ci:
        raise AssertionError("CI must constrain development installs from requirements.txt")

    print(
        "dependency architecture verified: "
        f"{len(declared)} direct pins -> {len(locked)} exact production pins"
    )


if __name__ == "__main__":
    main()
