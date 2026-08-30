// @vitest-environment jsdom
import React from 'react';
import {beforeAll,afterEach,it,expect,vi} from 'vitest';
import {render,screen,fireEvent,cleanup,act} from '@testing-library/react';
import OperationProposal from './OperationProposal.jsx';
beforeAll(()=>{window.matchMedia=()=>({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});global.ResizeObserver=class{observe(){}disconnect(){}};const get=window.getComputedStyle;window.getComputedStyle=e=>get(e);});
afterEach(cleanup);
const proposal={id:'P1',digest:'d1',action:'update',doctype:'Item',name:'I-1',version:'v1',expires_at:'2099-01-01T00:00:00Z',status:'Pending',changes:[{field:'item_name',label:'物料名称',before:'旧名称',after:'新名称'}]};
it('结果不明时只核实当前业务事实，不重发执行或改成成功',async()=>{
 const confirm=vi.fn();const verify=vi.fn(async()=>({note:'一致不等于执行成功',matches_proposal:true,observed:{version:'v2',values:{item_name:'新名称'}}}));
 render(<OperationProposal proposal={{...proposal,status:'Unknown',execution:{status:'Unknown',error:'响应丢失'}}} onConfirm={confirm} onVerify={verify}/>);
 fireEvent.click(screen.getByRole('button',{name:'核实业务结果'}));await screen.findByText('一致不等于执行成功');
 expect(verify).toHaveBeenCalledExactlyOnceWith({proposal_id:'P1'});expect(confirm).not.toHaveBeenCalled();
 expect(screen.queryByText('执行成功，已读取业务结果')).toBeNull();
});
it('填表先获得服务端确认再应用草稿，刷新不会自动重放',async()=>{
 const fill={...proposal,action:'fill'};
 const result={status:'Authorized',target:'browser-draft',doctype:'Item',name:'I-1',version:'v1',values:{item_name:'新名称'}};
 const apply=vi.fn(async()=>({status:'Applied'}));const confirm=vi.fn(async()=>result);
 const view=render(<OperationProposal proposal={fill} onConfirm={confirm} onApply={apply}/>);
 expect(apply).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'确认填入'}));
 await screen.findByText('已填入当前草稿，尚未保存或提交');expect(apply).toHaveBeenCalledOnce();
 view.unmount();render(<OperationProposal proposal={{...fill,status:'Authorized',execution:result}} onConfirm={confirm} onApply={apply}/>);
 expect(screen.getByText('本次填入已获授权；请核实当前草稿，不会自动重新填入。')).toBeTruthy();
 expect(apply).toHaveBeenCalledOnce();
});
it('销售订单状态操作显示业务含义和影响，不能只显示数字状态',()=>{
 render(<OperationProposal proposal={{...proposal,doctype:'Sales Order',action:'cancel',changes:[{field:'docstatus',label:'单据状态',before:1,after:2}]}} onConfirm={vi.fn()}/>);
 expect(screen.getByText('已提交')).toBeTruthy();expect(screen.getByText('已取消')).toBeTruthy();
 expect(screen.getByText('取消将使此已提交订单失效，不会撤销已发生的其他业务。')).toBeTruthy();
});
it('新建提案先展示新记录，成功后显示原生命名结果',async()=>{
 const confirm=vi.fn(async()=>({status:'Succeeded',doctype:'Customer',name:'C-NEW',version:'v2',values:{customer_name:'新客户'}}));
 render(<OperationProposal proposal={{...proposal,action:'create',doctype:'Customer',name:null,changes:[{field:'customer_name',label:'客户名称',before:null,after:'新客户'}]}} onConfirm={confirm}/>);
 expect(screen.getByText('Customer / 新记录')).toBeTruthy();fireEvent.click(screen.getByRole('button',{name:'确认执行'}));
 expect(await screen.findByText('已保存记录：Customer / C-NEW')).toBeTruthy();
});
it('结果未核实的兜底提示指向本卡片的核实入口，而不是已删除的执行记录视图',async()=>{
 const confirm=vi.fn(async()=>({status:'Unknown'}));
 render(<OperationProposal proposal={proposal} onConfirm={confirm} onVerify={vi.fn()}/>);
 fireEvent.click(screen.getByRole('button',{name:'确认执行'}));
 expect(await screen.findByText('执行结果尚未核实，请在本提案卡中核实业务结果')).toBeTruthy();
 expect(screen.queryByText(/请查看执行记录/)).toBeNull();
 expect(screen.getByRole('button',{name:'核实业务结果'})).toBeTruthy();
});

