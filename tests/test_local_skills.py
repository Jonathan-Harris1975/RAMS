from __future__ import annotations

from pathlib import Path

from repo_mgmt.local_skills import (
    REPO_ROOT,
    load_local_skills,
    local_skill_reference,
    rams_local_skills_contract,
)


def test_rams_local_skill_contract_is_self_contained() -> None:
    contract = rams_local_skills_contract(pipeline_id="mobile-ux")

    assert contract["mode"] == "repository-local-native"
    assert contract["cataloguePath"] == "config/skills"
    assert contract["accessMode"] == "local-read-only"
    assert contract["skillCount"] == 4
    assert contract["sharedBucketRequired"] is False
    assert contract["externalNetworkRequired"] is False
    assert contract["runtimeInstallRequired"] is False
    assert contract["governance"]["externalSkillBundlesAllowed"] is False


def test_local_skill_ids_and_paths_are_valid() -> None:
    records = load_local_skills()

    assert [record["id"] for record in records] == [
        "RAMS-sk001",
        "RAMS-sk002",
        "RAMS-sk003",
        "RAMS-sk004",
    ]
    for record in records:
        assert record["origin"]["external_skill_content_copied"] is False
        assert record["source_uri"].startswith("repo://config/skills/RAMS-sk")
        for implementation in record["implementation_paths"]:
            assert (REPO_ROOT / Path(implementation)).is_file()


def test_local_skill_reference_rejects_unknown_ids() -> None:
    assert local_skill_reference("RAMS-sk001").startswith("repo://config/skills/")

    try:
        local_skill_reference("S159")
    except KeyError as exc:
        assert "Unknown RAMS local skill" in str(exc)
    else:
        raise AssertionError("unknown shared-pool identifier was accepted")
