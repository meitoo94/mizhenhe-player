#!/usr/bin/env bash
# 真机联调：编译 Debug 包、安装到 USB 连接的 iPhone 并启动
# 用法: ./scripts/device_run.sh            （自动取第一台真机）
#       ./scripts/device_run.sh <UDID>     （指定设备）
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

PROJ="米帧盒播放器.xcodeproj"
SCHEME="米帧盒播放器"
BUNDLE="$(awk -F'= ' '/^MZH_BUNDLE_ID/{gsub(/[ \t;]/,"",$2); print $2; exit}' Config-Debug.xcconfig)"

UDID="${1:-}"
if [ -z "$UDID" ]; then
  echo "可用设备（取第一台真机的 UDID）："
  xcrun xctrace list devices 2>/dev/null | grep -v Simulator | sed -n '1,20p'
  UDID="$(xcrun xctrace list devices 2>/dev/null \
          | grep -E 'iPhone|iPad' | grep -v Simulator \
          | sed -nE 's/.*\(([A-F0-9-]{20,})\).*/\1/p' | head -1)"
fi
[ -n "$UDID" ] || { echo "没检测到真机，用数据线连上并点“信任此电脑”；或直接传 UDID"; exit 1; }

[ -d "$PROJ" ] || xcodegen generate --spec project.yml

echo "编译 Debug (device=${UDID})"
xcodebuild -project "$PROJ" -scheme "$SCHEME" -configuration Debug \
  -destination "id=${UDID}" -derivedDataPath build/DerivedData \
  -allowProvisioningUpdates build

APP="$(ls -d build/DerivedData/Build/Products/Debug-iphoneos/*.app 2>/dev/null | head -1)"
[ -n "${APP:-}" ] || { echo "没找到 .app"; exit 1; }

echo "安装并启动 ${BUNDLE}"
xcrun devicectl device install app --device "$UDID" "$APP" || \
  ios-deploy --bundle "$APP" --justlaunch
xcrun devicectl device process launch --device "$UDID" "$BUNDLE" 2>/dev/null || true

echo
echo "真机联调要点："
echo "  - 手机与 Mac/服务器在同一局域网；Config-Debug.xcconfig 里填 Mac 的局域网 IP"
echo "  - 首次会弹「允许访问本地网络」，必须点允许，否则 /v1/config 拉不到"
echo "  - 验渠道：打开后台 /admin 的「⑦ 百度推广 API 归因对接」模拟一条 bbdd1 点击，"
echo "    卸载重装 App 后首启即走开屏 → 试看 → 充值闭环"
