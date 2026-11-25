# -*- coding: utf-8 -*-
"""端到端自检: 模拟自然量 / 投放渠道 / 灰度渠道 / 下单支付, 把真实响应落盘。"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("SWITCH_BASE", "http://127.0.0.1:8787")
SECRET = os.environ.get("SWITCH_SECRET", "demo-secret-change-in-production").encode()
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(PROJ, "evidence", "selftest_output.json")


def call(path, body=None, method=None):
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json",
                                          "X-Forwarded-For": "203.0.113.42"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    out = {"base": BASE, "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": []}

    def step(name, path, body=None):
        d = call(path, body)
        out["steps"].append({"name": name, "request": {"path": path, "body": body}, "response": d})
        print(f"[{name}] {path}")
        print(json.dumps(d, ensure_ascii=False)[:900])
        if path == "/v1/config" and "decision" in d:
            print("  >>> mode =", json.loads(d["config"])["mode"], "| decision =", d["decision"])
        print("-" * 78)
        return d

    step("健康检查", "/health")

    click = step("广告点击打点", "/v1/click", {
        "channel": "douyin_1125_a",
        "ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15",
        "model": "iPhone", "tz": "Asia/Shanghai", "lang": "zh-CN",
        "screen": "393x852@3", "ref": "https://ad.example.com/feed"
    })

    attrib = step("首启归因-剪贴板凭据", "/v1/attribution", {
        "clipboard_token": click["clipboard_token"],
        "idfv": "AAAAAAAA-1111-2222-3333-444444444444",
        "model": "iPhone17,3", "os": "18.0", "tz": "Asia/Shanghai",
        "lang": "zh-CN", "screen": "393x852@3", "country": "cn"
    })

    step("首启归因-无凭据(自然量, 特征不足)", "/v1/attribution", {
        "idfv": "BBBBBBBB-1111-2222-3333-444444444444",
        "model": "iPhone17,3", "tz": "Asia/Shanghai", "country": "cn"
    })

    step("首启归因-剪贴板丢失(指纹兜底命中)", "/v1/attribution", {
        "idfv": "DDDDDDDD-1111-2222-3333-444444444444",
        "model": "iPhone", "os": "18.0", "tz": "Asia/Shanghai",
        "lang": "zh-CN", "screen": "393x852@3", "country": "cn"
    })

    def rollout_hit(channel, ratio=0.30):
        for i in range(2000):
            candidate = f"GRAY-{i:04d}-1111-2222-3333-444444444444"
            b = int(hashlib.md5((candidate + "|" + channel).encode()).hexdigest()[:8], 16) % 10000
            if b < ratio * 10000:
                return candidate, b
        return None, None

    hit_idfv, bucket = rollout_hit("gdt_1125_b")
    print(f"[灰度命中样本] idfv={hit_idfv} bucket={bucket} (需 <3000)")

    ctx = {"idfv": "AAAAAAAA-1111-2222-3333-444444444444",
           "build": "1.2.0", "country": "cn", "install_ts": int(time.time()) - 7200}

    c_organic = step("配置-自然量(organic)", "/v1/config", dict(ctx, channel="organic"))
    c_pay = step("配置-投放渠道A", "/v1/config", dict(ctx, channel="douyin_1125_a"))
    step("配置-灰度渠道B(安装2h <24h)", "/v1/config",
         dict(ctx, channel="gdt_1125_b", install_ts=int(time.time()) - 7200))
    step("配置-灰度渠道B(安装48h但灰度外)", "/v1/config",
         dict(ctx, channel="gdt_1125_b", install_ts=int(time.time()) - 48 * 3600,
              idfv="CCCCCCCC-1111-2222-3333-444444444444"))
    step("配置-灰度渠道B(安装48h+灰度命中)", "/v1/config",
         dict(ctx, channel="gdt_1125_b", install_ts=int(time.time()) - 48 * 3600,
              idfv=hit_idfv))
    step("配置-不存在的渠道", "/v1/config", dict(ctx, channel="unknown_ch"))
    step("配置-地区守卫(us)", "/v1/config",
         dict(ctx, channel="douyin_1125_a", country="us"))

    # 验签: 服务端对 config 字符串原文做 HMAC-SHA256
    sig = hmac.new(SECRET, c_pay["config"].encode(), hashlib.sha256).hexdigest()
    out["signature_check"] = {"expected": sig, "returned": c_pay["sig"], "match": sig == c_pay["sig"]}
    print("[验签]", out["signature_check"])

    cfg = json.loads(c_pay["config"])
    out["paywall_summary"] = {
        "mode": cfg["mode"],
        "splash_countdown": cfg["paywall"]["splash"]["countdown_seconds"],
        "trial_countdown": cfg["paywall"]["trial"]["countdown_seconds"],
        "packages": [(p["name"], p["price"]) for p in cfg["paywall"]["vip"]["packages"]],
        "checkout": cfg["paywall"]["checkout"]["method"],
    }
    print("[付费墙配置摘要]", json.dumps(out["paywall_summary"], ensure_ascii=False))

    order = step("创建VIP订单", "/v1/order", {
        "channel": "douyin_1125_a", "package_id": "vip_3y",
        "idfv": "AAAAAAAA-1111-2222-3333-444444444444"
    })
    step("支付回调", "/v1/pay/notify", {
        "order_no": order["order_no"], "idfv": "AAAAAAAA-1111-2222-3333-444444444444"
    })
    step("查询会员状态", "/v1/vip/status", {"idfv": "AAAAAAAA-1111-2222-3333-444444444444"})
    step("查询未付费用户", "/v1/vip/status", {"idfv": "BBBBBBBB-1111-2222-3333-444444444444"})

    out["attribution_channel"] = attrib["channel"]
    out["organic_mode"] = json.loads(c_organic["config"])["mode"]
    out["paywall_mode"] = cfg["mode"]

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved:", OUT)
    ok = (out["signature_check"]["match"]
          and out["organic_mode"] == "normal"
          and out["paywall_mode"] == "paywall"
          and out["attribution_channel"] == "douyin_1125_a")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
