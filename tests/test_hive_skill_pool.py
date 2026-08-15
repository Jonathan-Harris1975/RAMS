from __future__ import annotations

from repo_mgmt.hive_skill_pool import rams_skill_pool_contract, skill_descriptor_url


def test_rams_skill_pool_contract_points_to_central_manifest(monkeypatch) -> None:
    monkeypatch.setenv("R2_BUCKET_HIVE_SKILLS", "hive-skills")

    contract = rams_skill_pool_contract(pipeline_id="mobile-ux")

    assert contract["mode"] == "central-r2-read-only"
    assert contract["bucket"] == "hive-skills"
    assert contract["manifest"]["objectKey"] == "manifests/rams-skills-manifest.json"
    assert contract["publicBaseUrl"] is None
    assert contract["storageUri"] == "r2://hive-skills"
    assert contract["accessMode"] == "private-r2-read-only"
    assert contract["manifest"]["url"] == "r2://hive-skills/manifests/rams-skills-manifest.json"
    assert contract["governance"]["localAgentsFolderRequired"] is False
    assert contract["governance"]["localSkillInstallRequired"] is False
    assert contract["governance"]["allowDirectSkillExecution"] is False


def test_skill_descriptor_url_uses_private_hive_skills_reference(monkeypatch) -> None:
    monkeypatch.setenv("R2_BUCKET_HIVE_SKILLS", "hive-skills")
    assert skill_descriptor_url("skills/S159_accessibility-audit.json") == (
        "r2://hive-skills/skills/S159_accessibility-audit.json"
    )
