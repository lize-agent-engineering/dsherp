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
        assert {tool.name for tool in catalog}=={
            'erp_read_configuration','erp_propose_configuration','erp_request_input'}
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
        assert {tool.name for tool in catalog}=={'erp_read_schema','erp_read_record','erp_search_records',
            'erp_propose_update','erp_propose_create','erp_propose_action','erp_propose_fill','erp_propose_make',
            'erp_request_input'}
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
        assert {t.name for t in tools}=={'erp_read_schema','erp_read_record','erp_search_records','erp_request_input'}
        assert all(not {'user','site','url','capability'} & set(t.inputSchema['properties']) for t in tools)
        assert all(t.inputSchema['properties']['doctype']=={'title':'Doctype','type':'string'}
                   for t in tools if 'doctype' in t.inputSchema['properties'])
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


@pytest.mark.parametrize('status,error_class,retryable',[
    (403,'permission',False),
    (409,'validation',False),
    (500,'transient',True),
])
def test_http_rejections_are_not_retried_or_replaced_by_empty_results(status,error_class,retryable):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(status,json={'exception':'sensitive-provider-detail'})
    with httpx.Client(base_url='http://synthetic',transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(context_mcp.ToolFailure) as error:
            context_mcp.post(client,'run_tool',run_id='RUN',capability='CAP')
        payload=json.loads(str(error.value))
        expected='业务请求失败' if error_class!='transient' else context_mcp.TRANSIENT_MESSAGE
        assert payload=={'error_class':error_class,'message':expected,'retryable':retryable}
        assert 'sensitive-provider-detail' not in str(error.value)
        assert set(error.value.classification)=={'error_class','message','retryable','http_status'}
        assert error.value.classification['http_status']==status
        assert error.value.status_code==status
        assert isinstance(error.value,context_mcp.BusinessRuntimeError)
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


def test_tool_failures_are_classified_and_serialized():
    from dsherp.context_mcp import classify_failure,ToolFailure,post
    import httpx,json
    assert classify_failure(417,{'exc_type':'ValidationError','_server_messages':json.dumps([json.dumps({'message':'库存不足'})])},None)=={'error_class':'validation','message':'库存不足','retryable':False,'http_status':417}
    assert classify_failure(403,{'exception':'frappe.exceptions.PermissionError: 无权读取'},None)['error_class']=='permission'
    assert classify_failure(503,None,None)['error_class']=='transient' and classify_failure(None,None,httpx.ReadTimeout('t'))['retryable'] is True
    def handler(request):return httpx.Response(417,json={'exc_type':'ValidationError','_server_messages':json.dumps([json.dumps({'message':'仓库不存在'})])})
    with httpx.Client(base_url='http://x',transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolFailure) as caught:post(client,'run_tool',run_id='r',capability='c',tool='erp_read_record',arguments={})
    payload=json.loads(str(caught.value))
    assert payload=={'error_class':'validation','message':'仓库不存在','retryable':False} and caught.value.status_code==417
    assert 'http_status' not in payload
    assert caught.value.classification=={'error_class':'validation','message':'仓库不存在','retryable':False,'http_status':417}
    assert isinstance(caught.value,context_mcp.BusinessRuntimeError)


def test_classify_failure_uses_status_exc_type_and_safe_messages():
    from dsherp.context_mcp import classify_failure
    assert classify_failure(400,{'exc_type':'ValidationError'},None)=={'error_class':'validation','message':'业务请求失败','retryable':False,'http_status':400}
    assert classify_failure(409,{'exception':'sensitive-provider-detail'},None)['error_class']=='validation'
    assert classify_failure(401,None,None)=={'error_class':'permission','message':'业务请求失败','retryable':False,'http_status':401}
    assert classify_failure(417,{'exc_type':'PermissionError','exception':'frappe.exceptions.PermissionError: 无权读取'},None)=={'error_class':'permission','message':'无权读取','retryable':False,'http_status':417}
    assert classify_failure(502,None,None)['retryable'] is True
    long_message='缺'*600
    classified=classify_failure(417,{'exc_type':'ValidationError','exception':'ValidationError: first\nTraceback (most recent call last):\n  File "x"\n'+long_message,'_server_messages':json.dumps([json.dumps({'message':long_message+'\nTraceback (most recent call last):'})])},None)
    assert classified['message']=='缺'*500
    assert '\n' not in classified['message'] and 'Traceback' not in classified['message']
    assert classify_failure(400,{'_server_messages':'not-json','exception':'ValidationError: 回退说明'},None)['message']=='回退说明'


def test_worker_rpc_preserves_raw_transport_errors_for_observability():
    """Only the model-facing tool path is collapsed; worker RPCs keep the httpx class for metrics."""
    def handler(request):
        raise httpx.ReadTimeout('t')
    with httpx.Client(base_url='http://synthetic',transport=httpx.MockTransport(handler)) as client:
        for method in ('claim_run','finish_run','record_run_event','run_status','worker_heartbeat'):
            with pytest.raises(httpx.ReadTimeout):
                context_mcp.post(client,method,run_id='RUN',capability='CAP')


def test_model_facing_failures_are_transient_and_never_leak_internal_text():
    """断网与 5xx 对模型都必须是结构化 transient，且服务端内部文本不得进入模型上下文。"""
    detail='[Errno 61] Connection refused to dsherp-validation-backend-1:8000'
    def offline(request):raise httpx.ConnectError(detail)
    with httpx.Client(base_url='http://synthetic',transport=httpx.MockTransport(offline)) as client:
        with pytest.raises(context_mcp.ToolFailure) as caught:
            context_mcp.post(client,'run_tool',run_id='RUN',capability='CAP',tool='erp_read_record',arguments={})
    assert json.loads(str(caught.value))=={'error_class':'transient','message':context_mcp.TRANSIENT_MESSAGE,'retryable':True}
    assert caught.value.classification['http_status'] is None and caught.value.status_code is None
    assert isinstance(caught.value,context_mcp.BusinessRuntimeError)
    assert isinstance(caught.value.__cause__,httpx.ConnectError)
    assert detail not in str(caught.value)

    leak='pymysql.err.OperationalError: (1054, "Unknown column \'ds_secret_ref\' in \'field list\'")'
    def broken(request):return httpx.Response(500,json={'exc_type':'OperationalError','exception':leak})
    with httpx.Client(base_url='http://synthetic',transport=httpx.MockTransport(broken)) as client:
        with pytest.raises(context_mcp.ToolFailure) as failed:
            context_mcp.post(client,'run_tool',run_id='RUN',capability='CAP',tool='erp_read_record',arguments={})
    serialized=str(failed.value)
    assert json.loads(serialized)=={'error_class':'transient','message':context_mcp.TRANSIENT_MESSAGE,'retryable':True}
    assert 'ds_secret_ref' not in serialized and 'Unknown column' not in serialized and '1054' not in serialized
    assert failed.value.status_code==500
    assert context_mcp.classify_failure(503,{'exception':'frappe.exceptions.SiteExpiredError: /home/frappe/sites'},None)['message']==context_mcp.TRANSIENT_MESSAGE

    business=json.dumps([json.dumps({'message':'仓库 WH-01 已停用'})])
    def rejected(request):return httpx.Response(417,json={'exc_type':'ValidationError','_server_messages':business})
    with httpx.Client(base_url='http://synthetic',transport=httpx.MockTransport(rejected)) as client:
        with pytest.raises(context_mcp.ToolFailure) as invalid:
            context_mcp.post(client,'run_tool',run_id='RUN',capability='CAP',tool='erp_read_record',arguments={})
    assert json.loads(str(invalid.value))=={'error_class':'validation','message':'仓库 WH-01 已停用','retryable':False}


def test_every_business_domain_exposes_strict_request_input_tool():
    for domain in ('query','operation','configuration'):
        server=context_mcp.create_server(None,'RUN','CAP',domain=domain)
        tool=next(item for item in asyncio.run(server.list_tools()) if item.name=='erp_request_input')
        assert tool.inputSchema['properties']=={'question':{'title':'Question','type':'string'}}
        assert tool.inputSchema['required']==['question']
        assert tool.inputSchema['additionalProperties'] is False
