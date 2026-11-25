#!/usr/bin/env bash
# 云 Mac 首次初始化。MacinCloud PAYG / Managed 这类「无 root」账号也能跑。
# 用法: 把整个 channel-switch/ 传到云 Mac，在远程桌面的终端里执行
#       cd channel-switch/xcode && ./scripts/cloud_mac_setup.sh
# 只做环境准备，不编译、不上传；完成后按提示打开 Xcode 登录开发者账号。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

say(){ printf '\n\033[36m== %s ==\033[0m\n' "$1"; }
ok(){ printf '  \033[32mOK\033[0m   %s\n' "$1"; }
warn(){ printf '  \033[33m注意\033[0m %s\n' "$1"; }
die(){ printf '  \033[31m停止\033[0m %s\n' "$1"; exit 1; }

say "1. 工具链（必须 Xcode 26+ / iOS 26 SDK）"
XV="$(xcodebuild -version 2>/dev/null | awk '/^Xcode/{print $2}' | head -1)"
MAJOR="${XV%%.*}"
[[ "${MAJOR:-0}" =~ ^[0-9]+$ ]] && [ "${MAJOR:-0}" -ge 26 ] \
  && ok "Xcode ${XV}" \
  || die "Xcode ${XV:-未安装} < 26，2026-04-28 起上传会被拒。换 macOS Tahoe 26.6+ 镜像，或让服务商装 Xcode 26.6"
SDKV="$(xcrun --sdk iphoneos --show-sdk-version 2>/dev/null || true)"
[[ "${SDKV%%.*}" =~ ^[0-9]+$ ]] && [ "${SDKV%%.*}" -ge 26 ] \
  && ok "iOS SDK ${SDKV}" \
  || warn "iOS SDK ${SDKV:-未找到}，Archive 前用 Xcode > Settings > Platforms 装 iOS 26 SDK"

say "2. 安装 xcodegen（不需要 sudo）"
if command -v xcodegen >/dev/null 2>&1; then
  ok "已有 $(xcodegen --version 2>/dev/null)"
else
  INSTALLED=0
  if command -v brew >/dev/null 2>&1 && brew install xcodegen >/dev/null 2>&1; then
    ok "brew install xcodegen 成功"; INSTALLED=1
  fi
  if [ "$INSTALLED" -eq 0 ]; then
    # 无 root 的托管账号：把官方预编译二进制装到用户目录，不碰 /usr/local
    TAG="$(curl -fsSL --max-time 20 \
      https://api.github.com/repos/yonaskolb/XcodeGen/releases/latest \
      | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
    TAG="${TAG:-2.42.0}"
    TMP="$(mktemp -d)"
    if curl -fsSL --max-time 120 -o "$TMP/xcodegen.zip" \
        "https://github.com/yonaskolb/XcodeGen/releases/download/${TAG}/xcodegen.zip" \
       && unzip -oq "$TMP/xcodegen.zip" -d "$TMP/out"; then
      BIN="$(find "$TMP/out" -type f -name xcodegen | head -1)"
      if [ -n "$BIN" ]; then
        mkdir -p "$HOME/.local/bin"
        cp "$BIN" "$HOME/.local/bin/xcodegen"
        chmod +x "$HOME/.local/bin/xcodegen"
        export PATH="$HOME/.local/bin:$PATH"
        ok "用户级安装 XcodeGen ${TAG} -> ~/.local/bin/xcodegen"
        echo '  若新开终端找不到命令，执行: export PATH="$HOME/.local/bin:$PATH"'
        INSTALLED=1
      fi
    fi
    rm -rf "$TMP"
  fi
  [ "$INSTALLED" -eq 1 ] || \
    warn "自动安装失败：在 MacinCloud 提工单让管理员装 XcodeGen，或改选带 root 的 Dedicated 计划"
fi

say "3. 工程文件"
for f in project.yml Info.plist Info-Debug.plist Config-Debug.xcconfig Config-Release.xcconfig \
         ExportOptions.plist Resources/PrivacyInfo.xcprivacy; do
  [ -s "$f" ] || die "缺文件 $f（上传工程时漏了）"
done
ok "工程文件齐全"
SWIFT_N="$(ls ../ios/*.swift 2>/dev/null | wc -l | tr -d ' ')"
ok "Swift 源文件 ${SWIFT_N} 个"

say "4. 签名与上传凭据"
TEAM="$(awk -F'= ' '/^MZH_TEAM_ID/{gsub(/[ \t;]/,"",$2); print $2; exit}' Config-Release.xcconfig)"
if [ -z "$TEAM" ] || [ "$TEAM" = "XXXXXXXXXX" ]; then
  warn "MZH_TEAM_ID 仍是占位，编辑 Config-Debug.xcconfig / Config-Release.xcconfig 填入 10 位 Team ID"
else
  ok "Team ID ${TEAM}"
fi
mkdir -p "$HOME/.appstoreconnect/private_keys"
if ls "$HOME/.appstoreconnect/private_keys"/AuthKey_*.p8 >/dev/null 2>&1; then
  ok "API Key 已就位"
else
  warn "把 AuthKey_<KeyID>.p8 放到 $HOME/.appstoreconnect/private_keys/ （upload.sh 用）"
fi
echo "  打开 Xcode > Settings > Accounts 登录已付费的 Apple Developer 账号（自动签名需要）。"
echo "  共享型云 Mac 用完请在 Xcode 里退出账号，并在 App Store Connect 撤销这次用的 API Key。"

say "5. 后端地址（云 Mac / TestFlight 真机最容易踩的坑）"
REL_URL="$(awk -F'= ' '/^CONFIG_BASE_URL/{gsub(/[ \t;]/,"",$2); print $2; exit}' Config-Release.xcconfig)"
DBG_URL="$(awk -F'= ' '/^CONFIG_BASE_URL/{gsub(/[ \t;]/,"",$2); print $2; exit}' Config-Debug.xcconfig)"
echo "  Release: ${REL_URL}"
echo "  Debug  : ${DBG_URL}"
case "$REL_URL" in
  *yourdomain.com*) warn "Release 域名还是占位，提审前换成你自己的 HTTPS 配置域名" ;;
esac
case "$DBG_URL" in
  *192.168.*|*10.*|*172.16.*|*127.0.0.1*)
    warn "云 Mac 在机房，访问不到你 Windows 的局域网 192.168.x.x；手机装 TestFlight 后也拉不到配置。"
    echo "        两种解法：① 正式方案已备好：把 server/app.py 布到已有域名 https://app.kqi.ink/mzh"
    echo "           （Windows 上跑 python deploy/upload.py，见《后端部署到 app.kqi.ink 操作手册》）；"
    echo "        ② 临时穿透本机 8790：cloudflared tunnel --url http://localhost:8790"
    echo "           把得到的 https://xxx.trycloudflare.com 填进 Config-Release.xcconfig 的 CONFIG_BASE_URL。"
    echo "           现在两份 xcconfig 默认已是 https://app.kqi.ink/mzh，不再需要临时穿透。"
    echo "        注意：Archive 编译本身不需要连后端，只有手机运行时才需要。" ;;
esac

say "6. 下一步"
echo "  ./scripts/preflight.sh      # 环境自检（后端不可达只告警，不算失败）"
echo "  xcodegen generate --spec project.yml"
echo "  ./scripts/build.sh          # Release Archive -> IPA"
echo "  ./scripts/upload.sh         # 上传 TestFlight，iPhone 装 TestFlight 无线测试"
echo "  首次编译若报 Swift 错误，把完整报错贴回来，按行修。"
