import asyncio
from unittest.mock import MagicMock, patch
from repo_mgmt import update_executor


def test_update_executor_happy_path_in_dry_run(
    settings, mock_router, sample_audit, tmp_repo
):
    issue = {
        **sample_audit["findings"][0],
        "taskId": "t1",
        "classification": "code_fix",
        "status": "pending",
    }
    result = asyncio.run(
        update_executor.run_task(
            issue, tmp_repo, "on-brand", settings, mock_router, None, True
        )
    )
    assert result["status"] == "planned"
    assert result["patch"]["patchProtocol"] == "AnchorPatch/v1"


def test_update_executor_validation_failure_reverts(
    settings, mock_router, sample_audit, tmp_repo
):
    issue = {
        **sample_audit["findings"][0],
        "taskId": "t1",
        "classification": "code_fix",
        "status": "pending",
    }
    gm = MagicMock()
    gm.assert_write_allowed = MagicMock()
    failed = MagicMock(
        passed=False, commands=["false"], output_tail="bad", failed_command="false"
    )
    with patch("repo_mgmt.update_executor.validation_runner.run", return_value=failed):
        result = asyncio.run(
            update_executor.run_task(
                issue, tmp_repo, "on-brand", settings, mock_router, gm, False
            )
        )
    gm.revert.assert_called_once()
    assert result["status"] == "reverted" and "validation failed" in result["error"]
