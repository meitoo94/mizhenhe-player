#!/usr/bin/env python3
"""部署后真实 HTTP 验证：子路径前缀、配置签名、下单地址、后台鉴权。

  python deploy/verify_public.py --base https://app.kqi.ink/mzh \
      --user mzhadmin --pass '密码' --out evidence/public_deploy_verify.json
本地反代模拟：
  python deploy/verify_public.py --base http://127.0.0.1:8794/mzh \
      --user mzhadmin --pass test123 --out evidence/prefix_proxy_verify.json
"""
import argparse
import base64
import json
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
results = []


def check(name, ok, detail):
    results.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return bool(ok)


def req(base, path, method="GET", body=None, auth=None, timeout=10):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    if auth:
        r.add_header("Authorization", "Basic " + base64.b64encode(
            f"{auth[0]}:{auth[1]}".encode()).decode())
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(r, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)
    except Exception as e:
        return 0, repr(e), {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://app.kqi.ink/mzh")
    ap.add_argument("--user", default="mzhadmin")
    ap.add_argument("--pass", dest="passwd", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--expected-public", default="",
                    help="后端应生成的公网基址；本地反代模拟时默认 https://app.kqi.ink/mzh")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    expected = args.expected_public.rstrip("/") or (
        "https://app.kqi.ink/mzh" if ("127.0.0.1" in base or "localhost" in base) else base)
    auth = (args.user, args.passwd) if args.passwd else None
    print(f"验证目标: {base}\n期望公网基址: {expected}\n")

    code, txt, _ = req(base, "/health")
    check("1. /health 可达", code == 200 and '"ok": true' in txt, f"HTTP {code}")

    code, demo, _ = req(base, "/demo")
    check("2. /demo 页面返回", code == 200 and "米帧盒播放器" in demo, f"HTTP {code} / {len(demo)} bytes")
    check("3. 页面注入子路径前缀", 'window.MZH_BASE="/mzh"' in demo,
          "window.MZH_BASE=\"/mzh\"" if 'window.MZH_BASE="/mzh"' in demo else "前缀未注入")
    check("4. 页面请求走 API() 带前缀", "API(\"/demo/config?ch=" in demo,
          "fetch(API(...)) 已就位")
    check("5. 后台链接由 API() 生成带前缀",
          'API("/admin")' in demo and "__MZH_BASE__" not in demo,
          "静态页无残留占位符，在线链接由 API() 拼 /mzh")

    code, txt, _ = req(base, "/demo/config?ch=bbdd1")
    try:
        cfg = json.loads(txt).get("config", {})
        mode = cfg.get("mode")
    except Exception:
        mode = "parse_error"
    check("6. /demo/config 下发配置", code == 200 and mode in ("normal", "paywall"),
          f"HTTP {code} / mode={mode}")

    code, txt, hdr = req(base, "/v1/config", "POST",
                         {"idfv": "DEPLOY-CHECK", "channel": "bbdd1", "country": "cn"})
    endpoint = ""
    try:
        env = json.loads(txt)
        cfg = json.loads(env["config"])
        endpoint = cfg.get("paywall", {}).get("checkout", {}).get("order_endpoint", "")
        sig = env.get("sig", "")
    except Exception:
        sig = ""
    check("7. /v1/config 带签名", code == 200 and len(sig) > 20, f"HTTP {code} / sig={len(sig)} chars")
    check("8. 下单地址带 /mzh 前缀", endpoint == expected + "/v1/order",
          endpoint or "未下发 paywall.checkout.order_endpoint")

    pkg = "vip_3y"
    code, txt, _ = req(base, "/v1/order", "POST",
                       {"channel": "bbdd1", "package_id": pkg, "idfv": "DEPLOY-CHECK"})
    order_no, page_url = "", ""
    try:
        o = json.loads(txt)
        order_no = o.get("order_no", "")
        page_url = o.get("order_page_url", "")
    except Exception:
        pass
    if not order_no and code == 400:
        # 套餐 ID 可能随皮肤变化，取配置里的第一个
        code2, txt2, _ = req(base, "/demo/config?ch=bbdd1")
        try:
            packages = json.loads(json.loads(txt2)["config"])["paywall"]["vip"]["packages"]
            pkg = packages[0]["id"]
            code, txt, _ = req(base, "/v1/order", "POST",
                               {"channel": "bbdd1", "package_id": pkg, "idfv": "DEPLOY-CHECK"})
            o = json.loads(txt)
            order_no, page_url = o.get("order_no", ""), o.get("order_page_url", "")
        except Exception:
            pass
    check("9. /v1/order 下单", code == 200 and bool(order_no), f"HTTP {code} / {order_no}")
    check("10. 订单页地址带 /mzh 前缀", page_url.startswith(expected + "/order/"), page_url)
    if order_no:
        code, page, _ = req(base, "/order/" + order_no)
        check("11. 订单页可打开", code == 200 and "米帧盒" in page, f"HTTP {code}")

    code, lp, _ = req(base, "/lp?ch=bbdd1")
    import re
    server_line = re.search(r'const SERVER\s*=\s*"([^"]+)"', lp)
    server_val = server_line.group(1) if server_line else ""
    check("12. 落地页 SERVER 指向公网子路径",
          code == 200 and server_val == expected,
          f"HTTP {code} / SERVER={server_val or '未替换'}")

    code, _, _ = req(base, "/admin", auth=None)
    check("13. 未认证访问后台被拒", code == 401, f"HTTP {code}（应为 401）")
    code, state, _ = req(base, "/admin/state", auth=auth)
    token = ""
    try:
        token = json.loads(state).get("admin_token", "")
    except Exception:
        pass
    check("14. 带认证的 /admin/state 可用", code == 200 and '"ok": true' in state,
          f"HTTP {code} / admin_token={'空(公网模式)' if not token else '本地模式'}")

    code, txt, _ = req(base, "/v1/attribution/resolve", "POST",
                       {"idfv": "DEPLOY-CHECK", "country": "cn"})
    check("15. 百度归因解析接口", code == 200 and '"channel"' in txt, f"HTTP {code}")

    passed = sum(1 for r in results if r["ok"])
    print(f"\n结果: {passed}/{len(results)} PASS")
    payload = {"ts": datetime.now(timezone.utc).isoformat(), "base": base,
               "passed": passed, "total": len(results), "results": results}
    out = Path(args.out) if args.out else ROOT / "evidence" / "prefix_proxy_verify.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"证据: {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
