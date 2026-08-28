"""Actual platform DB, capabilities and ERP permissions; no model calls."""
import json
import uuid
from contextlib import contextmanager
from pathlib import Path
import httpx
from test_platform_identity import platform_client, platform_operator
import pytest

API='/api/method/dsherp_platform.agent_api.'

@pytest.fixture
def created_tasks():
    ids=[]
    yield ids
    with platform_operator() as operator:
        for task_id in ids:
            assert operator.delete('/api/resource/DS Agent Task/'+task_id).status_code==202

@contextmanager
def worker_client():
    p=json.loads(Path('.runtime/platform-users.json').read_text())['worker']
    with httpx.Client(base_url='http://127.0.0.1:18083',headers={'Host':'platform.localhost','Authorization':f"token {p['api_key']}:{p['api_secret']}"},trust_env=False,timeout=30) as c:
        yield c


def test_task_endpoint_requires_real_login():
    with httpx.Client(base_url='http://127.0.0.1:18083',headers={'Host':'platform.localhost'},trust_env=False) as c:
        assert c.get(API+'list_tasks',params={'enterprise':'alpha'}).status_code==403


def test_task_lifecycle_is_idempotent_scoped_and_records_actual_reads(created_tasks):
    with platform_client() as member, worker_client() as worker, platform_client('outsider') as outsider:
        assert worker.post(API+'worker_heartbeat').status_code==200
        args={'enterprise':'alpha','question':'查找测试物料','request_id':str(uuid.uuid4())}
        r=member.post(API+'submit_task',json=args)
        assert r.status_code==200, r.text
        task=r.json()['message']; task_id=task['id']; created_tasks.append(task_id)
        assert member.post(API+'submit_task',json=args).json()['message']['id']==task_id
        assert member.post(API+'submit_task',json={**args,'question':'不同问题'}).status_code!=200
        assert outsider.get(API+'get_task',params={'task_id':task_id}).status_code==403
        assert member.get('/api/resource/DS Agent Task').status_code==403
        assert member.post(API+'claim_task').status_code==403
        claim=worker.post(API+'claim_task').json()['message']; assert claim['task_id']==task_id
        assert worker.post(API+'claim_task').json()['message'] is None
        cap={'task_id':task_id,'capability':claim['capability']}
        assert member.post(API+'finish_task',json={**cap,'status':'Succeeded','answer':'伪造'}).status_code!=200
        assert member.post(API+'task_tool',json={**cap,'capability':'bad','tool':'erp_read_schema','arguments':{'doctype':'Item'}}).status_code==403
        assert member.post(API+'task_tool',json={**cap,'tool':'erp_read_schema','arguments':{'doctype':'Item','enterprise':'beta'}}).status_code!=200
        r=member.post(API+'task_tool',json={**cap,'tool':'erp_search_records','arguments':{'doctype':'Item','query':'DSHERP'}})
        assert r.status_code==200,r.text
        assert len(r.json()['message'])<=20
        r=member.post(API+'task_tool',json={**cap,'tool':'erp_read_record','arguments':{'doctype':'Item','name':'DSHERP-TEST-ITEM'}})
        assert r.status_code==200,r.text
        assert member.post(API+'finish_task',json={**cap,'status':'Succeeded','answer':'已读取测试物料'}).status_code==200
        result=member.get(API+'get_task',params={'task_id':task_id}).json()['message']
        assert result['status']=='Succeeded' and len(result['events'])==2
        assert all(e['status']=='Succeeded' for e in result['events'])
        assert 'capability' not in json.dumps(result) and 'api_secret' not in json.dumps(result)
        assert member.post(API+'task_tool',json={**cap,'tool':'erp_read_schema','arguments':{'doctype':'Item'}}).status_code==403
        assert task_id in {t['id'] for t in member.get(API+'list_tasks',params={'enterprise':'alpha'}).json()['message']}
        assert task_id not in {t['id'] for t in member.get(API+'list_tasks',params={'enterprise':'beta'}).json()['message']}


