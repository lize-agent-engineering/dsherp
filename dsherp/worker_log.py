"""One JSON line per worker event on stderr; credential values never appear."""
import json
import sys
from datetime import datetime, timezone


def redactor(values):
    secrets=tuple(value for value in values if isinstance(value,str) and value)

    def redact(value):
        if isinstance(value,str):
            for secret in secrets:
                value=value.replace(secret,'[redacted]')
            return value
        if isinstance(value,dict):
            return {key:redact(item) for key,item in value.items()}
        if isinstance(value,(list,tuple)):
            return [redact(item) for item in value]
        return value

    return redact


_redact=redactor([])


def configure(secrets):
    global _redact
    _redact=redactor(secrets)


def log(event,**fields):
    record={'ts':datetime.now(timezone.utc).isoformat(timespec='milliseconds'),'event':event,**_redact(fields)}
    print(json.dumps(record,ensure_ascii=False,default=str),file=sys.stderr,flush=True)
