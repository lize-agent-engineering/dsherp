"""Authenticated data envelopes for a configured pair of business Sites."""
import hashlib
import hmac
import json
import time


def _encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def seal(payload,secret,purpose,*,now=None):
    if not isinstance(secret,str) or not secret:raise ValueError('Missing configuration transport key')
    body={'purpose':purpose,'issued_at':int(time.time() if now is None else now),'payload':json.loads(_encode(payload))}
    return {**body,'signature':hmac.new(secret.encode(),_encode(body),hashlib.sha256).hexdigest()}


def open_envelope(packet,secret,purpose,*,now=None):
    if not isinstance(packet,dict) or set(packet)!={'purpose','issued_at','payload','signature'}:
        raise ValueError('Invalid configuration transport envelope')
    body={key:packet[key] for key in ('purpose','issued_at','payload')}
    if type(body['issued_at']) is not int or body['purpose']!=purpose or not isinstance(packet['signature'],str):
        raise ValueError('Invalid configuration transport binding')
    elapsed=(time.time() if now is None else now)-body['issued_at']
    if not 0<=elapsed<=60:raise ValueError('Configuration transport envelope expired')
    expected=seal(body['payload'],secret,purpose,now=body['issued_at'])['signature']
    if not hmac.compare_digest(packet['signature'],expected):raise ValueError('Configuration transport signature mismatch')
    return json.loads(_encode(body['payload']))
