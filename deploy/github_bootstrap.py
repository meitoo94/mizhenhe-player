#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""米帧盒播放器 · 一键建 GitHub 仓库并触发首次 Ad Hoc 出包（纯 API 通道）。

本机实测（2026-11-25）：github.com 网页慢且偶发 5xx，但 api.github.com 稳定可达。
本脚本 **不访问 github.com 网页表单、不用 git push**，所有操作走 REST API：
    认证 → 建公开仓库 → 加密写 6 个 Secrets → Git Data API 上传代码 →
    提交落到 main 自动触发 .github/workflows/ios-adhoc.yml → 云端 macos-26 编译。

前提：一个 GitHub token（repo + workflow）。优先用设备授权，不必进 PAT 设置页：
    python deploy/github_device_auth.py --start
    # 在已登录 GitHub 的浏览器打开 https://github.com/login/device 输入 8 位码并授权
    python deploy/github_device_auth.py --poll
  token 会保存到 deploy/signing/secrets/GITHUB_TOKEN.txt（已被 .gitignore 排除），
  本脚本自动读取；也可手动生成 classic PAT 后设置 GITHUB_TOKEN 环境变量。
  fine-grained PAT 需要 Contents / Secrets / Actions / Administration 的读写权限。

用法：
    python deploy/github_bootstrap.py               # 一键建仓 + 传码 + 触发 CI

    python deploy/github_bootstrap.py --check       # 本地前置体检，不联网
    python deploy/github_bootstrap.py --probe       # 只测 api.github.com 通道
    python deploy/github_bootstrap.py --status      # 查最近几次 CI 构建状态
    python deploy/github_bootstrap.py --artifact    # 下载最新 ipa 到 download/ipa/
    python deploy/github_bootstrap.py --repo NAME   # 指定仓库名
    python deploy/github_bootstrap.py --dispatch    # 额外手动触发一次 CI
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEC_DIR = ROOT / "deploy" / "signing" / "secrets"
WORKFLOW = ROOT / ".github" / "workflows" / "ios-adhoc.yml"
API = "https://api.github.com"

SECRET_KEYS = (
    "MZH_TEAM_ID",
    "MZH_BUNDLE_ID",
    "CERT_P12_BASE64",
    "CERT_PASSWORD",
    "PROFILE_BASE64",
    "PROFILE_UUID",
)

TEXT_EXT = {
    ".sh", ".yml", ".yaml", ".py", ".plist", ".xcconfig", ".swift", ".html",
    ".json", ".js", ".css", ".md", ".txt", ".conf", ".service", ".php",
    ".csr", ".pem", ".xml", ".properties",
}
TEXT_NAME = {"Makefile", "Dockerfile", ".gitignore", ".gitattributes"}

TOKEN = ""
OPENER = None


def clean(s: str) -> str:
    """任何输出前先脱敏，防止 token 出现在报错或日志里。"""
    s = str(s)
    return s.replace(TOKEN, "<redacted>") if TOKEN else s


def say(*parts) -> None:
    print(clean(" ".join(str(p) for p in parts)))


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "").strip()
    err = clean((r.stderr or "").strip())
    if check and r.returncode != 0:
        raise RuntimeError("git %s 失败: %s" % (" ".join(args), err))
    if r.returncode != 0 and err:
        out = (out + "\n" + err).strip()
    return out, r.returncode


def tracked_files():
    """-z + quotepath=false，避免中文文件名被转义成 \\347\\261\\263 导致读文件失败。"""
    r = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files", "-z"],
                       cwd=str(ROOT), capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("git ls-files 失败: " +
                           clean(r.stderr.decode("utf-8", "replace").strip()))
    return [p.decode("utf-8") for p in r.stdout.split(b"\x00") if p]


def build_opener():
    """优先直连 api.github.com（本机实测比环境代理快），失败再退回环境代理。"""
    candidates = (
        ("直连", urllib.request.build_opener(urllib.request.ProxyHandler({}))),
        ("环境代理", urllib.request.build_opener()),
    )
    for name, op in candidates:
        try:
            req = urllib.request.Request(API + "/rate_limit",
                                         headers={"User-Agent": "mizhenhe-bootstrap"})
            with op.open(req, timeout=8) as r:
                if r.status == 200:
                    return name, op
        except Exception:
            continue
    return None, None


def request(method, path, body=None, binary=False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "mizhenhe-bootstrap",
    }
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with OPENER.open(req, timeout=60) as r:
            raw = r.read()
            if binary:
                return r.status, raw, r.geturl()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        if binary:
            return e.code, raw, ""
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"message": clean(raw.decode("utf-8", "replace")[:400])}
    except urllib.error.URLError as e:
        raise RuntimeError("请求 api.github.com 失败：%s" % clean(e.reason))


