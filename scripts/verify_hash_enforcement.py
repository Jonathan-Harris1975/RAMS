"""Prove pip rejects a deliberately corrupted dependency hash."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIX_BLOCK_RE = re.compile(
    r"(?ms)^six==(?P<version>[^\s;\\]+)(?P<body>.*?)(?=^[A-Za-z0-9_.-]+(?:\[[^\]]+\])?==|\Z)"
)
HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def _different_hash(value: str) -> str:
    replacement = "0" if value[0] != "0" else "1"
    return replacement + value[1:]


def main() -> None:
    lock = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    match = SIX_BLOCK_RE.search(lock)
    if match is None:
        raise SystemExit("requirements.txt does not contain the six dependency")
    version = match.group("version")
    expected_hashes = set(HASH_RE.findall(match.group(0)))
    if not expected_hashes:
        raise SystemExit("six has no SHA-256 hashes in requirements.txt")

    with tempfile.TemporaryDirectory(prefix="rams-negative-hash-") as tmp:
        root = Path(tmp)
        wheelhouse = root / "wheelhouse"
        wheelhouse.mkdir()
        valid = root / "valid.txt"
        hash_options = " ".join(
            f"--hash=sha256:{digest}" for digest in sorted(expected_hashes)
        )
        valid.write_text(
            f"six=={version} {hash_options}\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--only-binary=:all:",
                "--no-deps",
                "--require-hashes",
                "--dest",
                str(wheelhouse),
                "--requirement",
                str(valid),
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        wheels = list(wheelhouse.glob("six-*.whl"))
        if len(wheels) != 1:
            raise SystemExit(f"expected exactly one six wheel, found {len(wheels)}")
        actual_hash = hashlib.sha256(wheels[0].read_bytes()).hexdigest()
        if actual_hash not in expected_hashes:
            raise SystemExit("downloaded six wheel is not covered by the committed lock")

        corrupt = root / "corrupt.txt"
        corrupt.write_text(
            f"six=={version} --hash=sha256:{_different_hash(actual_hash)}\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "--no-deps",
                "--ignore-installed",
                "--target",
                str(root / "target"),
                "--require-hashes",
                "--requirement",
                str(corrupt),
            ],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        output = result.stdout.lower()
        if result.returncode == 0 or "hash" not in output or "do not match" not in output:
            raise SystemExit(
                "negative hash verification did not fail with pip's hash-mismatch error"
            )
    print("Negative hash verification passed: pip rejected the corrupted six artefact hash")


if __name__ == "__main__":
    main()
