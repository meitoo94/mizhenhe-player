#!/usr/bin/env bash
# 商店截图：6.9" iPhone（默认 iPhone-only 工程，不需要 13" iPad 截图）
# 用法: ./scripts/screenshots.sh "iPhone 17 Pro Max"
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
DEVICE="${1:-iPhone 17 Pro Max}"
OUT="build/screenshots"
mkdir -p "$OUT"

echo "可用模拟器："
xcrun simctl list devices available | grep -i iphone | sed -n '1,15p'

UDID="$(xcrun simctl list devices available | grep -F "$DEVICE" | grep -v unavailable \
        | sed -nE 's/.*\(([A-F0-9-]{36})\).*/\1/p' | head -1)"
[ -n "$UDID" ] || { echo "找不到 ${DEVICE}，把上面列表里的名字作为参数传入"; exit 1; }

xcrun simctl bootstatus "$UDID" -b || true
echo "启动完成，截 5 张（合规首页 / 片仓 / 本地播放 / 账户 / 会员说明）"
echo "真机上按需要的界面手动切页后，用下面的命令补拍："
echo "  xcrun simctl io booted screenshot ${OUT}/01_home.png"
xcrun simctl io "$UDID" screenshot "${OUT}/01_home.png"
echo "已输出 ${OUT}/01_home.png；尺寸："
sips -g pixelWidth -g pixelHeight "${OUT}/01_home.png" 2>/dev/null || true
echo "6.9\" 需要 1290x2796 / 1320x2868；若不一致可同尺寸截图补拍，或直接在真机截。"
