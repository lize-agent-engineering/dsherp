import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Popover } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { DatabaseOutlined, FileSearchOutlined, RightOutlined, SearchOutlined, SettingOutlined, SwapOutlined, ToolOutlined } from '@ant-design/icons';
import { statusText, statusTone } from './agent-format.js';
import { linkHosts } from './page-context.js';
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

// Everything rendered here reached the model as business data first. An image URL or a
// link it repeats is an outbound channel out of this page, so images never render, and a
// link stays clickable only when it is site-relative or its host is on the allowlist the
// server sent down; anything else degrades to visible text. The allowlist is empty unless
// the site configures one, which is exactly today's behaviour.
const SAME_SITE = /^\/(?!\/)/;

export const isSameSiteHref = (href, hosts = []) => {
  if (typeof href !== 'string') return false;
  if (SAME_SITE.test(href)) return true;
  if (!hosts.length) return false;
  let url;
  try {
    url = new URL(href);
  } catch {
    return false;                       // relative, protocol-relative or malformed
  }
  return (url.protocol === 'http:' || url.protocol === 'https:') && hosts.includes(url.hostname);
};

export const makeMarkdownComponents = (hosts = []) => ({
  table: (props) => (
    <div className="dsh-table-scroll">
      <table {...props} />
    </div>
  ),
  img: ({ alt }) => <span className="dsh-blocked-media">{alt ? `[图片：${alt}]` : '[图片已屏蔽]'}</span>,
  a: ({ href, children }) =>
    isSameSiteHref(href, hosts) ? (
      <a href={href}>{children}</a>
    ) : (
      <span className="dsh-plain-link">
        {children}
        {href ? `（${href}）` : ''}
      </span>
    ),
});

