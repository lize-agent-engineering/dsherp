// @vitest-environment jsdom
import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import AgentWorkbench from "./AgentWorkbench.jsx";

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
  try {
    window.localStorage.clear();
  } catch {
    /* a jsdom without storage simply has nothing to clear */
  }
});

it("测试环境使用 jsdom 的页面 localStorage，不落到 Node 实验存储", () => {
  expect(window.localStorage).toBeInstanceOf(window.Storage);
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

  const trigger = within(turn).getByRole("button", { name: "工具：查看本轮 ERP 读取（1 次）" });
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
      return { items: [{ id: "S-1", title: active.title, modified: "2026-08-29", pending_count: 1 }], has_more: false };
    if (method === "get_session") return { ...active, proposals: [proposal] };
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
  fireEvent.click(within(turn).getByRole("button", { name: /查看本轮 ERP 读取（1 次）/ }));
  const chain = await screen.findByLabelText("本轮 ERP 读取");
  fireEvent.click(within(chain).getByRole("button", { name: /读取 Item 结构/ }));
  expect(within(chain).getByText(/共 69 个/)).toBeTruthy();
  expect(within(chain).getByText(/field_12/)).toBeTruthy();
  expect(within(chain).queryByText(/field_69/)).toBeNull();
});

const other = {
  ...active,
  id: "S-9",
  title: "客户核对",
  messages: [{ ...active.messages[0], id: "M-9", answer: "客户已核对", sources: [] }],
};

function apiWithSources(sources) {
  const api = apiFactory();
  api.mockImplementation(async (method) => {
    if (method === "search_sessions")
      return { items: [{ id: "S-1", title: active.title, modified: "2026-08-29" }], has_more: false };
    if (method === "get_session") return { ...active, messages: [{ ...active.messages[0], sources }] };
    return { items: [] };
  });
  return api;
}

it("配置读取的链路步骤展开后显示模块、角色与配置基线", async () => {
  const api = apiWithSources([
    {
      tool: "erp_read_configuration",
      arguments: { doctype: "Item" },
      modules: ["stock", "manufacturing"],
      roles: ["Item Manager"],
      exists: true,
      version: "2026-08-29 03:00:00",
      configuration_revision: "r-9",
    },
  ]);
  render(<AgentWorkbench api={api} initialSession="S-1" />);
  const turn = (await screen.findByText("已核对")).closest("article");
  fireEvent.click(within(turn).getByRole("button", { name: /查看本轮 ERP 读取（1 次）/ }));
  const chain = await screen.findByLabelText("本轮 ERP 读取");
  const step = within(chain).getByRole("button", { name: /读取配置 Item/ });
  expect(within(step).getByText("2 个模块 · 1 个角色")).toBeTruthy();
  fireEvent.click(step);
  expect(within(chain).getByText("stock、manufacturing")).toBeTruthy();
  expect(within(chain).getByText("Item Manager")).toBeTruthy();
  expect(within(chain).getByText(/配置：2026-08-29 03:00:00/)).toBeTruthy();
  expect(within(chain).getByText(/配置修订：r-9/)).toBeTruthy();
});

it("省略的空 query 不会在参数行里显示成悬空的 query=", async () => {
  const api = apiWithSources([
    { tool: "erp_search_records", arguments: { query: "", doctype: "Item" }, fields: [], records: ["I-1"] },
  ]);
  render(<AgentWorkbench api={api} initialSession="S-1" />);
  const turn = (await screen.findByText("已核对")).closest("article");
  fireEvent.click(within(turn).getByRole("button", { name: /查看本轮 ERP 读取（1 次）/ }));
  const chain = await screen.findByLabelText("本轮 ERP 读取");
  fireEvent.click(within(chain).getByRole("button", { name: /搜索 Item/ }));
  expect(within(chain).getByText("doctype=Item")).toBeTruthy();
  expect(within(chain).queryByText(/query=/)).toBeNull();
});

it("运行中的回合就地显示已发生的工具读取，不必等回答", async () => {
  // 服务端对 Running 的运行同样返回 sources；已授权的读取应当实时可见，
  // 而不是等回答出现后一次性弹出。
  const api = apiFactory();
  api.mockImplementation(async (method) => {
    if (method === "search_sessions")
      return { items: [{ id: "S-1", title: active.title, modified: "2026-08-29" }], has_more: false };
    if (method === "get_session")
      return { ...active, active_run: "R-1", messages: [{ ...active.messages[0], answer: "", status: "Running" }] };
    return { items: [] };
  });
  render(<AgentWorkbench api={api} initialSession="S-1" />);
  await screen.findByText("正在处理");
  const turn = screen.getByText("查物料").closest("article");
  expect(within(turn).getByRole("button", { name: /本轮 ERP 读取（1 次）/ })).toBeTruthy();
});

