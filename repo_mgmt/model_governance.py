"""Validate, persist and apply HIVE AI Council model governance for RAMS."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from repo_mgmt.config import Settings
from repo_mgmt.model_policy import clean_justification_id, is_premium_model
from repo_mgmt.r2_client import R2Client, R2Error

logger = logging.getLogger(__name__)

_STATE_KEY = "state/model-governance/rams.json"
_SCHEMA_VERSION = "rams-model-governance/v2"
_RETIREMENT_WINDOW_DAYS = 30
_PREMIUM_COST_MULTIPLIER = 2.0
_PREMIUM_APPROVAL_MAX_DAYS = 90
_KNOWN_RETIRED_OR_DEPRECATED_MODELS = frozenset(
    {
        # Deprecated by OpenRouter on 1 September 2026. Use the regular Opus 5
        # model with the fast service tier instead of this dedicated alias.
        "anthropic/claude-opus-5-fast",
    }
)
_INACTIVE_STATUSES = frozenset(
    {
        "blocked",
        "deprecated",
        "disabled",
        "expired",
        "missing",
        "policy-incompatible",
        "retired",
        "retiring",
        "unavailable",
    }
)
_ASSIGNMENT_FIELDS = {
    "OPENROUTER_PRIMARY_MODEL": "openrouter_primary_model",
    "OPENROUTER_SECONDARY_MODEL": "openrouter_secondary_model",
    "OPENROUTER_TRIAGE_MODEL": "openrouter_triage_model",
    "RMS_ENGINEERING_COUNCIL_ARCHITECT_MODEL": "rms_engineering_council_architect_model",
    "RMS_ENGINEERING_COUNCIL_SPECIALIST_MODEL": "rms_engineering_council_specialist_model",
    "RMS_ENGINEERING_COUNCIL_CHAIR_MODEL": "rms_engineering_council_chair_model",
}
_ROLE_ALIASES = {
    "primary": frozenset({"primary", "patch", "patch-planning", "patch_planning", "standard"}),
    "secondary": frozenset({"secondary", "fallback", "standard"}),
    "triage": frozenset({"triage", "classification", "lightweight", "fast"}),
    "architect": frozenset({"architect", "architecture", "complex", "planning"}),
    "specialist": frozenset({"specialist", "review", "complex", "coding"}),
    "chair": frozenset({"chair", "adjudication", "expert"}),
}
_ROLE_ENV_NAMES = {
    "primary": "OPENROUTER_PRIMARY_MODEL",
    "secondary": "OPENROUTER_SECONDARY_MODEL",
    "triage": "OPENROUTER_TRIAGE_MODEL",
    "architect": "RMS_ENGINEERING_COUNCIL_ARCHITECT_MODEL",
    "specialist": "RMS_ENGINEERING_COUNCIL_SPECIALIST_MODEL",
    "chair": "RMS_ENGINEERING_COUNCIL_CHAIR_MODEL",
}
_ROLE_CATEGORIES = {
    "primary": ("coding", "reasoning", "planning"),
    "secondary": ("coding", "reasoning", "planning", "fast", "cheap"),
    "triage": ("fast", "cheap", "reasoning"),
    "architect": ("complex", "reasoning", "planning", "coding"),
    "specialist": ("complex", "coding", "reasoning", "planning"),
    "chair": ("expert", "reasoning", "planning", "coding"),
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


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _score(item: Mapping[str, object]) -> float:
    for key in ("task_score", "quality_score", "score"):
        parsed = _number(item.get(key))
        if parsed is not None:
            return parsed
    return 0.0


def _reliability_score(item: Mapping[str, object]) -> float:
    for key in ("reliability_score", "availability", "success_rate"):
        parsed = _number(item.get(key))
        if parsed is not None:
            return parsed
    return 0.0


def _estimated_task_cost(item: Mapping[str, object]) -> float | None:
    """Return HIVE's measured/estimated cost for one successful role task."""
    for key in (
        "cost_per_successful_task",
        "estimated_cost_per_task",
        "expected_cost_per_task",
    ):
        parsed = _number(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _model_id(item: Mapping[str, object]) -> str:
    return str(item.get("model_id") or item.get("id") or "").strip()


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


def _role_allowed(item: Mapping[str, object], role: str) -> bool:
    configured = item.get("approved_roles", item.get("roles"))
    if configured is None:
        return True
    if isinstance(configured, str):
        values = {part.strip().lower() for part in configured.split(",") if part.strip()}
    elif isinstance(configured, Sequence) and not isinstance(configured, (bytes, bytearray)):
        values = {str(part).strip().lower() for part in configured if str(part).strip()}
    else:
        return False
    accepted = set(_ROLE_ALIASES.get(role, frozenset({role})))
    accepted.add(role)
    accepted.add(_ROLE_ENV_NAMES.get(role, "").lower())
    return bool(values & accepted)


def _candidate_eligible(
    item: Mapping[str, object],
    *,
    role: str,
    now: datetime,
) -> bool:
    model_id = _model_id(item)
    if (
        not model_id
        or model_id in _KNOWN_RETIRED_OR_DEPRECATED_MODELS
        or not _role_allowed(item, role)
    ):
        return False

    status = str(item.get("status") or "active").strip().lower().replace("_", "-")
    if status in _INACTIVE_STATUSES:
        return False
    for field in (
        "available",
        "compatible",
        "data_policy_compatible",
        "privacy_compatible",
    ):
        if _optional_bool(item.get(field)) is False:
            return False
    if _optional_bool(item.get("evaluation_passed")) is False:
        return False

    expiration_value = item.get("expiration_date", item.get("expires_at"))
    if expiration_value not in (None, ""):
        expiration = _parse_datetime(expiration_value)
        if expiration is None:
            return False
        if expiration <= now + timedelta(days=_RETIREMENT_WINDOW_DAYS):
            return False

    parameters = item.get("supported_parameters")
    if (
        role != "triage"
        and isinstance(parameters, Sequence)
        and not isinstance(parameters, (str, bytes, bytearray))
    ):
        supported = {str(value).strip() for value in parameters}
        if supported and not {"response_format", "structured_outputs"} & supported:
            return False
    return True


def _ranked_models(
    registry: Mapping[str, object],
    category: str,
    *,
    role: str,
    now: datetime,
) -> list[dict[str, object]]:
    raw = registry.get(category, [])
    if not isinstance(raw, list):
        return []
    items: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        normalized = {str(key): value for key, value in item.items()}
        normalized["_category"] = category
        if _candidate_eligible(normalized, role=role, now=now):
            items.append(normalized)

    evaluated = [item for item in items if _optional_bool(item.get("evaluation_passed")) is True]
    if evaluated:
        items = evaluated

    if any(_estimated_task_cost(item) is not None for item in items):
        return sorted(
            items,
            key=lambda item: (
                _estimated_task_cost(item) is None,
                _estimated_task_cost(item) or 0.0,
                -_score(item),
                -_reliability_score(item),
                _model_id(item),
            ),
        )
    return sorted(
        items,
        key=lambda item: (-_score(item), -_reliability_score(item), _model_id(item)),
    )


def _first_selection(
    registry: Mapping[str, object],
    role: str,
    *categories: str,
    exclude: frozenset[str] = frozenset(),
    now: datetime,
) -> dict[str, object] | None:
    for category in categories:
        for item in _ranked_models(registry, category, role=role, now=now):
            if _model_id(item) not in exclude:
                return item
    return None


def _build_rams_model_selections(
    registry: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, object]]:
    checked_at = now or datetime.now(UTC)
    primary = _first_selection(
        registry, "primary", "coding", "reasoning", "planning", now=checked_at
    )
    primary_id = _model_id(primary or {})
    secondary = _first_selection(
        registry,
        "secondary",
        "coding",
        "reasoning",
        "planning",
        "fast",
        "cheap",
        exclude=frozenset({primary_id}) if primary_id else frozenset(),
        now=checked_at,
    )
    if secondary is None:
        secondary = primary

    triage = _first_selection(registry, "triage", "fast", "cheap", "reasoning", now=checked_at)
    architect = _first_selection(
        registry,
        "architect",
        "complex",
        "reasoning",
        "planning",
        "coding",
        now=checked_at,
    )
    architect_id = _model_id(architect or {})
    specialist = _first_selection(
        registry,
        "specialist",
        "complex",
        "coding",
        "reasoning",
        "planning",
        exclude=frozenset({architect_id}) if architect_id else frozenset(),
        now=checked_at,
    )
    if specialist is None:
        specialist = architect
    chair = _first_selection(
        registry,
        "chair",
        "expert",
        "reasoning",
        "planning",
        "coding",
        now=checked_at,
    )

    candidates = {
        "OPENROUTER_PRIMARY_MODEL": primary,
        "OPENROUTER_SECONDARY_MODEL": secondary,
        "OPENROUTER_TRIAGE_MODEL": triage,
        "RMS_ENGINEERING_COUNCIL_ARCHITECT_MODEL": architect,
        "RMS_ENGINEERING_COUNCIL_SPECIALIST_MODEL": specialist,
        "RMS_ENGINEERING_COUNCIL_CHAIR_MODEL": chair,
    }
    return {name: item for name, item in candidates.items() if item is not None}


