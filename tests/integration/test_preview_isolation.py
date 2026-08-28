import json
import subprocess
import re
import httpx


def test_preview_has_internal_only_backend_and_separate_native_entry():
    inspected=json.loads(subprocess.check_output(['docker','inspect','dsherp-validation-beta-backend-1']))[0]
    assert set(inspected['NetworkSettings']['Networks'])=={'dsherp-validation_validation'}
    with httpx.Client(base_url='http://127.0.0.1:18085',trust_env=False,timeout=15) as client:
        page=client.get('/login',headers={'Host':'preview.localhost:18085'})
        assert page.status_code==200
        assets=re.findall(r'(?:src|href)="(/assets/[^"?]+\.(?:css|js))',page.text)
        assert assets
        for asset in assets:assert client.get(asset,headers={'Host':'preview.localhost:18085'}).status_code==200
        assert client.get('/login',headers={'Host':'preview.localhost:18085','Origin':'https://foreign.invalid'}).status_code==403
        assert client.get('/login',headers={'Host':'dsherp-validation.localhost:18085'}).status_code==421
        assert client.get('/api/method/ping',headers={'Host':'preview.localhost:18085'}).json()['message']=='pong'


def test_preview_native_configuration_rejects_external_effects():
    script=r'''
import os,frappe,socket
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-beta.localhost');frappe.connect()
import frappe.sessions
try:
    assert frappe.conf.dsherp_preview==1
    assert frappe.conf.mute_emails==1 and frappe.conf.disable_scheduler==1 and frappe.conf.pause_scheduler==1
    try:
        connection=socket.create_connection(('1.1.1.1',443),timeout=2)
    except OSError:pass
    else:connection.close();raise AssertionError('preview has external route')
    frappe.set_user('dsherp-preview@example.invalid')
    frappe.local.request=None
    assert frappe.sessions.get()['disable_async']==1
    frappe.set_user('Administrator')
    try:
        frappe.get_doc({'doctype':'Webhook','name':'DS Synthetic Preview Webhook','webhook_doctype':'Item','webhook_docevent':'on_update','request_url':'https://example.invalid/','request_method':'POST'}).insert()
        raise AssertionError('preview webhook accepted')
    except frappe.ValidationError as error:assert '隔离预览不允许外部副作用' in str(error)
finally:frappe.db.rollback();frappe.destroy()
'''
    result=subprocess.run(['docker','exec','-i','dsherp-validation-beta-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
