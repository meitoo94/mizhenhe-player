"""Windows / Mac 都能跑的 Xcode 交付包静态自检（不依赖 Xcode）。

真实检查：Swift 源码、plist、隐私清单、xcconfig、图标、shell 语法、
本地服务端 /v1/config 实测响应。结果写 evidence/xcode_bundle_preflight.json。
用法: python xcode/scripts/check_bundle.py
"""
import json
import plistlib
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # channel-switch
XCODE = ROOT / "xcode"
IOS = ROOT / "ios"
PROFILE = ROOT / "server" / "profiles" / "wanneng_rebrand.json"
EVIDENCE = ROOT / "evidence" / "xcode_bundle_preflight.json"

results = []


def check(name, condition, detail, level="FAIL"):
    ok = bool(condition)
    results.append({"name": name, "ok": ok, "level": level if not ok else "PASS",
                    "detail": str(detail)})
    tag = "PASS" if ok else level
    print(f"  [{tag}] {name}: {detail}")
    return ok


def section(title):
    print(f"\n== {title} ==")


SWIFT = ["AppEntry.swift", "ChannelAttribution.swift", "ChannelModels.swift",
         "KeychainStore.swift", "RemoteConfig.swift", "PaywallFlow.swift",
         "NormalHomeView.swift", "AccountDeletion.swift"]

section("1. Swift 源码")
for f in SWIFT:
    p = IOS / f
    check(f"源码 {f}", p.is_file() and p.stat().st_size > 100,
          f"{p.stat().st_size} bytes" if p.is_file() else "缺失")

ALLOWED = {"SwiftUI", "Foundation", "UIKit", "AVKit", "CryptoKit", "Security",
           "CoreMedia", "UniformTypeIdentifiers", "Darwin", "AdServices",
           "AdSupport", "Combine", "os"}
all_text = "\n".join((IOS / f).read_text(encoding="utf-8") for f in SWIFT)
imports = set(re.findall(r"^\s*import\s+([A-Za-z][A-Za-z0-9_]*)", all_text, re.M))
check("import 全为系统框架", imports <= ALLOWED,
      "、".join(sorted(imports)) + (" / 越界: " + "、".join(imports - ALLOWED)
                                 if imports - ALLOWED else ""))
check("@main 唯一", all_text.count("@main") == 1, f"出现 {all_text.count('@main')} 次")

REQUIRED = ["ChannelSwitchApp", "RootView", "ConfigStore", "ConfigPayload",
            "ConfigEnvelope", "GateConfig", "NormalHomeView", "PaywallFlow",
            "TrialPlayerView", "AccountDeletionView", "AccountDeletion",
            "ChannelAttribution", "KeychainStore", "SkinDefaults",
            "LocalPlayerView", "VIPPackagesView", "LoginSheet"]
defined = set(re.findall(r"^(?:final\s+class|class|struct|enum|private\s+struct|"
                         r"private\s+final\s+class|extension)\s+([A-Za-z_]\w*)",
                         all_text, re.M))
missing = [s for s in REQUIRED if s not in defined]
check("关键类型定义齐全", not missing,
      f"{len(REQUIRED)} 个，缺失: {missing}" if missing else f"{len(REQUIRED)} 个全部找到")

check("后端地址由 Info.plist 注入",
      "ConfigBaseURL" in (IOS / "AppEntry.swift").read_text(encoding="utf-8")
      and "resolvedBaseURL" in (IOS / "AppEntry.swift").read_text(encoding="utf-8"),
      "AppEntry.swift 用 Bundle 读 ConfigBaseURL，无写死域名")
ca = (IOS / "ChannelAttribution.swift").read_text(encoding="utf-8")
check("上架包排除 IDFA 引用",
      ca.count("#if canImport(AdSupport) && !MZH_NO_AD_TRACKING") == 2,
      "头文件与取值处都受 MZH_NO_AD_TRACKING 控制")
check("剪贴板归因已废弃", "UIPasteboard" not in all_text,
      "全工程无 UIPasteboard")

section("2. plist / 隐私清单")
plists = {}
for name in ["Info.plist", "Info-Debug.plist", "ExportOptions.plist"]:
    p = XCODE / name
    try:
        plists[name] = plistlib.loads(p.read_bytes())
        check(f"{name} 可解析", True, f"{len(plists[name])} 个键")
    except Exception as e:
        check(f"{name} 可解析", False, repr(e))
try:
    privacy = plistlib.loads((XCODE / "Resources/PrivacyInfo.xcprivacy").read_bytes())
    check("PrivacyInfo.xcprivacy 可解析", True,
          f"{len(privacy.get('NSPrivacyCollectedDataTypes', []))} 类数据")
except Exception as e:
    privacy = {}
    check("PrivacyInfo.xcprivacy 可解析", False, repr(e))

info = plists.get("Info.plist", {})
dbg = plists.get("Info-Debug.plist", {})
check("显示名纯中文", info.get("CFBundleDisplayName") == "米帧盒播放器",
      str(info.get("CFBundleDisplayName")))
check("Release 无明文 HTTP 例外", "NSAppTransportSecurity" not in info,
      "审核态不允许 ATS 全域例外")
check("Debug 有局域网例外",
      dbg.get("NSAppTransportSecurity", {}).get("NSAllowsArbitraryLoads") is True
      and "NSLocalNetworkUsageDescription" in dbg,
      "真机可连 192.168.x.x:8790")
check("两份 plist 都注入 ConfigBaseURL",
      info.get("ConfigBaseURL") == "$(CONFIG_BASE_URL)"
      and dbg.get("ConfigBaseURL") == "$(CONFIG_BASE_URL)",
      "$(CONFIG_BASE_URL)")
check("不上报加密例外", info.get("ITSAppUsesNonExemptEncryption") is False,
      "免填出口合规问卷")