def build_rams_model_assignments(registry: Mapping[str, object]) -> dict[str, str]:
    """Map qualified HIVE candidates onto the model roles RAMS actually uses."""
    return {
        env_name: _model_id(item)
        for env_name, item in _build_rams_model_selections(registry).items()
        if _model_id(item)
    }


def _role_candidates(
    registry: Mapping[str, object],
    role: str,
    now: datetime,
) -> list[dict[str, object]]:
    seen: set[str] = set()
    candidates: list[dict[str, object]] = []
    for category in _ROLE_CATEGORIES[role]:
        for item in _ranked_models(registry, category, role=role, now=now):
            model_id = _model_id(item)
            if model_id not in seen:
                seen.add(model_id)
                candidates.append(item)
    return candidates


def _cost_requires_justification(
    selected: Mapping[str, object],
    alternatives: Sequence[Mapping[str, object]],
) -> bool:
    selected_cost = _estimated_task_cost(selected)
    if selected_cost is None:
        return False
    other_costs = [
        cost
        for item in alternatives
        if _model_id(item) != _model_id(selected)
        if (cost := _estimated_task_cost(item)) is not None
    ]
    return bool(other_costs) and selected_cost > min(other_costs) * _PREMIUM_COST_MULTIPLIER


def _field(data: Mapping[str, object], *names: str) -> object:
    for name in names:
        value = data.get(name)
        if value not in (None, ""):
            return value
    return ""


