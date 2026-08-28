exports.name = 'dsherp-context-protocol';
exports.inject = ['agents', 'sessionPersistence'];
exports.apply = async function (ctx) {
  // Public Loader.import resolves modules inside the pinned packaged runtime.
  const {JsonRpcLineTransport} = await ctx.loader.import('@deepseek-ai/dsh-sdk-protocol');
  const {HarnessSdkJsonRpcServer} = await ctx.loader.import('@deepseek-ai/dsh-sdk-jsonrpc-server');
  const {createUserMessage} = await ctx.loader.import('@deepseek-ai/dsh-llm');
  const transport = new JsonRpcLineTransport(process.stdin, process.stdout);
  // Reuse the official handshake and event notifications; it never owns our handles.
  const server = new HarnessSdkJsonRpcServer(ctx, transport);
  const handles = new Map();
  let options;
  let closing = false;
  let teardown;
  let opening;
  async function dispose() {
    if (!teardown) teardown = (async()=>{
      closing = true;
      // Failed opens report to their own caller; cleanup still drains the reservation.
      if(opening) await Promise.allSettled([opening]);
      for(const handle of handles.values()) await handle.dispose();
      handles.clear();
      await server.shutdown();
    })();
    return teardown;
  }
  function handleFor(id) {
    const handle=handles.get(id);
    if(!handle || ctx.agents.get(id)!==handle.agent)throw new Error('Session must be explicitly opened');
    return handle;
  }
  transport.onRequest(async(method,params)=>{
    if(closing)throw new Error('Runtime is closing');
    if(method==='initialize'){
      if(options)throw new Error('Runtime already initialized');
      await ctx.get('loader')?.await();
      const result=await server.initialize(params);
      options={cwd:params.cwd,provider:params.provider,model:params.model,maxTokens:params.maxTokens};
      return result;
    }
    if(method==='shutdown'){
      await dispose();
      setImmediate(async()=>{
        try{await transport.flush();await ctx.root.fiber.dispose();process.exit(0);}
        catch{process.exit(1);}
      });
      return {};
    }
    if(!options)throw new Error('Initialize required');
    if(method==='dsherp/session/open'){
      const id=params.sessionId;
      if(typeof id!=='string'||!/^[a-zA-Z0-9_-]{1,128}$/.test(id)||typeof params.resume!=='boolean')throw new Error('Invalid session open request');
      if(handles.size)throw new Error('One session writer per runtime');
      // Reserve before awaiting the native factory: concurrent open must fail.
      handles.set(id,null);
      opening=(async()=>{try{
        if(!params.resume && (await ctx.sessionPersistence.list()).some(meta=>String(meta.id)===id)) {
          throw new Error('Persisted session requires explicit resume');
        }
        const agentOptions={provider:options.provider,model:options.model,maxTokens:options.maxTokens};
        const handle=params.resume
          ? await ctx.agents.resume({resumeSessionId:id,agentOptions})
          : await ctx.agents.create({sessionId:id,meta:{cwd:options.cwd},agentOptions});
        handles.set(id,handle);
        return {sessionId:id,source:params.resume?'resume':'startup'};
      }catch(e){handles.delete(id);throw e;}})();
      return opening;
    }
    if(method==='session/prompt'){
      const handle=handleFor(params.sessionId);
      if(handle.agent.status!=='idle')throw new Error('Session is already running');
      const message=createUserMessage({content:params.contentBlocks,source:{kind:'user'}});
      handle.agent.followup(message);
      return {messageId:message.id};
    }
    if(method==='dsherp/session/cancel'){
      const handle=handleFor(params.sessionId);
      // Node fetch can attach a non-enumerable stack to an extensible abort reason;
      // native durable events require lossless JSON, so preserve the readonly cause.
      handle.agent.cancel(Object.freeze({kind:'user'}));
      await handle.agent.whenIdle();
      return {sessionId:params.sessionId,status:'idle'};
    }
    throw new Error('Unknown dsherp runtime method');
  });
  ctx.effect(()=>{
    transport.start();
    return async()=>{await dispose();transport.close();};
  },'dsherp.context.protocol');
};
