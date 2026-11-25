#!/usr/bin/env bash
# 上传 IPA 到 TestFlight / App Store Connect
# 前置: .p8 放在 ~/.appstoreconnect/private_keys/AuthKey_<KEY_ID>.p8
# 用法: ASC_KEY_ID=XXXXXXXXXX ASC_ISSUER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx ./scripts/upload.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

: "${ASC_KEY_ID:?环境变量 ASC_KEY_ID 未设置（App Store Connect > 用户和访问 > 密钥）}"
: "${ASC_ISSUER_ID:?环境变量 ASC_ISSUER_ID 未设置}"

IPA="${1:-}"
if [ -z "$IPA" ]; then
  IPA="$(ls -1t build/ipa/*.ipa 2>/dev/null | head -1 || true)"
fi
[ -n "$IPA" ] && [ -s "$IPA" ] || { echo "没找到 IPA，先跑 ./scripts/build.sh"; exit 1; }

KEY="$HOME/.appstoreconnect/private_keys/AuthKey_${ASC_KEY_ID}.p8"
[ -s "$KEY" ] || { echo "缺少 $KEY"; exit 1; }

echo "上传 ${IPA} (apiKey=${ASC_KEY_ID})"
if xcrun altool --upload-app --type ios -f "$IPA" \
    --apiKey "$ASC_KEY_ID" --apiIssuer "$ASC_ISSUER_ID" --verbose; then
  echo "altool 上传完成"
else
  echo "altool 不可用，改用 iTMSTransporter"
  xcrun iTMSTransporter -m upload -assetFile "$IPA" \
    -apiKey "$ASC_KEY_ID" -apiIssuer "$ASC_ISSUER_ID" -v informational
fi

echo "上传完成。到 App Store Connect > TestFlight 看构建状态（Processing 通常几分钟）。"
echo "备选：gem install fastlane && fastlane pilot upload --api_key_path fastlane/asc_key.json"
