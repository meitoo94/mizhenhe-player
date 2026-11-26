# -*- coding: utf-8 -*-
"""一键部署「米帧盒VIP套餐」独立栏目（Yii2）+ 米帧盒服务端改读新栏目。

用法：
    python deploy/yii2_merge/install_mizhenhe_vip.py --check   # 只体检，不写
    python deploy/yii2_merge/install_mizhenhe_vip.py           # 备份 → 上传 → 校验 → 初始化 → 重启 → 自检

原则：
- 绝不修改 novel / browser 两套在售档位、绝不改跑量渠道 bbdd1；
- 只新增 scope=mizhenhe 的档位与 user.mzh_vip_expire_at 一列；
- Yii2 每个 PHP 文件先 php -l 再通过，任一个失败则整批不替换；
- 线上 mzh.env 只改 MZH_UPSTREAM_VIP_SCOPE 一行，其余密钥原样保留。
"""
import argparse
import os
import sys
import time
from pathlib import Path

try:
    import paramiko
except ImportError:
    sys.exit("缺 paramiko：pip install paramiko")

ROOT = Path(__file__).resolve().parents[2]
PATCH = Path(__file__).resolve().parent / "mizhenhe_vip"
HOST = os.environ.get("MZH_SSH_HOST", "43.142.72.226")
USER = os.environ.get("MZH_SSH_USER", "root")
KEY = Path.home() / ".ssh" / "ishiwenge_ed25519"
YII2 = "/var/www/yii2-novel"
APP = "/opt/mizhenhe"
STAGE = "/tmp/mzh_vip_patch"

YII2_FILES = [
    "models/VipPlan.php",
    "models/User.php",
    "models/RechargeOrder.php",
    "controllers/admin/VipController.php",
    "controllers/api/PayController.php",
    "views/admin/vip/index.php",
    "views/layouts/admin.php",
]
APP_FILES = [
    "server/app.py",
    "server/yii2_bridge.py",
    "admin/index.html",
    "verify/index.html",
]


def connect():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = dict(hostname=HOST, port=22, username=USER, timeout=25,
              banner_timeout=25, auth_timeout=25)
    if KEY.is_file():
        kw["key_filename"] = str(KEY)
    if os.environ.get("MZH_SSH_PASSWORD"):
        kw["password"] = os.environ["MZH_SSH_PASSWORD"]
    cli.connect(**kw)
    return cli


def run(cli, cmd, timeout=180, quiet=False):
    _, o, e = cli.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if not quiet:
        if out.strip():
            print(out.rstrip())
        if err.strip():
            print("[stderr] " + err.rstrip())
    return code, out, err


