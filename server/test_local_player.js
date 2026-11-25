// 合规态本地播放验收：商店名「米帧盒播放器」必须让审核员找到真能播视频的入口。
// 在 Node vm 里跑 demo/index.html 的真实内联 JS，模拟文件选择器选了一个本地视频。
// 用法: node server/test_local_player.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "demo/index.html"), "utf8");
const block = html.match(/<script[^>]*>([\s\S]*?)<\/script>/);
if (!block) throw new Error("demo/index.html 里没有内联 script");

const results = [];
const check = (name, ok, detail) => results.push({ name, pass: !!ok, detail: detail ?? null });
const created = [];
const appended = [];
const revoked = [];

function makeEl(tag) {
  const el = {
    tag, innerHTML: "", textContent: "", value: "", src: "", files: null,
    style: {}, dataset: {}, children: [], listeners: {}, removed: false,
    classList: { add() {}, remove() {} },
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
    removeEventListener() {},
    appendChild(c) { this.children.push(c); return c; },
    querySelector(sel) {
      if (sel === "video") return this._video || (this._video = makeEl("video"));
      if (sel === "#localClose") return this._close || (this._close = makeEl("button"));
      return null;
    },
    querySelectorAll() { return []; },
    closest() { return null; },
    remove() { this.removed = true; },
    focus() {}, blur() {}, click() { this.fire("click"); },
    fire(ev) { (this.listeners[ev] || []).forEach(f => f({ target: this })); },
    play() { return Promise.resolve(); },
    pause() {},
  };
  return el;
}

const screen = makeEl("div");
screen.querySelector = () => null;
const body = makeEl("body");
const fakeURL = {
  createObjectURL: () => "blob:local-selftest",
  revokeObjectURL: (u) => revoked.push(u),
};
const document = {
  querySelector: (sel) => sel === "#screen" ? screen : makeEl("div"),
  getElementById: () => null,
  createElement(tag) { const e = makeEl(tag); created.push(e); return e; },
  addEventListener() {},
  body,
};

const ctx = vm.createContext({
  console, URL: fakeURL, URLSearchParams, JSON, Math, Date, Number, String,
  Array, Object, Set, Map, Promise, Error, RegExp,
  document,
  window: { URL: fakeURL, webkitURL: fakeURL, devicePixelRatio: 1 },
  location: { search: "?ch=organic", href: "http://127.0.0.1:8790/demo?ch=organic" },
  navigator: { userAgent: "node-selftest", language: "zh-CN" },
  fetch: async () => { throw new Error("offline"); },   // 走 OFFLINE 内置配置
  setTimeout: () => 0, clearTimeout: () => {},
  setInterval: () => 0, clearInterval: () => {},
});
vm.runInContext(block[1], ctx, { filename: "demo/index.html" });
const run = (expr) => vm.runInContext(expr, ctx);

(async () => {
  await new Promise(r => setImmediate(r));
  await new Promise(r => setImmediate(r));

  // 1. 合规态「片仓」页按配置渲染出可点击的导入播放入口
  run(`CFG.normal.local_player = {enabled:true, button:"导入本地视频并播放",
        hint:"选择本机视频文件，仅在本地播放，不上传服务器", accept:"video/*"};
       vip = true; step = "home"; tab = 1; render();`);
  const home = screen.innerHTML;
  check("片仓页渲染本地播放入口", home.includes('data-act="localplay"'), null);
  check("按钮文案来自服务端配置", home.includes("导入本地视频并播放"), null);
  check("说明写明不上传服务器", home.includes("不上传服务器"), null);

  // 2. 关闭开关后入口消失（后台可控制）
  run(`CFG.normal.local_player.enabled = false; render();`);
  check("后台关闭后不再显示该入口", !screen.innerHTML.includes('data-act="localplay"'), null);
  run(`CFG.normal.local_player.enabled = true; render();`);

  // 3. 走真实事件分发，模拟用户选中一个本地视频
  run(`act("localplay")`);
  const inp = created.filter(e => e.tag === "input").pop();
  check("创建了文件选择器且只接受视频", !!inp && inp.accept === "video/*" && inp.type === "file",
        inp ? { type: inp.type, accept: inp.accept } : null);
  if (inp) {
    inp.files = [{ name: "我的片段.mp4" }];
    inp.fire("change");
  }
  const layer = appended.length ? appended[appended.length - 1] : (body.children.pop() || null);
  const layerHtml = layer ? layer.innerHTML : "";
  check("弹出本地播放层", !!layer && layerHtml.includes("<video"), null);
  check("播放层含真实 video 控件与文件名",
        layerHtml.includes("controls") && layerHtml.includes("我的片段.mp4"), null);
  check("用 blob URL 播本地文件（不上传）",
        !!(layer && layer.querySelector("video") && layer.querySelector("video").src === "blob:local-selftest"),
        layer ? layer.querySelector("video").src : null);

  // 4. 关闭播放层会回收 blob URL
  if (layer) {
    layer.querySelector("#localClose").fire("click");
    check("关闭后回收 blob URL 并移除播放层",
          revoked.includes("blob:local-selftest") && layer.removed === true,
          { revoked, removed: layer.removed });
  }

  // 5. 归因命中的付费墙态不会出现合规首页的播放入口
  run(`CFG = JSON.parse(JSON.stringify(OFFLINE.wn_douyin_a));
       CFG.channel = "wn_douyin_a"; vip = false; step = "vip"; render();`);
  check("付费墙态不含合规首页入口", !screen.innerHTML.includes('data-act="localplay"'), null);

  const passed = results.filter(r => r.pass).length;
  console.log("=".repeat(78));
  for (const r of results) console.log((r.pass ? "  PASS  " : "  FAIL  ") + r.name +
    (r.detail !== null && r.detail !== undefined ? "  | " + JSON.stringify(r.detail) : ""));
  console.log("-".repeat(78));
  console.log(`LOCAL PLAYER SELFTEST ${passed === results.length ? "PASS" : "FAIL"}  ${passed}/${results.length}`);
  fs.writeFileSync(path.join(ROOT, "evidence/local_player_test.json"),
    JSON.stringify({ base: "demo/index.html (vm)", ts: Date.now(), passed,
                     total: results.length, all_pass: passed === results.length,
                     checks: results }, null, 2), "utf8");
  console.log("saved: " + path.join(ROOT, "evidence/local_player_test.json"));
  process.exit(passed === results.length ? 0 : 1);
})();
