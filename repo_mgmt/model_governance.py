"""Persist and apply HIVE AI Council model governance for RAMS."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from repo_mgmt.config import Settings
from repo_mgmt.r2_client import R2Client, R2Error

logger = logging.getLogger(__name__)

_STATE_KEY = "state/model-governance/rams.json"
_ASSIGNMENT_FIELDS = {
    "OPENROUTER_PRIMARY_MODEL": "openrouter_primary_model",
    "OPENROUTER_SECONDARY_MODEL": "openrouter_secondary_model",
    "OPENROUTER_TRIAGE_MODEL": "openrouter_triage_model",
    "RMS_ENGINEERING_COUNCIL_ARCHITECT_MODEL": "rms_engineering_council_architect_model",
    "RMS_ENGINEERING_COUNCIL_SPECIALIST_MODEL": "rms_engineering_council_specialist_model",
    "RMS_ENGINEERING_COUNCIL_CHAIR_MODEL": "rms_engineering_council_chair_model",
}


def _score(item: Mapping[str, object]) -> float:
    raw = item.get("score")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return 0.0
    return 0.0


def _ranked_models(registry: Mapping[str, object], category: str) -> list[dict[str, object]]:
    raw = registry.get(category, [])
    if not isinstance(raw, list):
        return []
    items: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        normalized = {str(key): value for key, value in item.items()}
        if str(normalized.get("model_id") or "").strip():
            items.append(normalized)
    return sorted(items, key=_score, reverse=True)


def _first_model(registry: Mapping[str, object], *categories: str) -> str:
    for category in categories:
        ranked = _ranked_models(registry, category)
        if ranked:
            return str(ranked[0]["model_id"]).strip()
    return ""


def _first_distinct_model(
    registry: Mapping[str, object], primary: str, *categories: str
) -> str:
    for category in categories:
        for item in _ranked_models(registry, category):
            model_id = str(item["model_id"]).strip()
            if model_id and model_id != primary:
                return model_id
    return primary


def build_rams_model_assignments(registry: Mapping[str, object]) -> dict[str, str]:
    """Map HIVE model categories onto the model roles RAMS actually uses."""
    primary = _first_model(registry, "coding", "reasoning", "planning")
    secondary = _first_distinct_model(
        registry, primary, "coding", "reasoning", "planning", "fast", "cheap"
    )
    triage = _first_model(registry, "fast", "cheap", "reasoning")
    architect = _first_model(registry, "reasoning", "planning", "coding")
    specialist = _first_model(registry, "coding", "reasoning", "planning")
    chair = _first_model(registry, "reasoning", "planning", "coding")

    candidates = {
        "OPENROUTER_PRIMARY_MODEL": primary,
        "OPENROUTER_SECONDARY_MODEL": secondary,
        "OPENROUTER_TRIAGE_MODEL": triage,
        "RMS_ENGINEERING_COUNCIL_ARCHITECT_MODEL": architect,
        "RMS_ENGINEERING_COUNCIL_SPECIALIST_MODEL": specialist,
        "RMS_ENGINEERING_COUNCIL_CHAIR_MODEL": chair,
    }
    return {name: value for name, value in candidates.items() if value}


def _apply_assignments(cfg: Settings, assignments: Mapping[str, object]) -> None:
    for env_name, raw_value in assignments.items():
        field_name = _ASSIGNMENT_FIELDS.get(env_name)
        if field_name is None:
            continue
        value = str(raw_value or "").strip()
        if not value:
            raise ValueError(f"Invalid persisted model assignment for {env_name}")
        setattr(cfg, field_name, value)


def apply_rams_model_governance(
    cfg: Settings,
    r2: R2Client,
    *,
    registry: Mapping[str, object],
    source_run_id: str | None,
) -> dict[str, Any]:
    """Persist HIVE model selections, then apply them to the live RAMS settings."""
    assignments = build_rams_model_assignments(registry)
    clean_source_run_id = str(source_run_id or "").strip() or None
    if not assignments:
        return {
            "ok": True,
            "applied": False,
            "persisted": False,
            "reason": "no-compatible-ranked-models",
            "sourceRunId": clean_source_run_id,
        }

    payload: dict[str, Any] = {
        "schemaVersion": "rams-model-governance/v1",
        "source": "HIVE AI Council",
        "sourceRunId": clean_source_run_id,
        "appliedAt": datetime.now(timezone.utc).isoformat(),
        "assignments": assignments,
    }
    # Persist first. A successful API response must mean the governance selection
    # survives the next Koyeb restart rather than being a process-only illusion.
    r2.put_object(
        cfg.r2_bucket_audits,
        _STATE_KEY,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        "application/json",
    )
    _apply_assignments(cfg, assignments)
    logger.info(
        "model_governance: applied HIVE Council selection source_run_id=%s assignments=%s",
        clean_source_run_id,
        assignments,
    )
    return {
        "ok": True,
        "applied": True,
        "persisted": True,
        "bucket": cfg.r2_bucket_audits,
        "key": _STATE_KEY,
        **payload,
    }


def restore_rams_model_governance(cfg: Settings, r2: R2Client) -> dict[str, Any]:
    """Restore the most recently persisted HIVE model selection on startup."""
    try:
        if not r2.object_exists(cfg.r2_bucket_audits, _STATE_KEY):
            return {"ok": True, "restored": False, "reason": "no-persisted-model-governance"}
        raw = r2.get_object(cfg.r2_bucket_audits, _STATE_KEY)
    except R2Error as exc:
        text = str(exc)
        logger.warning("model_governance: restore failed: %s", exc)
        return {"ok": False, "restored": False, "error": text}

    try:
        payload = json.loads(raw.decode("utf-8"))
        assignments = payload.get("assignments", {})
        if not isinstance(assignments, dict):
            raise ValueError("persisted assignments must be an object")
        _apply_assignments(cfg, assignments)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("model_governance: invalid persisted state: %s", exc)
        return {"ok": False, "restored": False, "error": str(exc)}

    source_run_id = payload.get("sourceRunId")
    logger.info("model_governance: restored source_run_id=%s", source_run_id)
    return {"ok": True, "restored": True, "sourceRunId": source_run_id}
