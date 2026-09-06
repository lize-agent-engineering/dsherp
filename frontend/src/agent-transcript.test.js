import {expect,it} from 'vitest';
import {buildTranscript,toolEvents,pendingCount,runEventRows} from './agent-transcript.js';

const run = (id, extra={}) => ({id,question:'问题 '+id,answer:'回答 '+id,status:'Succeeded',context:{page_type:'unknown'},sources:[],...extra});

it('把工具读取翻译成可读事件，未知工具保留原名', () => {
 const events = toolEvents([
  {tool:'erp_read_record',arguments:{doctype:'Item',name:'I-1'},fields:['item_name','item_code'],records:['I-1']},
  {tool:'erp_read_schema',arguments:{doctype:'Sales Order'},fields:['a','b','c'],records:[]},
  {tool:'erp_search_records',arguments:{doctype:'Customer',query:'甲'},fields:[],records:['C-1','C-2']},
  {tool:'erp_read_configuration',arguments:{doctype:'Inspection'},modules:[],roles:[]},
  {tool:'erp_future_tool',arguments:{doctype:'Item'},fields:[],records:[]},
 ]);
 expect(events.map(e=>e.label)).toEqual([
  '读取 Item / I-1',
  '读取 Sales Order 结构',
  '搜索 Customer：甲',
  '读取配置 Inspection',
  'erp_future_tool',
 ]);
 expect(events[0].detail).toBe('2 个字段 · 1 条记录');
 expect(events[2].detail).toBe('2 条记录');
});

it('提案按产生它的运行归位，而不是堆在对话末尾', () => {
 const session = {
  messages:[run('R-1'),run('R-2')],
  proposals:[{id:'P-2',model_run:'R-2'},{id:'P-1',model_run:'R-1'}],
  configuration_bundles:[],
  configuration_confirmations:[],
 };
 const {turns,loose} = buildTranscript(session);
 expect(turns).toHaveLength(2);
 expect(turns[0].proposals.map(p=>p.id)).toEqual(['P-1']);
 expect(turns[1].proposals.map(p=>p.id)).toEqual(['P-2']);
 expect(loose.proposals).toEqual([]);
});

it('配置确认跟随所属配置包的运行', () => {
 const session = {
  messages:[run('R-1'),run('R-2')],
  proposals:[],
  configuration_bundles:[{id:'B-1',model_run:'R-2'}],
  configuration_confirmations:[{id:'C-1',bundle_id:'B-1'}],
 };
 const {turns} = buildTranscript(session);
 expect(turns[1].bundles.map(b=>b.id)).toEqual(['B-1']);
 expect(turns[1].confirmations.map(c=>c.id)).toEqual(['C-1']);
 expect(turns[0].bundles).toEqual([]);
});

it('归属不明的条目单独列出，不被静默丢弃也不假装属于某一轮', () => {
 const session = {
  messages:[run('R-1')],
  proposals:[{id:'P-9',model_run:'R-missing'},{id:'P-8'}],
  configuration_bundles:[],
  configuration_confirmations:[{id:'C-9',bundle_id:'B-missing'}],
 };
 const {turns,loose} = buildTranscript(session);
 expect(turns[0].proposals).toEqual([]);
 expect(loose.proposals.map(p=>p.id)).toEqual(['P-9','P-8']);
 expect(loose.confirmations.map(c=>c.id)).toEqual(['C-9']);
});

it('工具事件跟随各自的消息', () => {
 const session = {
  messages:[
   run('R-1',{sources:[{tool:'erp_read_record',arguments:{doctype:'Item',name:'I-1'},fields:['a'],records:['I-1']}]}),
   run('R-2',{sources:[]}),
  ],
  proposals:[],configuration_bundles:[],configuration_confirmations:[],
 };
 const {turns} = buildTranscript(session);
 expect(turns[0].tools).toHaveLength(1);
 expect(turns[0].tools[0].label).toBe('读取 Item / I-1');
 expect(turns[1].tools).toEqual([]);
});

it('待确认计数只算真正待确认的条目', () => {
 expect(pendingCount({
  proposals:[{status:'Pending'},{status:'Succeeded'}],
  configuration_confirmations:[{status:'Pending'},{status:'Failed'}],
 })).toBe(2);
 expect(pendingCount(null)).toBe(0);
});

it('空会话不会崩', () => {
 expect(buildTranscript(null)).toEqual({turns:[],loose:{proposals:[],bundles:[],confirmations:[]}});
});

it('工具事件保留参数、字段、记录与版本，供链路详情逐条展开', () => {
 const [event] = toolEvents([
  {tool:'erp_read_record',arguments:{doctype:'Item',name:'I-1'},fields:['item_name','item_code'],records:['I-1'],
   record_versions:{'I-1':'2026-08-29 03:26:23'}},
 ]);
 expect(event.arguments).toEqual({doctype:'Item',name:'I-1'});
 expect(event.fields).toEqual(['item_name','item_code']);
 expect(event.records).toEqual(['I-1']);
 expect(event.recordVersions).toEqual({'I-1':'2026-08-29 03:26:23'});
 expect(event.step).toBe(1);
});

