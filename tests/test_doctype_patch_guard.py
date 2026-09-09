"""A DocType change ships with its migration or it does not ship."""
from infra.check_doctype_patches import added_patch_lines, review, schema_version_moved


DOCTYPE = "frappe_app/dsherp_bridge/dsherp_bridge/doctype/ds_model_run/ds_model_run.json"
PATCH_ADDED = "--- a/frappe_app/dsherp_bridge/patches.txt\n+++ b\n+dsherp_bridge.patches.v1_backfill_owner\n"
ONLY_COMMENT = "+++ b\n+# a note\n+[post_model_sync]\n"


def test_a_change_that_touches_no_doctype_needs_nothing():
    assert review(["dsherp/admin.py", "README.md"], {}, "feat: 无关变更") == []


def test_a_doctype_change_without_a_patch_or_a_reason_is_refused():
    problems = review([DOCTYPE], {}, "feat: 加一个字段")
    assert len(problems) == 1 and "dsherp_bridge" in problems[0]


def test_a_doctype_change_with_a_new_patch_line_is_accepted():
    assert review([DOCTYPE, "frappe_app/dsherp_bridge/patches.txt"],
                  {"dsherp_bridge": PATCH_ADDED}, "feat: 加一个字段") == []


def test_a_patches_file_that_only_gained_comments_does_not_count_as_a_migration():
    problems = review([DOCTYPE, "frappe_app/dsherp_bridge/patches.txt"],
                      {"dsherp_bridge": ONLY_COMMENT}, "feat: 加一个字段")
    assert len(problems) == 1


def test_an_explicit_no_patch_reason_is_accepted_for_a_pure_definition_change():
    assert review([DOCTYPE], {}, "feat: 加一个字段\n\nno-patch: 新字段无存量数据需要回填") == []


def test_a_bare_no_patch_without_a_reason_is_not_accepted():
    assert len(review([DOCTYPE], {}, "feat: 加一个字段\n\nno-patch:")) == 1


def test_a_stored_schema_version_change_always_needs_a_backfill():
    problems = review([DOCTYPE], {}, "feat: 变更 payload\n\nno-patch: 我觉得不用",
                      schema_version_changed=True)
    assert len(problems) == 1 and "schema_version" in problems[0]
    assert review([DOCTYPE, "frappe_app/dsherp_bridge/patches.txt"],
                  {"dsherp_bridge": PATCH_ADDED}, "feat: 变更 payload",
                  schema_version_changed=True) == []


def test_a_version_that_moved_needs_a_backfill_but_one_merely_introduced_does_not():
    """The half of the rule that was missing, and that refused a whole branch for it.

    A backfill exists to carry **stored** data forward. A payload declared for the first time
    at today's version has nothing stored behind it — and every false positive so far was
    exactly that shape: a new test fixture, a rebuilt bundle, a new file. A version that
    actually moved removes the old line as well as adding the new one.
    """
    moved = ("--- a/frappe_app/dsherp_bridge/context_api.py\n"
             "+++ b/frappe_app/dsherp_bridge/context_api.py\n"
             "-    if value.get('schema_version') != 1:\n"
             "+    if value.get('schema_version') != 2:\n")
    introduced = ("--- /dev/null\n"
                  "+++ b/frappe_app/dsherp_bridge/tests/test_quota.py\n"
                  "+PAGE = {'schema_version': 1, 'page_type': 'unknown', 'route': []}\n")
    assert schema_version_moved(moved) is True
    assert schema_version_moved(introduced) is False
    assert schema_version_moved("") is False


def test_a_native_test_fixture_is_not_a_stored_payload():
    """A native test builds a page context to hand to the code under test, so its fixture
    contains `schema_version` too. Slices 4 and 6 each added such files and the guard refused
    the whole branch over them, naming a migration that never happened — the same false
    positive the built bundles produced, one directory over.

    Asserted as the rule rather than as the presence of any particular fixture, so it holds on
    every branch: the scan excludes the native test tree, and a fixture-shaped diff does not
    trip the judgement even inside the scan."""
    from infra.check_doctype_patches import PAYLOAD_SCOPE

    assert any('tests' in item and item.startswith(':(exclude)') for item in PAYLOAD_SCOPE), \
        '原生测试目录必须排除在存量 payload 的扫描之外'
    assert any('public/dist' in item for item in PAYLOAD_SCOPE), '构建产物的排除不能被顺手删掉'
    fixture = ("--- /dev/null\n"
               "+++ b/frappe_app/dsherp_bridge/tests/test_read_tools.py\n"
               "+PAGE = json.dumps({'schema_version': 1, 'page_type': 'unknown', 'route': []})\n")
    assert schema_version_moved(fixture) is False


