// This is untrusted page context, never an identity or permission grant.
const scalarTypes=new Set(['Data','Link','Dynamic Link','Select','Text','Small Text','Long Text','Text Editor','Int','Float','Currency','Percent','Check','Date','Datetime','Time','Duration']);
function businessTypes(env) {
  const values=env.frappe?.boot?.dsherp_context_doctypes;
  return new Set(Array.isArray(values)?values.filter(value=>typeof value==='string'):[]);
}
// Hosts the server allows a model answer to link to. Same shape as businessTypes: a
// server-decided list, never derived from the answer text or from the page's own location.
export function linkHosts(env=globalThis) {
  const values=env.frappe?.boot?.dsherp_link_hosts;
  return Array.isArray(values)?values.filter(value=>typeof value==='string'):[];
}
export function contextOptions(env=globalThis) {
  const route=env.frappe?.get_route();const frm=env.cur_frm;
  if(route?.[0]!=='Form'||!businessTypes(env).has(route[1])||frm?.doctype!==route[1]||frm.doc?.name!==route[2]||!frm.is_dirty())return [];
  const options=[];
  for(const field of frm.meta.fields){
    if(field.hidden)continue;
    if(scalarTypes.has(field.fieldtype))options.push({value:field.fieldname,label:field.label||field.fieldname});
    if(field.fieldtype==='Table')for(const column of env.frappe.get_meta(field.options).fields){
      if(!column.hidden&&scalarTypes.has(column.fieldtype))options.push({value:field.fieldname+'.'+column.fieldname,label:(field.label||field.fieldname)+' / '+(column.label||column.fieldname)});
    }
  }
  return options;
}
export function selectedContext(keys,env=globalThis){
  const allowed=new Set(contextOptions(env).map(option=>option.value));
  const fields=[];const tables={};
  for(const key of keys){
    if(!allowed.has(key))throw new Error('所选字段已不在当前表单中，请重新选择');
    const [field,column]=key.split('.');
    if(column)(tables[field]??=[]).push(column);else fields.push(field);
  }
  return capturePageContext(env,{fields,tables});
}
function freeze(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}
function fieldName(name) {
  if (typeof name !== 'string' || !/^[a-z][a-z0-9_]*$/.test(name)) {
    throw new Error('请选择有效的业务字段');
  }
}
function scalar(doc, name) {
  fieldName(name);
  if (!Object.hasOwn(doc, name)) throw new Error('所选字段不存在：' + name);
  const value = doc[name];
  if (value !== null && typeof value === 'object') throw new Error('子表必须明确选择列，不能整体上传');
  if (!['string', 'number', 'boolean'].includes(typeof value) && value !== null) throw new Error('字段值不可序列化');
  return value;
}
export function capturePageContext(env = globalThis, selection = {}) {
  const route = env.frappe.get_route();
  if(route===undefined)return freeze({schema_version:1,route:[],page_type:'unknown',reason:'页面尚未就绪，当前上下文能力有限。'});
  let snapshot = {schema_version:1, route:[...route], page_type:'unknown', reason:'当前页面上下文能力有限；请明确说明业务对象。'};
  const frm = env.cur_frm;
  const list = env.cur_list;
  const formMatches=route[0] === 'Form' && frm?.doctype === route[1] && frm.doc?.name === route[2];
  const listMatches=route[0] === 'List' && list?.doctype === route[1];
  const types=businessTypes(env);
  if (formMatches && types.has(route[1])) {
    snapshot = {schema_version:1,route:[...route],page_type:'form',doctype:frm.doctype,name:frm.doc.name,version:frm.doc.modified ?? null,dirty:frm.is_dirty()};
    const unsaved = {};
    for (const name of selection.fields ?? []) unsaved[name] = scalar(frm.doc, name);
    for (const [name, columns] of Object.entries(selection.tables ?? {})) {
      fieldName(name);
      if (!Array.isArray(frm.doc[name]) || !Array.isArray(columns) || !columns.length) throw new Error('子表必须明确选择列');
      unsaved[name] = frm.doc[name].map(row => Object.fromEntries(['name', ...columns].map(column=>[column,scalar(row,column)])));
    }
    if (Object.keys(unsaved).length) snapshot.unsaved = unsaved;
  } else if (listMatches && types.has(route[1])) {
    snapshot = {schema_version:1,route:[...route],page_type:'list',doctype:list.doctype,filters:list.get_filters_for_args(),selected:list.get_checked_items(true)};
  } else if (formMatches || listMatches) {
    snapshot.reason='当前页面未纳入业务上下文策略。';
  }
  const encoded = JSON.stringify(snapshot);
  if (new TextEncoder().encode(encoded).length > 32768) throw new Error('页面快照超过预算，请减少选择的字段或记录');
  return freeze(JSON.parse(encoded));
}
