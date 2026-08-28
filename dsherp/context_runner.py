"""Business run lifecycle around the native Agent, not a second Agent loop."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pydantic import BaseModel


class Cancelled(BaseModel):
    sessionId: str
    status: str


def monitored_run(runtime,question,session_id,status,*,poll_interval=2):
    def check():
        value=status()
        if value not in ('Running','Cancelling'):raise RuntimeError('Unexpected business run status')
        return value

    def cancel():
        result=runtime.client.request('dsherp/session/cancel',{'sessionId':session_id},response_model=Cancelled)
        if result.sessionId!=session_id or result.status!='idle':
            raise RuntimeError('Native cancellation did not settle')

    if check()=='Cancelling':return {'status':'Cancelled','answer':''}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(runtime.run,question,session_id=session_id)
        try:
            while True:
                try:
                    result=future.result(timeout=poll_interval)
                    break
                except FutureTimeout:
                    if future.done():raise
                if check()=='Cancelling':
                    cancel();future.result(timeout=5)
                    return {'status':'Cancelled','answer':''}
            if check()=='Cancelling':return {'status':'Cancelled','answer':''}
            if result.finish_reason!='completed' or not result.final_response.strip():
                raise RuntimeError('Native Agent did not complete with an answer')
            return {'status':'Succeeded','answer':result.final_response.strip()}
        except BaseException:
            cancel()
            raise
