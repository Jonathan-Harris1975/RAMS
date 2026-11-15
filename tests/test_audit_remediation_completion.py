from __future__ import annotations

from types import SimpleNamespace

from repo_mgmt.audit_remediation import AuditRemediation, digest


class Storage:
    def __init__(self):
        self.objects = {}

    def put_object_if_absent(self, bucket, key, body, content_type='application/json'):
        if (bucket, key) in self.objects:
            return False
        self.objects[bucket, key] = body
        return True

    def put_object(self, bucket, key, body, content_type='application/json'):
        self.objects[bucket, key] = body

    def get_object_limited(self, bucket, key, limit):
        return self.objects[bucket, key]

    def list_objects(self, bucket, prefix, max_keys=100):
        return sorted(k for b, k in self.objects if b == bucket and k.startswith(prefix))[:max_keys]

    def object_exists(self, bucket, key):
        return (bucket, key) in self.objects

    def delete_object(self, bucket, key):
        self.objects.pop((bucket, key), None)


class Github:
    def __init__(self):
        self.events = []
        self.pr = None
        self.files = []
        self.production = False
        self.head_checks = True

    def __call__(self, method, path, body=None):
        if method == 'POST':
            self.events.append((path, body))
            return None
        if '/commits/' in path:
            return {'sha': 'a' * 40}
        if '/files?' in path:
            return self.files
        if '/pulls?' in path:
            return [] if self.pr is None else [{'number': 7}]
        if '/pulls/7' in path:
            return self.pr
        if '/actions/runs?' in path:
            sha = path.split('head_sha=')[1].split('&')[0]
            names = ['AIMS CI', 'CodeQL', 'Security and repository quality', 'Item 6 hardening']
            if sha == 'c' * 40:
                names = ['Koyeb production deployment watch'] if self.production else []
            return {'workflow_runs': [{'id': i + 1, 'name': name, 'head_sha': sha,
                'event': 'pull_request' if sha == 'b' * 40 else 'workflow_run',
                'status': 'completed', 'conclusion': 'success' if self.head_checks else 'failure'} for i, name in enumerate(names)]}
        raise AssertionError(path)


def setup():
    cfg = SimpleNamespace(rms_report_prefix='qa-suite/reports', r2_bucket_audits='audits',
        rms_report_max_bytes=4194304, repo_url_for=lambda _: 'https://github.com/Jonathan-Harris1975/AIMS',
        repo_branch_for=lambda _: 'main')
    storage, github = Storage(), Github()
    return AuditRemediation(cfg, storage, github, lambda *args: github.production), storage, github


def task(index=1):
    return {'taskId': f'c-{index}', 'classification': 'manual_review', 'status': 'routing_required',
        'proposedClassification': 'code_fix', 'affectedPaths': [f'services/{index}.js'],
        'sourceFindingIds': [f'S-{index}'], 'requiredOutcome': 'Correct observed defect',
        'acceptanceCriterion': 'Original defect does not recur', 'verificationMethod': 'Regression test and required CI'}


def audit():
    return {'sourceAuditKey': 'audits/content-master/2026-11/session/content-audit.json', 'schemaVersion': 'content-audit-report/v2'}


def test_dispatch_is_durable_and_duplicate_registration_does_not_redispatch():
    service, storage, github = setup()
    state = service.register('content', 'run-1', audit(), [task()])
    assert state['status'] == 'pending'
    assert len(github.events) == 1
    plan = service.read(state['batches'][0]['planKey'])
    assert plan['auditDigest'] == digest({'schemaVersion': 'content-audit-report/v2'})
    service.register('content', 'run-1', audit(), [task()])
    assert len(github.events) == 1
    assert any('/active/' in key for _, key in storage.objects)


