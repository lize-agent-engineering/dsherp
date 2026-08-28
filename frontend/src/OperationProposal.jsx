import React, {useRef, useState} from 'react';
import {Alert, Button, Space, Table, Typography} from 'antd';

const display = value => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const actions={create:'创建',update:'修改',submit:'提交',cancel:'取消'};
const displayChange=(value,change)=>change.field==='docstatus'?['草稿','已提交','已取消'][value]:display(value);

// A new immutable proposal gets a fresh interaction state; late promises cannot
// turn a replacement proposal into a successful execution.
export default function OperationProposal(props) {
  return <Proposal key={`${props.proposal.id}:${props.proposal.digest}`} {...props}/>;
}

function Proposal({proposal, onConfirm}) {
  const claimed = useRef(false);
  const [localState, setState] = useState(null);
  const state = localState || proposal.execution;
  const expired = !Number.isFinite(Date.parse(proposal.expires_at)) || Date.parse(proposal.expires_at) <= Date.now();
  async function confirm() {
    if (claimed.current || proposal.execution_ready===false || expired || Date.parse(proposal.expires_at) <= Date.now() || proposal.status !== 'Pending') return;
    claimed.current = true;
    setState({status: 'Running'});
    try {
      const result = await onConfirm({proposal_id: proposal.id, digest: proposal.digest, request_id: crypto.randomUUID()});
      setState(result.status === 'Succeeded' ? result : {...result, error: result.error || '执行结果尚未核实，请查看执行记录'});
    } catch (error) {
      setState({status: 'Unknown', error: error.message});
    }
  }
  return <Space direction="vertical" style={{width: '100%'}}>
    <Typography.Text strong>{proposal.doctype} / {proposal.name || '新记录'}</Typography.Text>
    <Typography.Text type="secondary">操作：{actions[proposal.action]} · 基线版本：{proposal.version || '新建'}</Typography.Text>
    {proposal.action==='submit'&&<Typography.Text>提交后订单进入已提交状态，后续修改受原生业务规则限制。</Typography.Text>}
    {proposal.action==='cancel'&&<Typography.Text>取消将使此已提交订单失效，不会撤销已发生的其他业务。</Typography.Text>}
    <Table size="small" pagination={false} rowKey="field" dataSource={proposal.changes}
      columns={[{title: '字段', dataIndex: 'label'}, {title: '原值', dataIndex: 'before', render: displayChange}, {title: '修改后', dataIndex: 'after', render: displayChange}]}/>
    {expired && <Alert type="warning" message="确认已过期，请重新提出操作"/>}
    {state?.status === 'Succeeded' && <Alert type="success" message="执行成功，已读取业务结果"/>}
    {state?.status === 'Succeeded' && state.name && <Typography.Text>已保存记录：{state.doctype} / {state.name}</Typography.Text>}
    {state?.error && <Alert type="error" message={state.error}/>}
    {proposal.execution_ready===false&&<Typography.Text type="secondary">提案生成运行尚未成功结束，请核实运行记录。</Typography.Text>}
    <Button aria-label="确认执行" type="primary" loading={state?.status === 'Running'} disabled={proposal.execution_ready===false || expired || proposal.status !== 'Pending' || Boolean(state)} onClick={confirm}>确认执行</Button>
  </Space>;
}
