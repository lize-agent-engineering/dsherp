"""Content identity of the selected runtime, never a model-supplied version."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FILES=('config/dsh-business.yml','config/dsh-context.yml',
       'runtime/context-plugin.cjs','runtime/model-guard.cjs',
       'dsherp/context_runner.py','dsherp/context_mcp.py',
       'dsherp/read_tools.py','dsherp/session_runtime.py',
       'dsherp/runtime_revision.py','requirements.lock')
KEYS=('DEEPSEEK_API_KEY','DSH_MODEL','DEEPSEEK_BASE_URL')


def configuration_revision(settings,root=ROOT):
    for key in KEYS:
        if not isinstance(settings.get(key),str) or not settings[key].strip():
            raise ValueError('Missing runtime configuration: '+key)
    files=[[name,hashlib.sha256((root/name).read_bytes()).hexdigest()] for name in FILES]
    # The digest binds credentials too, but never stores or exposes their value.
    value=[files,[settings[key] for key in KEYS]]
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
