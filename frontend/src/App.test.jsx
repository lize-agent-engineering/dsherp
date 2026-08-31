// @vitest-environment jsdom
import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import App from "./App.jsx";
afterEach(cleanup);
const click = (name) =>
  fireEvent.click(screen.getByRole("button", { name, exact: true }));
async function prepare() {
  render(<App />);
  click("开始构建");
  fireEvent.change(screen.getByRole("textbox", { name: "业务需求" }), {
    target: { value: "设备维修：员工报修，主管审批费用。" },
  });
  click("生成演示方案");
  fireEvent.click(screen.getByRole("tab", { name: "版本与发布" }));
  click("确认候选版本");
  click("确认此版本（演示）");
}
it("完整演示闭环可操作，始终明确未真实发布", async () => {
  await prepare();
  click("执行演示发布");
  expect(
    screen
      .getAllByRole("alert")
      .some((node) => node.textContent.includes("演示发布完成")),
  ).toBe(true);
  expect(screen.getByText(/没有向 ERP 安装或发布/)).toBeTruthy();
  click("打开演示业务页面");
  click("新增演示报修");
  fireEvent.change(screen.getByRole("textbox", { name: "设备名称" }), {
    target: { value: "演示空压机" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "故障描述" }), {
    target: { value: "异常振动" },
  });
  click("保存到本页演示");
  expect(await screen.findByText("演示空压机")).toBeTruthy();
  expect(screen.getByRole("cell", { name: "异常振动" })).toBeTruthy();
});
it("切换角色立即移除确认弹窗，直接进入构建页显示无权访问", () => {
  render(<App />);
  click("开始构建");
  click("生成演示方案");
  fireEvent.click(screen.getByRole("tab", { name: "版本与发布" }));
  click("确认候选版本");
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "演示角色" }));
  fireEvent.click(screen.getByText("业务员工"));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByText("无权访问此空间")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "执行演示发布" })).toBeNull();
});
it("未知结果提示核实且没有再次执行发布入口", async () => {
  await prepare();
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "演示发布结果" }));
  fireEvent.click(screen.getByText("结果待核实"));
  click("执行演示发布");
  expect(screen.queryByRole("button", { name: "执行演示发布" })).toBeNull();
  click("模拟核实：版本已生效");
  expect(screen.getByText(/没有再次执行发布/)).toBeTruthy();
});
it("平台界面只显示技术任务，无客户业务字段", () => {
  render(<App />);
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "演示角色" }));
  fireEvent.click(screen.getByText("平台运维"));
  fireEvent.click(screen.getByRole("menuitem", { name: "平台管理" }));
  const main = screen.getByRole("main");
  expect(within(main).getByText("开通与部署任务")).toBeTruthy();
  expect(within(main).queryByText("故障描述")).toBeNull();
});
