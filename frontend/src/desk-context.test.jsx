// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
const mocks=vi.hoisted(()=>({render:vi.fn(),createRoot:vi.fn()}));
vi.mock('react-dom/client',()=>({createRoot:mocks.createRoot}));
import {mountDeskContext,mountPreviewTransfer} from './desk-context.jsx';
import ErrorBoundary from './ErrorBoundary.jsx';
import ContextSidebar from './ContextSidebar.jsx';
import PreviewTransfer from './PreviewTransfer.jsx';
import {contextApi} from './context-api.js';
afterEach(()=>{document.body.innerHTML='';vi.clearAllMocks();});
it('正式 Agent 工作台不重复挂载全局侧栏',()=>{
 history.pushState({},'', '/desk/dsherp-agent');
 mountDeskContext();
 expect(mocks.createRoot).not.toHaveBeenCalled();
 expect(document.querySelector('#dsherp-context-root')).toBeNull();
 history.pushState({},'', '/');
});
it('全局侧栏只挂载一次，路由切换不移除原生表单或重复创建会话组件',()=>{
  document.body.innerHTML='<main id="native-form"><input value="尚未保存"></main>';
  mocks.createRoot.mockReturnValue({render:mocks.render});
  const form=document.querySelector('main');
  mountDeskContext();mountDeskContext();
  expect(mocks.createRoot).toHaveBeenCalledTimes(1);
  expect(mocks.render).toHaveBeenCalledTimes(1);
  expect(document.querySelector('main')).toBe(form);
  expect(form.querySelector('input').value).toBe('尚未保存');
  expect(document.querySelectorAll('#dsherp-context-root')).toHaveLength(1);
});
it('全局侧栏根包在 ErrorBoundary 中，现有挂载语义不变',()=>{
  mocks.createRoot.mockReturnValue({render:mocks.render});
  mountDeskContext();
  const tree=mocks.render.mock.calls[0][0];
  expect(tree.type).toBe(ErrorBoundary);
  expect(tree.props.children.type).toBe(ContextSidebar);
  expect(tree.props.children.props.api).toBe(contextApi);
});
it('PreviewTransfer 根包在 ErrorBoundary 中，现有挂载句柄语义不变',()=>{
  const unmount=vi.fn();
  mocks.createRoot.mockReturnValue({render:mocks.render,unmount});
  const element=document.createElement('div');
  const dispose=mountPreviewTransfer(element,'T-1');
  const tree=mocks.render.mock.calls[0][0];
  expect(tree.type).toBe(ErrorBoundary);
  expect(tree.props.children.type).toBe(PreviewTransfer);
  expect(tree.props.children.props.transferId).toBe('T-1');
  expect(tree.props.children.props.api).toBe(contextApi);
  expect(typeof dispose).toBe('function');
  dispose();
  expect(unmount).toHaveBeenCalledTimes(1);
});
