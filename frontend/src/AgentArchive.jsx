import React, { useEffect, useRef, useState } from 'react';
import { Button, Input } from 'antd';
import { UndoOutlined } from '@ant-design/icons';
import { EmptyState, LoadMore, SkeletonLine } from './agent-ui.jsx';
import { relativeTime } from './agent-format.js';

// Archived conversations live here, out of the working rail. Restoring is the
// only state change offered: there is no delete endpoint, so none is implied.
export default function AgentArchive({ api, onOpen, onChange, refresh = 0 }) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [busy, setBusy] = useState(true);
  const [moreBusy, setMoreBusy] = useState(false);
  const [working, setWorking] = useState(null);
  const [error, setError] = useState('');
  const growing = useRef(false);

  async function load(nextPage = 1, nextQuery = query, append = false) {
    const result = await api('search_sessions', { query: nextQuery, page: nextPage, archived: 1 });
    setItems((old) =>
      append ? [...old, ...(result.items ?? []).filter((row) => !old.some((seen) => seen.id === row.id))] : result.items ?? [],
    );
    setHasMore(Boolean(result.has_more));
    setPage(nextPage);
  }
  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      setBusy(true);
      setError('');
      load(1, query)
        .catch((e) => live && setError(e.message))
        .finally(() => live && setBusy(false));
    }, 200);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [query, refresh]);

  async function grow() {
    if (growing.current || !hasMore) return;
    growing.current = true;
    setMoreBusy(true);
    try {
      await load(page + 1, query, true);
    } catch (e) {
      setError(e.message);
    } finally {
      growing.current = false;
      setMoreBusy(false);
    }
  }
  async function restore(item) {
    setWorking(item.id);
    setError('');
    try {
      await api('restore_session', { session_id: item.id });
      setItems((old) => old.filter((row) => row.id !== item.id));
      onChange?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setWorking(null);
    }
  }

  return (
    <div className="dsh-archive">
      <Input.Search
        aria-label="搜索已归档对话"
        allowClear
        placeholder="搜索已归档对话"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {error && (
        <p className="dsh-wb-notice" role="alert">
          {error}
        </p>
      )}
      <div className="dsh-archive-list dsh-scroll">
        {busy && !items.length && (
          <div className="dsh-wb-session-skeleton">
            {[0, 1, 2].map((row) => (
              <div key={row}>
                <SkeletonLine width="70%" />
                <SkeletonLine width="36%" height={9} />
              </div>
            ))}
          </div>
        )}
        {items.map((item) => (
          <div className="dsh-archive-item" key={item.id}>
            <button type="button" className="dsh-archive-open" aria-label={`打开已归档对话 ${item.title}`} onClick={() => onOpen?.(item)}>
              <span className="dsh-archive-title">{item.title}</span>
              <span className="dsh-meta">
                归档于 {relativeTime(item.archived_at) || relativeTime(item.modified) || '—'}
              </span>
            </button>
            <Button
              size="small"
              aria-label={`取消归档 ${item.title}`}
              icon={<UndoOutlined aria-hidden="true" />}
              loading={working === item.id}
              onClick={() => restore(item)}
            >
              取消归档
            </Button>
          </div>
        ))}
        {!busy && !items.length && (
          <EmptyState
            title={query ? '没有匹配的已归档对话' : '暂无已归档对话'}
            description="在对话标题行归档后，会话会移到这里；归档期间为只读。"
            compact
          />
        )}
        <LoadMore hasMore={hasMore} busy={moreBusy} onLoad={grow} label="加载更多已归档对话" />
      </div>
    </div>
  );
}