// Long business answers fold, but only the answer prose: alerts, proposals and
// execution results always stay in view.
export function Prose({ children, foldAt = 420 }) {
  const body = useRef(null);
  const [tall, setTall] = useState(false);
  const [open, setOpen] = useState(false);
  const components = useMemo(() => makeMarkdownComponents(linkHosts()), []);
  useLayoutEffect(() => {
    const node = body.current;
    if (!node || !foldAt) return;
    setTall(node.scrollHeight > foldAt + 80);
  }, [children, foldAt]);
  const clipped = tall && !open;
  return (
    <div className="dsh-fold">
      <div ref={body} className={`dsh-prose${clipped ? ' dsh-fold-clipped' : ''}`}>
        <ReactMarkdown
          skipHtml
          remarkPlugins={[remarkGfm]}
          components={components}
          // Left intact on purpose: only a site-relative href ever becomes an anchor,
          // so a javascript: or data: URL can reach the reader as text but never as a link.
          urlTransform={(url) => url}
        >
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
// Past LOAD_MORE_CAP rows we stop growing entirely: the sentinel is never observed
// and the button is gone, so scrolling to the bottom cannot keep pulling pages.
export const LOAD_MORE_CAP = 200;
export function LoadMore({ hasMore, busy, onLoad, label, count }) {
  const sentinel = useRef(null);
  const load = useRef(onLoad);
  load.current = onLoad;
  const capped = typeof count === 'number' && count >= LOAD_MORE_CAP;
  useEffect(() => {
    const node = sentinel.current;
    if (!node || !hasMore || capped || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) load.current();
      },
      { rootMargin: '200px' },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasMore, busy, capped]);
  if (!hasMore) return null;
  if (capped) {
    return (
      <div className="dsh-more">
        <span className="dsh-load-capped">已显示前 {LOAD_MORE_CAP} 条，请用搜索缩小范围</span>
      </div>
    );
  }
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

const toolIcons = {
  erp_read_record: FileSearchOutlined,
  erp_read_schema: FileSearchOutlined,
  erp_search_records: SearchOutlined,
  erp_read_configuration: SettingOutlined,
};

// Long lists are trimmed with their real total stated, never silently cut.
const listed = (values, unit, separator = '、') =>
  values.length > 12 ? `${values.slice(0, 12).join(separator)}…共 ${values.length} ${unit}` : values.join(separator);

function ToolStep({ event }) {
  const [open, setOpen] = useState(false);
  const Icon = toolIcons[event.tool] ?? DatabaseOutlined;
  // The server pads optional arguments with empty strings; a dangling
  // `query=` says nothing, so empty values stay out of the row.
  const args = Object.entries(event.arguments ?? {}).filter(([, value]) => value !== '' && value != null);
  // Baseline wording lives here, not in the transcript model: schema, record
  // and configuration baselines are different server facts.
  const baselines = [
    ...(event.schemaVersion ? [`${event.doctype ?? event.tool} 结构：${event.schemaVersion}`] : []),
    ...(event.configVersion ? [`配置：${event.configVersion}`] : []),
    ...(event.configRevision ? [`配置修订：${event.configRevision}`] : []),
    ...Object.entries(event.recordVersions ?? {}).map(([name, version]) => `${name}：${version}`),
  ];
  const details = [
    args.length ? ['参数', args.map(([key, value]) => `${key}=${value}`).join('，')] : null,
    event.fields?.length ? ['读取字段', listed(event.fields, '个')] : null,
    event.records?.length ? ['涉及记录', listed(event.records, '条')] : null,
    event.modules?.length ? ['涉及模块', listed(event.modules, '个')] : null,
    event.roles?.length ? ['涉及角色', listed(event.roles, '个')] : null,
    event.exists === false ? ['结果', '该对象尚无已保存配置'] : null,
    baselines.length ? ['基线版本', listed(baselines, '项', '；')] : null,
  ].filter(Boolean);
  return (
    <li className="dsh-chain-step">
      <button type="button" className="dsh-chain-head" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <span className="dsh-chain-step-no">{event.step}</span>
        <Icon aria-hidden="true" />
        <span className="dsh-chain-label">{event.label}</span>
        {event.detail && <span className="dsh-chain-detail">{event.detail}</span>}
        <RightOutlined aria-hidden="true" className={open ? 'dsh-chain-caret dsh-is-open' : 'dsh-chain-caret'} />
      </button>
      {open && (
        <dl className="dsh-chain-body">
          {details.map(([term, value]) => (
            <div key={term}>
              <dt>{term}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

// The ERP reads a run actually made, offered after the answer rather than in
// front of it. Every step comes from the server's authorized record: nothing
// is inferred from the model's own account, and the record carries order but
// no per-call timestamp, so none is shown.
export function ToolChain({ events }) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef(null);
  const chainRef = useRef(null);
  // The popover portals to the end of the document; without moving focus a
  // keyboard user would have to tab through the whole page to reach it. The
  // content mounts synchronously in tests but asynchronously (after the open
  // motion) in a real browser, so both the effect and afterOpenChange focus it.
  useEffect(() => {
    if (open) chainRef.current?.focus();
  }, [open]);
  if (!events?.length) return null;
  const close = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };
  const renderChain = () => (
    <div
      className="dsh-chain"
      role="dialog"
      aria-label="本轮 ERP 读取"
      tabIndex={-1}
      ref={chainRef}
      onKeyDown={(event) => {
        if (event.key === 'Escape') close();
      }}
    >
      <ol className="dsh-chain-list">
        {events.map((event) => (
          <ToolStep key={event.key} event={event} />
        ))}
      </ol>
      <p className="dsh-chain-note">服务端逐次复核权限并记录；顺序为实际调用次序，不含每次调用的时间。</p>
    </div>
  );
  return (
    <Popover
      content={renderChain}
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      afterOpenChange={(value) => {
        if (value) chainRef.current?.focus();
      }}
      placement="bottomLeft"
      rootClassName="dsh-chain-popover"
    >
      <button
        ref={triggerRef}
        type="button"
        className="dsh-chain-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`工具：查看本轮 ERP 读取（${events.length} 次）`}
      >
        <ToolOutlined aria-hidden="true" />
        工具
        <span className="dsh-chain-count">{events.length}</span>
      </button>
    </Popover>
  );
}
