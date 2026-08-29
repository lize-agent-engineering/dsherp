// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import AgentWorkbench from "./AgentWorkbench.jsx";

beforeAll(() => {
  window.matchMedia = () => ({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  });
  global.ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
});
afterEach(cleanup);

const context = {
  schema_version: 1,
  route: ["Form", "Item", "I-1"],
  page_type: "form",
  doctype: "Item",
  name: "I-1",
  version: "v1",
  dirty: false,
};
const active = {
  id: "S-1",
  title: "今天的物料核对",
  archived: false,
  active_run: null,
  messages: [
    {
      id: "M-1",
      question: "查物料",
      answer: "已核对",
      status: "Succeeded",
      context,
    },
  ],
  proposals: [],
  configuration_bundles: [],
  configuration_confirmations: [],
};
const archived = {
  ...active,
  id: "S-2",
  title: "更早的客户核对",
  archived: true,
};

function apiFactory() {
  return vi.fn(async (method, params) => {
    if (method === "search_sessions")
      return {
        items: [
          params.archived
            ? {
                id: "S-2",
                title: archived.title,
                archived: true,
                modified: "2026-08-28",
              }
            : {
                id: "S-1",
                title: active.title,
                archived: false,
                modified: "2026-08-29",
              },
        ],
        page: 1,
        has_more: false,
      };
    if (method === "get_session")
      return params.session_id === "S-2" ? archived : active;
    if (
      method === "list_pending" ||
      method === "list_execution_records" ||
      method === "list_configuration_records"
    )
      return { items: [], page: 1, has_more: false };
    return active;
  });
}

it("提供四个一级视图并按 session 参数定位对话", async () => {
  const api = apiFactory();
  render(<AgentWorkbench api={api} initialSession="S-1" handoff={context} />);
  expect(await screen.findByText("已核对")).toBeTruthy();
  for (const name of ["对话", "待确认", "执行记录", "应用配置"])
    expect(screen.getByRole("tab", { name })).toBeTruthy();
  expect(
    api.mock.calls.some(
      (call) => call[0] === "get_session" && call[1].session_id === "S-1",
    ),
  ).toBe(true);
  expect(
    within(screen.getByRole("complementary", { name: "当前上下文" })).getByText(
      "Item / I-1",
    ),
  ).toBeTruthy();
});

it("搜索会话、切换归档并使归档会话保持只读", async () => {
  const api = apiFactory();
  render(<AgentWorkbench api={api} />);
  await screen.findByText("今天的物料核对");
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索会话" }), {
    target: { value: "客户" },
  });
  await waitFor(() =>
    expect(
      api.mock.calls.some(
        (call) => call[0] === "search_sessions" && call[1].query === "客户",
      ),
    ).toBe(true),
  );
  fireEvent.click(screen.getByRole("button", { name: "归档会话" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "更早的客户核对" }),
  );
  expect(await screen.findByText("归档会话为只读")).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: "业务问题" })).toBeNull();
});

it("非对话一级视图调用真实摘要接口而非从会话详情拼装", async () => {
  const api = apiFactory();
  render(<AgentWorkbench api={api} />);
  await screen.findByText("今天的物料核对");
  for (const [tab, method] of [
    ["待确认", "list_pending"],
    ["执行记录", "list_execution_records"],
    ["应用配置", "list_configuration_records"],
  ]) {
    fireEvent.click(screen.getByRole("tab", { name: tab }));
    await waitFor(() =>
      expect(api.mock.calls.some((call) => call[0] === method)).toBe(true),
    );
  }
});

