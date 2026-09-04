"""Provider circuit breaker and free /models probe. Clock is always caller-supplied."""
import httpx

_OUTCOMES=frozenset({'ok','provider_failure','other'})


def _require_now(now):
    if type(now) not in (int,float):
        raise ValueError('Invalid circuit time')


class CircuitBreaker:
    def __init__(self,threshold=3,open_seconds=60):
        if type(threshold) is not int or threshold<1:
            raise ValueError('Invalid circuit threshold')
        if type(open_seconds) not in (int,float) or open_seconds<=0:
            raise ValueError('Invalid circuit open_seconds')
        self._threshold=threshold
        self._open_seconds=open_seconds
        self.reset()

    @property
    def state(self):
        return self._state

    @property
    def consecutive_failures(self):
        return self._consecutive_failures

    def reset(self):
        self._state='closed'
        self._consecutive_failures=0
        self._opened_at=None
        self._half_open_used=False

    def record(self,outcome,now):
        _require_now(now)
        if outcome not in _OUTCOMES:
            raise ValueError('Unknown circuit outcome')
        if outcome=='ok':
            self.reset()
            return
        if outcome=='other':
            if self._state=='half_open':
                self._state='open'
                self._opened_at=now
                self._half_open_used=False
            return
        self._consecutive_failures+=1
        if self._state=='half_open' or self._consecutive_failures>=self._threshold:
            self._state='open'
            self._opened_at=now
            self._half_open_used=False

    def allow(self,now):
        _require_now(now)
        if self._state=='closed':
            return True
        if self._state=='half_open':
            if self._half_open_used:
                return False
            self._half_open_used=True
            return True
        if now<self._opened_at+self._open_seconds:
            return False
        self._state='half_open'
        self._half_open_used=True
        return True


def probe_models(base_url,api_key,*,timeout=5,client=None):
    http=client
    owned=http is None
    try:
        if owned:
            http=httpx.Client(trust_env=False)
        return http.get(base_url+'/models',headers={'Authorization':'Bearer '+api_key},timeout=timeout).status_code==200
    except Exception:
        return False
    finally:
        if owned and http is not None:
            http.close()
