"""Legacy compatibility module boundaries."""

from __future__ import annotations

import inspect

from repo_mgmt import pipeline


def test_live_pipeline_path_does_not_use_legacy_git_ops_validator_or_writer() -> None:
    """Production pipeline imports canonical modules, not legacy shims."""
    source = inspect.getsource(pipeline)
    assert "git_ops" not in source
    assert "report_writer" not in source
    assert "validator" not in source
