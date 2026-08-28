// @vitest-environment jsdom
import React from 'react';
import {afterEach,beforeAll,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import Portal from './Portal.jsx';
beforeAll(()=>{window.matchMedia=()=>({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});global.ResizeObserver=class{observe(){}disconnect(){}};const get=window.getComputedStyle;window.getComputedStyle=e=>get(e);});
afterEach(cleanup);
const context={user:'member@example.invalid',enterprises:[{id:'alpha',label:'甲企业',status:'Ready'},{id:'beta',label:'乙企业',status:'Ready'}]};
const done={id:'TASK',question:'旧查询',answer:'历史回答'};
async function select(label='甲企业'){await screen.findByText(context.user);fireEvent.mouseDown(screen.getByRole('combobox',{name:'当前企业'}));fireEvent.click(screen.getByText(label));}
it('平台只提供企业 Desk 入口与只读历史，不再发起模型任务',async()=>{
 const api=vi.fn(async method=>method==='context'?context:method==='desk_entry'?{url:'http://localhost:18082/api/method/dsherp_bridge.sso.start'}:[]),openDesk=vi.fn();
 render(<Portal api={api} openDesk={openDesk}/>);await select();expect(screen.queryByRole('textbox',{name:'业务问题'})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'进入企业 Desk'}));await waitFor(()=>expect(openDesk).toHaveBeenCalledWith('http://localhost:18082/api/method/dsherp_bridge.sso.start'));
 expect(api.mock.calls.some(c=>c[0]==='submit_task')).toBe(false);
});
it('历史点开重新授权，拒绝后清除旧内容',async()=>{
 let denied=false;const api=async method=>{if(method==='context')return context;if(method==='list_tasks')return [done];if(denied)throw new Error('成员已撤销');return done;};
 render(<Portal api={api}/>);await select();fireEvent.click(await screen.findByRole('button',{name:'旧查询'}));await screen.findByText('历史回答');
 denied=true;fireEvent.click(screen.getByRole('button',{name:'旧查询'}));await screen.findByText('成员已撤销');expect(screen.queryByText('历史回答')).toBeNull();
});
it('切换企业后迟到历史不串入新企业',async()=>{
 let finish;const api=(method,p)=>method==='context'?Promise.resolve(context):p.enterprise==='alpha'?new Promise(resolve=>finish=resolve):Promise.resolve([]);
 render(<Portal api={api}/>);await select();await select('乙企业');await act(async()=>finish([done]));expect(screen.queryByRole('button',{name:'旧查询'})).toBeNull();
});
it('无成员不显示业务入口',async()=>{render(<Portal api={async()=>({...context,enterprises:[]})}/>);await screen.findByText('尚未加入可用企业');expect(screen.queryByRole('button',{name:'进入企业 Desk'})).toBeNull();});
