// 后台「⑧ 账号注销台账」面板渲染自检：
// 在 Node vm 里跑 admin/index.html 的真实内联 JS，fetch 指向真实 8790 服务，
// 验证 loadDeletions 能拉到台账并渲染回执 / 删除计数 / 哈希（且不泄露手机号明文）。
// 用法: node server/test_deletions_panel.js
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
  // 连真实服务，等台账渲染出来
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, 50));
    if (String(document.querySelector("#deletionList").innerHTML).includes("DEL")) break;
  }
  check("后台初始化没有 JS 异常",
        !String(document.querySelector("#toast").textContent).includes("加载失败"),
        document.querySelector("#toast").textContent || "");
  check("HTML 含 ⑧ 账号注销台账面板与刷新按钮",
        html.includes("⑧ 账号注销台账") && html.includes('id="delRefresh"'), null);
  check("HTML 引用 /admin/account/deletions 接口",
        code.includes("/admin/account/deletions"), null);

  const panel = String(document.querySelector("#deletionList").innerHTML);
  check("台账面板已渲染（非空）", panel.length > 50, panel.length);
  check("渲染出注销回执编号", panel.includes("DEL"), null);
  check("渲染出累计条数", panel.includes("累计"), null);
  check("渲染出各项删除计数",
        panel.includes("登录") && panel.includes("会员") &&
        panel.includes("绑定") && panel.includes("流水去标识"), null);
  // 回执编号本身含长数字串，不能用「11 位数字」粗判；直接查明文与掩码
  check("渲染出手机号哈希而非明文",
        panel.includes("手机号哈希") && !panel.includes("****")
        && !panel.includes("13900139000") && !panel.includes("13800138000"), null);
  check("台账数据来自服务端且 total > 0",
        (await fetch(BASE + "/admin/account/deletions").then((r) => r.json())).total > 0,
        null);

  const passed = results.filter((r) => r.pass).length;
  const dest = path.join(ROOT, "evidence", "deletions_panel_test.json");
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, JSON.stringify({ base: BASE, passed, total: results.length,
                                         results }, null, 2), "utf8");
  for (const r of results) {
    console.log(`  ${r.pass ? "PASS" : "FAIL"}  ${r.name}` +
      (r.detail !== null ? `  | ${JSON.stringify(r.detail)}` : ""));
  }
  console.log("=".repeat(78));
  console.log(`ADMIN DELETIONS PANEL ${passed === results.length ? "PASS" : "FAIL"}  ${passed}/${results.length}`);
  console.log("saved:", dest);
  process.exit(passed === results.length ? 0 : 1);
})();
