# -*- coding: utf-8 -*-
"""皮肤配置实测: 用 --profile 加载新声皮肤后, 验证商店链接/落地页/归因/配置/价格/验签。"""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("SWITCH_BASE", "http://127.0.0.1:8788")
SECRET = os.environ.get("SWITCH_SECRET", "demo-secret-change-in-production").encode()
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(PROJ, "evidence", "profile_xinsheng_test.json")
PROFILE = os.path.join(PROJ, "server", "profiles", "xinsheng.json")


def call(path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method="POST" if data else "GET",
        headers={"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.42"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.status, dict(r.headers), json.loads(r.read().decode("utf-8"))


def get_raw(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.status, dict(r.headers), r.read().decode("utf-8", errors="ignore")


def main():
    out = {"base": BASE, "profile": PROFILE,
           "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"), "checks": [], "steps": []}

    def check(name, ok, detail=""):
        out["checks"].append({"name": name, "pass": bool(ok), "detail": str(detail)})
        print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail) if detail else ""))

    def step(name, path, body=None):
        s, h, d = call(path, body)
        out["steps"].append({"name": name, "path": path, "body": body, "status": s, "response": d})
        print(f"  [{name}] {path} -> {s}")
        return d

    print("=== 1. profile 加载结果 ===")
    s, h, raw = get_raw("/admin/channels")
    d = json.loads(raw)
    ch = d.get("channels", d)
    names = list(ch.keys()) if isinstance(ch, dict) else []
    check("渠道表来自新声皮肤", set(["organic", "xs_douyin_a", "xs_gdt_b"]).issubset(set(names)), names)
    out["admin_channels"] = d

    print("=== 2. 落地页与商店跳转 ===")
    s, h, html = get_raw("/lp?ch=xs_douyin_a")
    check("落地页三个占位符全部替换",
          all(x not in html for x in ["__SERVER__", "__CHANNEL__", "__APPSTORE__"]))
    check("落地页注入新声渠道号", "xs_douyin_a" in html)
    check("落地页注入新声商店页", "id6789638090" in html)
    out["landing"] = {"status": s, "bytes": len(html)}

    try:
        req = urllib.request.Request(BASE + "/go?ch=xs_douyin_a")
        urllib.request.urlopen(req, timeout=8)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        check("/go 302 指向新声商店页", "id6789638090" in loc, loc)
        out["go_location"] = loc

    print("=== 3. 归因 + 配置 + 价格 + 验签 ===")
    click = step("广告点击打点", "/v1/click", {
        "channel": "xs_douyin_a",
        "ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15",
        "model": "iPhone", "tz": "Asia/Shanghai", "lang": "zh-CN",
        "screen": "393x852@3", "ref": "https://ad.example.com/feed"})
    attrib = step("首启归因", "/v1/attribution", {
        "clipboard_token": click["clipboard_token"],
        "idfv": "XSAAAAAA-1111-2222-3333-444444444444",
        "model": "iPhone17,3", "os": "18.0", "tz": "Asia/Shanghai",
        "lang": "zh-CN", "screen": "393x852@3", "country": "cn"})
    check("归因命中新声渠道", attrib.get("channel") == "xs_douyin_a", attrib.get("channel"))

    ctx = {"idfv": "XSAAAAAA-1111-2222-3333-444444444444",
           "build": "1.1.0", "country": "cn", "install_ts": int(time.time()) - 7200}
    c_pay = step("配置-渠道A", "/v1/config", dict(ctx, channel="xs_douyin_a"))
    cfg_pay = json.loads(c_pay["config"])
    prices = [p["price"] for p in cfg_pay["paywall"]["vip"]["packages"]]
    check("渠道A返回 paywall", cfg_pay["mode"] == "paywall", c_pay.get("decision"))
    check("价格与会员协议一致(198/168/98)", prices == [198.0, 168.0, 98.0], prices)
    check("合规首页 Tab 来自新声皮肤", cfg_pay["normal"]["tabs"] == ["光廊", "声库", "创坊", "我的"],
          cfg_pay["normal"]["tabs"])
    check("收银台 support_url 指向 kindsoft.vip",
          "kindsoft.vip" in cfg_pay["paywall"]["checkout"]["support_url"])
    check("配置签名一致",
          hmac.new(SECRET, c_pay["config"].encode("utf-8"), hashlib.sha256).hexdigest() == c_pay["sig"])

    c_org = step("配置-自然量", "/v1/config", dict(ctx, channel="organic"))
    cfg_org = json.loads(c_org["config"])
    check("自然量回落 normal", cfg_org["mode"] == "normal", c_org.get("decision"))
    check("normal 配置不含付费墙", "paywall" not in cfg_org)

    c_b = step("配置-渠道B安装2h", "/v1/config",
               dict(ctx, channel="xs_gdt_b", install_ts=int(time.time()) - 7200))
    check("渠道B守卫按皮肤参数拦截(24h)",
          json.loads(c_b["config"])["mode"] == "normal", c_b.get("decision"))

    order = step("下单三年会员", "/v1/order", {
        "idfv": ctx["idfv"], "channel": "xs_douyin_a",
        "package_id": "vip_3y", "amount": 198.00})
    check("订单金额 198", float(order.get("amount", 0)) == 198.0, order.get("order_no"))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    n_pass = sum(1 for c in out["checks"] if c["pass"])
    print(f"\nPROFILE TEST: {n_pass}/{len(out['checks'])} passed -> {OUT}")
    return 0 if n_pass == len(out["checks"]) else 1


if __name__ == "__main__":
    sys_exit = main()
    raise SystemExit(sys_exit)
