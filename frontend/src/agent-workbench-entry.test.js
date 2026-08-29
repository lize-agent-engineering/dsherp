// @vitest-environment jsdom
import {afterEach,expect,it} from 'vitest';
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
