from __future__ import annotations

from repo_mgmt.hive_skill_pool import rams_skill_pool_contract, skill_descriptor_url


BASE_URL = "https://pub-da50a6512f164566955a3076a1c795ef.r2.dev"


def test_rams_skill_pool_contract_points_to_central_manifest(monkeypatch) -> None:
    monkeypatch.setenv("R2_PUBLIC_BASE_URL_HIVE_SKILLS", BASE_URL)
    monkeypatch.setenv("R2_BUCKET_HIVE_SKILLS", "hive-skills")

    contract = rams_skill_pool_contract(pipeline_id="mobile-ux")

    assert contract["mode"] == "central-r2-read-only"
    assert contract["bucket"] == "hive-skills"
    assert contract["manifest"]["objectKey"] == "manifests/rams-skills-manifest.json"
    assert contract["manifest"]["url"] == f"{BASE_URL}/manifests/rams-skills-manifest.json"
    assert contract["governance"]["localAgentsFolderRequired"] is False
    assert contract["governance"]["localSkillInstallRequired"] is False
    assert contract["governance"]["allowDirectSkillExecution"] is False


def test_skill_descriptor_url_uses_hive_skills_base(monkeypatch) -> None:
    monkeypatch.setenv("R2_PUBLIC_BASE_URL_HIVE_SKILLS", BASE_URL + "/")
    assert skill_descriptor_url("skills/S159_accessibility-audit.json") == (
        f"{BASE_URL}/skills/S159_accessibility-audit.json"
    )
