// Applies an already server-authorized fill proposal to the native draft only.
// This client check prevents accidental overwrites; it is not authorization.
export async function applyFormProposal(proposal,env=globalThis){
 const frm=env.cur_frm;
 const route=env.frappe.get_route();
 if(proposal.action!=='fill')throw new Error('不是表单填入提案');
 if(JSON.stringify(route)!==JSON.stringify(['Form',proposal.doctype,proposal.name])||frm?.doctype!==proposal.doctype||frm.doc?.name!==proposal.name||frm.doc.modified!==proposal.version){
  throw new Error('当前表单或版本已变化，请重新提出建议');
 }
 const values={};const rowUpdates=[];const seen=new Set();
 for(const change of proposal.changes){
  const field=frm.meta.fields.find(field=>field.fieldname===change.field);
  if(!field||field.read_only||seen.has(change.field))throw new Error('填入字段无效：'+change.field);
  seen.add(change.field);
  if(field.fieldtype==='Table'){
   const current=frm.doc[change.field];const original=change.before;
   if(!Array.isArray(current)||!Array.isArray(original)||!Array.isArray(change.after)||JSON.stringify(current.map(row=>row.name))!==JSON.stringify(original.map(row=>row.name))||JSON.stringify(change.after.map(row=>row.name))!==JSON.stringify(original.map(row=>row.name)))throw new Error('子表行集合或顺序已变化，请重新提出建议');
   const provided=new Map((change.form_before??[]).map(row=>[row.name,row]));
   const meta=env.frappe.get_meta(field.options);
   for(let index=0;index<current.length;index++){
    const row=current[index];const before={...original[index],...provided.get(row.name)};
    for(const [key,value] of Object.entries(before))if(JSON.stringify(row[key]??null)!==JSON.stringify(value??null))throw new Error('目标子表字段已变化：'+key);
    const edits={};
    for(const [key,value] of Object.entries(change.after[index])){
     if(key==='name')continue;
     const column=meta.fields.find(column=>column.fieldname===key);
     if(!column||column.read_only||['Table','Table MultiSelect','Password'].includes(column.fieldtype)||value!==null&&typeof value==='object')throw new Error('填入子表字段无效：'+key);
     edits[key]=value;
    }
    if(Object.keys(edits).length)rowUpdates.push({row,doctype:field.options,edits});
   }
   continue;
  }
  const before=Object.hasOwn(change,'form_before')?change.form_before:change.before;
  if(JSON.stringify(frm.doc[change.field]??null)!==JSON.stringify(before??null))throw new Error('目标字段已变化，请重新核对：'+change.field);
  values[change.field]=change.after;
 }
 if(!Object.keys(values).length&&!rowUpdates.length)throw new Error('没有需要填入的字段');
 if(Object.keys(values).length)await frm.set_value(values);
 for(const {row,doctype,edits} of rowUpdates)await env.frappe.model.set_value(doctype,row.name,edits);
 for(const {row,edits} of rowUpdates)for(const [key,value] of Object.entries(edits))if(JSON.stringify(row[key]??null)!==JSON.stringify(value??null))throw new Error('原生子表处理后内容不一致，请核实草稿，不要重复应用');
 for(const [field,value] of Object.entries(values)){
  if(JSON.stringify(frm.doc[field]??null)!==JSON.stringify(value??null))throw new Error('原生表单处理后内容不一致，请核实草稿，不要重复应用');
 }
 return {status:'Applied',doctype:proposal.doctype,name:proposal.name};
}
