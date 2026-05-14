"""Tests for repo_mgmt.issue_normaliser."""

from __future__ import annotations


from repo_mgmt.issue_normaliser import normalise


class TestNormalise:
    def test_code_fix_finding_classified_correctly(
        self, settings, sample_audit
    ) -> None:
        issues = normalise(sample_audit, "on-brand", "2026-05-05", settings)
        assert len(issues) == 1
        issue = issues[0]
        assert issue["classification"] == "code_fix"
        assert issue["status"] == "pending"
        assert issue["pipeline"] == "on-brand"

    def test_task_id_format(self, settings, sample_audit) -> None:
        issues = normalise(sample_audit, "on-brand", "2026-05-05", settings)
        assert issues[0]["taskId"] == "rms-on-brand-2026-05-05-001"

    def test_empty_audit_returns_empty_list(self, settings) -> None:
        issues = normalise({}, "mobile-ux", "2026-05-05", settings)
        assert issues == []

    def test_mobile_ux_reports_protected_paths_as_skipped(self, settings) -> None:
        audit = {
            "findings": [
                {
                    "title": "Layout issue on blog post",
                    "description": "Font size too small on mobile",
                    "severity": "medium",
                    "confidence": 0.8,
                    "fixClass": "css_fix",
                    "affectedPaths": ["blog/posts/my-post.html"],
                    "evidence": [],
                    "requiredOutcome": "Increase font size",
                    "sourceAudit": "mobile-ux",
                }
            ]
        }
        issues = normalise(audit, "mobile-ux", "2026-05-05", settings)
        assert len(issues) == 1
        assert issues[0]["classification"] == "skipped"
        assert issues[0]["status"] == "skipped_not_actionable"
        assert "protected" in issues[0]["skipReason"]

    def test_mobile_ux_allows_non_protected_paths(self, settings) -> None:
        audit = {
            "findings": [
                {
                    "title": "Viewport meta missing",
                    "description": "No viewport meta tag on contact page",
                    "severity": "high",
                    "confidence": 0.95,
                    "fixClass": "meta_fix",
                    "affectedPaths": ["contact.html"],
                    "evidence": ["No <meta name='viewport'>"],
                    "requiredOutcome": "Add viewport meta tag",
                    "sourceAudit": "mobile-ux",
                }
            ]
        }
        issues = normalise(audit, "mobile-ux", "2026-05-05", settings)
        assert issues[0]["classification"] == "code_fix"

    def test_unknown_fix_class_goes_to_manual_review(self, settings) -> None:
        audit = {
            "findings": [
                {
                    "title": "Something weird",
                    "description": "Some unknown issue type",
                    "severity": "low",
                    "confidence": 0.5,
                    "fixClass": "magic_unicorn_fix",
                    "affectedPaths": ["index.html"],
                    "evidence": [],
                    "requiredOutcome": "Fix it somehow",
                    "sourceAudit": "on-brand",
                }
            ]
        }
        issues = normalise(audit, "on-brand", "2026-05-05", settings)
        assert issues[0]["classification"] == "manual_review"

    def test_future_guidance_fix_class(self, settings) -> None:
        audit = {
            "findings": [
                {
                    "title": "Consider adding FAQ schema",
                    "description": "FAQ schema could improve visibility",
                    "severity": "low",
                    "confidence": 0.6,
                    "fixClass": "future_guidance",
                    "affectedPaths": [],
                    "evidence": [],
                    "requiredOutcome": "Add FAQ schema in future sprint",
                    "sourceAudit": "seo-aeo-geo",
                }
            ]
        }
        issues = normalise(audit, "seo-aeo-geo", "2026-05-05", settings)
        assert issues[0]["classification"] == "future_guidance"

    def test_on_brand_editorial_blog_finding_is_future_guidance(self, settings) -> None:
        audit = {
            "findings": [
                {
                    "title": "Blog post tone improvement",
                    "description": "Rewrite the intro to be more punchy and compelling",
                    "severity": "low",
                    "confidence": 0.7,
                    "fixClass": "html_fix",
                    "affectedPaths": ["blog/posts/example.html"],
                    "evidence": [],
                    "requiredOutcome": "Improve tone and wording",
                    "sourceAudit": "on-brand",
                }
            ]
        }
        issues = normalise(audit, "on-brand", "2026-05-05", settings)
        assert issues[0]["classification"] == "future_guidance"

    def test_multiple_findings_get_sequential_task_ids(self, settings) -> None:
        audit = {
            "findings": [
                {
                    "title": f"Issue {i}",
                    "description": "Missing canonical tag",
                    "severity": "medium",
                    "confidence": 0.8,
                    "fixClass": "html_fix",
                    "affectedPaths": [f"page{i}.html"],
                    "evidence": [],
                    "requiredOutcome": "Add canonical",
                    "sourceAudit": "on-brand",
                }
                for i in range(3)
            ]
        }
        issues = normalise(audit, "on-brand", "2026-05-05", settings)
        task_ids = [i["taskId"] for i in issues]
        assert task_ids == [
            "rms-on-brand-2026-05-05-001",
            "rms-on-brand-2026-05-05-002",
            "rms-on-brand-2026-05-05-003",
        ]
