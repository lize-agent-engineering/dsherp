import {expect, it} from 'vitest';
import * as context from './page-context.js';

const form = () => ({doctype:'Sales Order', doc:{doctype:'Sales Order', name:'SO-1', modified:'v1', customer:'C-1', secret:'never', items:[{name:'ROW-1',qty:2,rate:900}], __unsaved:1}, is_dirty:()=>true});
const desk = (route, extra={}, doctypes=['Item','Customer','Sales Order']) => ({
 frappe:{get_route:()=>route,boot:{dsherp_context_doctypes:doctypes}},...extra,
});
it('原生页面路由尚未就绪时明确说明上下文有限，不读取残留表单',()=>{
 expect(context.capturePageContext(desk(undefined,{cur_frm:form()}))).toEqual({schema_version:1,route:[],page_type:'unknown',reason:'页面尚未就绪，当前上下文能力有限。'});
});
it('选择目录仅包含当前表单业务字段和明确的子表列，不携带字段值',()=>{
 const frm=form();frm.meta={fields:[{fieldname:'customer',fieldtype:'Link',label:'客户'},{fieldname:'secret',fieldtype:'Password'},{fieldname:'items',fieldtype:'Table',label:'明细',options:'Sales Order Item'}]};
 const env=desk(['Form','Sales Order','SO-1'],{cur_frm:frm});
 env.frappe.get_meta=()=>({fields:[{fieldname:'qty',fieldtype:'Float',label:'数量'},{fieldname:'hidden',fieldtype:'Data',hidden:1}]});
 expect(context.contextOptions(env)).toEqual([{value:'customer',label:'客户'},{value:'items.qty',label:'明细 / 数量'}]);
 expect(context.selectedContext(['customer','items.qty'],env).unsaved).toEqual({customer:'C-1',items:[{name:'ROW-1',qty:2}]});
 expect(context.contextOptions(desk(['List','Item'],{cur_frm:frm}))).toEqual([]);
});
// Removing route matching must not attach a stale form to a different document.
it('发送时冻结表单身份和版本，默认不上传字段或子表',()=>{
  const frm=form(); const env=desk(['Form','Sales Order','SO-1'],{cur_frm:frm});
  const snap=context.capturePageContext(env);
  frm.doc.name='SO-2'; frm.doc.modified='v2';
  expect(snap).toMatchObject({page_type:'form',doctype:'Sales Order',name:'SO-1',version:'v1',dirty:true});
  expect(snap.unsaved).toBeUndefined();
  expect(JSON.stringify(snap)).not.toContain('never');
  expect(Object.isFrozen(snap)).toBe(true);
});
it('显式选择才包含未保存字段及子表列，深复制不持有表单引用',()=>{
  const frm=form();
  const snap=context.capturePageContext(desk(['Form','Sales Order','SO-1'],{cur_frm:frm}),{fields:['customer'],tables:{items:['qty']}});
  frm.doc.items[0].qty=99;
  expect(snap.unsaved).toEqual({customer:'C-1',items:[{name:'ROW-1',qty:2}]});
  expect(JSON.stringify(snap)).not.toContain('rate');
  expect(Object.isFrozen(snap.unsaved.items[0])).toBe(true);
});
it('列表只发送筛选及选中名称，不发送选中整份记录',()=>{
  const filters=[['Item','disabled','=',0]];
  const list={doctype:'Item',get_filters_for_args:()=>filters,get_checked_items:()=>['I-1','I-2']};
  const snap=context.capturePageContext(desk(['List','Item','List'],{cur_list:list}));
  filters[0][3]=1;
  expect(snap).toMatchObject({page_type:'list',doctype:'Item',filters:[['Item','disabled','=',0]],selected:['I-1','I-2']});
});
it('Supplier 表单由通用页面结构采集，不在客户端复制业务策略',()=>{
  const frm={doctype:'Supplier',doc:{doctype:'Supplier',name:'SUP-1',modified:'v1'},is_dirty:()=>false};
  expect(context.capturePageContext(desk(['Form','Supplier','SUP-1'],{cur_frm:frm},['Supplier']))).toMatchObject({
    page_type:'form',doctype:'Supplier',name:'SUP-1',version:'v1',dirty:false,
  });
});
it('Supplier 列表由通用页面结构采集，保留筛选与选中名称',()=>{
  const filters=[['Supplier','disabled','=',0]];
  const list={doctype:'Supplier',get_filters_for_args:()=>filters,get_checked_items:()=>['SUP-1']};
  expect(context.capturePageContext(desk(['List','Supplier','List'],{cur_list:list},['Supplier']))).toMatchObject({
    page_type:'list',doctype:'Supplier',filters,selected:['SUP-1'],
  });
});
it('页面分类只使用服务端策略派生的 DocType，未纳入的 Supplier 不被客户端放行',()=>{
  const frm={doctype:'Supplier',doc:{doctype:'Supplier',name:'SUP-1',modified:'v1'},is_dirty:()=>false};
  const snap=context.capturePageContext(desk(['Form','Supplier','SUP-1'],{cur_frm:frm},['Item']));
  expect(snap).toEqual({schema_version:1,route:['Form','Supplier','SUP-1'],page_type:'unknown',reason:'当前页面未纳入业务上下文策略。'});
});
it('切页后未就绪的旧 cur_frm 不成为新页面上下文',()=>{
  const snap=context.capturePageContext(desk(['Form','Customer','C-2'],{cur_frm:form()}));
  expect(snap.page_type).toBe('unknown');
  expect(snap.reason).toBeTruthy();
  expect(snap.name).toBeUndefined();
});
it('未知页面明确限制，不读取残留表单或 DOM',()=>{
  const snap=context.capturePageContext(desk(['query-report','Revenue'],{cur_frm:form()}));
  expect(snap).toMatchObject({page_type:'unknown',route:['query-report','Revenue']});
  expect(snap.doctype).toBeUndefined();
});
it('未纳入服务端业务策略的配置 Form/List 保持 unknown',()=>{
 const frm={doctype:'Custom Field',doc:{doctype:'Custom Field',name:'Item-ds_note',modified:'v1'},is_dirty:()=>false};
 const formSnap=context.capturePageContext(desk(['Form','Custom Field','Item-ds_note'],{cur_frm:frm},['Supplier']));
 const list={doctype:'Custom Field',get_filters_for_args:()=>[],get_checked_items:()=>[]};
 const listSnap=context.capturePageContext(desk(['List','Custom Field','List'],{cur_list:list},['Supplier']));
 expect(formSnap.page_type).toBe('unknown');
 expect(listSnap.page_type).toBe('unknown');
});
it('超过快照预算明确拒绝，不静默截断子表',()=>{
  const frm=form(); frm.doc.customer='x'.repeat(40000);
  expect(()=>context.capturePageContext(desk(['Form','Sales Order','SO-1'],{cur_frm:frm}),{fields:['customer']})).toThrow(/预算/);
});
it('禁止把元字段或未获明确选择的对象整体上传',()=>{
  const env=desk(['Form','Sales Order','SO-1'],{cur_frm:form()});
  expect(()=>context.capturePageContext(env,{fields:['items']})).toThrow(/子表/);
  expect(()=>context.capturePageContext(env,{fields:['__unsaved']})).toThrow(/字段/);
});