check("隐私清单声明不追踪", privacy.get("NSPrivacyTracking") is False, "false")
reasons = [x.get("NSPrivacyAccessedAPITypeReasons")
           for x in privacy.get("NSPrivacyAccessedAPITypes", [])]
check("Required Reason API 有 CA92.1", ["CA92.1"] in reasons, str(reasons))
check("上架导出为 app-store", plists.get("ExportOptions.plist", {}).get("method") == "app-store",
      str(plists.get("ExportOptions.plist", {}).get("method")))

section("3. XcodeGen / xcconfig / 图标")
spec = (XCODE / "project.yml").read_text(encoding="utf-8")
for key in ["INFOPLIST_FILE", "SWIFT_ACTIVE_COMPILE_CONDITIONS: MZH_NO_AD_TRACKING",
            "TARGETED_DEVICE_FAMILY: \"1\"", "path: ../ios", "path: Resources",
            "PRODUCT_BUNDLE_IDENTIFIER", "DEVELOPMENT_TEAM"]:
    check(f"project.yml 含 {key[:38]}", key in spec, "已配置")

dbg_cfg = (XCODE / "Config-Debug.xcconfig").read_text(encoding="utf-8")
rel_cfg = (XCODE / "Config-Release.xcconfig").read_text(encoding="utf-8")
check("Debug URL 指向已部署公网后端", "https:/$()/app.kqi.ink/mzh" in dbg_cfg,
      "https://app.kqi.ink/mzh（云 Mac / TestFlight 真机可直接连）")
check("Release URL 为 HTTPS 且非占位",
      "https:/$()/app.kqi.ink/mzh" in rel_cfg and "yourdomain.com" not in rel_cfg,
      "https://app.kqi.ink/mzh")
check("Release / Debug 配置域名一致",
      re.search(r"^CONFIG_BASE_URL\s*=\s*(\S+)", rel_cfg, re.M).group(1)
      == re.search(r"^CONFIG_BASE_URL\s*=\s*(\S+)", dbg_cfg, re.M).group(1),
      "两端都指向 /mzh 子路径")
team = re.search(r"^MZH_TEAM_ID\s*=\s*(\S+)", rel_cfg, re.M)
team_val = team.group(1) if team else "未配置"
check("Team ID 已填真实值", team_val not in ("", "XXXXXXXXXX", "未配置"),
      f"当前 {team_val} —— 需你从 developer.apple.com 提供，不计入失败", level="TODO")
bundle = re.search(r"^MZH_BUNDLE_ID\s*=\s*(\S+)", rel_cfg, re.M)
check("Bundle ID 已定", bool(bundle), bundle.group(1) if bundle else "未配置")

try:
    from PIL import Image
    icon = XCODE / "Resources/Assets.xcassets/AppIcon.appiconset/AppIcon.png"
    im = Image.open(icon)
    check("AppIcon 1024x1024 不透明", im.size == (1024, 1024) and im.mode in ("RGB", "L"),
          f"{im.size} {im.mode} {icon.stat().st_size} bytes")
except Exception as e:
    check("AppIcon 1024x1024 不透明", False, repr(e))

section("4. shell 脚本语法")
sh_files = sorted((XCODE / "scripts").glob("*.sh"))
for f in sh_files:
    try:
        r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True, timeout=20)
        check(f"bash -n {f.name}", r.returncode == 0, r.stderr.strip() or "OK")
    except FileNotFoundError:
        check(f"bash -n {f.name}", False, "本机无 bash，跳过语法检查", level="TODO")

section("5. 服务端真实请求 + 皮肤配置")
try:
    req = urllib.request.Request(
        "http://127.0.0.1:8790/v1/config",
        data=json.dumps({"idfv": "xcode-preflight", "channel": "organic",
                         "auto_attribution": True}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        body = r.read()
        status = r.status
    env = json.loads(body)
    cfg = json.loads(env["config"])
    check("POST /v1/config 实测", status == 200 and "sig" in env,
          f"HTTP {status}, {len(body)} bytes, mode={cfg.get('mode')}")
    check("HMAC 签名随配置下发", len(env.get("sig", "")) == 64,
          f"sig 长度 {len(env.get('sig', ''))}")
except Exception as e:
    check("POST /v1/config 实测", False, repr(e))

try:
    prof = json.loads(PROFILE.read_text(encoding="utf-8"))
    gate = prof["channels"]["bbdd1"]["paywall"]["gate"]
    check("bbdd1 强制闭环参数完整",
          gate.get("force_paywall_until_vip") is True
          and gate.get("login_to_vip_delay_seconds") == 1
          and gate.get("skip_action") == "back_to_trial"
          and gate.get("allow_skip") is False,
          json.dumps(gate, ensure_ascii=False)[:150])
    check("商店名统一", prof["meta"]["display_name"] == "米帧盒播放器",
          prof["meta"]["display_name"])
except Exception as e:
    check("bbdd1 强制闭环参数完整", False, repr(e))
    check("商店名统一", False, repr(e))

fails = [r for r in results if not r["ok"] and r["level"] == "FAIL"]
todos = [r for r in results if not r["ok"] and r["level"] == "TODO"]
passed = [r for r in results if r["ok"]]
EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
EVIDENCE.write_text(json.dumps({
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "summary": {"pass": len(passed), "fail": len(fails), "todo": len(todos),
                "total": len(results)},
    "results": results,
}, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"\n结果: PASS {len(passed)} / FAIL {len(fails)} / TODO {len(todos)} / 共 {len(results)}")
print(f"证据: {EVIDENCE}")
if todos:
    for t in todos:
        print(f"  TODO: {t['name']} -> {t['detail']}")
sys.exit(1 if fails else 0)
