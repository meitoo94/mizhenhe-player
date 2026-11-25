#!/usr/bin/env python3
"""修改线上 /mzh/admin 的 Nginx Basic Auth 密码，并实测生效。

用法：
  python deploy/set_admin_password.py --new-pass '<你的新密码>'
  python deploy/set_admin_password.py --new-pass 'xxx' --old-pass '旧密码'
  python deploy/set_admin_password.py --new-pass 'xxx' --password 'root密码'

同时更新服务器上的 /etc/nginx/.htpasswd_mzh 与 /opt/mizhenhe/ADMIN_ACCESS.txt，
reload nginx 后从公网实测：无认证 401、新密码 200、旧密码 401（给了 --old-pass 时）。
凭据通过 base64 传给远端，密码里的引号 / 空格 / $ 都不会破坏命令。
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import paramiko
except ImportError:
    sys.exit("缺 paramiko：pip install paramiko")

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = "https://app.kqi.ink/mzh/admin/state"


def connect(a):
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = dict(hostname=a.host, port=a.port, username=a.user,
              timeout=15, banner_timeout=15, auth_timeout=15)
    if a.key:
        kw["key_filename"] = str(Path(a.key).expanduser())
    elif a.password:
        kw["password"] = a.password
    else:
        default_key = Path.home() / ".ssh" / "ishiwenge_ed25519"
        if default_key.is_file():
            kw["key_filename"] = str(default_key)
    cli.connect(**kw)
    return cli


def http_status(user, password):
    req = urllib.request.Request(PUBLIC)
    if user:
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return f"ERR {type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new-pass", required=True)
    ap.add_argument("--old-pass", default="", help="仅用于验证旧密码确实失效")
    ap.add_argument("--user", default="root", help="SSH 用户名")
    ap.add_argument("--host", default="43.142.72.226")
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--password", default=os.environ.get("MZH_SSH_PASSWORD", ""))
    ap.add_argument("--key", default="")
    ap.add_argument("--admin-user", default="mzhadmin", help="Basic Auth 用户名")
    ap.add_argument("--alias", default="admin",
                    help="同时兼容的第二个用户名（用户常习惯填 admin），留空则只写主用户名")
    ap.add_argument("--out", default="evidence/admin_password_change.json")
    a = ap.parse_args()

    if not a.new_pass.strip():
        sys.exit("新密码不能为空")

    b64 = base64.b64encode(a.new_pass.encode("utf-8")).decode("ascii")
    # 远端脚本：从命令行参数还原密码，写 htpasswd + 凭据文件，校验并 reload nginx。
    cmd = (
        "set -e; "
        f'P="$(printf %s \'{b64}\' | base64 -d)"; '
        'H="$(openssl passwd -apr1 "$P")"; '
        f'printf \'%s:%s\\n\' "{a.admin_user}" "$H" > /etc/nginx/.htpasswd_mzh; '
        + (f'if [ "{a.admin_user}" != "{a.alias}" ]; then printf \'%s:%s\\n\' "{a.alias}" "$H" >> /etc/nginx/.htpasswd_mzh; fi; '
           if a.alias else '')
        + 'U="$(awk \'/^[[:space:]]*user[[:space:]]/{gsub(/;/,\"\",$2); print $2; exit}\' '
        '/etc/nginx/nginx.conf 2>/dev/null)"; [ -n "$U" ] || U=www-data; '
        'chown root:"$U" /etc/nginx/.htpasswd_mzh; chmod 640 /etc/nginx/.htpasswd_mzh; '
        'python3 -c \'import sys,re,pathlib; p=pathlib.Path("/opt/mizhenhe/ADMIN_ACCESS.txt"); '
        's=p.read_text(encoding="utf-8"); '
        'p.write_text(re.sub(r"(?m)^(Basic Auth 密码:\\s*).*$", lambda m: m.group(1)+sys.argv[1], s), encoding="utf-8"); '
        'p.chmod(0o600)\' "$P"; '
        + (('grep -q "Basic Auth 别名:" /opt/mizhenhe/ADMIN_ACCESS.txt || '
            f'printf \'Basic Auth 别名: {a.alias}（同一密码，两个用户名都能登录）\\n\' '
            '>> /opt/mizhenhe/ADMIN_ACCESS.txt; ') if a.alias else '')
        + 'nginx -t >/dev/null 2>&1; systemctl reload nginx; '
        'echo CHANGED; grep -c . /etc/nginx/.htpasswd_mzh; '
        'grep "Basic Auth 密码" /opt/mizhenhe/ADMIN_ACCESS.txt'
    )

    cli = connect(a)
    try:
        stdin, stdout, stderr = cli.exec_command(cmd, timeout=60)
        out = stdout.read().decode("utf-8", "replace").strip()
        err = stderr.read().decode("utf-8", "replace").strip()
        code = stdout.channel.recv_exit_status()
    finally:
        cli.close()
    print("远端执行：", out or err)
    if code != 0 or "CHANGED" not in out:
        sys.exit(f"改密失败 exit={code}: {err}")

    checks = []
    def check(name, got, want):
        ok = got == want
        checks.append({"name": name, "ok": ok, "got": got, "expect": want})
        print(("  PASS " if ok else "  FAIL ") + f"{name}: {got} (期望 {want})")
        return ok

    print("公网复核：")
    all_ok = check("无认证 /admin/state 返回 401", http_status("", ""), 401)
    all_ok &= check(f"主用户名 {a.admin_user} + 新密码返回 200",
                    http_status(a.admin_user, a.new_pass), 200)
    if a.alias and a.alias != a.admin_user:
        all_ok &= check(f"别名 {a.alias} + 新密码返回 200",
                        http_status(a.alias, a.new_pass), 200)
    all_ok &= check("错误密码返回 401",
                    http_status(a.admin_user, a.new_pass + "_wrong"), 401)
    if a.old_pass:
        all_ok &= check("旧密码已失效 401",
                        http_status(a.admin_user, a.old_pass), 401)

    result = {
        "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "target": PUBLIC,
        "admin_user": a.admin_user,
        "password_changed": True,
        "passed": sum(1 for c in checks if c["ok"]),
        "total": len(checks),
        "checks": checks,
    }
    outfile = ROOT / a.out
    outfile.parent.mkdir(parents=True, exist_ok=True)
    outfile.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果 {result['passed']}/{result['total']} PASS -> {a.out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