def api(method, path, body=None):
    return request(method, path, body)


def sealed(value, public_key_b64, key_id):
    """GitHub Actions Secrets 用 libsodium sealed box 加密后再上传。"""
    from nacl.public import PublicKey, SealedBox
    pk = PublicKey(base64.b64decode(public_key_b64))
    enc = SealedBox(pk).encrypt(value.encode("utf-8"))
    return {"encrypted_value": base64.b64encode(enc).decode("ascii"), "key_id": key_id}


def is_text(path):
    p = Path(path)
    return p.suffix.lower() in TEXT_EXT or p.name in TEXT_NAME


def ensure_initial_commit(base):
    """空仓库的 /git/blobs 会回 409 Git Repository is empty。
    先用 Contents API 落一个 README 生成首个 commit，仓库才可用于 Git Data API。"""
    status, ref = api("GET", base + "/git/ref/heads/main")
    if status == 200:
        return ref["object"]["sha"]
    readme = "# 米帧盒播放器\n\niOS Ad Hoc 构建工程（代码由 GitHub REST API 上传）。\n"
    st, r = api("PUT", base + "/contents/README.md", {
        "message": "chore: 初始化仓库",
        "content": base64.b64encode(readme.encode("utf-8")).decode("ascii"),
    })
    if st not in (200, 201):
        raise RuntimeError("空仓库初始化失败: %s %s" % (st, r.get("message", "")))
    say("  空仓库已初始化（Contents API 首个提交）")
    status, ref = api("GET", base + "/git/ref/heads/main")
    if status != 200:
        # 少数情况下默认分支不叫 main，把 main 指过去并设为默认分支
        _, info = api("GET", base)
        db = info.get("default_branch", "main")
        st2, dref = api("GET", base + "/git/ref/heads/" + db)
        if st2 == 200:
            api("POST", base + "/git/refs",
                {"ref": "refs/heads/main", "sha": dref["object"]["sha"]})
            api("PATCH", base, {"default_branch": "main"})
            status, ref = api("GET", base + "/git/ref/heads/main")
    if status != 200:
        raise RuntimeError("初始化后仍找不到 main: %s" % ref.get("message", ""))
    return ref["object"]["sha"]


