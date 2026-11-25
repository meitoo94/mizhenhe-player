#!/usr/bin/env python3
"""把 Apple 开发者后台下载的签名材料转成 GitHub Actions Secrets。

全程本地处理、不联网、不读取任何 Apple 账号密码。

用法:
    python deploy/signing/prepare_secrets.py --check    # 看还缺哪些材料
    python deploy/signing/prepare_secrets.py            # 材料齐了就生成 secrets

两条路线共用：
    *.cer（Apple Distribution 下载原件）        Certificates 页面下载，文件名不限
    *.mobileprovision                         Profiles 页面下载
    (私钥已预置在 deploy/signing/out/mizhenhe_distribution.key)

  Ad Hoc 快线（扫码直装）：描述文件带 ProvisionedDevices 时自动识别，
    不需要 .p8 / ASC Key ID / Issuer ID，只生成 6 个 Secrets。
  TestFlight 主线：还需 AuthKey_XXXXXXXXXX.p8（App Store Connect > 集成 下载）。

用法:
    python deploy/signing/prepare_secrets.py --check    # 看还缺哪些材料
    python deploy/signing/prepare_secrets.py            # 自动按描述文件类型生成 secrets
    python deploy/signing/prepare_secrets.py --adhoc    # 强制按 Ad Hoc 快线生成

输出 deploy/signing/secrets/ 下的 .txt 就是可直接粘进 GitHub Secrets 的值。
"""
import argparse
import base64
import glob
import os
import plistlib
import secrets as _secrets
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(HERE, "in")
OUT_DIR = os.path.join(HERE, "out")
SEC_DIR = os.path.join(HERE, "secrets")
KEY_PATH = os.path.join(OUT_DIR, "mizhenhe_distribution.key")
CSR_PATH = os.path.join(OUT_DIR, "mizhenhe_distribution.csr")


def find(patterns):
    for pat in patterns:
        hits = sorted(glob.glob(os.path.join(IN_DIR, pat)))
        if hits:
            return hits[0]
    return ""


def run(cmd, check=True):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError("命令失败: %s\n%s" % (" ".join(cmd), p.stderr.strip()[:600]))
    return p.stdout.strip(), p.stderr.strip(), p.returncode


def b64_file(path):
    return base64.b64encode(open(path, "rb").read()).decode("ascii")


def profile_meta(path):
    """解出描述文件 plist，返回 (info, 类型)。Ad Hoc 带 ProvisionedDevices。"""
    if not path:
        return {}, ""
    tmp = os.path.join(OUT_DIR, "_probe_profile.plist")
    try:
        run(["openssl", "smime", "-verify", "-in", path, "-inform", "DER",
             "-noverify", "-out", tmp], check=False)
        if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            with open(tmp, "rb") as f:
                info = plistlib.load(f)
            return info, ("Ad Hoc" if info.get("ProvisionedDevices") else "App Store")
    except Exception:
        pass
    finally:
        if os.path.isfile(tmp):
            os.remove(tmp)
    return {}, ""


