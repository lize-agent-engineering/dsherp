"""Native DocType policies gate tools and rotate pending authorization."""
import asyncio
import json
import subprocess

from dsherp import context_mcp


GOVERNANCE_TARGETS = ('DS Doctype Policy', 'DS Doctype Policy Route')


def _governance_tool_cases():
    cases = []
    for domain in ('query', 'operation', 'configuration'):
        server = context_mcp.create_server(None, 'registry-run', 'registry-capability', domain=domain)
        for tool in asyncio.run(server.list_tools()):
            schema = tool.inputSchema
            required = schema['required']
            properties = schema['properties']
            if tool.name == 'erp_propose_configuration':
                if (domain != 'configuration' or set(required) != {'package'}
                        or properties.get('package', {}).get('type') != 'object'):
                    raise AssertionError(f'已注册配置提案工具 {tool.name} 的 schema 已变化')
                for target in GOVERNANCE_TARGETS:
                    arguments = {'package': {'version': 1, 'doctypes': [], 'extensions': [
                        {'doctype': target, 'fields': [
                            {'fieldname': 'ds_governance_probe', 'label': 'Governance Probe',
                             'fieldtype': 'Data',
                             'insert_after': 'target_doctype' if target == 'DS Doctype Policy' else 'route_name'}
                        ]}
                    ], 'workflows': []}}
                    cases.append({'domain': domain, 'owner': 'control', 'tool': tool.name,
                                  'target': target, 'arguments': arguments, 'source': 'configuration'})
                continue
            target_parameter=('doctype' if 'doctype' in required else
                              'source_doctype' if 'source_doctype' in required else None)
            if not target_parameter:
                continue
            actions = [None]
            for field in required:
                if field == 'action':
                    actions = properties[field].get('enum', [])
                    if not actions:
                        raise AssertionError(f'已注册工具 {tool.name} 未声明状态动作枚举')
            source = 'record' if tool.name.startswith('erp_propose_') and ({'name','source_name'}&set(required)) else (
                'schema' if tool.name.startswith('erp_propose_') else None
            )
            owner = 'control' if domain == 'configuration' else 'business'
            for target in GOVERNANCE_TARGETS:
                arguments = {target_parameter: target}
                for field in required:
                    if field == target_parameter:
                        continue
                    if field == 'values':
                        arguments[field] = {}
                    elif field != 'action':
                        arguments[field] = 'governance-source-version'
                for action in actions:
                    cases.append({'domain': domain, 'owner': owner, 'tool': tool.name, 'target': target,
                                  'arguments': {**arguments, **({'action': action} if action is not None else {})},
                                  'source': source})
    if not cases:
        raise AssertionError('未发现公开业务 MCP 工具')
    return cases


