// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {readWorkbenchState} from './agent-workbench-entry.js';

afterEach(()=>sessionStorage.clear());

it('URL 只读取会话和随机 handoff 标识，业务正文从 sessionStorage 一次性取得',()=>{
 const token='a'.repeat(32);const context={schema_version:1,page_type:'form',route:['Form','Item','I-1'],doctype:'Item',name:'I-1'};
 sessionStorage.setItem(`dsherp-agent-handoff:${token}`,JSON.stringify(context));
 const result=readWorkbenchState(`?session=S-1&handoff=${token}`);
 expect(result).toEqual({initialSession:'S-1',handoff:context});
 expect(sessionStorage.getItem(`dsherp-agent-handoff:${token}`)).toBeNull();
 expect(readWorkbenchState('?session=S-2&handoff=Item-I-1')).toEqual({initialSession:'S-2',handoff:null});
});

it('挂载句柄既能卸载，也把 Agent 设置入口交给原生页面头部', async () => {
 const {mount}=await import('./agent-workbench-entry.js');
 const element=document.createElement('div');
 document.body.append(element);
 const dispose=mount(element);
 expect(typeof dispose).toBe('function');
 expect(typeof dispose.openSettings).toBe('function');
 expect(dispose.unmount).toBe(dispose);
 dispose();
 element.remove();
});

it('工作台根包在 ErrorBoundary 中，挂载句柄语义不变', async () => {
 vi.resetModules();
 const render=vi.fn();
 const unmount=vi.fn();
 vi.doMock('react-dom/client',()=>({createRoot:()=>({render,unmount})}));
 try{
  const {mount}=await import('./agent-workbench-entry.js');
  const {default:ErrorBoundary}=await import('./ErrorBoundary.jsx');
  const {default:AgentWorkbench}=await import('./AgentWorkbench.jsx');
  const {contextApi}=await import('./context-api.js');
  const element=document.createElement('div');
  const dispose=mount(element);
  const tree=render.mock.calls[0][0];
  expect(tree.type).toBe(ErrorBoundary);
  expect(tree.props.children.type).toBe(AgentWorkbench);
  expect(tree.props.children.props.api).toBe(contextApi);
  expect(typeof dispose).toBe('function');
  expect(typeof dispose.openSettings).toBe('function');
  expect(dispose.openSettings()).toBe(false);
  expect(dispose.unmount).toBe(dispose);
  dispose();
  expect(unmount).toHaveBeenCalledTimes(1);
 }finally{
  vi.doUnmock('react-dom/client');
  vi.resetModules();
 }
});
it('工作台尚未注册设置入口时 openSettings 如实返回 false，不静默吞掉', async () => {
 // 若 React 树没渲染成功（或点击发生在注册 effect 之前），调用方需要据此
 // 提示"尚未加载完成"，而不是让按钮看起来点了没反应。
 const {mount}=await import('./agent-workbench-entry.js');
 const element=document.createElement('div');
 // 不挂进 document：React 树不会提交 effect，controls.openSettings 不存在。
 const dispose=mount(element);
 expect(dispose.openSettings()).toBe(false);
 dispose();
});
