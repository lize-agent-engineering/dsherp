// @vitest-environment jsdom
import React from 'react';
import {it,expect,vi,afterEach,beforeAll} from 'vitest';
import {render,screen,fireEvent,cleanup} from '@testing-library/react';
import ConfigurationProposal from './ConfigurationProposal.jsx';
beforeAll(()=>{window.matchMedia=()=>({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});global.ResizeObserver=class{observe(){}disconnect(){}};const get=window.getComputedStyle;window.getComputedStyle=e=>get(e);});
afterEach(cleanup);
it('来源运行未成功时展示提案但不能执行，成功后恢复确认',()=>{
 const confirm=vi.fn();const view=render(<ConfigurationProposal proposal={{...proposal,execution_ready:false}} onConfirm={confirm}/>);
 expect(screen.getByRole('button',{name:'确认应用到隔离预览'}).disabled).toBe(true);
 expect(screen.getByText('来源运行尚未成功完成，不能应用此配置')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'确认应用到隔离预览'}));expect(confirm).not.toHaveBeenCalled();
 view.rerender(<ConfigurationProposal proposal={{...proposal,execution_ready:true}} onConfirm={confirm}/>);
 expect(screen.getByRole('button',{name:'确认应用到隔离预览'}).disabled).toBe(false);
});
const proposal={id:'C1',digest:'immutable-1',purpose:'preview',target:'preview.localhost',baseline:'baseline-1',expires_at:'2099-01-01T00:00:00Z',status:'Pending',changes:[{object:'Quality Check',action:'新增 DocType',detail:'检查结果 / Select / 合格、不合格'}]};
it('展示配置目标与具体内容，预览只确认本次冻结提案，不自动发布',async()=>{
 const confirm=vi.fn(async()=>({status:'Succeeded',steps:[{object:'Quality Check',status:'Succeeded'}]}));
 render(<ConfigurationProposal proposal={proposal} onConfirm={confirm}/>);
 expect(screen.getByText('preview.localhost')).toBeTruthy();expect(screen.getByText('检查结果 / Select / 合格、不合格')).toBeTruthy();
 expect(confirm).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'确认应用到隔离预览'}));
 await screen.findByText('隔离预览配置已应用；目标站点尚未发布');
 expect(confirm).toHaveBeenCalledExactlyOnceWith({proposal_id:'C1',digest:'immutable-1',request_id:expect.any(String)});
 expect(screen.queryByRole('button',{name:'确认发布到目标站点'})).toBeNull();
});
it('部分成功保留逐项结果，不宣称回滚、不提供整包重跑',()=>{
 const confirm=vi.fn();render(<ConfigurationProposal proposal={{...proposal,purpose:'publish',status:'Partial',execution:{status:'Partial',steps:[{object:'Quality Check',status:'Succeeded'},{object:'Workflow',status:'Unknown'}]}}} onConfirm={confirm}/>);
 expect(screen.getByText('发布未全部完成，请核实逐项结果；已发生的配置变更不保证回滚')).toBeTruthy();
 expect(screen.getByRole('button',{name:'确认发布到目标站点'}).disabled).toBe(true);expect(confirm).not.toHaveBeenCalled();
});