def test_governance_doctype_has_no_business_tool_access():
    cases = _governance_tool_cases()
    assert {
        (case['domain'], case['target']) for case in cases if case['tool'] == 'erp_search_records'
    } == {
        (domain, target) for domain in ('query', 'operation') for target in GOVERNANCE_TARGETS
    }
    assert {(case['domain'],case['target']) for case in cases if case['tool']=='erp_propose_make'}=={
        ('operation',target) for target in GOVERNANCE_TARGETS
    }
    assert len(cases) == 28
    assert {(case['tool'], case['target']) for case in cases if case['domain'] == 'configuration'} == {
        ('erp_read_configuration', target) for target in GOVERNANCE_TARGETS
    } | {('erp_propose_configuration', target) for target in GOVERNANCE_TARGETS}
    script = r'''
import hashlib,json,os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_execution import run_tool
from dsherp_bridge.context_permissions import run_revision

cases=json.loads(__CASES__)
targets=('DS Doctype Policy','DS Doctype Policy Route');business='governance-zero-access-'+uuid.uuid4().hex+'@example.invalid'
control='governance-control-access-'+uuid.uuid4().hex+'@example.invalid'
conversations={};run_names=[];denied=[]
probe_route={'route_name':'governance_read_probe','method_path':'dsherp_bridge.synthetic.governance_read_probe','target_doctype':'Item'}
governance_name=None;routes_before=None
try:
    frappe.set_user('Administrator')
    for target in targets:assert not frappe.db.exists('DS Doctype Policy',{'target_doctype':target})
    governance_name=frappe.db.get_value('DS Doctype Policy',{},'name')
    assert governance_name,'alpha policy fixture is required for proposal argument shape'
    routes_before=frappe.get_all('DS Doctype Policy Route',filters={
        'parent':governance_name,'parenttype':'DS Doctype Policy','parentfield':'routes'
    },fields=['route_name','method_path','target_doctype'],order_by='idx asc,name asc')
    frappe.get_doc({'doctype':'User','email':business,'first_name':'Synthetic governance zero access','enabled':1,
                    'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.get_doc({'doctype':'User','email':control,'first_name':'Synthetic governance control access','enabled':1,
                    'send_welcome_email':0,'roles':[{'role':'System Manager'}]}).insert()
    policy=frappe.get_doc('DS Doctype Policy',governance_name)
    policy.append('routes',probe_route)
    policy.change_reason='集成测试 '+frappe.generate_hash(length=8);policy.save()
    frappe.set_user(control)
    policy=frappe.get_doc('DS Doctype Policy',governance_name)
    policy.check_permission('read')
    policy.apply_fieldlevel_read_permissions()
    assert 'routes' in policy.as_dict()
    assert any(
        row.get('route_name')=='governance_read_probe'
        and row.get('method_path')=='dsherp_bridge.synthetic.governance_read_probe'
        and row.get('target_doctype')=='Item'
        for row in policy.as_dict()['routes']
    )
    route_fields=set(frappe.get_meta('DS Doctype Policy Route').get_permitted_fieldnames(
        parenttype='DS Doctype Policy',user=control,permission_type='read'
    ))
    assert {'route_name','method_path','target_doctype'}<=route_fields,route_fields
    frappe.set_user(business)
    try:
        frappe.get_doc('DS Doctype Policy',governance_name).check_permission('read')
        raise AssertionError('business user read governance policy directly')
    except frappe.PermissionError:
        pass
    for key,owner in {'business':business,'control':control}.items():
        frappe.set_user(owner)
        conversations[key]=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic governance zero access '+key}).insert(ignore_permissions=True)

    def new_run(case,sources):
        capability=uuid.uuid4().hex
        owner={'business':business,'control':control}[case['owner']]
        frappe.set_user(owner)
        run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversations[case['owner']].name,'domain':case['domain'],
            'status':'Running','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
            'permission_revision':run_revision(owner,case['domain']),
            'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
            'expires_at':add_to_date(now_datetime(),minutes=3),'sources':json.dumps(sources)}).insert(ignore_permissions=True)
        run_names.append(run.name)
        return {'run_id':run.name,'capability':capability}

    for case in cases:
        target=case['target']
        arguments=case['arguments']
        if arguments.get('name')=='governance-source-version':arguments['name']=governance_name
        if arguments.get('source_name')=='governance-source-version':arguments['source_name']=governance_name
        if case['source']=='record':
            version=arguments.get('version') or arguments.get('source_version')
            sources=[{'tool':'erp_read_record','arguments':{'doctype':target,'name':governance_name},'fields':[],
                      'records':[],'record_versions':{governance_name:version}}]
        elif case['source']=='schema':
            sources=[{'tool':'erp_read_schema','arguments':{'doctype':target},'fields':[],'records':[],
                      'schema_version':arguments['version']}]
        elif case['source']=='configuration':
            sources=[{'tool':'erp_read_configuration','arguments':{'doctype':target},'modules':[],'roles':[],
                      'exists':True,'version':'governance-source-version',
                      'configuration_revision':'governance-source-revision'}]
        else:sources=[]
        cap=new_run(case,sources)
        frappe.set_user('Guest')
        try:
            run_tool(**cap,tool=case['tool'],arguments=arguments)
            raise AssertionError('governance tool access allowed: '+case['domain']+'/'+case['tool']+'/'+target)
        except frappe.PermissionError as error:
            assert str(error)=='缺少 DS DocType 策略：'+target,error
            denied.append((case['domain'],case['tool'],target,arguments.get('action')))
        finally:
            frappe.set_user({'business':business,'control':control}[case['owner']])
        assert frappe.db.get_value('DS Model Run',cap['run_id'],'sources')==json.dumps(sources)
        proposal_ids=frappe.get_all('DS Operation Proposal',filters={'conversation':conversations[case['owner']].name},pluck='name')
        assert proposal_ids==[]
        execution_ids=(frappe.get_all('DS Execution Record',filters={'proposal':['in',proposal_ids]},pluck='name')
                       if proposal_ids else [])
        assert execution_ids==[]
        configuration_bundle_ids=frappe.get_all('DS Configuration Bundle',filters={'conversation':conversations[case['owner']].name},pluck='name')
        assert configuration_bundle_ids==[]
    assert set(denied)=={(case['domain'],case['tool'],case['target'],case['arguments'].get('action')) for case in cases}
    other_case={'domain':'configuration','owner':'control'}
    cap=new_run(other_case,[]);frappe.set_user('Guest')
    other=run_tool(**cap,tool='erp_read_configuration',arguments={'doctype':'Item'})
    assert other['doctype']=='Item' and other['exists']
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    assert not frappe.db.exists('User',business) and not frappe.db.exists('User',control)
    assert all(not frappe.db.exists('DS Conversation',conversation.name) for conversation in conversations.values())
    assert all(not frappe.db.exists('DS Model Run',name) for name in run_names)
    frappe.destroy()
    if governance_name is not None and routes_before is not None:
        os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
        try:
            frappe.set_user('Administrator')
            routes_after=frappe.get_all('DS Doctype Policy Route',filters={
                'parent':governance_name,'parenttype':'DS Doctype Policy','parentfield':'routes'
            },fields=['route_name','method_path','target_doctype'],order_by='idx asc,name asc')
            assert routes_after==routes_before,(routes_before,routes_after)
        finally:
            frappe.destroy()
'''.replace('__CASES__', repr(json.dumps(cases, ensure_ascii=False, sort_keys=True)))
    result = subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=40,
    )
    assert result.returncode == 0, result.stderr


