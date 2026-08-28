// This is untrusted page context, never an identity or permission grant.
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
  let snapshot = {schema_version:1, route:[...route], page_type:'unknown', reason:'当前页面上下文能力有限；请明确说明业务对象。'};
  const frm = env.cur_frm;
  const list = env.cur_list;
  if (route[0] === 'Form' && frm?.doctype === route[1] && frm.doc?.name === route[2]) {
    snapshot = {schema_version:1,route:[...route],page_type:'form',doctype:frm.doctype,name:frm.doc.name,version:frm.doc.modified ?? null,dirty:frm.is_dirty()};
    const unsaved = {};
    for (const name of selection.fields ?? []) unsaved[name] = scalar(frm.doc, name);
    for (const [name, columns] of Object.entries(selection.tables ?? {})) {
      fieldName(name);
      if (!Array.isArray(frm.doc[name]) || !Array.isArray(columns) || !columns.length) throw new Error('子表必须明确选择列');
      unsaved[name] = frm.doc[name].map(row => Object.fromEntries(['name', ...columns].map(column=>[column,scalar(row,column)])));
    }
    if (Object.keys(unsaved).length) snapshot.unsaved = unsaved;
  } else if (route[0] === 'List' && list?.doctype === route[1]) {
    snapshot = {schema_version:1,route:[...route],page_type:'list',doctype:list.doctype,filters:list.get_filters_for_args(),selected:list.get_checked_items(true)};
  }
  const encoded = JSON.stringify(snapshot);
  if (new TextEncoder().encode(encoded).length > 32768) throw new Error('页面快照超过预算，请减少选择的字段或记录');
  return freeze(JSON.parse(encoded));
}
