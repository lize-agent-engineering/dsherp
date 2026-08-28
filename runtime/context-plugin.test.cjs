const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');

function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
async function setup(){
  let request,cleanup;const created=deferred(),entered=deferred();let disposals=0;
  class Transport {onRequest(fn){request=fn;}start(){}close(){}async flush(){}}
  class Server {async initialize(){return {};}async shutdown(){}}
  const exports={};
  runInNewContext(readFileSync(__dirname+'/context-plugin.cjs','utf8'),{exports,process:{stdin:{},stdout:{}},setImmediate:()=>{}});
  const ctx={
    loader:{import:async name=>name.endsWith('sdk-protocol')?{JsonRpcLineTransport:Transport}:name.endsWith('jsonrpc-server')?{HarnessSdkJsonRpcServer:Server}:{createUserMessage:x=>x}},
    get:()=>null,root:{fiber:{dispose:async()=>{}}},sessionPersistence:{list:async()=>[]},
    agents:{create:async()=>{entered.resolve();return created.promise;},get:()=>null},
    effect:fn=>{cleanup=fn();},
  };
  await exports.apply(ctx);
  await request('initialize',{cwd:'/synthetic',provider:'synthetic',model:'synthetic',maxTokens:10});
  return {request,cleanup:()=>cleanup(),entered,created,handle:{agent:{},dispose:async()=>{disposals++;}},disposals:()=>disposals};
}
test('dispose waits for native creation and releases exactly the completed handle',async()=>{
  const s=await setup();
  const opening=s.request('dsherp/session/open',{sessionId:'one',resume:false});
  await s.entered.promise;
  const closing=s.cleanup();
  const observed=closing.then(()=>null,e=>e);
  s.created.resolve(s.handle);
  await opening;
  assert.equal(await observed,null);
  assert.equal(s.disposals(),1);
  await assert.rejects(s.request('session/prompt',{sessionId:'one'}),/closing/);
});
test('failed creation remains a request error but cannot break cleanup',async()=>{
  const s=await setup();
  const opening=s.request('dsherp/session/open',{sessionId:'one',resume:false});
  const rejected=assert.rejects(opening,/native creation failed/);
  await s.entered.promise;
  const closing=s.cleanup();const observed=closing.then(()=>null,e=>e);
  s.created.reject(new Error('native creation failed'));
  await rejected;
  assert.equal(await observed,null);
  assert.equal(s.disposals(),0);
});
