import React, {useEffect,useRef,useState} from 'react';
import {Alert,Button,Drawer,Input,Select} from 'antd';
import {capturePageContext} from './page-context.js';

const label = context => context?.page_type === 'unknown' ? context.reason : [context?.doctype,context?.name].filter(Boolean).join(' / ');
export default function ContextSidebar({api,capture=capturePageContext,pollInterval=5000}) {
  const [open,setOpen]=useState(false);
  const [sessions,setSessions]=useState([]);
  const [session,setSession]=useState(null);
  const [question,setQuestion]=useState('');
  const [page,setPage]=useState(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const generation=useRef(0);
  const selected=useRef(null);
  const visible=useRef(false);
  const pending=useRef(false);
  useEffect(()=>()=>{generation.current++;visible.current=false;},[]);
  function fail(e,ticket) {
    if(ticket!==generation.current)return;
    generation.current++;
    setError(e.message);setSession(null);setSessions([]);setBusy(false);pending.current=false;
  }
  async function restore(id, list=false) {
    const ticket=++generation.current;
    setError('');setSession(null);setBusy(true);pending.current=true;
    try {
      if(list){
        const history=await api('list_sessions');
        if(ticket!==generation.current)return;
        setSessions(history);id=id??history[0]?.id;
      }
      selected.current=id??null;
      if(id){
        const loaded=await api('get_session',{session_id:id});
        if(ticket!==generation.current)return;
        setSession(loaded);
      }
    } catch(e){fail(e,ticket);}
    finally{if(ticket===generation.current){setBusy(false);pending.current=false;}}
  }
  function show(){
    visible.current=true;setOpen(true);
    try{setPage(capture());}catch(e){setError(e.message);return;}
    void restore(selected.current,true);
  }
  function close(){
    visible.current=false;setOpen(false);generation.current++;
    setSession(null);setSessions([]);setBusy(false);pending.current=false;
  }
  function fresh(){
    generation.current++;selected.current=null;pending.current=false;
    setSession(null);setQuestion('');setError('');setBusy(false);
  }
  async function send(){
    if(pending.current||!question.trim()||error)return;
    const ticket=++generation.current;
    pending.current=true;setBusy(true);
    try{
      const context=capture();setPage(context);
      const result=await api('send_message',{session_id:selected.current,question:question.trim(),context,request_id:crypto.randomUUID()});
      if(ticket!==generation.current)return;
      selected.current=result.id;setSession(result);setQuestion('');
      setSessions(old=>[{id:result.id,title:result.title},...old.filter(s=>s.id!==result.id)]);
    }catch(e){fail(e,ticket);}
    finally{if(ticket===generation.current){pending.current=false;setBusy(false);}}
  }
  async function cancel(){
    const ticket=++generation.current;pending.current=true;setBusy(true);
    try{
      const result=await api('cancel_run',{session_id:session.id,run_id:session.active_run,request_id:crypto.randomUUID()});
      if(ticket===generation.current)setSession(result);
    }catch(e){fail(e,ticket);}
    finally{if(ticket===generation.current){pending.current=false;setBusy(false);}}
  }
  useEffect(()=>{
    if(!open||error)return;
    let stopped=false;
    let timer;
    async function poll(){
      if(stopped)return;
      if(!pending.current){
        const ticket=generation.current;
        try{
          setPage(capture());
          if(selected.current){
            const result=await api('get_session',{session_id:selected.current});
            if(!stopped&&ticket===generation.current)setSession(result);
          }
        }catch(e){if(!stopped)fail(e,ticket);}
      }
      if(!stopped)timer=setTimeout(poll,pollInterval);
    }
    timer=setTimeout(poll,pollInterval);
    return()=>{stopped=true;clearTimeout(timer);};
  },[open,error,api,capture,pollInterval]);
  return <>
    <Button aria-label="打开 Agent" onClick={show} style={{position:'fixed',right:20,bottom:20,zIndex:1040}}>Agent</Button>
    <Drawer title="业务 Agent" open={open} onClose={close} mask={false} autoFocus={false} keyboard={false} width={440} rootStyle={{top:'var(--navbar-height)'}}
      closable={false} extra={<Button aria-label="关闭 Agent" onClick={close}>关闭</Button>}>
      <div style={{display:'flex',gap:8,marginBottom:16}}>
        <Button onClick={fresh}>新建会话</Button>
        <Select aria-label="会话历史" placeholder="会话历史" value={session?.id} style={{flex:1}}
          options={sessions.map(s=>({value:s.id,label:s.title}))} onChange={id=>restore(id)}/>
        <Button onClick={()=>restore(selected.current,true)}>刷新记录</Button>
      </div>
      <p>本次页面：{label(page)}</p>
      {page?.dirty&&<p>未保存内容默认不发送，也不会自动保存或刷新表单。</p>}
      {error&&<Alert type="error" showIcon message={error} description="请刷新记录核实状态；不会自动重发。"/>}
      <div aria-live="polite">
        {session?.messages.map(m=><article key={m.id} style={{borderTop:'1px solid #eee',padding:'12px 0'}}>
          <small>使用页面：{label(m.context)}</small><p>{m.question}</p>
          <div style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{m.answer}</div>
          {m.error&&<Alert type="error" message={m.error}/>}
          {m.status==='Cancelled'&&<p>已取消后续工作；已发生的操作不会自动撤销。</p>}
        </article>)}
      </div>
      <Input.TextArea aria-label="业务问题" value={question} onChange={e=>setQuestion(e.target.value)} autoSize={{minRows:3,maxRows:8}} maxLength={8000}/>
      <div style={{display:'flex',gap:8,marginTop:12}}>
        <Button aria-label="发送问题" type="primary" onClick={send} loading={busy} disabled={busy||!!error||!!session?.active_run||!question.trim()}>发送问题</Button>
        {session?.active_run&&<Button onClick={cancel} disabled={busy}>停止运行</Button>}
      </div>
    </Drawer>
  </>;
}
