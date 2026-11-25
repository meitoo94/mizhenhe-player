# -*- coding: utf-8 -*-
"""百度推广 API 归因链路端到端自检（连接已启动的 8790 服务）。
链路: 百度回传 click_id/IP/设备号 → 计划ID映射渠道号 bbdd1 →
      App 首启按 IP/设备号 匹配并绑定 → /v1/config 按该渠道展示配置下发。
用法: python server/test_baidu_attribution.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("TEST_BASE", "http://127.0.0.1:8790")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE = os.path.join(ROOT, "evidence")
TOKEN = "admin-local"
RESULTS = []


def check(name, ok, detail=None):
    RESULTS.append({"name": name, "pass": bool(ok), "detail": detail})
    flag = "PASS" if ok else "FAIL"
    tail = "" if detail is None else "  | " + json.dumps(detail, ensure_ascii=False)
    print(f"  {flag}  {name}{tail}")


def req(path, body=None):
    url = BASE + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    r = urllib.request.Request(url, data=data,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=6) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def get(path):
    return req(path)[1]


def post(path, body):
    return req(path, body)[1]


def admin_post(path, body):
    body = dict(body)
    body["token"] = TOKEN
    return post(path, body)


def main():
    # 0. 干净起点
    d = admin_post("/admin/attribution/clear", {})
    check("清空归因测试数据", d.get("ok") is True, d)
    check("恢复默认归因参数", admin_post("/admin/attribution/config", {
        "window_seconds": 86400, "min_confidence": 0.6,
        "ip_only_confidence": 0.65, "allow_ip_only": True,
        "strict_mode": False, "channel_map": {"baidu_plan_a": "bbdd1"}
    }).get("ok") is True)

    st = get("/admin/attribution")
    check("渠道表已包含 bbdd1", "bbdd1" in st["channels_order"], st["channels_order"])
    check("百度计划ID → bbdd1 映射生效",
          st["config"]["channel_map"].get("baidu_plan_a") == "bbdd1",
          st["config"]["channel_map"])

    # 1. 百度推广 API 回传
    click = {
        "click_id": "BAIDU-TEST-0001", "plan_id": "baidu_plan_a",
        "ip": "203.0.113.77", "device_id": "DEVICE-BAIDU-001",
        "ua": "iPhone", "model": "iPhone", "tz": "Asia/Shanghai",
        "lang": "zh-CN", "screen": "1170x2532@3",
    }
    d = post("/v1/attribution/baidu/callback", click)
    check("回传一条点击且映射到 bbdd1",
          d.get("ok") is True and d.get("saved") == 1 and
          d["clicks"][0]["channel"] == "bbdd1", d.get("clicks"))
    check("设备号只以哈希落盘（不存明文）",
          "DEVICE-BAIDU-001" not in json.dumps(d, ensure_ascii=False) and
          len(d["clicks"][0]["device_hash"]) == 24,
          d["clicks"][0]["device_hash"])

    d2 = post("/v1/attribution/baidu/callback", click)
    check("同一 click_id 重复回传幂等去重",
          d2.get("duplicates") == 1 and d2.get("total") == 1, d2)

    d3 = post("/v1/attribution/baidu/callback", {
        "click_id": "BAIDU-TEST-0002", "plan_id": "unknown_plan", "ip": "203.0.113.88"})
    check("未映射的计划ID 不产生渠道号",
          d3.get("saved") == 0 and d3["rejected"][0]["reason"] == "no_channel_mapping",
          d3.get("rejected"))

    # 2. 首次启动归因
    same = {"ip": "203.0.113.77", "device_id": "DEVICE-BAIDU-001",
            "idfv": "IDFV-TEST-001", "country": "cn",
            "model": "iPhone", "tz": "Asia/Shanghai", "lang": "zh-CN",
            "screen": "1170x2532@3"}
    r1 = post("/v1/attribution/resolve", same)
    check("同 IP + 同设备号 → bbdd1 / device_exact",
          r1.get("channel") == "bbdd1" and r1.get("method") == "device_exact" and
          r1.get("bound") is True, r1)

    st = get("/admin/attribution")
    check("命中后写入设备绑定表", st["total_bindings"] >= 1,
          {"total_bindings": st["total_bindings"],
           "bindings": st["bindings"][:1]})
    key = st["bindings"][-1]["device_key"]

    r2 = post("/v1/attribution/resolve", {
        "ip": "203.0.113.77", "device_id": "DEVICE-NAT-OTHER",
        "idfv": "IDFV-NAT-OTHER", "country": "cn"})
    check("同 IP 不同设备号 → 纯 IP 仍命中 bbdd1（后台知道该 IP 来自百度）",
          r2.get("channel") == "bbdd1" and
          r2.get("method") == "ip_from_partner_api", r2)

    r3 = post("/v1/attribution/resolve", {
        "ip": "198.51.100.9", "device_id": "DEVICE-NATURAL",
        "idfv": "IDFV-NATURAL", "country": "cn"})
    check("无关 IP / 设备号 → organic（自然量不被误判）",
          r3.get("channel") == "organic" and r3.get("bound") is False, r3)

    # 3. 严格模式：NAT 防护
    admin_post("/admin/attribution/config", {"strict_mode": True})
    r4 = post("/v1/attribution/resolve", {
        "ip": "203.0.113.77", "device_id": "DEVICE-NAT-OTHER2",
        "idfv": "IDFV-NAT-OTHER2", "country": "cn"})
    check("严格模式下纯 IP 不再判渠道",
          r4.get("channel") == "organic", r4)
    admin_post("/admin/attribution/config", {"strict_mode": False})

    # 4. /v1/config 自动解析渠道 + 按该渠道展示
    cfg_env = post("/v1/config", {"idfv": "IDFV-TEST-001",
                                  "device_id": "DEVICE-BAIDU-001",
                                  "ip": "203.0.113.77", "country": "cn"})
    cfg = json.loads(cfg_env["config"])
    check("/v1/config 不传 channel 时按绑定解析为 bbdd1 / paywall",
          cfg["channel"] == "bbdd1" and cfg["mode"] == "paywall",
          {"channel": cfg["channel"], "mode": cfg["mode"],
           "attribution": cfg_env.get("attribution")})
    check("下发的不是剪贴板方案，而是服务端设备绑定",
          "binding" in str(cfg_env.get("attribution", {}).get("method", "")),
          cfg_env.get("attribution"))

    other = get("/admin/preview?ch=wn_douyin_a")["config"]
    bb = cfg
    check("bbdd1 按自己的展示配置下发，与 wn_douyin_a 不同",
          len(bb["paywall"]["vip"]["packages"]) == 2 and
          len(other["paywall"]["vip"]["packages"]) == 3 and
          bb["paywall"]["trial"]["countdown_seconds"] == 20 and
          other["paywall"]["trial"]["countdown_seconds"] == 15 and
          bb["paywall"]["splash"]["countdown_seconds"] == 5 and
          len(bb["normal"]["home"]["sections"]) == 2,
          {"bbdd1": {"packages": len(bb["paywall"]["vip"]["packages"]),
                     "trial": bb["paywall"]["trial"]["countdown_seconds"],
                     "splash": bb["paywall"]["splash"]["countdown_seconds"],
                     "sections": len(bb["normal"]["home"]["sections"]),
                     "features": len(bb["normal"]["craft"]["features"])},
           "wn_douyin_a": {"packages": len(other["paywall"]["vip"]["packages"]),
                           "trial": other["paywall"]["trial"]["countdown_seconds"],
                           "sections": len(other["normal"]["home"]["sections"])}})
    check("配置仍带 HMAC 签名", len(cfg_env.get("sig", "")) == 64,
          len(cfg_env.get("sig", "")))

    # 5. 剪贴板不再是渠道判定依据
    post("/v1/click", {"channel": "wn_douyin_a", "ua": "iPhone", "source": "lp"})
    admin_post("/admin/attribution/clear", {})
    old = post("/v1/attribution", {
        "ip": "203.0.113.77", "idfv": "IDFV-NATURAL-2",
        "clipboard_token": "CHTOK1#CLKFAKE|wn_douyin_a|%d#deadbeef" % int(time.time())})
    check("旧入口带剪贴板 token 也不再决定渠道号",
          old.get("channel") == "organic" and
          "clipboard_token_ignored" in old.get("detail", []), old)

    # 6. 解绑 / 清空
    post("/v1/attribution/baidu/callback", click)
    r5 = post("/v1/attribution/resolve", same)
    st = get("/admin/attribution")
    key = st["bindings"][-1]["device_key"]
    d = admin_post("/admin/attribution/unbind", {"device_key": key})
    check("后台可手动解绑设备", d.get("ok") is True, d)
    check("解绑后绑定数减少",
          get("/admin/attribution")["total_bindings"] < st["total_bindings"],
          {"before": st["total_bindings"],
           "after": get("/admin/attribution")["total_bindings"]})

    # 7. 状态持久化
    disk = os.path.join(ROOT, "server", "state", "attribution.json")
    check("归因状态已原子写盘", os.path.exists(disk), disk)
    if os.path.exists(disk):
        with open(disk, encoding="utf-8") as f:
            persisted = json.load(f)
        check("写盘内容含 点击库 / 绑定表 / 参数",
              all(k in persisted for k in ("clicks", "bindings", "config")),
              list(persisted.keys()))

    # 8. 留一条演示数据，方便真机验收（不预先绑定）
    admin_post("/admin/attribution/clear", {})
    demo_click = {
        "click_id": "BAIDU-DEMO-" + str(int(time.time())),
        "plan_id": "baidu_plan_a", "channel": "bbdd1",
        "ip": get("/admin/attribution")["lan_ip"],
        "device_id": "DEMO-BAIDU-001", "ua": "iPhone", "model": "iPhone",
        "tz": "Asia/Shanghai", "lang": "zh-CN", "screen": "1170x2532@3",
    }
    post("/v1/attribution/baidu/callback", demo_click)
    final = get("/admin/attribution")
    check("保留一条待匹配点击供真机验证",
          final["total_clicks"] == 1 and final["clicks"][0]["channel"] == "bbdd1" and
          final["total_bindings"] == 0,
          {"clicks": final["total_clicks"], "bindings": final["total_bindings"],
           "ip": final["clicks"][0]["ip"], "device": "DEMO-BAIDU-001"})

    passed = sum(1 for x in RESULTS if x["pass"])
    total = len(RESULTS)
    os.makedirs(EVIDENCE, exist_ok=True)
    dest = os.path.join(EVIDENCE, "baidu_attribution_test.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump({"base": BASE, "passed": passed, "total": total,
                   "results": RESULTS}, f, ensure_ascii=False, indent=2)
    print("=" * 78)
    print(f"BAIDU ATTRIBUTION SELFTEST {'PASS' if passed == total else 'FAIL'}  {passed}/{total}")
    print("saved:", dest)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
