// @vitest-environment jsdom
import React from 'react';
import {beforeAll,afterEach,it,expect,vi} from 'vitest';
import {render,screen,fireEvent,cleanup,act} from '@testing-library/react';
import OperationProposal from './OperationProposal.jsx';
beforeAll(()=>{window.matchMedia=()=>({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});global.ResizeObserver=class{observe(){}disconnect(){}};const get=window.getComputedStyle;window.getComputedStyle=e=>get(e);});
afterEach(cleanup);
const proposal={id:'P1',digest:'d1',action:'update',doctype:'Item',name:'I-1',version:'v1',expires_at:'2099-01-01T00:00:00Z',status:'Pending',changes:[{field:'item_name',label:'物料名称',before:'旧名称',after:'新名称'}]};
it('新建提案先展示新记录，成功后显示原生命名结果',async()=>{
 const confirm=vi.fn(async()=>({status:'Succeeded',doctype:'Customer',name:'C-NEW',version:'v2',values:{customer_name:'新客户'}}));
 render(<OperationProposal proposal={{...proposal,action:'create',doctype:'Customer',name:null,changes:[{field:'customer_name',label:'客户名称',before:null,after:'新客户'}]}} onConfirm={confirm}/>);
 expect(screen.getByText('Customer / 新记录')).toBeTruthy();fireEvent.click(screen.getByRole('button',{name:'确认执行'}));
 expect(await screen.findByText('已保存记录：Customer / C-NEW')).toBeTruthy();
});
it('刷新从服务端执行记录恢复结果，不重发确认',()=>{
 const confirm=vi.fn();render(<OperationProposal proposal={{...proposal,status:'Succeeded',execution:{status:'Succeeded',execution_id:'E1',version:'v2',values:{item_name:'新名称'}}}} onConfirm={confirm}/>);
 expect(screen.getByText('执行成功，已读取业务结果')).toBeTruthy();
 expect(screen.getByRole('button',{name:'确认执行'}).disabled).toBe(true);expect(confirm).not.toHaveBeenCalled();
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