def test_new_supplier_policy_controls_real_context_read_and_create_proposal():
    script = r'''
import hashlib,json,os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_execution import run_tool
from dsherp_bridge.context_permissions import revision

tag=uuid.uuid4().hex;actor='supplier-policy-'+tag+'@example.invalid'
try:
    frappe.set_user('Administrator')
    meta=frappe.get_meta('Supplier')
    assert not meta.issingle and not meta.is_submittable and meta.title_field=='supplier_name'
    native=frappe.db.get_value('DocPerm',{'parent':'Supplier','role':'Purchase Master Manager'},
        ['read','write','create'],as_dict=True)
    assert native and native.read and native.write and native.create,native
    supplier=frappe.get_doc('Supplier','DSHERP 制造测试合成供应商')
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic Supplier policy','enabled':1,
        'send_welcome_email':0,'roles':[{'role':'Purchase Master Manager'}]}).insert()

    existing=frappe.get_doc('DS Doctype Policy','Supplier')
    assert existing.enabled and existing.allow_read and existing.allow_create
    frappe.delete_doc('DS Doctype Policy','Supplier')

    def new_run():
        capability=uuid.uuid4().hex
        run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'operation',
            'status':'Running','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
            'permission_revision':revision(actor),
            'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
            'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]'}).insert(ignore_permissions=True)
        return {'run_id':run.name,'capability':capability}

    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic Supplier policy'}).insert(ignore_permissions=True)
    missing=new_run();frappe.set_user('Guest')
    try:
        run_tool(**missing,tool='erp_read_record',arguments={'doctype':'Supplier','name':supplier.name})
        raise AssertionError('missing policy allowed Supplier read')
    except frappe.PermissionError as error:
        assert '缺少 DS DocType 策略' in str(error),error

    frappe.set_user('Administrator')
    policy=frappe.get_doc({'doctype':'DS Doctype Policy','change_reason':'集成测试 '+frappe.generate_hash(length=8),'target_doctype':'Supplier','enabled':0,
        'allow_read':1,'allow_create':1,'allow_update':0,'allow_submit':0,'allow_cancel':0,
        'allow_fill':0,'company_scope':None,'routes':[]}).insert()
    frappe.set_user(actor);disabled=new_run();frappe.set_user('Guest')
    try:
        run_tool(**disabled,tool='erp_read_record',arguments={'doctype':'Supplier','name':supplier.name})
        raise AssertionError('disabled policy allowed Supplier read')
    except frappe.PermissionError as error:
        assert 'DS DocType 策略未启用' in str(error),error

    frappe.set_user('Administrator');policy.enabled=1;policy.allow_create=0;policy.change_reason='集成测试 '+frappe.generate_hash(length=8);policy.save()
    frappe.set_user(actor);denied=new_run();frappe.set_user('Guest')
    record=run_tool(**denied,tool='erp_read_record',arguments={'doctype':'Supplier','name':supplier.name})
    assert record['name']==supplier.name and record['doctype']=='Supplier'
    schema=run_tool(**denied,tool='erp_read_schema',arguments={'doctype':'Supplier'})
    values={'supplier_name':'DSHERP 瞬时策略供应商 '+tag,
        'supplier_group':supplier.supplier_group,'supplier_type':supplier.supplier_type}
    create={'doctype':'Supplier','values':values,'version':str(schema['modified'])}
    try:
        run_tool(**denied,tool='erp_propose_create',arguments=create)
        raise AssertionError('allow_create=0 accepted Supplier create proposal')
    except frappe.PermissionError as error:
        assert '未允许 Supplier 的 create 操作' in str(error),error

    frappe.set_user('Administrator');policy.allow_create=1;policy.change_reason='集成测试 '+frappe.generate_hash(length=8);policy.save()
    frappe.set_user(actor);allowed=new_run();frappe.set_user('Guest')
    schema=run_tool(**allowed,tool='erp_read_schema',arguments={'doctype':'Supplier'})
    create['version']=str(schema['modified'])
    before=frappe.db.count('Supplier')
    proposal=run_tool(**allowed,tool='erp_propose_create',arguments=create)
    assert proposal['doctype']=='Supplier' and proposal['action']=='create' and proposal['status']=='Pending'
    assert frappe.db.count('Supplier')==before
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result = subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=40,
    )
    assert result.returncode == 0, result.stderr


def test_boot_context_doctypes_derive_from_policy_and_stale_snapshot_cannot_authorize():
    script = r'''