def push_via_api(base):
    """用 Git Data API 上传所有已跟踪文件，生成单个 commit，不碰 github.com。"""
    files = tracked_files()
    say("  %d 个文件待上传（全部来自 git ls-files，敏感文件已被 .gitignore 排除）" % len(files))

    parent = ensure_initial_commit(base)

    def one(path):
        data = (ROOT / path).read_bytes()
        if is_text(path):
            data = data.replace(b"\r\n", b"\n")  # macOS runner 上 CRLF 会让 bash 报错
        st, r = api("POST", base + "/git/blobs",
                    {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
        if st != 201 or "sha" not in r:
            raise RuntimeError("blob 上传失败 %s: %s %s" % (path, st, r.get("message", "")))
        mode = "100755" if path.endswith((".sh", ".py")) else "100644"
        return {"path": path, "mode": mode, "type": "blob", "sha": r["sha"]}

    with ThreadPoolExecutor(max_workers=6) as pool:
        tree_items = list(pool.map(one, files))

    st, tree = api("POST", base + "/git/trees", {"tree": tree_items})
    if st != 201:
        raise RuntimeError("建 tree 失败: %s %s" % (st, tree.get("message", "")))

    st, commit = api("POST", base + "/git/commits", {
        "message": "chore: 米帧盒播放器 Ad Hoc 出包基线（API 通道上传）",
        "tree": tree["sha"],
        "parents": [parent] if parent else [],
    })
    if st != 201:
        raise RuntimeError("建 commit 失败: %s %s" % (st, commit.get("message", "")))

    if parent:
        st, r = api("PATCH", base + "/git/refs/heads/main", {"sha": commit["sha"]})
    else:
        st, r = api("POST", base + "/git/refs",
                    {"ref": "refs/heads/main", "sha": commit["sha"]})
        api("PATCH", base, {"default_branch": "main"})
    if st not in (200, 201):
        raise RuntimeError("更新 main 失败: %s %s" % (st, r.get("message", "")))
    return len(files), commit["sha"][:8]


def run_status(base):
    st, r = api("GET", base + "/actions/workflows/ios-adhoc.yml/runs?per_page=5")
    if st != 200:
        say("查询失败:", st, r.get("message", ""))
        return 1
    runs = r.get("workflow_runs", [])
    if not runs:
        say("还没有构建记录（代码刚上传时 Actions 需要几秒识别 workflow）")
        return 0
    for x in runs:
        say("  #%-3s %-12s %-12s %s" % (x["run_number"], x["status"],
                                       x.get("conclusion") or "-", x["html_url"]))
    return 0


def fetch_artifact(base):
    st, r = api("GET", base + "/actions/runs?per_page=10")
    if st != 200:
        say("查询构建记录失败:", st, r.get("message", ""))
        return 1
    for run in r.get("workflow_runs", []):
        if run.get("conclusion") != "success":
            continue
        st2, arts = api("GET", base + "/actions/runs/%s/artifacts" % run["id"])
        for a in arts.get("artifacts", []):
            if a.get("expired") or "AdHoc" not in a.get("name", ""):
                continue
            try:
                st3, blob, final_url = request(
                    "GET", base + "/actions/artifacts/%s/zip" % a["id"], binary=True)
            except Exception as e:
                say("artifact 下载失败（可能是重定向域名不通）:", clean(e))
                return 1
            if st3 != 200:
                say("artifact 返回", st3)
                return 1
            z = zipfile.ZipFile(io.BytesIO(blob))
            names = [n for n in z.namelist() if n.lower().endswith(".ipa")]
            if not names:
                say("artifact 里没有 ipa:", z.namelist()[:5])
                return 1
            out_dir = ROOT / "download" / "ipa"
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / Path(names[0]).name
            out.write_bytes(z.read(names[0]))
            say("[OK] ipa 已下载:", out, "(%d 字节)" % out.stat().st_size)
            say("接着执行: python deploy/release_ipa.py --deploy")
            return 0
    say("还没有构建成功的 ipa artifact，先跑 --status 看进度")
    return 1


def precheck():
    print("== 本地前置体检 ==")
    ok = True

    for k in SECRET_KEYS:
        p = SEC_DIR / (k + ".txt")
        good = p.is_file() and p.stat().st_size > 0
        print("  %-20s %s" % (k, "OK" if good else "缺失"))
        ok &= good

    head, rc = git("rev-parse", "--verify", "HEAD", check=False)
    good = rc == 0 and bool(head)
    print("  %-20s %s" % ("git 首个提交", head[:8] if good else "缺失"))
    ok &= good

    _, rc = git("diff", "--quiet", check=False)
    good = rc == 0
    print("  %-20s %s" % ("无未提交改动", "OK" if good else "有改动，请先 commit"))
    ok &= good

    wf = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.is_file() else ""
    good = "runs-on: macos-26" in wf and "workflow_dispatch:" in wf
    print("  %-20s %s" % ("Ad Hoc workflow", "OK (macos-26)" if good else "异常"))
    ok &= good

    files = tracked_files()
    bad = [f for f in files if f.endswith((".key", ".p12", ".p8", ".mobileprovision", ".cer", ".ipa"))
           or "/secrets/" in f or "signing/in/" in f]
    print("  %-20s %s" % ("仓库无敏感材料", "OK" if not bad else "泄露风险: " + ", ".join(bad[:5])))
    ok &= not bad

    try:
        import nacl  # noqa: F401
        print("  %-20s OK" % "Secrets 加密库")
    except ImportError:
        print("  %-20s 缺失（pip install pynacl）" % "Secrets 加密库")
        ok = False

    print("\n结论：%s" % ("全部就绪，跑 deploy/github_device_auth.py 拿 token 后即可一键出包" if ok else "有未就绪项，先补齐再跑"))
    return 0 if ok else 1


def main():
    global TOKEN, OPENER
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只做本地前置体检，不联网")
    ap.add_argument("--probe", action="store_true", help="只测 api.github.com 通道")
    ap.add_argument("--status", action="store_true", help="查最近几次 CI 构建状态")
    ap.add_argument("--artifact", action="store_true", help="下载最新 ipa 到 download/ipa/")
    ap.add_argument("--repo", default="mizhenhe-player", help="仓库名（默认 mizhenhe-player）")
    ap.add_argument("--token", default="", help="PAT；不推荐，会留在命令行历史里")
    ap.add_argument("--dispatch", action="store_true", help="上传代码之外再手动触发一次 CI")
    args = ap.parse_args()

    if args.check:
        return precheck()

    mode, OPENER = build_opener()
    if OPENER is None:
        print("api.github.com 不可达，检查网络或代理后重跑 --probe（github.com 网页用不上）。")
        return 1
    print("== 网络通道 ==")
    print("  api.github.com 经%s可达（网页主站 github.com 用不上）" % mode)
    if args.probe:
        return 0

    token_file = ROOT / "deploy" / "signing" / "secrets" / "GITHUB_TOKEN.txt"
    TOKEN = (args.token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if not TOKEN and token_file.is_file():
        TOKEN = token_file.read_text(encoding="utf-8").strip()
    if not TOKEN:
        print("\n缺少 GitHub token。最省事的是设备授权（不进 PAT 设置页）：")
        print("  1) python deploy/github_device_auth.py --start")
        print("  2) 浏览器打开 https://github.com/login/device 输入 8 位码并授权")
        print("  3) python deploy/github_device_auth.py --poll")
        print("  也可手动生成 classic PAT（勾 repo + workflow）后设置 GITHUB_TOKEN。")
        return 2

    if args.status or args.artifact:
        status, me = api("GET", "/user")
        if status != 200:
            say("认证失败:", status, me.get("message", ""))
            return 1
        base = "/repos/%s/%s" % (me["login"], args.repo)
        return fetch_artifact(base) if args.artifact else run_status(base)

    rc = precheck()
    if rc != 0:
        return rc

    print("\n== 连接 GitHub ==")
    status, me = api("GET", "/user")
    if status != 200:
        say("认证失败:", status, me.get("message", ""))
        say("检查 PAT 是否过期、是否勾选 repo + workflow（fine-grained PAT 还需 Administration 权限）")
        return 1
    owner = me["login"]
    say("已认证:", owner)

    repo = args.repo
    status, data = api("POST", "/user/repos", {
        "name": repo,
        "description": "米帧盒播放器 · iOS 构建工程",
        "private": False,
        "has_issues": False,
        "has_wiki": False,
        "has_projects": False,
        "auto_init": False,
    })
    if status == 201:
        say("[1/5] 公开仓库已创建:", data["html_url"])
    elif status == 422:
        status2, data2 = api("GET", "/repos/%s/%s" % (owner, repo))
        if status2 != 200:
            say("同名仓库存在但读不到:", status2, data2.get("message", ""))
            return 1
        say("[1/5] 复用已有仓库:", data2["html_url"], "| private =", data2["private"])
    else:
        say("建仓库失败:", status, data.get("message", ""))
        return 1

    base = "/repos/%s/%s" % (owner, repo)

    print("\n== 写入 6 个 Actions Secrets ==")
    status, keyinfo = api("GET", base + "/actions/secrets/public-key")
    if status != 200:
        say("取仓库公钥失败:", status, keyinfo.get("message", ""))
        say("仓库刚建好可能还没启用 Actions，稍等一分钟重跑即可（脚本可重复执行）")
        return 1
    for k in SECRET_KEYS:
        value = (SEC_DIR / (k + ".txt")).read_text(encoding="utf-8").strip()
        status, r = api("PUT", base + "/actions/secrets/" + k,
                        sealed(value, keyinfo["key"], keyinfo["key_id"]))
        if status not in (201, 204):
            say("  %-20s 失败 %s %s" % (k, status, r.get("message", "")))
            return 1
        say("  %-20s OK (%d 字符)" % (k, len(value)))
    say("[2/5] 6 个 Secrets 已加密写入")

    print("\n== 用 API 上传代码（不访问 github.com、不用 git push） ==")
    try:
        n, sha = push_via_api(base)
    except Exception as e:
        say("上传失败:", clean(e))
        return 1
    say("[3/5] %d 个文件已落到 main（commit %s，纯 API 通道，单一提交）" % (n, sha))

    # remote 只作记录，不含 token，也不参与上传
    plain_url = "https://github.com/%s/%s.git" % (owner, repo)
    cur, rc = git("remote", "get-url", "origin", check=False)
    if rc != 0:
        git("remote", "add", "origin", plain_url)
    elif cur.strip() != plain_url:
        git("remote", "set-url", "origin", plain_url)
    say("[4/5] 本地 origin 记为", plain_url, "（仅记录，不含 token）")

    print("\n== 首次出包 ==")
    say("[5/5] 提交命中 .github/workflows/ios-adhoc.yml，云端 macos-26 将自动开始编译")
    if args.dispatch:
        status, r = api("POST", base + "/actions/workflows/ios-adhoc.yml/dispatches", {"ref": "main"})
        say("手动触发:", "OK" if status == 204 else "%s %s" % (status, r.get("message", "")))

    print("\n查询构建  : python deploy/github_bootstrap.py --status")
    print("下载 ipa  : python deploy/github_bootstrap.py --artifact")
    print("构建日志  : https://github.com/%s/%s/actions （这台电脑打不开可忽略，我用 API 查）" % (owner, repo))
    print("\nipa 到手后执行 python deploy/release_ipa.py --deploy，")
    print("即可生成 OTA 二维码，iPhone 打开 https://app.kqi.ink/mzh/download 扫码安装。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("失败:", clean(e))
        sys.exit(1)
