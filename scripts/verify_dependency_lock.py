"""Verify RAMS source manifests, SHA-256 locks and install enforcement."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^;\s]+)(?:\s*;.*)?$"
)
HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})(?=\s|$)")


@dataclass(frozen=True)
class LockedRequirement:
    version: str
    hashes: frozenset[str]


@dataclass(frozen=True)
class LockSpec:
    label: str
    source: str
    lock: str


LOCK_SPECS = (
    LockSpec("runtime", "requirements.in", "requirements.txt"),
    LockSpec("bootstrap", "requirements-bootstrap.in", "requirements-bootstrap.txt"),
    LockSpec("build", "requirements-build.in", "requirements-build.txt"),
    LockSpec("development", "requirements-dev.in", "requirements-dev.txt"),
)


def normalise_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def exact_pin(value: str) -> tuple[str, str]:
    match = PIN_RE.fullmatch(value.strip())
    if not match:
        raise AssertionError(f"dependency is not an exact pin: {value}")
    return normalise_name(match.group(1)), match.group(2)


def _logical_lines(path: Path) -> list[tuple[int, str]]:
    logical: list[tuple[int, str]] = []
    start = 0
    pending: list[str] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if not pending:
            start = line_number
        if line.endswith("\\"):
            pending.append(line[:-1].strip())
            continue
        pending.append(line)
        logical.append((start, " ".join(part for part in pending if part)))
        pending = []
    if pending:
        raise AssertionError(f"{path.name}:{start}: unterminated line continuation")
    return logical


def read_source_pins(path: Path, *, seen: set[Path] | None = None) -> dict[str, str]:
    pins: dict[str, str] = {}
    visited = set() if seen is None else seen
    resolved = path.resolve()
    if resolved in visited:
        raise AssertionError(f"recursive requirements include: {path.name}")
    visited.add(resolved)
    for line_number, line in _logical_lines(path):
        if line.startswith(("-r ", "--requirement ")):
            include_name = line.split(maxsplit=1)[1]
            included = read_source_pins(path.parent / include_name, seen=visited)
            overlap = set(pins) & set(included)
            if overlap:
                raise AssertionError(
                    f"{path.name}:{line_number}: duplicate included pins: {sorted(overlap)}"
                )
            pins.update(included)
            continue
        try:
            name, version = exact_pin(line)
        except AssertionError as exc:
            raise AssertionError(f"{path.name}:{line_number}: {exc}") from exc
        if name in pins:
            raise AssertionError(f"{path.name}:{line_number}: duplicate package: {name}")
        pins[name] = version
    visited.remove(resolved)
    return pins


def read_hashed_lock(path: Path) -> dict[str, LockedRequirement]:
    text = path.read_text(encoding="utf-8")
    if re.search(r"(?m)^\s*--(?:index-url|extra-index-url|trusted-host)\b", text):
        raise AssertionError(f"{path.name} must not embed package-index configuration")
    pins: dict[str, LockedRequirement] = {}
    for line_number, line in _logical_lines(path):
        if line.startswith("--"):
            raise AssertionError(f"{path.name}:{line_number}: unexpected global option: {line}")
        requirement = line.split(" --hash=", 1)[0].strip()
        try:
            name, version = exact_pin(requirement)
        except AssertionError as exc:
            raise AssertionError(f"{path.name}:{line_number}: {exc}") from exc
        hashes = frozenset(HASH_RE.findall(line))
        if not hashes:
            raise AssertionError(
                f"{path.name}:{line_number}: {name}=={version} has no SHA-256 hashes"
            )
        if name in pins:
            raise AssertionError(f"{path.name}:{line_number}: duplicate package: {name}")
        pins[name] = LockedRequirement(version=version, hashes=hashes)
    if not pins:
        raise AssertionError(f"{path.name} contains no locked requirements")
    return pins


def verify_lock(source: Path, lock: Path) -> None:
    direct = read_source_pins(source)
    compiled = read_hashed_lock(lock)
    mismatched = {
        name: version
        for name, version in direct.items()
        if name not in compiled or compiled[name].version != version
    }
    if mismatched:
        detail = ", ".join(
            f"{name}=={version}" for name, version in sorted(mismatched.items())
        )
        raise AssertionError(f"{lock.name} does not match {source.name}: {detail}")


def _require_all(text: str, expected: tuple[str, ...], label: str) -> None:
    missing = [value for value in expected if value not in text]
    if missing:
        raise AssertionError(f"{label} is missing dependency enforcement: {missing}")


def verify_sources() -> None:
    if (ROOT / "requirements.lock").exists():
        raise AssertionError("requirements.lock must not be reintroduced; use requirements.txt")
    for spec in LOCK_SPECS:
        verify_lock(ROOT / spec.source, ROOT / spec.lock)

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dynamic = (
        pyproject.get("tool", {})
        .get("setuptools", {})
        .get("dynamic", {})
        .get("dependencies", {})
    )
    if dynamic.get("file") != ["requirements.in"]:
        raise AssertionError(
            "pyproject.toml must source project dependencies only from requirements.in"
        )

    build_requires = pyproject.get("build-system", {}).get("requires", [])
    if read_source_pins(ROOT / "requirements-build.in") != {
        name: version for name, version in map(exact_pin, build_requires)
    }:
        raise AssertionError("requirements-build.in must match build-system.requires")

    dev_source = read_source_pins(ROOT / "requirements-dev.in")
    dev_extra = pyproject.get("project", {}).get("optional-dependencies", {}).get("dev", [])
    for entry in dev_extra:
        name, version = exact_pin(entry)
        if dev_source.get(name) != version:
            raise AssertionError(
                f"requirements-dev.in does not match pyproject dev pin: {name}=={version}"
            )

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    _require_all(
        dockerfile,
        (
            "requirements-build.txt",
            "requirements.txt",
            "--require-hashes",
            "--no-index --no-deps --no-build-isolation",
        ),
        "Dockerfile",
    )
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    _require_all(
        ci,
        (
            "requirements-bootstrap.txt",
            "requirements-dev.txt",
            "--require-hashes",
            "--no-index --no-deps --no-build-isolation",
            "verify_hash_enforcement.py",
        ),
        "CI",
    )
    production_install = (ROOT / "scripts" / "install_production.sh").read_text(
        encoding="utf-8"
    )
    _require_all(
        production_install,
        (
            "requirements-build.txt",
            "requirements.txt",
            "--require-hashes",
            "--no-index --no-deps --no-build-isolation",
            "pip check",
        ),
        "production installer",
    )


def verify_compiled_locks() -> None:
    for spec in LOCK_SPECS:
        committed = read_hashed_lock(ROOT / spec.lock)
        with tempfile.TemporaryDirectory(prefix=f"rams-{spec.label}-lock-") as tmp:
            generated_path = Path(tmp) / spec.lock
            # Retaining the committed output as pip-tools input prevents an
            # ordinary verification run from becoming an unintended upgrade.
            shutil.copy2(ROOT / spec.lock, generated_path)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "piptools",
                    "compile",
                    "--quiet",
                    "--generate-hashes",
                    "--allow-unsafe",
                    "--no-emit-index-url",
                    "--no-emit-trusted-host",
                    "--no-strip-extras",
                    "--output-file",
                    str(generated_path),
                    str(ROOT / spec.source),
                ],
                cwd=ROOT,
                check=True,
            )
            generated = read_hashed_lock(generated_path)
        if generated != committed:
            changed = sorted(set(generated) | set(committed))
            differences = [
                f"{name}: committed={committed.get(name)!r}, compiled={generated.get(name)!r}"
                for name in changed
                if committed.get(name) != generated.get(name)
            ]
            raise AssertionError(
                f"compiled {spec.label} lock differs:\n" + "\n".join(differences)
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--compile",
        action="store_true",
        help="re-run pip-compile and compare every version and SHA-256 hash",
    )
    args = parser.parse_args()
    verify_sources()
    if args.compile:
        verify_compiled_locks()
    print("RAMS dependency architecture and SHA-256 locks are consistent")


if __name__ == "__main__":
    main()
