"""Per-Site holds: the host CLI opens one while a Site's data is being backed up or judged;
the worker reads them every tick and claims nothing for a held Site.

A hold only stops NEW claims. It does not end work already in flight - the caller waits for
that separately - and it never stops heartbeats, because a Site whose heartbeat stops answers
503 to its users."""
import json
import os
import time
from pathlib import Path


def _directory(runtime_dir):
    return Path(runtime_dir) / 'holds'


# A hold outlives a killed holder only this long. Long enough for a backup window (executor
# wait, drain, dump) - not for ever: a SIGKILLed command must not stop a Site's claims until
# somebody notices.
DEFAULT_TTL_SECONDS = 7200


def hold(runtime_dir, site, reason, ttl_seconds=DEFAULT_TTL_SECONDS):
    directory = _directory(runtime_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / site
    now = time.time()
    path.write_text(json.dumps({'reason': reason, 'pid': os.getpid(),
                                'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
                                'until': now + ttl_seconds}))
    return path


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _in_force(path, now):
    """Whether this hold still counts: not past its end, and its holder still running. A file
    that cannot be read is counted as held - not knowing is not the same as no hold."""
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return True
    until = record.get('until')
    if isinstance(until, (int, float)) and now >= until:
        return False
    pid = record.get('pid')
    if isinstance(pid, int) and pid > 0 and not _alive(pid):
        return False
    return True


def release(runtime_dir, site):
    (_directory(runtime_dir) / site).unlink(missing_ok=True)


def held(runtime_dir, on_error='raise', now=None):
    """The Sites currently held. `on_error='raise'` propagates an unreadable directory: a
    caller that cannot tell whether a window is open must not conclude 'no holds'. A hold
    past its end, or whose holder is no longer running, no longer counts."""
    directory = _directory(runtime_dir)
    now = time.time() if now is None else now
    try:
        if not directory.is_dir():
            return set()
        return {entry.name for entry in directory.iterdir() if entry.is_file() and _in_force(entry, now)}
    except OSError:
        if on_error == 'raise':
            raise
        return set()
