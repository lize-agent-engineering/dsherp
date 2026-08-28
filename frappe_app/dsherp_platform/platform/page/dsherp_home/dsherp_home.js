frappe.pages["dsherp-home"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: "业务工作台",
    single_column: true,
  });
  const root = document.createElement("div");
  page.body.append(root);
  let active = false;
  let dispose = null;
  // Native browser resource events report 404/network failures. Frappe v15
  // frappe.require has no rejection path and can leave its global freeze active.
  function load(tag, url) {
    return new Promise((resolve, reject) => {
      const node = document.createElement(tag);
      if (tag === "link") {
        node.rel = "stylesheet";
        node.href = url;
      } else {
        node.src = url;
      }
      node.onload = () => resolve();
      node.onerror = () => reject(new Error("无法加载 " + url));
      document.head.append(node);
    });
  }
  const assets = Promise.all([
    load("link", "/assets/dsherp_bridge/dist/studio.css"),
    load("script", "/assets/dsherp_bridge/dist/studio.js"),
  ]);
  // Frappe v15 container triggers jQuery hide; pageview invokes on_page_show.
  $(wrapper).on("hide", () => {
    active = false;
    if (dispose) dispose();
    dispose = null;
  });
  wrapper.on_page_show = function () {
    active = true;
    if (!dispose) root.textContent = "正在加载 dsherp 工作台…";
    assets
      .then(() => {
        if (!active || dispose) return;
        if (!window.dsherpStudio) throw new Error("dsherp 工作台资源未加载");
        dispose = window.dsherpStudio.mountPortal(root);
      })
      .catch(() => {
        if (active)
          root.textContent =
            "工作台加载失败。请先构建前端资源并检查网络，再刷新页面。";
      });
  };
};
