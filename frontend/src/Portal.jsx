import React,{useEffect,useRef,useState} from 'react';
import {Alert,Button,ConfigProvider,Empty,Select,Space} from 'antd';
import zhCN from 'antd/locale/zh_CN.js';
import './style.css';
const navigate=url=>window.location.assign(url);
export default function Portal({api,openDesk=navigate}){
 const [identity,setIdentity]=useState(null),[enterprise,setEnterprise]=useState(null);
 const [history,setHistory]=useState([]),[task,setTask]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const epoch=useRef(0);
 useEffect(()=>{let active=true;api('context').then(value=>{if(active)setIdentity(value);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;epoch.current++;};},[api]);
 async function load(id){
  const ticket=++epoch.current;setEnterprise(id);setHistory([]);setTask(null);setError('');setBusy(false);
  try{const items=await api('list_tasks',{enterprise:id});if(ticket===epoch.current)setHistory(items);}
  catch(e){if(ticket===epoch.current)setError(e.message);}
 }
 async function read(id){
  const ticket=++epoch.current;setTask(null);setError('');
  try{const value=await api('get_task',{task_id:id});if(ticket===epoch.current)setTask(value);}
  catch(e){if(ticket===epoch.current){setHistory([]);setError(e.message);}}
 }
 async function enter(){
  const ticket=++epoch.current;setBusy(true);setError('');
  try{const result=await api('desk_entry',{enterprise});if(ticket===epoch.current)openDesk(result.url);}
  catch(e){if(ticket===epoch.current){setHistory([]);setTask(null);setError(e.message);}}
  finally{if(ticket===epoch.current)setBusy(false);}
 }
 return <ConfigProvider locale={zhCN} prefixCls="dsh-ant" theme={{token:{colorPrimary:'#176b63',borderRadius:6}}}>
  <div className="dsherp-prototype">
   <header className="dsh-context"><div className="dsh-wordmark">dsherp <span>企业入口</span></div><Space>{identity?.user}<Button href="/app/user-profile">个人设置</Button></Space></header>
   <main className="dsh-main">
    {error&&<Alert type="error" showIcon message={error}/>}
    {identity?.platform_admin&&<Space><Button href="/app/ds-enterprise">企业管理</Button><Button href="/app/ds-membership">成员绑定</Button></Space>}
    {identity?.enterprises.length===0&&<Empty description="尚未加入可用企业"/>}
    {!!identity?.enterprises.length&&<>
     <h2>进入企业，继续工作</h2><p>业务操作在企业原生 Desk 完成；在业务页面打开 Agent 即可连续问询。</p>
     <Space wrap><Select aria-label="当前企业" placeholder="选择企业" style={{width:250}} value={enterprise} onChange={load}
      options={identity.enterprises.map(item=>({value:item.id,label:item.label,disabled:item.status!=='Ready'}))}/>
      <Button type="primary" disabled={!enterprise||busy} loading={busy} onClick={enter}>进入企业 Desk</Button></Space>
     {enterprise&&<section className="dsh-section"><h3>旧平台查询历史（只读）</h3><p>仅供查阅，不搬入新企业，不会重新执行。</p><Button onClick={()=>load(enterprise)}>刷新记录</Button>
      {history.map(item=><Button key={item.id} block onClick={()=>read(item.id)}>{item.question}</Button>)}
      {task&&<article aria-label="历史结果"><h3>{task.question}</h3><p>{task.status}</p><div className="dsh-agent-answer">{task.answer}</div>{task.error&&<Alert type="error" message={task.error}/>} {!!task.events?.length&&<details><summary>原始读取记录</summary><pre className="dsh-agent-json">{JSON.stringify(task.events,null,2)}</pre></details>}</article>}
     </section>}
    </>}
   </main>
  </div>
 </ConfigProvider>;
}
