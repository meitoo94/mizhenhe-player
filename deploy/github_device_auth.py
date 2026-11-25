#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""米帧盒播放器 · GitHub 设备授权（OAuth Device Flow，无需打开 PAT 设置页）。

背景：github.com 网页慢/打不开时，Settings → Developer settings → PAT 页面
很难操作；但 https://github.com/login/device/code 是轻量 JSON 端点，
配合已登录浏览器打开 https://github.com/login/device 输入 8 位码即可授权，
全程不需要进 PAT 设置页、不需要手机流量、不需要把 token 贴到聊天里。

用法：
    python deploy/github_device_auth.py --start     # 申请设备码，打印 8 位 user_code
    # 在已登录的浏览器打开 https://github.com/login/device 输入该码并点授权
    python deploy/github_device_auth.py --poll      # 轮询并保存 token（不回显）
    python deploy/github_device_auth.py --wait     # 上面两步合起来，阻塞等待授权
    python deploy/github_device_auth.py --status   # 只看 token 是否已落地

token 落盘位置：deploy/signing/secrets/GITHUB_TOKEN.txt（该目录已被 .gitignore 排除），
github_bootstrap.py 在没有 GITHUB_TOKEN 环境变量时会自动读取它。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEC_DIR = ROOT / "deploy" / "signing" / "secrets"
TOKEN_FILE = SEC_DIR / "GITHUB_TOKEN.txt"
STATE_FILE = SEC_DIR / ".device_state.json"

# GitHub CLI 的公开 OAuth App client_id，已启用 Device Flow，无需自行注册 App
CLIENT_ID = "178c6fc778ccc68e1d6a"
SCOPES = "repo workflow"
DEVICE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
VERIFY_URL = "https://github.com/login/device"

OPENER = None


def build_opener():
    """github.com 在本机走环境代理更稳；保留直连兜底。"""
    candidates = (
        ("环境代理", urllib.request.build_opener()),
        ("直连", urllib.request.build_opener(urllib.request.ProxyHandler({}))),
    )
    for name, op in candidates:
        try:
            req = urllib.request.Request(
                "https://api.github.com/rate_limit",
                headers={"User-Agent": "mizhenhe-device-auth"})
            with op.open(req, timeout=10) as r:
                if r.status == 200:
                    return name, op
        except Exception:
            continue
    return None, None


def post(url, params):
    """走 curl 发 POST：本机实测 curl 对 github.com 的 POST 稳定 200，
    urllib 在同一链路上会被网关回 5xx（UA/Connection 差异），故统一用 curl。"""
    body = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    r = subprocess.run(
        ["curl", "-sS", "-X", "POST", "-H", "Accept: application/json",
         "-d", body, "--connect-timeout", "20", "--max-time", "90", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return {"error": "network", "message": (r.stderr or "")[:200]}
    try:
        return json.loads(r.stdout or "{}")
    except Exception:
        return {"error": "http_error", "message": (r.stdout or "")[:200]}


def start():
    r = post(DEVICE_URL, {"client_id": CLIENT_ID, "scope": SCOPES})
    if "device_code" not in r:
        print("[FAIL] 申请设备码失败:", r)
        return 1
    SEC_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "device_code": r["device_code"],
        "interval": int(r.get("interval", 5)),
        "expires_in": int(r.get("expires_in", 900)),
        "created": int(time.time()),
    }), encoding="utf-8")
    print("== GitHub 设备授权 ==")
    print("  授权页   :", VERIFY_URL)
    print("  8 位码   :", r.get("user_code"))
    print("  有效期   :", r.get("expires_in"), "秒")
    print("  申请范围 :", SCOPES)
    print("\n在已登录 GitHub 的浏览器打开授权页，填入 8 位码并点 Authorize。")
    print("随后执行: python deploy/github_device_auth.py --poll")
    return 0


