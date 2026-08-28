import {afterEach,expect,it,vi} from 'vitest';
import {platformApi} from './platform-api.js';
afterEach(()=>vi.unstubAllGlobals());
it('任务提交使用原生 CSRF 与 POST，身份接口保持只读',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{id:'TASK'}})}));
 vi.stubGlobal('fetch',fetch);vi.stubGlobal('frappe',{csrf_token:'session-csrf'});
 await platformApi('submit_task',{enterprise:'alpha',question:'物料',request_id:'request'});
 expect(fetch.mock.calls[0][0]).toBe('/api/method/dsherp_platform.agent_api.submit_task');
 expect(fetch.mock.calls[0][1]).toMatchObject({method:'POST',credentials:'same-origin',headers:{'X-Frappe-CSRF-Token':'session-csrf','Content-Type':'application/json'}});
 await platformApi('context');expect(fetch.mock.calls[1][0]).toBe('/api/method/dsherp_platform.api.context');
});
it('服务错误不直接展示服务器原始回溯或密钥',async()=>{
 vi.stubGlobal('fetch',async()=>({ok:false,status:500,text:async()=> 'SECRET_RAW_TRACE'}));
 await expect(platformApi('get_task',{task_id:'T'})).rejects.toThrow('HTTP 500');
});
it('未初始化会话令牌时不发送写请求',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);vi.stubGlobal('frappe',undefined);
 await expect(platformApi('submit_task',{})).rejects.toThrow('会话尚未就绪');
 expect(fetch).not.toHaveBeenCalled();
});
