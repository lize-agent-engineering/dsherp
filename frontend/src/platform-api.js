const reads = new Set(['context', 'list_tasks', 'get_task']);
export async function platformApi(method, params = {}, signal) {
  const module = method === 'context' ? 'api' : 'agent_api';
  const url = '/api/method/dsherp_platform.' + module + '.' + method;
  const options = {credentials:'same-origin', signal};
  let target = url;
  if (reads.has(method)) {
    const query = new URLSearchParams(params).toString();
    if (query) target += '?' + query;
  } else {
    if (!globalThis.frappe?.csrf_token) throw new Error('会话尚未就绪，请刷新页面后再提交。');
    options.method = 'POST';
    options.headers = {'Content-Type':'application/json', 'X-Frappe-CSRF-Token':globalThis.frappe.csrf_token};
    options.body = JSON.stringify(params);
  }
  const response = await fetch(target, options);
  if (!response.ok) {
    if ([401,403].includes(response.status)) throw new Error('当前身份或企业成员权限不足，请重新登录或联系企业管理员。');
    if (response.status === 503) throw new Error('Agent 执行服务尚未就绪，请联系管理员启动服务。');
    throw new Error(`请求未完成（HTTP ${response.status}）。请刷新查询记录核实状态，不要连续重复提交。`);
  }
  return (await response.json()).message;
}