it('刷新从服务端执行记录恢复结果，不重发确认',()=>{
 const confirm=vi.fn();render(<OperationProposal proposal={{...proposal,status:'Succeeded',execution:{status:'Succeeded',execution_id:'E1',version:'v2',values:{item_name:'新名称'}}}} onConfirm={confirm}/>);
 expect(screen.getByText('执行成功，已读取业务结果')).toBeTruthy();
 expect(screen.getByRole('button',{name:'确认执行'}).disabled).toBe(true);expect(confirm).not.toHaveBeenCalled();
});
it('已成功的历史提案不会再提示过期或要求重新提出',()=>{
 render(<OperationProposal proposal={{...proposal,expires_at:'2000-01-01T00:00:00Z',status:'Succeeded',execution:{status:'Succeeded'}}} onConfirm={vi.fn()}/>);
 expect(screen.queryByText('确认已过期，请重新提出操作')).toBeNull();
 expect(screen.getByText('执行成功，已读取业务结果')).toBeTruthy();
});
it('填入差异展示用户已提供的草稿原值，而不是伪装成数据库原值',()=>{
 render(<OperationProposal proposal={{...proposal,action:'fill',changes:[{...proposal.changes[0],form_before:'已提供的草稿'}]}} onConfirm={vi.fn()}/>);
 expect(screen.getByText('填入前（表单）')).toBeTruthy();expect(screen.getByText('已提供的草稿')).toBeTruthy();
 expect(screen.queryByText('旧名称')).toBeNull();
});
it('展示冻结目标和具体差异，确认前零调用；确认只传提案绑定而非可改写内容',async()=>{
 let finish;const confirm=vi.fn(()=>new Promise(resolve=>finish=resolve));render(<OperationProposal proposal={proposal} onConfirm={confirm}/>);
 expect(screen.getByText('Item / I-1')).toBeTruthy();expect(screen.getByText('旧名称')).toBeTruthy();expect(screen.getByText('新名称')).toBeTruthy();expect(confirm).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'确认执行'}));fireEvent.click(screen.getByRole('button',{name:'确认执行'}));
 expect(confirm).toHaveBeenCalledTimes(1);expect(confirm.mock.calls[0][0]).toEqual({proposal_id:'P1',digest:'d1',request_id:expect.any(String)});
 await act(async()=>finish({status:'Succeeded',doctype:'Item',name:'I-1'}));expect(await screen.findByText('执行成功，已读取业务结果')).toBeTruthy();
});
it('过期提案不可确认，结果不明不自动重试',async()=>{
 const confirm=vi.fn(async()=>{throw new Error('结果尚未核实');});const view=render(<OperationProposal proposal={{...proposal,expires_at:'2000-01-01T00:00:00Z'}} onConfirm={confirm}/>);
 expect(screen.getByRole('button',{name:'确认执行'}).disabled).toBe(true);
 view.rerender(<OperationProposal proposal={proposal} onConfirm={confirm}/>);fireEvent.click(screen.getByRole('button',{name:'确认执行'}));
 await screen.findByText('结果尚未核实');expect(confirm).toHaveBeenCalledTimes(1);expect(screen.getByRole('button',{name:'确认执行'}).disabled).toBe(true);
});
