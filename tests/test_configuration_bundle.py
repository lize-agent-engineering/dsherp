import copy
import pytest
from frappe_app.dsherp_bridge.configuration_bundle import freeze_bundle


def bundle():
    return {'version':1,'doctypes':[{'name':'DS Quality Check','module':'DSHERP Bridge','fields':[
        {'fieldname':'section_result','label':'检查结果','fieldtype':'Section Break'},
        {'fieldname':'result','label':'结果','fieldtype':'Select','options':'合格\n不合格'}],
        'permissions':[{'role':'Sales User','read':1,'write':1,'create':1}]}],
        'extensions':[],'workflows':[]}


def test_freezes_native_configuration_without_mutating_input():
    value=bundle();original=copy.deepcopy(value)
    result=freeze_bundle(value)
    assert value==original
    assert result['digest']==freeze_bundle(dict(reversed(list(value.items()))))['digest']
    assert result['package']['doctypes'][0]['fields'][1]['options']=='合格\n不合格'
    value['doctypes'][0]['fields'][1]['label']='changed'
    assert result['package']['doctypes'][0]['fields'][1]['label']=='结果'


@pytest.mark.parametrize('extra',[{'fetch_from':'customer.name'},{'default':'eval:danger()'},
    {'depends_on':'eval:danger()'},{'fieldtype':'Code'},{'rename_from':'old_field'}])
def test_rejects_executable_or_destructive_field_configuration(extra):
    value=bundle();value['doctypes'][0]['fields'][1].update(extra)
    with pytest.raises(ValueError):freeze_bundle(value)


def test_extensions_cannot_make_existing_records_invalid_or_replace_workflow():
    value=bundle();value['extensions']=[{'doctype':'Item','fields':[{'fieldname':'custom_note','label':'备注','fieldtype':'Data','reqd':1}]}]
    with pytest.raises(ValueError):freeze_bundle(value)
    value['extensions'][0]['fields'][0]['reqd']=0
    assert freeze_bundle(value)['digest']
    value['workflows']=[{'workflow_name':'Override ERP','document_type':'Sales Order','states':[],'transitions':[]}]
    with pytest.raises(ValueError):freeze_bundle(value)


def test_rejects_unknown_root_and_duplicate_field_names():
    value=bundle();value['users']=[{'roles':['System Manager']}]
    with pytest.raises(ValueError):freeze_bundle(value)
    value=bundle();value['doctypes'][0]['fields'].append(copy.deepcopy(value['doctypes'][0]['fields'][1]))
    with pytest.raises(ValueError):freeze_bundle(value)
