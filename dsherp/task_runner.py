"""Single task SDK process. Production entry is only an isolated container."""
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from deepseek_harness import DeepSeekHarness

ROOT = Path(__file__).resolve().parents[1]


def run_task(config_path: Path, work_root: Path) -> str:
    config = json.loads(config_path.read_text())
    for key in ('task_id', 'capability', 'question', 'DEEPSEEK_API_KEY', 'DSH_MODEL', 'DEEPSEEK_BASE_URL'):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError(f'Missing task configuration: {key}')
    # rc1 merges env with os.environ. Clear the ambient environment before spawning.
    original = dict(os.environ)
    clean = {k: original[k] for k in ('PATH', 'LANG', 'SSL_CERT_FILE') if k in original}
    clean.update({'HOME': str(work_root), 'TMPDIR': str(work_root), 'PYTHONPATH': str(ROOT),
                  'PYTHONDONTWRITEBYTECODE': '1', 'DSHERP_PYTHON': sys.executable,
                  'DSHERP_PROJECT': str(ROOT), 'DSHERP_TASK_CONFIG': str(config_path)})
    try:
        os.environ.clear()
        os.environ.update(clean)
        with TemporaryDirectory(prefix='session-', dir=work_root) as directory:
            harness = DeepSeekHarness(provider='deepseek-official', model=config['DSH_MODEL'],
                api_key=config['DEEPSEEK_API_KEY'], base_url=config['DEEPSEEK_BASE_URL'],
                cordis=str(ROOT / 'config/dsh-task.yml'), cwd=directory, runtime_cwd=directory,
                session_root=str(Path(directory) / 'sessions'), max_tokens=2048,
                request_timeout_seconds=90, shutdown_timeout_seconds=3)
            try:
                result = harness.run(config['question'])
                if result.finish_reason != 'completed' or not result.final_response.strip():
                    raise RuntimeError('Agent did not complete with an answer')
                return result.final_response.strip()
            finally:
                harness.close()
    finally:
        os.environ.clear()
        os.environ.update(original)


def main():
    try:
        answer = run_task(Path('/run/task.json'), Path('/tmp'))
        print(json.dumps({'answer': answer}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f'Agent execution failed: {type(exc).__name__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
