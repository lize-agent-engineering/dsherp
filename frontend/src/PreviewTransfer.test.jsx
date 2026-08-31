// @vitest-environment jsdom
import React from 'react';
import {it,expect,vi,afterEach} from 'vitest';
import {render,screen,cleanup} from '@testing-library/react';
import PreviewTransfer from './PreviewTransfer.jsx';
afterEach(()=>{vi.restoreAllMocks();cleanup();});
it('加载提示使用 Ant Design 支持的结构，不产生 Spin 用法警告',()=>{
 const error=vi.spyOn(console,'error');
 render(<PreviewTransfer transferId="T1" api={()=>new Promise(()=>{})}/>);
 expect(error.mock.calls.flat().join(' ')).not.toContain('[antd: Spin] `tip` only work in nest or fullscreen pattern.');
});
it('原生预览页面以当前身份接收一次并指引到侧栏，不应用DDL',async()=>{
 const api=vi.fn(async()=>({session_id:'S1',bundle:{id:'B1'}}));
 render(<PreviewTransfer transferId="T1" api={api}/>);
 expect(await screen.findByText('配置包已接收到隔离站点')).toBeTruthy();
 expect(screen.getByText('请打开右下角 Agent，查看具体差异并生成预览确认。')).toBeTruthy();
 expect(api).toHaveBeenCalledExactlyOnceWith('accept_configuration_transfer',{transfer_id:'T1'});
});
