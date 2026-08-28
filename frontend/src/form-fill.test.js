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