def test_capability_guest_scope_revocation_and_expiry(created_tasks):
    with platform_client() as member, worker_client() as worker, platform_operator() as operator, httpx.Client(base_url='http://127.0.0.1:18083',headers={'Host':'platform.localhost'},trust_env=False,timeout=30) as runtime:
        worker.post(API+'worker_heartbeat')
        def submit():
            r=member.post(API+'submit_task',json={'enterprise':'alpha','question':'查找测试物料','request_id':str(uuid.uuid4())})
            assert r.status_code==200,r.text
            created_tasks.append(r.json()['message']['id'])
            return r.json()['message']['id']
        first=submit();second=submit()
        claim=worker.post(API+'claim_task').json()['message'];assert claim['task_id']==first
        cap={'task_id':first,'capability':claim['capability']}
        tool={'tool':'erp_search_records','arguments':{'doctype':'Customer'}}
        assert runtime.post(API+'task_tool',json={**cap,**tool,'task_id':second}).status_code==403
        r=runtime.post(API+'task_tool',json={**cap,**tool});assert r.status_code==200,r.text
        assert 'DSHERP-TEST-OTHER-CUSTOMER' not in {x['name'] for x in r.json()['message']}
        memberships=operator.get('/api/resource/DS Membership',params={'filters':json.dumps({'enterprise':'alpha','platform_user':'member@example.invalid'})}).json()['data']
        path='/api/resource/DS Membership/'+memberships[0]['name']
        try:
            assert operator.put(path,json={'enabled':0}).status_code==200
            assert member.get(API+'get_task',params={'task_id':first}).status_code==403
            assert member.get(API+'list_tasks',params={'enterprise':'alpha'}).status_code==403
            assert runtime.post(API+'task_tool',json={**cap,**tool}).status_code==403
            assert runtime.post(API+'finish_task',json={**cap,'status':'Succeeded','answer':'旧结果'}).status_code==403
        finally:
            assert operator.put(path,json={'enabled':1}).status_code==200
        assert runtime.post(API+'task_tool',json={**cap,**tool}).status_code==403
        assert member.get(API+'get_task',params={'task_id':first}).status_code==403
        # Restore only the synthetic task fingerprint for the separate expiry check.
        binding=operator.get(path).json()['data']
        enterprise=operator.get('/api/resource/DS Enterprise/alpha').json()['data']
        import hashlib
        fingerprint=hashlib.sha256((binding['name']+'\0'+str(binding['modified'])+'\0'+str(enterprise['modified'])).encode()).hexdigest()
        for task_id in (first,second):
            assert operator.put('/api/resource/DS Agent Task/'+task_id,json={'binding_fingerprint':fingerprint}).status_code==200
        assert operator.put('/api/resource/DS Agent Task/'+first,json={'expires_at':'2020-01-01 00:00:00'}).status_code==200
        assert runtime.post(API+'task_tool',json={**cap,**tool}).status_code==403
        r=member.get(API+'get_task',params={'task_id':first});assert r.json()['message']['status']=='Failed'
        claim=worker.post(API+'claim_task').json()['message'];assert claim['task_id']==second
        assert runtime.post(API+'finish_task',json={'task_id':second,'capability':claim['capability'],'status':'Failed','error':'Synthetic test completed'}).status_code==200
        assert worker.get('/api/resource/DS Membership').status_code==403
        assert worker.get('/api/resource/DS Agent Task').status_code==403


def test_concurrent_claims_never_duplicate(created_tasks):
    from concurrent.futures import ThreadPoolExecutor
    with platform_client() as member, worker_client() as worker:
        worker.post(API+'worker_heartbeat')
        task=member.post(API+'submit_task',json={'enterprise':'beta','question':'查找测试物料','request_id':str(uuid.uuid4())}).json()['message']
        created_tasks.append(task['id'])
        def claim():
            with worker_client() as c:
                r=c.post(API+'claim_task'); assert r.status_code==200,r.text
                return r.json()['message']
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims=list(pool.map(lambda _:claim(),range(2)))
        actual=[c for c in claims if c is not None]
        assert len(actual)==1 and actual[0]['task_id']==task['id']
        assert worker.post(API+'finish_task',json={**actual[0],'status':'Failed','error':'Synthetic test completed'}).status_code==200


