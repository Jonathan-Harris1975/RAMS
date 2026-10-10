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
