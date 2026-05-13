import json
from pathlib import Path
from repo_mgmt.report_publisher import CommitInfo, RunReport, ValidationSummary, publish
def _report(dry_run=True):
    return RunReport(runId='run-1',pipeline='on-brand',targetRepo='/tmp/repo',branch='rms-qa/on-brand-run-1',dryRun=dry_run,summary={'snapshotsRead':1,'tasksGenerated':1,'codeFixesAttempted':1,'committed':1,'validationFailed':0,'futureGuidance':0,'manualReview':0},tasks=[{'taskId':'t1','status':'committed'}],validation=ValidationSummary(commands=['python -m pytest'],passed=True,output_tail='ok'),commits=[CommitInfo(sha='abc123',message='msg',files=['index.html'])])
def test_dry_run_writes_local_json(tmp_path,monkeypatch,settings,mock_r2):
    monkeypatch.chdir(tmp_path); dest=publish(_report(True),settings,mock_r2); data=json.loads(Path(dest).read_text()); assert set(data)=={'runId','pipeline','targetRepo','branch','dryRun','summary','tasks','validation','commits'}; assert data['validation']['outputTail']=='ok'
def test_live_writes_report_and_latest(settings,mock_r2):
    dest=publish(_report(False),settings,mock_r2); keys=[c.kwargs['key'] for c in mock_r2.put_object.call_args_list]; assert dest.endswith('/report.json'); assert f'{settings.rms_report_prefix}/on-brand/run-1/report.json' in keys; assert f'{settings.rms_report_prefix}/on-brand/latest.json' in keys

def test_report_error_is_serialised(tmp_path, monkeypatch, settings, mock_r2):
    monkeypatch.chdir(tmp_path)
    report = _report(True)
    report.error = 'boom'
    dest = publish(report, settings, mock_r2)
    data = json.loads(Path(dest).read_text())
    assert data['error'] == 'boom'
