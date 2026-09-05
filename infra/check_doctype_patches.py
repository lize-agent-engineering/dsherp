#!/usr/bin/env python3
"""A schema change without a migration path is a change that cannot be deployed.

Run over one revision range. A commit that edits a DocType JSON must add a line to
that App's patches.txt, or say "no-patch: <reason>" in its message. A change to a
stored payload's schema_version has no such escape: it always needs a backfill.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCTYPE_JSON = re.compile(r'^frappe_app/(?P<app>[a-z_]+)/.*/doctype/[a-z0-9_]+/[a-z0-9_]+\.json$')
PATCHES = re.compile(r'^frappe_app/(?P<app>[a-z_]+)/patches\.txt$')
NO_PATCH = re.compile(r'no-patch:\s*(?P<reason>\S.*)')
SECTION = re.compile(r'^\[[a-z_]+\]$')


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


def _git(*arguments, root=ROOT):
    result = subprocess.run(['git', *arguments], cwd=root, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise SystemExit(f'git {" ".join(arguments)} 失败：{result.stderr.strip()}')
    return result.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description='DocType 变更必须带迁移路径')
    parser.add_argument('base', nargs='?', default='origin/main')
    parser.add_argument('head', nargs='?', default='HEAD')
    arguments = parser.parse_args(argv)
    span = f'{arguments.base}..{arguments.head}'
    changed = [name for name in _git('diff', '--name-only', span).splitlines() if name]
    patch_diffs = {}
    for name in changed:
        match = PATCHES.match(name)
        if match:
            patch_diffs[match.group('app')] = _git('diff', span, '--', name)
    schema_version_changed = any(
        '+' in line and 'schema_version' in line
        for line in _git('diff', span, '--', 'frappe_app').splitlines() if line.startswith('+'))
    problems = review(changed, patch_diffs, _git('log', '--format=%B', span), schema_version_changed)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
