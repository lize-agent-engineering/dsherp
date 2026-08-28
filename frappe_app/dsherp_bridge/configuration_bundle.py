"""Data-only native configuration packages; authorization belongs to the Site.

This parser does not publish, grant roles, or infer missing native metadata.
Existence, Link targets, baselines and native permissions are checked at use.
"""
import hashlib
import json
import re

FIELD_TYPES={'Data','Small Text','Text','Long Text','Int','Float','Currency','Percent',
             'Check','Date','Datetime','Time','Select','Link','Table','Section Break',
             'Column Break','Tab Break'}


def _object(value,allowed,required):
    if not isinstance(value,dict) or set(value)-set(allowed) or set(required)-set(value):
        raise ValueError('配置包含不支持的属性或缺少必要内容')


def _text(value):
    if not isinstance(value,str) or not value.strip() or len(value)>140:
        raise ValueError('配置名称无效')


def _list(value):
    if not isinstance(value,list):raise ValueError('配置集合必须是数组')


def _flag(value):
    if type(value) not in (int,bool) or value not in (0,1):raise ValueError('配置开关必须为0或1')


def _fields(fields,extension=False):
    _list(fields)
    if not fields:raise ValueError('必须提供字段')
    names=set()
    for field in fields:
        _object(field,{'fieldname','label','fieldtype','options','reqd','in_list_view','insert_after'},
                {'fieldname','label','fieldtype'})
        _text(field['label']);name=field['fieldname']
        if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',name) or name in names:
            raise ValueError('字段名称无效或重复')
        names.add(name)
        if field['fieldtype'] not in FIELD_TYPES:raise ValueError('不支持此字段类型')
        for flag in ('reqd','in_list_view'):
            if flag in field:_flag(field[flag])
        if extension and field.get('reqd'):raise ValueError('扩展字段不能使既有记录缺少必填值')
        if 'options' in field:
            if field['fieldtype'] not in ('Select','Link','Table') or not isinstance(field['options'],str):
                raise ValueError('此字段不能设置options')
        if field['fieldtype'] in ('Link','Table'):_text(field.get('options'))
        if 'insert_after' in field:
            if not extension:raise ValueError('新DocType布局使用字段顺序')
            _text(field['insert_after'])


def freeze_bundle(package):
    _object(package,{'version','doctypes','extensions','workflows'},{'version','doctypes','extensions','workflows'})
    if type(package['version']) is not int or package['version']!=1:raise ValueError('配置包版本不支持')
    for key in ('doctypes','extensions','workflows'):_list(package[key])
    if not any(package[key] for key in ('doctypes','extensions','workflows')):raise ValueError('配置包为空')
    names=set()
    for doc in package['doctypes']:
        _object(doc,{'name','module','fields','permissions','is_submittable','istable'}, {'name','module','fields','permissions'})
        _text(doc['name']);_text(doc['module'])
        if doc['name'] in names:raise ValueError('DocType重复')
        names.add(doc['name']);_fields(doc['fields']);_list(doc['permissions'])
        for flag in ('is_submittable','istable'):
            if flag in doc:_flag(doc[flag])
        roles=set()
        for permission in doc['permissions']:
            _object(permission,{'role','read','write','create','submit','cancel'},{'role','read'})
            _text(permission['role'])
            if permission['role'] in roles:raise ValueError('角色权限重复')
            roles.add(permission['role'])
            for key,value in permission.items():
                if key!='role':_flag(value)
    targets=set()
    for extension in package['extensions']:
        _object(extension,{'doctype','fields'},{'doctype','fields'});_text(extension['doctype'])
        if extension['doctype'] in targets or extension['doctype'] in names:raise ValueError('扩展目标重复')
        targets.add(extension['doctype']);_fields(extension['fields'],extension=True)
    workflow_targets=set()
    for workflow in package['workflows']:
        _object(workflow,{'workflow_name','document_type','states','transitions'},
                {'workflow_name','document_type','states','transitions'})
        _text(workflow['workflow_name'])
        if workflow['document_type'] not in names or workflow['document_type'] in workflow_targets:
            raise ValueError('仅允许新应用自己的新工作流，不能覆盖既有工作流')
        workflow_targets.add(workflow['document_type'])
        _list(workflow['states']);_list(workflow['transitions']);states=set()
        if not workflow['states'] or not workflow['transitions']:raise ValueError('工作流必须包含状态和转换')
        for state in workflow['states']:
            _object(state,{'state','doc_status','allow_edit'},{'state','doc_status','allow_edit'})
            _text(state['state']);_text(state['allow_edit'])
            if state['state'] in states or state['doc_status'] not in ('0','1','2'):raise ValueError('工作流状态无效')
            states.add(state['state'])
        for transition in workflow['transitions']:
            _object(transition,{'state','action','next_state','allowed','allow_self_approval'},
                    {'state','action','next_state','allowed'})
            _text(transition['action']);_text(transition['allowed'])
            if transition['state'] not in states or transition['next_state'] not in states:raise ValueError('转换状态不存在')
            if 'allow_self_approval' in transition:_flag(transition['allow_self_approval'])
    encoded=json.dumps(package,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return {'package':json.loads(encoded),'digest':hashlib.sha256(encoded.encode()).hexdigest()}
