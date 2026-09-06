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


def hold(runtime_dir, site, reason):
    directory = _directory(runtime_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / site
    path.write_text(json.dumps({'reason': reason, 'pid': os.getpid(),
                                'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}))
    return path


def release(runtime_dir, site):
    (_directory(runtime_dir) / site).unlink(missing_ok=True)


def held(runtime_dir, on_error='raise'):
    """The Sites currently held. `on_error='raise'` propagates an unreadable directory: a
    caller that cannot tell whether a window is open must not conclude 'no holds'."""
    directory = _directory(runtime_dir)
    try:
        if not directory.is_dir():
            return set()
        return {entry.name for entry in directory.iterdir() if entry.is_file()}
    except OSError:
        if on_error == 'raise':
            raise
        return set()
