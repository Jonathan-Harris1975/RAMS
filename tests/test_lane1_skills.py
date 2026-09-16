from repo_mgmt.lane1_skills import build_lane1_skills_baseline


def test_lane1_skills_baseline_uses_local_native_catalogue():
    baseline = build_lane1_skills_baseline(pipeline_id="seo-aeo-geo")
    assert baseline["lane"] == "Lane 1 - Autonomous"
    assert baseline["pipeline"] == "seo-aeo-geo"
    assert baseline["mode"] == "repository-local-native"
    assert baseline["repoSideSetup"] is True
    assert baseline["externalInstallRequired"] is False
    assert baseline["localAgentsFolderRequired"] is False
    assert baseline["manifestControlled"] is True
    assert baseline["sharedBucketRequired"] is False
    assert baseline["skillCount"] == 4
    assert baseline["localSkillCatalogue"]["externalNetworkRequired"] is False
    assert all(skill["id"].startswith("RAMS-sk") for skill in baseline["skills"])