import os,uuid,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.boot import boot_session
from dsherp_bridge.context_api import _context
assert 'dsherp_bridge.boot.boot_session' in frappe.get_hooks('boot_session')

actor='context-policy-'+uuid.uuid4().hex+'@example.invalid'
try:
    frappe.set_user('Administrator')
    existing=frappe.get_doc('DS Doctype Policy','Supplier')
    assert existing.enabled and existing.allow_read
    frappe.delete_doc('DS Doctype Policy','Supplier')
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Context policy','enabled':1,
        'send_welcome_email':0,'roles':[{'role':'Purchase Master Manager'}]}).insert()
    frappe.set_user(actor);before=frappe._dict();boot_session(before)
    assert 'Supplier' not in before.dsherp_context_doctypes
    assert 'Custom Field' not in before.dsherp_context_doctypes

    frappe.set_user('Administrator')
    policy=frappe.get_doc({'doctype':'DS Doctype Policy','change_reason':'集成测试 '+frappe.generate_hash(length=8),'target_doctype':'Supplier','enabled':1,
        'allow_read':1,'allow_create':0,'allow_update':0,'allow_submit':0,'allow_cancel':0,
        'allow_fill':0,'routes':[]}).insert()
    frappe.set_user(actor);enabled=frappe._dict();boot_session(enabled)
    assert 'Supplier' in enabled.dsherp_context_doctypes
    assert 'Custom Field' not in enabled.dsherp_context_doctypes
    stale=list(enabled.dsherp_context_doctypes)

    frappe.set_user('Administrator');policy.enabled=0;policy.change_reason='集成测试 '+frappe.generate_hash(length=8);policy.save();frappe.set_user(actor)
    disabled=frappe._dict();boot_session(disabled)
    assert 'Supplier' not in disabled.dsherp_context_doctypes
    assert 'Supplier' in stale
    context={'schema_version':1,'route':['Form','Supplier','DSHERP 制造测试合成供应商'],
        'page_type':'form','doctype':'Supplier','name':'DSHERP 制造测试合成供应商','version':None,'dirty':False}
    try:_context(context);raise AssertionError('stale client context bypassed disabled Supplier policy')
    except frappe.PermissionError as error:assert 'DS DocType 策略未启用' in str(error),error
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result = subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=40,
    )
    assert result.returncode == 0, result.stderr