def test_report_acceptance_or_a_merged_pr_never_counts_as_completed():
    service, _, github = setup()
    state = service.register('content', 'run-1', audit(), [task()])
    batch = state['batches'][0]
    github.pr = {'number': 7, 'head': {'ref': batch['branch'], 'sha': 'b' * 40,
        'repo': {'full_name': state['repository']}}, 'base': {'ref': 'main'},
        'merged': True, 'merge_commit_sha': 'c' * 40, 'html_url': 'https://github.com/Jonathan-Harris1975/AIMS/pull/7'}
    github.files = [{'filename': 'services/1.js'}, {'filename': batch['regressionTestPath']}]
    pending = service.reconcile('content', 'run-1')
    assert pending['status'] != 'completed'
    assert pending['batches'][0]['status'] == 'awaiting_production_verification'
    github.production = True
    completed = service.reconcile('content', 'run-1')
    assert completed['status'] == 'completed'
    assert completed['batches'][0]['mergeSha'] == 'c' * 40
    receipt = service.read('qa-suite/reports/remediation/content/run-1/completion.json')
    assert receipt['batches'][0]['verification']['regressionTestPath'] == batch['regressionTestPath']


def test_missing_regression_test_cannot_complete():
    service, _, github = setup()
    state = service.register('content', 'run-1', audit(), [task()])
    github.pr = {'number':7,'head':{'ref':state['batches'][0]['branch'],'sha':'b'*40,
        'repo':{'full_name':state['repository']}},'base':{'ref':'main'},'merged':True,'merge_commit_sha':'c'*40}
    github.files = [{'filename':'services/1.js'}]
    github.production = True
    assert service.reconcile('content','run-1')['status'] == 'attention_required'


def test_manual_findings_remain_unresolved_and_protected_paths_are_not_dispatched():
    service, _, github = setup()
    unsafe = {**task(), 'affectedPaths':['.github/workflows/ci.yml']}
    result = service.register('content','run-1',audit(),[unsafe])
    assert result['status'] == 'attention_required'
    assert len(result['manualReview']) == 1
    assert not github.events


def test_every_eligible_finding_is_retained_in_sequential_batches():
    service, _, github = setup()
    result = service.register('content','run-1',audit(),[task(i) for i in range(12)])
    assert [len(b['tasks']) for b in result['batches']] == [5,5,2]
    assert len(github.events) == 1
    assert sum(len(b['tasks']) for b in result['batches']) == 12


def test_latest_current_production_revision_must_match_completed_repair():
    service, _, _ = setup()
    service.github=lambda method,path,body=None: ([
        {'id':2,'sha':'d'*40,'environment':'production-zeroth-kara/aims','creator':{'login':'koyeb[bot]'}},
        {'id':1,'sha':'c'*40,'environment':'production-zeroth-kara/aims','creator':{'login':'koyeb[bot]'}}
    ] if '/deployments?' in path else [{'state':'success'}])
    assert not service._live_revision('Jonathan-Harris1975/AIMS','content','c'*40)
    assert service._live_revision('Jonathan-Harris1975/AIMS','content','d'*40)


def test_missing_acceptance_is_reported_without_external_dispatch():
    service, _, github=setup()
    incomplete=task()
    incomplete.pop('acceptanceCriterion')
    state=service.register('content','missing-acceptance',audit(),[incomplete])
    assert state['status']=='attention_required'
    assert not github.events


def test_stale_before_handoff_replans_without_replaying_uncertain_provider():
    service, storage, github=setup()
    state=service.register('content','stale-run',audit(),[task()])
    old=state['batches'][0]['planKey']
    service.write(old.removesuffix('.json')+'/dispatch.json',{'status':'stale'})
    state=service.reconcile('content','stale-run')
    assert state['batches'][0]['status']=='pending'
    assert state['batches'][0]['previousPlans'][0]['planKey']==old
    state=service.reconcile('content','stale-run')
    assert len(github.events)==2
    new=state['batches'][0]['planKey']
    service.write(new.removesuffix('.json')+'/dispatch.json',{'status':'stale'})
    service.write(new.removesuffix('.json')+'/provider-reservation.json',{'outcome':'unknown'})
    assert service.reconcile('content','stale-run')['status']=='attention_required'
    assert len(github.events)==2


def test_repeated_registration_recovers_missing_active_index():
    service, storage, github=setup()
    service.register('content','orphaned-run',audit(),[task()])
    index='qa-suite/reports/remediation/active/content-orphaned-run.json'
    storage.delete_object('audits',index)
    service.register('content','orphaned-run',audit(),[task()])
    assert storage.object_exists('audits',index)
    assert len(github.events)==1
    assert service.sweep()==1


