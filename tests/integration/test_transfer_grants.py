"""The authorization a configuration transfer is exported under lives beside the transfer, not
in it, for the transfer's window and no longer (plan 5; R7 did the same for runs). Real Site."""
import uuid

from site_exec import run_site_json

SITE = 'dsherp-validation.localhost'
# Proposing a configuration package needs DocType.create (configuration.authorize); on alpha the
# only synthetic actor that has it is the operator, so the grant probe runs as that one.
ACTOR = 'dsherp-preview@example.invalid'
BODY = r'''
from frappe.utils.password import encrypt
from dsherp_bridge import sso,grants
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_transfer import prepare_transfer,export_transfer,TRANSFER_WINDOW_SECONDS
from dsherp_bridge.configuration_transport import seal,open_envelope
actor=ACTOR;frappe.set_user(actor)
info={'sub':'member@example.invalid','email':actor,'site':frappe.local.site,'enterprise':'alpha',
      'binding_version':'1','enterprise_version':'v1'}
sso.identity_for_token=lambda token,verify_business=False:info
grant=encrypt(json.dumps({'identity':info,'token':'synthetic-token'}))
frappe.session.data.dsherp_platform_grant=grant
frappe.conf.dsherp_configuration_preview={'site':'isolated-preview.localhost','url':'http://preview-backend:8000',
    'public_url':'http://preview.localhost:18085','secret':'test-pair-secret'}
out={'configured':TRANSFER_WINDOW_SECONDS}
conversation=frappe.get_doc({'doctype':'DS Conversation','title':TITLE}).insert(ignore_permissions=True)
package={'version':1,'doctypes':[{'name':'DS Transfer Grant Probe','module':'DSHERP Bridge',
    'fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],
    'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}
bundle=propose_bundle(conversation.name,package);frappe.db.commit()
transfer=prepare_transfer(bundle['id'],bundle['digest'],TAG);frappe.db.commit()
try:
    row=frappe.db.get_value('DS Configuration Transfer',transfer['id'],'*',as_dict=True)
    out['column_present']='platform_grant' in frappe.db.get_table_columns('DS Configuration Transfer')
    out['row_mentions_token']='synthetic-token' in json.dumps(row,default=str)
    out['cached']=grants.of_transfer(transfer['id'])=={'grant':grant}
    out['window']=(row['expires_at']-row['creation']).total_seconds()
    request=seal({'transfer_id':transfer['id'],'actor':actor,'source_site':frappe.local.site,
        'preview_site':'isolated-preview.localhost'},'test-pair-secret','export-request')
    frappe.session.data.pop('dsherp_platform_grant',None);frappe.set_user('Guest')
    data=open_envelope(export_transfer(request),'test-pair-secret','export-response')
    out['exported']=data['package']==package and data['actor']==actor
    grants.drop_transfer(transfer['id'])
    try:
        export_transfer(request);out['without_lease']='exported'
    except frappe.ValidationError as error:out['without_lease']=str(error)
finally:
    grants.drop_transfer(transfer['id'])
print(json.dumps(out,ensure_ascii=False))
'''


def test_the_grant_is_cached_for_the_window_never_stored_in_the_row_and_the_export_acts_under_it(residue):
    tag = uuid.uuid4().hex
    title = f'Transfer grant probe {tag}'
    residue.doc(SITE, 'DS Conversation', {'owner': ACTOR, 'title': title})
    out = run_site_json(SITE, BODY.replace('ACTOR', repr(ACTOR), 1).replace('TITLE', repr(title), 1)
                        .replace('TAG', repr('transfer-grant-' + tag), 1), timeout=180)
    assert out['column_present'] is False and out['row_mentions_token'] is False, out
    assert out['cached'] is True, out
    assert abs(out['window'] - out['configured']) < 5, out
    assert out['exported'] is True, out
    assert '配置交接授权已失效' in out['without_lease'], out
