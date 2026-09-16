"""Strict loader for RAMS repository-local capability records.

The files under ``config/skills`` describe native RAMS behaviour only. They are
versioned with the application and never downloaded, installed or executed as
third-party skill bundles.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_SKILLS_DIR = REPO_ROOT / "config" / "skills"
LOCAL_SKILL_SCHEMA = "2026-09-15.repository-skill.v1"
LOCAL_SKILL_ID = re.compile(r"^RAMS-sk\d{3}$")


class LocalSkillConfigurationError(ValueError):
    """Raised when a bundled local capability record is unsafe or incomplete."""


@lru_cache(maxsize=1)
def load_local_skills() -> tuple[dict[str, Any], ...]:
    """Load and validate every bundled RAMS local capability record."""

    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_slugs: set[str] = set()
    for path in sorted(LOCAL_SKILLS_DIR.glob("RAMS-sk*.local.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LocalSkillConfigurationError(f"Invalid local skill file: {path.name}") from exc
        if not isinstance(raw, dict):
            raise LocalSkillConfigurationError(f"Local skill must be an object: {path.name}")
        record = dict(raw)
        skill_id = _required_text(record, "id", path)
        slug = _required_text(record, "slug", path)
        for field in ("title", "description", "risk"):
            _required_text(record, field, path)
        if record.get("schema_version") != LOCAL_SKILL_SCHEMA:
            raise LocalSkillConfigurationError(f"Unexpected schema version: {path.name}")
        if not LOCAL_SKILL_ID.fullmatch(skill_id) or not path.name.startswith(f"{skill_id}-"):
            raise LocalSkillConfigurationError(f"Invalid RAMS skill identity: {path.name}")
        if skill_id in seen_ids or slug in seen_slugs:
            raise LocalSkillConfigurationError(f"Duplicate RAMS skill identity: {path.name}")
        seen_ids.add(skill_id)
        seen_slugs.add(slug)
        implementations = _required_string_list(record, "implementation_paths", path)
        for implementation in implementations:
            local_path = _safe_repo_path(implementation, path)
            if not local_path.is_file():
                raise LocalSkillConfigurationError(
                    f"Missing implementation path {implementation!r} in {path.name}"
                )
        _required_string_list(record, "pipelines", path)
        _required_string_list(record, "tags", path)
        origin = record.get("origin")
        if not isinstance(origin, dict) or origin.get("type") != "repository-native":
            raise LocalSkillConfigurationError(f"Invalid local origin in {path.name}")
        if origin.get("external_skill_content_copied") is not False:
            raise LocalSkillConfigurationError(f"External content provenance is unsafe: {path.name}")
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        record["source_path"] = relative_path
        record["source_uri"] = f"repo://{relative_path}"
        records.append(record)
    if not records:
        raise LocalSkillConfigurationError("No RAMS local capability records were found")
    return tuple(records)


def local_skill_reference(skill_id: str) -> str:
    """Return the repository URI for a known local skill identifier."""

    wanted = str(skill_id or "").strip().lower()
    for record in load_local_skills():
        if str(record["id"]).lower() == wanted:
            return str(record["source_uri"])
    raise KeyError(f"Unknown RAMS local skill: {skill_id}")


def rams_local_skills_contract(*, pipeline_id: str | None = None) -> dict[str, Any]:
    """Return deterministic local-capability provenance for RAMS reports."""

    all_records = load_local_skills()
    active = [
        dict(record)
        for record in all_records
        if pipeline_id is None or pipeline_id in record.get("pipelines", [])
    ]
    return {
        "source": "RAMS repository-local native capabilities",
        "mode": "repository-local-native",
        "pipeline": pipeline_id,
        "cataloguePath": "config/skills",
        "accessMode": "local-read-only",
        "skillCount": len(active),
        "skills": active,
        "sharedBucketRequired": False,
        "externalNetworkRequired": False,
        "runtimeInstallRequired": False,
        "governance": {
            "repo": "RAMS",
            "localAgentsFolderRequired": False,
            "allowDirectMetadataExecution": False,
            "allowRepoWritesFromMetadata": False,
            "externalSkillBundlesAllowed": False,
            "allowedRepoUse": [
                "describe existing native RAMS capabilities in reports",
                "route approved pipeline work to existing RAMS modules",
                "validate that declared native implementation paths exist",
            ],
            "blockedRepoUse": [
                "download or install third-party skill bundles",
                "load skill instructions from object storage",
                "treat capability metadata as permission to patch, push or deploy",
            ],
        },
    }


def _required_text(record: dict[str, Any], field: str, path: Path) -> str:
    value = str(record.get(field) or "").strip()
    if not value:
        raise LocalSkillConfigurationError(f"Missing {field!r} in {path.name}")
    return value


def _required_string_list(record: dict[str, Any], field: str, path: Path) -> list[str]:
    value = record.get(field)
    if not isinstance(value, list):
        raise LocalSkillConfigurationError(f"Missing {field!r} list in {path.name}")
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    if not cleaned:
        raise LocalSkillConfigurationError(f"Empty {field!r} list in {path.name}")
    return cleaned


def _safe_repo_path(value: str, source: Path) -> Path:
    candidate = PurePosixPath(str(value).replace("\\", "/"))
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise LocalSkillConfigurationError(f"Unsafe implementation path in {source.name}")
    resolved = (REPO_ROOT / Path(*candidate.parts)).resolve()
    if not resolved.is_relative_to(REPO_ROOT.resolve()):
        raise LocalSkillConfigurationError(f"Implementation escapes repository in {source.name}")
    return resolved
