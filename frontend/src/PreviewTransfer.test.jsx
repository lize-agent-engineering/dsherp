// @vitest-environment jsdom
import React from 'react';
import {it,expect,vi,beforeAll,afterEach} from 'vitest';
import {render,screen,cleanup} from '@testing-library/react';
import PreviewTransfer from './PreviewTransfer.jsx';
beforeAll(()=>{window.matchMedia=()=>({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});});
afterEach(cleanup);
it('原生预览页面以当前身份接收一次并指引到侧栏，不应用DDL',async()=>{
 const api=vi.fn(async()=>({session_id:'S1',bundle:{id:'B1'}}));
 render(<PreviewTransfer transferId="T1" api={api}/>);
 expect(await screen.findByText('配置包已接收到隔离站点')).toBeTruthy();
 expect(screen.getByText('请打开右下角 Agent，查看具体差异并生成预览确认。')).toBeTruthy();
 expect(api).toHaveBeenCalledExactlyOnceWith('accept_configuration_transfer',{transfer_id:'T1'});
});
