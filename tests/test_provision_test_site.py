"""A throwaway test Site is created on an existing bench: never the default site, never on a
bench that lacks the apps, never twice; passwords stay off argv."""
import ast
import json
from pathlib import Path

from infra import provision_test_site as p


def test_targets_name_the_two_test_sites_and_their_apps():
    assert p.TARGETS['bridge']['site'] == 'dsherp-test.localhost'
    assert p.TARGETS['bridge']['apps'] == ('erpnext', 'dsherp_bridge') and p.TARGETS['bridge']['abbr'] == 'DNT'
    assert p.TARGETS['platform']['site'] == 'dsherp-platform-test.localhost'
    assert p.TARGETS['platform']['apps'] == ('dsherp_platform',) and p.TARGETS['platform']['company'] is None
    assert p.SITE_CONFIG['allow_tests'] == 1, 'bench run-tests refuses a Site without it'


def test_preflight_refuses_an_existing_site_a_bench_without_the_apps_or_without_a_database(tmp_path):
    sites = tmp_path / 'sites'
    sites.mkdir()
    (sites / 'apps.txt').write_text('frappe\nerpnext\n')
    (sites / 'common_site_config.json').write_text('{}')
    problems = p.preflight(sites, p.TARGETS['bridge'])
    assert any('dsherp_bridge' in problem for problem in problems)
    assert any('db_host' in problem for problem in problems)
    (sites / 'apps.txt').write_text('frappe\nerpnext\ndsherp_bridge\n')
    (sites / 'common_site_config.json').write_text(json.dumps({'db_host': 'db'}))
    assert p.preflight(sites, p.TARGETS['bridge']) == []
    (sites / 'dsherp-test.localhost').mkdir()
    assert p.preflight(sites, p.TARGETS['bridge']) == ['dsherp-test.localhost already exists; inspect before retrying']


def test_no_password_ever_reaches_a_subprocess_argv():
    """bench logs its own argv. The two passwords are read from /run/secrets and stay Python
    values passed to frappe.installer._new_site in this same process."""
    tree = ast.parse(Path(p.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, 'attr', '') == 'run':
            for argument in ast.walk(node):
                if isinstance(argument, ast.Name):
                    assert 'password' not in argument.id, ast.unparse(node)
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    assert '--db-root-password' not in argument.value, ast.unparse(node)
                    assert '--admin-password' not in argument.value, ast.unparse(node)


def test_the_bench_layout_is_read_never_rewritten():
    """The two older provisioners overwrite sites/apps.txt and common_site_config.json. This
    one runs on a bench that is already serving four Sites, so it may only read them."""
    source = Path(p.__file__).read_text()
    for name in ("apps.txt", "common_site_config.json"):
        for line in source.splitlines():
            if name in line:
                assert 'write_text' not in line and 'set-common-config' not in line, line
    assert '--set-default' not in source
