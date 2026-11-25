# -*- coding: utf-8 -*-
"""
渠道展示后台端到端实测：
  - 后台改栏目 / 功能卡 / 套餐显隐 / 价格 / 倒计时 -> /v1/config 立即变化
  - 关掉「会员页 / 收银台」模块 -> 下发配置里彻底不出现支付界面
  - 被隐藏的套餐下单被拒
  - 全局熔断 -> 所有渠道回落 normal, 关闭后恢复
  - 保存写盘 + 重启后仍生效
  - 最后把配置完整还原, 不留下被改坏的工程
用法: 先启动 server/app.py --port 8790 --host 0.0.0.0 --profile server/profiles/wanneng_rebrand.json
     再 python server/test_admin.py
"""
import copy
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("TEST_BASE", "http://127.0.0.1:8790")
TOKEN = os.environ.get("ADMIN_TOKEN", "admin-local")
ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(ROOT)
PROFILE = os.path.join(ROOT, "profiles", "wanneng_rebrand.json")
STATE = os.path.join(ROOT, "state", "display.json")
EVIDENCE = os.path.join(PROJ, "evidence", "admin_test.json")

RESULTS = []


def req(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(r, timeout=10)
        code, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read()
    try:
        return code, json.loads(raw.decode("utf-8"))
    except Exception:
        # 订单页是 HTML, 全文返回以便校验订单号(不要截断)
        return code, {"raw": raw.decode("utf-8", "ignore")}


def check(name, ok, detail=""):
    RESULTS.append({"name": name, "pass": bool(ok), "detail": detail})
    print(("  PASS  " if ok else "  FAIL  ") + name + ("  | " + str(detail) if detail else ""))
    return ok


def get(path):
    return req("GET", path)[1]


def post(path, body):
    return req("POST", path, body)


def save(profile, display):
    return post("/admin/save", {"token": TOKEN, "profile": profile, "display": display})


def preview(ch):
    return get("/admin/preview?ch=" + ch)


def main():
    global BASE
    print("=" * 78)
    print("渠道展示后台 · 端到端实测   BASE=" + BASE)
    print("=" * 78)

    st = get("/admin/state")
    assert st.get("ok"), "服务未就绪: " + str(st)[:200]
    orig_p = copy.deepcopy(st["profile"])
    orig_d = copy.deepcopy(st["display"])
    print("[0] 皮肤 %s | 局域网 %s:%s | 渠道 %s" %
          (st["profile_meta"]["name"], st["lan_ip"], st["port"], st["channels_order"]))

    # ---------------- 1. 初始态 ----------------
    p0 = preview("wn_douyin_a")
    c0 = p0["config"]
    check("初始: 渠道A 为 paywall", p0["mode"] == "paywall", p0["decision"])
    check("初始: 4 个 Tab 全部展示", len(c0["normal"]["tabs"]) == 4, c0["normal"]["tabs"])
    check("初始: 4 张玩法卡全展示", len(c0["normal"]["craft"]["features"]) == 4)
    check("初始: 5 个模块全开", all(c0["paywall"]["modules"].values()), c0["paywall"]["modules"])
    check("初始: 3 档套餐", len(c0["paywall"]["vip"]["packages"]) == 3)
    check("初始: 自然量为 normal", preview("organic")["mode"] == "normal")
    g0 = c0["paywall"].get("gate", {})
    check("闭环: 未付费常驻充值页", g0.get("force_paywall_until_vip") is True, g0)
    check("闭环: 登录后 1 秒弹充值", g0.get("login_to_vip_delay_seconds") == 1,
          g0.get("login_to_vip_delay_seconds"))

    # ---------------- 2. 后台改栏目 / 套餐 / 价格 / 倒计时 ----------------
    p = copy.deepcopy(orig_p)
    d = copy.deepcopy(orig_d)
    dc = d["channels"]["wn_douyin_a"]
    dc["normal_tabs"] = ["今映", "造片", "账户"]                 # 关掉「片仓」
    dc["home_sections"] = ["精选频道"]                            # 只留一个分区
    dc["craft_features"] = ["frame_classify", "grade_studio", "frame_strip"]  # 关掉「节律曲线」
    dc["cloud_subtitle"] = False
    dc["membership_entry"] = False
    dc["visible_packages"] = ["vip_3y", "vip_1m"]                # 隐藏「年度」
    pw = p["channels"]["wn_douyin_a"]["paywall"]
    pw["splash"]["countdown_seconds"] = 4
    pw["trial"]["countdown_seconds"] = 9
    pw["vip"]["packages"][0]["price"] = 199.0
    code, r = save(p, d)
    check("后台保存成功", code == 200 and r.get("ok"), r.get("modes_after_save") or r)

    pv = preview("wn_douyin_a")
    c = pv["config"]
    n, w = c["normal"], c.get("paywall") or {}
    check("展示栏目: 片仓 Tab 消失", "片仓" not in n["tabs"], n["tabs"])
    check("展示栏目: 首页只剩精选频道", n["home"]["sections"] == ["精选频道"], n["home"]["sections"])
    check("展示栏目: 节律曲线卡片消失",
          [f["id"] for f in n["craft"]["features"]] == ["frame_classify", "grade_studio", "frame_strip"],
          [f["name"] for f in n["craft"]["features"]])
    check("展示栏目: 云字幕入口消失", "cloud" not in n["craft"])
    check("展示栏目: 会员中心入口消失", "membership" not in n["mine"])
    check("套餐: 年度被隐藏, 只剩 2 档",
          [x["id"] for x in w["vip"]["packages"]] == ["vip_3y", "vip_1m"],
          [x["name"] for x in w["vip"]["packages"]])
    check("价格: 3年改为 199", w["vip"]["packages"][0]["price"] == 199.0,
          w["vip"]["packages"][0]["price"])
    check("倒计时: 开屏 4s / 试看 9s",
          w["splash"]["countdown_seconds"] == 4 and w["trial"]["countdown_seconds"] == 9)
    check("其它渠道不受影响", preview("wn_gdt_b")["config"]["normal"]["tabs"] == st["tabs_all"],
          preview("wn_gdt_b")["config"]["normal"]["tabs"])

    # ---------------- 3. 隐藏套餐不可下单 / 价格按新值 ----------------
    code, r = post("/v1/order", {"channel": "wn_douyin_a", "package_id": "vip_1y"})
    check("隐藏套餐下单被拒", code == 400 and r.get("error") == "package_hidden_by_display", r)
    code, r = post("/v1/order", {"channel": "wn_douyin_a", "package_id": "vip_3y"})
    check("可见套餐按新价格 199 下单", code == 200 and r.get("amount") == 199.0, r.get("order_no"))
    order_no = r.get("order_no", "")
    code, raw = req("GET", "/order/" + order_no)
    check("自建订单页可访问", code == 200 and order_no in str(raw), "order_no=" + order_no)
    code, r = post("/v1/login", {"phone": "13800138000", "confirm_phone": "13800138000",
                                 "agreed": True, "channel": "wn_douyin_a"})
    check("手机号免验证码登录", code == 200 and r.get("ok"), r.get("phone_masked"))

    # ---------------- 4. 关掉会员页/收银台 -> 支付界面消失 ----------------
    d2 = copy.deepcopy(d)
    d2["channels"]["wn_douyin_a"]["paywall_modules"]["vip"] = False
    d2["channels"]["wn_douyin_a"]["paywall_modules"]["checkout"] = False
    code, r = save(p, d2)
    check("关闭支付模块保存成功", code == 200 and r.get("ok"))
    pv2 = preview("wn_douyin_a")
    w2 = pv2["config"].get("paywall") or {}
    check("下发配置不再包含会员页", "vip" not in w2)
    check("下发配置不再包含收银台", "checkout" not in w2)
    check("模块标记 vip=false / checkout=false",
          w2["modules"]["vip"] is False and w2["modules"]["checkout"] is False, w2["modules"])
    check("开屏与试看仍然保留", "splash" in w2 and "trial" in w2)
    demo = get("/demo/config?ch=wn_douyin_a")
    check("手机 Demo 拿到同一份配置", "vip" not in (demo["config"].get("paywall") or {}),
          demo["mode"])

    # ---------------- 5. 全局熔断 ----------------
    code, r = post("/admin/killswitch", {"token": TOKEN, "enabled": True})
    check("熔断开启", code == 200 and r.get("kill_switch") is True, r)
    modes = {ch: preview(ch)["mode"] for ch in st["channels_order"]}
    check("所有渠道回落 normal", all(m == "normal" for m in modes.values()), modes)
    check("熔断理由写入 decision", "global_kill_switch" in preview("wn_douyin_a")["decision"])
    code, r = post("/admin/killswitch", {"token": TOKEN, "enabled": False})
    check("熔断关闭后恢复", code == 200 and preview("wn_douyin_a")["mode"] == "paywall")

    # ---------------- 6. 写盘 + 重启持久化 ----------------
    with open(PROFILE, encoding="utf-8") as f:
        disk_p = json.load(f)
    check("皮肤文件已写盘且价格=199",
          disk_p["channels"]["wn_douyin_a"]["paywall"]["vip"]["packages"][0]["price"] == 199.0)
    check("皮肤 meta / evidence 未丢失",
          bool(disk_p.get("meta", {}).get("name")) and bool(disk_p.get("evidence")))
    check("运营态文件已生成", os.path.exists(STATE))
    with open(STATE, encoding="utf-8") as f:
        disk_d = json.load(f)
    check("运营态写盘: vip 模块为 false",
          disk_d["channels"]["wn_douyin_a"]["paywall_modules"]["vip"] is False)

    port2 = 8791
    env = dict(os.environ)
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "app.py"),
                             "--port", str(port2), "--host", "127.0.0.1", "--profile", PROFILE],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        old = BASE
        BASE = "http://127.0.0.1:%d" % port2
        ok = False
        for _ in range(12):
            try:
                rp = preview("wn_douyin_a")
                ok = ("片仓" not in rp["config"]["normal"]["tabs"] and
                      "vip" not in (rp["config"].get("paywall") or {}))
                break
            except Exception:
                time.sleep(0.4)
        check("重启新进程后配置仍生效(持久化)", ok)
        BASE = old
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except Exception:
            proc.kill()

    # ---------------- 7. 完整还原 ----------------
    code, r = save(orig_p, orig_d)
    check("还原初始配置", code == 200 and r.get("ok"))
    pv3 = preview("wn_douyin_a")
    c3 = pv3["config"]
    check("还原: 4 Tab / 4 玩法 / 3 套餐 / 5 模块全开 / 198 / 6s / 15s",
          len(c3["normal"]["tabs"]) == 4 and
          len(c3["normal"]["craft"]["features"]) == 4 and
          len(c3["paywall"]["vip"]["packages"]) == 3 and
          all(c3["paywall"]["modules"].values()) and
          c3["paywall"]["vip"]["packages"][0]["price"] == 198.0 and
          c3["paywall"]["splash"]["countdown_seconds"] == 6 and
          c3["paywall"]["trial"]["countdown_seconds"] == 15)
    aud = get("/admin/audit").get("audit", [])
    check("审计日志已记录", len(aud) >= 4,
          [a["action"] for a in aud[-6:]])

    # ---------------- 8. 闭环参数随后台保存变化 ----------------
    p4 = copy.deepcopy(orig_p)
    gt4 = p4["channels"]["wn_douyin_a"]["paywall"]["gate"]
    gt4["force_paywall_until_vip"] = False
    gt4["login_to_vip_delay_seconds"] = 3
    code, r = save(p4, orig_d)
    g4 = preview("wn_douyin_a")["config"]["paywall"]["gate"]
    check("后台关掉闭环后下发 force=false", g4["force_paywall_until_vip"] is False, g4)
    check("登录后延迟按后台值下发", g4["login_to_vip_delay_seconds"] == 3)
    code, r = save(orig_p, orig_d)
    g5 = preview("wn_douyin_a")["config"]["paywall"]["gate"]
    check("闭环参数已还原为常驻", g5["force_paywall_until_vip"] is True and
          g5["login_to_vip_delay_seconds"] == 1)

    passed = sum(1 for x in RESULTS if x["pass"])
    total = len(RESULTS)
    out = {"base": "http://127.0.0.1:8790", "passed": passed, "total": total,
           "all_pass": passed == total, "results": RESULTS,
           "lan_ip": st["lan_ip"], "port": st["port"],
           "profile": PROFILE, "state_file": STATE,
           "generated_at": int(time.time())}
    os.makedirs(os.path.dirname(EVIDENCE), exist_ok=True)
    with open(EVIDENCE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("=" * 78)
    print("ADMIN SELFTEST %s  %d/%d" % ("PASS" if passed == total else "FAIL", passed, total))
    print("saved: " + EVIDENCE)
    print("=" * 78)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
