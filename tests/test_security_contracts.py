from __future__ import annotations

from pathlib import Path

from scripts.secret_scan import scan_file


REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_docker_smoke_uses_ephemeral_api_key() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "ci_api_key=\"$(python -c 'import secrets; print(secrets.token_urlsafe(32))')\"" in workflow
    assert '-e RMS_API_KEY="$ci_api_key"' in workflow
    assert 'Authorization: Bearer $ci_api_key' in workflow
    legacy_static_api_key = "RMS_API_" + "KEY=" + "'ci-local-" + "rams-key'"
    assert legacy_static_api_key not in workflow


def test_secret_scan_rejects_provider_token_without_fixture_escape(tmp_path: Path) -> None:
    candidate = tmp_path / "production.env"
    # Build the fake provider token in pieces so this regression test does not itself
    # become a secret-pattern finding in repository-level scanners.
    candidate.write_text("AWS_ACCESS_KEY_ID=" + "AKIA" + "ABCDEFGHIJKLMNOP" + "\n", encoding="utf-8")

    findings = scan_file(candidate, tmp_path)

    assert findings == ["production.env:1: AWS access key", "production.env:1: literal value assigned to AWS_ACCESS_KEY_ID"]



def test_duplicate_dead_code_copies_are_not_reintroduced() -> None:
    assert not (REPO_ROOT / "github_pr.py").exists()
    assert (REPO_ROOT / "repo_mgmt" / "github_pr.py").is_file()
    assert not (REPO_ROOT / "repo_mgmt" / "sample_mobile_ux_audit.json").exists()
    assert (REPO_ROOT / "tests" / "fixtures" / "sample_mobile_ux_audit.json").is_file()
