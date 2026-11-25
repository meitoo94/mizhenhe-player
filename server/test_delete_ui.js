// App 内账号注销 UI 全流程验收：在 Node vm 里跑 demo/index.html 的真实内联 JS，
// 连真实 8790 服务，模拟「未付费停在会员页 → 点注销入口 → 输入注销 → 确认」。
// 验证浮层不受付费墙闭环拦截、请求真发到服务端、本地登录态与会员被清空。
// 用法: node server/test_delete_ui.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const BASE = process.env.TEST_BASE || "http://127.0.0.1:8790";
const html = fs.readFileSync(path.join(ROOT, "demo/index.html"), "utf8");
const block = html.match(/<script[^>]*>([\s\S]*?)<\/script>/);
if (!block) throw new Error("demo/index.html 里没有内联 script");

const results = [];
const check = (name, ok, detail) => results.push({ name, pass: !!ok, detail: detail ?? null });
const layers = new Map();
let createCalls = 0;

function makeEl(id) {
  const el = {
    id: id || "", innerHTML: "", textContent: "", value: "", checked: true,
    disabled: false, removed: false, style: {}, dataset: {}, children: {}, handlers: {},
    classList: { add() {}, remove() {} },
    addEventListener(ev, fn) { this.handlers[ev] = fn; },
    removeEventListener() {}, appendChild() {}, focus() {}, blur() {}, click() {},
    querySelector(sel) {
      if (!this.children[sel]) this.children[sel] = makeEl(sel);
      return this.children[sel];
    },
    querySelectorAll() { return []; },
    closest() { return null; },
    remove() { this.removed = true; },
  };
  return el;
}
const screen = makeEl("#screen");
let screenAppends = 0;
screen.appendChild = () => { screenAppends++; };
const fakeVideo = { currentTime: 0, readyState: 1, style: {}, addEventListener() {} };
screen.querySelector = (sel) => sel === "#vd" ? fakeVideo : makeEl(sel);
const body = makeEl("body");
body.appendChild = (el) => { if (el.id) layers.set(el.id, el); };
const document = {
  querySelector: (sel) => sel === "#screen" ? screen
    : sel === "#vd" ? fakeVideo : makeEl(sel),
  getElementById: (id) => layers.get(id) || null,
  createElement() { createCalls++; return makeEl(); },
  addEventListener() {},
  body,
};
const ctx = vm.createContext({
  console, URL, URLSearchParams, JSON, Math, Date, Number, String,
  Array, Object, Set, Map, Promise, Error, Intl,
  document,
  location: {
    search: "?ch=wn_douyin_a&step=vip",
    href: BASE + "/demo?ch=wn_douyin_a&step=vip",
  },
  navigator: { userAgent: "node-selftest", language: "zh-CN" },
  screen: { width: 390, height: 844 }, window: { devicePixelRatio: 3 },
  fetch: (u, opt) => fetch(new URL(String(u), BASE), opt),
  setTimeout: () => 0, clearTimeout: () => {},
  setInterval: () => 0, clearInterval: () => {},
});
vm.runInContext(block[1], ctx, { filename: "demo/index.html" });
const run = (expr) => vm.runInContext(expr, ctx);
const api = async (p, b) => {
  const r = await fetch(BASE + p, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(b),
  });
  return r.json();
};

(async () => {
  // 造一个真实登录票据（服务端注销要求登录态）
  const login = await api("/v1/login", {
    phone: "13900139000", confirm_phone: "13900139000", agreed: true,
    idfv: "UI-DELETE-DEVICE", channel: "wn_douyin_a",
  });
  check("前置：真实登录拿到票据", !!login.ticket, login.phone_masked);

  // 连的是真实服务，loadConfig 要走完网络；轮询等到 ?step=vip 生效
  for (let i = 0; i < 40 && run("step") !== "vip"; i++) {
    await new Promise((r) => setTimeout(r, 50));
  }
  check("首屏命中渠道停在会员页且闭环开启",
        run("step") === "vip" && run("forced()") === true, run("step"));

  run(`loginTicket = ${JSON.stringify(login.ticket)}; logged = true; loginPhoneMasked = "139****9000";`);
  const before = screenAppends;
  run('act("deleteAccount")');
  const layer = layers.get("acctDelLayer");
  check("会员页点注销弹出独立浮层（未被付费墙拦住）", !!layer, !!layer);
  check("点击注销没有额外 toast / 提示", screenAppends === before,
        screenAppends - before);
  check("浮层写明删除范围与二次确认",
        layer.innerHTML.includes("注销账号") && layer.innerHTML.includes("确认注销")
        && layer.innerHTML.includes("此操作不可撤销"), null);
  check("浮层挂在当前会员页之上", run("step") === "vip", run("step"));

  // 二次确认输错：不发起请求
  const confirmEl = layer.querySelector("#delConfirm");
  confirmEl.value = "随便";
  await layer.querySelector("#delSubmit").handlers.click();
  check("未输入「注销」不发请求、不删账号",
        layer.querySelector("#delErr").textContent.includes("请输入") &&
        run("loginTicket") === login.ticket,
        layer.querySelector("#delErr").textContent);

  // 正确输入 → 真实请求 /v1/account/delete
  confirmEl.value = "注销";
  const submitted = layer.querySelector("#delSubmit").handlers.click();
  if (submitted && typeof submitted.then === "function") await submitted;
  await new Promise((r) => setImmediate(r));

  check("注销成功后浮层关闭", layer.removed === true, layer.removed);
  check("本地清掉登录态 / 票据 / 会员",
        run("logged") === false && run("vip") === false && run("loginTicket") === "",
        { logged: run("logged"), vip: run("vip"), ticket: run("loginTicket") });
  check("注销后回到未登录页，不误解锁完整功能首页",
        run("step") === "login", run("step"));
  check("注销后付费墙仍保持闭环（不因注销放行）",
        run("forced()") === true, run("forced()"));

  const led = await fetch(BASE + "/admin/account/deletions").then((r) => r.json());
  check("服务端台账新增一条注销回执",
        led.total > 0 && led.deletions.some((x) => typeof x.receipt === "string"),
        led.total);
  const blob = JSON.stringify(led.deletions[led.deletions.length - 1] || {});
  check("台账不含手机号明文", !blob.includes("13900139000"), null);

  const passed = results.filter((r) => r.pass).length;
  const dest = path.join(ROOT, "evidence", "delete_ui_test.json");
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, JSON.stringify({ base: BASE, passed, total: results.length,
                                         results }, null, 2), "utf8");
  for (const r of results) {
    console.log(`  ${r.pass ? "PASS" : "FAIL"}  ${r.name}` +
      (r.detail !== null ? `  | ${JSON.stringify(r.detail)}` : ""));
  }
  console.log("=".repeat(78));
  console.log(`ACCOUNT DELETION UI ${passed === results.length ? "PASS" : "FAIL"}  ${passed}/${results.length}`);
  console.log("saved:", dest);
  process.exit(passed === results.length ? 0 : 1);
})();
