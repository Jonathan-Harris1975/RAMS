"""Cost-aware, fail-closed engineering council for RAMS code repair."""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any

from repo_mgmt.model_policy import (
    clean_justification_id,
    is_premium_model,
    premium_approval_is_active,
)

SEATS = (
    "Issue Verifier",
    "Root Cause Reviewer",
    "Minimal Change Architect",
    "Repository Architecture Reviewer",
    "Security Reviewer",
    "Reliability Reviewer",
    "Regression Reviewer",
    "Test Strategy Reviewer",
    "Dependency Reviewer",
    "Performance Reviewer",
    "Operations Reviewer",
    "Data/Schema Safety Reviewer",
    "Rollback Reviewer",
    "Final Engineering Chair",
)

SYSTEM = """You are a senior software engineering review council. Review only the proposed bounded patch.
Prefer micro-surgery over refactors. Reject architecture changes, dependency changes, schema/migration changes,
auth/security-policy changes, deployment/infrastructure changes, secrets/config changes, broad rewrites, or any
patch whose root cause is not supported by the supplied evidence. Return JSON only."""

_SPECIALIST_REVIEW_ROLE = "Security + Reliability + Regression + Tests + Dependencies + Performance + Operations + Data Safety"


def _payload(issue: dict[str, Any], patch: dict[str, Any], role: str) -> str:
    return json.dumps(
        {
            "role": role,
            "task": {
                key: issue.get(key)
                for key in (
                    "taskId",
                    "title",
                    "description",
                    "requiredOutcome",
                    "affectedPaths",
                    "evidence",
                    "classification",
                )
            },
            "patch": patch,
            "decisionContract": {
                "decision": "approve|reject|manual_review",
                "confidence": 0,
                "defects": [],
                "reason": "",
            },
        },
        ensure_ascii=False,
    )


