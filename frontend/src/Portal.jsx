import React, {useEffect, useRef, useState} from 'react';
import {Alert, Button, Collapse, ConfigProvider, Empty, Input, Select, Space, Tag} from 'antd';
import zhCN from 'antd/locale/zh_CN.js';
import './style.css';

const statuses = {Queued:'等待执行', Running:'正在读取业务数据', Succeeded:'已完成', Failed:'执行失败'};
const tools = {erp_read_schema:'读取字段定义', erp_read_record:'读取记录', erp_search_records:'搜索记录'};
const active = task => task && ['Queued', 'Running'].includes(task.status);

export default function Portal({api, pollInterval = 1500}) {
  const [identity, setIdentity] = useState(null);
  const [enterprise, setEnterprise] = useState(null);
  const [question, setQuestion] = useState('');
  const [task, setTask] = useState(null);
  const [history, setHistory] = useState([]);
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const epoch = useRef(0);
  const dataGeneration = useRef(0);
  const submission = useRef(null);
  const requestId = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    api('context', {}, controller.signal).then(value => {
      if (!controller.signal.aborted) setIdentity(value);
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => { controller.abort(); submission.current?.abort(); };
  }, [api]);

  useEffect(() => {
    if (!enterprise) return;
    const version = epoch.current;
    const generation = dataGeneration.current;
    const controller = new AbortController();
    api('list_tasks', {enterprise}, controller.signal).then(tasks => {
      if (!controller.signal.aborted && generation === dataGeneration.current) {
        setHistory(items => [...items, ...tasks.filter(item => !items.some(current => current.id === item.id))].slice(0,20));
        if (version === epoch.current) setTask(tasks[0] || null);
      }
    }).catch(e => { if (!controller.signal.aborted && version === epoch.current) setError(e.message); });
    return () => controller.abort();
  }, [api, enterprise, refresh]);

  useEffect(() => {
    if (!active(task)) return;
    const controller = new AbortController();
    let timer;
    async function poll() {
      try {
        const updated = await api('get_task', {task_id: task.id}, controller.signal);
        if (controller.signal.aborted) return;
        setTask(updated);
        setHistory(items => items.map(item => item.id === updated.id ? updated : item));
        if (active(updated)) timer = setTimeout(poll, pollInterval);
      } catch (e) {
        if (!controller.signal.aborted) { dataGeneration.current += 1; setTask(null); setHistory([]); setError(e.message); }
      }
    }
    timer = setTimeout(poll, pollInterval);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, task?.id, active(task), pollInterval]);

  function switchEnterprise(value) {
    epoch.current += 1;
    submission.current?.abort();
    requestId.current = null;
    setEnterprise(value); setQuestion(''); setTask(null); setHistory([]); setError(''); setSending(false);
  }

  async function openTask(id) {
    const version = ++epoch.current;
    submission.current?.abort();
    const controller = new AbortController();
    submission.current = controller;
    setTask(null); setError(''); setSending(false);
    try {
      const current = await api('get_task', {task_id:id}, controller.signal);
      if (!controller.signal.aborted && version === epoch.current) setTask(current);
    } catch (e) {
      if (!controller.signal.aborted && version === epoch.current) { dataGeneration.current += 1; setHistory([]); setError(e.message); }
    }
  }

  async function send() {
    if (!question.trim() || sending || active(task)) return;
    const version = ++epoch.current;
    const controller = new AbortController();
    submission.current = controller;
    if (!requestId.current) requestId.current = crypto.randomUUID();
    setSending(true); setError('');
    try {
      const created = await api('submit_task', {enterprise, question: question.trim(), request_id: requestId.current}, controller.signal);
      if (controller.signal.aborted || version !== epoch.current) return;
      setTask(created); setHistory(items => [created, ...items.filter(item => item.id !== created.id)].slice(0,20));
      requestId.current = null; setQuestion('');
    } catch (e) {
      if (!controller.signal.aborted && version === epoch.current) { dataGeneration.current += 1; setTask(null); setHistory([]); setError(e.message); }
    } finally {
      if (!controller.signal.aborted && version === epoch.current) setSending(false);
    }
  }

  return <ConfigProvider locale={zhCN} prefixCls="dsh-ant" theme={{token:{colorPrimary:'#176b63',borderRadius:6}}}>
    <div className="dsherp-prototype">
      <header className="dsh-context">
        <div className="dsh-wordmark">dsherp <span>业务工作台</span></div>
        <Space>{identity && <span>{identity.user}</span>}<Button href="/app/user-profile">个人设置</Button></Space>
      </header>
      <main className="dsh-main">
        {error && <Alert showIcon type="error" message={error}/>}
        {!identity && !error && <p>正在读取登录身份…</p>}
        {identity?.enterprises.length === 0 && <Empty description="尚未加入可用企业"/>}
        {identity?.platform_admin && <Space className="dsh-spaced"><Button href="/app/ds-enterprise">企业管理</Button><Button href="/app/ds-membership">成员绑定</Button></Space>}
        {identity?.enterprises.length > 0 && <>
          <div className="dsh-heading"><div><h2>问业务，查实据</h2><p>按你的业务权限查询物料和客户；回答与读取记录会保存到当前企业。</p></div>
            <Select aria-label="当前企业" placeholder="选择企业" style={{width:250}} value={enterprise}
              options={identity.enterprises.map(item => ({value:item.id,label:item.label,disabled:item.status !== 'Ready'}))} onChange={switchEnterprise}/>
          </div>
          {!enterprise ? <Empty description="选择企业后，告诉 Agent 你想查什么。"/> : <div className="dsh-agent-grid">
            <section>
              <div className="dsh-section">
                <label htmlFor="business-question">你想了解什么？</label>
                <Input.TextArea id="business-question" aria-label="业务问题" placeholder="例如：有哪些物料？查一下某个客户的资料。" autoSize={{minRows:3,maxRows:8}}
                  maxLength={2000} value={question} disabled={sending || active(task)} onChange={event => {setQuestion(event.target.value); requestId.current = null;}}/>
                <div className="dsh-agent-actions"><span className="dsh-muted">只读查询 · 不修改业务数据</span><Button type="primary" loading={sending} disabled={!question.trim() || active(task)} onClick={send}>发送问题</Button></div>
              </div>
              {task && <section className="dsh-section" aria-label="执行结果">
                <Space wrap><Tag color={task.status === 'Failed' ? 'error' : task.status === 'Succeeded' ? 'success' : 'processing'}>{statuses[task.status]}</Tag><span className="dsh-muted">{task.id}</span></Space>
                <h3 className="dsh-spaced">{task.question}</h3>
                {active(task) && <p role="status">任务已保存，正在执行。离开页面不会重新发起；返回后可查看结果。</p>}
                {task.status === 'Failed' && <Alert type="error" showIcon message={task.error}/>}
                {task.status === 'Succeeded' && <div className="dsh-agent-answer">{task.answer}</div>}
                {task.events?.length > 0 && <><h3 className="dsh-spaced">执行记录</h3><Collapse size="small" items={task.events.map((event,index) => ({
                  key:String(index),label:<Space><span>{tools[event.tool]}</span><Tag>{event.status === 'Succeeded' ? '读取成功' : event.status === 'Running' ? '正在读取' : '读取失败'}</Tag></Space>,
                  children:<><div className="dsh-muted">请求参数</div><pre className="dsh-agent-json">{JSON.stringify(event.arguments,null,2)}</pre><div className="dsh-muted">业务返回</div><pre className="dsh-agent-json">{JSON.stringify(event.result ?? event.error,null,2)}</pre></>
                }))}/></>}
              </section>}
            </section>
            <aside className="dsh-section dsh-agent-history"><h3>我的查询记录</h3><Button size="small" className="dsh-history-refresh" disabled={sending} onClick={() => { epoch.current += 1; setTask(null); setHistory([]); setError(''); setRefresh(value => value + 1); }}>刷新记录</Button>
              {history.length === 0 ? <p className="dsh-muted">还没有查询。发送一个问题开始。</p> : history.map(item => <Button key={item.id} block type={task?.id === item.id ? 'primary' : 'default'} ghost={task?.id === item.id}
                className="dsh-history-item" onClick={() => openTask(item.id)}><span>{item.question}</span><small>{statuses[item.status]}</small></Button>)}
            </aside>
          </div>}
        </>}
      </main>
    </div>
  </ConfigProvider>;
}
