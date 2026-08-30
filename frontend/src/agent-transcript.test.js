import {expect,it} from 'vitest';
import {buildTranscript,toolEvents,pendingCount} from './agent-transcript.js';

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
