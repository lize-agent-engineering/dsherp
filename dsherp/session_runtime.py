"""Stable native session runtime ownership."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import sys
import json
from typing import Literal

from deepseek_harness import DeepSeekHarness
from pydantic import BaseModel

ROOT=Path(__file__).resolve().parents[1]


def _business_budget(config):
    raw=config.get('budget') if isinstance(config,dict) else None
    if not isinstance(raw,dict):raise ValueError('Missing run budget')
    required=('provider','model','model_request_timeout_seconds','model_max_output_tokens_per_call')
    if any(key not in raw for key in required):raise ValueError('Missing run budget')
    provider=raw['provider'];model=raw['model']
    if (provider!='deepseek-official' or not isinstance(model,str) or not model
        or model.strip()!=model):
        raise ValueError('Invalid run budget policy')
    for key in ('model_request_timeout_seconds','model_max_output_tokens_per_call'):
        if type(raw[key]) is not int or raw[key]<1:raise ValueError('Invalid run budget value')
    return raw


class OpenedSession(BaseModel):
    sessionId: str
    source: str


class PersistedSession(BaseModel):
    sessionId: str
    exists: bool


@contextmanager
def session_writer(directory: Path):
    """One writer per stable directory; the kernel releases ownership on death."""
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(directory/'writer.lock',os.O_CREAT|os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise RuntimeError('Native session already has a writer') from exc
        yield
    finally:
        os.close(fd)


@contextmanager
def open_runtime(settings: dict, directory: Path, session_id: str, *, resume: bool | Literal['inspect'], run_config: Path | None = None):
    """Internal runtime entry: the isolated worker supplies its authorized directory.

    This does not establish tenant authorization or sanitize a host environment;
    business use belongs inside the credential-scoped container.
    """
    if not isinstance(session_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',session_id):
        raise ValueError('Invalid native session identity')
    if type(resume) is not bool and resume!='inspect':raise ValueError('Explicit resume decision required')
    config=json.loads(run_config.read_text()) if run_config else None
    budget=_business_budget(config) if config is not None else None
    required=('DEEPSEEK_API_KEY','DEEPSEEK_BASE_URL') if budget else ('DEEPSEEK_API_KEY','DSH_MODEL','DEEPSEEK_BASE_URL')
    for key in required:
        if not isinstance(settings.get(key),str) or not settings[key].strip():
            raise ValueError('Missing runtime setting: '+key)
    directory=directory.absolute()
    domain=None
    if config is not None:
        domain=config.get('domain')
        if domain not in ('query','operation','configuration'):raise ValueError('Unknown business domain')
    with session_writer(directory):
        runtime=DeepSeekHarness(provider=budget['provider'] if budget else 'deepseek-official',
            model=budget['model'] if budget else settings['DSH_MODEL'],
            api_key=settings['DEEPSEEK_API_KEY'],base_url=settings['DEEPSEEK_BASE_URL'],
            cordis=str(ROOT/'config'/('dsh-business.yml' if run_config else 'dsh-context.yml')),cwd=str(directory),runtime_cwd=str(directory),
            session_root=str(directory/'sessions'),
            max_tokens=budget['model_max_output_tokens_per_call'] if budget else 2048,
            request_timeout_seconds=budget['model_request_timeout_seconds'] if budget else 90,
            shutdown_timeout_seconds=5,
            env={} if run_config is None else {'DSHERP_RUN_CONFIG':str(run_config.absolute()),
                'DSHERP_PYTHON':sys.executable,'DSHERP_PROJECT':str(ROOT),'DSHERP_DOMAIN':domain,
                'DSHERP_MODEL_MAX_OUTPUT_TOKENS':str(budget['model_max_output_tokens_per_call'])})
        try:
            runtime.start()
            if resume=='inspect':
                persisted=runtime.client.request('dsherp/session/exists',{'sessionId':session_id},response_model=PersistedSession)
                if persisted.sessionId!=session_id:raise RuntimeError('Native persistence identity mismatch')
                resume=persisted.exists
            opened=runtime.client.request('dsherp/session/open',{'sessionId':session_id,'resume':resume},response_model=OpenedSession)
            if opened.sessionId!=session_id or opened.source!=('resume' if resume else 'startup'):
                raise RuntimeError('Native session open response did not match request')
            yield runtime
        finally:
            runtime.close()
