// 退出行为验收：点「暂不开通 / 稍后再说 / 返回」后静默回试看片段，
// 不弹任何提示，且保留原来的试看进度（剩 5s 就还是 5s；已看完就还是「试看结束」）。
// 直接在 Node vm 里跑 demo/index.html 的真实内联 JS，不改写页面逻辑。
// 用法: node server/test_exit_behavior.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "demo/index.html"), "utf8");
const block = html.match(/<script[^>]*>([\s\S]*?)<\/script>/);
if (!block) throw new Error("demo/index.html 里没有内联 script");

const results = [];
const check = (name, ok, detail) => results.push({ name, pass: !!ok, detail: detail ?? null });

let createCalls = 0;              // document.createElement 只在 showToast 里被调用
const intervalMap = new Map();
let tid = 0;

function makeEl() {
  return {
    innerHTML: "", textContent: "", value: "", checked: true,
    style: {}, dataset: {}, classList: { add() {}, remove() {} },
    addEventListener() {}, removeEventListener() {}, appendChild() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    closest() { return null; }, remove() {}, focus() {}, blur() {}, click() {},
  };
}
const screen = makeEl();
const fakeVideo = { currentTime: 0, readyState: 1, style: {}, addEventListener() {} };
screen.querySelector = (sel) => (sel === "#vd" ? fakeVideo : null);
const document = {
  querySelector: (sel) => sel === "#screen" ? screen
    : sel === "#vd" ? fakeVideo : makeEl(),
  createElement() { createCalls++; return makeEl(); },
  addEventListener() {},
  body: makeEl(),
};

const ctx = vm.createContext({
  console, URL, URLSearchParams, JSON, Math, Date, Number, String,
  Array, Object, Set, Map, Promise, Error,
  document,
  location: {
    search: "?ch=wn_douyin_a&step=vip",
    href: "http://127.0.0.1:8790/demo?ch=wn_douyin_a&step=vip",
  },
  navigator: { userAgent: "node-selftest" },
  fetch: async () => { throw new Error("offline"); },  // 走 OFFLINE 内置配置
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: (fn) => { const id = ++tid; intervalMap.set(id, fn); return id; },
  clearInterval: (id) => { intervalMap.delete(id); },
});
vm.runInContext(block[1], ctx, { filename: "demo/index.html" });
const run = (expr) => vm.runInContext(expr, ctx);
const tickAll = () => { for (const fn of [...intervalMap.values()]) fn(); };
const step = () => run("step");
const cd = () => run("cd");

(async () => {
  await new Promise((r) => setImmediate(r));   // 等 loadConfig 的异步回落
  await new Promise((r) => setImmediate(r));

  check("命中渠道后停在会员套餐页", step() === "vip", step());
  check("闭环开启 / 退出动作为 back_to_trial",
        run("forced()") === true && run("gateCfg().skip_action") === "back_to_trial",
        { forced: run("forced()"), skip_action: run("gateCfg().skip_action") });
  check("登录后 1 秒弹充值页仍保留",
        run("gateCfg().login_to_vip_delay_seconds") === 1,
        run("gateCfg().login_to_vip_delay_seconds"));
  check("首次进入时没有试看现场可恢复", run("trialSave === null"), run("trialSave"));

  // ① 从未看过片段：首次退出从片段开头开始（合理）
  let base = createCalls;
  run('act("home")');
  check("首次点「暂不开通 · 看片段」进入试看片段", step() === "trial", step());
  check("首次进入倒计时为完整 15s", cd() === 15, cd());
  check("点击后没有任何 toast / 提示", createCalls === base,
        { toast_created: createCalls - base });
  check("没有弹回充值页 / 没有进完整功能首页",
        step() !== "vip" && step() !== "home", step());

  // ② 看到剩 5s 时点「开通会员」，再点退出：必须还是 5s，视频也在原来那一帧
  run("cd=5");
  fakeVideo.currentTime = 7.5;
  run('act("tovip")');                              // 试看页右上「开通会员」
  check("离开试看时保存了现场", run("trialSave && trialSave.cd") === 5 &&
        run("trialSave && trialSave.videoTime") === 7.5, run("trialSave"));
  fakeVideo.currentTime = 0;                       // 模拟切页后播放器被销毁
  base = createCalls;
  run('act("home")');
  check("退出后剩余秒数还是 5s（不重播、不重置）", cd() === 5, cd());
  check("退出后视频回到原来看到的那一帧", fakeVideo.currentTime === 7.5,
        fakeVideo.currentTime);
  check("恢复进度时仍然没有任何提示", createCalls === base,
        { toast_created: createCalls - base });
  check("回充值的计时按剩余 5s 计算", run("reopenLeft") === 7, run("reopenLeft"));

  // ③ 片段看完后再静默回充值（全程无 toast）
  base = createCalls;
  for (let i = 0; i < 20; i++) tickAll();
  check("剩下的 5s 看完后静默回到充值页", step() === "vip", step());
  check("静默重弹也没有任何提示", createCalls === base,
        { toast_created: createCalls - base });

  // ④ 本来已经看完了，退出后还是「试看结束」，不会重播
  run('step="trialEnd"; render()');
  run('act("tovip")');                              // 从「试看结束」点开通会员
  check("离开「试看结束」时保存为已看完",
        run("trialSave && trialSave.step === 'trialEnd' && trialSave.cd === 0"),
        run("trialSave"));
  base = createCalls;
  run('act("home")');                               // 稍后再说
  check("已看完时退出仍是「试看结束」", step() === "trialEnd", step());
  check("已看完时倒计时保持 0，不重新开始", cd() === 0, cd());
  check("已看完状态不会重播倒计时", intervalMap.size <= 1, intervalMap.size);
  check("「稍后再说」没有任何提示", createCalls === base,
        { toast_created: createCalls - base });

  // ⑤ 切 Tab / 点玩法卡仍然静默拦回充值页
  base = createCalls;
  run('act("tab","2")');
  const tabOk = step() === "vip" && createCalls === base;
  run('step="vip"; render()');
  run('act("feat","帧类归档")');
  check("切 Tab / 点玩法卡静默回充值页，无提示",
        tabOk && step() === "vip" && createCalls === base,
        { tab_ok: tabOk, step: step(), toast_created: createCalls - base });

  // ⑥ 只有支付成功才放行到合规首页
  run("vip=true");
  run('act("home")');
  check("支付成功后退出才真正回合规首页", step() === "home", step());
  check("支付成功后 forced() 关闭", run("forced()") === false, run("forced()"));

  // ⑦ 切换渠道 / 重启 App 时清空旧进度
  run("restart()");
  check("重启后清空旧试看现场", run("trialSave === null"), run("trialSave"));

  const passed = results.filter((r) => r.pass).length;
  const out = {
    file: "demo/index.html",
    mode: "offline built-in config",
    channel: "wn_douyin_a",
    passed, total: results.length, results,
  };
  const dest = path.join(ROOT, "evidence", "exit_behavior_test.json");
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, JSON.stringify(out, null, 2), "utf8");
  for (const r of results) {
    console.log(`  ${r.pass ? "PASS" : "FAIL"}  ${r.name}` +
      (r.detail !== null ? `  | ${JSON.stringify(r.detail)}` : ""));
  }
  console.log("=".repeat(78));
  console.log(`EXIT BEHAVIOR SELFTEST ${passed === results.length ? "PASS" : "FAIL"}  ${passed}/${results.length}`);
  console.log("saved:", dest);
  process.exit(passed === results.length ? 0 : 1);
})();
