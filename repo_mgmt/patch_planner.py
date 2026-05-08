"""AnchorPatch/v1 patch planner for RAMS."""
from __future__ import annotations
import json, logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from repo_mgmt.patch_protocol import PathTraversalError, PatchSchemaError, validate_patch
if TYPE_CHECKING:
    from repo_mgmt.config import Settings
    from repo_mgmt.model_router import ModelRouter
logger=logging.getLogger(__name__); _MAX_FILE_BYTES=256*1024
class PatchPlanError(Exception): pass
SYSTEM_PROMPT="""You are a precise repository remediation engineer.
Return STRICT AnchorPatch/v1 JSON only.
Do not return prose.
Do not return markdown fences.
Do not return commentary.
Do not return a custom schema.
Do not return taskId or operations.
Required root schema: {"patchProtocol":"AnchorPatch/v1","changes":[{"file":"repo-relative path","operation":"replace | insert_after | delete","anchorBefore":"optional unique context string","find":"exact unique text for replace/insert_after, optional for delete","replace":"replacement text for replace/insert_after","rationale":"short reason"}]}
Rules: make the smallest bounded safe change, modify only affectedPaths, use verbatim anchors/find text. If no bounded safe patch is possible, return exactly: {"patchProtocol":"AnchorPatch/v1","changes":[]}.
"""
def plan(issue:dict[str,Any], target_repo:Path, pipeline_id:str, settings:'Settings', model_router:'ModelRouter')->dict[str,Any]:
    task_id=str(issue.get('taskId','<unknown>'))
    if issue.get('classification')!='code_fix':
        raise PatchPlanError(f"plan() called on non-code_fix issue (classification={issue.get('classification')!r})")
    context_files=_load_context(issue.get('affectedPaths',[]), target_repo)
    prompt=_build_prompt(issue, context_files, pipeline_id)
    try: raw=model_router.complete(prompt=prompt, system=SYSTEM_PROMPT, max_tokens=4096)
    except Exception as exc: raise PatchPlanError(f'LLM call failed: {exc}') from exc
    return _parse_plan(raw, task_id)
def _parse_plan(raw:str, task_id:str)->dict[str,Any]:
    text=raw.strip()
    if text.startswith('```') or text.endswith('```'):
        raise PatchPlanError('Model response must be strict JSON, not markdown fences')
    try: data=json.loads(text)
    except json.JSONDecodeError as exc: raise PatchPlanError(f'Model response is not valid JSON: {exc}\nRaw (first 400 chars): {raw[:400]}') from exc
    if isinstance(data,dict) and ('operations' in data or 'taskId' in data):
        raise PatchPlanError('Planner must emit AnchorPatch/v1 directly, not taskId/operations')
    try: return validate_patch(data)
    except PatchSchemaError as exc: raise PatchPlanError(f"Invalid AnchorPatch/v1 plan for {task_id!r}: {exc}") from exc
def _load_context(affected_paths:list[str], repo_root:Path)->dict[str,str]:
    real_root=repo_root.resolve(); out={}
    for rel in affected_paths:
        try:
            resolved=(real_root/rel).resolve(); resolved.relative_to(real_root)
        except ValueError as exc:
            logger.warning('patch_planner: rejecting path outside repo: %r', rel)
            raise PathTraversalError(f'context path {rel!r} resolves outside repo root') from exc
        if not resolved.is_file(): logger.warning('patch_planner: file not found: %r', rel); continue
        if resolved.stat().st_size>_MAX_FILE_BYTES: logger.warning('patch_planner: skipping oversized file: %r', rel); continue
        out[rel]=resolved.read_text(encoding='utf-8', errors='replace')
    return out
def _build_prompt(issue:dict[str,Any], context_files:dict[str,str], pipeline_id:str)->str:
    return json.dumps({'pipeline':pipeline_id,'task':issue,'contextFiles':context_files,'outputContract':{'patchProtocol':'AnchorPatch/v1','changes':[]}}, indent=2, ensure_ascii=False)
