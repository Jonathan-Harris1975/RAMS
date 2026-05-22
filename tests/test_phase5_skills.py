from __future__ import annotations

from repo_mgmt.phase5_skills import phase5_skills_summary
from repo_mgmt.report_publisher import CommitInfo, RunReport, ValidationSummary, _report_quality
from repo_mgmt.issue_normaliser import normalise


def test_phase5_mobile_ux_summary_includes_accessibility_skill() -> None:
    summary = phase5_skills_summary("mobile-ux")

    assert summary["phase"] == "5A/5B/5C"
    assert summary["activeSkills"] == {"accessibilityMobileUx": ["accessibility-audit"]}
    assert "paid-ads" in summary["parkedSkills"]


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
    assert quality["phase5Skills"]["activeSkills"] == {"accessibilityMobileUx": ["accessibility-audit"]}


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
