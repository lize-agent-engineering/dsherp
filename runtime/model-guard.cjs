const {readFileSync}=require('node:fs');
exports.name='dsherp-model-authorization';
exports.inject=['llm'];

function createGuard(authorize){
  let disabled=false;
  return async function*(options,next){
    if(disabled)throw new Error('Model access disabled after failed authorization');
    try{
      const input_bytes=Buffer.byteLength(JSON.stringify({system:options.system,messages:options.messages,tools:options.tools}),'utf8');
      await authorize({input_bytes,max_output_tokens:options.maxTokens,
        provider:options.provider,model:options.model,purpose:options.purpose??'conversation'});
    }catch(error){disabled=true;throw error;}
    if(disabled)throw new Error('Model access disabled after failed authorization');
    yield* next();
  };
}
exports.createGuard=createGuard;
exports.apply=function(ctx){
  const config=JSON.parse(readFileSync(process.env.DSHERP_RUN_CONFIG,'utf8'));
  const endpoint=new URL('/api/method/dsherp_bridge.context_execution.reserve_model_call',config.business_url);
  ctx.on('llm/stream',createGuard(async metadata=>{
    const response=await fetch(endpoint,{method:'POST',redirect:'error',signal:AbortSignal.timeout(20000),
      headers:{'Content-Type':'application/json','X-Frappe-Site-Name':config.site},
      body:JSON.stringify({run_id:config.run_id,capability:config.capability,...metadata})});
    if(!response.ok)throw new Error(`Model authorization rejected (HTTP ${response.status})`);
    const body=await response.json();
    if(body.message?.allowed!==true)throw new Error('Invalid model authorization response');
  }));
};
