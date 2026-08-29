import httpx


def test_canonical_alpha_hostname_has_independent_login_and_origin():
    with httpx.Client(base_url='http://127.0.0.1:18082',trust_env=False,timeout=15) as client:
        headers={'Host':'dsherp-validation.localhost:18082'}
        assert client.get('/login',headers=headers).status_code==200
        params={'EIO':4,'transport':'polling'}
        response=client.get('/socket.io/',params=params,headers={**headers,'Origin':'http://dsherp-validation.localhost:18082'})
        assert response.status_code==404
        assert client.get('/socket.io/',params=params,headers={**headers,'Origin':'http://foreign.invalid'}).status_code==403
        assert client.get('/login',headers={'Host':'foreign.invalid'}).status_code==421
