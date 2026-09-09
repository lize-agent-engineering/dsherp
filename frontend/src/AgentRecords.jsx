import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Collapse } from 'antd';
import { ClockCircleOutlined, InboxOutlined, InfoCircleOutlined, LeftOutlined, SettingOutlined } from '@ant-design/icons';
import ConfigurationBundle from './ConfigurationBundle.jsx';
import { ConfirmCard, EmptyState, KindIcon, LoadMore, SkeletonLine, StatusChip } from './agent-ui.jsx';
import { expiryText, isExpired, recordKind, relativeTime } from './agent-format.js';
import { runEventRows } from './agent-transcript.js';

// A record row names a frozen configuration bundle inside one session. The
// detail pane reads that session on demand and shows the exact bundle, never
// a guess.
const locate = (record, detail) =>
  detail?.configuration_bundles?.find((row) => row.id === record?.id) ?? null;

// Loaded on first expand only. Putting the rows on the public session payload
// would reopen the size problem the dedicated GET exists to avoid.
export function RunEventPanel({ api, runId }) {
  const requested = useRef(false);
  const [state, setState] = useState({ loading: false, error: '', rows: null });

  function load() {
    if (requested.current) return;
    requested.current = true;
    setState({ loading: true, error: '', rows: null });
    api('list_run_events', { run_id: runId, page: 1 })
      .then((result) => {
        setState({ loading: false, error: '', rows: runEventRows(result.events) });
      })
      .catch((e) => {
        setState({ loading: false, error: e.message, rows: null });
      });
  }

  return (
    <Collapse
      ghost
      size="small"
      className="dsh-run-events"
      expandIcon={({ isActive }) => <span aria-hidden="true">{isActive ? '⌄' : '›'}</span>}
      onChange={(keys) => {
        const open = (Array.isArray(keys) ? keys : [keys]).filter(Boolean).includes('events');
        if (open) load();
      }}
      items={[
        {
          key: 'events',
          label: '事件流',
          children: (
            <>
              {state.error && (
                <p className="dsh-run-events-error" role="alert">
                  {state.error}
                </p>
              )}
              {state.loading && <p className="dsh-run-event-count">正在读取事件…</p>}
              {state.rows && <p className="dsh-run-event-count">共 {state.rows.length} 条</p>}
              {state.rows?.map((row) => (
                <div key={row.seq} className={row.tone === 'danger' ? 'dsh-run-event dsh-is-danger' : 'dsh-run-event'}>
                  <div className="dsh-run-event-head">
                    <strong>{row.label}</strong>
                    <time dateTime={row.time}>{row.time}</time>
                  </div>
                  <div className="dsh-run-event-detail">{row.detail}</div>
                </div>
              ))}
            </>
          ),
        },
      ]}
    />
  );
}

