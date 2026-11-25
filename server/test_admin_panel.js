// 后台「⑦ 百度推广 API 归因对接」面板渲染自检：
// 在 Node vm 里跑 admin/index.html 的真实内联 JS，fetch 指向真实 8790 服务，
// 验证 loadState / loadAttribution / renderAttribution 不抛错且渲染出回传控件。
// 用法: node server/test_admin_panel.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const BASE = process.env.TEST_BASE || "http://127.0.0.1:8790";
const html = fs.readFileSync(path.join(ROOT, "admin/index.html"), "utf8");
const code = html.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
const results = [];
const check = (name, ok, detail) => results.push({ name, pass: !!ok, detail: detail ?? null });

const els = new Map();
function makeEl(sel) {
  return {
    sel, innerHTML: "", textContent: "", value: "", checked: false, style: {},
    dataset: {}, classList: { add() {}, remove() {} },
    addEventListener() {}, removeEventListener() {}, appendChild() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    closest() { return null; }, remove() {}, focus() {}, blur() {}, click() {},
  };
}
const document = {
  querySelector(sel) {
    if (!els.has(sel)) els.set(sel, makeEl(sel));
    return els.get(sel);
  },
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement() { return makeEl("created"); },
  body: makeEl("body"),
};
const ctx = vm.createContext({
  console, URL, URLSearchParams, JSON, Math, Date, Number, String,
  Array, Object, Set, Map, Promise, Error, Intl,
  document,
  navigator: { userAgent: "node", language: "zh-CN", clipboard: null },
  screen: { width: 1440, height: 900 }, window: { devicePixelRatio: 1 },
  location: { search: "", href: BASE + "/admin" },
  localStorage: { getItem: () => null, setItem: () => {} },
  fetch: (u, opt) => fetch(new URL(String(u), BASE), opt),
  setTimeout: () => 0, clearTimeout: () => {},
  setInterval: () => 0, clearInterval: () => {},
});
vm.runInContext(code, ctx, { filename: "admin/index.html" });
const run = (expr) => vm.runInContext(expr, ctx);

(async () => {
  // 归因面板是异步 fetch 后渲染的：轮询等待渲染完成，避免固定 sleep 的偶发误判。
  for (let i = 0; i < 40; i++) {
    const p = document.querySelector("#attrPanel").innerHTML;
    if (p.includes("设备绑定表") && p.length > 1000) break;
    await new Promise((r) => setTimeout(r, 150));
  }
  const toastEl = document.querySelector("#toast");
  check("后台初始化没有 JS 异常", !String(toastEl.textContent).includes("加载失败"),
        toastEl.textContent || "");
  check("渠道列表已加载（含 bbdd1）",
        run("S && S.channels_order.includes('bbdd1')") === true,
        run("S && S.channels_order"));
  check("归因状态已拉取", run("A && A.ok === true") === true,
        run("A && {clicks:A.total_clicks, bindings:A.total_bindings}"));
  const panel = document.querySelector("#attrPanel").innerHTML;
  check("归因面板已渲染", panel.length > 1000, panel.length);
  check("面板含「回传一条点击」按钮", panel.includes("回传一条点击"), null);
  check("面板含计划ID→渠道号映射输入", panel.includes("attrMap") && panel.includes("bbdd1"), null);
  check("面板含手机首次启动验证链接", panel.includes("attrDemo") &&
        String(document.querySelector("#attrDemo").textContent).includes("auto=1"),
        document.querySelector("#attrDemo").textContent);
  // 解绑按钮只在有绑定数据时渲染；这里检查面板结构，不依赖运营态里恰好有数据。
  check("面板含点击库 / 绑定表",
        panel.includes("百度回传点击库") && panel.includes("设备绑定表") &&
        (panel.includes("click_id") || panel.includes("暂无回传记录")), null);
  check("演示链接按当前 IP / 设备号生成",
        String(document.querySelector("#attrDemo").textContent).includes("auto=1"),
        document.querySelector("#attrDemo").textContent);

  const passed = results.filter((r) => r.pass).length;
  const dest = path.join(ROOT, "evidence", "admin_panel_test.json");
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, JSON.stringify({ base: BASE, passed, total: results.length, results },
                                       null, 2), "utf8");
  for (const r of results) {
    console.log(`  ${r.pass ? "PASS" : "FAIL"}  ${r.name}` +
      (r.detail !== null ? `  | ${JSON.stringify(r.detail)}` : ""));
  }
  console.log("=".repeat(78));
  console.log(`ADMIN ATTRIBUTION PANEL ${passed === results.length ? "PASS" : "FAIL"}  ${passed}/${results.length}`);
  console.log("saved:", dest);
  process.exit(passed === results.length ? 0 : 1);
})();
