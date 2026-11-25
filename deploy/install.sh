#!/usr/bin/env bash
# 米帧盒播放器后端 · 服务器端安装脚本
# 用法（root）：bash /opt/mizhenhe/_stage/deploy/install.sh
# 幂等：重复执行只更新程序文件，不覆盖 mzh.env / 皮肤 / 运营态 / 日志。
set -u

APP=/opt/mizhenhe
STAGE="$APP/_stage"
DOMAIN=app.kqi.ink
PREFIX=/mzh
PORT=8790
SNIPPET=/etc/nginx/snippets/mizhenhe.conf
UNIT=/etc/systemd/system/mizhenhe.service

die(){ echo "  [FAIL] $*" >&2; exit 1; }
ok(){ echo "  [OK]   $*"; }

[ "$(id -u)" -eq 0 ] || die "需要 root 运行（sudo bash install.sh）"
[ -f "$STAGE/server/app.py" ] || die "未找到 $STAGE/server/app.py，请先用 deploy/upload.py 上传文件"
command -v python3 >/dev/null || die "缺少 python3"
command -v nginx >/dev/null || die "缺少 nginx"
command -v openssl >/dev/null || die "缺少 openssl"

echo "== 1. 目录与运行用户 =="
id mizhenhe >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin mizhenhe
mkdir -p "$APP"/{server/profiles,server/state,admin,demo,landing,download/ipa,signing,media,evidence,deploy/signing}
ok "目录 $APP 就绪，运行用户 mizhenhe"

echo "== 2. 同步程序文件（活数据不覆盖）=="
cp "$STAGE/server/app.py"                       "$APP/server/app.py"
[ -f "$STAGE/server/yii2_bridge.py" ] && cp "$STAGE/server/yii2_bridge.py" "$APP/server/yii2_bridge.py" || true
cp "$STAGE/admin/index.html"                    "$APP/admin/index.html"
cp "$STAGE/demo/index.html"                     "$APP/demo/index.html"
cp "$STAGE/landing/index.html"                  "$APP/landing/index.html"
cp "$STAGE/download/index.html"                 "$APP/download/index.html"
cp "$STAGE/signing/index.html"                  "$APP/signing/index.html"
# 材料进度由本地 deploy/signing/next.py 体检生成，每次部署同步最新结果
[ -f "$STAGE/server/signing_progress.json" ] && \
  cp "$STAGE/server/signing_progress.json" "$APP/server/signing_progress.json" || true