def test_missing_policy_schema_has_one_explicit_error_for_queries_and_ordinary_boot():
    script = r'''
import os,frappe
from unittest.mock import patch
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.boot import boot_session
from dsherp_bridge.doctype_policy import policy_revision_material,require_action

EXPECTED='DS DocType 策略表未迁移至本站点'
actual_table_exists=frappe.db.table_exists

def injected_table_exists(table):
    return False if table=='DS Doctype Policy' else actual_table_exists(table)

def exact_permission_error(call):
    try:call();return 'returned'
    except frappe.PermissionError as error:return str(error)

try:
    frappe.set_user('dsherp-writer@example.invalid')
    with patch.object(frappe.db,'table_exists',side_effect=injected_table_exists):
        observed={
            'require_action':exact_permission_error(lambda:require_action('Item','fill')),
            'policy_revision_material':exact_permission_error(policy_revision_material),
            'boot_session':exact_permission_error(lambda:boot_session(frappe._dict())),
        }
    assert observed=={key:EXPECTED for key in observed},observed
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result = subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_policy_rows_gate_tool_doctypes():
    script = r'''
import hashlib,json,os,uuid,frappe
from frappe.utils import add_to_date,now_datetime
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge import context_execution
from dsherp_bridge.context_permissions import run_revision

