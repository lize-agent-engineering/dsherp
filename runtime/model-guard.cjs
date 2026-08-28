const {readFileSync,readdirSync,lstatSync}=require('node:fs');
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
  if(!['query','operation'].includes(config.domain)||config.domain!==process.env.DSHERP_DOMAIN)throw new Error('Business domain mismatch');
  const material=[files.map(file=>[file,createHash('sha256').update(readFileSync(path.join(root,file))).digest('hex')]),
    ['DEEPSEEK_API_KEY','DSH_MODEL','DEEPSEEK_BASE_URL'].map(key=>config[key])];
  const revision=createHash('sha256').update(JSON.stringify(material)).digest('hex');
  if(revision!==config.runtime_revision)throw new Error('Runtime revision mismatch');
  const endpoint=new URL('/api/method/dsherp_bridge.context_execution.reserve_model_call',config.business_url);
  ctx.on('llm/stream',createGuard(async metadata=>{
    check();
    const response=await fetch(endpoint,{method:'POST',redirect:'error',signal:AbortSignal.timeout(20000),
      headers:{'Content-Type':'application/json','X-Frappe-Site-Name':config.site},
      body:JSON.stringify({run_id:config.run_id,capability:config.capability,runtime_revision:revision,domain:config.domain,...metadata})});
    if(!response.ok)throw new Error(`Model authorization rejected (HTTP ${response.status})`);
    const body=await response.json();
    if(body.message?.allowed!==true)throw new Error('Invalid model authorization response');
    check();
  },check));
};
