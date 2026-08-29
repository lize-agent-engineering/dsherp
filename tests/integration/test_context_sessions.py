"""Business Site persistence and real Frappe permissions; never calls a model."""
import json
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest

API='/api/method/dsherp_bridge.context_api.'

@pytest.fixture
def clients():
    profiles=json.loads(Path('.runtime/erp-users.json').read_text())
    opened=[]
    for actor in ('reader','denied'):
        p=profiles[actor]
        opened.append(httpx.Client(base_url=p['base_url'],headers={'X-Frappe-Site-Name':p['site'],'Authorization':'token '+p['api_key']+':'+p['api_secret']},trust_env=False,timeout=20))
    yield opened
    for client in opened:client.close()

@pytest.fixture
def created():
    names=[]
    yield names
    if names:
        script="""
import os,json,frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dsherp-validation.localhost');frappe.connect()
frappe.set_user('Administrator')
for name in NAMES:
    for run in frappe.get_all('DS Model Run',filters={'conversation':name},pluck='name'):
        frappe.delete_doc('DS Model Run',run,ignore_permissions=True)
    frappe.delete_doc('DS Conversation',name,ignore_permissions=True)
frappe.db.commit();frappe.destroy()
""".replace('NAMES',repr(names))
        result=subprocess.run(['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],input=script,text=True,capture_output=True,timeout=30)
        assert result.returncode==0,result.stderr

def params():
    return {'session_id':None,'question':'查询当前物料','request_id':uuid.uuid4().hex,'context':{'schema_version':1,'page_type':'form','route':['Form','Item','DSHERP-TEST-ITEM'],'doctype':'Item','name':'DSHERP-TEST-ITEM','version':None,'dirty':False}}

def test_selected_domain_is_persisted_and_bound_to_request_id(clients,created):
    reader,_=clients;args={**params(),'domain':'operation'}
    data=send(reader,created,args)
    assert data['messages'][0]['domain']=='operation'
    assert reader.post(API+'send_message',json={**args,'domain':'query'}).status_code==417
    assert reader.post(API+'send_message',json={**params(),'domain':'admin'}).status_code==417

def test_explicit_child_columns_are_context_only_and_reject_foreign_fields(clients,created):
    reader,_=clients;args=params()
    args['context'].update(dirty=True,unsaved={'uoms':[{'name':'new-uom-conversion-detail-1','uom':'DSHERP-TEST-UNIT','conversion_factor':2}]})
    data=send(reader,created,args)
    assert data['messages'][0]['context']['unsaved']==args['context']['unsaved']
    reader.post(API+'cancel_run',json={'session_id':data['id'],'run_id':data['active_run'],'request_id':uuid.uuid4().hex}).raise_for_status()
    for row in ({'name':'foreign-row','conversion_factor':2},{'name':'new-uom-conversion-detail-1','parent':'other-item'}, {'name':'new-uom-conversion-detail-1','conversion_factor':{'nested':'forged'}}):
        invalid=params();invalid['context']['unsaved']={'uoms':[row]}
        response=reader.post(API+'send_message',json=invalid)
        assert response.status_code in (403,417),response.text

def send(client,created,args=None):
    r=client.post(API+'send_message',json=args or params())
    assert r.status_code==200,r.text
    data=r.json()['message'];created.append(data['id']);return data

def test_persisted_session_is_owned_idempotent_and_refresh_does_not_run(clients,created):
    reader,denied=clients;args=params();data=send(reader,created,args)
    repeated=reader.post(API+'send_message',json=args)
    assert repeated.status_code==200,repeated.text
    assert repeated.json()['message']['id']==data['id']
    loaded=reader.get(API+'get_session',params={'session_id':data['id']})
    assert loaded.status_code==200,loaded.text
    assert len(loaded.json()['message']['messages'])==1
    assert loaded.json()['message']['messages'][0]['status']=='Queued'
    assert loaded.json()['message']['messages'][0]['context']['version']
    assert denied.get(API+'get_session',params={'session_id':data['id']}).status_code==403
    assert reader.get('/api/resource/DS Model Run').status_code==403
    assert reader.post(API+'send_message',json={**args,'question':'篡改问题'}).status_code==417

def test_native_record_and_field_permissions_apply_before_queueing(clients):
    reader,denied=clients;args=params()
    assert denied.post(API+'send_message',json=args).status_code==403
    args['context'].update(doctype='Customer',name='DSHERP-TEST-OTHER-CUSTOMER',route=['Form','Customer','DSHERP-TEST-OTHER-CUSTOMER'])
    assert reader.post(API+'send_message',json=args).status_code==403
    args['context'].update(name='DSHERP-TEST-CUSTOMER',route=['Form','Customer','DSHERP-TEST-CUSTOMER'],unsaved={'custom_dsherp_restricted':'secret'})
    assert reader.post(API+'send_message',json=args).status_code==403

def test_cancel_queued_run_and_continue_same_conversation(clients,created):
    reader,_=clients;data=send(reader,created)
    cancellation={'session_id':data['id'],'run_id':data['active_run'],'request_id':uuid.uuid4().hex}
    cancelled=reader.post(API+'cancel_run',json=cancellation)
    assert cancelled.status_code==200,cancelled.text
    assert cancelled.json()['message']['active_run'] is None
    assert cancelled.json()['message']['messages'][0]['status']=='Cancelled'
    again=reader.post(API+'cancel_run',json=cancellation)
    assert again.status_code==200,again.text
    args={**params(),'session_id':data['id']}
    next_run=reader.post(API+'send_message',json=args)
    assert next_run.status_code==200,next_run.text
    assert len(next_run.json()['message']['messages'])==2

def test_active_run_prevents_duplicate_parallel_work(clients,created):
    reader,_=clients;data=send(reader,created)
    response=reader.post(API+'send_message',json={**params(),'session_id':data['id']})
    assert response.status_code==409,response.text

def test_forged_context_identity_fails_but_stale_page_can_query_without_refresh(clients,created):
    reader,_=clients;args=params();args['context']['user']='Administrator'
    assert reader.post(API+'send_message',json=args).status_code==417
    del args['context']['user'];args['context']['version']='2000-01-01 00:00:00'
    args['context']['server_version']='forged-server-version'
    data=send(reader,created,args)
    snapshot=data['messages'][0]['context']
    assert snapshot['version']=='2000-01-01 00:00:00'
    assert snapshot['server_version'] not in ('forged-server-version',snapshot['version'])
    invalid=params();invalid['context']={'schema_version':1,'page_type':'list','route':['List','Item'],'doctype':'Item','server_version':'forged'}
    response=reader.post(API+'send_message',json=invalid)
    if response.status_code==200:created.append(response.json()['message']['id'])
    assert response.status_code==417

def test_sidebar_lists_only_ten_recent_unarchived_sessions_with_has_more(clients,created):
    reader,_=clients
    for index in range(12):
        data=send(reader,created,{**params(),'question':f'近期会话 {index:02d}'})
        reader.post(API+'cancel_run',json={'session_id':data['id'],'run_id':data['active_run'],'request_id':uuid.uuid4().hex}).raise_for_status()
    response=reader.get(API+'list_sessions')
    assert response.status_code==200,response.text
    result=response.json()['message']
    assert len(result['items'])==10
    assert result['has_more'] is True
    assert all(item['archived'] is False for item in result['items'])

def test_search_rename_archive_and_restore_session_lifecycle(clients,created):
    reader,_=clients
    first=send(reader,created,{**params(),'question':'今天的物料核对'})
    reader.post(API+'cancel_run',json={'session_id':first['id'],'run_id':first['active_run'],'request_id':uuid.uuid4().hex}).raise_for_status()
    second=send(reader,created,{**params(),'question':'更早的客户核对'})

    active_archive=reader.post(API+'archive_session',json={'session_id':second['id']})
    assert active_archive.status_code==409,active_archive.text

    reader.post(API+'cancel_run',json={'session_id':second['id'],'run_id':second['active_run'],'request_id':uuid.uuid4().hex}).raise_for_status()
    renamed=reader.post(API+'rename_session',json={'session_id':first['id'],'title':'物料核对结果'})
    assert renamed.status_code==200,renamed.text
    assert renamed.json()['message']['title']=='物料核对结果'

    archived=reader.post(API+'archive_session',json={'session_id':first['id']})
    assert archived.status_code==200,archived.text
    assert archived.json()['message']['archived'] is True
    assert reader.post(API+'send_message',json={**params(),'session_id':first['id']}).status_code==409

    hidden=reader.get(API+'search_sessions',params={'query':'物料核对','page':1,'archived':0})
    assert hidden.status_code==200,hidden.text
    assert hidden.json()['message']['items']==[]
    found=reader.get(API+'search_sessions',params={'query':'物料核对','page':1,'archived':1})
    assert found.status_code==200,found.text
    assert found.json()['message']['items'][0]['id']==first['id']

    restored=reader.post(API+'restore_session',json={'session_id':first['id']})
    assert restored.status_code==200,restored.text
    assert restored.json()['message']['archived'] is False

def test_session_management_never_exposes_other_users_sessions(clients,created):
    reader,denied=clients
    data=send(reader,created)
    result=denied.get(API+'search_sessions',params={'query':'','page':1,'archived':0})
    assert result.status_code==200,result.text
    assert result.json()['message']['items']==[]
    assert denied.post(API+'rename_session',json={'session_id':data['id'],'title':'越权'}).status_code==403
    assert denied.post(API+'archive_session',json={'session_id':data['id']}).status_code==403

def test_workbench_summary_endpoints_are_paginated_and_never_return_frozen_payloads(clients):
    reader,denied=clients
    for method in ('list_pending','list_execution_records','list_configuration_records'):
        response=reader.get(API+method,params={'page':1})
        assert response.status_code==200,response.text
        result=response.json()['message']
        assert result['page']==1
        assert len(result['items'])<=20
        assert all('payload' not in item and 'result' not in item and 'steps' not in item for item in result['items'])
        other=denied.get(API+method,params={'page':1})
        assert other.status_code==200,other.text
        assert all(item.get('session_id')!=entry.get('session_id') for item in other.json()['message']['items'] for entry in result['items'])
