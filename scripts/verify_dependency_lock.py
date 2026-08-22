"""Verify that the production runtime is installed from an exact dependency lock."""

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


def main() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = dict(exact_pin(item) for item in project["project"]["dependencies"])

    locked: dict[str, str] = {}
    for line_number, raw in enumerate((ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            name, version = exact_pin(line)
        except AssertionError as exc:
            raise AssertionError(f"requirements.lock:{line_number}: {exc}") from exc
        if name in locked:
            raise AssertionError(f"requirements.lock contains duplicate package: {name}")
        locked[name] = version

    missing = sorted(name for name in declared if name not in locked)
    mismatched = sorted(
        f"{name}: pyproject={declared[name]} lock={locked[name]}"
        for name in declared
        if name in locked and declared[name] != locked[name]
    )
    if missing or mismatched:
        detail = "; ".join([*(f"missing={','.join(missing)}" for _ in [0] if missing), *mismatched])
        raise AssertionError(f"production lock does not match pyproject runtime dependencies: {detail}")

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    if "COPY pyproject.toml requirements.lock ." not in dockerfile:
        raise AssertionError("Dockerfile must copy requirements.lock into the builder")
    if "-r requirements.lock" not in dockerfile:
        raise AssertionError("Dockerfile must install the production requirements.lock")
    if "--no-deps ." not in dockerfile:
        raise AssertionError("Dockerfile must install the RAMS package without re-resolving dependencies")

    print(f"production dependency lock verified: {len(locked)} exact pins")


if __name__ == "__main__":
    main()
