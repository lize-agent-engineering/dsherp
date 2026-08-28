import React,{useEffect,useRef,useState} from 'react';
import {Alert,Button,ConfigProvider,Empty,Input,Select,Space,Table} from 'antd';
import zhCN from 'antd/locale/zh_CN.js';
import './style.css';
export default function Portal({api}){
 const [identity,setIdentity]=useState(null),[enterprise,setEnterprise]=useState(null),[doctype,setDoctype]=useState('Item'),[name,setName]=useState(''),[result,setResult]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
 const request=useRef(null);
 useEffect(()=>{const controller=new AbortController();api('context',{},controller.signal).then(value=>{if(!controller.signal.aborted)setIdentity(value);}).catch(e=>{if(!controller.signal.aborted)setError(e.message);});return()=>{controller.abort();request.current?.abort();};},[api]);
 function clear(){request.current?.abort();setResult(null);setName('');setError('');setLoading(false);}
 async function read(method){
  request.current?.abort();const controller=new AbortController();request.current=controller;setLoading(true);setError('');setResult(null);
  try{const value=await api(method,{enterprise,doctype,...(method==='read_record'?{name}: {})},controller.signal);if(!controller.signal.aborted)setResult({method,value});}
  catch(e){if(!controller.signal.aborted)setError(e.message);}
  finally{if(!controller.signal.aborted)setLoading(false);}
 }
 const selected=identity?.enterprises.find(x=>x.id===enterprise);
 const rows=result?(result.method==='read_schema'?result.value.fields.map(f=>({key:f.fieldname,label:f.label||f.fieldname,value:f.fieldtype})):Object.entries(result.value.fields).map(([key,value])=>({key,label:key,value:typeof value==='object'?JSON.stringify(value):String(value??'')}))):[];
 return <ConfigProvider locale={zhCN} prefixCls="dsh-ant" theme={{token:{colorPrimary:'#176b63',borderRadius:6}}}><div className="dsherp-prototype">
 <header className="dsh-context"><div className="dsh-wordmark">dsherp <span>业务工作台</span></div><Space>{identity&&<span>{identity.user}</span>}<Button href="/app/user-profile">个人设置</Button></Space></header>
 <main className="dsh-main"><h2>企业业务查询</h2><p className="dsh-muted">当前查询来自真实业务 Site，按当前成员绑定的业务用户权限执行。</p>
 {error&&<Alert showIcon type="error" message={error}/>}
 {!identity&&!error&&<p>正在读取登录身份…</p>}
 {identity&&identity.enterprises.length===0&&<Empty description="尚未加入可用企业"/>}
 {identity?.platform_admin&&<Space className="dsh-spaced"><Button href="/app/ds-enterprise">企业管理</Button><Button href="/app/ds-membership">成员绑定</Button></Space>}
 {identity?.enterprises.length>0&&<><section className="dsh-section"><Space wrap><label htmlFor="enterprise">当前企业</label><Select id="enterprise" aria-label="当前企业" placeholder="选择已加入的企业" style={{width:250}} value={enterprise} options={identity.enterprises.map(x=>({value:x.id,label:x.label,disabled:x.status!=='Ready'}))} onChange={value=>{clear();setEnterprise(value);}}/></Space></section>
 {!enterprise?<p>请选择企业后查询业务记录。</p>:<section className="dsh-section"><h3>{selected.label}</h3><Space wrap><Select aria-label="业务对象" value={doctype} options={[{value:'Item',label:'物料'},{value:'Customer',label:'客户'}]} onChange={value=>{clear();setDoctype(value);}} style={{width:120}}/><Input aria-label="记录编号" placeholder="输入记录编号" value={name} onChange={e=>{request.current?.abort();setLoading(false);setResult(null);setName(e.target.value);}} style={{width:260}}/><Button type="primary" disabled={!name.trim()} loading={loading} onClick={()=>read('read_record')}>查询记录</Button><Button disabled={loading} onClick={()=>read('read_schema')}>查看可读字段</Button></Space></section>}
 {result&&<section className="dsh-section"><h3>{result.method==='read_record'?result.value.name:`${doctype} · 当前可读字段`}</h3><Table rowKey="key" size="small" pagination={{pageSize:12}} dataSource={rows} columns={[{title:'字段',dataIndex:'label',width:230},{title:result.method==='read_record'?'值':'类型',dataIndex:'value'}]}/></section>}
 </>}
 <p className="dsh-muted">Agent 查询与应用构建发布仍在接入中；当前为真实只读查询，不执行写入。</p>
 </main></div></ConfigProvider>;
}
