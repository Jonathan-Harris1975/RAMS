"""Independent engineering council for RAMS autonomous code repair.

The council is deliberately fail-closed. It does not write code. It decides whether
an already-bounded AnchorPatch is genuinely micro-surgery or should be escalated.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

SEATS = (
    "Issue Verifier", "Root Cause Reviewer", "Minimal Change Architect",
    "Repository Architecture Reviewer", "Security Reviewer", "Reliability Reviewer",
    "Regression Reviewer", "Test Strategy Reviewer", "Dependency Reviewer",
    "Performance Reviewer", "Operations Reviewer", "Data/Schema Safety Reviewer",
    "Rollback Reviewer", "Final Engineering Chair",
)

SYSTEM = """You are a senior software engineering review council. Review only the proposed bounded patch.
Prefer micro-surgery over refactors. Reject architecture changes, dependency changes, schema/migration changes,
auth/security-policy changes, deployment/infrastructure changes, secrets/config changes, broad rewrites, or any
patch whose root cause is not supported by the supplied evidence. Return JSON only."""

def _payload(issue: dict[str, Any], patch: dict[str, Any], role: str) -> str:
    return json.dumps({
        "role": role,
        "task": {k: issue.get(k) for k in ("taskId","title","description","requiredOutcome","affectedPaths","evidence","classification")},
        "patch": patch,
        "decisionContract": {"decision": "approve|reject|manual_review", "confidence": 0, "defects": [], "reason": ""},
    }, ensure_ascii=False)

def _parse(raw: str) -> dict[str, Any]:
    text=str(raw or "").strip().removeprefix("```json").removesuffix("```").strip()
    data=json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("council response must be object")
    decision=str(data.get("decision","")).lower()
    if decision not in {"approve", "reject", "manual_review"}:
        raise ValueError("invalid council decision")
    return {"decision":decision,"confidence":max(0,min(100,int(data.get("confidence",0)))),"defects":[str(x) for x in data.get("defects",[])][:12],"reason":str(data.get("reason",""))[:1500]}

async def _review(router: Any, model: str, role: str, issue: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    raw=await router.complete_with_model_async(model,_payload(issue,patch,role),SYSTEM,max_tokens=1400,json_mode=True,temperature=0.0)
    return {"role":role,"model":model,**_parse(raw)}

async def run_engineering_council(issue: dict[str, Any], patch: dict[str, Any], cfg: Any, router: Any) -> dict[str, Any]:
    if not cfg.rms_engineering_council_enabled:
        return {"decision":"manual_review","reason":"engineering council disabled","seats":list(SEATS),"reviews":[]}
    models=[
        (cfg.rms_engineering_council_architect_model,"Root Cause + Minimal Change Architecture"),
        (cfg.rms_engineering_council_specialist_model,"Security + Reliability + Regression"),
        (cfg.rms_engineering_council_specialist_model,"Tests + Dependencies + Performance + Operations + Data Safety"),
    ]
    try:
        reviews=list(await asyncio.gather(*[_review(router,m,r,issue,patch) for m,r in models]))
        if any(r["decision"] != "approve" or r["confidence"] < 80 for r in reviews):
            return {"decision":"manual_review","reason":"one or more specialist panels did not confidently approve","seats":list(SEATS),"reviews":reviews}
        chair=await _review(router,cfg.rms_engineering_council_chair_model,"Final Engineering Chair",issue,patch)
        reviews.append(chair)
        decision="approve_micro_surgery" if chair["decision"]=="approve" and chair["confidence"]>=85 else "manual_review"
        return {"decision":decision,"reason":chair["reason"],"seats":list(SEATS),"reviews":reviews}
    except Exception as exc:
        return {"decision":"manual_review","reason":f"engineering council failed closed: {exc}","seats":list(SEATS),"reviews":[]}
