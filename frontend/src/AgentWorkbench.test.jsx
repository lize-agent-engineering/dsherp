// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
afterEach(() => {
  cleanup();
  try {
    window.localStorage.clear();
  } catch {
    /* a jsdom without storage simply has nothing to clear */
  }
});

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
      sources: [
        { tool: "erp_read_record", arguments: { doctype: "Item", name: "I-1" }, fields: ["item_name"], records: ["I-1"] },
      ],
    },
  ],
  proposals: [],
  configuration_bundles: [],
  configuration_confirmations: [],
};
const archived = { ...active, id: "S-2", title: "更早的客户核对", archived: true };
const rail = () => screen.getByRole("navigation", { name: "会话" });

function apiFactory() {
  return vi.fn(async (method, params) => {
    if (method === "search_sessions")
      return {
        items: [
          params.archived
            ? { id: "S-2", title: archived.title, archived: true, modified: "2026-08-28" }
            : { id: "S-1", title: active.title, archived: false, modified: "2026-08-29" },
        ],
        page: 1,
        has_more: false,
      };
    if (method === "get_session") return params.session_id === "S-2" ? archived : active;
    return { items: [], page: 1, has_more: false };
  });
}

it("对话是唯一主表面：没有一级标签栏，会话在左栏，记录是次级入口", async () => {
  const api = apiFactory();
  render(<AgentWorkbench api={api} initialSession="S-1" handoff={context} />);
  expect(await screen.findByText("已核对")).toBeTruthy();
  expect(screen.queryAllByRole("tab")).toHaveLength(0);
  expect(await within(rail()).findByRole("button", { name: "今天的物料核对" })).toBeTruthy();
  expect(within(rail()).getByRole("button", { name: "新建会话" })).toBeTruthy();
  expect(within(rail()).queryByRole("button", { name: "执行记录" })).toBeNull();
  expect(screen.getByRole("log", { name: "对话记录" })).toBeTruthy();
});

