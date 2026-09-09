import {expect,it} from 'vitest';
import {statusTone,statusText,recordKind,relativeTime,expiryText,isExpired,parseTime} from './agent-format.js';

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

it('预算上限是提醒而不是失败：文案说清是「这一轮问得太大」而不是「你的活儿出错了」', () => {
 expect(statusText('BudgetExceeded')).toBe('已达本轮预算上限');
 expect(statusTone('BudgetExceeded')).toBe('warning');
 expect(statusTone('BudgetExceeded')).not.toBe('danger');
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

it('NeedsInput、Rejected、Expired 使用准确文案与色调', () => {
 expect(statusText('NeedsInput')).toBe('需要你补充信息');
 expect(statusTone('NeedsInput')).toBe('warning');
 expect(statusText('Rejected')).toBe('已拒绝');
 expect(statusTone('Rejected')).toBe('default');
 expect(statusText('Expired')).toBe('已过期');
 expect(statusTone('Expired')).toBe('default');
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

it('parseTime 统一解析 Frappe 的本地裸时间串，供所有时间比较复用', () => {
 // 空格分隔的形式在部分引擎（Safari）下 Date.parse 直接返回 NaN。
 expect(parseTime('2026-08-29 12:20:00')).toBe(new Date(2026, 7, 29, 12, 20, 0).getTime());
 expect(Number.isFinite(parseTime('not-a-date'))).toBe(false);
 expect(Number.isFinite(parseTime(undefined))).toBe(false);
});

it('纯日期串按本地零点解析，不落回 UTC 零点', () => {
 // Date.parse('YYYY-MM-DD') 是 UTC 零点，带时间的形式却是本地时间；
 // 混用会让同一列表里的时间相互矛盾。
 expect(parseTime('2026-08-29')).toBe(new Date(2026, 7, 29).getTime());
 const now = new Date(2026, 7, 29, 12, 0, 0).getTime();
 expect(relativeTime('2026-08-20', now)).toBe('08-20 00:00');
});

it('过期判断对 Safari 无法解析的空格时间串仍然正确', () => {
 // isExpired/expiryText 必须与 relativeTime 走同一 parseTime，不再裸调 Date.parse。
 const now = new Date(2026, 7, 29, 12, 0, 0).getTime();
 expect(isExpired('2026-08-29 12:30:00', now)).toBe(false);
 expect(expiryText('2026-08-29 12:30:00', now)).toBe('30 分钟后过期');
});