it('结构读取带回 schema 版本原始字段，步骤按服务端记录顺序编号', () => {
 // 版本如何措辞属于展示层；数据层只透传服务端字段，不拼展示串。
 const events = toolEvents([
  {tool:'erp_read_schema',arguments:{doctype:'Item'},fields:['a'],records:[],schema_version:'2026-08-01 00:00:00'},
  {tool:'erp_search_records',arguments:{doctype:'Item',query:'合成'},fields:[],records:['I-1']},
 ]);
 expect(events.map(e=>e.step)).toEqual([1,2]);
 expect(events[0].schemaVersion).toBe('2026-08-01 00:00:00');
 expect(events[1].schemaVersion).toBeNull();
});

it('配置读取事件带回模块、角色与配置基线，而不是只剩参数', () => {
 // 服务端为 erp_read_configuration 记录的是 modules/roles/exists/version/
 // configuration_revision（无 fields/records），这些同样要能被链路展示。
 const [event] = toolEvents([
  {tool:'erp_read_configuration',arguments:{doctype:'Item'},modules:['stock','manufacturing'],roles:['Item Manager'],
   exists:true,version:'2026-08-29 03:00:00',configuration_revision:'r-9'},
 ]);
 expect(event.modules).toEqual(['stock','manufacturing']);
 expect(event.roles).toEqual(['Item Manager']);
 expect(event.exists).toBe(true);
 expect(event.configVersion).toBe('2026-08-29 03:00:00');
 expect(event.configRevision).toBe('r-9');
 expect(event.detail).toBe('2 个模块 · 1 个角色');
});

it('运行事件逐类翻译且错误类使用 danger 语气', () => {
 const events = [
  ['queued',{}],['claimed',{}],['runtime_started',{}],['model_call_reserved',{model_calls:2}],
  ['model_response',{}],['model_error',{}],['runtime_tool_call',{name:'erp_read_record'}],
  ['tool_call',{tool:'erp_read_record',duration_ms:7}],['tool_result',{}],['tool_error',{}],
  ['compaction',{}],['turn_end',{reason:'completed'}],['runtime_failed',{},'RuntimeError'],
  ['container_finished',{status:'Succeeded'}],['finished',{status:'Succeeded'}],['expired',{}],
  ['cancel_requested',{}],['worker_error',{}],['future_kind',{}],
 ].map(([kind,payload,error_class],index)=>({seq:index+1,kind,payload,error_class,recorded_at:'2026-09-03 10:00:00'}));
 const rows=runEventRows(events);
 expect(rows.map(row=>row.label)).toEqual([
  '已排队','已领取','运行时启动','模型调用 #2','模型返回','模型错误','调用工具 erp_read_record',
  '服务端执行 erp_read_record（7 ms）','工具返回','工具错误','上下文压缩','回合结束（completed）',
  '运行失败（RuntimeError）','容器结束（Succeeded）','运行结束（Succeeded）','运行过期','已请求取消',
  'worker 错误','future_kind',
 ]);
 expect(rows.filter(row=>row.tone==='danger').map(row=>row.seq)).toEqual([6,10,13,18]);
 expect(rows[0]).toMatchObject({seq:1,time:'2026-09-03 10:00:00'});
});

it('新增运行事件按精确标签翻译，未核实完成自述用 danger', () => {
 const events = [
  ['lease_renewed',{}],['needs_input',{}],['proposal_rejected',{}],
  ['proposal_expired',{}],['unverified_completion_claim',{}],
 ].map(([kind,payload],index)=>({seq:index+1,kind,payload,recorded_at:'2026-09-04 12:00:00'}));
 const rows=runEventRows(events);
 expect(rows.map(row=>row.label)).toEqual([
  '租约续期','请求用户补充','提案已拒绝','提案已过期','完成自述未经核实',
 ]);
 expect(rows.map(row=>row.tone)).toEqual(['default','default','default','default','danger']);
});

it('运行失败事件只显示错误类别与可读原因，不渲染原始 payload', () => {
 const rows = runEventRows([
  {seq:1,kind:'runtime_failed',source:'runner',error_class:'RuntimeError',payload:{reason:'runtime_error'},recorded_at:'2026-09-05 10:00:00'},
  {seq:2,kind:'runtime_failed',source:'runner',error_class:'RuntimeError',payload:{reason:'run_total_exceeded'},recorded_at:'2026-09-05 10:00:01'},
 ]);
 expect(rows[0].detail).toBe('RuntimeError 运行时错误');
 expect(rows[1].detail).toBe('RuntimeError 超过运行时长预算');
 expect(rows.map(r=>r.detail).join(' ')).not.toContain('{');
});

it('领取未确认的过期原因不再声称已退回排队，服务端拒绝工具调用有自己的标签', () => {
 const rows = runEventRows([
  {seq:1,kind:'expired',source:'server',payload:{reason:'claim_unacked'},recorded_at:'2026-09-06 10:00:00'},
  {seq:2,kind:'tool_refused',source:'server',error_class:'PermissionError',payload:{tool:'erp_read_record',reason:'无权读取'},recorded_at:'2026-09-06 10:00:01'},
 ]);
 expect(rows[0].label).toBe('运行过期');
 expect(rows[0].detail).toBe('领取未确认，运行未开始，请重试');
 expect(rows[0].detail).not.toContain('退回');
 expect(rows[1].label).toBe('服务端拒绝 erp_read_record');
 expect(rows[1].tone).toBe('danger');
 expect(rows[1].detail).toContain('无权读取');
});
