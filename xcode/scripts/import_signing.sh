#!/usr/bin/env bash
# CI / 无 Xcode 账号登录场景：把 Distribution 证书与描述文件导入临时钥匙串，走手动签名。
# 云 Mac 用 Xcode 自动签名时不需要这个脚本。
#
# 需要的环境变量（全部来自 CI secrets / 本地 shell）：
#   CERT_P12_BASE64    Apple Distribution 证书 .p12 的 base64
#   CERT_PASSWORD      .p12 导出密码
#   PROFILE_BASE64     描述文件 .mobileprovision 的 base64（Ad Hoc / App Store 均可）
#   PROFILE_UUID       描述文件里的 UUID（security cms 可查）
#   KEYCHAIN_PASSWORD  临时钥匙串密码（任意随机串）
set -euo pipefail

: "${CERT_P12_BASE64:?CERT_P12_BASE64 未设置}"
: "${CERT_PASSWORD:?CERT_PASSWORD 未设置}"
: "${PROFILE_BASE64:?PROFILE_BASE64 未设置}"
: "${PROFILE_UUID:?PROFILE_UUID 未设置}"
KEYCHAIN_PASSWORD="${KEYCHAIN_PASSWORD:-ci-$(date +%s)}"
TMP="${RUNNER_TEMP:-/tmp}"
KC="$TMP/mizhenhe-signing.keychain-db"

security create-keychain -p "$KEYCHAIN_PASSWORD" "$KC"
security set-keychain-settings -lut 21600 "$KC"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KC"

printf '%s' "$CERT_P12_BASE64" | tr -d '\r\n\t ' | base64 -d > "$TMP/dist.p12"
security import "$TMP/dist.p12" -k "$KC" -P "$CERT_PASSWORD" \
  -T /usr/bin/codesign -T /usr/bin/security
security set-key-partition-list -S apple-tool:,apple:,codesign: \
  -s -k "$KEYCHAIN_PASSWORD" "$KC" >/dev/null
security list-keychains -d user -s "$KC" $(security list-keychains -d user | tr -d '"')

PPDIR="$HOME/Library/MobileDevice/Provisioning Profiles"
mkdir -p "$PPDIR"
printf '%s' "$PROFILE_BASE64" | tr -d '\r\n\t ' | base64 -d > "$PPDIR/$PROFILE_UUID.mobileprovision"

echo "签名材料已导入临时钥匙串：$KC"
security find-identity -v -p codesigning "$KC" | sed -n '1,5p'
echo "描述文件：$PPDIR/$PROFILE_UUID.mobileprovision"
