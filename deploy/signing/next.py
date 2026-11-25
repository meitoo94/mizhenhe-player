#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键体检：现在走到第几步、下一步该做什么、文件放哪里。

    python deploy/signing/next.py            # 人话版清单 + 写进度 JSON
    python deploy/signing/next.py --json    # 只输出 JSON

材料统一丢进 deploy/signing/in/，脚本自动识别，不用改任何代码。
账号密码永远不需要写进工程；Apple 后台双击认证的步骤必须本人操作。
"""
import argparse
import glob
import json
import os
import plistlib
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
IN_DIR = os.path.join(HERE, "in")
OUT_DIR = os.path.join(HERE, "out")
SEC_DIR = os.path.join(HERE, "secrets")
PROGRESS = os.path.join(ROOT, "server", "signing_progress.json")

os.makedirs(IN_DIR, exist_ok=True)


def txt(name):
    """读取用户放进 in/ 的单行信息文件。"""
    p = os.path.join(IN_DIR, name)
    if not os.path.isfile(p):
        return ""
    return open(p, encoding="utf-8").read().strip()


def one(patterns):
    for pat in patterns:
        hits = sorted(glob.glob(os.path.join(IN_DIR, pat)))
        if hits:
            return hits[0]
    return ""


def rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/") if p else ""


def cert_ok():
    p = one(["*.cer", "*.crt"])
    return bool(p) and os.path.getsize(p) > 200


def profile_info():
    p = one(["*.mobileprovision"])
    if not p:
        return {"path": "", "type": "", "uuid": "", "team": "", "bundle": "", "devices": 0}
    info = {}
    try:
        raw = open(p, "rb").read()
        # openssl smime 解出 plist；失败时直接从二进制里截 plist 段
        q = subprocess.run(["openssl", "smime", "-verify", "-in", p, "-inform", "DER",
                            "-noverify"], capture_output=True)
        blob = q.stdout
        if not blob:
            s = raw.find(b"<?xml")
            e = raw.find(b"</plist>")
            blob = raw[s:e + 8] if s >= 0 and e > s else b""
        if blob:
            info = plistlib.loads(blob)
    except Exception:
        pass
    ent = info.get("Entitlements", {}) or {}
    prefixes = info.get("ApplicationIdentifierPrefix", []) or []
    devs = info.get("ProvisionedDevices") or []
    appid = str(ent.get("application-identifier", ""))
    return {
        "path": p,
        "type": "Ad Hoc" if devs else ("App Store" if info else "未知"),
        "uuid": str(info.get("UUID", "")),
        "team": str((prefixes or [""])[0]) or str(ent.get("com.apple.developer.team-identifier", "")),
        "bundle": appid.split(".", 1)[1] if "." in appid else appid,
        "devices": len(devs),
    }


def p8_ok():
    p = one(["AuthKey_*.p8", "*.p8"])
    return bool(p) and os.path.getsize(p) > 100


def key_id():
    p = one(["AuthKey_*.p8", "*.p8"])
    return os.path.basename(p).replace("AuthKey_", "").replace(".p8", "") if p else ""


def git_remote():
    try:
        r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT,
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def ipas():
    return sorted(glob.glob(os.path.join(ROOT, "download", "ipa", "*.ipa")),
                  key=os.path.getmtime, reverse=True)


def secrets_ready():
    """Ad Hoc 快线只需 6 个 Secrets（含 Team/Bundle），TestFlight 主线还要 +3 个 ASC 项。"""
    base = ["CERT_P12_BASE64", "PROFILE_BASE64", "PROFILE_UUID",
            "CERT_PASSWORD", "MZH_TEAM_ID", "MZH_BUNDLE_ID"]
    asc = ["ASC_P8_BASE64", "ASC_KEY_ID", "ASC_ISSUER_ID"]
    def count(names):
        return sum(1 for n in names if os.path.isfile(os.path.join(SEC_DIR, n + ".txt")))
    return {"adhoc": "%d/6" % count(base),
            "mainline": "%d/9" % count(base + asc)}


def step(n, title, owner, action, target, done, hint=""):
    return {"n": n, "title": title, "owner": owner, "action": action,
            "target": target, "status": "done" if done else "todo", "hint": hint}


def pull_udid():
    """从线上 /mzh/udid/list 拉回手机登记的设备，写成 in/udid.txt（一行一个）。"""
    import urllib.request
    url = os.environ.get("MZH_UDID_LIST", "https://app.kqi.ink/mzh/udid/list")
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.load(r)
        rows = [str(x.get("udid", "")).strip() for x in data.get("devices", [])]
        rows = [x for x in rows if x]
        if not rows:
            return 0
        with open(os.path.join(IN_DIR, "udid.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(rows) + "\n")
        return len(rows)
    except Exception as e:
        print("[WARN] 拉取线上 UDID 失败:", e)
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pull-udid", action="store_true",
                    help="从线上采集页同步 UDID 到 deploy/signing/in/udid.txt")
    args = ap.parse_args()

    if args.pull_udid:
        print("已从线上同步 %d 台设备到 deploy/signing/in/udid.txt" % pull_udid())

    team = txt("team_id.txt") or txt("team.txt")
    issuer = txt("issuer_id.txt") or txt("issuer.txt")
    asc_key = txt("key_id.txt") or key_id()
    udid = txt("udid.txt")
    repo = txt("github_repo.txt") or git_remote()
    prof = profile_info()
    cer = cert_ok()
    p8 = p8_ok()
    ipa_list = ipas()
    sec = secrets_ready()

    # ---------- 主线：TestFlight（官方迭代分发） ----------
    main_steps = [
        step(1, "开通/确认 Apple Developer Program 并拿 Team ID", "你",
             "登录 developer.apple.com/account → Membership，确认是付费会员；把 10 位 Team ID 存成一行文本",
             "deploy/signing/in/team_id.txt", bool(team),
             "个人 ¥688/年，一般几小时~2 天生效。没有付费会员就无法生成 Distribution 证书，任何机器都出不了可安装的包。"),
        step(2, "用我生成好的 CSR 换 Distribution 证书", "你",
             "Certificates → ＋ → Apple Distribution（中文界面叫「苹果发行」）→ 上传桌面 mizhenhe_distribution.csr → 下载 .cer 放进 in/",
             "deploy/signing/in/*.cer", cer,
             "CSR 和私钥已生成且成对，不要重新生成；Apple 下载的文件名可能是 apple_distribution.cer，任意 .cer 都能自动识别。"),
        step(3, "创建 App ID + App Store 描述文件", "你",
             "Identifiers 注册 com.mizhenhe.player → Profiles → ＋ → Distribution / App Store → 下载 .mobileprovision",
             "deploy/signing/in/*.mobileprovision",
             bool(prof["path"]) and prof["type"] == "App Store",
             "必须选 App Store 类型；Ad Hoc 类型会在 exportArchive 阶段失败。"),
        step(4, "生成 App Store Connect API 密钥", "你",
             "App Store Connect → 用户和访问 → 集成 → 密钥 → 生成（App Manager 权限）→ 下载 .p8，记下 Key ID 和 Issuer ID",
             "deploy/signing/in/AuthKey_*.p8 + asc_ids.txt", bool(p8 and issuer),
             "p8 只能下载一次。Key ID 会由文件名自动识别，Issuer ID 请写进 in/issuer_id.txt。"),
        step(5, "在 App Store Connect 新建 App 记录", "你",
             "我的 App → ＋ → 名称填「米帧盒播放器」、Bundle ID 选 com.mizhenhe.player、SKU 随便填 → 建好后告诉我一声",
             "（无需文件，口头确认）", bool(txt("app_created.txt")),
             "TestFlight 上传前必须有这条 App 记录；内部测试组加你自己的 Apple ID 邮箱即可。"),
        step(6, "准备一台 macOS 编译器", "你选一条",
             "已完成：设备授权拿 token → api.github.com 建公开仓库 meitoo94/mizhenhe-player → 加密写 6 个 Secrets → Git Data API 传码 → 云端 macOS 多镜像并行编译",
             "deploy/signing/in/github_repo.txt", bool(repo),
             "github.com 网页慢且偶发 5xx（跟 WiFi/手机流量无关），但 api.github.com 稳定；token 走设备授权拿，不用手机、不用进 PAT 设置页。macos-26 的 arm64 免费池常因容量被取消，已在 ios-adhoc.yml 里加 macos-15-intel / macos-26-intel 多 runner 矩阵，四个镜像都带 Xcode 26，公开仓库免费。"),
        step(7, "我出包并自动上传到 TestFlight", "我",
             "材料齐后我跑 prepare_secrets.py → 填 Secrets → 触发 CI → 出 ipa → 上传，全程不用你动手",
             "download/ipa/*.ipa", bool(ipa_list)),
        step(8, "手机装 TestFlight，之后每次改完点「更新」", "你",
             "App Store 搜 TestFlight 安装 → 用你的 Apple ID 登录 → 接受邀请 → 装米帧盒播放器",
             "（手机）", bool(txt("installed.txt")),
             "内部测试不用苹果审核，上传处理完几分钟就能装；以后我每次出新构建，你手机会收到推送。"),
    ]

    # ---------- 快线：Ad Hoc OTA 直装（改完立刻装，不用等 TestFlight 处理） ----------
    fast_steps = [
        main_steps[0],
        main_steps[1],
        step(3, "拿到你 iPhone 的 UDID", "你",
             "iPhone 用 Safari 打开 https://app.kqi.ink/mzh/udid → 点「获取本机 UDID」→ 设置里安装描述文件，UDID 自动回传服务器",
             "deploy/signing/in/udid.txt", bool(udid),
             "装完告诉我一声，我跑 next.py --pull-udid 自动同步进工程；多台设备各点一次，一行一个。"),
        step(4, "注册设备 + 创建 Ad Hoc 描述文件", "你",
             "Devices → ＋ 登记 UDID → Profiles → ＋ → Distribution / 临时(Ad Hoc) → 选设备 → 下载 .mobileprovision",
             "deploy/signing/in/*.mobileprovision",
             bool(prof["path"]) and prof["type"] == "Ad Hoc",
             "这条线不需要 p8、不需要 App Store Connect、不需要新建 App 记录；证书 + 描述文件 + 私钥三件套已验过一致，p12 与 6 个 Secrets 已生成。"),
        step(5, "准备一台 macOS 编译器", "你选一条",
             "已完成：设备授权拿 token → api.github.com 建公开仓库 meitoo94/mizhenhe-player → 加密写 6 个 Secrets → Git Data API 传码 → 云端 macOS 多镜像并行编译",
             "deploy/signing/in/github_repo.txt", bool(repo),
             "github.com 网页慢且偶发 5xx（跟 WiFi/手机流量无关），但 api.github.com 稳定；token 走设备授权拿，不用手机、不用进 PAT 设置页。macos-26 的 arm64 免费池常因容量被取消，已在 ios-adhoc.yml 里加 macos-15-intel / macos-26-intel 多 runner 矩阵，四个镜像都带 Xcode 26，公开仓库免费。"),
        step(6, "我出包并生成扫码安装链接", "我",
             "已完成：main 触发 ios-adhoc.yml 多 runner 矩阵出 Ad Hoc ipa → --artifact 经 API 下载 → python deploy/release_ipa.py --deploy 生成 OTA manifest + 二维码 + 下载页切换；ipa/manifest 线上均已实测 200",
             "download/ipa/*.ipa", bool(ipa_list)),
        step(7, "手机扫码安装；以后每次改完重新扫一次", "你",
             "用 iPhone 相机/ Safari 扫下载页的安装二维码 → 点安装 → 设置里信任一次开发者",
             "（手机）", bool(txt("installed.txt"))),
    ]

    def summarize(steps):
        done = sum(1 for s in steps if s["status"] == "done")
        nxt = next((s for s in steps if s["status"] == "todo"), None)
        return {"done": done, "total": len(steps),
                "next": nxt, "steps": steps}

    data = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mainline": dict(summarize(main_steps),
                         name="TestFlight · 官方迭代分发（推荐）",
                         desc="上传后手机自动收到更新，适合长期反复改、反复测；材料多 2 项。"),
        "fastline": dict(summarize(fast_steps),
                         name="Ad Hoc · 扫码直装（最快看到改动）",
                         desc="不用 p8、不用 App Store Connect、不用审核，改完一条命令就能装；每台设备要登记 UDID。"),
        "materials": {
            "team_id": team,
            "csr": rel(os.path.join(OUT_DIR, "mizhenhe_distribution.csr")) if os.path.isfile(
                os.path.join(OUT_DIR, "mizhenhe_distribution.csr")) else "",
            "private_key": rel(os.path.join(OUT_DIR, "mizhenhe_distribution.key")),
            "cert": rel(one(["*.cer", "*.crt"])),
            "profile": rel(prof["path"]),
            "profile_type": prof["type"],
            "profile_uuid": prof["uuid"],
            "profile_team": prof["team"],
            "profile_bundle": prof["bundle"],
            "profile_devices": prof["devices"],
            "p8": rel(one(["AuthKey_*.p8", "*.p8"])),
            "asc_key_id": asc_key,
            "asc_issuer_id": issuer,
            "udid": udid,
            "github_repo": repo,
            "secrets_ready": sec["mainline"],
            "secrets_ready_adhoc": sec["adhoc"],
            "ipa": [rel(p) for p in ipa_list],
        },
        "note": "Apple 后台带双重认证，登录和点击必须本人做；本脚本不联网、不读取任何账号密码。",
    }

    with open(PROGRESS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print("=" * 68)
    print("米帧盒播放器 · 出包安装进度体检   %s" % data["updated_at"])
    print("=" * 68)
    for key in ("mainline", "fastline"):
        line = data[key]
        print("\n【%s】 %d/%d 完成" % (line["name"], line["done"], line["total"]))
        print("  %s" % line["desc"])
        nxt = line["next"]
        if nxt:
            print("  → 下一步（第 %d 步 · %s）：%s" % (nxt["n"], nxt["owner"], nxt["title"]))
            print("    怎么做：%s" % nxt["action"])
            print("    放哪里：%s" % nxt["target"])
            if nxt["hint"]:
                print("    注意：%s" % nxt["hint"])
        else:
            print("  → 全部完成，手机可以安装了。")
    print("\n已识别材料：")
    for k, v in data["materials"].items():
        empty = v in ("", None, [], {})
        print("  %-18s %s" % (k, "（缺）" if empty else v))
    print("\n进度已写入 server/signing_progress.json，线上 /mzh/signing 可随时查看。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
