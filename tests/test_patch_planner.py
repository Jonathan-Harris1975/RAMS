from __future__ import annotations
import json, pytest
from repo_mgmt.patch_planner import SYSTEM_PROMPT, PatchPlanError, _parse_plan, plan
from repo_mgmt.patch_protocol import PathTraversalError
def _patch(changes=None): return {'patchProtocol':'AnchorPatch/v1','changes':changes if changes is not None else []}
class TestParsePlan:
    def test_valid_anchor_patch_parses_directly(self):
        doc=_patch([{'file':'index.html','operation':'replace','anchorBefore':'<title>Old</title>','find':'Old','replace':'New'}]); assert _parse_plan(json.dumps(doc),'t')==doc
    def test_empty_changes_are_safe_noop(self): assert _parse_plan(json.dumps(_patch([])),'t')['changes']==[]
    def test_rejects_markdown_fences_as_not_strict_json(self):
        with pytest.raises(PatchPlanError): _parse_plan('```json\n{}\n```','t')
    def test_raises_on_invalid_json(self):
        with pytest.raises(PatchPlanError): _parse_plan('not json','t')
    def test_raises_on_custom_operations_contract(self):
        with pytest.raises(PatchPlanError): _parse_plan(json.dumps({'taskId':'x','operations':[]}),'t')
    def test_raises_on_unknown_operation(self):
        with pytest.raises(PatchPlanError): _parse_plan(json.dumps(_patch([{'file':'x','operation':'create'}])),'t')
    def test_raises_on_replace_missing_find(self):
        with pytest.raises(PatchPlanError): _parse_plan(json.dumps(_patch([{'file':'x','operation':'replace','replace':'x'}])),'t')
class TestPlan:
    def test_prompt_requires_direct_anchor_patch_contract(self): assert 'AnchorPatch/v1' in SYSTEM_PROMPT and 'Do not return taskId or operations' in SYSTEM_PROMPT and 'markdown fences' in SYSTEM_PROMPT
    def test_raises_if_classification_not_code_fix(self,settings,mock_router,tmp_repo):
        with pytest.raises(PatchPlanError): plan({'taskId':'x','classification':'future_guidance','affectedPaths':[]},tmp_repo,'on-brand',settings,mock_router)
    def test_returns_anchor_patch_on_success(self,settings,mock_router,tmp_repo):
        result=plan({'taskId':'x','classification':'code_fix','affectedPaths':['index.html']},tmp_repo,'on-brand',settings,mock_router); assert result['patchProtocol']=='AnchorPatch/v1'; assert 'operations' not in result
    def test_raises_on_model_failure(self,settings,mock_router,tmp_repo):
        mock_router.complete.side_effect=RuntimeError('model down')
        with pytest.raises(PatchPlanError): plan({'taskId':'x','classification':'code_fix','affectedPaths':['index.html']},tmp_repo,'on-brand',settings,mock_router)
    def test_read_side_sibling_prefix_traversal_blocked(self,settings,mock_router,tmp_path):
        repo=tmp_path/'repo'; repo.mkdir(); sib=tmp_path/'repo-secret'; sib.mkdir(); (sib/'secret.txt').write_text('secret')
        with pytest.raises(PathTraversalError): plan({'taskId':'x','classification':'code_fix','affectedPaths':['../repo-secret/secret.txt']},repo,'on-brand',settings,mock_router)
