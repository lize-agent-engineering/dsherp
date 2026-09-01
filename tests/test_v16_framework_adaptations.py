"""Source-level guards for v16-only boundaries; runtime probes provide the integration proof."""
import ast
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SSO = (ROOT / "frappe_app/dsherp_bridge/sso.py").read_text()
OPERATIONS = (ROOT / "frappe_app/dsherp_bridge/operations.py").read_text()
OAUTH_PROVISION = (ROOT / "infra/provision_desk_oauth.py").read_text()


def test_sso_uses_native_v16_desk_route_and_ends_identity_snapshot_before_login():
    assert "/app/home" not in SSO
    assert SSO.count("/desk/home") == 3
    exchange = SSO.index("info,token=exchange(code)")
    rollback = SSO.index("frappe.db.rollback()", exchange)
    login = SSO.index("frappe.local.login_manager.login_as(user)", rollback)
    assert exchange < rollback < login


def test_oauth_client_explicitly_allows_the_existing_platform_member_role():
    compact = "".join(OAUTH_PROVISION.split())
    assert "'allowed_roles':[{{'role':'DSHERPMember'}}]" in compact


def test_subcontracting_preparer_uses_the_v16_supplied_items_method():
    assert "SubcontractingController.create_raw_materials_supplied(doc)" not in OPERATIONS
    compact = "".join(OPERATIONS.split())
    assert (
        "SubcontractingController.create_raw_materials_supplied_or_received("
        "doc,raw_material_table='supplied_items')"
    ) in compact


def test_all_custom_doctype_json_files_make_creation_sorting_explicit():
    paths = sorted(ROOT.glob("frappe_app/**/doctype/*/*.json"))
    assert len(paths) == 13
    missing = []
    for path in paths:
        data = json.loads(path.read_text())
        if data.get("sort_field") != "creation" or data.get("sort_order") != "DESC":
            missing.append(path.relative_to(ROOT).as_posix())
    assert not missing, missing


def test_both_apps_register_a_native_v16_apps_screen_route():
    hooks = {
        "bridge": (ROOT / "frappe_app/dsherp_bridge/hooks.py").read_text(),
        "platform": (ROOT / "frappe_app/dsherp_platform/hooks.py").read_text(),
    }
    assert "add_to_apps_screen" in hooks["bridge"]
    assert '"route": "/desk/dsherp-agent"' in hooks["bridge"]
    assert "add_to_apps_screen" in hooks["platform"]
    assert '"route": "/desk/dsherp-home"' in hooks["platform"]


def test_every_custom_get_all_query_has_explicit_deterministic_ordering():
    missing = []
    for path in sorted(ROOT.glob("frappe_app/**/*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "get_all":
                continue
            if not any(keyword.arg == "order_by" for keyword in node.keywords):
                missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not missing, missing


def test_stock_ledger_verification_reads_all_rows_in_deterministic_order():
    tree = ast.parse(OPERATIONS)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_list"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "Stock Ledger Entry"
    ]
    assert len(calls) == 1
    keywords = {
        keyword.arg: ast.literal_eval(keyword.value)
        for keyword in calls[0].keywords
        if keyword.arg in {"order_by", "limit_page_length"}
    }
    assert keywords["order_by"] == "creation asc, name asc"
    assert keywords["limit_page_length"] == 0


def test_server_generated_desk_links_do_not_retain_v15_app_routes():
    paths = [
        ROOT / "frappe_app/dsherp_bridge/configuration.py",
        ROOT / "frappe_app/dsherp_bridge/configuration_transfer.py",
        ROOT / "frappe_app/dsherp_bridge/sso.py",
    ]
    stale = [path.relative_to(ROOT).as_posix() for path in paths if "/app/" in path.read_text()]
    assert not stale, stale


def test_manufacturing_fixture_requires_the_exact_v16_image_versions():
    source = (ROOT / "infra/provision_manufacturing_fixture.py").read_text()
    assert "ERP_VERSION = '16.33.0'" in source
    assert "FRAPPE_VERSION = '16.31.0'" in source
