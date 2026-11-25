#!/usr/bin/env python3
"""从 Windows 把 channel-switch 后端上传到已有服务器并安装（paramiko）。

用法：
  python deploy/upload.py --check                        # 只测 SSH 能否登录 + 看环境
  python deploy/upload.py --password '你的root密码'      # 上传 + 安装
  python deploy/upload.py --key C:/path/id_ed25519      # 用私钥
  MZH_SSH_PASSWORD=xxx python deploy/upload.py          # 密码走环境变量，不落命令行

只上传程序文件；服务器上的 mzh.env / 皮肤 / 运营态 / 日志由 install.sh 保留。
"""
import argparse
import os
import posixpath
import sys
from pathlib import Path

try:
    import paramiko
except ImportError:
    sys.exit("缺 paramiko：pip install paramiko")

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "server/app.py",
    "server/yii2_bridge.py",
    "server/release_state.json",
    "server/signing_progress.json",
    "server/profiles/wanneng_rebrand.json",
    "admin/index.html",
    "demo/index.html",
    "landing/index.html",
    "download/index.html",
    "signing/index.html",
    "deploy/install.sh",
    "deploy/release_ipa.py",
    "deploy/signing/next.py",
    "deploy/signing/prepare_secrets.py",
    "deploy/nginx-mizhenhe.conf",
    "deploy/mizhenhe.service",
    "deploy/mzh.env.example",
    "deploy/verify_public.py",
]
# 试看素材（mp4）自动随包上传，不必逐个登记
if (ROOT / "media").is_dir():
    FILES += ["media/" + p.name for p in sorted((ROOT / "media").glob("*.mp4"))]
REMOTE_ROOT = "/opt/mizhenhe/_stage"


def connect(args):
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = dict(hostname=args.host, port=args.port, username=args.user,
              timeout=15, banner_timeout=15, auth_timeout=15)
    if args.key:
        kw["key_filename"] = str(Path(args.key).expanduser())
    elif args.password:
        kw["password"] = args.password
    else:
        default_key = Path.home() / ".ssh" / "ishiwenge_ed25519"
        if default_key.is_file():
            kw["key_filename"] = str(default_key)
    cli.connect(**kw)
    return cli


def run(cli, cmd, show=True):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=300)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if show:
        if out:
            print(out, end="" if out.endswith("\n") else "\n")
        if err:
            print("[stderr] " + err, end="" if err.endswith("\n") else "\n")
    return code, out, err


def sftp_mkdirs(sftp, path):
    parts, cur = path.strip("/").split("/"), ""
    for p in parts:
        cur += "/" + p
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="43.142.72.226")
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default=os.environ.get("MZH_SSH_PASSWORD", ""))
    ap.add_argument("--key", default="")
    ap.add_argument("--check", action="store_true", help="只测连接与环境，不上传")
    ap.add_argument("--no-install", action="store_true", help="只上传，不执行 install.sh")
    args = ap.parse_args()

    print(f"连接 {args.user}@{args.host}:{args.port} ...")
    try:
        cli = connect(args)
    except Exception as e:
        print(f"[FAIL] SSH 登录失败：{type(e).__name__}: {e}")
        print("  若未授权本机公钥，请在服务器 WebShell 执行 deploy/authorize_key.txt 里的一行命令；")
        print("  或用 --password 传 root 密码 / --key 指定私钥。")
        return 2
    print("[OK] SSH 已连接")
    code, out, _ = run(cli,
        "echo HOST=$(hostname); echo USER=$(whoami); python3 -V; "
        "nginx -v 2>&1; systemctl --version | head -1; "
        "ls /etc/nginx/sites-enabled/ 2>/dev/null; "
        "grep -rl 'app.kqi.ink' /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | head -5",
        show=True)

    if args.check:
        cli.close()
        return 0

    sftp = cli.open_sftp()
    sftp_mkdirs(sftp, REMOTE_ROOT)
    for rel in FILES:
        local = ROOT / rel
        if not local.is_file():
            print(f"[FAIL] 本地缺文件 {rel}")
            return 3
        remote = posixpath.join(REMOTE_ROOT, rel)
        sftp_mkdirs(sftp, posixpath.dirname(remote))
        sftp.put(str(local), remote)
        print(f"  uploaded {rel} ({local.stat().st_size} bytes)")
    # install.sh 必须以 LF 执行
    run(cli, f"chmod +x {REMOTE_ROOT}/deploy/install.sh")
    sftp.close()

    if args.no_install:
        print("仅上传完成，未执行 install.sh")
        cli.close()
        return 0

    print("\n== 执行服务器端安装 ==")
    code, out, err = run(cli, f"bash {REMOTE_ROOT}/deploy/install.sh", show=True)
    cli.close()
    if code != 0:
        print(f"[FAIL] install.sh 退出码 {code}")
        return code
    print("[OK] 部署完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