def _normalise_premium_justification(
    item: Mapping[str, object],
    *,
    now: datetime,
) -> dict[str, object]:
    raw = item.get("premium_justification", item.get("justification"))
    if not isinstance(raw, Mapping):
        raise TypeError(
            f"Premium model {_model_id(item)!r} requires a structured premium_justification"
        )

    justification_id = clean_justification_id(
        _field(raw, "justification_id", "justificationId", "id")
    )
    owner = str(_field(raw, "owner", "requested_by", "requestedBy")).strip()[:200]
    reason = str(_field(raw, "reason", "business_reason", "businessReason")).strip()[:2000]
    cheaper_model = str(_field(raw, "cheaper_model_tested", "cheaperModelTested")).strip()[:200]
    approved_by = str(_field(raw, "approved_by", "approvedBy")).strip()[:200]
    evidence_raw = _field(raw, "evidence", "evaluation_evidence", "evaluationEvidence")
    if isinstance(evidence_raw, Sequence) and not isinstance(evidence_raw, (str, bytes, bytearray)):
        evidence: str | list[str] = [
            str(value).strip()[:500] for value in evidence_raw[:12] if str(value).strip()
        ]
    else:
        evidence = str(evidence_raw).strip()[:3000]
    expires_raw = _field(raw, "expires_at", "expiresAt")
    expires_at = _parse_datetime(expires_raw)

    missing: list[str] = []
    for name, value in (
        ("justificationId", justification_id),
        ("owner", owner),
        ("reason", reason),
        ("cheaperModelTested", cheaper_model),
        ("evidence", evidence),
        ("approvedBy", approved_by),
        ("expiresAt", expires_at),
    ):
        if not value:
            missing.append(name)
    if missing:
        raise ValueError(
            f"Premium model {_model_id(item)!r} justification is missing: {', '.join(missing)}"
        )
    if expires_at is not None and expires_at <= now:
        raise ValueError(f"Premium model {_model_id(item)!r} justification has expired")
    if expires_at is not None and expires_at > now + timedelta(days=_PREMIUM_APPROVAL_MAX_DAYS):
        raise ValueError(
            f"Premium model {_model_id(item)!r} justification exceeds "
            f"{_PREMIUM_APPROVAL_MAX_DAYS} days"
        )
    if cheaper_model.casefold() == _model_id(item).casefold():
        raise ValueError(
            f"Premium model {_model_id(item)!r} justification must name a cheaper model"
        )
    if owner.casefold() == approved_by.casefold():
        raise ValueError(f"Premium model {_model_id(item)!r} requester cannot self-approve")

    return {
        "justificationId": justification_id,
        "owner": owner,
        "reason": reason,
        "cheaperModelTested": cheaper_model,
        "evidence": evidence,
        "approvedBy": approved_by,
        "expiresAt": expires_at.isoformat() if expires_at is not None else "",
    }