it("回答之后可以查看本轮实际发生的 ERP 读取链路，并逐条展开", async () => {
  const api = apiFactory();
  render(<AgentWorkbench api={api} initialSession="S-1" />);
  const turn = (await screen.findByText("已核对")).closest("article");
  expect(within(turn).queryByLabelText("本轮 ERP 读取")).toBeNull();

  const trigger = within(turn).getByRole("button", { name: "查看本轮 ERP 读取（1 次）" });
  expect(within(trigger).getByText("1")).toBeTruthy();
  fireEvent.click(trigger);

  const chain = await screen.findByLabelText("本轮 ERP 读取");
  const step = within(chain).getByRole("button", { name: /读取 Item \/ I-1/ });
  expect(step.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(step);
  expect(step.getAttribute("aria-expanded")).toBe("true");
  expect(within(chain).getByText("item_name")).toBeTruthy();
  expect(within(chain).getByText(/不含每次调用的时间/)).toBeTruthy();
});

it("没有工具读取的一轮不显示工具入口", async () => {
  const api = apiFactory();
  api.mockImplementation(async (method) => {
    if (method === "search_sessions")
      return { items: [{ id: "S-1", title: active.title, modified: "2026-08-29" }], has_more: false };
    if (method === "get_session")
      return { ...active, messages: [{ ...active.messages[0], sources: [] }] };
    return { items: [] };
  });
  render(<AgentWorkbench api={api} initialSession="S-1" />);
  const turn = (await screen.findByText("已核对")).closest("article");
  expect(within(turn).queryByRole("button", { name: /查看本轮 ERP 读取/ })).toBeNull();
});

it("待确认提案就地出现在产生它的那条消息下，标题右侧提示需要确认", async () => {
  const proposal = {
    id: "P-1",
    model_run: "M-1",
    digest: "d1",
    action: "submit",
    doctype: "Sales Order",
    name: "SO-1",
    version: "v1",
    status: "Pending",
    expires_at: "2099-01-01T00:00:00Z",
    changes: [{ field: "docstatus", label: "单据状态", before: 0, after: 1 }],
  };
  const api = apiFactory();
  api.mockImplementation(async (method, params) => {
    if (method === "search_sessions")
      return { items: [{ id: "S-1", title: active.title, modified: "2026-08-29" }], has_more: false };
    if (method === "get_session") return { ...active, proposals: [proposal] };
    if (method === "list_pending")
      return { items: [{ id: "P-1", session_id: "S-1", kind: "operation", title: "销售订单 · submit", status: "Pending" }] };
    return { items: [] };
  });
  render(<AgentWorkbench api={api} initialSession="S-1" />);
  const turn = (await screen.findByText("已核对")).closest("article");
  expect(within(turn).getByRole("button", { name: "确认执行" })).toBeTruthy();
  expect(within(turn).getByText("待你确认的业务操作")).toBeTruthy();
  expect(screen.getByText("需要确认")).toBeTruthy();
  await waitFor(() =>
    expect(within(rail()).getByRole("button", { name: "今天的物料核对（需要确认）" })).toBeTruthy(),
  );
});

it("归属不明的提案单独列出，不假装属于某一轮", async () => {
  const api = apiFactory();
  api.mockImplementation(async (method) => {
    if (method === "search_sessions")
      return { items: [{ id: "S-1", title: active.title, modified: "2026-08-29" }], has_more: false };
    if (method === "get_session")
      return { ...active, proposals: [{ id: "P-9", model_run: "M-missing", digest: "d", action: "update", doctype: "Item", name: "I-1", version: "v1", status: "Pending", expires_at: "2099-01-01T00:00:00Z", changes: [] }] };
    return { items: [] };
  });
  render(<AgentWorkbench api={api} initialSession="S-1" />);
  const loose = await screen.findByRole("region", { name: "未归属到具体消息的条目" });
  expect(within(loose).getByText(/没有记录产生它们的运行/)).toBeTruthy();
  const turn = screen.getByText("已核对").closest("article");
  expect(within(turn).queryByRole("button", { name: "确认执行" })).toBeNull();
});

it("应用配置只在 Agent 设置里，由原生页面头部按钮打开", async () => {
  const api = apiFactory();
  const controls = {};
  render(<AgentWorkbench api={api} controls={controls} />);
  await screen.findByText("已核对");
  expect(screen.queryByRole("region", { name: "应用配置" })).toBeNull();
  expect(api.mock.calls.some((call) => call[0] === "list_configuration_records")).toBe(false);
  controls.openSettings();
  const settings = await screen.findByRole("region", { name: "应用配置" });
  expect(within(settings).getByRole("heading", { name: "应用配置" })).toBeTruthy();
  await waitFor(() =>
    expect(api.mock.calls.some((call) => call[0] === "list_configuration_records")).toBe(true),
  );
  fireEvent.click(screen.getByRole("button", { name: "关闭 Agent 设置" }));
});

it("左栏只列进行中的对话，搜索也不会翻出归档的", async () => {
  const api = apiFactory();
  render(<AgentWorkbench api={api} />);
  await within(rail()).findByRole("button", { name: "今天的物料核对" });
  expect(within(rail()).queryByRole("button", { name: "归档会话" })).toBeNull();
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索会话" }), { target: { value: "客户" } });
  await waitFor(() =>
    expect(api.mock.calls.some((call) => call[0] === "search_sessions" && call[1].query === "客户")).toBe(true),
  );
  expect(
    api.mock.calls.filter((call) => call[0] === "search_sessions").every((call) => call[1].archived === 0),
  ).toBe(true);
});

it("已归档对话在 Agent 设置里：可以取消归档，也可以直接打开为只读", async () => {
  const api = vi.fn(async (method, params) => {
    if (method === "search_sessions")
      return params.archived === 1
        ? { items: [{ id: "S-2", title: archived.title, archived: true, modified: "2026-08-28", archived_at: "2026-08-28" }], has_more: false }
        : { items: [{ id: "S-1", title: active.title, modified: "2026-08-29" }], has_more: false };
    if (method === "get_session") return params.session_id === "S-2" ? archived : active;
    if (method === "restore_session") return { id: "S-2", title: archived.title, archived: false, modified: "2026-08-29" };
    return { items: [] };
  });
  const controls = {};
  render(<AgentWorkbench api={api} controls={controls} />);
  await screen.findByText("已核对");
  controls.openSettings();
  fireEvent.click(await screen.findByRole("button", { name: "已归档对话" }));
  const panel = await screen.findByRole("region", { name: "已归档对话" });
  const row = await within(panel).findByRole("button", { name: "打开已归档对话 更早的客户核对" });

  fireEvent.click(row);
  expect(await screen.findByText("归档会话为只读")).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: "业务问题" })).toBeNull();

  controls.openSettings();
  const reopened = await screen.findByRole("region", { name: "已归档对话" });
  const restore = await within(reopened).findByRole("button", { name: "取消归档 更早的客户核对" });
  fireEvent.click(restore);
  await waitFor(() =>
    expect(api.mock.calls.some((call) => call[0] === "restore_session" && call[1].session_id === "S-2")).toBe(true),
  );
  await waitFor(() =>
    expect(
      within(screen.getByRole("region", { name: "已归档对话" })).queryByRole("button", {
        name: "打开已归档对话 更早的客户核对",
      }),
    ).toBeNull(),
  );
});

