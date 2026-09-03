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
