import {expect,it} from 'vitest';
import {statusTone,statusText,recordKind,relativeTime,expiryText,isExpired} from './agent-format.js';

it('把后端真实状态翻译成中文说明，但不改写原状态', () => {
 expect(statusText('Authorized')).toBe('已授权，结果待核实');
 expect(statusText('Succeeded')).toBe('已成功');
 expect(statusText('Partial')).toBe('部分成功');
 expect(statusText('Unknown')).toBe('结果不明');
 expect(statusText('Failed')).toBe('执行失败');
 expect(statusText('Pending')).toBe('待确认');
 expect(statusText('Draft')).toBe('尚未确认');
 expect(statusText('Running')).toBe('执行中');
});

it('未知状态原样显示，不伪造成功也不吞掉', () => {
 expect(statusText('Reconciling')).toBe('Reconciling');
 expect(statusTone('Reconciling')).toBe('neutral');
});

it('状态色调区分成功、需要注意和失败', () => {
 expect(statusTone('Succeeded')).toBe('success');
 expect(statusTone('Partial')).toBe('warning');
 expect(statusTone('Unknown')).toBe('warning');
 expect(statusTone('Failed')).toBe('danger');
 expect(statusTone('Pending')).toBe('accent');
 expect(statusTone('Authorized')).toBe('accent');
});

it('区分业务与配置两类记录', () => {
 expect(recordKind('operation')).toBe('业务');
 expect(recordKind('configuration')).toBe('配置');
 expect(recordKind(undefined)).toBe('记录');
});

it('相对时间按分钟、小时和日期分级，按本地时区显示', () => {
 const now = Date.parse('2026-08-29 12:00:00');
 expect(relativeTime('2026-08-29 11:59:30', now)).toBe('刚刚');
 expect(relativeTime('2026-08-29 11:40:00', now)).toBe('20 分钟前');
 expect(relativeTime('2026-08-29 09:00:00', now)).toBe('3 小时前');
 expect(relativeTime('2026-08-20 09:00:00', now)).toBe('08-20 09:00');
 expect(relativeTime(undefined, now)).toBe('');
 expect(relativeTime('not-a-date', now)).toBe('');
});

it('过期时间如实显示，已过期不掩饰', () => {
 const now = Date.parse('2026-08-29 12:00:00');
 expect(isExpired('2026-08-29 11:00:00', now)).toBe(true);
 expect(isExpired('2026-08-29 12:30:00', now)).toBe(false);
 expect(isExpired(undefined, now)).toBe(true);
 expect(expiryText('2026-08-29 11:00:00', now)).toBe('已过期');
 expect(expiryText('2026-08-29 12:20:00', now)).toBe('20 分钟后过期');
 expect(expiryText(undefined, now)).toBe('无有效期，需重新提出');
});
