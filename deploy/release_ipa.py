#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ipa → 手机能装，一条命令。

    python deploy/release_ipa.py --check                 # 看当前 ipa 状态，不改文件
    python deploy/release_ipa.py 某个.ipa                # 本地生成安装链接/二维码/状态
    python deploy/release_ipa.py 某个.ipa --deploy       # 上面 + 直接部署上线并重启

做的事：
  1. 把 ipa 放进 download/ipa/（下载页自动出现按钮）
  2. 解出 bundle id / 版本号 / 签名类型（Ad Hoc 才能扫码直装）
  3. 生成 OTA manifest.plist + 安装二维码（码里是普通 https 网页链接，微信/相机都能打开）
  4. 更新 server/release_state.json
  5. --deploy：上传 ipa / manifest / 状态到 43.142.72.226 并重启服务

只有 Ad Hoc / Development 签名的 ipa 能 itms-services 直装；
App Store 签名的包请走 TestFlight（脚本会明确告诉你）。
"""
import argparse
import base64
import glob
import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
import zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
IPA_DIR = os.path.join(ROOT, "download", "ipa")
STATE = os.path.join(ROOT, "server", "release_state.json")
PUBLIC_BASE = "https://app.kqi.ink/mzh"


def ipa_meta(path):
    """从 ipa 里读 Info.plist 与 embedded.mobileprovision。"""
    out = {"bundle_id": "", "version": "", "build": "", "name": "",
           "signing": "unknown", "devices": 0, "profile_uuid": ""}
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            app = next((n for n in names if n.endswith(".app/Info.plist")), "")
            if app:
                info = plistlib.loads(z.read(app))
                out["bundle_id"] = str(info.get("CFBundleIdentifier", ""))
                out["version"] = str(info.get("CFBundleShortVersionString", ""))
                out["build"] = str(info.get("CFBundleVersion", ""))
                out["name"] = str(info.get("CFBundleDisplayName") or
                                  info.get("CFBundleName") or "")
            prov = next((n for n in names if n.endswith("embedded.mobileprovision")), "")
            if prov:
                raw = z.read(prov)
                blob = b""
                try:
                    q = subprocess.run(["openssl", "smime", "-verify", "-inform", "DER",
                                        "-noverify"], input=raw, capture_output=True)
                    blob = q.stdout
                except Exception:
                    pass
                if not blob:
                    s, e = raw.find(b"<?xml"), raw.find(b"</plist>")
                    blob = raw[s:e + 8] if s >= 0 and e > s else b""
                if blob:
                    p = plistlib.loads(blob)
                    devs = p.get("ProvisionedDevices") or []
                    out["devices"] = len(devs)
                    out["profile_uuid"] = str(p.get("UUID", ""))
                    out["signing"] = "Ad Hoc / Development" if devs else "App Store"
    except Exception as e:
        out["error"] = str(e)[:160]
    return out


def manifest_plist(state, ipa_url, icon_url):
    items = {
        "items": [{
            "assets": [
                {"kind": "software-package", "url": ipa_url},
                {"kind": "display-image", "needs-shine": False, "url": icon_url},
                {"kind": "full-size-image", "needs-shine": False, "url": icon_url},
            ],
            "metadata": {
                "bundle-identifier": state.get("bundle_id") or "com.mizhenhe.player",
                "bundle-version": state.get("build") or "1",
                "kind": "software",
                "title": state.get("app_name") or "米帧盒播放器",
            },
        }]
    }
    return plistlib.dumps(items, fmt=plistlib.FMT_XML).decode("utf-8")


def qr_png(text):
    """生成二维码 PNG 字节。码里只放 https 网页链接，不放 itms-services 协议：
    微信扫一扫 / iOS 相机遇到 itms-services:// 只会显示一串纯文本，打不开安装。"""
    import qrcode
    from qrcode.image.pil import PilImage
    from PIL import Image
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=8, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(image_factory=PilImage).convert("RGB").resize((360, 360), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def qr_data_uri(text):
    try:
        return "data:image/png;base64," + base64.b64encode(qr_png(text)).decode()
    except Exception as e:
        print("[WARN] 二维码生成失败（不影响 ipa 下载）:", str(e)[:120])
        return ""


def write_qr_file(text, path):
    try:
        with open(path, "wb") as f:
            f.write(qr_png(text))
        return True
    except Exception as e:
        print("[WARN] 二维码图片写失败:", str(e)[:120])
        return False


def deploy(files):
    sys.path.insert(0, os.path.join(ROOT, "deploy", "yii2_merge"))
    import merge
    cli = merge.connect()
    sftp = cli.open_sftp()
    merge.run(cli, "mkdir -p /opt/mizhenhe/download/ipa", quiet=True)
    for local, remote in files:
        sftp.put(os.path.abspath(local), remote)
        sftp.chmod(remote, 0o644)
        print("  上传", os.path.relpath(local, ROOT).replace("\\", "/"), "→", remote)
    sftp.close()
    code, out, err = merge.run(
        cli,
        "chown -R mizhenhe:mizhenhe /opt/mizhenhe/download /opt/mizhenhe/server/release_state.json && "
        "systemctl restart mizhenhe && echo RESTART_OK", quiet=True)
    print("  " + (out + err).strip().splitlines()[-1])
    if code != 0:
        raise RuntimeError("远端重启失败: " + (out + err)[-400:])
    for _ in range(15):
        c, o, _ = merge.run(
            cli, "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8790/health",
            quiet=True)
        if o.strip() == "200":
            break
        time.sleep(0.7)
    print("  服务健康:", o.strip())
    cli.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ipa", nargs="?", help="ipa 路径；不填则用 download/ipa/ 里最新的")
    ap.add_argument("--deploy", action="store_true", help="同时部署上线")
    ap.add_argument("--check", action="store_true", help="只看当前状态")
    args = ap.parse_args()

    os.makedirs(IPA_DIR, exist_ok=True)
    existing = sorted(glob.glob(os.path.join(IPA_DIR, "*.ipa")),
                      key=os.path.getmtime, reverse=True)
    state = json.load(open(STATE, encoding="utf-8"))

    if args.check:
        print("当前 release_state:")
        print(json.dumps({k: state.get(k) for k in
                          ("native_status", "ipa_url", "install_available", "version", "build")},
                         ensure_ascii=False, indent=2))
        print("download/ipa/:", [os.path.basename(p) for p in existing] or "（空）")
        for p in existing[:1]:
            print("最新 ipa 信息:", json.dumps(ipa_meta(p), ensure_ascii=False, indent=2))
        return 0

    src = args.ipa
    if not src:
        if not existing:
            print("[FAIL] 没有 ipa。把出包得到的 .ipa 路径作为参数传给我：")
            print("  python deploy/release_ipa.py 路径/米帧盒播放器.ipa --deploy")
            return 1
        src = existing[0]
    src = os.path.abspath(src)
    if not os.path.isfile(src):
        print("[FAIL] 文件不存在:", src)
        return 1

    # 服务端 /download/ipa/<name> 不做 URL 解码，中文文件名经 quote() 后会 404。
    # 统一规范成 ASCII 名，OTA manifest 与 release_state 都指向它，更新安装也覆盖同一路径。
    name = "mizhenhe-player.ipa"
    dst = os.path.join(IPA_DIR, name)
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copy2(src, dst)
        print("[1/5] ipa 已放入 download/ipa/", name, os.path.getsize(dst), "bytes")

    meta = ipa_meta(dst)
    print("[2/5] 包信息:", json.dumps(meta, ensure_ascii=False))
    can_ota = meta["signing"].startswith("Ad Hoc")

    state["bundle_id"] = meta["bundle_id"] or state.get("bundle_id", "com.mizhenhe.player")
    state["version"] = meta["version"] or state.get("version", "1.0")
    state["build"] = meta["build"] or state.get("build", "1")
    state["ipa_url"] = PUBLIC_BASE + "/download/ipa/" + name
    state["updated_at"] = time.strftime("%Y-%m-%d")
    state["signing_type"] = meta["signing"]
    state["install_available"] = can_ota

    if can_ota and not state.get("testflight_url") and not state.get("appstore_url"):
        state["native_status"] = "ipa_ready"
        state["native_status_text"] = "安装包已上传 · 手机扫码即可安装"
        state["native_hint"] = "用微信或 iPhone 相机扫下方安装二维码 → 在 Safari 中打开 → 自动弹出安装；首次安装需在 设置 → 通用 → VPN与设备管理 里信任一次。"
        manifest_url = PUBLIC_BASE + "/download/manifest.plist"
        plist_text = manifest_plist(state, state["ipa_url"],
                                    PUBLIC_BASE + "/download/apple-touch-icon.png")
        with open(os.path.join(IPA_DIR, "manifest.plist"), "w", encoding="utf-8") as f:
            f.write(plist_text)
        ota = "itms-services://?action=download-manifest&url=" + manifest_url
        install_page = PUBLIC_BASE + "/download?install=1"
        state["install_page_url"] = install_page
        state["install_qr"] = qr_data_uri(install_page)
        state["install_ota_url"] = ota
        write_qr_file(install_page, os.path.join(ROOT, "download", "米帧盒-安装二维码.png"))
        print("[3/5] OTA manifest + 网页链接安装二维码已生成（Safari 自动唤起直装）")
    else:
        state["native_status"] = "ipa_ready"
        state["native_status_text"] = "安装包已上传 · 请通过 TestFlight 安装"
        state["native_hint"] = ("这个包是 %s 签名，不能用二维码直接装；"
                                "上传到 TestFlight 后手机会收到更新。" % meta["signing"])
        state.pop("install_qr", None)
        state.pop("install_ota_url", None)
        print("[3/5] 非 Ad Hoc 签名 → 走 TestFlight，不生成直装二维码")

    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)
    print("[4/5] release_state.json 已更新")

    if args.deploy:
        files = [(dst, "/opt/mizhenhe/download/ipa/" + name),
                 (STATE, "/opt/mizhenhe/server/release_state.json"),
                 (os.path.join(ROOT, "download", "index.html"),
                  "/opt/mizhenhe/download/index.html")]
        mf = os.path.join(IPA_DIR, "manifest.plist")
        if os.path.isfile(mf):
            files.append((mf, "/opt/mizhenhe/download/ipa/manifest.plist"))
        print("[5/5] 部署上线")
        deploy(files)
        print("\n安装页:", PUBLIC_BASE + "/download")
        if can_ota:
            print("扫码打开的是网页链接（微信/相机都可），Safari 里会自动唤起安装。")
    else:
        print("[5/5] 未部署。加 --deploy 直接上线，或我后面统一部署。")

    print("\n以后每次改完：重新出包 → 再跑一次本脚本（带 --deploy）→ 手机点安装/更新。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
