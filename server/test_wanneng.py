# -*- coding: utf-8 -*-
"""万能云播放器换皮 profile 的端到端实测。

启动:  python server/app.py --port 8789 --profile server/profiles/wanneng_rebrand.json
运行:  python server/test_wanneng.py
输出:  evidence/wanneng_test.json + 控制台 PASS/FAIL 汇总
"""
import hashlib
import hmac
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# 当前部署是 8790(--host 0.0.0.0)。8789 是早期遗留的旧服务(只有 3 个渠道、无百度归因),
# 早期默认连它导致品牌/渠道比对拿到旧数据; 需要测别的服务用 TEST_BASE 覆盖。
BASE = os.environ.get("TEST_BASE", "http://127.0.0.1:8790")
SECRET = os.environ.get("SWITCH_SECRET", "demo-secret-change-in-production").encode()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evidence", "wanneng_test.json")

steps, checks = [], []


def record(name, path, request, response, ok=None, note=""):
    steps.append({"name": name, "path": path, "request": request,
                  "response": response, "ok": ok, "note": note})


def check(name, cond, detail=""):
    checks.append({"name": name, "pass": bool(cond), "detail": detail})
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  | " + str(detail) if detail else ""))
    return bool(cond)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def get(path, raw=False):
    with urllib.request.urlopen(BASE + path, timeout=8) as r:
        b = r.read()
        return r.status, b.decode("utf-8") if raw else json.loads(b.decode("utf-8"))


IDFV = "AAAAAAAA-1111-2222-3333-444444444444"
NATURAL = "BBBBBBBB-1111-2222-3333-444444444444"


def cfg_request(channel, idfv=IDFV, **kw):
    body = {"idfv": idfv, "channel": channel, "country": "cn",
            "build": "1.2.0", "install_ts": int(time.time()) - 72 * 3600}
    body.update(kw)
    return body


def parse_envelope(env):
    return json.loads(env["config"])


def signature_ok(env):
    mac = hmac.new(SECRET, env["config"].encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, env["sig"])


def rollout_hit_idfv(channel, need=3000):
    for i in range(20000):
        v = "ROLLOUT-%05d-0000-0000-0000-000000000000" % i
        b = int(hashlib.md5((v + "|" + channel).encode()).hexdigest()[:8], 16) % 10000
        if b < need:
            return v, b
    raise RuntimeError("no bucket found")


