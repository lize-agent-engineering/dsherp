"""Rotate only the synthetic alpha Desk OAuth client, preserving native sessions."""
import json
from provision_desk_oauth import execute


def main():
    secret=json.loads(execute('dsherp-validation-platform-backend-1','dsherp-platform.localhost',"""
import secrets
names=frappe.get_all('OAuth Client',filters={'app_name':'DSHERP alpha Desk'},pluck='name')
assert len(names)==1
doc=frappe.get_doc('OAuth Client',names[0]);doc.client_secret=secrets.token_urlsafe(32);doc.save()
frappe.db.commit();print(json.dumps(doc.client_secret));frappe.destroy()
"""))
    execute('dsherp-validation-backend-1','dsherp-validation.localhost',f"""
doc=frappe.get_doc('Social Login Key','dsherp_platform');doc.client_secret={secret!r};doc.save()
frappe.db.commit();frappe.clear_cache();frappe.destroy()
""")
    print('Rotated the synthetic alpha Desk OAuth client secret on both Sites; no other credentials changed.')


if __name__=='__main__':main()
