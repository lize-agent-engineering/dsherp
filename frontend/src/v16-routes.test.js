import {readFileSync} from 'node:fs';
import {describe,expect,it} from 'vitest';

const files=[
 'desk-context.jsx',
 'ContextSidebar.jsx',
 'Portal.jsx',
 'App.jsx',
];

describe('v16 Desk routes',()=>{
 it('does not retain v15 /app hardcoded links',()=>{
  const stale=files.filter(file=>/['"`]\/app\//.test(readFileSync(new URL(file,import.meta.url),'utf8')));
  expect(stale).toEqual([]);
 });

 it('ships rebuilt public bundles without v15 routes',()=>{
  const artifacts=['studio.js','context-agent.js','agent-workbench.js'];
  const contents=artifacts.map(file=>readFileSync(new URL(`../../frappe_app/dsherp_bridge/public/dist/${file}`,import.meta.url),'utf8'));
  expect(contents.some(content=>content.includes('/desk/'))).toBe(true);
  expect(artifacts.filter((_,index)=>contents[index].includes('/app/'))).toEqual([]);
 });
});