def main():
    print("=" * 78)
    print("WAN NENG REBRAND PROFILE - END TO END TEST   base=" + BASE)
    print("=" * 78)

    # 1. 皮肤加载
    s, d = get("/health")
    record("健康检查", "/health", {}, d, s == 200)
    check("服务可用", s == 200 and d.get("ok"), d)

    s, d = get("/admin/channels")
    profile, normal = d["profile"], d["normal"]
    record("皮肤加载", "/admin/channels", {}, {"profile": profile, "normal": normal})
    check("profile = mizhenhe_wn_v1", profile["name"] == "mizhenhe_wn_v1", profile["name"])
    check("合规态四 Tab 已换皮", normal["tabs"] == ["今映", "片仓", "造片", "账户"], normal["tabs"])
    maps = [f["maps_to"] for f in normal["craft"]["features"]]
    check("四大玩法功能逐项照抄",
          maps == ["视界识档", "光谱调色台", "动能曲线", "帧廊胶片"], maps)
    check("云字幕链路保留", "72 小时" in normal["craft"]["cloud"]["desc"],
          normal["craft"]["cloud"]["desc"])
    check("会员占位文案保留", "Apple 会员内购正在开发中" in normal["mine"]["membership"]["placeholder"])
    check("主题色已替换", normal["theme"]["primary"] == "#35E0C8", normal["theme"]["primary"])

    # 2. 落地页 / 跳转
    s, html = get("/lp?ch=wn_douyin_a", raw=True)
    record("落地页", "/lp?ch=wn_douyin_a", {}, {"bytes": len(html)})
    check("落地页注入渠道号", "wn_douyin_a" in html)
    check("落地页占位全部替换",
          all(x not in html for x in ("__SERVER__", "__CHANNEL__", "__APPSTORE__")))
    # 上架前：落地页与短链都指向自有下载页 /download（不再跳别人的 App Store）
    check("落地页指向自有下载页", "/download?ch=wn_douyin_a" in html)

    req = urllib.request.Request(BASE + "/go?ch=wn_douyin_a", method="GET")
    # 不自动跟随 302, 直接读 Location
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    op = urllib.request.build_opener(NoRedirect)
    try:
        r = op.open(req, timeout=8)
        loc, code = r.headers.get("Location"), r.status
    except urllib.error.HTTPError as e:
        loc, code = e.headers.get("Location"), e.code
    record("跳转下载页", "/go?ch=wn_douyin_a", {}, {"status": code, "location": loc})
    check("/go 302 到自有下载页", code == 302 and "/download?ch=wn_douyin_a" in (loc or ""), loc)

    # 上架后：只要往 release_state.json 填 appstore_url，全站自动切回 App Store。
    # 这里临时写入再还原，验证切换逻辑且不留下改动。
    rs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "release_state.json")
    origin = ""
    try:
        with open(rs_path, encoding="utf-8") as f:
            origin = f.read()
        obj = json.loads(origin)
        obj["appstore_url"] = "https://apps.apple.com/cn/app/id0000000000"
        tmp = rs_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, rs_path)
        req2 = urllib.request.Request(BASE + "/go?ch=wn_douyin_a", method="GET")
        try:
            r2 = op.open(req2, timeout=8)
            loc2, code2 = r2.headers.get("Location"), r2.status
        except urllib.error.HTTPError as e:
            loc2, code2 = e.headers.get("Location"), e.code
        check("填入 App Store 链接后 /go 自动切商店",
              code2 == 302 and "id0000000000" in (loc2 or ""), loc2)
    finally:
        if origin:
            with open(rs_path, "w", encoding="utf-8") as f:
                f.write(origin)

    # 3. 归因
    click_body = {"channel": "wn_douyin_a", "model": "iPhone17,3",
                  "tz": "Asia/Shanghai", "lang": "zh-CN",
                  "screen": "393x852@3", "source": "lp"}
    s, click = post("/v1/click", click_body)
    record("广告点击打点", "/v1/click", click_body, click)
    token = click["clipboard_token"]
    check("返回剪贴板签名凭据", token.startswith("CHTOK1#"))

    # 主链路已改为百度推广 API 回传: 后台按「计划ID → 我方渠道号 bbdd1」建点击库,
    # 首启只用出口 IP / 设备号匹配; 剪贴板 token 即使收到也只记日志, 不参与命中。
    bd_click = {"click_id": "SELFTEST-BAIDU-%d" % int(time.time() * 1000),
                "plan_id": "baidu_plan_a", "ip": "203.0.113.201",
                "device_id": "SELFTEST-BD-001", "model": "iPhone17,3"}
    s, cb = post("/v1/attribution/baidu/callback", bd_click)
    record("百度推广 API 回传", "/v1/attribution/baidu/callback", bd_click, cb)
    attr_body = {"clipboard_token": token, "idfv": IDFV, "ip": "203.0.113.201",
                 "device_id": "SELFTEST-BD-001",
                 "model": "iPhone17,3", "tz": "Asia/Shanghai",
                 "lang": "zh-CN", "screen": "393x852@3"}
    s, attr = post("/v1/attribution", attr_body)
    record("首启归因-百度渠道用户", "/v1/attribution",
           {"idfv": IDFV, "ip": "203.0.113.201"}, attr)
    check("百度回传命中 bbdd1 + 剪贴板不再参与归因",
          attr["channel"] == "bbdd1"
          and "clipboard_token_ignored" in attr["detail"],
          (attr["channel"], attr["method"]))

    s, nat = post("/v1/attribution", {"idfv": NATURAL, "model": "iPhone14,2",
                                      "tz": "Asia/Tokyo", "lang": "ja-JP",
                                      "screen": "390x844@3"})
    record("首启归因-自然量", "/v1/attribution", {"idfv": NATURAL}, nat)
    check("自然量回落 organic", nat["channel"] == "organic", nat["detail"])

    # 4. 配置两态 + 三段参数
    s, env_n = post("/v1/config", cfg_request("organic"))
    pn = parse_envelope(env_n)
    record("配置-自然量", "/v1/config", cfg_request("organic"),
           {"mode": pn["mode"], "decision": env_n.get("decision")})
    check("自然量 = normal", pn["mode"] == "normal")
    check("自然量配置带新皮肤", pn["normal"]["brand"] == "米帧盒播放器")
    check("配置 HMAC 验签一致", signature_ok(env_n))

    s, env_a = post("/v1/config", cfg_request("wn_douyin_a"))
    pa = parse_envelope(env_a)
    record("配置-渠道A", "/v1/config", cfg_request("wn_douyin_a"),
           {"mode": pa["mode"], "paywall": pa.get("paywall")})
    check("渠道 A = paywall", pa["mode"] == "paywall", env_a.get("decision"))
    check("渠道 A 配置验签一致", signature_ok(env_a))

    pw = pa["paywall"]
    sp, tr, vip, ck, lg, gt = (pw["splash"], pw["trial"], pw["vip"],
                               pw["checkout"], pw["login"], pw["gate"])
    check("开屏 6s + 进入",
          sp["countdown_seconds"] == 6 and sp["button"] == "进入", sp)
    check("试看 15s + 固定小字",
          tr["countdown_seconds"] == 15 and "开通 VIP 解锁全部功能" in tr["hint"], tr["hint"])
    check("试看结束弹窗双按钮",
          tr["end_title"] == "试看结束" and tr["end_primary"] == "开通会员"
          and tr["end_secondary"] == "稍后再说", (tr["end_title"], tr["end_secondary"]))
    prices = [p["price"] for p in vip["packages"]]
    check("三档价格 198 / 158 / 98", prices == [198.0, 158.0, 98.0], prices)
    check("划线原价与权益文案",
          [p["origin_price"] for p in vip["packages"]] == [598.0, 398.0, 128.0]
          and "一键字幕 100 次" in vip["packages"][0]["perk"])
    check("支付按钮 = 支付宝支付", vip["button"] == "支付宝支付")
    check("支付通道 = 自建订单页 + 支付宝 H5",
          ck["method"] == "alipay_h5" and "mclient.alipay.com" in ck["cashier_base"], ck["method"])
    _port = BASE.rsplit(":", 1)[-1]
    check("订单端点按请求 Host 动态回填",
          (":" + _port) in ck["order_endpoint"]
          and (":" + _port) in ck["order_page_base"],
          ck["order_endpoint"])
    check("手机号免验证码登录", lg["method"] == "phone_no_sms"
          and lg["confirm_phone_input"] is True, lg["method"])
    check("付费墙闭环: 退出静默回片段 / 登录1s / 只认支付解锁",
          gt.get("force_paywall_until_vip") is True
          and gt.get("once_per_install") is False
          and gt.get("home_after_skip") is False
          and gt.get("skip_action") == "back_to_trial"
          and float(gt.get("login_to_vip_delay_seconds", 0)) == 1.0
          and "blocked_toast" not in gt, gt)

    # 5. 守卫
    s, env_g1 = post("/v1/config", cfg_request("wn_gdt_b", install_ts=int(time.time()) - 2 * 3600))
    pg1 = parse_envelope(env_g1)
    record("守卫-安装2小时", "/v1/config", {"age": "2h"},
           {"mode": pg1["mode"], "decision": env_g1.get("decision")})
    check("安装时长守卫拦截", pg1["mode"] == "normal", env_g1.get("decision"))

    s, env_g2 = post("/v1/config", cfg_request("wn_gdt_b", idfv=NATURAL))
    pg2 = parse_envelope(env_g2)
    record("守卫-灰度外", "/v1/config", {"idfv": NATURAL},
           {"mode": pg2["mode"], "decision": env_g2.get("decision")})
    check("灰度外回落 normal", pg2["mode"] == "normal", env_g2.get("decision"))

    hit_idfv, bucket = rollout_hit_idfv("wn_gdt_b")
    s, env_g3 = post("/v1/config", cfg_request("wn_gdt_b", idfv=hit_idfv))
    pg3 = parse_envelope(env_g3)
    record("守卫-灰度命中", "/v1/config", {"idfv": hit_idfv, "bucket": bucket},
           {"mode": pg3["mode"], "decision": env_g3.get("decision")})
    check("灰度命中 = paywall", pg3["mode"] == "paywall", env_g3.get("decision"))

    # 6. 下单 → 自建订单页 → 收银台
    s, order = post("/v1/order", {"channel": "wn_douyin_a", "package_id": "vip_3y",
                                  "idfv": IDFV, "amount": 198.0})
    record("下单", "/v1/order", {"package_id": "vip_3y"}, order)
    check("订单金额 ¥198 且套餐名正确",
          order["amount"] == 198.0 and order["package_name"] == "3年", order["package_name"])
    check("返回自建订单页 URL", "/order/" in order["order_page_url"], order["order_page_url"])
    check("收银台指向支付宝 H5", "mclient.alipay.com" in order["cashier_url"])

    page_path = urllib.parse.urlparse(order["order_page_url"]).path
    s, page = get(page_path, raw=True)
    record("自建订单页", page_path, {}, {"bytes": len(page)})
    check("订单页含订单编号 / 金额 / 重新支付 / 售后联系",
          all(x in page for x in (order["order_no"], "198.00", "交易进行中",
                                  "重新支付", "售后联系")))
    check("订单页用新皮肤主题色", "#35E0C8" in page and "米帧盒播放器" in page.upper() or "米帧盒播放器" in page)

    # 7. 登录
    s, login = post("/v1/login", {"phone": "13800138000", "confirm_phone": "13800138000",
                                  "agreed": True, "idfv": IDFV, "channel": "wn_douyin_a"})
    record("手机号登录", "/v1/login", {"phone": "138****8000"}, login)
    check("免验证码登录成功并脱敏", login.get("ok") and login.get("phone_masked") == "138****8000",
          login.get("phone_masked"))
    try:
        post("/v1/login", {"phone": "13800138000", "confirm_phone": "13900139000",
                           "agreed": True})
        mismatch_ok = False
    except urllib.error.HTTPError as e:
        mismatch_ok = e.code == 400
    check("两次手机号不一致被拒", mismatch_ok)

    # 8. 支付回调 → 会员状态
    s, paid = post("/v1/pay/notify", {"order_no": order["order_no"], "idfv": IDFV})
    record("支付回调", "/v1/pay/notify", {"order_no": order["order_no"]}, paid)
    check("订单状态 paid", paid.get("order", {}).get("status") == "paid")
    s, vipstat = post("/v1/vip/status", {"idfv": IDFV})
    s, vipstat0 = post("/v1/vip/status", {"idfv": NATURAL})
    record("会员状态", "/v1/vip/status", {"idfv": IDFV},
           {"paid": vipstat, "unpaid": vipstat0})
    check("付费设备 is_vip=true / 未付费 false",
          vipstat["is_vip"] is True and vipstat0["is_vip"] is False)

    s, logs = get("/admin/logs")
    record("判定日志", "/admin/logs", {}, {"counts": {k: len(v) for k, v in logs.items()}})
    check("登录日志落盘", len(logs.get("logins", [])) >= 1)

    passed = sum(1 for c in checks if c["pass"])
    total = len(checks)
    result = {"base": BASE, "ts": int(time.time()), "passed": passed, "total": total,
              "all_pass": passed == total, "checks": checks, "steps": steps,
              "profile": "server/profiles/wanneng_rebrand.json"}
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2))
    print("-" * 78)
    print(("WAN NENG REBRAND SELFTEST PASS %d/%d" % (passed, total)) if passed == total
          else ("WAN NENG REBRAND SELFTEST FAIL %d/%d" % (passed, total)))
    print("saved: " + OUT)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
