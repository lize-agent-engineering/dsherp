import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { SettingOutlined, SwapOutlined } from '@ant-design/icons';
import { statusText, statusTone } from './agent-format.js';
import './agent-theme.css';

// The product mark, drawn rather than borrowed from a glyph so it keeps its
// weight next to the icon set.
export function Spark({ size = 16, className }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path
        d="M8 .9l1.35 4.06a3.6 3.6 0 0 0 2.28 2.28L15.1 8.6l-3.47 1.35a3.6 3.6 0 0 0-2.28 2.28L8 15.1l-1.35-3.87a3.6 3.6 0 0 0-2.28-2.28L.9 8.6l3.47-1.36a3.6 3.6 0 0 0 2.28-2.28z"
        fill="currentColor"
      />
    </svg>
  );
}

export const KindIcon = ({ kind }) =>
  kind === 'configuration' ? <SettingOutlined aria-hidden="true" /> : <SwapOutlined aria-hidden="true" />;

// Shows the readable label and keeps the server's own status word beside it, so
// Partial, Unknown and Expired are never softened into something friendlier.
export function StatusChip({ status, tone, raw = true }) {
  if (!status) return null;
  return (
    <span className={`dsh-chip dsh-chip-${tone ?? statusTone(status)}`} title={String(status)}>
      {statusText(status)}
      {raw && statusText(status) !== String(status) && <em className="dsh-chip-raw">{status}</em>}
    </span>
  );
}

const markdownComponents = {
  table: (props) => (
    <div className="dsh-table-scroll">
      <table {...props} />
    </div>
  ),
};

// Long business answers fold, but only the answer prose: alerts, proposals and
// execution results always stay in view.
export function Prose({ children, foldAt = 420 }) {
  const body = useRef(null);
  const [tall, setTall] = useState(false);
  const [open, setOpen] = useState(false);
  useLayoutEffect(() => {
    const node = body.current;
    if (!node || !foldAt) return;
    setTall(node.scrollHeight > foldAt + 80);
  }, [children, foldAt]);
  const clipped = tall && !open;
  return (
    <div className="dsh-fold">
      <div ref={body} className={`dsh-prose${clipped ? ' dsh-fold-clipped' : ''}`}>
        <ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} components={markdownComponents}>
          {children || ''}
        </ReactMarkdown>
      </div>
      {tall && (
        <button type="button" className="dsh-fold-toggle" onClick={() => setOpen((value) => !value)}>
          {open ? '收起长回答' : '展开完整回答'}
        </button>
      )}
    </div>
  );
}

export function ConfirmCard({ icon, title, meta, children }) {
  return (
    <section className="dsh-confirm">
      <header className="dsh-confirm-head">
        <span className="dsh-confirm-icon">{icon}</span>
        <strong>{title}</strong>
        {meta}
      </header>
      {children}
    </section>
  );
}

export const SkeletonLine = ({ width = '100%', height = 12 }) => (
  <span className="dsh-skeleton" style={{ display: 'block', width, height }} />
);

export function EmptyState({ icon, title, description, children, compact }) {
  return (
    <div className={`dsh-empty${compact ? ' dsh-empty-compact' : ''}`}>
      {icon && <span className="dsh-empty-icon">{icon}</span>}
      <strong>{title}</strong>
      {description && <p>{description}</p>}
      {children}
    </div>
  );
}

// Lists grow as you scroll. The button stays real so keyboard users and
// browsers without IntersectionObserver can still reach the next page.
export function LoadMore({ hasMore, busy, onLoad, label }) {
  const sentinel = useRef(null);
  const load = useRef(onLoad);
  load.current = onLoad;
  useEffect(() => {
    const node = sentinel.current;
    if (!node || !hasMore || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) load.current();
      },
      { rootMargin: '200px' },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasMore, busy]);
  if (!hasMore) return null;
  return (
    <div className="dsh-more" ref={sentinel}>
      {busy ? (
        <>
          <SkeletonLine width="72%" />
          <SkeletonLine width="44%" height={9} />
        </>
      ) : (
        <button type="button" className="dsh-more-btn" onClick={() => load.current()}>
          {label}
        </button>
      )}
    </div>
  );
}
