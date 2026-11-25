// 验收手机 Demo 的「首次启动自动归因」分支：不传渠道号，由服务端按
// 百度推广回传的 IP / 设备号 判定 bbdd1，再按该渠道展示配置下发。
// 在 Node vm 里跑 demo/index.html 的真实内联 JS，fetch 指向真实 8790 服务。
// 用法: node server/test_demo_auto.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const BASE = process.env.TEST_BASE || "http://127.0.0.1:8790";
const IP = process.env.TEST_IP || "192.168.11.14";
const DEV = process.env.TEST_DEV || "DEMO-BAIDU-001";
const html = fs.readFileSync(path.join(ROOT, "demo/index.html"), "utf8");
const code = html.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];

const results = [];
const check = (name, ok, detail) => results.push({ name, pass: !!ok, detail: detail ?? null });

function makeEl() {
  return {
    innerHTML: "", textContent: "", value: "", checked: true, style: {},
    dataset: {}, classList: { add() {}, remove() {} },
    addEventListener() {}, removeEventListener() {}, appendChild() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    closest() { return null; }, remove() {}, focus() {}, blur() {}, click() {},
  };
}
const screenEl = makeEl();
const document = {
  querySelector: (sel) => sel === "#screen" ? screenEl : makeEl(),
  createElement: () => makeEl(),
  addEventListener() {}, body: makeEl(),
};
const search = `?auto=1&ip=${encodeURIComponent(IP)}&dev=${encodeURIComponent(DEV)}`;
const ctx = vm.createContext({
  console, URL, URLSearchParams, JSON, Math, Date, Number, String,
  Array, Object, Set, Map, Promise, Error, Intl,
  document, navigator: { userAgent: "node-selftest", language: "zh-CN" },
  screen: { width: 1170, height: 2532 }, window: { devicePixelRatio: 3 },
  location: { search, href: BASE + "/demo" + search },
  fetch: (u, opt) => fetch(new URL(String(u), BASE), opt),
  setTimeout: () => 0, clearTimeout: () => {},
  setInterval: () => 0, clearInterval: () => {},
});
vm.runInContext(code, ctx, { filename: "demo/index.html" });
const run = (expr) => vm.runInContext(expr, ctx);

(async () => {
  await new Promise((r) => setTimeout(r, 1400));   // 等真实网络请求完成
  const attr = run("ATTR");
  const cfg = run("CFG");
  check("Demo 走的是 auto 归因分支", run("AUTO") === true, run("AUTO"));
  check("服务端返回渠道号 bbdd1", attr && attr.channel === "bbdd1", attr);
  check("归因方式来自百度 API 回传/绑定（非剪贴板）",
        attr && /device_exact|binding|ip_from_partner_api|ip_plus_fingerprint/.test(attr.method || ""),
        attr && attr.method);
  check("Demo 实际加载了 bbdd1 渠道配置",
        cfg && cfg.channel === "bbdd1" && cfg.mode === "paywall",
        cfg && { channel: cfg.channel, mode: cfg.mode });
  check("bbdd1 的专属展示已生效（20s 试看 / 2 档套餐 / 2 个玩法）",
        cfg && cfg.paywall.trial.countdown_seconds === 20 &&
        cfg.paywall.vip.packages.length === 2 &&
        cfg.normal.craft.features.length === 2,
        cfg && { trial: cfg.paywall.trial.countdown_seconds,
                 packages: cfg.paywall.vip.packages.length,
                 features: cfg.normal.craft.features.length });
  check("渠道下拉里已加入自动解析出的 bbdd1",
        run("CHANNELS.includes('bbdd1')") === true, run("CHANNELS"));

  const passed = results.filter((r) => r.pass).length;
  const dest = path.join(ROOT, "evidence", "demo_auto_test.json");
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, JSON.stringify({
    base: BASE, search, passed, total: results.length, results,
  }, null, 2), "utf8");
  for (const r of results) {
    console.log(`  ${r.pass ? "PASS" : "FAIL"}  ${r.name}` +
      (r.detail !== null ? `  | ${JSON.stringify(r.detail)}` : ""));
  }
  console.log("=".repeat(78));
  console.log(`DEMO AUTO ATTRIBUTION ${passed === results.length ? "PASS" : "FAIL"}  ${passed}/${results.length}`);
  console.log("saved:", dest);
  process.exit(passed === results.length ? 0 : 1);
})();
