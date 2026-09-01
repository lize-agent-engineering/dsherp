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
});
