// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
const mocks=vi.hoisted(()=>({render:vi.fn(),createRoot:vi.fn()}));
vi.mock('react-dom/client',()=>({createRoot:mocks.createRoot}));
import {mountDeskContext} from './desk-context.jsx';
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
