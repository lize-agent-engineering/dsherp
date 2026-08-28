import { describe, it, expect } from "vitest";
import { initialState, transition, canAccess } from "./model.js";

const build = () => transition(initialState(), { type: "preview" });
const confirmed = () => transition(build(), { type: "confirm" });
describe("演示发布与企业状态", () => {
  it("预览并确认具体版本才允许演示发布，成功后出现业务菜单", () => {
    expect(() =>
      transition(build(), { type: "publish", outcome: "success" }),
    ).toThrow("确认");
    const result = transition(confirmed(), {
      type: "publish",
      outcome: "success",
    });
    expect(result.publishedVersion).toBe(1);
    expect(result.history[0].status).toBe("success");
    expect(canAccess(result, "repairs")).toBe(true);
  });
  it("修改需求后旧确认失效，版本递增", () => {
    const state = transition(confirmed(), {
      type: "edit",
      value: "维修费用超过 500 元需要主管审批",
    });
    expect(state.confirmation).toBeNull();
    expect(state.candidate).toBeNull();
    expect(transition(state, { type: "preview" }).candidate.version).toBe(2);
    expect(() =>
      transition(state, { type: "publish", outcome: "success" }),
    ).toThrow();
  });
  it("权限撤销不能使用已有确认", () => {
    const state = transition(confirmed(), { type: "role", role: "employee" });
    expect(state.confirmation).toBeNull();
    expect(() =>
      transition(state, { type: "publish", outcome: "success" }),
    ).toThrow("发布权限");
    expect(canAccess(state, "build")).toBe(false);
  });
  it("业务、构建、平台权限不互相包含", () => {
    for (const role of ["employee", "designer", "publisher"]) {
      expect(canAccess({ ...initialState(), role }, "platform")).toBe(false);
    }
    expect(canAccess({ ...initialState(), role: "operator" }, "build")).toBe(
      false,
    );
    expect(canAccess({ ...initialState(), role: "operator" }, "platform")).toBe(
      true,
    );
  });
  it("目标版本变化导致发布冲突，不覆盖配置", () => {
    const state = transition(confirmed(), { type: "targetChanged" });
    const result = transition(state, { type: "publish", outcome: "success" });
    expect(result.result.status).toBe("conflict");
    expect(result.publishedVersion).toBe(0);
  });
  it.each(["failed", "conflict", "partial", "unknown"])(
    "%s 不显示发布成功或新增菜单",
    (outcome) => {
      const state = transition(confirmed(), { type: "publish", outcome });
      expect(state.result.status).toBe(outcome);
      expect(state.publishedVersion).toBe(0);
      expect(canAccess(state, "repairs")).toBe(false);
      expect(state.confirmation).toBeNull();
      if (outcome === "partial")
        expect(state.result.completed).toContain("创建演示字段");
    },
  );
  it("结果不明时禁止重新发布，必须核实后才能继续", () => {
    const state = transition(confirmed(), {
      type: "publish",
      outcome: "unknown",
    });
    expect(() => transition(state, { type: "confirm" })).toThrow("核实");
    expect(() => transition(state, { type: "preview" })).toThrow("核实");
    const resolved = transition(state, { type: "reconcile" });
    expect(resolved.result.status).toBe("success");
    expect(resolved.history).toHaveLength(1);
    expect(resolved.publishedVersion).toBe(1);
  });
  it("切换企业清空需求、确认、任务、业务记录和页面状态", () => {
    const state = transition(
      transition(confirmed(), { type: "publish", outcome: "success" }),
      { type: "tenant", tenant: "beta" },
    );
    expect(state.tenant).toBe("beta");
    expect(state.confirmation).toBeNull();
    expect(state.publishedVersion).toBe(0);
    expect(state.history).toEqual([]);
    expect(state.repairs).toEqual([]);
    expect(state.page).toBe("work");
    expect(state.requirement).not.toContain("甲");
  });
  it("会话失效清空敏感页面，所有动作明确拒绝", () => {
    const state = transition(confirmed(), { type: "expire" });
    expect(state.candidate).toBeNull();
    expect(state.history).toEqual([]);
    expect(canAccess(state, "build")).toBe(false);
    expect(() => transition(state, { type: "preview" })).toThrow("会话");
  });
  it("演示业务录入校验必填，保存只进入本企业内存", () => {
    const state = transition(confirmed(), {
      type: "publish",
      outcome: "success",
    });
    expect(() =>
      transition(state, { type: "repair", equipment: "", description: "异响" }),
    ).toThrow("设备");
    const next = transition(state, {
      type: "repair",
      equipment: "演示空压机",
      description: "异响",
    });
    expect(next.repairs[0]).toMatchObject({
      equipment: "演示空压机",
      description: "异响",
      status: "待分配",
    });
    expect(state.repairs).toEqual([]);
  });
});

it("已发布候选不可重复发布，必须修改后生成新版本", () => {
  const state = transition(confirmed(), {
    type: "publish",
    outcome: "success",
  });
  expect(() => transition(state, { type: "confirm" })).toThrow("已发布");
  expect(() => transition(state, { type: "preview" })).toThrow("已发布");
  const edited = transition(state, { type: "edit", value: "新的维修需求" });
  expect(transition(edited, { type: "preview" }).candidate.version).toBe(2);
});
