from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_hive_repository_metadata_uses_canonical_id_and_current_paths() -> None:
    metadata = json.loads(
        (ROOT / "HIVE_REPOSITORY_METADATA.json").read_text(encoding="utf-8")
    )

    assert metadata["$schema"] == "hive.repository-intelligence/v1"
    assert metadata["canonicalId"] == "RAMS"
    assert metadata["deployment"]["idempotencyScope"] == "process-local"
    assert metadata["deployment"]["configuredInstances"] == 1
    assert metadata["deployment"]["horizontalScalingSupported"] is False
    assert metadata["dependencies"]["integrity"] == "sha256"
    assert all((ROOT / path).exists() for path in metadata["repositoryPaths"])
