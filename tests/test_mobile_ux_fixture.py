import json
from pathlib import Path
from repo_mgmt.issue_normaliser import normalise
def test_sample_mobile_ux_audit_fixture_exists(): assert Path('tests/fixtures/sample_mobile_ux_audit.json').is_file()
def test_sample_mobile_ux_audit_is_consumed_by_normaliser(settings):
    issues=normalise(json.loads(Path('tests/fixtures/sample_mobile_ux_audit.json').read_text()),'mobile-ux','2026-05-05',settings); assert len(issues)==1 and issues[0]['classification']=='code_fix'
