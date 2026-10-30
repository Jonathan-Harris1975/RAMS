"""Read-only startup restoration of pinned operator model defaults.

The legacy object is retained as configuration; no automatic model selection
or downstream apply endpoint exists.
"""
from __future__ import annotations
import json
import logging
from datetime import UTC, datetime
from collections.abc import Mapping
from typing import Any
from repo_mgmt.config import Settings
from repo_mgmt.r2_client import R2Client, R2Error
from repo_mgmt.model_policy import clean_justification_id, is_premium_model
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

def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None

def _parse_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)

def _field(data: Mapping[str, object], *names: str) -> object:
    for name in names:
        value = data.get(name)
        if value not in (None, ""):
            return value
    return ""

def _apply_assignments(cfg: Settings, assignments: Mapping[str, object]) -> None:
    validated = {}
    for env_name, raw_value in assignments.items():
        field_name = _ASSIGNMENT_FIELDS.get(env_name)
        if field_name is None:
            continue
        value = str(raw_value or "").strip()
        if not value:
            raise ValueError(f"Invalid persisted model assignment for {env_name}")
        validated[field_name] = value
    for field_name, value in validated.items():
        setattr(cfg, field_name, value)

def _apply_premium_approvals(
    cfg: Settings,
    approvals: Mapping[str, object],
    decisions: Mapping[str, object],
) -> None:
    normalized: dict[str, str] = {}
    expiries: dict[str, str] = {}
    now = datetime.now(UTC)
    for env_name, raw in approvals.items():
        if env_name not in _ASSIGNMENT_FIELDS or not isinstance(raw, Mapping):
            continue
        expires_at = _parse_datetime(_field(raw, "expiresAt", "expires_at"))
        if expires_at is None or expires_at <= now:
            logger.warning(
                "model_defaults: ignored missing or expired premium approval role=%s",
                env_name,
            )
            continue
        justification_id = clean_justification_id(
            _field(raw, "justificationId", "justification_id", "id")
        )
        if justification_id:
            normalized[env_name] = justification_id
            expiries[env_name] = expires_at.isoformat()
    cfg.rms_model_governance_premium_approvals = normalized
    cfg.rms_model_governance_premium_approval_expiries = expiries

    premium_roles: set[str] = set()
    premium_models: set[str] = set()
    for env_name, raw in decisions.items():
        if env_name not in _ASSIGNMENT_FIELDS or not isinstance(raw, Mapping):
            continue
        if _optional_bool(raw.get("premium")) is not True:
            continue
        premium_roles.add(env_name)
        model_id = str(raw.get("modelId") or raw.get("model_id") or "").strip()
        if model_id:
            premium_models.add(model_id)
    cfg.rms_model_governance_premium_roles = premium_roles
    cfg.rms_model_governance_premium_models = premium_models

    chair_key = "RMS_ENGINEERING_COUNCIL_CHAIR_MODEL"
    chair_approval = normalized.get(chair_key, "")
    cfg.rms_engineering_council_expert_justification_id = chair_approval
    if is_premium_model(cfg.rms_engineering_council_chair_model) or chair_key in premium_roles:
        cfg.rms_engineering_council_expert_enabled = bool(chair_approval)

def restore_pinned_model_defaults(cfg: Settings, r2: R2Client) -> dict[str, Any]:
    """Restore the pinned model defaults on startup."""
    try:
        if not r2.object_exists(cfg.r2_bucket_audits, _STATE_KEY):
            return {"ok": True, "restored": False, "reason": "no-persisted-model-governance"}
        raw = r2.get_object_limited(
            cfg.r2_bucket_audits, _STATE_KEY, cfg.rms_report_max_bytes
        )
    except R2Error as exc:
        text = str(exc)
        logger.warning("model_defaults: restore failed: %s", exc)
        return {"ok": False, "restored": False, "error": text}

    try:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("persisted model governance must be an object")
        assignments = payload.get("assignments", {})
        approvals = payload.get("premiumApprovals", {})
        decisions = payload.get("decisions", {})
        if not isinstance(assignments, dict):
            raise TypeError("persisted assignments must be an object")
        if not isinstance(approvals, dict):
            raise TypeError("persisted premiumApprovals must be an object")
        if not isinstance(decisions, dict):
            raise TypeError("persisted decisions must be an object")
        _apply_assignments(cfg, assignments)
        _apply_premium_approvals(cfg, approvals, decisions)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("model_defaults: invalid persisted state: %s", exc)
        return {"ok": False, "restored": False, "error": str(exc)}

    source_run_id = payload.get("sourceRunId")
    logger.info("model_defaults: restored source_run_id=%s", source_run_id)
    return {
        "ok": True,
        "restored": True,
        "sourceRunId": source_run_id,
        "schemaVersion": payload.get("schemaVersion", "rams-model-governance/v1"),
    }
