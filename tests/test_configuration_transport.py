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
