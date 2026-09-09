import React, {useRef, useState} from 'react';
import {Alert, Button, Space, Table, Typography} from 'antd';
import {applyFormProposal} from './form-fill.js';
import {proposalRows} from './proposal-rows.js';
import {isExpired} from './agent-format.js';
import {requestId} from './context-api.js';

const display = value => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const actions={create:'创建',update:'修改',submit:'提交',cancel:'取消',fill:'填入当前草稿'};
const displayChange=(value,change)=>change.field==='docstatus'?['草稿','已提交','已取消'][value]:display(value);

// A new immutable proposal gets a fresh interaction state; late promises cannot
// turn a replacement proposal into a successful execution.
export default function OperationProposal(props) {
  return <Proposal key={`${props.proposal.id}:${props.proposal.digest}`} {...props}/>;
}

function Proposal({proposal, onConfirm,onReject,onApply=applyFormProposal,onVerify}) {
  const claimed = useRef(false);
  const [localState, setState] = useState(null);
  const [verification,setVerification]=useState(null);
  const [verifying,setVerifying]=useState(false);
  const state = localState || proposal.execution;
  const expired = isExpired(proposal.expires_at);
  const canReject=Boolean(onReject) && proposal.status==='Pending' && !expired && !state && proposal.execution_ready!==false;
  const stockEntries=(['submit','cancel'].includes(proposal.action)
    && proposal.impact?.kind==='stock'
    && Array.isArray(proposal.impact.entries)
    && proposal.impact.entries.length
    && proposal.impact.entries.every(entry=>entry
      && Object.keys(entry).length===4
      && ['item_code','quantity','uom','warehouse'].every(key=>Object.hasOwn(entry,key))
      && typeof entry.item_code==='string' && entry.item_code
      && typeof entry.uom==='string' && entry.uom
      && typeof entry.warehouse==='string' && entry.warehouse
      && typeof entry.quantity==='number' && Number.isFinite(entry.quantity) && entry.quantity!==0))
    ?proposal.impact.entries:[];
  async function verify(){
    if(verifying)return;
    setVerifying(true);
    try{setVerification(await onVerify({proposal_id:proposal.id}));}
    catch(error){setVerification({note:error.message});}
    finally{setVerifying(false);}
  }
  async function confirm() {
    if (claimed.current || proposal.execution_ready===false || isExpired(proposal.expires_at) || proposal.status !== 'Pending') return;
    claimed.current = true;
    setState({status: 'Running'});
    try {
      const result = await onConfirm({proposal_id: proposal.id, digest: proposal.digest, request_id: requestId()});
      if(result.status==='Authorized'&&proposal.action==='fill'){
        if(result.target!=='browser-draft'||result.doctype!==proposal.doctype||result.name!==proposal.name||result.version!==proposal.version)throw new Error('填入授权与目标不一致，请核实');
        for(const change of proposal.changes)if(JSON.stringify(result.values?.[change.field])!==JSON.stringify(change.after))throw new Error('填入授权内容不一致，请核实');
        setState(await onApply(proposal));return;
      }
      setState(result.status === 'Succeeded' ? result : {...result, error: result.error || '执行结果尚未核实，请在本提案卡中核实业务结果'});
    } catch (error) {
      setState({status: 'Unknown', error: error.message});
    }
  }
  async function reject() {
    if (claimed.current || !onReject || proposal.execution_ready===false || isExpired(proposal.expires_at) || proposal.status !== 'Pending') return;
    claimed.current = true;
    setState({status: 'Rejecting'});
    try {
      const result = await onReject({proposal_id: proposal.id, digest: proposal.digest, request_id: requestId()});
      setState(result?.status === 'Rejected' ? result : {status: 'Unknown', error: '拒绝结果尚未核实，请刷新记录核实'});
    } catch (error) {
      setState({status: 'Unknown', error: error.message});
    }
  }
  return <Space direction="vertical" style={{width: '100%'}}>
    <Typography.Text strong>{proposal.doctype} / {proposal.name || '新记录'}</Typography.Text>
    <Typography.Text type="secondary">操作：{actions[proposal.action]} · 基线版本：{proposal.version || '新建'}</Typography.Text>
    {proposal.action==='submit'&&<Typography.Text>提交后订单进入已提交状态，后续修改受原生业务规则限制。</Typography.Text>}
    {proposal.action==='cancel'&&<Typography.Text>取消将使此已提交订单失效，不会撤销已发生的其他业务。</Typography.Text>}
    <Table size="small" pagination={false} rowKey="field" dataSource={proposalRows(proposal.changes)}
      columns={[{title: '字段', dataIndex: 'label'}, {title: proposal.action==='fill'?'填入前（表单）':'原值', dataIndex: 'before', render: (value,change)=>displayChange(Object.hasOwn(change,'form_before')?change.form_before:value,change)}, {title: '修改后', dataIndex: 'after', render: displayChange}]}/>
    {stockEntries.length>0&&<Space direction="vertical" size="small">
      <Typography.Text strong>将变动库存：</Typography.Text>
      {stockEntries.map(entry=><Typography.Text key={`${entry.item_code}:${entry.uom}:${entry.warehouse}`}>
        {entry.item_code} × {entry.quantity>0?'+':''}{entry.quantity} {entry.uom} @ {entry.warehouse}
      </Typography.Text>)}
    </Space>}
    {expired && proposal.status==='Pending' && !state && <Alert type="warning" message="确认已过期，请重新提出操作"/>}
    {(state?.status === 'Rejected' || proposal.status === 'Rejected') && <Alert type="info" message="提案已拒绝"/>}
    {state?.status === 'Succeeded' && <Alert type="success" message="执行成功，已读取业务结果"/>}
    {state?.status==='Applied'&&<Alert type="success" message="已填入当前草稿，尚未保存或提交"/>}
    {state?.status==='Authorized'&&<Typography.Text>本次填入已获授权；请核实当前草稿，不会自动重新填入。</Typography.Text>}
    {state?.status === 'Succeeded' && state.name && <Typography.Text>已保存记录：{state.doctype} / {state.name}</Typography.Text>}
    {state?.error && <Alert type="error" message={state.error}/>}
    {onVerify&&['Unknown','Failed'].includes(state?.status)&&<Button onClick={verify} loading={verifying}>核实业务结果</Button>}
    {verification&&<Typography.Text>{verification.note}</Typography.Text>}
    {verification?.observed&&<>
      <Typography.Text>当前已保存版本：{verification.observed.version}</Typography.Text>
      <Table size="small" pagination={false} rowKey="field" dataSource={Object.entries(verification.observed.values).map(([field,value])=>({field,value}))}
        columns={[{title:'字段',dataIndex:'field'},{title:'当前已保存值',dataIndex:'value',render:display}]}/>
    </>}
    {proposal.execution_ready===false&&<Typography.Text type="secondary">提案生成运行尚未成功结束，请核实运行记录。</Typography.Text>}
    <Space>
      <Button aria-label={proposal.action==='fill'?'确认填入':'确认执行'} type="primary" loading={state?.status === 'Running'} disabled={proposal.execution_ready===false || expired || proposal.status !== 'Pending' || Boolean(state)} onClick={confirm}>{proposal.action==='fill'?'确认填入':'确认执行'}</Button>
      {canReject && <Button aria-label="拒绝" onClick={reject}>拒绝</Button>}
    </Space>
  </Space>;
}
