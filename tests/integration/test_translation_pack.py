import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", "infra/compose.validation.yml"]


def run_alpha(script, timeout=30):
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-"],
        cwd=ROOT,
        input=script,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def translate_alpha(source, context=""):
    script = f'''
import json, os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost')
frappe.connect()
try:
    print(json.dumps(frappe._({source!r},lang='zh',context={context!r}),ensure_ascii=False))
finally:
    frappe.destroy()
'''
    return json.loads(run_alpha(script))


def clear_alpha_translation_cache():
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "backend", "bench", "--site", "dsherp-validation.localhost", "clear-cache"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_alpha_uses_dsherp_pack_and_enterprise_translation_wins():
    script = r'''
import json, os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost')
frappe.connect()
frappe.set_user('Administrator')
filters={'language':'zh','source_text':'Trial Balance','context':['in',['',None]]}
existing=frappe.get_all('Translation',filters=filters,pluck='name')
assert not existing, 'alpha already has an enterprise Trial Balance translation'
doc=None
try:
    platform={
        'Trial Balance':frappe._('Trial Balance',lang='zh'),
        'Purchase Receipt':frappe._('Purchase Receipt',lang='zh'),
        'Journal Entry':frappe._('Journal Entry',lang='zh'),
        'General Ledger':frappe._('General Ledger',lang='zh'),
        'General Ledger:Warehouse':frappe._('General Ledger',lang='zh',context='Warehouse'),
    }
    doc=frappe.get_doc({'doctype':'Translation','language':'zh','source_text':'Trial Balance','translated_text':'企业科目余额表'})
    doc.insert()
    frappe.db.commit()
    enterprise=frappe._('Trial Balance',lang='zh')
    print(json.dumps({'apps':frappe.get_installed_apps(_ensure_on_bench=True),'platform':platform,'enterprise':enterprise,'translation_custom':bool(frappe.get_meta('Translation').custom)},ensure_ascii=False))
finally:
    if doc and frappe.db.exists('Translation',doc.name):
        frappe.delete_doc('Translation',doc.name,ignore_permissions=True)
        frappe.db.commit()
    frappe.translate.clear_cache()
    frappe.destroy()
'''
    state = json.loads(run_alpha(script))

    assert state == {
        "apps": ["frappe", "erpnext", "dsherp_bridge"],
        "platform": {
            "Trial Balance": "科目余额表",
            "Purchase Receipt": "采购入库单",
            "Journal Entry": "记账凭证",
            "General Ledger": "总账",
            "General Ledger:Warehouse": "总账",
        },
        "enterprise": "企业科目余额表",
        "translation_custom": False,
    }


def test_fixed_upstream_translation_files_are_unchanged():
    script = r'''
import hashlib, json
paths={
    'frappe':'/home/frappe/frappe-bench/apps/frappe/frappe/translations/zh.csv',
    'erpnext':'/home/frappe/frappe-bench/apps/erpnext/erpnext/translations/zh.csv',
}
print(json.dumps({name:hashlib.sha256(open(path,'rb').read()).hexdigest() for name,path in paths.items()},sort_keys=True))
'''

    assert json.loads(run_alpha(script)) == {
        "frappe": "9e9dbd64f0965853e6be131b6335efdbba906a1a8e2908bfdd9f8888a2f0fdc8",
        "erpnext": "233ab506626683446fb137dd8aab3fb6c28f78b1b6a55d803bc3cd537af00be9",
    }


def test_clear_cache_activates_changed_app_translation_and_restores_cleanly():
    translation_file = ROOT / "frappe_app/dsherp_bridge/translations/zh.csv"
    original = translation_file.read_bytes()
    current = "Trial Balance,科目余额表,\n".encode()
    temporary = "Trial Balance,临时科目余额表,\n".encode()
    assert original.count(current) == 1
    assert translate_alpha("Trial Balance") == "科目余额表"

    translation_file.write_bytes(original.replace(current, temporary))
    try:
        assert translate_alpha("Trial Balance") == "科目余额表"
        clear_alpha_translation_cache()
        assert translate_alpha("Trial Balance") == "临时科目余额表"
    finally:
        translation_file.write_bytes(original)
        clear_alpha_translation_cache()

    assert translation_file.read_bytes() == original
    assert translate_alpha("Trial Balance") == "科目余额表"
