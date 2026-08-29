// Presentation only: the server status string stays the record of truth and is
// always rendered alongside these labels, never replaced by them.
const statuses = {
  Pending: ['待确认', 'accent'],
  Authorized: ['已授权，结果待核实', 'accent'],
  Queued: ['排队中', 'accent'],
  Running: ['执行中', 'accent'],
  Cancelling: ['正在取消', 'warning'],
  Cancelled: ['已取消', 'neutral'],
  Draft: ['尚未确认', 'neutral'],
  Applied: ['已填入草稿', 'success'],
  Succeeded: ['已成功', 'success'],
  Partial: ['部分成功', 'warning'],
  Unknown: ['结果不明', 'warning'],
  Expired: ['已过期', 'neutral'],
  Failed: ['执行失败', 'danger'],
};
export const statusText = (status) => statuses[status]?.[0] ?? String(status ?? '');
export const statusTone = (status) => statuses[status]?.[1] ?? 'neutral';
export const recordKind = (kind) =>
  kind === 'operation' ? '业务' : kind === 'configuration' ? '配置' : '记录';

const MINUTE = 60000;
const pad = (value) => String(value).padStart(2, '0');
// Frappe sends naive datetimes in the site's timezone, which the browser parses
// as local time. Grouping must use the same local calendar day: comparing a UTC
// date against those strings puts a session from ten minutes ago under 更早.
const parse = (value) => Date.parse(typeof value === 'string' ? value.replace(' ', 'T') : value);
const startOfDay = (stamp) => {
  const date = new Date(stamp);
  date.setHours(0, 0, 0, 0);
  return date.getTime();
};
export function dayGroup(value, now = Date.now()) {
  const stamp = parse(value);
  if (!Number.isFinite(stamp)) return '更早';
  const days = Math.round((startOfDay(now) - startOfDay(stamp)) / 86400000);
  if (days <= 0) return '今天';
  if (days === 1) return '昨天';
  return '更早';
}

export function relativeTime(value, now = Date.now()) {
  const stamp = parse(value);
  if (!Number.isFinite(stamp)) return '';
  const minutes = Math.floor((now - stamp) / MINUTE);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} 小时前`;
  const date = new Date(stamp);
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function isExpired(value, now = Date.now()) {
  const stamp = Date.parse(value);
  return !Number.isFinite(stamp) || stamp <= now;
}
export function expiryText(value, now = Date.now()) {
  const stamp = Date.parse(value);
  if (!Number.isFinite(stamp)) return '无有效期，需重新提出';
  if (stamp <= now) return '已过期';
  const minutes = Math.round((stamp - now) / MINUTE);
  if (minutes < 1) return '不到 1 分钟后过期';
  if (minutes < 60) return `${minutes} 分钟后过期`;
  return `${Math.round(minutes / 60)} 小时后过期`;
}
