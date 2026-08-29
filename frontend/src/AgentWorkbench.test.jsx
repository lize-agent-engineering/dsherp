// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
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
  expect(screen.getByText("Item / I-1")).toBeTruthy();
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

it("摘要记录按需读取所属会话详情", async () => {
  const api = apiFactory();
  api.mockImplementation(async (method, params) => {
    if (method === "search_sessions")
      return {
        items: [{ id: "S-1", title: active.title, modified: "2026-08-29" }],
        has_more: false,
      };
    if (method === "get_session") return active;
    if (method === "list_pending")
      return {
        items: [
          {
            id: "P-1",
            session_id: "S-1",
            title: "销售订单 · submit",
            status: "Pending",
          },
        ],
      };
    return { items: [] };
  });
  render(<AgentWorkbench api={api} />);
  fireEvent.click(screen.getByRole("tab", { name: "待确认" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "查看销售订单 · submit详情" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("tab", { name: "对话" }).getAttribute("aria-selected"),
    ).toBe("true"),
  );
  expect(api).toHaveBeenCalledWith("get_session", { session_id: "S-1" });
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
