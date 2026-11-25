#!/usr/bin/env bash
# Mac 环境自检：在 channel-switch/xcode 目录下执行 ./scripts/preflight.sh
# 只做检查，不改任何东西。出现 FAIL 就按下方的修复提示处理，再跑一次。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

PASS=0; FAIL=0; WARN=0
ok(){ printf '  \033[32mPASS\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
no(){ printf '  \033[31mFAIL\033[0m %s\n' "$1 -> $2"; FAIL=$((FAIL+1)); }
warn(){ printf '  \033[33mWARN\033[0m %s\n' "$1 -> $2"; WARN=$((WARN+1)); }

echo "== 1. 工具链 =="
XV="$(xcodebuild -version 2>/dev/null | awk '/^Xcode/{print $2}' | head -1)"
MAJOR="${XV%%.*}"
if [[ "${MAJOR:-0}" =~ ^[0-9]+$ ]] && [ "${MAJOR:-0}" -ge 26 ]; then
  ok "Xcode ${XV}（>=26，满足 2026-04-28 起的上传要求）"
else
  no "Xcode 版本 ${XV:-未安装}" "App Store 或 developer.apple.com 装 Xcode 26+，再 sudo xcode-select -s /Applications/Xcode.app"
fi

SDKV="$(xcrun --sdk iphoneos --show-sdk-version 2>/dev/null)"
if [[ "${SDKV%%.*}" =~ ^[0-9]+$ ]] && [ "${SDKV%%.*}" -ge 26 ]; then
  ok "iOS SDK ${SDKV}"
else
  no "iOS SDK ${SDKV:-未找到}" "装 Xcode 26+ 后 sudo xcodebuild -runFirstLaunch"
fi

if command -v xcodegen >/dev/null 2>&1; then
  ok "xcodegen $(xcodegen --version 2>/dev/null)"
else
  no "xcodegen 未安装" "brew install xcodegen"
fi
command -v /usr/libexec/PlistBuddy >/dev/null 2>&1 && ok "PlistBuddy" || no "PlistBuddy" "随 Xcode 一起安装"

echo "== 2. 开发者账号 / 签名 =="
TEAM="$(awk -F'= ' '/^MZH_TEAM_ID/{gsub(/[ \t;]/,"",$2); print $2; exit}' Config-Release.xcconfig)"
if [ -n "$TEAM" ] && [ "$TEAM" != "XXXXXXXXXX" ]; then
  ok "Team ID 已填 ${TEAM}"
else
  no "MZH_TEAM_ID 仍是占位" "编辑 Config-Debug.xcconfig / Config-Release.xcconfig 两个文件"
fi
if security find-identity -v -p codesigning 2>/dev/null | grep -q "Apple Distribution"; then
  ok "钥匙串内有 Apple Distribution 证书"
else
  no "未找到 Apple Distribution 证书" "Xcode > Settings > Accounts > Manage Certificates 生成，或导入 .p12"
fi
KEY_DIR="$HOME/.appstoreconnect/private_keys"
if ls "$KEY_DIR"/AuthKey_*.p8 >/dev/null 2>&1; then
  ok "App Store Connect API Key: $(ls "$KEY_DIR"/AuthKey_*.p8 | xargs -n1 basename | tr '\n' ' ')"
else
  no "未找到 .p8" "把 AuthKey_XXXXXXXXXX.p8 放到 ${KEY_DIR}/（upload.sh 用）"
fi

echo "== 3. 后端连通（Debug 配置指向的那台服务）=="
RAW="$(awk -F'= ' '/^CONFIG_BASE_URL/{gsub(/[ \t;]/,"",$2); print $2; exit}' Config-Debug.xcconfig)"
URL="$(printf '%s' "$RAW" | sed 's#/\$()/#//#')"
if [ -n "$URL" ]; then
  CODE="$(curl -s -m 5 -o /tmp/mzh_preflight.json -w '%{http_code}' -X POST "$URL/v1/config" \
      -H 'Content-Type: application/json' \
      -d '{"idfv":"preflight-check","channel":"organic","auto_attribution":true}' 2>/dev/null || true)"
  if [ "$CODE" = "200" ]; then
    ok "POST ${URL}/v1/config -> 200 ($(wc -c </tmp/mzh_preflight.json) bytes)"
  else
    # 云 Mac 在机房，本来就访问不到你家里的局域网地址；只告警，不阻断编译/上传。
    warn "后端 ${URL}/v1/config 返回 ${CODE:-无响应}" "本机 Mac 上请启动 8790 服务；云 Mac 上属正常，但 TestFlight 真机运行时必须把后端换成公网 HTTPS 域名"
  fi
else
  no "Config-Debug.xcconfig 里没读到 CONFIG_BASE_URL" "检查文件格式"
fi

echo "== 4. 配置域名（云 Mac / 提审必看）=="
REL_URL="$(awk -F'= ' '/^CONFIG_BASE_URL/{gsub(/[ \t;]/,"",$2); print $2; exit}' Config-Release.xcconfig)"
case "$REL_URL" in
  *yourdomain.com*) warn "Release 域名仍是占位" "提审前把 Config-Release.xcconfig 换成自己的 HTTPS 配置域名" ;;
  *'/$()/'*) ok "Release 域名已填 ${REL_URL}" ;;
  *) warn "Release 域名格式可疑" "${REL_URL}" ;;
esac
DBG_URL="$(awk -F'= ' '/^CONFIG_BASE_URL/{gsub(/[ \t;]/,"",$2); print $2; exit}' Config-Debug.xcconfig)"
case "$DBG_URL" in
  *192.168.*|*127.0.0.1*) warn "Debug 指向局域网 ${DBG_URL}" "云 Mac / TestFlight 真机拉不到；换成 https://app.kqi.ink/mzh 或把后端公网化" ;;
  *) ok "Debug 后端 ${DBG_URL}" ;;
esac
# 已部署到用户自有域名时，真机与云 Mac 都走这个 HTTPS 地址。
PUB_CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 8 https://app.kqi.ink/mzh/health 2>/dev/null || true)"
if [ "$PUB_CODE" = "200" ]; then
  ok "公网后端 https://app.kqi.ink/mzh/health -> 200"
else
  warn "公网后端 /mzh/health 返回 ${PUB_CODE:-无响应}" "后端尚未上线；按 deploy/upload.py 部署后再验，本机 8790 联调不受影响"
fi

echo "== 5. 工程文件 =="
for f in project.yml Info.plist Info-Debug.plist ExportOptions.plist \
         Resources/PrivacyInfo.xcprivacy \
         Resources/Assets.xcassets/AppIcon.appiconset/AppIcon.png; do
  [ -s "$f" ] && ok "$f" || no "$f 缺失" "检查文件是否从 Windows 拷全"
done
for f in ../ios/*.swift; do :; done
SWIFT_N="$(ls ../ios/*.swift 2>/dev/null | wc -l | tr -d ' ')"
[ "${SWIFT_N:-0}" -ge 8 ] && ok "Swift 源文件 ${SWIFT_N} 个" || no "Swift 源文件只有 ${SWIFT_N} 个" "应有 8 个"

echo
echo "结果: PASS=${PASS} FAIL=${FAIL} WARN=${WARN}"
[ "$FAIL" -eq 0 ] || exit 1
echo "无 FAIL。下一步: ./scripts/build.sh"