def put(sftp, local, remote):
    parent = remote.rsplit("/", 1)[0]
    # 逐层建目录（SFTP 没有 makedirs）
    parts = parent.strip("/").split("/")
    cur = ""
    for p in parts:
        cur += "/" + p
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)
    sftp.put(str(local), remote)
    return remote


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只体检，不修改服务器")
    args = ap.parse_args()

    missing = [str(PATCH / f) for f in YII2_FILES
               if not (PATCH / f).is_file()]
    missing += [str(ROOT / f) for f in APP_FILES if not (ROOT / f).is_file()]
    missing += [str(PATCH / "init_db.py")] if not (PATCH / "init_db.py").is_file() else []
    if missing:
        sys.exit("本地补丁文件缺失：\n  " + "\n  ".join(missing))

    cli = connect()
    print("== 连接 %s@%s 成功 ==" % (USER, HOST))
    if args.check:
        run(cli, "php -v | head -1; sqlite3 --version; "
                 "grep -n MZH_UPSTREAM_VIP_SCOPE %s/mzh.env; "
                 "sqlite3 %s/data/novel.db \"select scope,count(*) from vip_plan group by scope;\"" % (APP, YII2))
        cli.close()
        return

    sftp = cli.open_sftp()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run(cli, "mkdir -p %s" % STAGE, quiet=True)

    # ---- 1. 上传补丁到暂存区 + PHP 语法检查 ----
    print("\n== 1. 上传 Yii2 补丁并逐个 php -l ==")
    bad = []
    for rel in YII2_FILES:
        remote_tmp = "%s/yii2/%s" % (STAGE, rel)
        put(sftp, PATCH / rel, remote_tmp)
        code, out, _ = run(cli, "php -l %s" % remote_tmp, quiet=True)
        print("   %-45s %s" % (rel, out.strip() or ("exit=%d" % code)))
        if code != 0:
            bad.append(rel)
    if bad:
        sftp.close()
        cli.close()
        sys.exit("PHP 语法检查未通过，服务器未被修改：" + ", ".join(bad))

    # ---- 2. 备份并原子替换 ----
    print("\n== 2. 备份原文件并替换 ==")
    for rel in YII2_FILES:
        target = "%s/%s" % (YII2, rel)
        run(cli, "cp -p %s %s.bak-mzh-%s 2>/dev/null || true; "
                 "cp %s/yii2/%s %s && chmod 644 %s" %
            (target, target, stamp, STAGE, rel, target, target), quiet=True)
        print("   ", rel, "->", target)

    # ---- 3. 初始化 DB（幂等） ----
    print("\n== 3. 初始化独立档位与权益字段 ==")
    put(sftp, PATCH / "init_db.py", "%s/init_db.py" % STAGE)
    code, out, _ = run(cli, "cd %s && python3 %s/init_db.py" % (YII2, STAGE))
    if code != 0:
        print("[FATAL] DB 初始化失败，请检查上面的备份 .bak-mzh-%s" % stamp)
        sftp.close()
        cli.close()
        sys.exit(1)
    run(cli, "find %s/runtime/cache -type f -delete 2>/dev/null || true; "
             "chown -R www-data:www-data %s/runtime/cache 2>/dev/null || true" %
        (YII2, YII2), quiet=True)
    print("    schema cache 已清（60s 缓存兜底，新列立即生效）")

    # ---- 4. 上传米帧盒服务端与前端 ----
    print("\n== 4. 同步米帧盒服务端 / 验证台 / 渠道展示后台 ==")
    for rel in APP_FILES:
        put(sftp, ROOT / rel, "%s/%s" % (APP, rel))
        print("   ", rel)
    for q in sorted((ROOT / "verify").glob("qr-*.png")):
        put(sftp, q, "%s/verify/%s" % (APP, q.name))
    print("    二维码 %d 张" % len(list((ROOT / "verify").glob("qr-*.png"))))
    run(cli, "chown -R mizhenhe:mizhenhe %s/server/app.py %s/server/yii2_bridge.py "
             "%s/admin/index.html %s/verify" % (APP, APP, APP, APP), quiet=True)

    # ---- 5. 只改 env 的一行 ----
    env_py = (
        "p='%s/mzh.env'\n"
        "lines=open(p,encoding='utf-8').read().splitlines()\n"
        "out=[];found=False\n"
        "for ln in lines:\n"
        "    if ln.startswith('MZH_UPSTREAM_VIP_SCOPE='):\n"
        "        out.append('MZH_UPSTREAM_VIP_SCOPE=mizhenhe');found=True\n"
        "    else: out.append(ln)\n"
        "if not found: out.append('MZH_UPSTREAM_VIP_SCOPE=mizhenhe')\n"
        "open(p,'w',encoding='utf-8').write('\\n'.join(out)+'\\n')\n"
        "print('MZH_UPSTREAM_VIP_SCOPE=mizhenhe')\n"
    ) % APP
    run(cli, "cat > %s/set_env.py <<'PY'\n%sPY\npython3 %s/set_env.py" %
        (STAGE, env_py, STAGE), quiet=True)
    run(cli, "grep MZH_UPSTREAM_VIP_SCOPE %s/mzh.env" % APP)

    # ---- 6. 重启并自检 ----
    print("\n== 6. 重启服务并线上自检 ==")
    run(cli, "systemctl restart mizhenhe && sleep 2 && systemctl is-active mizhenhe")
    checks = [
        ("Yii2 新栏目 API", "curl -sS 'https://app.kqi.ink/api/pay/vip-plans?scope=mizhenhe'"),
        ("Yii2 浏览器套餐未变", "curl -sS 'https://app.kqi.ink/api/pay/vip-plans?scope=browser'"),
        ("验证台后台档位", "curl -sS 'https://app.kqi.ink/mzh/v1/vip/plans'"),
        ("原生 App 配置", "curl -sS -X POST -H 'Content-Type: application/json' "
         "-d '{\"idfv\":\"DEPLOY-CHECK\",\"channel\":\"mzhtest\",\"country\":\"cn\",\"build\":\"1.0\",\"install_ts\":0}' "
         "'https://app.kqi.ink/mzh/v1/config'"),
        ("H5 配置", "curl -sS 'https://app.kqi.ink/mzh/demo/config?ch=mzhtest'"),
        ("验证台页面", "curl -sS -o /dev/null -w 'HTTP %{http_code}' 'https://app.kqi.ink/mzh/verify'"),
        ("后台栏目页", "curl -sS -o /dev/null -w 'HTTP %{http_code} %{redirect_url}' "
         "'https://app.kqi.ink/site/vip/index?scope=mizhenhe'"),
    ]
    for label, cmd in checks:
        code, out, _ = run(cli, cmd, timeout=60, quiet=True)
        text = out.strip().replace("\n", " ")
        print("   %-22s %s" % (label, text[:260]))

    sftp.close()
    cli.close()
    print("\n完成。备份后缀：.bak-mzh-%s；跑量渠道与 novel/browser 档位未改动。" % stamp)


if __name__ == "__main__":
    main()
