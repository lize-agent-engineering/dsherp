import React,{useRef,useState} from 'react';
import {Alert,Button,Space,Typography} from 'antd';
import ConfigurationProposal,{ConfigurationChanges} from './ConfigurationProposal.jsx';

export default function ConfigurationBundle({bundle,onPrepare,onConfirm,onTransfer,onPublish}){
 const [confirmation,setConfirmation]=useState(null),[transfer,setTransfer]=useState(bundle.transfer),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const requested=useRef(false);
 async function prepare(){
  if(requested.current||!bundle.execution_ready||!bundle.preview_available)return;
  requested.current=true;setBusy(true);
  try{setConfirmation(await onPrepare({bundle_id:bundle.id,digest:bundle.digest}));}
  catch(error){setError(error.message);}
 finally{setBusy(false);}
 }
 async function send(){
  if(requested.current||!bundle.execution_ready||!bundle.preview_transfer_available)return;
  requested.current=true;setBusy(true);
  try{setTransfer(await onTransfer({bundle_id:bundle.id,digest:bundle.digest,request_id:crypto.randomUUID()}));requested.current=false;}
  catch(error){setError(error.message);}
  finally{setBusy(false);}
 }
 async function publish(){
  if(requested.current||!transfer||!bundle.execution_ready)return;
  requested.current=true;setBusy(true);
  try{setConfirmation(await onPublish({transfer_id:transfer.id,digest:bundle.digest}));}
  catch(error){setError(error.message);}
  finally{setBusy(false);}
 }
 if(confirmation)return <ConfigurationProposal proposal={confirmation} onConfirm={onConfirm}/>;
 return <Space direction="vertical" style={{width:'100%'}}>
  <Typography.Text strong>应用配置提案</Typography.Text>
  <Typography.Text type="secondary">配置包：{bundle.digest}</Typography.Text>
  <ConfigurationChanges changes={bundle.changes}/>
  {!bundle.execution_ready&&<Alert type="info" message="来源运行尚未成功完成，不能应用此配置"/>}
  {!bundle.preview_available&&!bundle.preview_transfer_available&&<Typography.Text>配置包尚未应用；需在隔离预览站点继续</Typography.Text>}
  {error&&<Alert type="error" message={error} description="请刷新记录核实；不会自动重发。"/>}
  {bundle.preview_available&&<Button onClick={prepare} loading={busy} disabled={!bundle.execution_ready||requested.current}>查看预览确认</Button>}
  {bundle.preview_transfer_available&&!transfer&&<Button onClick={send} loading={busy} disabled={!bundle.execution_ready||requested.current}>发送到隔离预览</Button>}
  {transfer&&<Typography.Link href={transfer.preview_url} target="_blank" rel="noreferrer">打开隔离预览</Typography.Link>}
  {transfer?.expires_at&&<Typography.Text type="secondary">交接有效至 {transfer.expires_at}（站点时间）；过期后需重新发送到隔离预览</Typography.Text>}
  {transfer&&<Button onClick={publish} loading={busy} disabled={!bundle.execution_ready||requested.current}>读取预览结果并准备发布</Button>}
 </Space>;
}
