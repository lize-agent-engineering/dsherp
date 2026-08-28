import {expect,it} from 'vitest';
import {proposalRows} from './proposal-rows.js';
it('明细以业务字段展示修改、新增、删除和移动，不把补丁当整行覆盖',()=>{
 const rows=proposalRows([{field:'items',label:'明细',columns:{qty:'数量',item_code:'物料代码'},before:[{name:'r1',item_code:'I1',qty:2},{name:'r2',item_code:'I2',qty:4}],after:[{name:'r2',qty:5},{item_code:'I3',qty:1}]}]);
 expect(rows).toContainEqual(expect.objectContaining({label:'明细 / I2 / 数量',before:4,after:5}));
 expect(rows).toContainEqual(expect.objectContaining({label:'明细 / I2 / 行位置',before:2,after:1}));
 expect(rows).toContainEqual(expect.objectContaining({label:'明细 / 新增第2行 / 物料代码',before:null,after:'I3'}));
 expect(rows).toContainEqual(expect.objectContaining({label:'明细 / 删除第1行 / 物料代码',before:'I1',after:null}));
 expect(rows.every(row=>typeof row.before!=='object'||row.before===null)).toBe(true);
});
it('标量及已提供草稿保留原确认信息',()=>{
 const change={field:'name',label:'名称',before:'Saved',form_before:'Draft',after:'Suggested'};
 expect(proposalRows([change])).toEqual([change]);
});
