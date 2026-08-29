// @vitest-environment jsdom
import React from 'react';
import {it,expect,vi,afterEach,beforeAll} from 'vitest';
import {render,screen,fireEvent,cleanup} from '@testing-library/react';
import ConfigurationBundle from './ConfigurationBundle.jsx';
beforeAll(()=>{window.matchMedia=()=>({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});global.ResizeObserver=class{observe(){}disconnect(){}};const get=window.getComputedStyle;window.getComputedStyle=e=>get(e);});
afterEach(cleanup);
const bundle={id:'B1',digest:'package-1',site:'preview.localhost',execution_ready:true,preview_available:true,changes:[{object:'Inspection',action:'新增 DocType',detail:'检查结果 / Data'}]};
it('展示模型配置包，显式请求预览确认但不执行配置',async()=>{
 const prepare=vi.fn(async()=>({id:'C1',digest:'confirmation-1',purpose:'preview',target:'preview.localhost',baseline:'v1',status:'Pending',expires_at:'2099-01-01T00:00:00Z',changes:bundle.changes}));
 const confirm=vi.fn();render(<ConfigurationBundle bundle={bundle} onPrepare={prepare} onConfirm={confirm}/>);
 expect(screen.getByText('检查结果 / Data')).toBeTruthy();expect(prepare).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'查看预览确认'}));
 await screen.findByRole('button',{name:'确认应用到隔离预览'});
 expect(prepare).toHaveBeenCalledExactlyOnceWith({bundle_id:'B1',digest:'package-1'});expect(confirm).not.toHaveBeenCalled();
});
it('来源未成功不能准备，非预览站点不伪装成本地预览',()=>{
 const prepare=vi.fn();render(<ConfigurationBundle bundle={{...bundle,execution_ready:false,preview_available:false}} onPrepare={prepare}/>);
 expect(screen.getByText('配置包尚未应用；需在隔离预览站点继续')).toBeTruthy();
 expect(screen.queryByRole('button',{name:'查看预览确认'})).toBeNull();expect(prepare).not.toHaveBeenCalled();
});
it('源站只交接冻结包并提供预览入口，不在源站执行预览',async()=>{
 const transfer=vi.fn(async()=>({id:'T1',preview_url:'http://preview.localhost/app/dsherp-configuration-preview/T1'}));
 render(<ConfigurationBundle bundle={{...bundle,preview_available:false,preview_transfer_available:true}} onTransfer={transfer}/>);
 fireEvent.click(screen.getByRole('button',{name:'发送到隔离预览'}));
 const link=await screen.findByRole('link',{name:'打开隔离预览'});
 expect(link.href).toBe('http://preview.localhost/app/dsherp-configuration-preview/T1');
 expect(transfer).toHaveBeenCalledExactlyOnceWith({bundle_id:'B1',digest:'package-1',request_id:expect.any(String)});
 expect(screen.queryByRole('button',{name:'确认应用到隔离预览'})).toBeNull();
});
