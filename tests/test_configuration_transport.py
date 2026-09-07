import pytest
from frappe_app.dsherp_bridge.configuration_transport import seal,open_envelope


def test_signed_configuration_envelope_binds_payload_purpose_and_time():
    packet=seal({'transfer_id':'T1','actor':'user@example.invalid'},'pair-secret','export',now=1000)
    assert open_envelope(packet,'pair-secret','export',now=1020)=={'transfer_id':'T1','actor':'user@example.invalid'}
    for key,purpose,clock in [('wrong','export',1001),('pair-secret','receipt',1001),('pair-secret','export',1061),('pair-secret','export',900)]:
        with pytest.raises(ValueError):open_envelope(packet,key,purpose,now=clock)
    packet['payload']['actor']='other@example.invalid'
    with pytest.raises(ValueError):open_envelope(packet,'pair-secret','export',now=1001)


def test_envelope_rejects_unknown_fields_and_does_not_mutate_payload():
    payload={'values':['one']};packet=seal(payload,'secret','export',now=1000)
    payload['values'].append('two')
    assert open_envelope(packet,'secret','export',now=1000)=={'values':['one']}
    with pytest.raises(ValueError):open_envelope({**packet,'user':'Administrator'},'secret','export',now=1000)


def test_peer_reason_relays_only_what_the_peer_said_through_frappe_throw():
    """A paired Site refuses with frappe.throw so that the person on the other side learns why.
    Nothing else in an error body - the exception class, the traceback - is repeated to anyone."""
    import json as _json

    from frappe_app.dsherp_bridge.configuration_transport import peer_reason

    said = _json.dumps([_json.dumps({'message': '配置交接已过期，请重新发起交接',
                                     'title': 'Message', 'indicator': 'red'})])
    assert peer_reason({'exc_type': 'ValidationError',
                        'exception': 'Traceback (most recent call last)...',
                        '_server_messages': said}) == '配置交接已过期，请重新发起交接'
    assert peer_reason({'exc_type': 'PermissionError',
                        'exception': 'frappe.exceptions.PermissionError: 配置交接身份或站点不匹配'}) == ''
    assert peer_reason(None) == ''
    assert peer_reason({'_server_messages': 'not json'}) == ''
    assert peer_reason({'_server_messages': _json.dumps([42])}) == ''
    assert peer_reason({'_server_messages': _json.dumps(
        [_json.dumps({'message': 'Traceback (most recent call last)'})])}) == ''
    two = _json.dumps([_json.dumps({'message': '第一条\n第二行'}), _json.dumps({'message': '第二条'})])
    assert peer_reason({'_server_messages': two}) == '第一条；第二条'
    assert peer_reason({'_server_messages': _json.dumps([_json.dumps({'message': 'x' * 500})])}) == 'x' * 200
