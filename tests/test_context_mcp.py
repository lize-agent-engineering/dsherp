import asyncio
import json

import httpx
import pytest

from dsherp import context_mcp


def test_tools_send_only_bound_run_capability_and_named_arguments():
    calls=[]
    def handler(request):
        calls.append((request.url.path,json.loads(request.content)))
        return httpx.Response(200,json={'message':{'name':'SYNTHETIC-ITEM'}})
    with httpx.Client(base_url='http://synthetic',transport=httpx.MockTransport(handler)) as client:
        server=context_mcp.create_server(client,'RUN-1','CAP-1')
        tools=asyncio.run(server.list_tools())
        assert {t.name for t in tools}=={'erp_read_schema','erp_read_record','erp_search_records'}
        assert all(not {'user','site','url','capability'} & set(t.inputSchema['properties']) for t in tools)
        result=asyncio.run(server.call_tool('erp_read_record',{'doctype':'Item','name':'SYNTHETIC-ITEM'}))
        assert 'SYNTHETIC-ITEM' in str(result)
    assert calls==[('/api/method/dsherp_bridge.context_execution.run_tool',{
        'run_id':'RUN-1','capability':'CAP-1','tool':'erp_read_record','arguments':{'doctype':'Item','name':'SYNTHETIC-ITEM'}})]


@pytest.mark.parametrize('status',[403,409,500])
def test_http_rejections_are_not_retried_or_replaced_by_empty_results(status):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(status,json={'exception':'sensitive-provider-detail'})
    with httpx.Client(base_url='http://synthetic',transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError,match=f'HTTP {status}') as error:
            context_mcp.post(client,'run_tool',run_id='RUN',capability='CAP')
        assert 'sensitive-provider-detail' not in str(error.value)
    assert len(calls)==1
