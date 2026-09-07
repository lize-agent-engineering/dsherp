"""Two actual Sites and signed internal HTTP; no model or business DDL."""
import uuid

from site_exec import run_site_json, run_site_script

SOURCE, PREVIEW = 'dsherp-validation.localhost', 'dsherp-beta.localhost'
PREVIEW_ACTOR = 'dsherp-preview@example.invalid'
# The package the transfer carries, as the literal the script body embeds verbatim: the preview Site
# must accept and freeze it without ever running the DDL it describes.
PACKAGE = ("{'version':1,'doctypes':[{'name':'DS HTTP Transfer Test','module':'DSHERP Bridge',"
           "'fields':[{'fieldname':'result','label':'Result','fieldtype':'Data'}],"
           "'permissions':[{'role':'System Manager','read':1,'write':1,'create':1}]}],'extensions':[],'workflows':[]}")


def test_alpha_package_is_imported_once_into_beta_with_live_source_authorization(residue):
    title = f'Two Site transfer test {uuid.uuid4().hex}'
    # The source conversation is the audit-chain root of everything the first script commits: the
    # bundle hangs off it and the transfer off the bundle, so registering it sweeps all three.
    residue.doc(SOURCE, 'DS Conversation', {'owner': PREVIEW_ACTOR, 'title': title})
    prepared = run_site_json(SOURCE, r'''
from dsherp_bridge.configuration import propose_bundle
from dsherp_bridge.configuration_transfer import prepare_transfer
conversation=frappe.get_doc({'doctype':'DS Conversation','title':TITLE}).insert(ignore_permissions=True)
package=PACKAGE
bundle=propose_bundle(conversation.name,package);frappe.db.commit()
assert not bundle['preview_available'] and bundle['preview_transfer_available']
transfer=prepare_transfer(bundle['id'],bundle['digest'],'two-site-transfer-request')
frappe.db.commit();print(json.dumps({'session':conversation.name,'bundle':bundle['id'],'transfer':transfer['id'],'digest':bundle['digest']}))
'''.replace('TITLE', repr(title)).replace('PACKAGE', PACKAGE), user=PREVIEW_ACTOR, timeout=50)
    # What accept_transfer will create on the preview Site is hash-named and titled by the product,
    # so it cannot be registered by name before it exists. Register it by that Site's own clock read
    # taken now - the host clock is not comparable with the row's `creation`.
    since = run_site_json(PREVIEW, "from frappe.utils import now_datetime\nprint(json.dumps(str(now_datetime())))")
    residue.doc(PREVIEW, 'DS Configuration Bundle', {'owner': PREVIEW_ACTOR, 'creation': ['>=', since]})
    residue.doc(PREVIEW, 'DS Conversation', {'owner': PREVIEW_ACTOR, 'title': '隔离配置预览', 'creation': ['>=', since]})
    imported = run_site_json(PREVIEW, r'''
from dsherp_bridge.configuration_transfer import accept_transfer
from dsherp_bridge.configuration_execution import prepare_preview
result=accept_transfer(TRANSFER)
again=accept_transfer(TRANSFER);assert again['bundle']['id']==result['bundle']['id']
doc=frappe.get_doc('DS Configuration Bundle',result['bundle']['id']);origin=json.loads(doc.payload)['origin']
assert origin['source_site']=='dsherp-validation.localhost' and origin['bundle_digest']==DIGEST
confirmation=prepare_preview(doc.name,doc.digest)
assert not frappe.db.exists('DocType','DS HTTP Transfer Test')
frappe.db.commit();print(json.dumps({'session':result['session_id'],'bundle':doc.name,'confirmation':confirmation['id']}))
'''.replace('TRANSFER', repr(prepared['transfer'])).replace('DIGEST', repr(prepared['digest'])),
                             user=PREVIEW_ACTOR, timeout=50)
    assert imported['bundle'] and imported['confirmation']
    run_site_script(SOURCE, "assert not frappe.db.exists('DocType','DS HTTP Transfer Test')")