def test_index_recovers_a_crash_before_registration_state_write():
    import pytest
    service, storage, github=setup()
    original=storage.put_object_if_absent
    def crash(bucket,key,body,content_type='application/json'):
        if key.endswith('/state.json'):
            raise RuntimeError('simulated interruption')
        return original(bucket,key,body,content_type)
    storage.put_object_if_absent=crash
    with pytest.raises(RuntimeError):
        service.register('content','crash-run',audit(),[task()])
    storage.put_object_if_absent=original
    assert service.sweep()==1
    assert len(github.events)==1


def test_oversized_registration_never_creates_an_active_index():
    import pytest
    service, storage, _=setup()
    service.cfg.rms_report_max_bytes=64
    with pytest.raises(ValueError):
        service.register('content','oversized-run',audit(),[task()])
    assert not storage.objects


def test_infrastructure_and_dependency_paths_cannot_be_audit_scope():
    from repo_mgmt.audit_remediation import protected
    for path in ['Dockerfile','deploy/config.yml','requirements.txt','k8s/deploy.yaml','main.tf','setup.py','Pipfile','helm/chart.yaml']:
        assert protected(path)
    assert not protected('services/newsletter/prompt.js')


def legacy_deferred_state(service):
    finding = {'findingId': 'C-001', 'title': 'Test next month packaging', 'classification': 'future_guidance',
               'sourceFindingIds': ['S-1'], 'exactRemediation': 'Compare monthly evidence', 'affectedPaths': []}
    source = {'schemaVersion': 'content-audit-report/v2', 'sourceRefresh': {'freshnessLocked': True},
              'council': {'synthesisState': 'Complete', 'masterIssueLedger': [finding]}}
    source_key = audit()['sourceAuditKey']
    service.write(source_key, source)
    state = service.register('content', 'legacy', {**source, 'sourceAuditKey': source_key}, [{
        'title': finding['title'], 'classification': 'manual_review', 'sourceFindingIds': ['S-1'],
        'requiredOutcome': finding['exactRemediation'], 'affectedPaths': []}])
    return state, source


def test_saved_deferred_guidance_is_recovered_from_exact_original_evidence_without_dispatch():
    service, _, github = setup()
    legacy_deferred_state(service)
    state = service.status('content', 'legacy')
    assert state['status'] == 'completed'
    assert len(state['futureGuidance']) == 1
    assert not state['manualReview']
    assert state['classificationCorrection']['count'] == 1
    assert not github.events
    receipt = service.read('qa-suite/reports/remediation/content/legacy/completion.json')
    assert receipt['futureGuidance'] == state['futureGuidance']
    assert service.status('content', 'legacy') == state


def test_modified_source_cannot_clear_existing_manual_blocker():
    service, _, _ = setup()
    _, source = legacy_deferred_state(service)
    service.write(audit()['sourceAuditKey'], {**source, 'changed': True})
    assert service.status('content', 'legacy')['status'] == 'attention_required'


def test_unmatched_manual_blockers_survive_deferred_guidance_recovery():
    service, _, _ = setup()
    state, _ = legacy_deferred_state(service)
    state['manualReview'].append({'title': 'Unknown defect', 'classification': 'manual_review'})
    service.write('qa-suite/reports/remediation/content/legacy/state.json', state)
    result = service.status('content', 'legacy')
    assert result['status'] == 'attention_required'
    assert result['manualReview'][0]['title'] == 'Unknown defect'
    assert len(result['futureGuidance']) == 1


def test_legacy_correction_publication_recovers_after_interrupted_completion_write():
    service, storage, github = setup()
    legacy_deferred_state(service)
    write = storage.put_object_if_absent
    fail_once = True

    def interrupted(bucket, key, body, content_type='application/json'):
        nonlocal fail_once
        if key.endswith('/completion.json') and fail_once:
            fail_once = False
            raise RuntimeError('transient completion publication failure')
        return write(bucket, key, body, content_type)

    storage.put_object_if_absent = interrupted
    import pytest
    with pytest.raises(RuntimeError, match='transient completion'):
        service.status('content', 'legacy')
    result = service.status('content', 'legacy')
    assert result['status'] == 'completed'
    assert service.read('qa-suite/reports/remediation/content/legacy/completion.json')['futureGuidance']
    assert not github.events
