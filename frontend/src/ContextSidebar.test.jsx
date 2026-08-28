// @vitest-environment jsdom
import React from 'react';
import {afterEach,beforeAll,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import ContextSidebar from './ContextSidebar.jsx';
beforeAll(()=>{window.matchMedia=()=>({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});global.ResizeObserver=class{observe(){}disconnect(){}};const get=window.getComputedStyle;window.getComputedStyle=e=>get(e);});
afterEach(cleanup);
const snapshot={schema_version:1,route:['Form','Item','I-1'],page_type:'form',doctype:'Item',name:'I-1',version:'v1',dirty:true};
const session={id:'S-1',title:'查询物料',messages:[{id:'M-1',question:'旧问题',answer:'历史回答',status:'Succeeded',context:snapshot}],active_run:null};
const open=()=>fireEvent.click(screen.getByRole('button',{name:'打开 Agent'}));
const apiDefault=async(method)=>method==='list_sessions'?[{id:'S-1',title:'查询物料'}]:session;
it('页面版本落后时明确说明，不要求刷新覆盖未保存内容',async()=>{
 const api=async method=>method==='list_sessions'?[{id:session.id,title:session.title}]:{...session,messages:[{...session.messages[0],context:{...snapshot,server_version:'v2'}}]};
 render(<ContextSidebar api={api} capture={()=>snapshot}/>);open();
 expect(await screen.findByText('页面版本与服务器已保存版本不同；查询以实际读取为准，未保存内容不会被覆盖。')).toBeTruthy();
});
it('业务操作领域由用户选择并随本条请求发送',async()=>{
 const api=vi.fn(apiDefault);render(<ContextSidebar api={api} capture={()=>snapshot}/>);open();await screen.findByText('历史回答');
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'任务领域'}));fireEvent.click(await screen.findByText('业务操作'));
 fireEvent.change(screen.getByRole('textbox',{name:'业务问题'}),{target:{value:'修改物料名称'}});fireEvent.click(screen.getByRole('button',{name:'发送问题'}));
 await waitFor(()=>expect(api.mock.calls.find(c=>c[0]==='send_message')[1].domain).toBe('operation'));
});
it('会话提案显示冻结差异，切页后确认仍只提交原提案，且不保存或刷新当前表单',async()=>{
 let page=snapshot;
 const proposal={id:'P1',digest:'d1',action:'update',doctype:'Item',name:'I-1',version:'v1',expires_at:'2099-01-01T00:00:00Z',status:'Pending',changes:[{field:'item_name',label:'物料名称',before:'原物料名',after:'建议物料名'}]};
 const api=vi.fn(async method=>method==='list_sessions'?[{id:session.id,title:session.title}]:method==='confirm_operation'?{status:'Succeeded',doctype:'Item',name:'I-1'}:{...session,proposals:[proposal]});
 render(<ContextSidebar api={api} capture={()=>page}/>);open();await screen.findByText('建议物料名');
 expect(api.mock.calls.some(c=>c[0]==='confirm_operation')).toBe(false);
 page={...snapshot,name:'I-2',route:['Form','Item','I-2']};
 fireEvent.click(screen.getByRole('button',{name:'确认执行'}));
 await screen.findByText('执行成功，已读取业务结果');
 expect(api.mock.calls.find(c=>c[0]==='confirm_operation')[1]).toEqual({proposal_id:'P1',digest:'d1',request_id:expect.any(String)});
});
it('未保存字段显式选择后才发送，切换对象不能沿用选择',async()=>{
 let page=snapshot;const api=vi.fn(apiDefault);const captureSelected=vi.fn(()=>({...page,unsaved:{item_name:'建议名称'}}));
 render(<ContextSidebar api={api} capture={()=>page} options={()=>[{value:'item_name',label:'物料名称'}]} captureSelected={captureSelected}/>);open();await screen.findByText('历史回答');
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'提供未保存字段'}));
 fireEvent.click(await screen.findByText('物料名称'));
 fireEvent.change(screen.getByRole('textbox',{name:'业务问题'}),{target:{value:'检查未保存建议'}});
 fireEvent.click(screen.getByRole('button',{name:'发送问题'}));
 await waitFor(()=>expect(captureSelected).toHaveBeenCalledWith(['item_name']));
 expect(api.mock.calls.find(c=>c[0]==='send_message')[1].context.unsaved).toEqual({item_name:'建议名称'});
 page={...snapshot,name:'I-2',route:['Form','Item','I-2']};
 fireEvent.change(screen.getByRole('textbox',{name:'业务问题'}),{target:{value:'另一物料'}});
 fireEvent.click(screen.getByRole('button',{name:'发送问题'}));
 await screen.findByText('页面已变化，请重新选择要提供的未保存字段');
 expect(api.mock.calls.filter(c=>c[0]==='send_message')).toHaveLength(1);
});
it('打开只恢复服务端历史，不运行模型；无蒙层，不占用原生表单焦点',async()=>{
 const api=vi.fn(apiDefault);render(<ContextSidebar api={api} capture={()=>snapshot}/>);open();
 expect(await screen.findByText('历史回答')).toBeTruthy();
 expect(api.mock.calls.map(x=>x[0])).toEqual(['list_sessions','get_session']);
 expect(document.querySelector('.ant-drawer-mask')).toBeNull();
 expect(document.querySelector('.ant-drawer').style.top).toBe('var(--navbar-height)');
});
it('发送绑定点击时页面，关闭再打开保留输入且不保存原生表单',async()=>{
 let page=snapshot;const api=vi.fn(apiDefault);
 render(<ContextSidebar api={api} capture={()=>page}/>);open();await screen.findByText('历史回答');
 fireEvent.change(screen.getByRole('textbox',{name:'业务问题'}),{target:{value:'新问题'}});
 fireEvent.click(screen.getByRole('button',{name:'关闭 Agent'}));open();
 expect(screen.getByRole('textbox',{name:'业务问题'}).value).toBe('新问题');
 await screen.findByText('历史回答');
 page={...snapshot,name:'I-2',route:['Form','Item','I-2']};
 fireEvent.click(screen.getByRole('button',{name:'发送问题'}));
 await waitFor(()=>expect(api.mock.calls.some(c=>c[0]==='send_message')).toBe(true));
 expect(api.mock.calls.find(c=>c[0]==='send_message')[1]).toEqual({session_id:'S-1',question:'新问题',context:page,domain:'query',request_id:expect.any(String)});
});
it('新建会话不携带旧对象，迟到结果不能回灌新会话',async()=>{
 let finish;const api=async method=>method==='send_message'?new Promise(resolve=>finish=resolve):apiDefault(method);
 render(<ContextSidebar api={api} capture={()=>snapshot}/>);open();await screen.findByText('历史回答');
 fireEvent.change(screen.getByRole('textbox',{name:'业务问题'}),{target:{value:'待处理'}});
 fireEvent.click(screen.getByRole('button',{name:'发送问题'}));
 fireEvent.click(screen.getByRole('button',{name:'新建会话'}));
 await act(async()=>finish(session));
 expect(screen.queryByText('历史回答')).toBeNull();
 expect(screen.getByRole('textbox',{name:'业务问题'}).value).toBe('');
});
it('服务端拒权清除历史，后续不自动重复发送',async()=>{
 const api=vi.fn(async method=>{if(method==='send_message')throw new Error('权限已撤销');return apiDefault(method);});
 render(<ContextSidebar api={api} capture={()=>snapshot}/>);open();await screen.findByText('历史回答');
 fireEvent.change(screen.getByRole('textbox',{name:'业务问题'}),{target:{value:'问题'}});
 fireEvent.click(screen.getByRole('button',{name:'发送问题'}));
 await screen.findByText('权限已撤销');expect(screen.queryByText('历史回答')).toBeNull();
 expect(api.mock.calls.filter(c=>c[0]==='send_message')).toHaveLength(1);
});
it('取消针对实际运行标识，展示取消结果不宣称撤销业务',async()=>{
 const running={...session,active_run:'R-1'};
 const api=vi.fn(async method=>method==='list_sessions'?[{id:'S-1',title:'查询物料'}]:method==='cancel_run'?{...session,messages:[{...session.messages[0],status:'Cancelled',answer:''}]}:running);
 render(<ContextSidebar api={api} capture={()=>snapshot}/>);open();await screen.findByText('历史回答');
 fireEvent.click(screen.getByRole('button',{name:'停止运行'}));
 expect(await screen.findByText('已取消后续工作；已发生的操作不会自动撤销。')).toBeTruthy();
 expect(api.mock.calls.find(c=>c[0]==='cancel_run')[1]).toMatchObject({session_id:'S-1',run_id:'R-1'});
});
it('已完成历史也轮询重新授权，撤权时不保留正文',async()=>{
 let reads=0;const api=async method=>{if(method==='get_session'&&++reads>1)throw new Error('历史读取权限已撤销');return apiDefault(method);};
 render(<ContextSidebar api={api} capture={()=>snapshot} pollInterval={30}/>);open();await screen.findByText('历史回答');
 await screen.findByText('历史读取权限已撤销');expect(screen.queryByText('历史回答')).toBeNull();
});
