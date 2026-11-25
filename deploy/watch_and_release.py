#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等云端 Ad Hoc 出包 → 下载 ipa → 部署 OTA。

多 runner 矩阵下，整个 run 的 conclusion 可能因某个池排队失败而是 failure，
但只要有一个 job 跑完就会留下 ipa artifact，所以这里按 artifact 判定成功。
若所有 job 都因 runner 容量被取消，自动 rerun（最多 2 次）。

用法：
    python deploy/watch_and_release.py                # 等包 + 下载 + 部署
    python deploy/watch_and_release.py --no-deploy    # 只下载 ipa，不上服务器
    python deploy/watch_and_release.py --minutes 90   # 自定义等待上限
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOKEN = (ROOT / "deploy" / "signing" / "secrets" / "GITHUB_TOKEN.txt").read_text(
    encoding="utf-8").strip()
OWNER, REPO = "meitoo94", "mizhenhe-player"
API = "https://api.github.com"
BASE = "/repos/%s/%s" % (OWNER, REPO)
WF_RUNS = BASE + "/actions/workflows/ios-adhoc.yml/runs"


def api(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": "Bearer " + TOKEN,
        "Accept": "application/vnd.github+json",
        "User-Agent": "mizhenhe-watch",
    }
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=40) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"message": str(e)[:160]}


def head_sha():
    st, ref = api("GET", BASE + "/git/ref/heads/main")
    return ref.get("object", {}).get("sha", "") if st == 200 else ""


def find_artifacts(sha):
    st, d = api("GET", WF_RUNS + "?per_page=10")
    runs = [r for r in d.get("workflow_runs", []) if not sha or r.get("head_sha") == sha]
    found = []
    for r in runs:
        st2, arts = api("GET", BASE + "/actions/runs/%s/artifacts" % r["id"])
        for a in arts.get("artifacts", []):
            if "AdHoc" in a.get("name", "") and not a.get("expired"):
                found.append((r, a))
    return runs, found


def diagnose(run):
    st, jobs = api("GET", BASE + "/actions/runs/%s/jobs" % run["id"])
    for j in jobs.get("jobs", []):
        if j.get("conclusion") == "success":
            continue
        st2, ann = api("GET", BASE + "/check-runs/%s/annotations" % j["id"])
        msgs = [x.get("message", "") for x in ann] if isinstance(ann, list) else []
        uniq = []
        for m in msgs:
            if m and m not in uniq:
                uniq.append(m)
        print("  job %-18s %-12s %s" % (j.get("name"), j.get("conclusion"),
                                        " | ".join(uniq)[:300]), flush=True)


def finish(deploy):
    print("[OK] 检测到 ipa artifact，开始下载", flush=True)
    r = subprocess.run([sys.executable, "deploy/github_bootstrap.py", "--artifact"],
                       cwd=str(ROOT), capture_output=True, text=True)
    print(r.stdout, r.stderr, flush=True)
    if r.returncode != 0:
        print("[FAIL] ipa 下载失败", flush=True)
        return 3
    ipas = sorted((ROOT / "download" / "ipa").glob("*.ipa"),
                  key=lambda p: p.stat().st_mtime)
    if not ipas:
        print("[FAIL] 本地没找到 ipa", flush=True)
        return 4
    ipa = ipas[-1]
    print("[OK] ipa:", ipa, ipa.stat().st_size, "字节", flush=True)
    if not deploy:
        print("[DONE] 按要求未部署上线", flush=True)
        return 0
    r2 = subprocess.run([sys.executable, "deploy/release_ipa.py", str(ipa), "--deploy"],
                        cwd=str(ROOT), capture_output=True, text=True)
    print(r2.stdout, r2.stderr, flush=True)
    if r2.returncode != 0:
        print("[FAIL] OTA 部署失败 rc=%s" % r2.returncode, flush=True)
        return r2.returncode
    print("[DONE] OTA 已部署，手机打开 https://app.kqi.ink/mzh/download 扫码安装", flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--no-deploy", action="store_true")
    args = ap.parse_args()

    sha = head_sha()
    print("目标提交:", sha[:12] or "(取不到，按全部 run 处理)", flush=True)
    deadline = time.time() + args.minutes * 60
    reruns = 0
    last_state = ""
    while time.time() < deadline:
        runs, found = find_artifacts(sha)
        if found:
            return finish(not args.no_deploy)
        if runs:
            x = runs[0]
            state = "#%s %s %s" % (x.get("run_number"), x.get("status"),
                                   x.get("conclusion") or "")
            if state != last_state:
                print("[%s] %s" % (time.strftime("%H:%M:%S"), state), flush=True)
                last_state = state
            if x.get("status") == "completed" and reruns < 2:
                print("[WARN] 本轮全部完成但没有 ipa，诊断并自动重跑", flush=True)
                diagnose(x)
                st, r = api("POST", BASE + "/actions/runs/%s/rerun" % x["id"], {})
                reruns += 1
                print("  rerun HTTP=%s（第 %d/2 次）" % (st, reruns), flush=True)
                time.sleep(30)
                continue
        time.sleep(20)
    print("[TIMEOUT] %d 分钟内没有产出 ipa artifact" % args.minutes, flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
