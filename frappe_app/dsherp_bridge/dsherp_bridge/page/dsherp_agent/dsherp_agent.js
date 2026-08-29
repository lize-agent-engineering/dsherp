frappe.pages["dsherp-agent"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({parent: wrapper, title: "Agent 工作台", single_column: true});
  const root = document.createElement("div");
  page.body.append(root);
  let active = false;
  let dispose = null;
  function load(tag, url) {
    return new Promise((resolve, reject) => {
      const node = document.createElement(tag);
      if (tag === "link") { node.rel = "stylesheet"; node.href = url; }
      else node.src = url;
      node.onload = resolve;
      node.onerror = () => reject(new Error("无法加载 " + url));
      document.head.append(node);
    });
  }
  const assets = Promise.all([
    load("link", "/assets/dsherp_bridge/dist/agent-workbench.css"),
    load("script", "/assets/dsherp_bridge/dist/agent-workbench.js"),
  ]);
  // Agent settings live in the native page header, not in a second in-app
  // toolbar. add_button also registers the mobile menu entry for us.
  let settings = null;
  function ensureSettingsButton() {
    if (settings) return;
    settings = page.add_button("Agent 设置", () => {
      if (dispose && dispose.openSettings) dispose.openSettings();
      else frappe.show_alert({message: "Agent 工作台尚未加载完成", indicator: "orange"});
    }, {icon: "setting-gear"});
  }
  $(wrapper).on("hide", () => { active = false; if (dispose) dispose(); dispose = null; });
  wrapper.on_page_show = function () {
    active = true;
    const globalAgent = document.getElementById("dsherp-context-root");
    if (globalAgent) globalAgent.hidden = true;
    if (!dispose) root.textContent = "正在加载 Agent 工作台…";
    assets.then(() => {
      if (!active || dispose) return;
      if (!window.dsherpAgentWorkbench) throw new Error("Agent 工作台资源未加载");
      root.textContent = "";
      dispose = window.dsherpAgentWorkbench.mount(root);
      ensureSettingsButton();
    }).catch(() => { if (active) root.textContent = "Agent 工作台加载失败。请检查构建资源后刷新页面。"; });
  };
  $(wrapper).on("hide", () => {
    const globalAgent = document.getElementById("dsherp-context-root");
    if (globalAgent) globalAgent.hidden = false;
  });
};
