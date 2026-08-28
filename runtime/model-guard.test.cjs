const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createGuard}=require('./model-guard.cjs');
async function consume(stream){for await(const chunk of stream){}}
const request={provider:'deepseek-official',model:'deepseek-v4-flash',messages:[],maxTokens:2048};
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