def text_in(name):
    p = os.path.join(IN_DIR, name)
    return open(p, encoding="utf-8").read().strip() if os.path.isfile(p) else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查材料是否齐全")
    ap.add_argument("--adhoc", action="store_true", help="强制按 Ad Hoc 快线生成（忽略 .p8 要求）")
    args = ap.parse_args()

    os.makedirs(IN_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    if not shutil.which("openssl"):
        print("[FAIL] 没找到 openssl。Windows 上用 Git Bash 执行，或先装 OpenSSL。")
        return 2
    if not os.path.isfile(KEY_PATH):
        print("[FAIL] 缺少私钥:", KEY_PATH, " 先执行:")
        print("  openssl genrsa -out deploy/signing/out/mizhenhe_distribution.key 2048")
        print("  openssl req -new -key deploy/signing/out/mizhenhe_distribution.key \\")
        print("    -out deploy/signing/out/mizhenhe_distribution.csr \\")
        print('    -subj "/C=CN/CN=Mizhenhe Player Distribution"')
        return 2

    cer = find(["*.cer", "*.crt"])
    profile = find(["*.mobileprovision"])
    p8 = find(["AuthKey_*.p8", "*.p8"])
    info, ptype = profile_meta(profile)
    adhoc = args.adhoc or ptype == "Ad Hoc"

    print("== 当前材料 ==")
    print("  路线      :", "Ad Hoc 快线（扫码直装）" if adhoc else "TestFlight 主线")
    print("  CSR      :", CSR_PATH if os.path.isfile(CSR_PATH) else "(缺失)")
    print("  私钥     :", KEY_PATH)
    print("  证书 cer :", cer or "(缺：Apple 后台上传 CSR 后下载任意 .cer 放进 in/)")
    print("  描述文件 :", (profile + "  [" + ptype + "]") if profile else "(缺：Profiles 页面下载 .mobileprovision)")
    print("  ASC 密钥 :", p8 or ("(Ad Hoc 快线不需要)" if adhoc else "(缺：App Store Connect 生成 API 密钥后下载 .p8)"))
    required = [("证书", cer), ("描述文件", profile)]
    if not adhoc:
        required.append(("API 密钥", p8))
    missing = [n for n, p in required if not p]
    if missing:
        print("\n还缺:", "、".join(missing))
        print("把文件放进:", IN_DIR)
        return 1
    if args.check:
        print("\n材料齐全，去掉 --check 生成 Secrets。")
        return 0

    os.makedirs(SEC_DIR, exist_ok=True)
    pwd = os.environ.get("CERT_PASSWORD") or ("Mzh" + _secrets.token_hex(8))

    # 1) cer(DER) + 私钥 -> p12
    pem = os.path.join(OUT_DIR, "ios_distribution.pem")
    p12 = os.path.join(OUT_DIR, "mizhenhe_distribution.p12")
    run(["openssl", "x509", "-in", cer, "-inform", "DER", "-out", pem, "-outform", "PEM"])
    # OpenSSL 3 默认 PBES2/PBKDF2/AES-256 + SHA256 MAC，macOS 的 security import
    # 不认，CI 上会报 "MAC verification failed during PKCS12 import (wrong password?)"。
    # 显式用传统的 3DES + SHA1，GitHub macOS runner 可直接导入。
    run(["openssl", "pkcs12", "-export", "-out", p12,
         "-inkey", KEY_PATH, "-in", pem, "-passout", "pass:" + pwd,
         "-keypbe", "PBE-SHA1-3DES", "-certpbe", "PBE-SHA1-3DES",
         "-macalg", "sha1"])
    run(["openssl", "pkcs12", "-in", p12, "-info", "-noout", "-passin", "pass:" + pwd], check=False)
    print("\n[1/3] 已生成 p12:", p12)

    # 2) 解出描述文件 plist，读 UUID / Team ID / Bundle ID
    plist_path = os.path.join(OUT_DIR, "profile.plist")
    run(["openssl", "smime", "-verify", "-in", profile, "-inform", "DER",
         "-noverify", "-out", plist_path], check=False)
    profile_uuid, team_id, app_id, profile_type = "", "", "", ""
    if os.path.isfile(plist_path) and os.path.getsize(plist_path) > 0 and not info:
        with open(plist_path, "rb") as f:
            info = plistlib.load(f)
    if info:
        profile_uuid = str(info.get("UUID", ""))
        ent = info.get("Entitlements", {}) or {}
        prefixes = info.get("ApplicationIdentifierPrefix", []) or []
        team_id = str((prefixes or [""])[0]) or str(ent.get("com.apple.developer.team-identifier", ""))
        app_id = str(ent.get("application-identifier", ""))
        profile_type = "App Store" if not info.get("ProvisionedDevices") else "Ad Hoc"
    if not profile_uuid:
        print("[FAIL] 读不到描述文件 UUID，确认下载的是 .mobileprovision 且 openssl 可用")
        return 2
    print("[2/3] 描述文件:", profile_type, "| UUID", profile_uuid, "| Team", team_id or "(未读到)", "|", app_id)

    # 3) 生成所有 base64 secrets 文本
    key_id = os.path.basename(p8).replace("AuthKey_", "").replace(".p8", "") if p8 else ""
    bundle = app_id.split(".", 1)[1] if "." in app_id else text_in("bundle_id.txt") or "com.mizhenhe.player"
    values = {
        "CERT_P12_BASE64": b64_file(p12),
        "PROFILE_BASE64": b64_file(profile),
        "PROFILE_UUID": profile_uuid,
        "CERT_PASSWORD": pwd,
        "MZH_TEAM_ID": team_id or text_in("team_id.txt"),
        "MZH_BUNDLE_ID": bundle,
    }
    if p8:
        values["ASC_P8_BASE64"] = b64_file(p8)
        values["ASC_KEY_ID"] = key_id
        issuer = text_in("issuer_id.txt")
        if issuer:
            values["ASC_ISSUER_ID"] = issuer
    for k, v in values.items():
        with open(os.path.join(SEC_DIR, k + ".txt"), "w", encoding="utf-8", newline="") as f:
            f.write(v)

    print("[3/3] Secrets 文本已写入:", SEC_DIR, "（共 %d 个）" % len(values))
    print("\n== GitHub > Settings > Secrets and variables > Actions > New repository secret ==")
    for k in values:
        print("  %-22s <- secrets/%s.txt" % (k, k))
    if adhoc:
        print("\n  Ad Hoc 快线到此为止，无需 ASC_* / p8 / App Store Connect App 记录。")
        print("  触发 ios-adhoc.yml → 下载 ipa artifact → 本地跑 deploy/release_ipa.py --deploy")
    else:
        print("  %-22s <- App Store Connect > 集成 > Issuer ID" % "ASC_ISSUER_ID")
    print("  %-22s <- 可留空，默认 https:/$()/app.kqi.ink/mzh" % "MZH_CONFIG_BASE_URL")
    if team_id:
        print("\n  描述文件里读到的 Team ID = %s ，可直接用作 MZH_TEAM_ID" % team_id)
    print("\n  注意: *.key / *.p12 / *.p8 / secrets/ 全部不要提交到 Git。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