it("摘要记录在原地展开所属会话中的那一条提案，而不是跳到长对话", async () => {
  const proposal = {
    id: "P-1",
    digest: "d1",
    action: "submit",
    doctype: "Sales Order",
    name: "SO-1",
    version: "v1",
    status: "Pending",
    expires_at: "2099-01-01T00:00:00Z",
    changes: [{ field: "docstatus", label: "单据状态", before: 0, after: 1 }],
  };
  const api = vi.fn(async (method, params) => {
    if (method === "search_sessions")
      return {
        items: [{ id: "S-1", title: active.title, modified: "2026-08-29" }],
        has_more: false,
      };
    if (method === "get_session") return { ...active, proposals: [proposal] };
    if (method === "list_pending")
      return {
        items: [
          {
            id: "P-1",
            session_id: "S-1",
            kind: "operation",
            title: "销售订单 · submit",
            status: "Pending",
            expires_at: "2099-01-01T00:00:00Z",
            modified: "2026-08-29",
          },
        ],
      };
    return { items: [] };
  });
  render(<AgentWorkbench api={api} />);
  fireEvent.click(screen.getByRole("tab", { name: "待确认" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "查看销售订单 · submit（P-1）详情" }),
  );
  const detail = await screen.findByRole("region", { name: "记录详情" });
  expect(
    await within(detail).findByRole("button", { name: "确认执行" }),
  ).toBeTruthy();
  expect(within(detail).getByText("P-1")).toBeTruthy();
  expect(api).toHaveBeenCalledWith("get_session", { session_id: "S-1" });
  expect(
    screen.getByRole("tab", { name: "待确认" }).getAttribute("aria-selected"),
  ).toBe("true");
});

it("记录在所属会话中找不到时如实说明，不推测结果", async () => {
  const api = vi.fn(async (method) => {
    if (method === "search_sessions")
      return {
        items: [{ id: "S-1", title: active.title, modified: "2026-08-29" }],
        has_more: false,
      };
    if (method === "get_session") return active;
    if (method === "list_execution_records")
      return {
        items: [
          {
            id: "E-9",
            session_id: "S-1",
            kind: "operation",
            title: "业务执行",
            status: "Unknown",
            modified: "2026-08-29",
          },
        ],
      };
    return { items: [] };
  });
  render(<AgentWorkbench api={api} />);
  fireEvent.click(screen.getByRole("tab", { name: "执行记录" }));
  fireEvent.click(await screen.findByRole("button", { name: "查看业务执行（E-9）详情" }));
  const detail = await screen.findByRole("region", { name: "记录详情" });
  expect(within(detail).getByText("结果不明")).toBeTruthy();
  expect(within(detail).getByText("Unknown")).toBeTruthy();
  expect(
    await within(detail).findByText(/这条记录在所属会话中已不可见/),
  ).toBeTruthy();
});

it("窄屏抽屉的关闭按钮有明确名称并能关闭面板", async () => {
  const api = apiFactory();
  render(<AgentWorkbench api={api} />);
  await screen.findByText("今天的物料核对");
  fireEvent.click(screen.getByRole("button", { name: "打开会话列表" }));
  const close = await screen.findByRole("button", { name: "关闭会话面板" });
  expect(close.closest(".dsh-wb-drawer")).toBeTruthy();
  fireEvent.click(close);
  await waitFor(() =>
    expect(document.querySelector(".dsh-wb-drawer .ant-drawer-content-wrapper-hidden")).toBeTruthy(),
  );
});

