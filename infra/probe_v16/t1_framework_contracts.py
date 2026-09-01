"""Probe v16 framework imports, schemas, sorting, translations, and mapper signatures."""
import inspect
import json
import os
import frappe


SITE = "dsherp-v16probe.localhost"


os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=SITE)
frappe.connect()
frappe.set_user("Administrator")

try:
    from frappe.core.doctype.user.user import generate_keys
    from frappe.installer import update_site_config
    from frappe.permissions import add_permission
    from frappe.utils.password import update_password
    from frappe.utils.nestedset import get_root_of
    from frappe.utils.oauth import (
        consume_oauth_state,
        get_oauth2_authorize_url,
        get_oauth2_flow,
        get_oauth2_providers,
        get_redirect_uri,
    )
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
    from erpnext.buying.doctype.purchase_order.purchase_order import (
        make_purchase_receipt,
        make_subcontracting_order,
    )
    from erpnext.controllers.subcontracting_controller import (
        make_rm_stock_entry,
        SubcontractingController,
    )
    from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
        make_subcontracting_receipt,
    )

    oauth = {
        function.__name__: str(inspect.signature(function))
        for function in (
            get_oauth2_flow,
            get_oauth2_providers,
            get_redirect_uri,
            get_oauth2_authorize_url,
            consume_oauth_state,
        )
    }
    expected_oauth = {
        "get_oauth2_flow": "(provider: str)",
        "get_oauth2_providers": "() -> dict[str, dict]",
        "get_redirect_uri": "(provider: str) -> str",
        "get_oauth2_authorize_url": "(provider: str, redirect_to: str) -> str",
        "consume_oauth_state": "(state: str) -> str | None",
    }
    if oauth != expected_oauth:
        raise AssertionError({"oauth": oauth, "expected": expected_oauth})

    social_fields = {field.fieldname for field in frappe.get_meta("Social Login Key").fields}
    required_social = {
        "provider_name", "social_login_provider", "enable_social_login", "sign_ups",
        "client_id", "client_secret", "base_url", "authorize_url", "access_token_url",
        "api_endpoint", "redirect_url", "auth_url_data", "user_id_property",
    }
    if not required_social.issubset(social_fields):
        raise AssertionError({"missing_social_login_fields": sorted(required_social - social_fields)})

    provision = {
        function.__module__ + "." + function.__name__: str(inspect.signature(function))
        for function in (generate_keys, update_site_config, add_permission, update_password, get_root_of)
    }
    mappers = {
        function.__module__ + "." + function.__qualname__: str(inspect.signature(function))
        for function in (
            make_delivery_note,
            make_stock_entry,
            make_purchase_receipt,
            make_subcontracting_order,
            make_rm_stock_entry,
            make_subcontracting_receipt,
            SubcontractingController.set_items_conversion_factor,
            SubcontractingController.create_raw_materials_supplied_or_received,
        )
    }

    unsaved = frappe.get_doc({"doctype": "ToDo", "description": "before-save probe"})
    if unsaved.get_doc_before_save() is not None:
        raise AssertionError("get_doc_before_save() must be None for a new document")

    custom_doctypes = [
        "DS Conversation", "DS Model Run", "DS Operation Proposal", "DS Execution Record",
        "DS Configuration Bundle", "DS Configuration Confirmation", "DS Configuration Execution",
        "DS Doctype Policy", "DS Membership", "DS Enterprise", "DS Agent Task",
    ]
    sorting = {
        doctype: {
            "sort_field": frappe.get_meta(doctype).sort_field,
            "sort_order": frappe.get_meta(doctype).sort_order,
        }
        for doctype in custom_doctypes
        if frappe.db.exists("DocType", doctype)
    }
    translation = frappe._("General Ledger", context="Warehouse", lang="zh")

    print(json.dumps({
        "oauth": oauth,
        "provision": provision,
        "mappers": mappers,
        "social_login_fields": sorted(required_social),
        "sorting": sorting,
        "translation": translation,
    }, ensure_ascii=False, sort_keys=True))
    print("C1 framework contracts PASS")
finally:
    frappe.destroy()
