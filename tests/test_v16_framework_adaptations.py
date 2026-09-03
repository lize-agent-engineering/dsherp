"""Source-level guards for v16-only boundaries; runtime probes provide the integration proof."""
import ast
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SSO = (ROOT / "frappe_app/dsherp_bridge/sso.py").read_text()
OPERATIONS = (ROOT / "frappe_app/dsherp_bridge/operations.py").read_text()
OAUTH_PROVISION = (ROOT / "infra/provision_desk_oauth.py").read_text()
DAILY_OAUTH_PROVISION = (ROOT / "infra/provision_daily_agent.py").read_text()
DAILY_INITIALIZER = (ROOT / "infra/initialize_daily_synthetic.py").read_text()
DAILY_BACKUP_VERIFIER = (ROOT / "infra/verify_daily_backup.py").read_text()
CONFIGURATION_LOCKS = (ROOT / "frappe_app/dsherp_bridge/configuration_locks.py").read_text()
ALPHA_RUNTIME_PROVISION = (ROOT / "infra/provision_context_worker.py").read_text()
MANUFACTURING_PROVISION = (ROOT / "infra/provision_manufacturing_fixture.py").read_text()


def test_sso_uses_native_v16_desk_route_and_ends_identity_snapshot_before_login():
    assert "/app/home" not in SSO
    assert SSO.count("/desk/dsherp-agent") == 3
    exchange = SSO.index("info,token=exchange(code)")
    rollback = SSO.index("frappe.db.rollback()", exchange)
    login = SSO.index("frappe.local.login_manager.login_as(user)", rollback)
    assert exchange < rollback < login


def test_oauth_client_explicitly_allows_the_existing_platform_member_role():
    for source in (OAUTH_PROVISION, DAILY_OAUTH_PROVISION):
        compact = "".join(source.split())
        assert "'allowed_roles':[{{'role':'DSHERPMember'}}]" in compact


def test_alpha_and_beta_ready_enterprises_are_both_provisioned_for_desk_sso():
    for expected in (
        "'enterprise': 'alpha'",
        "'enterprise': 'beta'",
        "'app_name': 'DSHERP beta Desk'",
        "'callback': 'http://preview.localhost:18085/api/method/dsherp_bridge.sso.callback'",
        "'desk_url': 'http://preview.localhost:18085/api/method/dsherp_bridge.sso.start'",
    ):
        assert expected in OAUTH_PROVISION


def test_subcontracting_preparer_uses_the_v16_supplied_items_method():
    assert "SubcontractingController.create_raw_materials_supplied(doc)" not in OPERATIONS
    compact = "".join(OPERATIONS.split())
    assert (
        "SubcontractingController.create_raw_materials_supplied_or_received("
        "doc,raw_material_table='supplied_items')"
    ) in compact


def test_all_custom_doctype_json_files_make_creation_sorting_explicit():
    paths = sorted(ROOT.glob("frappe_app/**/doctype/*/*.json"))
    assert len(paths) == 14
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
    for name in ("Products", "Raw Material", "Services"):
        assert f"filters={{'name': '{name}', 'is_group': 0}}" in source
    for stale in ("产品展示", "filters={'name': '原材料'", "filters={'name': '服务'"):
        assert stale not in source


def test_daily_initializer_uses_the_fresh_v16_master_names():
    compact = "".join(DAILY_INITIALIZER.split())
    assert "'item_group':'Products'" in compact
    assert "'customer_group':'Individual'" in compact
    assert "get_root_of('ItemGroup')" not in compact
    assert "'customer_group':'个人'" not in compact


def test_daily_backup_verifier_uses_v16_file_archive_names():
    assert "f'{prefix}-files.tar'" in DAILY_BACKUP_VERIFIER
    assert "f'{prefix}-private-files.tar'" in DAILY_BACKUP_VERIFIER
    assert "-files.tgz" not in DAILY_BACKUP_VERIFIER


def test_alpha_runtime_profile_includes_the_container_business_url():
    compact = "".join(ALPHA_RUNTIME_PROVISION.split())
    assert "'business_url':'http://dsherp-validation-backend-1:8000'" in compact


def test_manufacturing_fixture_creates_the_zero_finished_goods_bin_natively():
    assert "from erpnext.stock.utils import get_or_make_bin" in MANUFACTURING_PROVISION
    assert "get_or_make_bin(FINISHED_GOOD, warehouses['finished_goods'])" in MANUFACTURING_PROVISION


def test_daily_backup_verifier_diagnoses_a_compressed_backup_rather_than_a_bare_miss():
    """v16 writes -files.tar; a --compress backup writes the v15-era .tgz shape instead.

    The miss must name that cause, otherwise the operator only sees 'Missing or empty
    backup artifact' and cannot tell a wrong backup flag from a real backup failure.
    """
    assert "with_suffix('.tgz')" in DAILY_BACKUP_VERIFIER
    assert "--compress" in DAILY_BACKUP_VERIFIER
    diagnosis = DAILY_BACKUP_VERIFIER.index("with_suffix('.tgz')")
    fastfail = DAILY_BACKUP_VERIFIER.index("Missing or empty backup artifact")
    assert diagnosis < fastfail


def test_global_before_insert_hook_tolerates_meta_without_custom_during_migrate():
    """`bench migrate` imports DocType JSON through doc.insert(), so the "*" before_insert
    hook runs against an in-flight Meta that has no `custom` attribute. Reading it directly
    aborts the whole migrate with AttributeError; absent must simply mean "not custom".
    """
    assert "frappe.get_meta(doc.doctype).custom" not in CONFIGURATION_LOCKS
    assert "getattr(frappe.get_meta(doc.doctype), 'custom', 0)" in CONFIGURATION_LOCKS
