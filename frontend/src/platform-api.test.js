import {afterEach,expect,it,vi} from 'vitest';
import {platformApi} from './platform-api.js';
afterEach(()=>vi.unstubAllGlobals());
it('企业入口与身份接口保持只读',async()=>{
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({message:{id:'TASK'}})}));
 vi.stubGlobal('fetch',fetch);vi.stubGlobal('frappe',{csrf_token:'session-csrf'});
 await platformApi('desk_entry',{enterprise:'alpha'});
 expect(fetch.mock.calls[0][0]).toBe('/api/method/dsherp_platform.api.desk_entry?enterprise=alpha');
 expect(fetch.mock.calls[0][1].method).toBeUndefined();
 await platformApi('context');expect(fetch.mock.calls[1][0]).toBe('/api/method/dsherp_platform.api.context');
});
it('服务错误不直接展示服务器原始回溯或密钥',async()=>{
 vi.stubGlobal('fetch',async()=>({ok:false,status:500,text:async()=> 'SECRET_RAW_TRACE'}));
 await expect(platformApi('get_task',{task_id:'T'})).rejects.toThrow('HTTP 500');
});
it('旧平台客户端不再提供任务提交',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);vi.stubGlobal('frappe',undefined);
 await expect(platformApi('submit_task',{})).rejects.toThrow('平台入口仅支持只读请求');
 expect(fetch).not.toHaveBeenCalled();
});
