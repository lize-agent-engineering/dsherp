const {readFileSync,readdirSync,lstatSync}=require('node:fs');
const {createHash}=require('node:crypto');
const path=require('node:path');
exports.name='dsherp-model-authorization';
exports.inject=['llm'];

// The runtime reports in-stream failures with a code; a thrown failure only carries a
// JS error name. Both must land in one vocabulary or the server cannot tell a dead
// provider from a bad request, and the circuit never opens.
const THROWN_ERROR_CLASSES={TimeoutError:'TIMEOUT',AbortError:'TIMEOUT',ConnectTimeoutError:'TIMEOUT',
  HeadersTimeoutError:'TIMEOUT',BodyTimeoutError:'TIMEOUT',SocketError:'TRANSPORT',ConnectionRefusedError:'TRANSPORT'};
function thrownErrorClass(error){
  const name=error?.name;
  if(THROWN_ERROR_CLASSES[name])return THROWN_ERROR_CLASSES[name];
  // undici surfaces a network failure as a TypeError carrying the socket error as cause.
  if(name==='TypeError'&&error?.cause)return 'TRANSPORT';
  return name??'Error';
}
exports.thrownErrorClass=thrownErrorClass;

function createGuard(authorize,check=()=>{},report=async()=>{},requireSystem=null){
  let disabled=false;
  const safeReport=async record=>{try{await report(record);}catch{}};
  return async function*(options,next){
    if(disabled)throw new Error('Model access disabled after failed authorization');
    try{
      // Before authorize, so that "the skill summary did not load" costs zero provider
      // requests rather than one that is answered badly. Treated as a failed authorization:
      // it poisons the rest of the run the same way, because a run whose system prompt does
      // not say what it is operating under must not continue under a different assumption.
      if(requireSystem)requireSystem(options.system);
      const input_bytes=Buffer.byteLength(JSON.stringify({system:options.system,messages:options.messages,tools:options.tools}),'utf8');
      await authorize({input_bytes,max_output_tokens:options.maxTokens,
        provider:options.provider,model:options.model,purpose:options.purpose??'conversation'});
    }catch(error){disabled=true;throw error;}
    if(disabled)throw new Error('Model access disabled after failed authorization');
    // The provider's token counts arrive on their own chunk (`{type:'usage', usage}`),
    // emitted just before the terminal `{type:'finish', reason}` - the finish chunk never
    // carries them. Reading usage off finish alone records every single call as "the
    // provider did not account for this one", so actual_input_tokens/actual_output_tokens
    // stay 0 on every run ever made, with a real provider as much as with a stub.
    // Scoped to this stream so one call's numbers cannot be attributed to the next.
    let reported=null;
    try{
      for await(const chunk of next()){
        if(chunk.type==='usage')reported=chunk.usage??null;
        if(chunk.type==='finish'){
          check();
          if(chunk.reason?.kind==='error'){
            const code=chunk.reason.failure?.code;
            await safeReport({kind:'model_error',error_class:typeof code==='string'&&code?code:'ProviderError',payload:{purpose:options.purpose??'conversation'}});
          }else{
            await safeReport({kind:'model_response',payload:{usage:chunk.usage??reported??null,chunk_keys:Object.keys(chunk),purpose:options.purpose??'conversation',model:options.model}});
          }
        }
        yield chunk;
      }
      check();
    }catch(error){
      disabled=true;
      await safeReport({kind:'model_error',error_class:thrownErrorClass(error),payload:{purpose:options.purpose??'conversation'}});
      throw error;
    }
  };
}
exports.createGuard=createGuard;
function watchFiles(files){
  const hashes=()=>files.map(file=>createHash('sha256').update(readFileSync(file)).digest('hex')).join(':');
  const initial=hashes();
  return ()=>{if(hashes()!==initial)throw new Error('Runtime configuration changed during execution');};
}
exports.watchFiles=watchFiles;
function verifyBusinessSkills(root){
  const manifest=JSON.parse(readFileSync(path.join(root,'config/business-skills.json'),'utf8'));
  if(manifest.schema_version!==1||!Array.isArray(manifest.skills)||!manifest.skills.length)throw new Error('Invalid skill manifest');
  const names=manifest.skills.map(row=>row.name);
  if(new Set(names).size!==names.length||names.some(name=>typeof name!=='string'||! /^[a-z][a-z0-9-]*$/.test(name)))throw new Error('Invalid skill catalog');
  const directory=path.join(root,'business-skills');
  if(lstatSync(directory).isSymbolicLink())throw new Error('Skill symbolic links are forbidden');
  if(JSON.stringify(readdirSync(directory).sort())!==JSON.stringify([...names].sort()))throw new Error('Unexpected skill catalog');
  for(const row of manifest.skills){
    const folder=path.join(directory,row.name),file=path.join(folder,'SKILL.md');
    if(lstatSync(folder).isSymbolicLink()||lstatSync(file).isSymbolicLink())throw new Error('Skill symbolic links are forbidden');
    if(JSON.stringify(readdirSync(folder))!==JSON.stringify(['SKILL.md']))throw new Error('Unexpected skill catalog files');
    const content=readFileSync(file);
    if(createHash('sha256').update(content).digest('hex')!==row.sha256)throw new Error('Business skill digest mismatch');
    const header=content.toString('utf8').split('---')[1]?.split('\n');
    if(!header?.includes(`name: ${row.name}`)||!header.includes(`version: ${row.version}`))throw new Error('Business skill version mismatch');
  }
}
exports.verifyBusinessSkills=verifyBusinessSkills;
exports.apply=function(ctx){
  const root=path.resolve(__dirname,'..');
  verifyBusinessSkills(root);
  const files=JSON.parse(readFileSync(path.join(root,'config/runtime-files.json'),'utf8'));
  const checkFiles=watchFiles([...files.map(file=>path.join(root,file)),process.env.DSHERP_RUN_CONFIG]);
  const check=()=>{checkFiles();verifyBusinessSkills(root);};
  const config=JSON.parse(readFileSync(process.env.DSHERP_RUN_CONFIG,'utf8'));
  if(!['query','operation','configuration'].includes(config.domain)||config.domain!==process.env.DSHERP_DOMAIN)throw new Error('Business domain mismatch');
  if(!/^[a-f0-9]{64}$/.test(config.deployment_digest??''))throw new Error('Missing deployment digest');
  // Mirrors dsherp/runtime_revision.py: the provider key is deliberately not bound here, so
  // rotating it does not end every conversation's native session (S9). The endpoint is.
  const material=[files.map(file=>[file,createHash('sha256').update(readFileSync(path.join(root,file))).digest('hex')]),
    ['DEEPSEEK_BASE_URL'].map(key=>config[key]),config.deployment_digest];
  const revision=createHash('sha256').update(JSON.stringify(material)).digest('hex');
  if(revision!==config.runtime_revision)throw new Error('Runtime revision mismatch');
  const endpoint=new URL('/api/method/dsherp_bridge.context_execution.reserve_model_call',config.business_url);
  const recordEndpoint=new URL('/api/method/dsherp_bridge.context_execution.record_run_event',config.business_url);
  const report=async record=>{
    await fetch(recordEndpoint,{method:'POST',redirect:'error',signal:AbortSignal.timeout(5000),
      headers:{'Content-Type':'application/json','X-Frappe-Site-Name':config.site},
      body:JSON.stringify({run_id:config.run_id,capability:config.capability,events:[{...record,source:'runner'}]})});
  };
  // Derived here from the manifest, independently of runtime/prompt-sections.cjs which
  // builds the text: two derivations of one pinned fact, the same discipline the repository
  // already uses for the host and bridge copies of the metering rules. If they ever disagree,
  // the run stops instead of quietly running without its skill.
  const skillMarker=(()=>{
    const manifest=JSON.parse(readFileSync(path.join(root,'config/business-skills.json'),'utf8'));
    const name='erp-'+config.domain;
    const listed=(manifest.skills||[]).find(row=>row&&row.name===name);
    if(!listed?.version)throw new Error('Business skill not in manifest: '+name);
    return '业务技能：'+name+' v'+listed.version;
  })();
  const requireSystem=system=>{
    if(!String(system??'').includes(skillMarker))throw new Error('System prompt is missing the pinned business skill summary');
  };
  ctx.on('llm/stream',createGuard(async metadata=>{
    check();
    const response=await fetch(endpoint,{method:'POST',redirect:'error',signal:AbortSignal.timeout(20000),
      headers:{'Content-Type':'application/json','X-Frappe-Site-Name':config.site},
      body:JSON.stringify({run_id:config.run_id,capability:config.capability,runtime_revision:revision,domain:config.domain,claimed_budget:config.budget,...metadata})});
    if(!response.ok)throw new Error(`Model authorization rejected (HTTP ${response.status})`);
    const body=await response.json();
    if(body.message?.allowed!==true)throw new Error('Invalid model authorization response');
    check();
  },check,report,requireSystem));
};
