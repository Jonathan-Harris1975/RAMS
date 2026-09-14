"""Progressive, governed patch self-improvement before engineering council use."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repo_mgmt import context_builder, patch_planner
from repo_mgmt.patch_protocol import PatchSchemaError, validate_patch

if TYPE_CHECKING:
    from repo_mgmt.config import Settings
    from repo_mgmt.model_router import ModelRouter

_FENCED_JSON_RE = re.compile(
    r"^```(?:json)?\s*(?P<body>.*?)\s*```$", re.DOTALL | re.IGNORECASE
)

# Ordered from the lightest governed role to the strongest bounded review role.
# The configured model behind each role remains owned by HIVE model governance.
_ROLE_LADDER = (
    ("triage", "OPENROUTER_TRIAGE_MODEL", "openrouter_triage_model"),
    ("secondary", "OPENROUTER_SECONDARY_MODEL", "openrouter_secondary_model"),
    ("primary", "OPENROUTER_PRIMARY_MODEL", "openrouter_primary_model"),
    (
        "architect",
        "RMS_ENGINEERING_COUNCIL_ARCHITECT_MODEL",
        "rms_engineering_council_architect_model",
    ),
)

SYSTEM = """You are a bounded code-patch self-improvement reviewer.
Review the current AnchorPatch/v1 candidate against the supplied task and exact file context.
Prefer the smallest safe patch. Do not broaden scope, add dependencies, change architecture, change
security policy, or touch files outside affectedPaths. Return JSON only.

Return exactly this shape:
{
  "decision": "accept|improve|manual_review",
  "confidence": 0,
  "defects": [],
  "reason": "brief evidence-based reason",
  "patch": {"patchProtocol":"AnchorPatch/v1","changes":[]}
}

Use decision=accept only when the supplied/returned patch is ready for deterministic validation.
Use decision=improve when you corrected the patch but a stronger review should still check it.
Use decision=manual_review when a bounded autonomous patch is not justified.
Never include prose outside JSON.
"""


def _payload(
    issue: dict[str, Any],
    patch: dict[str, Any],
    context_files: dict[str, str],
    pipeline_id: str,
    iteration: int,
) -> str:
    return json.dumps(
        {
            "iteration": iteration,
            "pipeline": pipeline_id,
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
            "currentPatch": patch,
            "contextFiles": context_files,
            "acceptanceContract": {
                "decision": "accept|improve|manual_review",
                "confidence": "0..100",
                "defects": [],
                "reason": "brief reason",
                "patch": "valid AnchorPatch/v1 within affectedPaths",
            },
        },
        ensure_ascii=False,
    )


def _parse_response(
    raw: str,
    *,
    task_id: str,
    current_patch: dict[str, Any],
    affected_paths: list[str],
    pipeline_id: str,
) -> dict[str, Any]:
    text = str(raw or "").strip()
    fenced = _FENCED_JSON_RE.match(text)
    if fenced:
        text = fenced.group("body").strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise TypeError("self-improvement response must be an object")

    decision = str(data.get("decision", "")).strip().lower()
    if decision not in {"accept", "improve", "manual_review"}:
        raise ValueError("invalid self-improvement decision")

    defects = data.get("defects", [])
    if not isinstance(defects, list):
        defects = [str(defects)]

    proposed = data.get("patch", current_patch)
    try:
        candidate = validate_patch(proposed)
        patch_planner._validate_plan_scope(candidate, affected_paths, pipeline_id)  # noqa: SLF001
    except (PatchSchemaError, patch_planner.PatchPlanError) as exc:
        raise ValueError(f"invalid self-improvement patch for {task_id}: {exc}") from exc

    return {
        "decision": decision,
        "confidence": max(0, min(100, int(data.get("confidence", 0)))),
        "defects": [str(value) for value in defects][:12],
        "reason": str(data.get("reason", ""))[:1500],
        "patch": candidate,
    }


def _role_model(cfg: Settings, role_entry: tuple[str, str, str]) -> tuple[str, str, str]:
    role, governance_role, attr_name = role_entry
    return role, governance_role, str(getattr(cfg, attr_name, "") or "").strip()


async def run_self_improvement(
    issue: dict[str, Any],
    patch: dict[str, Any],
    target_repo: Path,
    pipeline_id: str,
    cfg: Settings,
    router: ModelRouter,
) -> dict[str, Any]:
    """Refine a patch through progressively stronger governed model roles.

    The function never expands beyond four loops. A loop only replaces the current
    candidate after the returned AnchorPatch passes schema and affected-path checks.
    """
    threshold = int(cfg.rms_self_improvement_confidence)
    max_loops = min(4, max(1, int(cfg.rms_self_improvement_max_loops)))
    candidate = validate_patch(patch)
    affected_paths = [str(path) for path in issue.get("affectedPaths", [])]

    if not cfg.rms_self_improvement_enabled:
        return {
            "decision": "not_run",
            "accepted": False,
            "threshold": threshold,
            "maxLoops": max_loops,
            "loops": [],
            "patch": candidate,
            "reason": "self-improvement disabled",
        }

    context_files = context_builder.load_context(
        affected_paths,
        target_repo,
        max_files=cfg.rms_max_context_files,
        max_file_bytes=cfg.rms_max_context_file_bytes,
        max_total_bytes=cfg.rms_max_context_total_bytes,
    )
    task_id = str(issue.get("taskId", "<unknown>"))
    loop_reports: list[dict[str, Any]] = []

    for iteration, role_entry in enumerate(_ROLE_LADDER[:max_loops], start=1):
        role, governance_role, model = _role_model(cfg, role_entry)
        report: dict[str, Any] = {
            "iteration": iteration,
            "role": role,
            "model": model,
            "threshold": threshold,
            "accepted": False,
        }
        if not model:
            report.update(decision="error", confidence=0, reason="configured model is empty")
            loop_reports.append(report)
            continue

        try:
            raw = await router.complete_with_model_async(
                model,
                _payload(issue, candidate, context_files, pipeline_id, iteration),
                SYSTEM,
                max_tokens=4096,
                json_mode=True,
                temperature=0.0,
                governance_role=governance_role,
            )
            parsed = _parse_response(
                raw,
                task_id=task_id,
                current_patch=candidate,
                affected_paths=affected_paths,
                pipeline_id=pipeline_id,
            )
            previous = candidate
            candidate = parsed["patch"]
            accepted = parsed["decision"] == "accept" and parsed["confidence"] >= threshold
            report.update(
                decision=parsed["decision"],
                confidence=parsed["confidence"],
                defects=parsed["defects"],
                reason=parsed["reason"],
                candidateChanged=candidate != previous,
                accepted=accepted,
            )
            loop_reports.append(report)
            if accepted:
                return {
                    "decision": "accept",
                    "accepted": True,
                    "threshold": threshold,
                    "maxLoops": max_loops,
                    "loops": loop_reports,
                    "patch": candidate,
                    "reason": parsed["reason"] or "self-improvement threshold met",
                }
        except Exception as exc:  # noqa: BLE001 - a failed loop escalates, it does not fail open
            report.update(
                decision="error",
                confidence=0,
                defects=[str(exc)[:1000]],
                reason="loop failed; escalating to the next governed role",
                candidateChanged=False,
            )
            loop_reports.append(report)

    return {
        "decision": "escalate_to_council",
        "accepted": False,
        "threshold": threshold,
        "maxLoops": max_loops,
        "loops": loop_reports,
        "patch": candidate,
        "reason": "self-improvement loops did not meet the acceptance threshold",
    }
