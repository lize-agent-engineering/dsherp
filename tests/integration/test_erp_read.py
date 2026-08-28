import json

import pytest

SCHEMA = "/api/method/dsherp_bridge.api.read_schema"
RECORD = "/api/method/dsherp_bridge.api.read_record"


@pytest.mark.parametrize("doctype,field", [("Customer", "customer_name"), ("Item", "item_code")])
def test_reader_discovers_real_schema(erp, doctype, field):
    status, body = erp("reader", SCHEMA, doctype=doctype)
    assert status == 200
    assert body["message"]["doctype"] == doctype
    assert field in {f["fieldname"] for f in body["message"]["fields"]}
    assert "custom_dsherp_restricted" not in json.dumps(body)


@pytest.mark.parametrize("doctype,name,field", [
    ("Customer", "DSHERP-TEST-CUSTOMER", "customer_name"),
    ("Item", "DSHERP-TEST-ITEM", "item_code"),
])
def test_reader_reads_actual_synthetic_record_without_restricted_field(erp, doctype, name, field):
    status, body = erp("reader", RECORD, doctype=doctype, name=name)
    assert status == 200
    assert body["message"]["name"] == name
    assert body["message"]["fields"][field] == name
    assert "custom_dsherp_restricted" not in json.dumps(body)
    assert "DSHERP-TEST-RESTRICTED" not in json.dumps(body)


@pytest.mark.parametrize("endpoint", [SCHEMA, RECORD])
@pytest.mark.parametrize("actor", ["denied", "guest"])
def test_unprivileged_actor_cannot_read_or_impersonate(erp, endpoint, actor):
    status, body = erp(actor, endpoint, doctype="Customer", name="DSHERP-TEST-CUSTOMER", user="Administrator")
    assert status == 403
    assert "DSHERP-TEST-RESTRICTED" not in json.dumps(body)
    assert "customer_name" not in json.dumps(body)


@pytest.mark.parametrize("endpoint", [SCHEMA, RECORD])
def test_tool_scope_rejects_other_doctypes(erp, endpoint):
    status, _ = erp("reader", endpoint, doctype="User", name="Administrator")
    assert status == 403


def test_native_resource_read_is_allowed_for_reader_and_denied_for_other_user(erp):
    for actor, expected in [("reader", 200), ("denied", 403)]:
        status, body = erp(actor, "/api/resource/Item/DSHERP-TEST-ITEM")
        assert status == expected
        if status == 200:
            assert body["data"]["item_code"] == "DSHERP-TEST-ITEM"


def test_reader_cannot_read_document_outside_user_permission(erp):
    status, body = erp("reader", RECORD, doctype="Customer", name="DSHERP-TEST-OTHER-CUSTOMER")
    assert status == 403
    assert "customer_name" not in json.dumps(body)
