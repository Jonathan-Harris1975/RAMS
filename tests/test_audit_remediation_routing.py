from repo_mgmt.pipeline import _apply_audit_remediation_routing


def _issue():
    return {
        "taskId": "rms-website-2026-10-06-001",
        "pipeline": "website",
        "sourceAudit": "website:final-report",
        "classification": "code_fix",
        "severity": "medium",
        "confidence": 1.0,
        "affectedPaths": ["assets/css/site.css"],
        "evidence": ["deterministic rendered defect"],
        "requiredOutcome": "Correct the verified CSS defect",
        "allowedFixClass": "css_fix",
        "validationCommands": ["python -m pytest"],
        "status": "pending",
        "sourceFindingIds": ["MUX-001"],
    }


def _audit():
    return {
        "operational": {
            "remediationRouting": {
                "schemaVersion": "audit-remediation-routing/v1",
                "repairExecutionPolicy": "external-agent-only",
                "singleWriterRequired": True,
                "auditOwnsRepair": False,
                "primaryRepairAgent": "kilo",
                "plannedEngineeringAgent": "cto.new",
                "ownershipTransferRequired": True,
                "dependencyVersionOwner": "renovate",
                "mergeAuthority": "mergify",
                "humanDecisionSurface": "hive-ui",
            }
        }
    }


def test_code_fix_becomes_external_agent_routing_task():
    routed = _apply_audit_remediation_routing([_issue()], _audit(), "website")
    assert len(routed) == 1
    item = routed[0]
    assert item["classification"] == "manual_review"
    assert item["proposedClassification"] == "code_fix"
    assert item["status"] == "routing_required"
    assert item["recommendedAgent"] == "kilo"
    assert item["fallbackAgent"] == "cto.new"
    assert item["fallbackEligible"] is True
    assert item["singleWriterRequired"] is True
    assert item["auditOwnsRepair"] is False
    assert item["ownershipFingerprint"].startswith("audit:website:")


def test_missing_contract_refuses_direct_rams_code_write():
    routed = _apply_audit_remediation_routing([_issue()], {}, "website")
    item = routed[0]
    assert item["classification"] == "code_fix"
    assert item["status"] == "manual_review"
    assert item["patchAttempted"] is False
    assert any("routing contract missing/invalid" in e for e in item["evidence"])