it("工具链入口可访问名称包含可见文本，弹层可聚焦并能用 Esc 关闭", async () => {
  const api = apiFactory();
  render(<AgentWorkbench api={api} initialSession="S-1" />);
  const turn = (await screen.findByText("已核对")).closest("article");
  const trigger = within(turn).getByRole("button", { name: "工具：查看本轮 ERP 读取（1 次）" });
  expect(trigger.getAttribute("aria-haspopup")).toBe("dialog");
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(trigger);
  const chain = await screen.findByRole("dialog", { name: "本轮 ERP 读取" });
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  await waitFor(() => expect(document.activeElement).toBe(chain));
  fireEvent.keyDown(chain, { key: "Escape" });
  await waitFor(() => expect(trigger.getAttribute("aria-expanded")).toBe("false"));
  expect(document.activeElement).toBe(trigger);
});

it("基线版本清单与字段清单一样截断并写明总数", async () => {
  const records = Array.from({ length: 20 }, (_, index) => `I-${index + 1}`);
  const api = apiWithSources([
    {
      tool: "erp_search_records",
      arguments: { query: "合成", doctype: "Item" },
      fields: [],
      records,
      record_versions: Object.fromEntries(records.map((name) => [name, "2026-08-29 03:26:23"])),
    },
  ]);
  render(<AgentWorkbench api={api} initialSession="S-1" />);
  const turn = (await screen.findByText("已核对")).closest("article");
  fireEvent.click(within(turn).getByRole("button", { name: /查看本轮 ERP 读取（1 次）/ }));
  const chain = await screen.findByLabelText("本轮 ERP 读取");
  fireEvent.click(within(chain).getByRole("button", { name: /搜索 Item：合成/ }));
  expect(within(chain).getByText(/共 20 项/)).toBeTruthy();
  expect(within(chain).queryByText(/I-20：/)).toBeNull();
});

it("切换会话后，上一会话在途的轮询响应不会覆盖新会话", async () => {
  let gate = false;
  let releaseStale;
  const stale = new Promise((resolve) => { releaseStale = resolve; });
  const api = vi.fn(async (method, params) => {
    if (method === "search_sessions")
      return {
        items: [
          { id: "S-1", title: active.title, modified: "2026-08-29 10:00:00" },
          { id: "S-9", title: other.title, modified: "2026-08-28 10:00:00" },
        ],
        has_more: false,
      };
    if (method === "get_session") {
      if (params.session_id === "S-9") return other;
      if (gate) return stale;
      return active;
    }
    return { items: [], has_more: false };
  });
  render(<AgentWorkbench api={api} initialSession="S-1" pollInterval={40} />);
  await screen.findByText("已核对");
  const target = await within(rail()).findByRole("button", { name: "客户核对" });
  gate = true;
  const staleFetches = () =>
    api.mock.calls.filter((call) => call[0] === "get_session" && call[1].session_id === "S-1").length;
  const before = staleFetches();
  await waitFor(() => expect(staleFetches()).toBeGreaterThan(before));
  fireEvent.click(target);
  await screen.findByText("客户已核对");
  releaseStale({ ...active, messages: [{ ...active.messages[0], answer: "陈旧轮询数据" }] });
  await new Promise((resolve) => setTimeout(resolve, 80));
  expect(screen.queryByText("陈旧轮询数据")).toBeNull();
  expect(screen.getByText("客户已核对")).toBeTruthy();
});

it("连续选择会话时，较慢的旧选择不会覆盖最后一次选择", async () => {
  let releaseSlow;
  let delayOther = false;
  const slowOther = new Promise((resolve) => { releaseSlow = resolve; });
  const api = vi.fn(async (method, params) => {
    if (method === "search_sessions")
      return {
        items: [
          { id: "S-1", title: active.title, modified: "2026-08-29 10:00:00" },
          { id: "S-9", title: other.title, modified: "2026-08-28 10:00:00" },
        ],
        has_more: false,
      };
    if (method === "get_session") {
      if (params.session_id === "S-9" && delayOther) return slowOther;
      return params.session_id === "S-9" ? other : active;
    }
    return { items: [], has_more: false };
  });
  render(<AgentWorkbench api={api} />);
  await screen.findByText("已核对");
  delayOther = true;
  fireEvent.click(within(rail()).getByRole("button", { name: "客户核对" }));
  fireEvent.click(within(rail()).getByRole("button", { name: "今天的物料核对" }));
  await waitFor(() =>
    expect(api.mock.calls.filter((call) => call[0] === "get_session" && call[1].session_id === "S-1").length).toBeGreaterThan(1),
  );
  releaseSlow(other);
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(screen.getByText("已核对")).toBeTruthy();
  expect(screen.queryByText("客户已核对")).toBeNull();
});

