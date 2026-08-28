// Applies an already server-authorized fill proposal to the native draft only.
// This client check prevents accidental overwrites; it is not authorization.
export async function applyFormProposal(proposal,env=globalThis){
 const frm=env.cur_frm;
 const route=env.frappe.get_route();
 if(proposal.action!=='fill')throw new Error('不是表单填入提案');
 if(JSON.stringify(route)!==JSON.stringify(['Form',proposal.doctype,proposal.name])||frm?.doctype!==proposal.doctype||frm.doc?.name!==proposal.name||frm.doc.modified!==proposal.version){
  throw new Error('当前表单或版本已变化，请重新提出建议');
 }
 const values={};
 for(const change of proposal.changes){
  const field=frm.meta.fields.find(field=>field.fieldname===change.field);
  if(!field||field.read_only||field.fieldtype==='Table'||Object.hasOwn(values,change.field))throw new Error('填入字段无效：'+change.field);
  if(JSON.stringify(frm.doc[change.field]??null)!==JSON.stringify(change.before??null))throw new Error('目标字段已变化，请重新核对：'+change.field);
  values[change.field]=change.after;
 }
 if(!Object.keys(values).length)throw new Error('没有需要填入的字段');
 await frm.set_value(values);
 for(const [field,value] of Object.entries(values)){
  if(JSON.stringify(frm.doc[field]??null)!==JSON.stringify(value??null))throw new Error('原生表单处理后内容不一致，请核实草稿，不要重复应用');
 }
 return {status:'Applied',doctype:proposal.doctype,name:proposal.name};
}
