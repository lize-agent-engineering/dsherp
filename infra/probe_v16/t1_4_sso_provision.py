"""Provision a second throwaway Site and native OAuth bindings for the C1 probe."""
import json
import os
from pathlib import Path
import secrets
import subprocess


ROOT = Path("/home/frappe/frappe-bench")
SITES = ROOT / "sites"
BUSINESS_SITE = "dsherp-v16probe.localhost"
PLATFORM_SITE = "dsherp-v16probe-platform.localhost"
CALLBACK = "http://localhost:28082/api/method/dsherp_bridge.sso.callback"
MEMBER = "probe-member@example.invalid"
BUSINESS_USER = "probe-business@example.invalid"
MEMBER_ROLE = "DSHERP v16 Probe Member"
BUSINESS_ROLE = "DSHERP v16 Probe Business"


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment: {name}")
    return value


if (SITES / PLATFORM_SITE).exists():
    raise SystemExit("Probe platform Site already exists; destroy the probe project before retrying")

subprocess.run([
    "bench", "new-site", PLATFORM_SITE,
    "--db-host", "db",
    "--db-root-username", "root",
    "--db-root-password", required_env("DSHERP_V16_PROBE_DB_ROOT_PASSWORD"),
    "--admin-password", required_env("DSHERP_V16_PROBE_ADMIN_PASSWORD"),
    "--mariadb-user-host-login-scope", "%",
    "--install-app", "erpnext",
], cwd=ROOT, check=True)
for app in ("dsherp_bridge", "dsherp_platform"):
    subprocess.run(["bench", "--site", PLATFORM_SITE, "install-app", app], cwd=ROOT, check=True)

import frappe
from frappe.installer import update_site_config
from frappe.core.doctype.user.user import generate_keys
from frappe.utils.password import update_password


def connect(site):
    os.chdir(SITES)
    frappe.init(site=site, sites_path=str(SITES))
    frappe.connect()
    frappe.set_user("Administrator")


def disconnect():
    frappe.destroy()


connect(BUSINESS_SITE)
frappe.get_doc({
    "doctype": "Role",
    "role_name": BUSINESS_ROLE,
    "desk_access": 1,
}).insert()
business = frappe.get_doc({
    "doctype": "User",
    "email": BUSINESS_USER,
    "first_name": "v16 Probe Business",
    "enabled": 1,
    "user_type": "System User",
    "send_welcome_email": 0,
    "roles": [{"role": BUSINESS_ROLE}],
}).insert()
generate_keys(business.name)
business.reload()
business_api_key = business.api_key
business_api_secret = business.get_password("api_secret")
frappe.db.commit()
disconnect()

connect(PLATFORM_SITE)
frappe.get_doc({
    "doctype": "Role",
    "role_name": MEMBER_ROLE,
    "desk_access": 1,
}).insert()
platform_member = frappe.get_doc({
    "doctype": "User",
    "email": MEMBER,
    "first_name": "v16 Probe Member",
    "enabled": 1,
    "user_type": "System User",
    "send_welcome_email": 0,
    "roles": [{"role": MEMBER_ROLE}],
}).insert()
update_password(platform_member.name, required_env("DSHERP_V16_PROBE_MEMBER_PASSWORD"))
enterprise = frappe.get_doc({
    "doctype": "DS Enterprise",
    "enterprise_id": "probe",
    "title": "v16 OAuth 合成企业",
    "site": BUSINESS_SITE,
    "base_url": "http://backend:8000",
    "status": "Ready",
}).insert()
frappe.get_doc({
    "doctype": "DS Membership",
    "enterprise": enterprise.name,
    "platform_user": MEMBER,
    "enabled": 1,
    "erp_user": BUSINESS_USER,
    "api_key": business_api_key,
    "api_secret": business_api_secret,
}).insert()
oauth = frappe.get_doc({
    "doctype": "OAuth Client",
    "app_name": "DSHERP v16 probe Desk",
    "client_secret": secrets.token_urlsafe(32),
    "scopes": "openid",
    "redirect_uris": CALLBACK,
    "default_redirect_uri": CALLBACK,
    "grant_type": "Authorization Code",
    "response_type": "Code",
    "skip_authorization": 0,
    "allowed_roles": [{"role": MEMBER_ROLE}],
}).insert()
update_site_config("dsherp_business_sites", {BUSINESS_SITE: "http://backend:8000"})
frappe.db.commit()
credentials = {"client_id": oauth.client_id, "client_secret": oauth.client_secret}
disconnect()

connect(BUSINESS_SITE)
provider = frappe.get_doc({
    "doctype": "Social Login Key",
    "provider_name": "DSHERP v16 probe Platform",
    "social_login_provider": "Custom",
    "enable_social_login": 0,
    "sign_ups": "Deny",
    "client_id": credentials["client_id"],
    "client_secret": credentials["client_secret"],
    "base_url": "http://platform-backend:8000",
    "authorize_url": "http://platform.localhost:28083/api/method/frappe.integrations.oauth2.authorize",
    "access_token_url": "http://platform-backend:8000/api/method/frappe.integrations.oauth2.get_token",
    "api_endpoint": "http://platform-backend:8000/api/method/dsherp_platform.api.desk_identity",
    "redirect_url": CALLBACK,
    "auth_url_data": json.dumps({"response_type": "code", "scope": "openid"}),
    "user_id_property": "sub",
}).insert()
update_site_config("dsherp_platform_oauth", {
    "provider": provider.name,
    "enterprise": enterprise.name,
    "platform_site": PLATFORM_SITE,
})
frappe.db.commit()
frappe.clear_cache()
disconnect()

subprocess.run(["bench", "--site", PLATFORM_SITE, "clear-cache"], cwd=ROOT, check=True)
print("C1-R2-R3 provision PASS: native OAuth provider, client, enterprise and users created")