it("当前会话可以重命名和归档，活动运行仍由服务端 fastfail", async () => {
  const api = apiFactory();
  render(<AgentWorkbench api={api} />);
  await screen.findByText("今天的物料核对");
  fireEvent.click(screen.getByRole("button", { name: "重命名当前会话" }));
  fireEvent.change(screen.getByRole("textbox", { name: "会话标题" }), {
    target: { value: "新的物料核对" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存会话标题" }));
  await waitFor(() =>
    expect(
      api.mock.calls.some(
        (call) =>
          call[0] === "rename_session" && call[1].title === "新的物料核对",
      ),
    ).toBe(true),
  );
  fireEvent.click(screen.getByRole("button", { name: "归档当前会话" }));
  await waitFor(() =>
    expect(
      api.mock.calls.some(
        (call) => call[0] === "archive_session" && call[1].session_id === "S-1",
      ),
    ).toBe(true),
  );
});
it("新建会话清除当前选择，下一条消息不追加到旧会话", async () => {
  const api = apiFactory();
  render(<AgentWorkbench api={api} />);
  await screen.findByText("已核对");
  fireEvent.click(screen.getByRole("button", { name: "新建会话" }));
  fireEvent.change(screen.getByRole("textbox", { name: "业务问题" }), {
    target: { value: "新的只读查询" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() =>
    expect(
      api.mock.calls.some(
        (call) => call[0] === "send_message" && call[1].session_id === null,
      ),
    ).toBe(true),
  );
});

it("活动运行可以显式取消", async () => {
  const running = { ...active, active_run: "R-1" };
  const api = apiFactory();
  api.mockImplementation(async (method, params) => {
    if (method === "search_sessions")
      return {
        items: [{ id: "S-1", title: active.title, modified: "2026-08-29" }],
        has_more: false,
      };
    if (method === "get_session") return running;
    if (method === "cancel_run") return { ...running, active_run: null };
    return { items: [] };
  });
  render(<AgentWorkbench api={api} />);
  fireEvent.click(await screen.findByRole("button", { name: "停止运行" }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      "cancel_run",
      expect.objectContaining({ session_id: "S-1", run_id: "R-1" }),
    ),
  );
});

it("配置卡片复用现有预览与发布回调", async () => {
  const bundle = {
    id: "B-1",
    digest: "d1",
    changes: [],
    execution_ready: true,
    preview_available: true,
  };
  const configured = { ...active, configuration_bundles: [bundle] };
  const api = apiFactory();
  api.mockImplementation(async (method, params) => {
    if (method === "search_sessions")
      return {
        items: [{ id: "S-1", title: active.title, modified: "2026-08-29" }],
        has_more: false,
      };
    if (method === "get_session") return configured;
    if (method === "prepare_configuration_preview")
      return {
        id: "C-1",
        purpose: "preview",
        digest: "d1",
        target: "preview",
        baseline: "v1",
        changes: [],
        status: "Pending",
        expires_at: "2099-01-01T00:00:00Z",
      };
    return { items: [] };
  });
  render(<AgentWorkbench api={api} />);
  fireEvent.click(await screen.findByRole("button", { name: "查看预览确认" }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith("prepare_configuration_preview", {
      bundle_id: "B-1",
      digest: "d1",
    }),
  );
});

it("会话列表向下渐次加载而不是翻页，追加不丢失已加载会话", async () => {
  const pages = {
    1: {
      items: [
        { id: "S-1", title: "今天的物料核对", modified: "2026-08-29" },
        { id: "S-3", title: "今天的库存核对", modified: "2026-08-29" },
      ],
      page: 1,
      has_more: true,
    },
    2: {
      items: [{ id: "S-9", title: "更早的价格核对", modified: "2026-08-20" }],
      page: 2,
      has_more: false,
    },
  };
  const api = vi.fn(async (method, params) => {
    if (method === "search_sessions") return pages[params.page];
    if (method === "get_session") return active;
    return { items: [] };
  });
  render(<AgentWorkbench api={api} />);
  await screen.findByRole("button", { name: "今天的物料核对" });
  expect(screen.queryByRole("button", { name: "上一页会话" })).toBeNull();
  expect(screen.queryByRole("button", { name: "下一页会话" })).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "加载更多会话" }));
  expect(await screen.findByRole("button", { name: "更早的价格核对" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "今天的物料核对" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "今天的库存核对" })).toBeTruthy();
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "加载更多会话" })).toBeNull(),
  );
  expect(
    api.mock.calls.filter((call) => call[0] === "search_sessions").map((call) => call[1].page),
  ).toEqual([1, 2]);
});

it("重命名当前会话就地更新列表，不把已加载的会话重置回第一页", async () => {
  const pages = {
    1: { items: [{ id: "S-1", title: "今天的物料核对", modified: "2026-08-29" }], page: 1, has_more: true },
    2: { items: [{ id: "S-9", title: "更早的价格核对", modified: "2026-08-20" }], page: 2, has_more: false },
  };
  const api = vi.fn(async (method, params) => {
    if (method === "search_sessions") return pages[params.page];
    if (method === "get_session") return active;
    if (method === "rename_session")
      return { id: "S-1", title: params.title, archived: false, modified: "2026-08-29" };
    return { items: [] };
  });
  render(<AgentWorkbench api={api} />);
  await screen.findByRole("button", { name: "今天的物料核对" });
  fireEvent.click(screen.getByRole("button", { name: "加载更多会话" }));
  await screen.findByRole("button", { name: "更早的价格核对" });
  fireEvent.click(screen.getByRole("button", { name: "重命名当前会话" }));
  fireEvent.change(screen.getByRole("textbox", { name: "会话标题" }), {
    target: { value: "新的物料核对" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存会话标题" }));
  expect(await screen.findByRole("button", { name: "新的物料核对" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "更早的价格核对" })).toBeTruthy();
  expect(
    api.mock.calls.filter((call) => call[0] === "search_sessions").map((call) => call[1].page),
  ).toEqual([1, 2]);
});