it("搜索请求乱序返回时，只展示最后一次搜索选中的会话", async () => {
  let releaseSlow;
  const slowSession = new Promise((resolve) => { releaseSlow = resolve; });
  const api = vi.fn(async (method, params) => {
    if (method === "search_sessions") {
      if (params.query === "旧") return { items: [{ id: "S-old", title: "旧搜索" }], has_more: false };
      if (params.query === "新") return { items: [{ id: "S-9", title: other.title }], has_more: false };
      return { items: [{ id: "S-1", title: active.title }], has_more: false };
    }
    if (method === "get_session") {
      if (params.session_id === "S-old") return slowSession;
      return params.session_id === "S-9" ? other : active;
    }
    return { items: [], has_more: false };
  });
  render(<AgentWorkbench api={api} />);
  await screen.findByText("已核对");
  fireEvent.click(within(rail()).getByRole("button", { name: "新建会话" }));
  const search = screen.getByRole("searchbox", { name: "搜索会话" });
  fireEvent.change(search, { target: { value: "旧" } });
  await waitFor(() => expect(api).toHaveBeenCalledWith("get_session", { session_id: "S-old" }));
  fireEvent.change(search, { target: { value: "新" } });
  expect(await screen.findByText("客户已核对")).toBeTruthy();
  releaseSlow({ ...active, id: "S-old", messages: [{ ...active.messages[0], answer: "陈旧搜索结果" }] });
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(screen.queryByText("陈旧搜索结果")).toBeNull();
  expect(screen.getByText("客户已核对")).toBeTruthy();
});

it("归档当前会话后不会把它重新打开，而是落到剩下的会话", async () => {
  let archivedNow = false;
  const api = vi.fn(async (method, params) => {
    if (method === "search_sessions")
      return {
        items: archivedNow
          ? [{ id: "S-9", title: other.title, modified: "2026-08-28 10:00:00" }]
          : [
              { id: "S-1", title: active.title, modified: "2026-08-29 10:00:00" },
              { id: "S-9", title: other.title, modified: "2026-08-28 10:00:00" },
            ],
        has_more: false,
      };
    if (method === "get_session") return params.session_id === "S-9" ? other : { ...active, archived: archivedNow };
    if (method === "archive_session") {
      archivedNow = true;
      return {};
    }
    return { items: [], has_more: false };
  });
  render(<AgentWorkbench api={api} initialSession="S-1" />);
  await screen.findByText("已核对");
  fireEvent.click(screen.getByRole("button", { name: "归档当前会话" }));
  expect(await screen.findByText("客户已核对")).toBeTruthy();
  expect(screen.queryByText("归档会话为只读")).toBeNull();
});

it("自己发消息刷新列表后，正在看的会话不会标成有新消息", async () => {
  let bumped = false;
  const api = vi.fn(async (method) => {
    if (method === "search_sessions")
      return {
        items: [{ id: "S-1", title: active.title, modified: bumped ? "2026-08-29 10:05:00" : "2026-08-29 10:00:00" }],
        has_more: false,
      };
    if (method === "get_session") return active;
    if (method === "send_message") {
      bumped = true;
      return active;
    }
    return { items: [], has_more: false };
  });
  render(<AgentWorkbench api={api} />);
  fireEvent.click(await within(rail()).findByRole("button", { name: "今天的物料核对" }));
  await screen.findByText("已核对");
  fireEvent.change(screen.getByLabelText("业务问题"), { target: { value: "再查一次" } });
  fireEvent.submit(screen.getByRole("form", { name: "Agent 输入区" }));
  await waitFor(() => expect(api.mock.calls.filter((call) => call[0] === "search_sessions").length).toBeGreaterThan(1));
  expect(within(rail()).queryByRole("button", { name: /有新消息/ })).toBeNull();
  // 打开期间已把新的活动时间当作已读，切走也不应再冒出新消息标记。
  fireEvent.click(within(rail()).getByRole("button", { name: "新建会话" }));
  expect(within(rail()).queryByRole("button", { name: /有新消息/ })).toBeNull();
});

it("其他会话新出现的待确认在轮询后点亮橙点，不用切换会话", async () => {
  let pendingReady = false;
  const api = vi.fn(async (method) => {
    if (method === "search_sessions")
      return {
        items: [
          { id: "S-1", title: active.title, modified: "2026-08-29 10:00:00", pending_count: 0 },
          { id: "S-9", title: other.title, modified: "2026-08-28 10:00:00", pending_count: pendingReady ? 1 : 0 },
        ],
        has_more: false,
      };
    if (method === "get_session") return active;
    return { items: [], has_more: false };
  });
  render(<AgentWorkbench api={api} initialSession="S-1" pollInterval={40} />);
  await screen.findByText("已核对");
  pendingReady = true;
  expect(await within(rail()).findByRole("button", { name: "客户核对（需要确认）" })).toBeTruthy();
  expect(api.mock.calls.some((call) => call[0] === "list_pending")).toBe(false);
});