def test_only_real_patch_lines_are_counted():
    assert added_patch_lines(ONLY_COMMENT) == []
    assert added_patch_lines(PATCH_ADDED) == ["dsherp_bridge.patches.v1_backfill_owner"]


def test_both_apps_are_reported_separately():
    problems = review([DOCTYPE, "frappe_app/dsherp_platform/platform/doctype/ds_enterprise/ds_enterprise.json"],
                      {}, "feat: 两个 App 都改了")
    assert len(problems) == 2


def test_the_guard_runs_over_this_branch_and_finds_it_shippable():
    """The unit tests above check the rule; this one checks that the branch obeys it, which is
    what the rule is for. It runs the guard the way an operator would."""
    import subprocess
    from pathlib import Path

    from infra.check_doctype_patches import base_revision

    root = Path(__file__).resolve().parents[1]
    base = base_revision()
    if base is None:
        import pytest
        pytest.skip("no DSHERP_GUARD_BASE and no origin/main to compare against")
    result = subprocess.run([str(root / ".venv/bin/python"), "-m", "infra.check_doctype_patches",
                             base, "HEAD"], cwd=root, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-500:]


def test_the_guard_base_comes_from_the_environment_before_the_merge_base():
    """CI names the base it wants judged (the PR's base, or the commit that was pushed over);
    a developer's checkout falls back to the merge-base with origin/main."""
    import subprocess
    from infra.check_doctype_patches import base_revision
    calls = []

    def git(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "abc123\n", "")

    assert base_revision({"DSHERP_GUARD_BASE": "deadbeef"}, runner=git) == "deadbeef" and calls == []
    assert base_revision({"DSHERP_GUARD_BASE": "  "}, runner=git) == "abc123" and calls[0][:2] == ["git", "merge-base"]

    def no_origin(command, **kwargs):
        return subprocess.CompletedProcess(command, 128, "", "fatal: Not a valid object name origin/main")

    assert base_revision({}, runner=no_origin) is None


def test_a_rebuilt_frontend_bundle_is_not_a_stored_payload_schema_change():
    """The built Desk bundles are committed - ci.yml requires dist to match source - and esbuild
    minifies the whole application onto a handful of enormous lines, two of which contain the
    string `schema_version` because the page-context snapshot carries a version of its own. So
    every frontend change adds a `+` line containing `schema_version`, and this guard's one
    branch with no escape hatch fired on it: every commit in this repository that ever touched
    those bundles trips it, each reporting a stored-payload migration that never happened.

    The gate itself stays: a real stored payload whose schema_version moves still needs a
    backfill, and no-patch still cannot excuse it. What must not count is a build artefact."""
    import subprocess
    import sys
    from pathlib import Path

    import pytest

    root = Path(__file__).resolve().parents[1]

    def git(*arguments):
        return subprocess.run(['git', *arguments], cwd=root, text=True, capture_output=True,
                              timeout=60).stdout

    bundles = sorted(str(path.relative_to(root)) for path in (root / 'frappe_app').glob('*/public/dist'))
    assert bundles, '仓库里应当有被提交的前端产物目录'
    dist_commits = git('log', '--format=%H', '-20', '--', *bundles).split()
    innocent = None
    for commit in dist_commits:
        touched = git('show', '--name-only', '--format=', commit).split()
        if not any('/doctype/' in name and name.endswith('.json') for name in touched):
            innocent = commit
            break
    if innocent is None:
        pytest.skip('no commit in recent history rebuilt the bundles without touching a DocType')

    result = subprocess.run(
        [sys.executable, '-m', 'infra.check_doctype_patches', innocent + '^', innocent],
        cwd=root, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, (
        f'{innocent[:9]} 只重建了前端产物、没碰任何 DocType，守卫却拒绝了它：\n{result.stderr}')