actor='policy-gate-'+uuid.uuid4().hex+'@example.invalid'
try:
    frappe.set_user('Administrator')
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic policy gate','enabled':1,
                    'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic policy gate'}).insert(ignore_permissions=True)

    def new_run():
        capability=uuid.uuid4().hex
        run=frappe.get_doc({'doctype':'DS Model Run','conversation':conversation.name,'domain':'operation',
            'status':'Running','page_context':json.dumps({'schema_version':1,'page_type':'unknown','route':[]}),
            'permission_revision':run_revision(actor,'operation'),
            'capability_hash':hashlib.sha256(capability.encode()).hexdigest(),
            'expires_at':add_to_date(now_datetime(),minutes=3),'sources':'[]'}).insert(ignore_permissions=True)
        return {'run_id':run.name,'capability':capability}

    frappe.set_user('Administrator')
    policy_name=frappe.db.get_value('DS Doctype Policy',{'target_doctype':'Item'},'name')
    policy=(frappe.get_doc('DS Doctype Policy',policy_name) if policy_name else
        frappe.get_doc({'doctype':'DS Doctype Policy','change_reason':'集成测试 '+frappe.generate_hash(length=8),'target_doctype':'Item'}).insert())
    policy.enabled=0;policy.allow_read=0;policy.allow_create=0;policy.allow_update=0
    policy.allow_submit=0;policy.allow_cancel=0;policy.allow_fill=0;policy.change_reason='集成测试 '+frappe.generate_hash(length=8);policy.save()
    frappe.set_user(actor)
    try:
        frappe.get_doc('DS Doctype Policy',policy.name).check_permission('read')
        raise AssertionError('business user read governance policy')
    except frappe.PermissionError:
        pass
    disabled=new_run()
    try:
        context_execution.run_tool(**disabled,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'})
        raise AssertionError('disabled policy allowed read')
    except frappe.PermissionError as error:
        assert '策略' in str(error),error

    frappe.set_user('Administrator');policy=frappe.get_doc('DS Doctype Policy',policy.name)
    policy.enabled=1;policy.allow_read=1;policy.change_reason='集成测试 '+frappe.generate_hash(length=8);policy.save();frappe.set_user(actor)
    read_only=new_run()
    item=context_execution.run_tool(**read_only,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'})
    arguments={'doctype':'Item','name':item['name'],'values':{'item_name':'Policy proposal only'},'version':str(item['modified'])}
    try:
        context_execution.run_tool(**read_only,tool='erp_propose_update',arguments=arguments)
        raise AssertionError('read-only policy allowed update proposal')
    except frappe.PermissionError as error:
        assert '策略' in str(error),error

    frappe.set_user('Administrator');policy=frappe.get_doc('DS Doctype Policy',policy.name)
    policy.allow_update=1;policy.change_reason='集成测试 '+frappe.generate_hash(length=8);policy.save();frappe.set_user(actor)
    allowed=new_run()
    item=context_execution.run_tool(**allowed,tool='erp_read_record',arguments={'doctype':'Item','name':'DSHERP-TEST-ITEM'})
    arguments={'doctype':'Item','name':item['name'],'values':{'item_name':'Policy proposal only'},'version':str(item['modified'])}
    proposal=context_execution.run_tool(**allowed,tool='erp_propose_update',arguments=arguments)
    assert proposal['status']=='Pending'
    assert frappe.db.get_value('Item',item['name'],'item_name')!='Policy proposal only'
finally:
    frappe.db.rollback();frappe.destroy()
'''
    result = subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=40,
    )
    assert result.returncode == 0, result.stderr


def test_policy_change_rotates_revision():
    script = r'''
import os,uuid,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_permissions import revision
from dsherp_bridge.operations import propose_update,confirm

tag=uuid.uuid4().hex;actor='policy-revision-'+tag+'@example.invalid';conversation=None;proposal_id=None
policy_name=None;original_policy=None
item_name='DSHERP-TEST-ITEM';before_name=None
try:
    frappe.set_user('Administrator')
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic policy revision','enabled':1,
                    'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    policy_name=frappe.db.get_value('DS Doctype Policy',{'target_doctype':'Item'},'name')
    if policy_name:
        policy=frappe.get_doc('DS Doctype Policy',policy_name)
        original_policy={field:policy.get(field) for field in ('enabled','allow_read','allow_create','allow_update',
            'allow_submit','allow_cancel','allow_fill','company_scope')}
        original_policy['routes']=[{key:row.get(key) for key in ('route_name','method_path','target_doctype')} for row in policy.routes]
    else:
        policy=frappe.get_doc({'doctype':'DS Doctype Policy','change_reason':'集成测试 '+frappe.generate_hash(length=8),'target_doctype':'Item'}).insert();policy_name=policy.name
    policy.enabled=1;policy.allow_read=1;policy.allow_create=0;policy.allow_update=1
    policy.allow_submit=0;policy.allow_cancel=0;policy.allow_fill=0;policy.company_scope=None
    policy.set('routes',[{'route_name':'delivery_note',
        'method_path':'erpnext.selling.doctype.sales_order.sales_order.make_delivery_note',
        'target_doctype':'Delivery Note'}]);policy.change_reason='集成测试 '+frappe.generate_hash(length=8);policy.save()
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic policy revision'}).insert(ignore_permissions=True).name
    item=frappe.get_doc('Item',item_name);before_name=item.item_name
    before_revision=revision(actor)
    proposal=propose_update(conversation,'Item',item.name,{'item_name':'Must remain only proposed'},str(item.modified))
    proposal_id=proposal['id'];frappe.db.commit()

    frappe.set_user('Administrator');policy=frappe.get_doc('DS Doctype Policy',policy.name)
    policy.routes[0].route_name='delivery_note_changed';policy.change_reason='集成测试 '+frappe.generate_hash(length=8);policy.save();frappe.db.commit()
    frappe.set_user(actor)
    assert revision(actor)!=before_revision
    result=confirm(proposal_id,proposal['digest'],uuid.uuid4().hex)
    assert result['status']=='Failed',result
    assert frappe.db.get_value('Item',item_name,'item_name')==before_name
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if proposal_id:
        for execution in frappe.get_all('DS Execution Record',filters={'proposal':proposal_id},pluck='name'):
            frappe.db.delete('DS Execution Record',{'name':execution})
        if frappe.db.exists('DS Operation Proposal',proposal_id):
            frappe.db.delete('DS Operation Proposal',{'name':proposal_id})
    if conversation and frappe.db.exists('DS Conversation',conversation):
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if policy_name and frappe.db.exists('DS Doctype Policy',policy_name):
        if original_policy:
            policy=frappe.get_doc('DS Doctype Policy',policy_name)
            for field,value in original_policy.items():
                if field!='routes':policy.set(field,value)
            policy.set('routes',original_policy['routes']);policy.change_reason='集成测试 '+frappe.generate_hash(length=8);policy.save()
        else:frappe.delete_doc('DS Doctype Policy',policy_name)
    if frappe.db.exists('User',actor):frappe.delete_doc('User',actor)
    frappe.db.commit();frappe.destroy()
'''
    result = subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=40,
    )
    assert result.returncode == 0, result.stderr


def test_disabled_policy_creation_rotates_revision_before_confirmation():
    script = r'''
import os,uuid,frappe
os.chdir('/home/frappe/frappe-bench/sites');frappe.init(site='dsherp-validation.localhost');frappe.connect()
from dsherp_bridge.context_permissions import revision
from dsherp_bridge.operations import propose_update,confirm

tag=uuid.uuid4().hex;actor='disabled-policy-'+tag+'@example.invalid';conversation=None;proposal_id=None
item_name='DS-POLICY-'+tag
disabled_target='Purchase Invoice'
try:
    frappe.set_user('Administrator')
    frappe.get_doc({'doctype':'User','email':actor,'first_name':'Synthetic disabled policy revision','enabled':1,
                    'send_welcome_email':0,'roles':[{'role':'Item Manager'}]}).insert()
    item=frappe.copy_doc(frappe.get_doc('Item','DSHERP-TEST-ITEM'))
    item.item_code=item_name;item.item_name='Before disabled policy';item.insert()
    item_policy=frappe.get_doc('DS Doctype Policy','Item')
    assert item_policy.enabled and item_policy.allow_read and item_policy.allow_update
    assert not frappe.db.exists('DS Doctype Policy',disabled_target)
    frappe.set_user(actor)
    conversation=frappe.get_doc({'doctype':'DS Conversation','title':'Synthetic disabled policy revision'}).insert(ignore_permissions=True).name
    item=frappe.get_doc('Item',item_name)
    before_revision=revision(actor)
    proposal=propose_update(conversation,'Item',item.name,{'item_name':'Disabled policy must invalidate'},str(item.modified))
    proposal_id=proposal['id'];frappe.db.commit()

    frappe.set_user('Administrator')
    policy=frappe.get_doc({'doctype':'DS Doctype Policy','change_reason':'集成测试 '+frappe.generate_hash(length=8),'target_doctype':disabled_target,'enabled':0,
        'allow_read':0,'allow_create':0,'allow_update':0,'allow_submit':0,'allow_cancel':0,'allow_fill':0}).insert()
    frappe.db.commit();frappe.set_user(actor)
    after_revision=revision(actor)
    assert after_revision!=before_revision,'disabled policy creation did not rotate authorization revision'
    result=confirm(proposal_id,proposal['digest'],uuid.uuid4().hex)
    assert result['status']=='Failed',result
    assert '权限或企业成员关系已变化' in result['error'],result
    assert frappe.db.get_value('Item',item_name,'item_name')=='Before disabled policy'
finally:
    frappe.db.rollback();frappe.set_user('Administrator')
    if proposal_id:
        for execution in frappe.get_all('DS Execution Record',filters={'proposal':proposal_id},pluck='name'):
            frappe.db.delete('DS Execution Record',{'name':execution})
        if frappe.db.exists('DS Operation Proposal',proposal_id):
            frappe.db.delete('DS Operation Proposal',{'name':proposal_id})
    if conversation and frappe.db.exists('DS Conversation',conversation):
        frappe.delete_doc('DS Conversation',conversation,ignore_permissions=True)
    if frappe.db.exists('DS Doctype Policy',disabled_target):
        frappe.delete_doc('DS Doctype Policy',disabled_target)
    if frappe.db.exists('Item',item_name):frappe.delete_doc('Item',item_name,ignore_permissions=True)
    if frappe.db.exists('User',actor):frappe.delete_doc('User',actor)
    frappe.db.commit();frappe.destroy()
'''
    result = subprocess.run(
        ['docker','exec','-i','dsherp-validation-backend-1','/home/frappe/frappe-bench/env/bin/python','-'],
        input=script,text=True,capture_output=True,timeout=40,
    )
    assert result.returncode == 0, result.stderr
