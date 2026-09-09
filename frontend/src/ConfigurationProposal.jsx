import React,{useRef,useState} from 'react';
import {Alert,Button,Space,Table,Typography} from 'antd';
import {isExpired} from './agent-format.js';
import {requestId} from './context-api.js';

export default function ConfigurationProposal(props){
 return <Confirmation key={`${props.proposal.id}:${props.proposal.digest}`} {...props}/>;
}
export function ConfigurationChanges({changes}){
 return <Table size="small" pagination={false} rowKey={row=>`${row.action}:${row.object}`} dataSource={changes}
   columns={[{title:'配置对象',dataIndex:'object'},{title:'动作',dataIndex:'action'},{title:'具体内容',dataIndex:'detail'}]}/>;
}
function Confirmation({proposal,onConfirm,onVerify}){
 if(!['preview','publish'].includes(proposal.purpose))throw new Error('不支持的配置确认类型');
 const [local,setLocal]=useState(null),[verification,setVerification]=useState(null),[verifying,setVerifying]=useState(false);const claimed=useRef(false);
 const result=local||proposal.execution;
 const preview=proposal.purpose==='preview';
 const expired=()=>isExpired(proposal.expires_at);
 const disabled=Boolean(result)||proposal.status!=='Pending'||proposal.execution_ready===false||expired();
 async function confirm(){
  if(claimed.current||disabled||expired())return;
  claimed.current=true;setLocal({status:'Running'});
  try{setLocal(await onConfirm({proposal_id:proposal.id,digest:proposal.digest,request_id:requestId()}));}
  catch(error){setLocal({status:'Unknown',error:error.message});}
 }
 async function verify(){
  if(verifying)return;setVerifying(true);
  try{setVerification(await onVerify({proposal_id:proposal.id}));}
  catch(error){setVerification({note:error.message,observations:[]});}
  finally{setVerifying(false);}
 }
 const incomplete=result&&['Partial','Unknown','Failed'].includes(result.status);
 return <Space direction="vertical" style={{width:'100%'}}>
  <Typography.Text strong>{preview?'隔离预览':'目标发布'}</Typography.Text>
  <Typography.Text>{proposal.target}</Typography.Text>
  <Typography.Text type="secondary">配置包：{proposal.digest} · 基线：{proposal.baseline}</Typography.Text>
  <ConfigurationChanges changes={proposal.changes}/>
  {expired()&&!result&&<Alert type="warning" message="确认已过期，请重新生成确认"/>}
  {proposal.execution_ready===false&&!result&&<Alert type="info" message="来源运行尚未成功完成，不能应用此配置"/>}
  {result?.status==='Succeeded'&&<Alert type="success" message={preview?'隔离预览配置已应用；目标站点尚未发布':'目标配置已发布并读取结果'}/>}
  {incomplete&&<Alert type="warning" message={`${preview?'预览应用':'发布'}未全部完成，请核实逐项结果；已发生的配置变更不保证回滚`}/>}
  {result?.error&&<Typography.Text type="danger">{result.error}</Typography.Text>}
  {result?.steps&&<Table size="small" pagination={false} rowKey="step_id" dataSource={result.steps}
   columns={[{title:'配置对象',dataIndex:'object'},{title:'执行结果',dataIndex:'status'}]}/>}
  {incomplete&&onVerify&&<Button onClick={verify} loading={verifying}>核实当前配置</Button>}
  {verification?.note&&<Typography.Text>{verification.note}</Typography.Text>}
  {verification?.observations?.length>0&&<Table size="small" pagination={false} rowKey="object" dataSource={verification.observations}
   columns={[{title:'配置对象',dataIndex:'object'},{title:'当前状态',dataIndex:'state'},{title:'当前版本',dataIndex:'version'}]}/>}
  <Button type="primary" disabled={disabled} loading={result?.status==='Running'} onClick={confirm}>{preview?'确认应用到隔离预览':'确认发布到目标站点'}</Button>
 </Space>;
}
