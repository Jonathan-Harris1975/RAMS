from repo_mgmt.lane1_skills import build_lane1_skills_baseline


def test_lane1_skills_baseline_uses_central_hive_pool():
    baseline = build_lane1_skills_baseline(pipeline_id="seo-aeo-geo")
    assert baseline["lane"] == "Lane 1 - Autonomous"
    assert baseline["pipeline"] == "seo-aeo-geo"
    assert baseline["mode"] == "central-r2-read-only"
    assert baseline["repoSideSetup"] is False
    assert baseline["externalInstallRequired"] is False
    assert baseline["localAgentsFolderRequired"] is False
    assert baseline["manifestControlled"] is True
    assert baseline["centralSkillPool"]["manifest"]["objectKey"] == "manifests/rams-skills-manifest.json"
    assert baseline["centralSkillPool"]["governance"]["localSkillInstallRequired"] is False