def poll(deadline_s=900):
    if not STATE_FILE.is_file():
        print("[FAIL] 没有待授权的设备码，先跑 --start")
        return 1
    st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    interval = max(5, st.get("interval", 5))
    hard_deadline = st.get("created", int(time.time())) + st.get("expires_in", 900)
    begun = time.time()
    while True:
        if time.time() > hard_deadline or time.time() - begun > deadline_s:
            print("[FAIL] 设备码已过期，重新执行 --start")
            return 1
        r = post(TOKEN_URL, {
            "client_id": CLIENT_ID,
            "device_code": st["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        if "access_token" in r:
            SEC_DIR.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(r["access_token"].strip() + "\n", encoding="utf-8")
            STATE_FILE.unlink(missing_ok=True)
            print("[OK] 授权成功，token 已保存到", TOKEN_FILE.relative_to(ROOT))
            print("     类型   :", r.get("token_type", "bearer"))
            print("     范围   :", r.get("scope", SCOPES))
            print("     值不回显。接下来直接跑: python deploy/github_bootstrap.py")
            return 0
        err = r.get("error", "")
        if err in ("network", "http_error"):
            print("  github.com 响应慢/波动，重试 ...", flush=True)
            time.sleep(interval)
            continue
        if err == "authorization_pending":
            print("  等待授权中 ...", flush=True)
            time.sleep(interval)
            continue
        if err == "slow_down":
            interval += 5
            time.sleep(interval)
            continue
        if err == "expired_token":
            print("[FAIL] 设备码已过期，重新执行 --start")
            return 1
        if err == "access_denied":
            print("[FAIL] 授权被拒绝")
            return 1
        print("[FAIL] 轮询失败:", {k: v for k, v in r.items() if k != "access_token"})
        return 1


def auto(then_bootstrap=False):
    """守护模式：github.com 网页偶发 5xx 时反复申请设备码并轮询，直到授权完成。

    每次重新申请都会打印新的 8 位码，以最后一次为准；拿到 token 后可选直接
    调用 github_bootstrap.py，把建仓 / 写 Secrets / 传码 / 触发 CI 一次跑完。
    """
    print("== 守护模式 ==")
    print("  在已登录 GitHub 的浏览器打开", VERIFY_URL)
    print("  输入下面【最新一次】打印的 8 位码并点授权，之后全自动。", flush=True)
    while True:
        r = post(DEVICE_URL, {"client_id": CLIENT_ID, "scope": SCOPES})
        if "device_code" not in r:
            print("  github.com 暂不可用（%s），20 秒后重试 ..." % r.get("error", ""), flush=True)
            time.sleep(20)
            continue
        SEC_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({
            "device_code": r["device_code"],
            "interval": int(r.get("interval", 5)),
            "expires_in": int(r.get("expires_in", 900)),
            "created": int(time.time()),
        }), encoding="utf-8")
        print("\n  授权页 :", VERIFY_URL)
        print("  8 位码 :", r.get("user_code"),
              "（以此为准，%s 秒有效）" % r.get("expires_in"), flush=True)
        if poll() == 0:
            break
        print("  设备码过期或服务波动，重新申请 ...", flush=True)
        time.sleep(2)
    if then_bootstrap:
        print("\n== token 到手，自动执行 bootstrap ==", flush=True)
        return subprocess.call(
            [sys.executable, str(ROOT / "deploy" / "github_bootstrap.py")], cwd=str(ROOT))
    return 0


def status():
    if not TOKEN_FILE.is_file():
        print("token 未落地；先跑 --start 并在浏览器授权，再跑 --poll")
        return 1
    raw = TOKEN_FILE.read_text(encoding="utf-8").strip()
    print("token 已落地:", TOKEN_FILE.relative_to(ROOT), "| 长度", len(raw), "| 前缀", raw[:4] + "***")
    print("验证权限: python deploy/github_bootstrap.py --probe")
    return 0


def main():
    global OPENER
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--poll", action="store_true")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--auto", action="store_true",
                    help="守护模式：github.com 恢复后自动申请设备码并等待授权")
    ap.add_argument("--then-bootstrap", action="store_true",
                    help="--auto 拿到 token 后自动跑完建仓/传码/出包")
    args = ap.parse_args()

    if args.status:
        return status()

    mode, OPENER = build_opener()
    if OPENER is None:
        print("[FAIL] api.github.com 不可达，检查网络或代理后重试")
        return 1
    print("网络通道: %s（github.com 与 api.github.com 共用）" % mode, flush=True)

    if args.auto or args.then_bootstrap:
        return auto(args.then_bootstrap)
    if args.poll:
        return poll()
    rc = start()
    if rc != 0 or not args.wait:
        return rc
    print("\n开始轮询授权结果（最多等 15 分钟）...", flush=True)
    return poll()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断；设备码仍在，稍后跑 --poll 即可")
        sys.exit(130)
