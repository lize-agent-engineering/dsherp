"""What the CI workflows promise, asserted against the YAML itself.

Two promises matter here and neither is visible from a green run: that the nightly's
evaluation step cannot make a paid call, and that no timer ever starts one.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NIGHTLY = ROOT / '.github/workflows/nightly.yml'
LIVE = ROOT / '.github/workflows/evals-live.yml'


def _step(text, name_fragment):
    """The block of a workflow step whose name contains the fragment."""
    steps = re.split(r'\n      - ', text)
    found = [step for step in steps if name_fragment in step.split('\n')[0]
             or name_fragment in step]
    assert found, f'找不到含 {name_fragment!r} 的步骤'
    return found[0]


def test_nightly_runs_the_replay_evaluation_without_a_provider_key():
    text = NIGHTLY.read_text(encoding='utf-8')
    step = _step(text, 'evals/run.py')
    assert '--mode replay' in step
    # Comments may mention live; no command may run it.
    commands = '\n'.join(line for line in text.splitlines()
                          if not line.lstrip().startswith('#'))
    assert '--mode live' not in commands
    # The whole job, not just the step: a key in the job env would reach every step.
    assert 'DEEPSEEK_API_KEY' not in text
    assert 'secrets.' not in text.split('jobs:')[1]


def test_nightly_rebuilds_the_injection_carriers_before_evaluating():
    """The Site is built from nothing every night. Without this the injection cases would
    quietly become "there is no injected text on the Site" — a green that proves nothing."""
    text = NIGHTLY.read_text(encoding='utf-8')
    carriers = text.index('evals/setup/injection.py')
    evaluate = text.index('evals/run.py')
    assert carriers < evaluate, '注入载体必须在评估之前重建'
    assert 'provision_eval_identity.py' in text


def test_nightly_compares_against_the_archived_baseline():
    assert '--compare-baseline evals/baseline.json' in NIGHTLY.read_text(encoding='utf-8')


def test_nightly_scans_and_uploads_the_evaluation_artifacts():
    text = NIGHTLY.read_text(encoding='utf-8')
    assert 'work/evals/**/*.json' in text, '评估报表必须过泄漏自检'
    assert re.search(r'path: \|(?:.|\n)*?work/evals/', text), '评估报表必须上传'


def test_live_evaluation_workflow_has_no_schedule_trigger():
    """A timer that spends money with nobody watching is a standing charge, not a gate."""
    text = LIVE.read_text(encoding='utf-8')
    triggers = text.split('on:')[1].split('permissions:')[0]
    assert 'workflow_dispatch' in triggers
    assert 'schedule' not in triggers
    assert 'push' not in triggers and 'pull_request' not in triggers


def test_live_evaluation_is_disabled_until_someone_enables_it():
    text = LIVE.read_text(encoding='utf-8')
    assert "vars.DSHERP_EVAL_LIVE == '1'" in text
    assert "vars.DSHERP_EVAL_LIVE != '1'" in text, '未启用时要明确失败，不是静默跳过'
