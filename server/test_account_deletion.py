#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号注销（App Store 5.1.1(v)）+ 纯中文定名 端到端自检。

覆盖：身份校验 / 二次确认 / 真正删除登录·会员·设备绑定 / 流水去标识化 /
      注销台账无明文 / 三处 App 内入口 / Swift 端 / 全工程品牌一致性。
输出：evidence/account_deletion_test.json
"""
import io
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("TEST_BASE", "http://127.0.0.1:8790")
EVIDENCE = os.path.join(ROOT, "evidence")
IDFV = "DEMO-DEVICE"
PHONE = "13800138000"
CHECKS = []


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=6) as r:
        return json.load(r)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def check(name, ok, detail=None):
    CHECKS.append({"name": name, "pass": bool(ok), "detail": detail})
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  | " + str(detail)) if detail else ""))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def main():
    print("BASE =", BASE)
    h = get("/health")
    check("健康检查带账号注销计数", h.get("ok") and "account_deletions" in h, h)
    led = get("/admin/account/deletions")
    check("后台注销台账接口可读", led.get("ok") and isinstance(led.get("deletions"), list),
          led.get("total"))
    before = led.get("total", 0)

    s, r = post("/v1/account/delete", {})
    check("匿名注销被拒（identity_required）",
          s == 400 and r.get("error") == "identity_required", (s, r))

    s, r = post("/v1/account/delete", {"ticket": "FAKE", "confirm": "yes"})
    check("未二次确认被拒（confirmation_required）",
          s == 400 and r.get("error") == "confirmation_required", (s, r))

    s, r = post("/v1/account/delete", {"ticket": "FAKE", "confirm": "DELETE"})
    check("伪造票据被拒（login_required）",
          s == 403 and r.get("error") == "login_required", (s, r))

    # ---- 造一个真实账号：登录 + 订单 + 支付 + 设备归因绑定 ----
    # 每轮用唯一的 click_id / 设备号 / IP，保证 resolve 一定新建绑定，
    # 不会命中上轮残留的绑定，也不破坏真机演示用的那条 bbdd1 点击。
    uniq = str(int(time.time() * 1000))
    dev, ip = "SELFTEST-DEL-" + uniq, "203.0.113." + str(100 + int(uniq[-3:]) % 100)
    s, login = post("/v1/login", {"phone": PHONE, "confirm_phone": PHONE,
                                  "agreed": True, "idfv": IDFV, "channel": "bbdd1"})
    check("手机号登录拿到票据", s == 200 and bool(login.get("ticket")),
          login.get("phone_masked"))
    ticket = login.get("ticket", "")

    s, order = post("/v1/order", {"channel": "bbdd1", "package_id": "vip_3y", "idfv": IDFV})
    check("下单成功", s == 200 and bool(order.get("order_no")), order.get("order_no"))
    s, pay = post("/v1/pay/notify", {"order_no": order.get("order_no"), "idfv": IDFV})
    check("支付回调开通会员", s == 200 and bool(pay.get("ok")), pay.get("ok"))
    s, vs = post("/v1/vip/status", {"idfv": IDFV})
    check("注销前会员状态为 true", vs.get("is_vip") is True, vs)

    cid = "SELFTEST-DEL-" + uniq
    s, cb = post("/v1/attribution/baidu/callback",
                 {"click_id": cid, "plan_id": "baidu_plan_a", "channel": "bbdd1",
                  "ip": ip, "device_id": dev,
                  "model": "iPhone", "tz": "Asia/Shanghai"})
    check("百度回传成功", s == 200 and cb.get("saved") == 1, cb.get("saved"))
    s, at = post("/v1/attribution/resolve",
                 {"ip": ip, "device_id": dev,
                  "idfv": IDFV, "model": "iPhone", "tz": "Asia/Shanghai"})
    check("设备绑定建立", s == 200 and at.get("channel") == "bbdd1", at.get("channel"))

    # ---- 真正注销 ----
    s, res = post("/v1/account/delete",
                  {"ticket": ticket, "idfv": IDFV, "confirm": "DELETE", "channel": "bbdd1"})
    items = res.get("deleted_items", {})
    check("注销成功并返回回执", s == 200 and res.get("ok") and bool(res.get("receipt")),
          res.get("receipt"))
    check("删除登录记录", items.get("logins", 0) >= 1, items)
    check("删除会员权益", items.get("vip", 0) >= 1, items)
    check("删除设备归因绑定", items.get("device_bindings", 0) >= 1, items)
    check("交易流水去标识化", items.get("orders_deidentified", 0) >= 1, items)

    s, vs = post("/v1/vip/status", {"idfv": IDFV})
    check("注销后会员状态为 false", vs.get("is_vip") is False, vs)

    logs = get("/admin/logs")
    check("登录记录里已无该账号",
          not any(x.get("ticket") == ticket for x in logs.get("logins", [])),
          len(logs.get("logins", [])))
    check("订单保留但已去标识化",
          any(x.get("personal_data_purged") for x in logs.get("orders", [])),
          len(logs.get("orders", [])))

    s, dup = post("/v1/account/delete",
                  {"ticket": ticket, "idfv": IDFV, "confirm": "DELETE"})
    check("重复注销被拒", s == 403 and dup.get("error") == "login_required", (s, dup))

    led = get("/admin/account/deletions")
    rows = led.get("deletions", [])
    last = rows[-1] if rows else {}
    blob = json.dumps(last, ensure_ascii=False)
    check("台账新增一条且带回执 / 哈希",
          led.get("total", 0) >= before + 1 and bool(last.get("receipt"))
          and bool(last.get("phone_hash")), last.get("receipt"))
    check("台账不含手机号明文", PHONE not in blob and "138****" not in blob, None)
    check("台账写明依法保留项", "retained" in last, last.get("retained"))

    # ---- 三处 App 内入口 ----
    demo = read("demo/index.html")
    check("Demo 会员页 / 登录页 / 账户 Tab 三处注销入口",
          demo.count('data-act="deleteAccount"') >= 3,
          demo.count('data-act="deleteAccount"'))
    check("Demo 走服务端注销接口", "/v1/account/delete" in demo, None)
    check("Demo 注销浮层独立于付费墙（不受强制闭环拦截）",
          "acctDelLayer" in demo and "openAccountDeletion" in demo, None)
    check("Demo 需要输入「注销」二次确认", "word !== \"注销\"" in demo, None)
    check("Demo 注销后清登录态与会员但不解锁付费墙",
          "logged = false; vip = false; order = null;" in demo, None)
    check("单机版与 Demo 一致", read("米帧盒播放器-单机版演示.html") == demo, None)

    admin = read("admin/index.html")
    check("后台有注销台账面板", "⑧ 账号注销台账" in admin and "/admin/account/deletions" in admin, None)

    swift = read("ios/AccountDeletion.swift")
    flow = read("ios/PaywallFlow.swift")
    home = read("ios/NormalHomeView.swift")
    check("Swift 注销服务调用服务端接口", "/v1/account/delete" in swift, None)
    check("Swift 登录成功后保存票据", "AccountDeletion.saveLogin(" in flow, None)
    check("Swift 会员页 / 登录页有注销入口",
          flow.count('Button("账号与隐私 · 注销账号")') >= 2,
          flow.count('Button("账号与隐私 · 注销账号")'))
    check("Swift 合规态账户 Tab 有注销入口",
          'Text("注销账号")' in home and "AccountDeletionView(" in home, None)

    # ---- 纯中文定名 ----
    prof = json.loads(read("server/profiles/wanneng_rebrand.json"))
    check("商店名与首页品牌都是米帧盒播放器",
          prof["meta"]["display_name"] == "米帧盒播放器"
          and prof["normal"]["brand"] == "米帧盒播放器",
          prof["meta"]["display_name"])
    check("内部皮肤标识同步", prof["meta"]["name"] == "mizhenhe_wn_v1",
          prof["meta"]["name"])
    cfg = post("/v1/config", {"channel": "organic", "country": "cn",
                              "idfv": "BRANDTEST", "ip": "127.0.0.1"})[1]
    payload = json.loads(cfg["config"]) if isinstance(cfg["config"], str) else cfg["config"]
    check("服务端下发新品牌", payload["normal"]["brand"] == "米帧盒播放器",
          payload["normal"]["brand"])
    offenders = []
    for rel in ["server/profiles/wanneng_rebrand.json", "demo/index.html",
                "admin/index.html", "server/app.py", "ios/NormalHomeView.swift",
                "真机测试与渠道展示操作手册.html"]:
        if "FRAMEBOX" in read(rel) or "LUMIGRID" in read(rel):
            offenders.append(rel)
    check("全工程无英文品牌残留", not offenders, offenders)

    passed = sum(1 for c in CHECKS if c["pass"])
    out = {"base": BASE, "ts": int(time.time()), "total": len(CHECKS),
           "passed": passed, "all_pass": passed == len(CHECKS),
           "checks": CHECKS}
    os.makedirs(EVIDENCE, exist_ok=True)
    with open(os.path.join(EVIDENCE, "account_deletion_test.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n%d/%d PASS -> evidence/account_deletion_test.json" % (passed, len(CHECKS)))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
