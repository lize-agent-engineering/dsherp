// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import AgentRecords from './AgentRecords.jsx';

beforeAll(() => {
  window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
  global.ResizeObserver = class { observe() {} disconnect() {} };
});
afterEach(cleanup);

const empty = { title: '暂无配置记录', hint: '配置包属于发起配置的业务用户。' };
const row = {
  id: 'B-1',
  session_id: 'S-1',
  kind: 'configuration',
  title: '应用配置包',
  summary: '基线 v3',
  status: 'Draft',
  modified: '2026-08-29 10:00:00',
};

function apiFor(session) {
  return vi.fn(async (method) => {
    if (method === 'list_configuration_records') return { items: [row], has_more: false };
    if (method === 'get_session') return session;
    return { items: [] };
  });
}

it('记录详情写明所属会话，即使记录在会话中已不可见也能找回去', async () => {
  // 会话内打开的入口已随执行记录视图一起移除；详情必须自己交代归属，
  // 否则"请在对话中核实"没法执行。
  const api = apiFor({ id: 'S-1', title: '今天的物料核对', configuration_bundles: [] });
  render(<AgentRecords api={api} method="list_configuration_records" empty={empty} />);
  fireEvent.click(await screen.findByRole('button', { name: /应用配置包/ }));
  const detail = await screen.findByRole('region', { name: '记录详情' });
  expect(await within(detail).findByText(/所属会话：今天的物料核对/)).toBeTruthy();
  expect(within(detail).getByText(/这条记录在所属会话中已不可见/)).toBeTruthy();
});

it('所属会话读不到时如实退回会话标识，不显示空白', async () => {
  const api = vi.fn(async (method) => {
    if (method === 'list_configuration_records') return { items: [row], has_more: false };
    if (method === 'get_session') throw new Error('无权限');
    return { items: [] };
  });
  render(<AgentRecords api={api} method="list_configuration_records" empty={empty} />);
  fireEvent.click(await screen.findByRole('button', { name: /应用配置包/ }));
  const detail = await screen.findByRole('region', { name: '记录详情' });
  expect(await within(detail).findByText(/所属会话：S-1/)).toBeTruthy();
});
