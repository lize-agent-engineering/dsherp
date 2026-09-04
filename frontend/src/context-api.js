// Identity and authorization are determined by the authenticated business Site.
const methods = {
  list_sessions:[], get_session:['session_id'],
  search_sessions:['query','page','archived'],
  rename_session:['session_id','title'],
  archive_session:['session_id'], restore_session:['session_id'],
  list_pending:['page'], list_configuration_records:['page'],
  send_message:['session_id','question','context','request_id','domain'],
  cancel_run:['session_id','run_id','request_id'],
  confirm_operation:['proposal_id','digest','request_id'],
  reject_operation:['proposal_id','digest','request_id'],
  confirm_configuration:['proposal_id','digest','request_id'],
  confirm_configuration_publish:['proposal_id','digest','request_id'],
  prepare_configuration_preview:['bundle_id','digest'],
  prepare_configuration_transfer:['bundle_id','digest','request_id'],
  prepare_configuration_publish:['transfer_id','digest'],
  accept_configuration_transfer:['transfer_id'],
  verify_operation:['proposal_id'],
  verify_configuration:['proposal_id'],
  list_run_events:['run_id','page'],
};
const loginErrors=['企业成员绑定已变化，请重新登录','平台登录授权已失效','绑定的业务用户未开通或已停用','需要当前业务用户身份','平台身份不属于当前企业','平台身份响应无效'];
function requestError(status,message,kind){
  const error=new Error(message);
  error.kind=kind;
  error.httpStatus=status;
  return error;
}
function safeHttpMessage(status){
  return `请求未完成（HTTP ${status}），请刷新记录核实，不要重复发送`;
}
function firstServerMessage(body){
  try{
    const raw=body?._server_messages;
    if(raw==null)return '';
    const items=typeof raw==='string'?JSON.parse(raw):raw;
    const first=items[0];
    const payload=typeof first==='string'?JSON.parse(first):first;
    const message=payload?.message;
    if(typeof message!=='string')return '';
    const line=message.split('\n')[0].trim();
    return !line||line.includes('Traceback')?'':line;
  }catch{
    return '';
  }
}
const safeExceptionTypes=new Set(['ValidationError','frappe.ValidationError','frappe.exceptions.ValidationError']);
function reasonFromException(exception){
  if(typeof exception!=='string'||exception.includes('\n')||exception.includes('Traceback'))return '';
  const index=exception.indexOf(':');
  if(index<0)return '';
  const type=exception.slice(0,index).trim();
  const reason=exception.slice(index+1).trim();
  if(!reason||!safeExceptionTypes.has(type))return '';
  return reason;
}
function classifyKind(status,message){
  if(status===401||status===403)return 'permission';
  if(status===503&&message.includes('助手服务暂不可用'))return 'unavailable';
  if(status>=500)return 'transient';
  return 'validation';
}
async function readJson(response){
  if(!response.headers?.get('content-type')?.includes('application/json'))return null;
  try{return await response.json();}
  catch{return null;}
}
export function describeError(error){
  const kind=error?.kind;
  return {message:error instanceof Error?error.message:String(error??''),retryable:kind==='transient'||kind==='unavailable'};
}
export async function contextApi(method,params={},signal){
  if(!Object.hasOwn(methods,method))throw new Error('不支持的会话操作');
  if(Object.keys(params).some(key=>!methods[method].includes(key)))throw new Error('会话参数不正确');
  const options={credentials:'same-origin',signal};
  let url=method==='confirm_operation'?'/api/method/dsherp_bridge.operations.confirm':method==='reject_operation'?'/api/method/dsherp_bridge.operations.reject':'/api/method/dsherp_bridge.context_api.'+method;
  if(method==='verify_operation')url='/api/method/dsherp_bridge.operations.verify_execution';
  if(method==='verify_configuration')url='/api/method/dsherp_bridge.configuration_execution.verify_execution';
  if(method==='confirm_configuration')url='/api/method/dsherp_bridge.configuration_execution.confirm_preview';
  if(method==='confirm_configuration_publish')url='/api/method/dsherp_bridge.configuration_execution.confirm_publish';
  if(method==='prepare_configuration_preview')url='/api/method/dsherp_bridge.configuration_execution.prepare_preview';
  if(method==='prepare_configuration_transfer')url='/api/method/dsherp_bridge.configuration_transfer.prepare_transfer';
  if(method==='prepare_configuration_publish')url='/api/method/dsherp_bridge.configuration_execution.prepare_publish';
  if(method==='accept_configuration_transfer')url='/api/method/dsherp_bridge.configuration_transfer.accept_transfer';
  if(method==='list_sessions'||method==='get_session'||method==='search_sessions'||method==='list_pending'||method==='list_configuration_records'||method==='verify_operation'||method==='verify_configuration'||method==='list_run_events'){
    const query=new URLSearchParams(params).toString();
    if(query)url+='?'+query;
  }else{
    const csrf=globalThis.frappe?.csrf_token;
    if(!csrf)throw new Error('当前会话尚未就绪，请刷新记录后再提交');
    Object.assign(options,{method:'POST',headers:{'Content-Type':'application/json','X-Frappe-CSRF-Token':csrf},body:JSON.stringify(params)});
  }
  const response=await fetch(url,options);
  if(!response.ok){
    const body=await readJson(response);
    if([401,403].includes(response.status)){
      const reason=loginErrors.find(message=>body?.exception===`frappe.exceptions.PermissionError: ${message}`);
      throw requestError(response.status,reason||'当前身份或业务权限已失效，请重新登录或联系管理员','permission');
    }
    const message=firstServerMessage(body)||reasonFromException(body?.exception)||safeHttpMessage(response.status);
    throw requestError(response.status,message,classifyKind(response.status,message));
  }
  const data=await response.json();
  if(!Object.hasOwn(data,'message'))throw new Error('会话响应不完整，请刷新记录核实');
  return data.message;
}
export function listRunEvents(runId,page=1,signal){
  return contextApi('list_run_events',{run_id:runId,page},signal);
}
