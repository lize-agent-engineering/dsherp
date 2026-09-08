// @vitest-environment jsdom
import React from 'react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';
import {LoadMore} from './agent-ui.jsx';

let observed = [];

beforeEach(() => {
  observed = [];
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback) { this.callback = callback; }
    observe(node) { observed.push(node); }
    disconnect() {}
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('到达封顶后不再加载，并说明怎么缩小范围', () => {
  const onLoad = vi.fn();
  render(<LoadMore hasMore busy={false} onLoad={onLoad} label="加载更多" count={200} />);
  expect(screen.queryByRole('button')).toBeNull();
  expect(screen.getByText(/已显示前 200 条/)).toBeTruthy();
  // 哨兵没有被 observe：滚到底也不会再拉一页。
  expect(observed).toEqual([]);
  expect(onLoad).not.toHaveBeenCalled();
});

it('封顶以下照常加载', () => {
  const onLoad = vi.fn();
  render(<LoadMore hasMore busy={false} onLoad={onLoad} label="加载更多" count={199} />);
  expect(screen.getByRole('button').textContent).toBe('加载更多');
  expect(observed).toHaveLength(1);
});

it('没有传 count 时行为与今天一致', () => {
  render(<LoadMore hasMore busy={false} onLoad={vi.fn()} label="加载更多" />);
  expect(screen.getByRole('button').textContent).toBe('加载更多');
  expect(observed).toHaveLength(1);
});

it('没有更多时什么也不渲染，即使已经到达封顶', () => {
  const {container} = render(<LoadMore hasMore={false} busy={false} onLoad={vi.fn()} label="加载更多" count={500} />);
  expect(container.textContent).toBe('');
});
