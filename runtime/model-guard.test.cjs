const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createGuard,watchFiles,verifyBusinessSkills}=require('./model-guard.cjs');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
async function consume(stream){for await(const chunk of stream){}}
const request={provider:'deepseek-official',model:'deepseek-v4-flash',messages:[],maxTokens:2048};
test('business catalog rejects unlisted skill directories',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'dsherp-skills-'));
  try{
    fs.mkdirSync(path.join(root,'config'));
    fs.copyFileSync(path.join(__dirname,'../config/business-skills.json'),path.join(root,'config/business-skills.json'));
    fs.cpSync(path.join(__dirname,'../business-skills'),path.join(root,'business-skills'),{recursive:true});
    verifyBusinessSkills(root);
    fs.mkdirSync(path.join(root,'business-skills/extra'));
    assert.throws(()=>verifyBusinessSkills(root),/catalog/);
  }finally{fs.rmSync(root,{recursive:true});}
});
test('ordinary and direct compaction requests both require authorization',async()=>{
  const seen=[];let calls=0;
  const guard=createGuard(async metadata=>seen.push(metadata));
  const next=async function*(){calls++;yield {type:'finish'};};
  await consume(guard(request,next));
  await consume(guard({...request,purpose:'compaction'},next));
  assert.equal(calls,2);assert.equal(seen.length,2);
  assert.deepEqual(seen.map(x=>x.purpose),['conversation','compaction']);
  assert.ok(seen.every(x=>x.input_bytes>0&&x.max_output_tokens===2048));
});
test('a swallowed compaction denial still poisons all subsequent model calls',async()=>{
  let authorizations=0,calls=0;
  const guard=createGuard(async()=>{authorizations++;throw new Error('denied');});
  const next=async function*(){calls++;};
  await assert.rejects(consume(guard({...request,purpose:'compaction'},next)),/denied/);
  await assert.rejects(consume(guard(request,next)),/disabled/);
  assert.equal(authorizations,1);assert.equal(calls,0);
});
test('runtime drift rejects subsequent streams even if the file is restored',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'dsherp-drift-'));
  try{
    const file=path.join(dir,'config');fs.writeFileSync(file,'original');
    const check=watchFiles([file]);let calls=0;
    const guard=createGuard(async()=>{check();});
    const next=async function*(){calls++;};
    await consume(guard(request,next));
    fs.writeFileSync(file,'changed');
    await assert.rejects(consume(guard({...request,purpose:'compaction'},next)),/changed/);
    fs.writeFileSync(file,'original');
    await assert.rejects(consume(guard(request,next)),/disabled/);
    assert.equal(calls,1);
  }finally{fs.rmSync(dir,{recursive:true});}
});
test('drift during a response cannot produce a successful terminal chunk',async()=>{
  let changed=false;const delivered=[];
  const guard=createGuard(async()=>{},()=>{if(changed)throw new Error('changed');});
  const next=async function*(){changed=true;yield {type:'finish'};};
  await assert.rejects(async()=>{for await(const item of guard(request,next))delivered.push(item);},/changed/);
  assert.deepEqual(delivered,[]);
});
test('finish and errors are reported without affecting the stream',async()=>{
  const reports=[];
  const guard=createGuard(async()=>{},()=>{},async record=>{reports.push(record);throw new Error('sink down');});
  const next=async function*(){yield {type:'chunk'};yield {type:'finish',usage:{input:3,output:4}};};
  const delivered=[];for await(const item of guard(request,next))delivered.push(item);
  assert.equal(delivered.length,2);
  assert.deepEqual(reports.map(record=>record.kind),['model_response']);
  assert.deepEqual(reports[0].payload.usage,{input:3,output:4});
  assert.deepEqual(reports[0].payload.chunk_keys,['type','usage']);
  const failing=createGuard(async()=>{},()=>{},async record=>{reports.push(record);});
  await assert.rejects(consume(failing(request,async function*(){throw new Error('provider down');})),/provider down/);
  assert.equal(reports.at(-1).kind,'model_error');assert.equal(reports.at(-1).error_class,'Error');
  assert.ok(!JSON.stringify(reports).includes('provider down'));
});