def _apply_assignments(cfg: Settings, assignments: Mapping[str, object]) -> None:
    for env_name, raw_value in assignments.items():
        field_name = _ASSIGNMENT_FIELDS.get(env_name)
        if field_name is None:
            continue
        value = str(raw_value or "").strip()
        if not value:
            raise ValueError(f"Invalid persisted model assignment for {env_name}")
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
                "model_governance: ignored missing or expired premium approval role=%s",
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


def apply_rams_model_governance(
    cfg: Settings,
    r2: R2Client,
    *,
    registry: Mapping[str, object],
    source_run_id: str | None,
) -> dict[str, Any]:
    """Persist qualified HIVE model selections, then activate them atomically."""
    now = datetime.now(UTC)
    selections = _build_rams_model_selections(registry, now=now)
    assignments = {
        env_name: _model_id(item) for env_name, item in selections.items() if _model_id(item)
    }
    clean_source_run_id = str(source_run_id or "").strip() or None
    if not assignments:
        return {
            "ok": True,
            "applied": False,
            "persisted": False,
            "reason": "no-qualified-active-models",
            "sourceRunId": clean_source_run_id,
        }

    premium_approvals: dict[str, dict[str, object]] = {}
    decisions: dict[str, dict[str, object]] = {}
    for env_name, item in selections.items():
        role = next(
            role_name
            for role_name, candidate_env in _ROLE_ENV_NAMES.items()
            if candidate_env == env_name
        )
        alternatives = _role_candidates(registry, role, now)
        premium = is_premium_model(_model_id(item), item) or _cost_requires_justification(
            item, alternatives
        )
        justification_id: str | None = None
        if premium:
            approval = _normalise_premium_justification(item, now=now)
            premium_approvals[env_name] = approval
            justification_id = str(approval["justificationId"])
        decisions[env_name] = {
            "modelId": _model_id(item),
            "role": role,
            "sourceCategory": str(item.get("_category") or ""),
            "score": _score(item),
            "reliabilityScore": _reliability_score(item),
            "estimatedCostPerTask": _estimated_task_cost(item),
            "expirationDate": item.get("expiration_date", item.get("expires_at")),
            "premium": premium,
            "justificationId": justification_id,
        }

    payload: dict[str, Any] = {
        "schemaVersion": _SCHEMA_VERSION,
        "source": "HIVE AI Council",
        "sourceRunId": clean_source_run_id,
        "appliedAt": now.isoformat(),
        "policy": {
            "selection": "lowest-qualified-cost-first",
            "retirementWindowDays": _RETIREMENT_WINDOW_DAYS,
            "premiumCostMultiplier": _PREMIUM_COST_MULTIPLIER,
            "premiumApprovalMaxDays": _PREMIUM_APPROVAL_MAX_DAYS,
            "budgetBehaviour": "observe-and-review-never-stop-authorised-work",
        },
        "assignments": assignments,
        "decisions": decisions,
        "premiumApprovals": premium_approvals,
    }
    # Persist first: a successful response means the governed selection survives
    # the next Koyeb restart rather than being a process-only change.
    r2.put_object(
        cfg.r2_bucket_audits,
        _STATE_KEY,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        "application/json",
    )
    _apply_assignments(cfg, assignments)
    _apply_premium_approvals(cfg, premium_approvals, decisions)
    logger.info(
        "model_governance: applied HIVE selection source_run_id=%s assignments=%s",
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
        raw = r2.get_object_limited(
            cfg.r2_bucket_audits, _STATE_KEY, cfg.rms_report_max_bytes
        )
    except R2Error as exc:
        text = str(exc)
        logger.warning("model_governance: restore failed: %s", exc)
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
        logger.warning("model_governance: invalid persisted state: %s", exc)
        return {"ok": False, "restored": False, "error": str(exc)}

    source_run_id = payload.get("sourceRunId")
    logger.info("model_governance: restored source_run_id=%s", source_run_id)
    return {
        "ok": True,
        "restored": True,
        "sourceRunId": source_run_id,
        "schemaVersion": payload.get("schemaVersion", "rams-model-governance/v1"),
    }
