"""Stable native session runtime ownership."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re

from deepseek_harness import DeepSeekHarness
from pydantic import BaseModel

ROOT=Path(__file__).resolve().parents[1]


class OpenedSession(BaseModel):
    sessionId: str
    source: str


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
def open_runtime(settings: dict, directory: Path, session_id: str, *, resume: bool):
    """Internal runtime entry: the isolated worker supplies its authorized directory.

    This does not establish tenant authorization or sanitize a host environment;
    business use belongs inside the credential-scoped container.
    """
    if not isinstance(session_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',session_id):
        raise ValueError('Invalid native session identity')
    if type(resume) is not bool:raise ValueError('Explicit resume decision required')
    for key in ('DEEPSEEK_API_KEY','DSH_MODEL','DEEPSEEK_BASE_URL'):
        if not isinstance(settings.get(key),str) or not settings[key].strip():
            raise ValueError('Missing runtime setting: '+key)
    directory=directory.absolute()
    with session_writer(directory):
        runtime=DeepSeekHarness(provider='deepseek-official',model=settings['DSH_MODEL'],
            api_key=settings['DEEPSEEK_API_KEY'],base_url=settings['DEEPSEEK_BASE_URL'],
            cordis=str(ROOT/'config/dsh-context.yml'),cwd=str(directory),runtime_cwd=str(directory),
            session_root=str(directory/'sessions'),max_tokens=2048,
            request_timeout_seconds=90,shutdown_timeout_seconds=5)
        try:
            runtime.start()
            opened=runtime.client.request('dsherp/session/open',{'sessionId':session_id,'resume':resume},response_model=OpenedSession)
            if opened.sessionId!=session_id or opened.source!=('resume' if resume else 'startup'):
                raise RuntimeError('Native session open response did not match request')
            yield runtime
        finally:
            runtime.close()
