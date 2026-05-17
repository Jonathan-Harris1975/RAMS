from repo_mgmt.lane1_skills import build_lane1_skills_baseline


def test_lane1_skills_baseline_lists_all_autonomous_skills():
    baseline = build_lane1_skills_baseline(pipeline_id="seo-aeo-geo")
    assert baseline["lane"] == "Lane 1 - Autonomous"
    assert baseline["pipeline"] == "seo-aeo-geo"
    assert baseline["repoSideSetup"] is True
    assert baseline["externalInstallRequired"] is True
    assert baseline["skillCount"] == 14
    assert any(skill["slug"] == "seo-audit" for skill in baseline["skills"])
    assert any(skill["slug"] == "browser-use" for skill in baseline["skills"])
    assert "auto-merge" in baseline["governance"]["blockedActions"]