it("重命名与归档在对话标题行完成", async () => {
  const api = apiFactory();
  render(<AgentWorkbench api={api} />);
  await screen.findByText("已核对");
  fireEvent.click(screen.getByRole("button", { name: "重命名当前会话" }));
  fireEvent.change(screen.getByRole("textbox", { name: "会话标题" }), { target: { value: "新的物料核对" } });
  fireEvent.click(screen.getByRole("button", { name: "保存会话标题" }));
  await waitFor(() =>
    expect(api.mock.calls.some((call) => call[0] === "rename_session" && call[1].title === "新的物料核对")).toBe(true),
  );
  fireEvent.click(screen.getByRole("button", { name: "归档当前会话" }));
  await waitFor(() =>
    expect(api.mock.calls.some((call) => call[0] === "archive_session" && call[1].session_id === "S-1")).toBe(true),
  );
});

it("新建会话清除当前选择，下一条消息不追加到旧会话", async () => {
  const api = apiFactory();
  render(<AgentWorkbench api={api} />);
  await screen.findByText("已核对");
  fireEvent.click(within(rail()).getByRole("button", { name: "新建会话" }));
  fireEvent.change(screen.getByRole("textbox", { name: "业务问题" }), { target: { value: "新的只读查询" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() =>
    expect(api.mock.calls.some((call) => call[0] === "send_message" && call[1].session_id === null)).toBe(true),
  );
});

it("活动运行可以显式取消", async () => {
  const running = { ...active, active_run: "R-1" };
  const api = apiFactory();
  api.mockImplementation(async (method) => {
    if (method === "search_sessions")
      return { items: [{ id: "S-1", title: active.title, modified: "2026-08-29" }], has_more: false };
    if (method === "get_session") return running;
    if (method === "cancel_run") return { ...running, active_run: null };
    return { items: [] };
  });
  render(<AgentWorkbench api={api} />);
  fireEvent.click(await screen.findByRole("button", { name: "停止运行" }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith("cancel_run", expect.objectContaining({ session_id: "S-1", run_id: "R-1" })),
  );
});

it("会话列表向下渐次加载而不是翻页，追加不丢失已加载会话", async () => {
  const pages = {
    1: {
      items: [
        { id: "S-1", title: "今天的物料核对", modified: "2026-08-29" },
        { id: "S-3", title: "今天的库存核对", modified: "2026-08-29" },
      ],
      has_more: true,
    },
    2: { items: [{ id: "S-9", title: "更早的价格核对", modified: "2026-08-20" }], has_more: false },
  };
  const api = vi.fn(async (method, params) => {
    if (method === "search_sessions") return pages[params.page];
    if (method === "get_session") return active;
    return { items: [] };
  });
  render(<AgentWorkbench api={api} />);
  await within(rail()).findByRole("button", { name: "今天的物料核对" });
  fireEvent.click(within(rail()).getByRole("button", { name: "加载更多会话" }));
  expect(await within(rail()).findByRole("button", { name: "更早的价格核对" })).toBeTruthy();
  expect(within(rail()).getByRole("button", { name: "今天的库存核对" })).toBeTruthy();
  expect(
    api.mock.calls.filter((call) => call[0] === "search_sessions").map((call) => call[1].page),
  ).toEqual([1, 2]);
});

it("窄屏会话抽屉的关闭按钮有明确名称并能关闭面板", async () => {
  const api = apiFactory();
  render(<AgentWorkbench api={api} />);
  await screen.findByText("已核对");
  fireEvent.click(screen.getByRole("button", { name: "打开会话列表" }));
  const close = await screen.findByRole("button", { name: "关闭会话面板" });
  expect(close.closest(".dsh-wb-drawer")).toBeTruthy();
  fireEvent.click(close);
  await waitFor(() => expect(screen.queryByRole("button", { name: "关闭会话面板" })).toBeNull());
});

it("会话列表是扁平的一条流，按最近活动排序并显示相对时间", async () => {
  // 本地 2026-08-30 01:11，此刻 UTC 仍是 08-29：任何按 UTC 日期做的分桶都会自相矛盾。
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 7, 30, 1, 11, 0));
  try {
    const api = vi.fn(async (method) => {
      if (method === "search_sessions")
        return {
          items: [
            { id: "S-1", title: "刚刚的会话", modified: "2026-08-30 00:42:00" },
            { id: "S-2", title: "昨天下午的会话", modified: "2026-08-29 16:10:00" },
            { id: "S-3", title: "上周的会话", modified: "2026-08-20 09:00:00" },
          ],
          has_more: false,
        };
      if (method === "get_session") return active;
      return { items: [] };
    });
    render(<AgentWorkbench api={api} />);
    const recent = await within(rail()).findByRole("button", { name: "刚刚的会话" });
    expect(within(recent).getByText("29 分钟前")).toBeTruthy();
    expect(within(rail()).queryByText("今天")).toBeNull();
    expect(within(rail()).queryByText("更早")).toBeNull();
    const titles = [...rail().querySelectorAll(".dsh-rail-title")].map((node) => node.textContent);
    expect(titles).toEqual(["刚刚的会话", "昨天下午的会话", "上周的会话"]);
  } finally {
    vi.useRealTimers();
  }
});

it("超长字段清单截断时把真实总数写出来，不静默省略", async () => {
  const fields = Array.from({ length: 69 }, (_, index) => `field_${index + 1}`);
  const api = apiFactory();
  api.mockImplementation(async (method) => {
    if (method === "search_sessions")
      return { items: [{ id: "S-1", title: active.title, modified: "2026-08-29" }], has_more: false };
    if (method === "get_session")
      return {
        ...active,
        messages: [
          {
            ...active.messages[0],
            sources: [{ tool: "erp_read_schema", arguments: { doctype: "Item" }, fields, records: [] }],
          },
        ],
      };
    return { items: [] };
  });
  render(<AgentWorkbench api={api} initialSession="S-1" />);
  const turn = (await screen.findByText("已核对")).closest("article");
  fireEvent.click(within(turn).getByRole("button", { name: "查看本轮 ERP 读取（1 次）" }));
  const chain = await screen.findByLabelText("本轮 ERP 读取");
  fireEvent.click(within(chain).getByRole("button", { name: /读取 Item 结构/ }));
  expect(within(chain).getByText(/共 69 个/)).toBeTruthy();
  expect(within(chain).getByText(/field_12/)).toBeTruthy();
  expect(within(chain).queryByText(/field_69/)).toBeNull();
});
