#!/usr/bin/env python3
"""A schema change without a migration path is a change that cannot be deployed.

Run over one revision range. A commit that edits a DocType JSON must add a line to
that App's patches.txt, or say "no-patch: <reason>" in its message. A change to a
stored payload's schema_version has no such escape: it always needs a backfill.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCTYPE_JSON = re.compile(r'^frappe_app/(?P<app>[a-z_]+)/.*/doctype/[a-z0-9_]+/[a-z0-9_]+\.json$')
PATCHES = re.compile(r'^frappe_app/(?P<app>[a-z_]+)/patches\.txt$')
NO_PATCH = re.compile(r'no-patch:\s*(?P<reason>\S.*)')
SECTION = re.compile(r'^\[[a-z_]+\]$')


# Where a stored payload's own version can actually live. Two exclusions, each for the same
# reason and each learned from a real refusal:
#
# - `public/dist`: esbuild puts the whole application on a few enormous lines, two of which
#   carry the page-context snapshot's `schema_version`. Every frontend rebuild added such a
#   line. A build artefact is not a stored payload.
# - `tests/`: a native test builds a page context to hand to the code under test, so its
#   fixture contains `schema_version` too. Slice 6 added three such files and the guard
#   refused the whole branch, naming a migration that never happened. A fixture is not a
#   stored payload either.
PAYLOAD_SCOPE = ('frappe_app', ':(exclude)frappe_app/*/public/dist/**',
                 ':(exclude)frappe_app/*/tests/**')


def schema_version_moved(diff):
    """Whether an **existing** payload's schema_version changed in this diff.

    Both halves matter. An added line alone is not a migration event: a payload declared for
    the first time at today's version has no stored data behind it to back-fill, and that is
    what every false positive so far has been — a fixture, a rebuilt bundle, a new test. What
    needs a backfill is a version that **moved**, and a move always removes the old line as
    well as adding the new one.
    """
    added = removed = False
    for line in diff.splitlines():
        if 'schema_version' not in line:
            continue
        if line.startswith('+') and not line.startswith('+++'):
            added = True
        elif line.startswith('-') and not line.startswith('---'):
            removed = True
    return added and removed


def added_patch_lines(diff):
    """Lines a diff adds to a patches.txt, ignoring comments and section headers."""
    added = []
    for line in diff.splitlines():
        if not line.startswith('+') or line.startswith('+++'):
            continue
        body = line[1:].strip()
        if not body or body.startswith('#') or SECTION.fullmatch(body):
            continue
        added.append(body)
    return added


def review(changed_files, patch_diffs, message, schema_version_changed=False):
    """Return the reasons this change may not ship; empty means it may."""
    apps = sorted({match.group('app') for match in
                   (DOCTYPE_JSON.match(name) for name in changed_files) if match})
    if not apps and not schema_version_changed:
        return []
    excuse = NO_PATCH.search(message or '')
    problems = []
    if schema_version_changed and not any(added_patch_lines(diff) for diff in patch_diffs.values()):
        problems.append('修改了存量 payload 的 schema_version，必须附回填 patch，且不接受 no-patch 说明')
        return problems
    for app in apps:
        if added_patch_lines(patch_diffs.get(app, '')):
            continue
        if excuse:
            continue
        problems.append(f'{app} 的 DocType 定义有变更，但 {app}/patches.txt 没有新增迁移，'
                        '提交信息里也没有 "no-patch: 原因"')
    return problems


def base_revision(environ=None, runner=subprocess.run, root=ROOT):
    """The revision a change is judged against. CI names it (DSHERP_GUARD_BASE: the PR's base
    or the commit that was pushed over); a developer's checkout falls back to the merge-base
    with origin/main; None when there is nothing to compare with."""
    environ = os.environ if environ is None else environ
    explicit = (environ.get('DSHERP_GUARD_BASE') or '').strip()
    if explicit:
        return explicit
    found = runner(['git', 'merge-base', 'origin/main', 'HEAD'], cwd=root, text=True,
                   capture_output=True, timeout=60)
    return found.stdout.strip() if found.returncode == 0 and found.stdout.strip() else None


def _git(*arguments, root=ROOT):
    result = subprocess.run(['git', *arguments], cwd=root, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise SystemExit(f'git {" ".join(arguments)} 失败：{result.stderr.strip()}')
    return result.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description='DocType 变更必须带迁移路径')
    parser.add_argument('base', nargs='?', default=None,
                        help='缺省：$DSHERP_GUARD_BASE，否则 merge-base origin/main HEAD')
    parser.add_argument('head', nargs='?', default='HEAD')
    arguments = parser.parse_args(argv)
    base = arguments.base or base_revision()
    if not base:
        raise SystemExit('没有可比较的基线：设置 DSHERP_GUARD_BASE，或先 git fetch origin main')
    span = f'{base}..{arguments.head}'
    changed = [name for name in _git('diff', '--name-only', span).splitlines() if name]
    patch_diffs = {}
    for name in changed:
        match = PATCHES.match(name)
        if match:
            patch_diffs[match.group('app')] = _git('diff', span, '--', name)
    schema_version_changed = schema_version_moved(_git('diff', span, '--', *PAYLOAD_SCOPE))
    problems = review(changed, patch_diffs, _git('log', '--format=%B', span), schema_version_changed)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
