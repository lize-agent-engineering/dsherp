import httpx
import pytest
from dsherp.provider_circuit import CircuitBreaker,probe_models


def test_breaker_opens_after_three_provider_failures_and_recovers():
    b=CircuitBreaker(threshold=3,open_seconds=60)
    for _ in range(2):b.record('provider_failure',now=0)
    assert b.allow(1) and b.state=='closed'
    b.record('provider_failure',now=2)
    assert b.state=='open' and not b.allow(30)
    assert b.allow(63) and b.state=='half_open'
    assert not b.allow(63) and b.state=='half_open'
    b.record('provider_failure',now=64);assert b.state=='open' and not b.allow(65)
    assert b.allow(125);b.record('ok',now=126);assert b.state=='closed' and b.consecutive_failures==0
    b.record('other',now=127);assert b.state=='closed'


def test_breaker_reopens_from_half_open_on_other():
    b=CircuitBreaker(threshold=3,open_seconds=60)
    for _ in range(3):b.record('provider_failure',now=0)
    assert b.state=='open'
    b.record('other',now=1);assert b.state=='open' and not b.allow(1)
    assert b.allow(60) and b.state=='half_open'
    b.record('other',now=61)
    assert b.state=='open' and not b.allow(120)
    assert b.allow(121) and b.state=='half_open'


def test_breaker_reset_closes_from_open():
    b=CircuitBreaker(threshold=1,open_seconds=60)
    b.record('provider_failure',now=0)
    assert b.state=='open' and b.consecutive_failures==1 and not b.allow(1)
    b.reset()
    assert b.state=='closed' and b.consecutive_failures==0 and b.allow(1)


def test_breaker_fastfails_illegal_outcome_and_params():
    with pytest.raises(ValueError):CircuitBreaker(threshold=0,open_seconds=60)
    with pytest.raises(ValueError):CircuitBreaker(threshold=-1,open_seconds=60)
    with pytest.raises(ValueError):CircuitBreaker(threshold=True,open_seconds=60)
    with pytest.raises(ValueError):CircuitBreaker(threshold=3,open_seconds=0)
    with pytest.raises(ValueError):CircuitBreaker(threshold=3,open_seconds=-1)
    with pytest.raises(ValueError):CircuitBreaker(threshold=3,open_seconds=True)
    b=CircuitBreaker(threshold=3,open_seconds=60)
    with pytest.raises(ValueError):b.record('timeout',now=0)
    with pytest.raises(ValueError):b.record('',now=0)
    with pytest.raises(ValueError):b.record('ok',now=None)
    with pytest.raises(ValueError):b.record('ok',now=True)
    with pytest.raises(ValueError):b.allow(None)
    with pytest.raises(ValueError):b.allow(True)
    assert b.state=='closed' and b.consecutive_failures==0


def test_breaker_does_not_read_clock():
    import time
    monkey=time.time
    time.time=lambda:(_ for _ in ()).throw(AssertionError('clock'))
    try:
        b=CircuitBreaker(threshold=1,open_seconds=10)
        b.record('provider_failure',now=0)
        assert b.state=='open' and not b.allow(9)
        assert b.allow(10) and b.state=='half_open'
    finally:
        time.time=monkey


def test_probe_models_is_free_quiet_and_never_raises(capsys):
    seen=[]
    def handler(request):
        seen.append((request.method,str(request.url),request.headers.get('authorization')))
        return httpx.Response(200,json={'data':[]}) if len(seen)==1 else httpx.Response(503)
    client=httpx.Client(transport=httpx.MockTransport(handler))
    assert probe_models('http://provider.invalid/v1','sk-synthetic-key',client=client) is True
    assert probe_models('http://provider.invalid/v1','sk-synthetic-key',client=client) is False
    assert seen[0][0]=='GET' and seen[0][1]=='http://provider.invalid/v1/models' and seen[0][2]=='Bearer sk-synthetic-key'
    broken=httpx.Client(transport=httpx.MockTransport(lambda r:(_ for _ in ()).throw(httpx.ConnectError('down'))))
    assert probe_models('http://provider.invalid/v1','sk-synthetic-key',client=broken) is False
    unauthorized=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(401)))
    assert probe_models('http://provider.invalid/v1','sk-synthetic-key',client=unauthorized) is False
    captured=capsys.readouterr()
    assert 'sk-synthetic-key' not in captured.out+captured.err
    assert 'sk-synthetic-key' not in ''.join(str(item) for item in (captured.out,captured.err))
