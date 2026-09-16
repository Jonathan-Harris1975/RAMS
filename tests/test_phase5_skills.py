from __future__ import annotations

from repo_mgmt.issue_normaliser import normalise
from repo_mgmt.phase5_skills import phase5_skills_summary
from repo_mgmt.report_publisher import CommitInfo, RunReport, ValidationSummary, _report_quality


def test_phase5_mobile_ux_summary_uses_local_accessibility_capabilities() -> None:
    summary = phase5_skills_summary("mobile-ux")

    assert summary["phase"] == "5A/5B/5C"
    assert summary["activeCapabilities"] == {
        "accessibilityMobileUx": ["RAMS-sk001", "RAMS-sk002", "RAMS-sk003"]
    }
    assert summary["activeSkills"] == summary["activeCapabilities"]
    assert summary["localAgentsFolderRequired"] is False
    assert summary["manifestControlled"] is True
    assert summary["sharedBucketRequired"] is False
    assert summary["localSkillCatalogue"]["mode"] == "repository-local-native"
    assert summary["skillSource"] == summary["localSkillCatalogue"]
    assert "paid-ads" in summary["parkedCapabilities"]


def test_report_quality_adds_accessibility_evidence_to_mobile_ux() -> None:
    report = RunReport(
        runId="run-one",
        pipeline="mobile-ux",
        targetRepo="website",
        branch="rms-qa/mobile-ux/run-one",
        dryRun=False,
        validation=ValidationSummary(commands=["pytest"], passed=True),
        commits=[CommitInfo(sha="abc123", message="test")],
    )

    quality = _report_quality(report)

    assert "accessibility-appendix.json" in quality["requiredEvidence"]
    assert quality["phase5Skills"]["activeCapabilities"] == {
        "accessibilityMobileUx": ["RAMS-sk001", "RAMS-sk002", "RAMS-sk003"]
    }
    assert quality["phase5Skills"]["manifestControlled"] is True
    assert quality["phase5Skills"]["sharedBucketRequired"] is False


def test_mobile_ux_accessibility_finding_maps_to_governed_source(settings) -> None:
    audit = {
        "artefacts": {
            "accessibility-appendix.json": {
                "routeViewportRows": [
                    {
                        "route": "/contact",
                        "url": "https://example.test/contact/",
                        "viewport": 390,
                        "status": "FAIL",
                        "issueCount": 1,
                        "issues": [
                            {
                                "type": "form-label",
                                "wcag": "3.3.2",
                                "selector": "input.email",
                                "message": "Visible form field has no associated label.",
                            }
                        ],
                    }
                ]
            }
        }
    }

    issues = normalise(audit, "mobile-ux", "2026-05-22", settings)

    assert issues
    assert any("phase5Accessibility" in " ".join(issue.get("evidence", [])) for issue in issues)
