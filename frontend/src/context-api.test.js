import {afterEach,expect,it,vi} from 'vitest';
import * as api from './context-api.js';
afterEach(()=>vi.unstubAllGlobals());
it('准备预览确认仅提交配置包身份，不执行配置',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{id:'C1'}})}));vi.stubGlobal('fetch',fetch);vi.stubGlobal('frappe',{csrf_token:'test-csrf'});
 await api.contextApi('prepare_configuration_preview',{bundle_id:'B1',digest:'b1'});
 expect(fetch.mock.calls[0][0]).toBe('/api/method/dsherp_bridge.configuration_execution.prepare_preview');
 expect(fetch.mock.calls[0][1].method).toBe('POST');
});
it('交接接口不接受站点、身份、URL或配置正文',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{id:'T1'}})}));vi.stubGlobal('fetch',fetch);vi.stubGlobal('frappe',{csrf_token:'test-csrf'});
 await api.contextApi('prepare_configuration_transfer',{bundle_id:'B1',digest:'d1',request_id:'r1'});
 expect(fetch.mock.calls[0][0]).toBe('/api/method/dsherp_bridge.configuration_transfer.prepare_transfer');
 await api.contextApi('accept_configuration_transfer',{transfer_id:'T1'});
 expect(fetch.mock.calls[1][0]).toBe('/api/method/dsherp_bridge.configuration_transfer.accept_transfer');
 await expect(api.contextApi('accept_configuration_transfer',{transfer_id:'T1',source_url:'http://other'})).rejects.toThrow(/参数/);
});
it('配置确认只发送同源冻结绑定与CSRF，不接受目标或配置正文',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{status:'Succeeded'}})}));vi.stubGlobal('fetch',fetch);
 vi.stubGlobal('frappe',{csrf_token:'test-csrf'});
 const params={proposal_id:'C1',digest:'d1',request_id:'r1'};
 await api.contextApi('confirm_configuration',params);
 expect(fetch.mock.calls[0][0]).toBe('/api/method/dsherp_bridge.configuration_execution.confirm_preview');
 expect(fetch.mock.calls[0][1]).toMatchObject({method:'POST',body:JSON.stringify(params),headers:{'X-Frappe-CSRF-Token':'test-csrf'}});
 await expect(api.contextApi('confirm_configuration',{...params,target:'other-site'})).rejects.toThrow(/参数/);
});
it('发布准备和发布确认使用独立端点',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{id:'C2'}})}));vi.stubGlobal('fetch',fetch);vi.stubGlobal('frappe',{csrf_token:'test-csrf'});
 await api.contextApi('prepare_configuration_publish',{transfer_id:'T1',digest:'d1'});
 await api.contextApi('confirm_configuration_publish',{proposal_id:'C2',digest:'d2',request_id:'r2'});
 expect(fetch.mock.calls[0][0]).toBe('/api/method/dsherp_bridge.configuration_execution.prepare_publish');
 expect(fetch.mock.calls[1][0]).toBe('/api/method/dsherp_bridge.configuration_execution.confirm_publish');
});
it('配置结果核实只使用同源GET且不发送重放请求',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{observations:[]}})}));vi.stubGlobal('fetch',fetch);
 await api.contextApi('verify_configuration',{proposal_id:'C1'});
 expect(fetch.mock.calls[0][0]).toBe('/api/method/dsherp_bridge.configuration_execution.verify_execution?proposal_id=C1');
 expect(fetch.mock.calls[0][1].method).toBeUndefined();
});
it('结果核实使用同源只读GET，不发送确认或重放请求',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{observed:null}})}));vi.stubGlobal('fetch',fetch);
 await api.contextApi('verify_operation',{proposal_id:'P1'});
 expect(fetch.mock.calls[0][0]).toBe('/api/method/dsherp_bridge.operations.verify_execution?proposal_id=P1');
 expect(fetch.mock.calls[0][1].method).toBeUndefined();
});
it('操作确认仅向同源原生执行端点发送提案绑定和 CSRF',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{status:'Succeeded'}})}));vi.stubGlobal('fetch',fetch);
 vi.stubGlobal('frappe',{csrf_token:'test-csrf'});
 const params={proposal_id:'P1',digest:'d1',request_id:'r1'};
 await api.contextApi('confirm_operation',params);
 expect(fetch.mock.calls[0][0]).toBe('/api/method/dsherp_bridge.operations.confirm');
 expect(fetch.mock.calls[0][1]).toMatchObject({method:'POST',body:JSON.stringify(params),headers:{'X-Frappe-CSRF-Token':'test-csrf'}});
 await expect(api.contextApi('confirm_operation',{...params,values:{item_name:'forged'}})).rejects.toThrow(/参数/);
});
it('操作拒绝仅向同源拒绝端点 POST 提案绑定和 CSRF，不接受正文、身份或 URL',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{status:'Rejected'}})}));vi.stubGlobal('fetch',fetch);
 vi.stubGlobal('frappe',{csrf_token:'test-csrf'});
 const params={proposal_id:'P1',digest:'d1',request_id:'r1'};
 await api.contextApi('reject_operation',params);
 expect(fetch.mock.calls[0][0]).toBe('/api/method/dsherp_bridge.operations.reject');
 expect(fetch.mock.calls[0][1]).toMatchObject({method:'POST',credentials:'same-origin',body:JSON.stringify(params),headers:{'Content-Type':'application/json','X-Frappe-CSRF-Token':'test-csrf'}});
 await expect(api.contextApi('reject_operation',{...params,values:{item_name:'forged'}})).rejects.toThrow(/参数/);
 await expect(api.contextApi('reject_operation',{...params,user:'Administrator'})).rejects.toThrow(/参数/);
 await expect(api.contextApi('reject_operation',{...params,url:'https://elsewhere'})).rejects.toThrow(/参数/);
 expect(fetch).toHaveBeenCalledTimes(1);
});
it('只展示明确列举的登录失效原因，不泄漏服务器回溯',async()=>{
 vi.stubGlobal('fetch',async()=>({ok:false,status:403,headers:new Headers({'Content-Type':'application/json'}),json:async()=>({exception:'frappe.exceptions.PermissionError: 企业成员绑定已变化，请重新登录',exc:'PRIVATE TRACE'})}));
 await expect(api.contextApi('list_sessions')).rejects.toThrow('企业成员绑定已变化，请重新登录');
 vi.stubGlobal('fetch',async()=>({ok:false,status:403,headers:new Headers({'Content-Type':'application/json'}),json:async()=>({exception:'private-detail',exc:'PRIVATE TRACE'})}));
 await expect(api.contextApi('list_sessions')).rejects.toThrow('当前身份或业务权限已失效');
});
it('业务会话走当前 Site 同源 GET，不接受模型指定 URL 或身份',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:[]})}));vi.stubGlobal('fetch',fetch);
 expect(await api.contextApi('list_sessions')).toEqual([]);
 expect(fetch.mock.calls[0][0]).toBe('/api/method/dsherp_bridge.context_api.list_sessions');
 expect(fetch.mock.calls[0][1].credentials).toBe('same-origin');
 await expect(api.contextApi('https://elsewhere')).rejects.toThrow(/不支持/);
 await expect(api.contextApi('get_session',{session_id:'S-1',user:'Administrator'})).rejects.toThrow(/参数/);
});
it('正式会话管理接口固定同源方法与参数',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{items:[]}})}));vi.stubGlobal('fetch',fetch);vi.stubGlobal('frappe',{csrf_token:'test-csrf'});
 await api.contextApi('search_sessions',{query:'物料',page:2,archived:0});
 await api.contextApi('rename_session',{session_id:'S-1',title:'新标题'});
 await api.contextApi('archive_session',{session_id:'S-1'});
 await api.contextApi('restore_session',{session_id:'S-1'});
 expect(fetch.mock.calls[0][0]).toBe('/api/method/dsherp_bridge.context_api.search_sessions?query=%E7%89%A9%E6%96%99&page=2&archived=0');
 expect(fetch.mock.calls.slice(1).every(call=>call[1].method==='POST')).toBe(true);
 await expect(api.contextApi('archive_session',{session_id:'S-1',user:'Administrator'})).rejects.toThrow(/参数/);
});
it('工作台摘要列表只使用同源分页 GET',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{items:[]}})}));vi.stubGlobal('fetch',fetch);
 for(const method of ['list_pending','list_configuration_records'])await api.contextApi(method,{page:1});
 expect(fetch.mock.calls.map(call=>call[0])).toEqual([
  '/api/method/dsherp_bridge.context_api.list_pending?page=1',
  '/api/method/dsherp_bridge.context_api.list_configuration_records?page=1',
 ]);
 // list_execution_records 仍是后端端点，但 UI 已无入口，客户端不再映射它。
 await expect(api.contextApi('list_execution_records',{page:1})).rejects.toThrow();
 expect(fetch.mock.calls.every(call=>call[1].method===undefined)).toBe(true);
});
it('运行事件接口只使用同源 GET 与固定分页参数',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{run_id:'M-1',events:[]}})}));vi.stubGlobal('fetch',fetch);
 expect(await api.listRunEvents('M-1',2)).toEqual({run_id:'M-1',events:[]});
 expect(fetch.mock.calls[0][0]).toBe('/api/method/dsherp_bridge.context_api.list_run_events?run_id=M-1&page=2');
 expect(fetch.mock.calls[0][1].method).toBeUndefined();
});
it('发送必须使用 CSRF、POST 和完整快照；缺少 CSRF 不请求',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{id:'S-1'}})}));vi.stubGlobal('fetch',fetch);
 vi.stubGlobal('frappe',{});
 const params={session_id:null,question:'问题',context:{page_type:'unknown'},request_id:'request'};
 await expect(api.contextApi('send_message',params)).rejects.toThrow(/会话/);
 expect(fetch).not.toHaveBeenCalled();
 vi.stubGlobal('frappe',{csrf_token:'test-csrf'});
 await api.contextApi('send_message',params);
 expect(fetch.mock.calls[0][1]).toMatchObject({method:'POST',headers:{'X-Frappe-CSRF-Token':'test-csrf'},body:JSON.stringify(params)});
});
it('权限拒绝和响应不明明确报错，不重试写请求',async()=>{
 const fetch=vi.fn(async()=>({ok:false,status:403}));vi.stubGlobal('fetch',fetch);
 await expect(api.contextApi('get_session',{session_id:'S-1'})).rejects.toThrow(/权限/);
 expect(fetch).toHaveBeenCalledTimes(1);
});
function jsonError(status,body){
 return {ok:false,status,headers:new Headers({'Content-Type':'application/json'}),json:async()=>body};
}
it('417 业务校验透传首条服务端原因，并标记 validation',async()=>{
 const fetch=vi.fn(async()=>jsonError(417,{exc_type:'ValidationError',exc:'PRIVATE TRACE',_server_messages:JSON.stringify([JSON.stringify({message:'库存不足'}),JSON.stringify({message:'第二条不应展示'})])}));
 vi.stubGlobal('fetch',fetch);
 await expect(api.contextApi('list_sessions')).rejects.toMatchObject({message:'库存不足',kind:'validation',httpStatus:417});
 expect(fetch).toHaveBeenCalledTimes(1);
});
it('503 保留助手不可用原因并标记 unavailable',async()=>{
 vi.stubGlobal('fetch',async()=>jsonError(503,{_server_messages:JSON.stringify([JSON.stringify({message:'助手服务暂不可用'})])}));
 await expect(api.contextApi('list_sessions')).rejects.toMatchObject({message:'助手服务暂不可用',kind:'unavailable',httpStatus:503});
});
it('无 JSON 或 JSON 解析失败的 502 保留安全文案并标记 transient',async()=>{
 const fetch=vi.fn(async()=>({ok:false,status:502}));vi.stubGlobal('fetch',fetch);
 await expect(api.contextApi('list_sessions')).rejects.toMatchObject({message:'请求未完成（HTTP 502），请刷新记录核实，不要重复发送',kind:'transient',httpStatus:502});
 expect(fetch).toHaveBeenCalledTimes(1);
 vi.stubGlobal('fetch',async()=>({ok:false,status:502,headers:new Headers({'Content-Type':'application/json'}),json:async()=>{throw new SyntaxError('bad json');}}));
 await expect(api.contextApi('list_sessions')).rejects.toMatchObject({message:'请求未完成（HTTP 502），请刷新记录核实，不要重复发送',kind:'transient',httpStatus:502});
});
it('401 与 403 保持既有文案并附 permission 与 httpStatus',async()=>{
 vi.stubGlobal('fetch',async()=>jsonError(403,{exception:'frappe.exceptions.PermissionError: 企业成员绑定已变化，请重新登录',exc:'PRIVATE TRACE'}));
 await expect(api.contextApi('list_sessions')).rejects.toMatchObject({message:'企业成员绑定已变化，请重新登录',kind:'permission',httpStatus:403});
 vi.stubGlobal('fetch',async()=>({ok:false,status:401}));
 await expect(api.contextApi('list_sessions')).rejects.toMatchObject({message:'当前身份或业务权限已失效，请重新登录或联系管理员',kind:'permission',httpStatus:401});
});
it('错误解析只取安全业务原因，失败则回退 HTTP 文案',async()=>{
 vi.stubGlobal('fetch',async()=>jsonError(417,{exception:'frappe.exceptions.ValidationError: 仓库不存在',exc:'PRIVATE TRACE'}));
 await expect(api.contextApi('list_sessions')).rejects.toMatchObject({message:'仓库不存在',kind:'validation',httpStatus:417});
 vi.stubGlobal('fetch',async()=>jsonError(417,{_server_messages:'not-json',exception:'private-detail',exc:'Traceback (most recent call last):\nSECRET'}));
 const failed=await api.contextApi('list_sessions').then(()=>{throw new Error('expected reject');},caught=>caught);
 expect(failed).toMatchObject({message:'请求未完成（HTTP 417），请刷新记录核实，不要重复发送',kind:'validation',httpStatus:417});
 expect(failed.message).not.toMatch(/private-detail|SECRET|Traceback|PRIVATE/);
});
it('417 的 RuntimeError exception 无有效 _server_messages 时回退安全 HTTP 文案',async()=>{
 vi.stubGlobal('fetch',async()=>jsonError(417,{exception:'RuntimeError: PRIVATE SECRET'}));
 const failed=await api.contextApi('list_sessions').then(()=>{throw new Error('expected reject');},caught=>caught);
 expect(failed).toMatchObject({message:'请求未完成（HTTP 417），请刷新记录核实，不要重复发送',kind:'validation',httpStatus:417});
 expect(failed.message).not.toMatch(/PRIVATE|SECRET/);
});
it('describeError 只把 transient 与 unavailable 标为可重试',()=>{
 expect(api.describeError(Object.assign(new Error('库存不足'),{kind:'validation',httpStatus:417}))).toMatchObject({message:'库存不足',retryable:false});
 expect(api.describeError(Object.assign(new Error('权限'),{kind:'permission',httpStatus:403}))).toMatchObject({message:'权限',retryable:false});
 expect(api.describeError(Object.assign(new Error('请求未完成（HTTP 502），请刷新记录核实，不要重复发送'),{kind:'transient',httpStatus:502}))).toMatchObject({message:'请求未完成（HTTP 502），请刷新记录核实，不要重复发送',retryable:true});
 expect(api.describeError(Object.assign(new Error('助手服务暂不可用'),{kind:'unavailable',httpStatus:503}))).toMatchObject({message:'助手服务暂不可用',retryable:true});
 expect(api.describeError(new Error('未知'))).toMatchObject({message:'未知',retryable:false});
});
it('fetch 网络中断只抛安全可重试错误，不泄漏原异常且不重试',async()=>{
 const fetch=vi.fn(async()=>{throw new TypeError('PRIVATE Failed to fetch');});
 vi.stubGlobal('fetch',fetch);
 const failed=await api.contextApi('list_sessions').then(()=>{throw new Error('expected reject');},caught=>caught);
 expect(failed).toMatchObject({message:'网络连接中断，请检查连接后重试',kind:'transient',httpStatus:null});
 expect(failed).toBeInstanceOf(Error);
 expect(failed.name).toBe('Error');
 expect(failed.message).not.toMatch(/PRIVATE|Failed to fetch|TypeError/);
 expect(String(failed)).not.toMatch(/PRIVATE|Failed to fetch/);
 expect(api.describeError(failed)).toMatchObject({message:'网络连接中断，请检查连接后重试',retryable:true});
 expect(fetch).toHaveBeenCalledTimes(1);
});
it('主动取消不伪装成网络故障',async()=>{
 const aborted=Object.assign(new Error('aborted'),{name:'AbortError'});
 const fetch=vi.fn(async()=>{throw aborted;});
 vi.stubGlobal('fetch',fetch);
 await expect(api.contextApi('list_sessions')).rejects.toBe(aborted);
 expect(fetch).toHaveBeenCalledTimes(1);
 const controller=new AbortController();
 controller.abort();
 const transport=new TypeError('PRIVATE Failed to fetch');
 vi.stubGlobal('fetch',vi.fn(async()=>{throw transport;}));
 await expect(api.contextApi('list_sessions',{},controller.signal)).rejects.toBe(transport);
});
