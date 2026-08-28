// @vitest-environment jsdom
import React from 'react';
import {afterEach,beforeAll,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import Portal from './Portal.jsx';
beforeAll(()=>{window.matchMedia=()=>({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});global.ResizeObserver=class{observe(){}disconnect(){}};const get=window.getComputedStyle;window.getComputedStyle=e=>get(e);});
afterEach(cleanup);
const context={user:'member@example.invalid',enterprises:[{id:'alpha',label:'甲企业',status:'Ready'},{id:'beta',label:'乙企业',status:'Ready'}],platform_admin:false};
it('真实入口从服务端获取身份，不提供角色模拟器',async()=>{
 render(<Portal api={async()=>context}/>);
 expect(await screen.findByText('member@example.invalid')).toBeTruthy();
 expect(screen.queryByRole('combobox',{name:'演示角色'})).toBeNull();
 expect(screen.getByText('请选择企业后查询业务记录。')).toBeTruthy();
});
it('读取服务端记录；切换企业清除旧记录与输入',async()=>{
 const api=async method=>method==='context'?context:{name:'REAL-001',fields:{item_name:'甲企业物料'}};
 render(<Portal api={api}/>);await screen.findByText('member@example.invalid');
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'当前企业'}));fireEvent.click(screen.getByText('甲企业'));
 fireEvent.change(screen.getByRole('textbox',{name:'记录编号'}),{target:{value:'REAL-001'}});
 fireEvent.click(screen.getByRole('button',{name:'查询记录'}));
 expect(await screen.findByText('甲企业物料')).toBeTruthy();
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'当前企业'}));fireEvent.click(screen.getByText('乙企业'));
 expect(screen.queryByText('甲企业物料')).toBeNull();expect(screen.getByRole('textbox',{name:'记录编号'}).value).toBe('');
});
it('没有成员关系不能显示业务数据，也不推断同邮箱企业',async()=>{
 render(<Portal api={async()=>({...context,enterprises:[]})}/>);
 expect(await screen.findByText('尚未加入可用企业')).toBeTruthy();
 expect(screen.queryByRole('button',{name:'查询记录'})).toBeNull();
});

it('切换企业后忽略未完成的旧企业响应',async()=>{
 let finish;
 const api=method=>method==='context'?Promise.resolve(context):new Promise(resolve=>{finish=resolve;});
 render(<Portal api={api}/>);await screen.findByText('member@example.invalid');
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'当前企业'}));fireEvent.click(screen.getByText('甲企业'));
 fireEvent.change(screen.getByRole('textbox',{name:'记录编号'}),{target:{value:'OLD'}});
 fireEvent.click(screen.getByRole('button',{name:'查询记录'}));
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'当前企业'}));fireEvent.click(screen.getByText('乙企业'));
 await (await import('@testing-library/react')).act(async()=>{finish({name:'OLD',fields:{item_name:'旧企业迟到数据'}});});
 expect(screen.queryByText('旧企业迟到数据')).toBeNull();
 expect(screen.getByRole('textbox',{name:'记录编号'}).value).toBe('');
});
