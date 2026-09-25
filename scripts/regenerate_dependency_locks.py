"""Regenerate every hashed dependency lock from its authoritative source manifest."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCKS = (
    ("requirements.in", "requirements-runtime.lock"),
    ("requirements-bootstrap.in", "requirements-bootstrap.txt"),
    ("requirements-build.in", "requirements-build.txt"),
    ("requirements-dev.in", "requirements-dev.txt"),
)


def main() -> None:
    for source, output in LOCKS:
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
                output,
                source,
            ],
            cwd=ROOT,
            check=True,
        )

    subprocess.run(
        [sys.executable, "scripts/verify_dependency_lock.py"],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
