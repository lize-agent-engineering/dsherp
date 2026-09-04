// @vitest-environment jsdom
import React from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import ErrorBoundary from './ErrorBoundary.jsx';
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();cleanup();});
function Boom(){throw new Error('secret-boom-stack');}
function silenceJsdom(){
 const ignore=event=>{
  if(String(event.message||event.error||event).includes('secret-boom-stack'))event.preventDefault();
 };
 window.addEventListener('error',ignore);
 const drop=error=>{if(String(error.message||error).includes('secret-boom-stack'))error.destroy?.();};
 window._virtualConsole.on('jsdomError',drop);
 return()=>{
  window.removeEventListener('error',ignore);
  window._virtualConsole.off('jsdomError',drop);
 };
}
it('捕获子树渲染错误后显示固定文案和重新加载按钮，不泄露原始错误或堆栈',()=>{
 const stop=silenceJsdom();
 const error=vi.spyOn(console,'error').mockImplementation(()=>{});
 try{
  render(<ErrorBoundary><Boom/></ErrorBoundary>);
  expect(document.querySelector('.ant-result')).toBeTruthy();
  expect(screen.getByText('助手暂不可用，请重新加载')).toBeTruthy();
  expect(screen.getByRole('button',{name:'重新加载'})).toBeTruthy();
  expect(screen.queryByText(/secret-boom-stack/)).toBeNull();
  const logged=error.mock.calls.find(call=>call[0] instanceof Error && call[1] && 'componentStack' in call[1]);
  expect(logged[0].message).toBe('secret-boom-stack');
  expect(logged[1].componentStack).toMatch(/Boom/);
 }finally{stop();}
});
it('重新加载按钮调用 globalThis.location.reload',()=>{
 const stop=silenceJsdom();
 vi.spyOn(console,'error').mockImplementation(()=>{});
 const reload=vi.fn();
 vi.stubGlobal('location',{reload});
 try{
  render(<ErrorBoundary><Boom/></ErrorBoundary>);
  fireEvent.click(screen.getByRole('button',{name:'重新加载'}));
  expect(reload).toHaveBeenCalledTimes(1);
 }finally{stop();}
});
it('无渲染错误时原样子树可见',()=>{
 render(<ErrorBoundary><p>正常内容</p></ErrorBoundary>);
 expect(screen.getByText('正常内容')).toBeTruthy();
 expect(screen.queryByText('助手暂不可用，请重新加载')).toBeNull();
});