def test_revocation_during_tool_read_prevents_result_return():
    """Real concurrent database transactions; only network timing is stubbed."""
    import subprocess
    from test_platform_identity_revocation import CONCURRENT_REVOCATION
    script=CONCURRENT_REVOCATION.replace('from dsherp_platform.api import read_record','''from dsherp_platform.agent_api import task_tool, _fingerprint
from dsherp_platform.api import _binding
import hashlib, json, secrets
from frappe.utils import add_to_date, now_datetime
capability=secrets.token_urlsafe(32)
task=frappe.get_doc({'doctype':'DS Agent Task','enterprise':'alpha','question':'Concurrent task permission regression','status':'Running','events':'[]','binding_fingerprint':_fingerprint(*_binding('alpha')),'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),'expires_at':add_to_date(now_datetime(),seconds=180)}).insert(ignore_permissions=True)
frappe.db.commit()''').replace('            set_enabled(0)\n            return Response(erp_user)','            return Response(erp_user)').replace('        business_reads.append(url)','        set_enabled(0)\n        business_reads.append(url)').replace("read_record('alpha', 'Item', 'DSHERP-TEST-ITEM')","task_tool(task.name, capability, 'erp_read_record', {'doctype':'Item','name':'DSHERP-TEST-ITEM'})").replace("assert not business_reads, 'ERP business read occurred after membership revocation'","assert business_reads, 'Expected synchronized network result'\n    frappe.db.rollback()\n    saved=frappe.get_doc('DS Agent Task',task.name)\n    assert json.loads(saved.events)[-1]['status']=='Failed'").replace('    frappe.destroy()','    frappe.delete_doc(\'DS Agent Task\',task.name,ignore_permissions=True)\n    frappe.db.commit()\n    frappe.destroy()')
    r=subprocess.run(['docker','compose','-f','infra/compose.validation.yml','exec','-T','platform-backend','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=90)
    assert r.returncode==0,r.stderr


def test_absent_worker_returns_http_503():
    import subprocess
    script="""
import os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-platform.localhost');frappe.connect()
frappe.cache.delete_value('dsherp_agent_worker');frappe.destroy()
"""
    with platform_client() as member, worker_client() as worker:
        r=subprocess.run(['docker','compose','-f','infra/compose.validation.yml','exec','-T','platform-backend','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
        assert r.returncode==0,r.stderr
        try:
            response=member.post(API+'submit_task',json={'enterprise':'alpha','question':'Agent服务不可用测试','request_id':str(uuid.uuid4())})
            assert response.status_code==503,response.text
        finally:
            assert worker.post(API+'worker_heartbeat').status_code==200


def test_search_finds_permitted_item_title(created_tasks):
    with platform_client() as member, worker_client() as worker:
        worker.post(API+'worker_heartbeat')
        task=member.post(API+'submit_task',json={'enterprise':'beta','question':'按中文名称查找测试物料','request_id':str(uuid.uuid4())}).json()['message']
        created_tasks.append(task['id'])
        claim=worker.post(API+'claim_task').json()['message']
        assert claim['task_id']==task['id']
        response=worker.post(API+'task_tool',json={**claim,'tool':'erp_search_records','arguments':{'doctype':'Item','query':'乙企业专属'}})
        assert response.status_code==200,response.text
        assert {'name':'DSHERP-BETA-ITEM'} in response.json()['message']
        assert worker.post(API+'finish_task',json={**claim,'status':'Succeeded','answer':'已找到物料'}).status_code==200


def test_revoked_member_task_can_finish_failed_but_not_succeeded(created_tasks):
    with platform_client() as member, worker_client() as worker, platform_operator() as operator:
        worker.post(API+'worker_heartbeat')
        task=member.post(API+'submit_task',json={'enterprise':'alpha','question':'撤销后终结任务测试','request_id':str(uuid.uuid4())}).json()['message']
        created_tasks.append(task['id'])
        claim=worker.post(API+'claim_task').json()['message']; assert claim['task_id']==task['id']
        memberships=operator.get('/api/resource/DS Membership',params={'filters':json.dumps({'enterprise':'alpha','platform_user':'member@example.invalid'})}).json()['data']
        path='/api/resource/DS Membership/'+memberships[0]['name']
        try:
            assert operator.put(path,json={'enabled':0}).status_code==200
            assert worker.post(API+'finish_task',json={**claim,'status':'Succeeded','answer':'无权成功'}).status_code==403
            response=worker.post(API+'finish_task',json={**claim,'status':'Failed','error':'成员权限已撤销'})
            assert response.status_code==200,response.text
            saved=operator.get('/api/resource/DS Agent Task/'+task['id']).json()['data']
            assert saved['status']=='Failed' and not saved['capability_hash']
            assert worker.post(API+'finish_task',json={**claim,'status':'Failed','error':'重复终结'}).status_code==403
        finally:
            assert operator.put(path,json={'enabled':1}).status_code==200
