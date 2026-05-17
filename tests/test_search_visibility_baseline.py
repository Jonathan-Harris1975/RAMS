import json
from pathlib import Path
from unittest.mock import patch

from repo_mgmt import pipeline as pipeline_mod
from repo_mgmt.report_publisher import publish
from repo_mgmt.search_visibility_baseline import search_visibility_baseline_for


def test_search_visibility_baseline_is_seo_pipeline_only():
    baseline = search_visibility_baseline_for("seo-aeo-geo")
    assert baseline is not None
    assert baseline["batch"] == "Batch 1 - Search visibility baseline"
    assert baseline["mode"] == "reports-only"
    assert [skill["name"] for skill in baseline["skills"]] == ["seo-audit", "ai-seo"]
    assert search_visibility_baseline_for("mobile-ux") is None
    assert search_visibility_baseline_for("on-brand") is None


def test_seo_pipeline_report_serialises_batch_1_metadata(settings, mock_r2, mock_router, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings.rms_report_dir = str(tmp_path)
    mock_r2.get_object.return_value = b"{}"
    fixed = "2026-05-17T12-00-00Z"
    with patch("repo_mgmt.pipeline.ModelRouter", return_value=mock_router):
        report = pipeline_mod.run("seo-aeo-geo", settings, mock_r2, dry_run=True, run_id=fixed)

    assert report.skills_baseline is not None
    dest = publish(report, settings, mock_r2)
    data = json.loads(Path(dest).read_text())
    assert data["skillsBaseline"]["batch"] == "Batch 1 - Search visibility baseline"
    assert data["skillsBaseline"]["mode"] == "reports-only"
