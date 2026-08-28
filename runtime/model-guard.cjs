const {readFileSync}=require('node:fs');
const {createHash}=require('node:crypto');
const path=require('node:path');
exports.name='dsherp-model-authorization';
exports.inject=['llm'];

function createGuard(authorize,check=()=>{}){
  let disabled=false;
  return async function*(options,next){
    if(disabled)throw new Error('Model access disabled after failed authorization');
    try{
      const input_bytes=Buffer.byteLength(JSON.stringify({system:options.system,messages:options.messages,tools:options.tools}),'utf8');
      await authorize({input_bytes,max_output_tokens:options.maxTokens,
        provider:options.provider,model:options.model,purpose:options.purpose??'conversation'});
    }catch(error){disabled=true;throw error;}
    if(disabled)throw new Error('Model access disabled after failed authorization');
    try{
      for await(const chunk of next()){
        if(chunk.type==='finish')check();
        yield chunk;
      }
      check();
    }catch(error){disabled=true;throw error;}
  };
}
exports.createGuard=createGuard;
function watchFiles(files){
  const hashes=()=>files.map(file=>createHash('sha256').update(readFileSync(file)).digest('hex')).join(':');
  const initial=hashes();
  return ()=>{if(hashes()!==initial)throw new Error('Runtime configuration changed during execution');};
}
exports.watchFiles=watchFiles;
exports.apply=function(ctx){
  const root=path.resolve(__dirname,'..');
  const files=JSON.parse(readFileSync(path.join(root,'config/runtime-files.json'),'utf8'));
  const check=watchFiles([...files.map(file=>path.join(root,file)),process.env.DSHERP_RUN_CONFIG]);
  const config=JSON.parse(readFileSync(process.env.DSHERP_RUN_CONFIG,'utf8'));
  const material=[files.map(file=>[file,createHash('sha256').update(readFileSync(path.join(root,file))).digest('hex')]),
    ['DEEPSEEK_API_KEY','DSH_MODEL','DEEPSEEK_BASE_URL'].map(key=>config[key])];
  const revision=createHash('sha256').update(JSON.stringify(material)).digest('hex');
  if(revision!==config.runtime_revision)throw new Error('Runtime revision mismatch');
  const endpoint=new URL('/api/method/dsherp_bridge.context_execution.reserve_model_call',config.business_url);
  ctx.on('llm/stream',createGuard(async metadata=>{
    check();
    const response=await fetch(endpoint,{method:'POST',redirect:'error',signal:AbortSignal.timeout(20000),
      headers:{'Content-Type':'application/json','X-Frappe-Site-Name':config.site},
      body:JSON.stringify({run_id:config.run_id,capability:config.capability,runtime_revision:revision,...metadata})});
    if(!response.ok)throw new Error(`Model authorization rejected (HTTP ${response.status})`);
    const body=await response.json();
    if(body.message?.allowed!==true)throw new Error('Invalid model authorization response');
    check();
  },check));
};
