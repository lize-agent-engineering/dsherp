"""A DocType change ships with its migration or it does not ship."""
from infra.check_doctype_patches import added_patch_lines, review


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


def test_only_real_patch_lines_are_counted():
    assert added_patch_lines(ONLY_COMMENT) == []
    assert added_patch_lines(PATCH_ADDED) == ["dsherp_bridge.patches.v1_backfill_owner"]


def test_both_apps_are_reported_separately():
    problems = review([DOCTYPE, "frappe_app/dsherp_platform/platform/doctype/ds_enterprise/ds_enterprise.json"],
                      {}, "feat: 两个 App 都改了")
    assert len(problems) == 2
