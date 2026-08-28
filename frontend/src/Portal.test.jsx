// @vitest-environment jsdom
import React from 'react';
import {afterEach,beforeAll,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import Portal from './Portal.jsx';
beforeAll(()=>{window.matchMedia=()=>({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});global.ResizeObserver=class{observe(){}disconnect(){}};const get=window.getComputedStyle;window.getComputedStyle=e=>get(e);});
afterEach(()=>{cleanup();vi.useRealTimers();});
const context={user:'member@example.invalid',enterprises:[{id:'alpha',label:'甲企业',status:'Ready'},{id:'beta',label:'乙企业',status:'Ready'}],platform_admin:false};
const done={id:'TASK-1',enterprise:'alpha',question:'查询物料',status:'Succeeded',answer:'服务端真实回答',events:[{tool:'erp_search_records',arguments:{doctype:'Item',query:''},status:'Succeeded',result:{records:[{name:'ITEM-1'}]}}]};
async function selectEnterprise(label='甲企业'){
 await screen.findByText('member@example.invalid');
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'当前企业'}));fireEvent.click(screen.getByText(label));
}
it('真实身份与自然语言入口，不显示手工编号查询或演示角色',async()=>{
 render(<Portal api={async method=>method==='context'?context:[]}/>);
 await selectEnterprise();
 expect(screen.getByRole('textbox',{name:'业务问题'})).toBeTruthy();
 expect(screen.queryByRole('textbox',{name:'记录编号'})).toBeNull();
 expect(screen.queryByRole('combobox',{name:'演示角色'})).toBeNull();
});
it('提交问题到服务端并显示持久结果和真实工具记录',async()=>{
 const api=vi.fn(async method=>method==='context'?context:method==='list_tasks'?[]:done);
 render(<Portal api={api}/>);await selectEnterprise();
 fireEvent.change(screen.getByRole('textbox',{name:'业务问题'}),{target:{value:'查询物料'}});
 fireEvent.click(screen.getByRole('button',{name:'发送问题'}));
 expect(await screen.findByText('服务端真实回答')).toBeTruthy();
 const call=api.mock.calls.find(x=>x[0]==='submit_task');
 expect(call[1]).toEqual({enterprise:'alpha',question:'查询物料',request_id:expect.any(String)});
 expect(await screen.findByText('搜索记录')).toBeTruthy();
});
it('重新打开后从服务端恢复任务，切换企业清空旧回答与输入',async()=>{
 render(<Portal api={async(method,p)=>method==='context'?context:method==='list_tasks'?(p.enterprise==='alpha'?[done]:[]):done}/>);
 await selectEnterprise();expect(await screen.findByText('服务端真实回答')).toBeTruthy();
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'当前企业'}));fireEvent.click(screen.getByText('乙企业'));
 expect(screen.queryByText('服务端真实回答')).toBeNull();
 expect(screen.getByRole('textbox',{name:'业务问题'}).value).toBe('');
});
it('无成员不能发起任务',async()=>{
 render(<Portal api={async()=>({...context,enterprises:[]})}/>);
 expect(await screen.findByText('尚未加入可用企业')).toBeTruthy();
 expect(screen.queryByRole('button',{name:'发送问题'})).toBeNull();
});
it('企业切换后忽略迟到的提交结果',async()=>{
 let finish;
 const api=method=>method==='context'?Promise.resolve(context):method==='list_tasks'?Promise.resolve([]):new Promise(resolve=>{finish=resolve;});
 render(<Portal api={api}/>);await selectEnterprise();
 fireEvent.change(screen.getByRole('textbox',{name:'业务问题'}),{target:{value:'查询物料'}});
 fireEvent.click(screen.getByRole('button',{name:'发送问题'}));
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'当前企业'}));fireEvent.click(screen.getByText('乙企业'));
 await act(async()=>finish(done));
 expect(screen.queryByText('服务端真实回答')).toBeNull();
});
it('任务失败显示服务端原因，不伪造回答',async()=>{
 render(<Portal api={async method=>method==='context'?context:[{...done,status:'Failed',answer:'',error:'业务读取权限已撤销',events:[]}]}/>);
 await selectEnterprise();expect(await screen.findByText('业务读取权限已撤销')).toBeTruthy();
 expect(screen.queryByText('服务端真实回答')).toBeNull();
});
it('运行中轮询实际任务，完成后显示结果',async()=>{
 const api=vi.fn(async method=>method==='context'?context:method==='list_tasks'?[{...done,status:'Running',answer:'',events:[]}]:done);
 render(<Portal api={api} pollInterval={10}/>);await selectEnterprise();
 expect(await screen.findByText('服务端真实回答')).toBeTruthy();
 await waitFor(()=>expect(api.mock.calls.some(x=>x[0]==='get_task')).toBe(true));
});
it('点击历史记录时重新授权读取，拒绝后不显示缓存回答',async()=>{
 const api=async method=>{if(method==='context')return context;if(method==='list_tasks')return [done];throw new Error('成员权限已撤销');};
 render(<Portal api={api}/>);await selectEnterprise();await screen.findByText('服务端真实回答');
 fireEvent.click(screen.getByRole('button',{name:/查询物料/}));
 expect(await screen.findByText('成员权限已撤销')).toBeTruthy();
 expect(screen.queryByText('服务端真实回答')).toBeNull();
});
it('提交拒绝会清除旧业务结果，刷新记录仅核实任务不重复提交',async()=>{
 const api=vi.fn(async method=>{if(method==='context')return context;if(method==='list_tasks')return [done];throw new Error('授权已撤销');});
 render(<Portal api={api}/>);await selectEnterprise();await screen.findByText('服务端真实回答');
 fireEvent.change(screen.getByRole('textbox',{name:'业务问题'}),{target:{value:'新问题'}});
 fireEvent.click(screen.getByRole('button',{name:'发送问题'}));await screen.findByText('授权已撤销');
 expect(screen.queryByText('服务端真实回答')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'刷新记录'}));await screen.findByText('服务端真实回答');
 expect(api.mock.calls.filter(x=>x[0]==='submit_task')).toHaveLength(1);
});
it('提交与历史加载交错时保留旧任务和新结果',async()=>{
 let load;
 const old={...done,id:'OLD',question:'历史问题'};
 const api=method=>method==='context'?Promise.resolve(context):method==='list_tasks'?new Promise(resolve=>{load=resolve;}):Promise.resolve(done);
 render(<Portal api={api}/>);await selectEnterprise();
 fireEvent.change(screen.getByRole('textbox',{name:'业务问题'}),{target:{value:'查询物料'}});
 fireEvent.click(screen.getByRole('button',{name:'发送问题'}));await screen.findByText('服务端真实回答');
 await act(async()=>load([old]));
 expect(screen.getByRole('button',{name:/历史问题/})).toBeTruthy();
 expect(screen.getByRole('button',{name:/查询物料/})).toBeTruthy();
});
it('提交拒权清理后，迟到历史不能回灌',async()=>{
 let load;
 const api=method=>method==='context'?Promise.resolve(context):method==='list_tasks'?new Promise(resolve=>{load=resolve;}):Promise.reject(new Error('权限撤销'));
 render(<Portal api={api}/>);await selectEnterprise();
 fireEvent.change(screen.getByRole('textbox',{name:'业务问题'}),{target:{value:'查询物料'}});
 fireEvent.click(screen.getByRole('button',{name:'发送问题'}));await screen.findByText('权限撤销');
 await act(async()=>load([done]));
 expect(screen.queryByRole('button',{name:/查询物料/})).toBeNull();
});
