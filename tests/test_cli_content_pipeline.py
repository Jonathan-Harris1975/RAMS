from repo_mgmt.cli import _validate_pipeline


def test_cli_accepts_content_pipeline() -> None:
    assert _validate_pipeline("content") == "content"
