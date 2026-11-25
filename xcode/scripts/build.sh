#!/usr/bin/env bash
# 生成 Xcode 工程 -> Release Archive -> 导出 App Store IPA
# 用法: ./scripts/build.sh   （在 xcode 目录下执行；先跑 preflight.sh）
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

PROJ="米帧盒播放器.xcodeproj"
SCHEME="米帧盒播放器"
ROOT="$(cd .. && pwd)"
BUILD="build"
mkdir -p "$BUILD/ipa"

TEAM="$(awk -F'= ' '/^MZH_TEAM_ID/{gsub(/[ \t;]/,"",$2); print $2; exit}' Config-Release.xcconfig)"
if [ -z "$TEAM" ] || [ "$TEAM" = "XXXXXXXXXX" ]; then
  echo "先把 Config-Release.xcconfig 的 MZH_TEAM_ID 换成真实 Team ID"; exit 1
fi

echo "[1/4] 生成 Xcode 工程"
xcodegen generate --spec project.yml
[ -d "$PROJ" ] || { echo "工程未生成"; exit 1; }

echo "[2/4] 写导出配置 (teamID=${TEAM})"
cp ExportOptions.plist "$BUILD/ExportOptions.plist"
/usr/libexec/PlistBuddy -c "Set :teamID ${TEAM}" "$BUILD/ExportOptions.plist"

echo "[3/4] Archive (Release / iOS 26 SDK)"
xcodebuild -project "$PROJ" -scheme "$SCHEME" \
  -configuration Release -destination 'generic/platform=iOS' \
  -archivePath "$BUILD/米帧盒播放器.xcarchive" \
  -derivedDataPath "$BUILD/DerivedData" \
  -allowProvisioningUpdates \
  clean archive

echo "[4/4] 导出 IPA"
xcodebuild -exportArchive \
  -archivePath "$BUILD/米帧盒播放器.xcarchive" \
  -exportPath "$BUILD/ipa" \
  -exportOptionsPlist "$BUILD/ExportOptions.plist" \
  -allowProvisioningUpdates

IPA="$(ls -1 "$BUILD/ipa"/*.ipa 2>/dev/null | head -1)"
if [ -n "${IPA:-}" ]; then
  echo
  echo "IPA: ${ROOT}/xcode/${IPA}"
  echo "下一步: ASC_KEY_ID=xxx ASC_ISSUER_ID=yyy ./scripts/upload.sh"
else
  echo "导出失败：看上面 xcodebuild 的报错"; exit 1
fi