# 试看视频素材同步到 nginx /mzhmedia/ 指向的目录
for f in "$STAGE"/media/*.mp4; do [ -f "$f" ] && cp "$f" "$APP/media/"; done
# 发布状态由运营维护（TestFlight / App Store 链接），重跑部署不覆盖
[ -f "$APP/server/release_state.json" ] || \
  cp "$STAGE/server/release_state.json" "$APP/server/release_state.json"
cp "$STAGE/deploy/"*                            "$APP/deploy/" 2>/dev/null || true
[ -f "$APP/server/profiles/wanneng_rebrand.json" ] || \
  cp "$STAGE/server/profiles/wanneng_rebrand.json" "$APP/server/profiles/wanneng_rebrand.json"
ok "程序文件已同步（皮肤/state/evidence 保留原样）"

echo "== 3. 生成密钥与 Basic Auth 凭据（仅首次）=="
if [ ! -f "$APP/mzh.env" ]; then
  SEC_SWITCH=$(openssl rand -hex 24)
  SEC_ADMIN=$(openssl rand -hex 16)
  SEC_BAIDU=$(openssl rand -hex 24)
  cat > "$APP/mzh.env" <<EOF
SWITCH_SECRET=$SEC_SWITCH
ADMIN_TOKEN=$SEC_ADMIN
BAIDU_API_SECRET=$SEC_BAIDU
PUBLIC_PREFIX=$PREFIX
MZH_PUBLIC=1
EOF
  chmod 600 "$APP/mzh.env"
  ok "mzh.env 已生成（HMAC 密钥 / 后台令牌 / 百度回传密钥）"
else
  ok "mzh.env 已存在，保留"
fi

if [ ! -f "$APP/ADMIN_ACCESS.txt" ]; then
  ADM_USER=mzhadmin
  # 允许首次部署就指定固定密码：MZH_ADMIN_PASS='xxx' ./install.sh
  ADM_PASS=${MZH_ADMIN_PASS:-$(openssl rand -hex 12)}
  cat > "$APP/ADMIN_ACCESS.txt" <<EOF
后台地址      : https://$DOMAIN$PREFIX/admin
Basic Auth 用户: $ADM_USER
Basic Auth 别名: admin（与 $ADM_USER 同一密码，两个用户名都能登录）
Basic Auth 密码: $ADM_PASS
后台写操作令牌: $(awk -F= '/^ADMIN_TOKEN=/{print $2}' "$APP/mzh.env")
                （登录后在「管理令牌」输入框填这个；/admin/state 在公网模式下不下发）
iOS 配置域名  : https://$DOMAIN$PREFIX
真机 Demo     : https://$DOMAIN$PREFIX/demo
EOF
  chmod 600 "$APP/ADMIN_ACCESS.txt"
  ok "ADMIN_ACCESS.txt 已生成"
else
  ok "ADMIN_ACCESS.txt 已存在，保留"
fi
# 已部署过的机器也能改密：MZH_ADMIN_PASS='xxx' ./install.sh（或 deploy/set_admin_password.py）
if [ -n "${MZH_ADMIN_PASS:-}" ] && [ -f "$APP/ADMIN_ACCESS.txt" ]; then
  python3 - "$APP/ADMIN_ACCESS.txt" "$MZH_ADMIN_PASS" <<'PY'
import re
import sys
from pathlib import Path
p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")
s = re.sub(r"(?m)^(Basic Auth 密码:\s*).*$", lambda m: m.group(1) + sys.argv[2], s)
p.write_text(s, encoding="utf-8")
p.chmod(0o600)
PY
  ok "已按 MZH_ADMIN_PASS 更新 ADMIN_ACCESS.txt 密码"
fi
ADM_USER=$(awk '/Basic Auth 用户/{print $NF}' "$APP/ADMIN_ACCESS.txt")
ADM_PASS=$(awk '/Basic Auth 密码/{print $NF}' "$APP/ADMIN_ACCESS.txt")
# 旧版凭据文件补上别名说明（幂等）
python3 - "$APP/ADMIN_ACCESS.txt" "$ADM_USER" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text(encoding="utf-8")
if "Basic Auth 别名:" not in s:
    s = s.replace("Basic Auth 用户: " + sys.argv[2] + "\n",
                  "Basic Auth 用户: " + sys.argv[2] + "\n" +
                  "Basic Auth 别名: admin（与 " + sys.argv[2] + " 同一密码，两个用户名都能登录）\n")
    p.write_text(s, encoding="utf-8"); p.chmod(0o600)
PY
mkdir -p /etc/nginx/snippets
ADM_HASH=$(openssl passwd -apr1 "$ADM_PASS")
printf '%s:%s\n' "$ADM_USER" "$ADM_HASH" > /etc/nginx/.htpasswd_mzh
# 用户习惯在弹框里填 admin，一并兼容（同一密码）；重跑部署也不会被抹掉。
[ "$ADM_USER" = "admin" ] || printf '%s:%s\n' admin "$ADM_HASH" >> /etc/nginx/.htpasswd_mzh
# nginx worker 通常以 www-data 运行；640 root:root 会让 auth_basic 读不到文件，
# 未认证返回 401、带认证却返回 nginx 500。必须把组设成 nginx worker 的组。
NGINX_USER=$(awk '/^[[:space:]]*user[[:space:]]/{gsub(/;/,"",$2); print $2; exit}' /etc/nginx/nginx.conf 2>/dev/null)
[ -n "$NGINX_USER" ] || NGINX_USER=www-data
if chown root:"$NGINX_USER" /etc/nginx/.htpasswd_mzh 2>/dev/null; then
  chmod 640 /etc/nginx/.htpasswd_mzh
else
  chmod 644 /etc/nginx/.htpasswd_mzh
fi
ok "Nginx Basic Auth 已写入 /etc/nginx/.htpasswd_mzh (nginx user: $NGINX_USER)"

echo "== 4. systemd 服务 =="
cp "$APP/deploy/mizhenhe.service" "$UNIT"
systemctl daemon-reload
chown -R mizhenhe:mizhenhe "$APP"
systemctl enable mizhenhe >/dev/null 2>&1
systemctl restart mizhenhe
sleep 1
systemctl is-active --quiet mizhenhe || die "mizhenhe 服务未启动，看 journalctl -u mizhenhe -n 50"
ok "mizhenhe.service 已启动并设为开机自启（127.0.0.1:$PORT）"

echo "== 5. Nginx 反代片段 =="
cp "$APP/deploy/nginx-mizhenhe.conf" "$SNIPPET"
python3 - <<'PY'
import glob, os, re, shutil, sys, time

marker = "snippets/mizhenhe.conf"
bakdir = "/opt/mizhenhe/deploy"
os.makedirs(bakdir, exist_ok=True)
# sites-enabled 会加载目录内所有文件；备份若留在这里会被当成第二份 server 配置，
# 触发 "duplicate default server"，所以备份统一放到 nginx 不加载的目录。
for old in glob.glob("/etc/nginx/sites-enabled/*.bak.mzh-*"):
    shutil.move(old, os.path.join(bakdir, os.path.basename(old)))
    print("  [OK]   已移走历史备份:", os.path.basename(old))
cands = []
for pat in ("/etc/nginx/sites-enabled/*", "/etc/nginx/conf.d/*.conf"):
    cands += [p for p in glob.glob(pat) if os.path.isfile(p) and ".bak.mzh-" not in p]
hit = None
blocks = []
for p in cands:
    txt = open(p, encoding="utf-8", errors="ignore").read()
    if marker in txt:
        print("  [OK]   已存在 include，跳过：", p)
        sys.exit(0)
    if "app.kqi.ink" not in txt:
        continue
    # 括号配对切出完整 server block
    i, found = 0, []
    while True:
        m = re.search(r"\bserver\s*\{", txt[i:])
        if not m:
            break
        start = i + m.start()
        j, depth = i + m.end(), 1
        while j < len(txt) and depth:
            if txt[j] == "{": depth += 1
            elif txt[j] == "}": depth -= 1
            j += 1
        found.append((start, j, txt[start:j]))
        i = j
    for start, end, body in found:
        if "app.kqi.ink" in body:
            hit = (p, txt, start, end, body)
            if re.search(r"listen\s+[^;]*443", body):
                break
if not hit:
    sys.exit("  [FAIL] 未找到 server_name 含 app.kqi.ink 的 nginx server block")
p, txt, start, end, body = hit
indent = "    "
pos = end - 1                      # server block 结尾的 "}" 之前
newtxt = txt[:pos] + indent + "include " + marker + ";  # MZH backend\n" + txt[pos:]
bak = os.path.join(bakdir, os.path.basename(p) + ".bak.mzh-" + time.strftime("%Y%m%d%H%M%S"))
shutil.copy2(p, bak)
open(p, "w", encoding="utf-8").write(newtxt)
print("  [OK]   已插入 include ->", p, "备份:", os.path.basename(bak))
open("/opt/mizhenhe/deploy/.nginx_target", "w").write(p + "\n" + bak + "\n")
PY
[ $? -eq 0 ] || die "插入 nginx include 失败"

if ! nginx -t >/tmp/mzh_nginx_test.log 2>&1; then
  cat /tmp/mzh_nginx_test.log >&2
  if [ -f "$APP/deploy/.nginx_target" ]; then
    TARGET=$(sed -n '1p' "$APP/deploy/.nginx_target")
    BAK=$(sed -n '2p' "$APP/deploy/.nginx_target")
    [ -f "$BAK" ] && cp "$BAK" "$TARGET" && echo "  已回滚 nginx 配置" >&2
  fi
  die "nginx -t 未通过，已回滚"
fi
systemctl reload nginx
ok "nginx -t 通过并已 reload"

echo "== 6. 部署自检 =="
LOCAL=$(curl -fsS -m 5 "http://127.0.0.1:$PORT/health" 2>/dev/null || true)
echo "$LOCAL" | grep -q '"ok": true' && ok "本机 /health 正常" || echo "  [WARN] 本机 /health 无响应"
PUB=$(curl -fsS -m 8 -k "https://$DOMAIN$PREFIX/health" 2>/dev/null || true)
echo "$PUB" | grep -q '"ok": true' && ok "公网 https://$DOMAIN$PREFIX/health 正常" || echo "  [WARN] 公网 /health 暂不可达，检查安全组/证书"
DL=$(curl -fsS -m 8 -k "https://$DOMAIN$PREFIX/download" 2>/dev/null || true)
echo "$DL" | grep -q 'window.MZH_RELEASE' && ok "下载页 /download 正常" || echo "  [WARN] 下载页未就绪（见 journalctl）"
DLS=$(curl -fsS -m 8 -k "https://$DOMAIN$PREFIX/download/state" 2>/dev/null || true)
echo "$DLS" | grep -q '"download_url"' && ok "发布状态 /download/state 正常" || echo "  [WARN] 发布状态接口未就绪"
SG=$(curl -fsS -m 8 -k "https://$DOMAIN$PREFIX/signing/state" 2>/dev/null || true)
echo "$SG" | grep -q '"mainline"' && ok "材料进度 /signing/state 正常" || echo "  [WARN] 材料进度接口未就绪"
UD=$(curl -fsS -m 8 -k "https://$DOMAIN$PREFIX/udid" 2>/dev/null || true)
echo "$UD" | grep -q 'MIZHENHE' && ok "UDID 登记页 /udid 正常" || echo "  [WARN] UDID 登记页未就绪"
UDL=$(curl -fsS -m 8 -k "https://$DOMAIN$PREFIX/udid/list" 2>/dev/null || true)
echo "$UDL" | grep -q '"devices"' && ok "UDID 接口 /udid/list 正常" || echo "  [WARN] UDID 接口未就绪"

echo
echo "==================== 部署完成 ===================="
echo "iOS 配置域名 : https://$DOMAIN$PREFIX"
echo "下载安装页  : https://$DOMAIN$PREFIX/download"
echo "出包材料清单: https://$DOMAIN$PREFIX/signing"
echo "设备登记页  : https://$DOMAIN$PREFIX/udid  (iPhone Safari 打开)"
echo "真机 Demo   : https://$DOMAIN$PREFIX/demo"
echo "管理后台    : https://$DOMAIN/site  (admin 登录 → 米帧盒播放器)"
echo "凭据文件    : $APP/ADMIN_ACCESS.txt  (chmod 600)"
echo "运行状态    : systemctl status mizhenhe"
echo "实时日志    : journalctl -u mizhenhe -f"
echo "=================================================="