def _parse(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip().removeprefix("```json").removesuffix("```").strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise TypeError("council response must be object")
    decision = str(data.get("decision", "")).lower()
    if decision not in {"approve", "reject", "manual_review"}:
        raise ValueError("invalid council decision")
    defects = data.get("defects", [])
    if not isinstance(defects, list):
        defects = [str(defects)]
    return {
        "decision": decision,
        "confidence": max(0, min(100, int(data.get("confidence", 0)))),
        "defects": [str(value) for value in defects][:12],
        "reason": str(data.get("reason", ""))[:1500],
    }


async def _review(
    router: Any,
    model: str,
    role: str,
    governance_role: str,
    issue: dict[str, Any],
    patch: dict[str, Any],
    *,
    justification_id: str | None = None,
) -> dict[str, Any]:
    raw = await router.complete_with_model_async(
        model,
        _payload(issue, patch, role),
        SYSTEM,
        max_tokens=1400,
        json_mode=True,
        temperature=0.0,
        governance_role=governance_role,
        justification_id=justification_id,
    )
    return {"role": role, "model": model, **_parse(raw)}


def _manual(reason: str, reviews: list[dict[str, Any]], route: str) -> dict[str, Any]:
    return {
        "decision": "manual_review",
        "reason": reason,
        "route": route,
        "seats": list(SEATS),
        "reviews": reviews,
    }


def _chair_justification(cfg: Any) -> str:
    approvals = getattr(cfg, "rms_model_governance_premium_approvals", {})
    expiries = getattr(cfg, "rms_model_governance_premium_approval_expiries", {})
    stored = (
        approvals.get("RMS_ENGINEERING_COUNCIL_CHAIR_MODEL", "")
        if isinstance(approvals, dict)
        else ""
    )
    expiry = (
        expiries.get("RMS_ENGINEERING_COUNCIL_CHAIR_MODEL", "")
        if isinstance(expiries, dict)
        else ""
    )
    if not premium_approval_is_active(expiry):
        return ""
    return clean_justification_id(stored)


async def run_engineering_council(
    issue: dict[str, Any],
    patch: dict[str, Any],
    cfg: Any,
    router: Any,
) -> dict[str, Any]:
    """Approve clear consensus cheaply and reserve expert adjudication for ambiguity."""
    if not cfg.rms_engineering_council_enabled:
        return _manual("engineering council disabled", [], "disabled")

    standard_reviews = (
        (
            cfg.rms_engineering_council_architect_model,
            "Root Cause + Minimal Change Architecture",
            "RMS_ENGINEERING_COUNCIL_ARCHITECT_MODEL",
        ),
        (
            cfg.rms_engineering_council_specialist_model,
            _SPECIALIST_REVIEW_ROLE,
            "RMS_ENGINEERING_COUNCIL_SPECIALIST_MODEL",
        ),
    )
    review_threshold = int(cfg.rms_engineering_council_review_confidence)
    tolerance_percent = min(
        5,
        max(
            0,
            int(
                getattr(
                    cfg,
                    "rms_engineering_council_near_threshold_tolerance_percent",
                    5,
                )
            ),
        ),
    )
    review_margin = math.ceil(review_threshold * tolerance_percent / 100)
    effective_review_threshold = max(0, review_threshold - review_margin)
    try:
        reviews = list(
            await asyncio.gather(
                *[
                    _review(router, model, role, governance_role, issue, patch)
                    for model, role, governance_role in standard_reviews
                ]
            )
        )

        confident_rejection = any(
            review["decision"] == "reject" and review["confidence"] >= review_threshold
            for review in reviews
        )
        if confident_rejection:
            return _manual(
                "a standard reviewer confidently rejected the patch",
                reviews,
                "standard-rejection",
            )

        standard_consensus = all(
            review["decision"] == "approve"
            and review["confidence"] >= effective_review_threshold
            for review in reviews
        )
        if standard_consensus:
            accepted_under_tolerance = any(
                review["confidence"] < review_threshold for review in reviews
            )
            return {
                "decision": "approve_micro_surgery",
                "reason": "independent standard reviewers approved the bounded patch",
                "route": "standard-consensus",
                "seats": list(SEATS),
                "reviews": reviews,
                "threshold": review_threshold,
                "effectiveThreshold": effective_review_threshold,
                "acceptedUnderTolerance": accepted_under_tolerance,
            }

        chair_model = cfg.rms_engineering_council_chair_model
        premium_chair = (
            is_premium_model(chair_model)
            or "RMS_ENGINEERING_COUNCIL_CHAIR_MODEL"
            in getattr(cfg, "rms_model_governance_premium_roles", set())
            or chair_model in getattr(cfg, "rms_model_governance_premium_models", set())
        )
        justification_id = _chair_justification(cfg)
        if premium_chair and not cfg.rms_engineering_council_expert_enabled:
            return _manual(
                "expert adjudication is required but is not enabled",
                reviews,
                "expert-not-enabled",
            )
        if premium_chair and not justification_id:
            return _manual(
                "expert adjudication requires an active premium justification",
                reviews,
                "expert-not-justified",
            )

        chair = await _review(
            router,
            chair_model,
            "Final Engineering Chair",
            "RMS_ENGINEERING_COUNCIL_CHAIR_MODEL",
            issue,
            patch,
            justification_id=justification_id or None,
        )
        reviews.append(chair)
        chair_threshold = int(cfg.rms_engineering_council_chair_confidence)
        chair_margin = math.ceil(chair_threshold * tolerance_percent / 100)
        effective_chair_threshold = max(0, chair_threshold - chair_margin)
        approved = (
            chair["decision"] == "approve"
            and chair["confidence"] >= effective_chair_threshold
        )
        accepted_under_tolerance = bool(
            approved and chair["confidence"] < chair_threshold
        )
        return {
            "decision": "approve_micro_surgery" if approved else "manual_review",
            "reason": chair["reason"] or "expert chair did not approve the patch",
            "route": "expert-adjudication" if premium_chair else "chair-adjudication",
            "seats": list(SEATS),
            "reviews": reviews,
            "premiumJustificationId": justification_id if premium_chair else None,
            "threshold": chair_threshold,
            "effectiveThreshold": effective_chair_threshold,
            "acceptedUnderTolerance": accepted_under_tolerance,
        }
    except Exception as exc:  # noqa: BLE001 - every council failure must fail closed
        return _manual(
            f"engineering council failed closed: {exc}",
            [],
            "council-failure",
        )
