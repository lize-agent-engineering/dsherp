import {afterEach,expect,it,vi} from 'vitest';
import * as api from './context-api.js';
afterEach(()=>vi.unstubAllGlobals());
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
