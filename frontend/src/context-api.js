// Identity and authorization are determined by the authenticated business Site.
const methods = {
  list_sessions:[], get_session:['session_id'],
  send_message:['session_id','question','context','request_id'],
  cancel_run:['session_id','run_id','request_id'],
};
const loginErrors=['企业成员绑定已变化，请重新登录','平台登录授权已失效','绑定的业务用户未开通或已停用','需要当前业务用户身份','平台身份不属于当前企业','平台身份响应无效'];
export async function contextApi(method,params={},signal){
  if(!Object.hasOwn(methods,method))throw new Error('不支持的会话操作');
  if(Object.keys(params).some(key=>!methods[method].includes(key)))throw new Error('会话参数不正确');
  const options={credentials:'same-origin',signal};
  let url='/api/method/dsherp_bridge.context_api.'+method;
  if(method==='list_sessions'||method==='get_session'){
    const query=new URLSearchParams(params).toString();
    if(query)url+='?'+query;
  }else{
    const csrf=globalThis.frappe?.csrf_token;
    if(!csrf)throw new Error('当前会话尚未就绪，请刷新记录后再提交');
    Object.assign(options,{method:'POST',headers:{'Content-Type':'application/json','X-Frappe-CSRF-Token':csrf},body:JSON.stringify(params)});
  }
  const response=await fetch(url,options);
  if(!response.ok){
    if([401,403].includes(response.status)){
      if(response.headers?.get('content-type')?.includes('application/json')){
        const body=await response.json();
        const reason=loginErrors.find(message=>body.exception===`frappe.exceptions.PermissionError: ${message}`);
        if(reason)throw new Error(reason);
      }
      throw new Error('当前身份或业务权限已失效，请重新登录或联系管理员');
    }
    throw new Error(`请求未完成（HTTP ${response.status}），请刷新记录核实，不要重复发送`);
  }
  const data=await response.json();
  if(!Object.hasOwn(data,'message'))throw new Error('会话响应不完整，请刷新记录核实');
  return data.message;
}
