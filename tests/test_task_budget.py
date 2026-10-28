import asyncio
from types import SimpleNamespace

import pytest

from repo_mgmt.patch_planner import _complete_for_plan_async, PatchPlanError
from repo_mgmt.task_budget import task_recovery_scope, claim_recovery


@pytest.mark.asyncio
async def test_nested_recovery_budget_is_shared_and_parallel_tasks_are_isolated():
    async def task():
        with task_recovery_scope(1):
            claim_recovery()
            await asyncio.sleep(0)
            with task_recovery_scope(99):
                with pytest.raises(RuntimeError, match="budget exhausted"):
                    claim_recovery()
    await asyncio.gather(task(), task())
    with task_recovery_scope(1):
        claim_recovery()


@pytest.mark.asyncio
async def test_planner_uses_explicit_sonnet_role_and_never_hidden_sol_fallback():
    calls = []

    class Router:
        async def complete_with_model_async(self, model, *args, **kwargs):
            calls.append((model, kwargs["governance_role"]))
            raise TypeError("technical failure")

        async def complete_async(self, **kwargs):
            pytest.fail("must not fall back to primary Sol after explicit planning fails")

    cfg = SimpleNamespace(openrouter_secondary_model="anthropic/claude-sonnet-5.5")
    with pytest.raises(PatchPlanError, match="technical failure"):
        await _complete_for_plan_async(Router(), "bounded patch evidence", "system", cfg)
    assert calls == [("anthropic/claude-sonnet-5.5", "OPENROUTER_SECONDARY_MODEL")]
