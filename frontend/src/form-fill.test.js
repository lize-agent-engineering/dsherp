import {it,expect,vi} from 'vitest';
import {applyFormProposal} from './form-fill.js';

function setup(){
 const doc={doctype:'Item',name:'I1',modified:'v1',item_name:'Before',description:'User draft'};
 const frm={doctype:'Item',doc,meta:{fields:[{fieldname:'item_name',fieldtype:'Data'},{fieldname:'description',fieldtype:'Text Editor'}]},set_value:vi.fn(async values=>Object.assign(doc,values)),save:vi.fn(),reload_doc:vi.fn()};
 return {frm,env:{cur_frm:frm,frappe:{get_route:()=>['Form','Item','I1']}},proposal:{action:'fill',doctype:'Item',name:'I1',version:'v1',changes:[{field:'item_name',before:'Before',after:'Suggested'}]}};
}
it('使用原生set_value填入已确认建议，不保存或刷新，也不覆盖无关草稿',async()=>{
 const {frm,env,proposal}=setup();
 await applyFormProposal(proposal,env);
 expect(frm.set_value).toHaveBeenCalledExactlyOnceWith({item_name:'Suggested'});
 expect(frm.doc.description).toBe('User draft');expect(frm.save).not.toHaveBeenCalled();expect(frm.reload_doc).not.toHaveBeenCalled();
});
it('切页、版本变化或目标字段已有新草稿时，零修改并明确报错',async()=>{
 for(const change of [s=>s.env.frappe.get_route=()=>['Form','Item','I2'],s=>s.frm.doc.modified='v2',s=>s.frm.doc.item_name='New user draft']){
  const s=setup();change(s);
  await expect(applyFormProposal(s.proposal,s.env)).rejects.toThrow(/变化/);
  expect(s.frm.set_value).not.toHaveBeenCalled();
 }
});
it('先校验全部字段，不让后面的无效字段造成前半段填入',async()=>{
 const s=setup();s.proposal.changes.push({field:'missing',before:null,after:'bad'});
 await expect(applyFormProposal(s.proposal,s.env)).rejects.toThrow(/字段/);
 expect(s.frm.set_value).not.toHaveBeenCalled();
});
it('已明确提供的草稿值是填入基线，后来再变化仍拒绝覆盖',async()=>{
 const s=setup();s.frm.doc.item_name='Provided draft';s.proposal.changes[0].form_before='Provided draft';
 await applyFormProposal(s.proposal,s.env);expect(s.frm.doc.item_name).toBe('Suggested');
 s.frm.set_value.mockClear();s.frm.doc.item_name='Later edit';
 await expect(applyFormProposal(s.proposal,s.env)).rejects.toThrow(/变化/);expect(s.frm.set_value).not.toHaveBeenCalled();
});
it('子表通过原生行字段更新，保留行名及未涉及的草稿字段',async()=>{
 const s=setup();s.frm.doc.items=[{name:'r1',doctype:'Sales Order Item',item_code:'I1',qty:2,rate:99}];
 s.frm.meta.fields.push({fieldname:'items',fieldtype:'Table',options:'Sales Order Item'});
 s.env.frappe.get_meta=()=>({fields:[{fieldname:'qty',fieldtype:'Float'}]});
 s.env.frappe.model={set_value:vi.fn(async(doctype,name,values)=>Object.assign(s.frm.doc.items[0],values))};
 s.proposal.changes=[{field:'items',before:[{name:'r1',item_code:'I1',qty:2}],after:[{name:'r1',qty:3}]}];
 await applyFormProposal(s.proposal,s.env);
 expect(s.env.frappe.model.set_value).toHaveBeenCalledExactlyOnceWith('Sales Order Item','r1',{qty:3});
 expect(s.frm.doc.items[0]).toMatchObject({name:'r1',qty:3,rate:99});expect(s.frm.set_value).not.toHaveBeenCalled();
});
