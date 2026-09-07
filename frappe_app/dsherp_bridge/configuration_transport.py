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


def peer_reason(body):
    """What a paired Site said through frappe.throw, joined; '' when it said nothing usable.

    Only `_server_messages` is read: it is the one place Frappe puts a message the peer chose
    to show a person, and the peer is the Site we already share a secret with. Exception
    classes and tracebacks are never relayed - they describe the peer's internals, not the
    reason the person on this side is being refused."""
    if not isinstance(body,dict):return ''
    raw=body.get('_server_messages')
    try:items=json.loads(raw) if isinstance(raw,str) else raw
    except ValueError:return ''
    if not isinstance(items,list):return ''
    reasons=[]
    for item in items:
        try:entry=json.loads(item) if isinstance(item,str) else item
        except ValueError:continue
        text=entry.get('message') if isinstance(entry,dict) else None
        if not isinstance(text,str) or not text.strip():continue
        line=text.strip().splitlines()[0].strip()
        if line and 'Traceback' not in line:reasons.append(line[:200])
    return '；'.join(reasons)