export default function AgentRecords({ api, method, empty, refresh = 0 }) {
  const [records, setRecords] = useState([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [busy, setBusy] = useState(true);
  const [moreBusy, setMoreBusy] = useState(false);
  const [active, setActive] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [error, setError] = useState('');
  const growing = useRef(false);
  const inspection = useRef(0);

  useEffect(() => {
    let live = true;
    setBusy(true);
    setActive(null);
    setDetail(null);
    api(method, { page: 1 })
      .then((result) => {
        if (!live) return;
        setRecords(result.items ?? []);
        setHasMore(Boolean(result.has_more));
        setPage(1);
      })
      .catch((e) => live && setError(e.message))
      .finally(() => live && setBusy(false));
    return () => {
      live = false;
    };
  }, [api, method, refresh]);

  async function grow() {
    if (growing.current || !hasMore) return;
    growing.current = true;
    setMoreBusy(true);
    try {
      const result = await api(method, { page: page + 1 });
      setRecords((old) => [...old, ...(result.items ?? []).filter((row) => !old.some((seen) => seen.id === row.id))]);
      setHasMore(Boolean(result.has_more));
      setPage(page + 1);
    } catch (e) {
      setError(e.message);
    } finally {
      growing.current = false;
      setMoreBusy(false);
    }
  }
  async function inspect(record) {
    const ticket = ++inspection.current;
    setActive(record);
    setDetail(null);
    setDetailBusy(true);
    setError('');
    try {
      const owner = await api('get_session', { session_id: record.session_id });
      if (ticket === inspection.current) setDetail(owner);
    } catch (e) {
      if (ticket === inspection.current) setError(e.message);
    } finally {
      if (ticket === inspection.current) setDetailBusy(false);
    }
  }
  const located = useMemo(() => locate(active, detail), [active, detail]);

  const list = (
    <div className="dsh-wb-record-list dsh-scroll" aria-label="记录列表">
      {busy && !records.length && (
        <div className="dsh-wb-session-skeleton">
          {[0, 1, 2].map((row) => (
            <div key={row}>
              <SkeletonLine width="66%" />
              <SkeletonLine width="38%" height={9} />
            </div>
          ))}
        </div>
      )}
      {records.map((item) => (
        <button
          type="button"
          key={item.id}
          className={active?.id === item.id ? 'dsh-wb-record dsh-is-current' : 'dsh-wb-record'}
          aria-label={`查看${item.title || item.action || item.id}（${item.id}）详情`}
          aria-current={active?.id === item.id || undefined}
          onClick={() => inspect(item)}
        >
          <span className="dsh-wb-record-icon">
            <KindIcon kind={item.kind} />
          </span>
          <span className="dsh-wb-record-main">
            <span className="dsh-wb-record-title">{item.title || item.action || item.id}</span>
            <span className="dsh-wb-record-meta">
              <span>{recordKind(item.kind)}</span>
              {item.summary && <span>{item.summary}</span>}
              {relativeTime(item.modified) && <span>{relativeTime(item.modified)}</span>}
              <span className="dsh-raw">{item.id}</span>
            </span>
          </span>
          <span className="dsh-wb-record-status">
            <StatusChip status={item.status} />
            {item.expires_at && (
              <span className={isExpired(item.expires_at) ? 'dsh-wb-record-expiry dsh-is-expired' : 'dsh-wb-record-expiry'}>
                <ClockCircleOutlined aria-hidden="true" /> {expiryText(item.expires_at)}
              </span>
            )}
          </span>
        </button>
      ))}
      {!busy && !records.length && <EmptyState icon={<InboxOutlined />} title={empty.title} description={empty.hint} compact />}
      <LoadMore hasMore={hasMore} busy={moreBusy} onLoad={grow} label="加载更多记录" count={records.length} />
    </div>
  );

  const pane = (
    <section className="dsh-wb-detail dsh-scroll" aria-label="记录详情">
      {!active && !busy && !records.length && (
        <EmptyState title="没有可查看的记录" description="有记录时，选中任意一条即可在这里看到它的冻结内容。" compact />
      )}
      {!active && (busy || records.length > 0) && (
        <EmptyState
          icon={<InfoCircleOutlined />}
          title="选择记录查看详情"
          description="详情直接来自记录所属会话，包含冻结的对象、差异与执行结果。"
        />
      )}
      {active && (
        <>
          <header className="dsh-wb-detail-head">
            <Button
              className="dsh-wb-detail-back"
              type="text"
              size="small"
              aria-label="返回记录列表"
              icon={<LeftOutlined aria-hidden="true" />}
              onClick={() => {
                setActive(null);
                setDetail(null);
              }}
            />
            <div>
              <strong>{active.title || active.id}</strong>
              <div className="dsh-wb-detail-meta">
                <span>{recordKind(active.kind)}</span>
                <span className="dsh-raw">{active.id}</span>
                {/* The in-conversation shortcut is gone with the records view;
                    the detail must say which session it belongs to itself. */}
                <span>所属会话：{detail?.title ?? active.session_id}</span>
                {relativeTime(active.modified) && <span>{relativeTime(active.modified)}</span>}
              </div>
            </div>
            <StatusChip status={active.status} />
          </header>
          {active.expires_at && (
            <p className={isExpired(active.expires_at) ? 'dsh-wb-notice' : 'dsh-wb-detail-expiry'}>
              {expiryText(active.expires_at)}
              {isExpired(active.expires_at) && '，需要重新提出后再确认'}
            </p>
          )}
          {error && (
            <p className="dsh-wb-notice" role="alert">
              {error}
            </p>
          )}
          {detailBusy && (
            <div className="dsh-wb-chat-skeleton">
              <SkeletonLine width="60%" height={20} />
              <SkeletonLine width="100%" height={110} />
            </div>
          )}
          {!detailBusy && located && (
            <ConfirmCard icon={<SettingOutlined aria-hidden="true" />} title="应用配置包">
              <ConfigurationBundle
                bundle={located}
                onPrepare={(binding) => api('prepare_configuration_preview', binding)}
                onTransfer={(binding) => api('prepare_configuration_transfer', binding)}
                onPublish={(binding) => api('prepare_configuration_publish', binding)}
                onConfirm={(binding) =>
                  api(located.preview_available ? 'confirm_configuration' : 'confirm_configuration_publish', binding)
                }
              />
            </ConfirmCard>
          )}
          {!detailBusy && detail && !located && (
            <p className="dsh-wb-notice">
              这条记录在所属会话中已不可见，可能权限、成员绑定或版本已经变化。请在对话中核实，系统不会据列表推测结果。
            </p>
          )}
        </>
      )}
    </section>
  );

  return (
    <div className="dsh-wb-records">
      {list}
      {pane}
    </div>
  );
}
