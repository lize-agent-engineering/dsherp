"""Where a run's native session lives on the host, and how it is found again (T4).

The runner is handed one directory and may not see any other: that isolation comes from the
scope id, a digest the server computes over site, owner, conversation, domain and the current
runtime session. Used as the whole path, it also made the sessions unfindable - nothing could
list what belonged to a person, and rotating the runtime session left a directory nobody could
attribute to anything.

The layout is now `<site>/<conversation>/<scope>`: the scope still names the directory the
runner gets, and a person's sessions are reachable the only way that respects the boundary -
through the conversations they own. No user identity is written into a path."""
import re
import shutil
import time
from pathlib import Path

SCOPE = re.compile('[a-f0-9]{64}')
# A Site name and a conversation name are both Frappe names: no separators, no traversal.
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
RETENTION_DAYS = 90


def _checked(value, pattern, what):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f'Invalid {what}: {value!r}')
    return value


def directory(root, *, site, conversation, scope):
    """The directory this run's session belongs in. Never escapes `root`."""
    return (Path(root) / _checked(site, NAME, 'site') / _checked(conversation, NAME, 'conversation')
            / _checked(scope, SCOPE, 'session scope'))


def of_conversations(root, site, conversations):
    """Every session directory of these conversations, superseded ones included."""
    found = []
    for conversation in conversations:
        folder = Path(root) / site / conversation
        if not folder.is_dir():
            continue
        found += [child for child in sorted(folder.iterdir()) if child.is_dir()]
    return found


def remove(root, site, conversations):
    """Delete these conversations' sessions whole. Returns the conversations actually removed."""
    removed = []
    for conversation in conversations:
        folder = Path(root) / _checked(site, NAME, 'site') / _checked(conversation, NAME, 'conversation')
        if folder.is_dir():
            shutil.rmtree(folder)
            removed.append(conversation)
    return removed


def _newest(path):
    newest = path.stat().st_mtime
    for child in path.rglob('*'):
        try:
            newest = max(newest, child.stat().st_mtime)
        except OSError:
            continue
    return newest


def sweep(root, days=RETENTION_DAYS, now=None):
    """Remove sessions untouched for longer than the window.

    Directories written before this layout are one-way hashes of five values; nothing can
    attribute them to a person or a conversation any more, so they are reported for a human to
    decide about rather than deleted on a guess."""
    root = Path(root)
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    report = {'removed': [], 'kept': 0, 'unattributed': []}
    if not root.is_dir():
        return report
    for site in sorted(root.iterdir()):
        if not site.is_dir():
            continue
        if SCOPE.fullmatch(site.name):
            report['unattributed'].append(site.name)     # the old flat layout
            continue
        for conversation in sorted(site.iterdir()):
            if not conversation.is_dir():
                continue
            for scope in sorted(conversation.iterdir()):
                if not scope.is_dir():
                    continue
                if _newest(scope) < cutoff:
                    shutil.rmtree(scope)
                    report['removed'].append(f'{site.name}/{conversation.name}/{scope.name}')
                else:
                    report['kept'] += 1
            if not any(conversation.iterdir()):
                conversation.rmdir()
    return report
