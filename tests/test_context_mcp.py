import asyncio
import json

import httpx
import pytest

from dsherp import context_mcp


def test_configuration_domain_has_only_native_configuration_read_and_proposal_tools():
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'message':{'id':'B1'}})
    with httpx.Client(base_url='http://synthetic',transport=httpx.MockTransport(handler)) as client:
        server=context_mcp.create_server(client,'R','C',domain='configuration')
        catalog=asyncio.run(server.list_tools())
        assert {tool.name for tool in catalog}=={'erp_read_configuration','erp_propose_configuration'}
        assert all(not {'site','user','url','grant','session_id','model_run'}&set(tool.inputSchema['properties']) for tool in catalog)
        asyncio.run(server.call_tool('erp_read_configuration',{'doctype':'New Inspection'}))
        asyncio.run(server.call_tool('erp_propose_configuration',{'package':{'version':1}}))
    assert calls==[{'run_id':'R','capability':'C','tool':'erp_read_configuration','arguments':{'doctype':'New Inspection'}},
                   {'run_id':'R','capability':'C','tool':'erp_propose_configuration','arguments':{'package':{'version':1}}}]


def test_operation_domain_can_propose_but_cannot_confirm_business_writes():
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'message':{'id':'P1','status':'Pending'}})
    with httpx.Client(base_url='http://synthetic',transport=httpx.MockTransport(handler)) as client:
        server=context_mcp.create_server(client,'RUN-1','CAP-1',domain='operation')
        catalog=asyncio.run(server.list_tools())
        assert {tool.name for tool in catalog}=={'erp_read_schema','erp_read_record','erp_search_records','erp_propose_update','erp_propose_create','erp_propose_action','erp_propose_fill','erp_propose_make'}
        assert all(tool.inputSchema['properties']['doctype']=={'title':'Doctype','type':'string'}
                   for tool in catalog if 'doctype' in tool.inputSchema['properties'])
        proposal=next(tool for tool in catalog if tool.name=='erp_propose_update')
        assert not {'session_id','user','site','grant','capability'} & set(proposal.inputSchema['properties'])
        make=next(tool for tool in catalog if tool.name=='erp_propose_make')
        assert set(make.inputSchema['properties'])=={'source_doctype','source_name','source_version','route'}
        assert set(make.inputSchema['required'])=={'source_doctype','source_name','source_version','route'}
        assert all(schema=={'title':field.replace('_',' ').title(),'type':'string'}
                   for field,schema in make.inputSchema['properties'].items())
        assert make.inputSchema['additionalProperties'] is False
        asyncio.run(server.call_tool('erp_propose_update',{'doctype':'Item','name':'I1','values':{'item_name':'New'},'version':'v1'}))
        asyncio.run(server.call_tool('erp_propose_create',{'doctype':'Item','values':{'item_name':'New'},'version':'schema-v1'}))
        asyncio.run(server.call_tool('erp_propose_action',{'doctype':'Sales Order','name':'SO1','action':'submit','version':'v2'}))
        asyncio.run(server.call_tool('erp_propose_make',{'source_doctype':'Sales Order','source_name':'SO1','source_version':'v2','route':'sales_order_to_delivery_note'}))
        with pytest.raises(Exception):
            asyncio.run(server.call_tool('erp_propose_make',{'source_doctype':'Sales Order','source_name':'SO1','source_version':'v2','route':'sales_order_to_delivery_note','options':{}}))
    assert calls==[{'run_id':'RUN-1','capability':'CAP-1','tool':'erp_propose_update','arguments':{'doctype':'Item','name':'I1','values':{'item_name':'New'},'version':'v1'}},
                   {'run_id':'RUN-1','capability':'CAP-1','tool':'erp_propose_create','arguments':{'doctype':'Item','values':{'item_name':'New'},'version':'schema-v1'}},
                   {'run_id':'RUN-1','capability':'CAP-1','tool':'erp_propose_action','arguments':{'doctype':'Sales Order','name':'SO1','action':'submit','version':'v2'}},
                   {'run_id':'RUN-1','capability':'CAP-1','tool':'erp_propose_make','arguments':{'source_doctype':'Sales Order','source_name':'SO1','source_version':'v2','route':'sales_order_to_delivery_note'}}]
    with pytest.raises(ValueError,match='domain'):context_mcp.create_server(None,'R','C',domain='admin')


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
        assert all(t.inputSchema['properties']['doctype']=={'title':'Doctype','type':'string'} for t in tools)
        search=next(tool for tool in tools if tool.name=='erp_search_records')
        assert set(search.inputSchema['properties'])=={'doctype','query','filters','fields'}
        assert search.inputSchema['required']==['doctype']
        assert search.inputSchema['additionalProperties'] is False
        assert search.inputSchema['properties']['filters']['anyOf']==[
            {'additionalProperties':True,'type':'object'}, {'type':'null'},
        ]
        assert search.inputSchema['properties']['fields']['anyOf']==[
            {'items':{'type':'string'},'type':'array'}, {'type':'null'},
        ]
        result=asyncio.run(server.call_tool('erp_read_record',{'doctype':'Item','name':'SYNTHETIC-ITEM'}))
        assert 'SYNTHETIC-ITEM' in str(result)
        asyncio.run(server.call_tool('erp_search_records',{
            'doctype':'Bin',
            'filters':{'item_code':['in',['DSHERP-MFG-SYN-RM']]},
            'fields':['item_code','warehouse','actual_qty','projected_qty'],
        }))
        with pytest.raises(Exception):
            asyncio.run(server.call_tool('erp_search_records',{
                'doctype':'Bin','filters':{},'fields':[],'site':'forged',
            }))
    assert calls==[
        ('/api/method/dsherp_bridge.context_execution.run_tool',{
            'run_id':'RUN-1','capability':'CAP-1','tool':'erp_read_record',
            'arguments':{'doctype':'Item','name':'SYNTHETIC-ITEM'}}),
        ('/api/method/dsherp_bridge.context_execution.run_tool',{
            'run_id':'RUN-1','capability':'CAP-1','tool':'erp_search_records',
            'arguments':{
                'doctype':'Bin','query':'',
                'filters':{'item_code':['in',['DSHERP-MFG-SYN-RM']]},
                'fields':['item_code','warehouse','actual_qty','projected_qty'],
            }}),
    ]


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


def test_post_timeout_is_transport_only_and_not_business_payload():
    seen=[]
    def handler(request):
        seen.append((json.loads(request.content),request.extensions['timeout']))
        return httpx.Response(200,json={'message':{'status':'ok'}})
    with httpx.Client(base_url='http://synthetic',transport=httpx.MockTransport(handler),timeout=30) as client:
        assert context_mcp.post(client,'record_run_event',timeout=5,run_id='RUN',events=[])=={'status':'ok'}
    assert seen[0][0]=={'run_id':'RUN','events':[]}
    assert set(seen[0][1].values())=={5.0}
