"""Keep scheduled CI and the local release gate consistent with the runtime."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_weekend_ci_target_accepts_dispatch():
    import yaml

    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    triggers = workflow.get('on', workflow.get(True))
    assert 'workflow_dispatch' in triggers
    launcher = (ROOT / '.github/workflows/weekend-phase-launcher.yml').read_text()
    assert 'workflow="ci.yml"' in launcher


def test_release_runtime_does_not_require_removed_pip():
    dockerfile = (ROOT / 'Dockerfile').read_text()
    gate = (ROOT / 'scripts/release_gate.sh').read_text()
    assert '/usr/local/lib/python3.14/site-packages/pip' in dockerfile
    assert '! python -m pip --version >/dev/null 2>&1' in gate
    assert 'test "$api_started" = "true"' in gate


def test_release_attestation_fails_on_missing_or_skipped_constituent_checks():
    workflow = (ROOT / '.github/workflows/ci.yml').read_text()
    gate = workflow.split('  release_gate:', 1)[1].split('  ci_gate:', 1)[0]
    aggregate = workflow.split('  ci_gate:', 1)[1].split('  autonomous_repair_guard:', 1)[0]

    for result in ('RESULT_TEST', 'RESULT_PERFORMANCE', 'RESULT_LINT', 'RESULT_DOCKER'):
        assert f'test "${result}" = "success"' in gate
    assert 'needs: [test, performance, lint, docker]' in gate
    assert 'test "$RELEASE_GATE_RESULT" = "success"' in aggregate
    assert 'release-attestation.json' in gate
