// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { afterEach, expect, it, vi } from "vitest";
const script = readFileSync(
  "../frappe_app/dsherp_bridge/dsherp_bridge/page/dsherp_studio/dsherp_studio.js",
  "utf8",
);
afterEach(() => {
  document.head.innerHTML = "";
  document.body.innerHTML = "";
});
const flush = async () => {
  for (let i = 0; i < 5; i++) await Promise.resolve();
};
function setup() {
  const handlers = {};
  const wrapper = document.createElement("div");
  document.body.append(wrapper);
  const dispose = vi.fn();
  const mount = vi.fn(() => dispose);
  const page = {};
  runInNewContext(script, {
    document,
    window: { dsherpStudio: { mount } },
    frappe: {
      pages: { "dsherp-studio": page },
      ui: { make_app_page: () => ({ body: wrapper }) },
      require: () => new Promise(() => {}),
    },
    $: () => ({
      on: (event, handler) => {
        handlers[event] = handler;
      },
    }),
  });
  page.on_page_load(wrapper);
  return {
    wrapper,
    mount,
    dispose,
    show: () => wrapper.on_page_show?.(),
    hide: () => handlers.hide?.(),
    load: () =>
      document.head
        .querySelectorAll("script,link")
        .forEach((node) => node.dispatchEvent(new Event("load"))),
  };
}
it("离开 Desk 页面卸载 React，返回重新挂载，避免 body portal 残留", async () => {
  const s = setup();
  s.show();
  s.load();
  await flush();
  expect(s.mount).toHaveBeenCalledTimes(1);
  s.hide();
  expect(s.dispose).toHaveBeenCalledTimes(1);
  s.show();
  await flush();
  expect(s.mount).toHaveBeenCalledTimes(2);
});
it("资源加载期间已离开页面，不得在后台挂载弹窗与状态", async () => {
  const s = setup();
  s.show();
  s.hide();
  s.load();
  await flush();
  expect(s.mount).not.toHaveBeenCalled();
});
it("资源加载失败明确报错，不冻结整个原生 Desk", async () => {
  const s = setup();
  s.show();
  const asset = document.head.querySelector("script");
  expect(asset).not.toBeNull();
  asset.dispatchEvent(new Event("error"));
  await flush();
  expect(s.wrapper.textContent).toContain("原型加载失败");
  expect(s.mount).not.toHaveBeenCalled();
});
