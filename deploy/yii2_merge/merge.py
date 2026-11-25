#!/usr/bin/env python3
"""把米帧盒后台合并进 app.kqi.ink/site（PHP Yii2）同一套登录。

    python deploy/yii2_merge/merge.py              # 上线（自动备份 + 验证）
    python deploy/yii2_merge/merge.py --rollback   # 一键回滚到上线前
    python deploy/yii2_merge/merge.py --check      # 只验证当前状态

改动范围（全部可回滚、不动任何渠道数据）：
  + 新增 3 个 PHP 文件（鉴权端点 / 后台控制器 / 嵌入视图）
  ~ views/layouts/admin.php 加一行菜单（带标记，幂等）
  ~ /etc/nginx/snippets/mizhenhe.conf 把 Basic Auth 换成 auth_request
  ~ /opt/mizhenhe 下米帧盒自己的 app.py / yii2_bridge.py / 皮肤 / admin 页面
  ~ /opt/mizhenhe/mzh.env 追加 MZH_UPSTREAM_* 开关（原密钥原样保留）
"""
import argparse
import json
import os
import posixpath
import sys
import time
from pathlib import Path

try:
    import paramiko
except ImportError:
    sys.exit("缺 paramiko：pip install paramiko")

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(__file__).resolve().parent
HOST = os.environ.get("MZH_SSH_HOST", "43.142.72.226")
USER = os.environ.get("MZH_SSH_USER", "root")
KEY = str(Path.home() / ".ssh" / "ishiwenge_ed25519")

YII = "/var/www/yii2-novel"
SNIPPET = "/etc/nginx/snippets/mizhenhe.conf"
BACKUP_ROOT = "/opt/mizhenhe/backup"

# 本地文件 -> 远程路径，权限
UPLOADS = [
    ("MizhenheAuthController.php", YII + "/controllers/MizhenheAuthController.php", "www-data:www-data", 0o644),
    ("admin/MizhenheController.php", YII + "/controllers/admin/MizhenheController.php", "www-data:www-data", 0o644),
    ("views/mizhenhe/index.php", YII + "/views/admin/mizhenhe/index.php", "www-data:www-data", 0o644),
    ("../nginx-mizhenhe.conf", SNIPPET, "root:root", 0o644),
    ("../../server/yii2_bridge.py", "/opt/mizhenhe/server/yii2_bridge.py", "mizhenhe:mizhenhe", 0o644),
    ("../../server/app.py", "/opt/mizhenhe/server/app.py", "mizhenhe:mizhenhe", 0o644),
    ("../../server/profiles/wanneng_rebrand.json",
     "/opt/mizhenhe/server/profiles/wanneng_rebrand.json", "mizhenhe:mizhenhe", 0o644),
    ("../../admin/index.html", "/opt/mizhenhe/admin/index.html", "mizhenhe:mizhenhe", 0o644),
]

# 会被「就地修改」的文件，上线前逐一备份
BACKUPS = [
    YII + "/views/layouts/admin.php",
    YII + "/config/web.php",
    "/etc/nginx/sites-enabled/yii2-novel.conf",
    SNIPPET,
    "/opt/mizhenhe/mzh.env",
    "/opt/mizhenhe/server/app.py",
    "/opt/mizhenhe/server/profiles/wanneng_rebrand.json",
    "/opt/mizhenhe/admin/index.html",
]

MENU_ANCHOR = "<?= Html::a('🎨 渠道展示', ['admin/channel-display/index'], ['class' => ($isChannelDisplay ? 'active' : '')]) ?>"
MENU_MARK = "MZH MERGE BEGIN"
MENU_BLOCK = """
                <?php /* MZH MERGE BEGIN 米帧盒播放器入口（由 merge.py 插入，可回滚） */ ?>
                <?php if ($can('admin/mizhenhe/index')): ?>
                    <?= Html::a('🎬 米帧盒播放器', ['admin/mizhenhe/index'], ['class' => (strpos($route, 'admin/mizhenhe') === 0 ? 'active' : '')]) ?>
                <?php endif; ?>
                <?php /* MZH MERGE END */ ?>"""

ENV_LINES = {
    "MZH_UPSTREAM_ENABLED": "1",
    "MZH_UPSTREAM_BASE": "https://app.kqi.ink",
    "MZH_UPSTREAM_TEST_CHANNELS": "mzhtest",
    "MZH_UPSTREAM_WRITES_TEST_ONLY": "1",
    "MZH_UPSTREAM_VIP_SCOPE": "browser",
}


def connect():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = dict(hostname=HOST, port=22, username=USER, timeout=20,
              banner_timeout=20, auth_timeout=20)
    if Path(KEY).is_file():
        kw["key_filename"] = KEY
    if os.environ.get("MZH_SSH_PASSWORD"):
        kw["password"] = os.environ["MZH_SSH_PASSWORD"]
    cli.connect(**kw)
    return cli


def run(cli, cmd, quiet=False):
    _, out, err = cli.exec_command(cmd, timeout=300)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    code = out.channel.recv_exit_status()
    if not quiet:
        if o:
            print(o, end="" if o.endswith("\n") else "\n")
        if e:
            print("[stderr] " + e, end="" if e.endswith("\n") else "\n")
    return code, o, e


def mkdirs(sftp, path):
    cur = ""
    for p in path.strip("/").split("/"):
        cur += "/" + p
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)


def patch_layout(cli, backup_dir):
    """幂等在左侧菜单「渠道展示」后插入米帧盒入口。"""
    script = '''# -*- coding: utf-8 -*-
import io, os, sys
p = "/var/www/yii2-novel/views/layouts/admin.php"
s = io.open(p, encoding="utf-8").read()
mark = "MZH MERGE BEGIN"
anchor = "<?= Html::a('🎨 渠道展示', ['admin/channel-display/index'], ['class' => ($isChannelDisplay ? 'active' : '')]) ?>"
block = """
                <?php /* MZH MERGE BEGIN 米帧盒播放器入口（由 merge.py 插入，可回滚） */ ?>
                <?php if ($can('admin/mizhenhe/index')): ?>
                    <?= Html::a('🎬 米帧盒播放器', ['admin/mizhenhe/index'], ['class' => (strpos($route, 'admin/mizhenhe') === 0 ? 'active' : '')]) ?>
                <?php endif; ?>
                <?php /* MZH MERGE END */ ?>"""
if mark in s:
    print("LAYOUT already patched")
    sys.exit(0)
if anchor not in s:
    print("LAYOUT anchor not found")
    sys.exit(2)
s = s.replace(anchor, anchor + block, 1)
tmp = p + ".mzh.tmp"
io.open(tmp, "w", encoding="utf-8").write(s)
os.replace(tmp, p)
print("LAYOUT patched")
'''
    # 把脚本写到服务器临时文件再执行，避免 shell 引号问题
    sftp = cli.open_sftp()
    with sftp.open("/tmp/mzh_patch_layout.py", "w") as f:
        f.write(script.encode("utf-8"))
    sftp.close()
    code, out, err = run(cli, "python3 /tmp/mzh_patch_layout.py && chown www-data:www-data "
                         + YII + "/views/layouts/admin.php && php -l "
                         + YII + "/views/layouts/admin.php", quiet=True)
    print("LAYOUT:", out.strip(), err.strip())
    if code != 0:
        raise RuntimeError("layout patch failed: " + out + err)


def patch_env(cli):
    script = r'''
import io, os, json
p = "/opt/mizhenhe/mzh.env"
lines = io.open(p, encoding="utf-8").read().splitlines()
want = json.loads(os.environ["MZH_ENV_LINES"])
seen = set()
out = []
for ln in lines:
    k = ln.split("=", 1)[0].strip()
    if k in want:
        out.append(k + "=" + want[k]); seen.add(k)
    else:
        out.append(ln)
for k, v in want.items():
    if k not in seen:
        out.append(k + "=" + v)
tmp = p + ".mzh.tmp"
io.open(tmp, "w", encoding="utf-8").write("\n".join(out) + "\n")
os.replace(tmp, p)
os.chmod(p, 0o600)
print("ENV updated, keys:", ",".join(sorted(want)))
'''
    sftp = cli.open_sftp()
    with sftp.open("/tmp/mzh_patch_env.py", "w") as f:
        f.write(script.encode("utf-8"))
    sftp.close()
    code, out, err = run(cli, "MZH_ENV_LINES='" + json.dumps(ENV_LINES)
                         + "' python3 /tmp/mzh_patch_env.py && chown mizhenhe:mizhenhe /opt/mizhenhe/mzh.env",
                         quiet=True)
    print("ENV:", out.strip(), err.strip())
    if code != 0:
        raise RuntimeError("env patch failed: " + out + err)


def http_checks(cli):
    """基础可用性检查；任一失败即回滚。"""
    checks = [
        ("/mzh/health", 200),
        ("/mzh/demo", 200),
        ("/site/auth/login", 200),
        ("/mizhenhe-auth/index", 403),   # 未登录应为 403
        ("/mzh/admin", 302),             # 未登录应跳登录页
        ("/site", 302),                  # 原后台未被影响
        ("/api/channel/config?qd=mzhtest", 200),
    ]
    results = []
    for path, want in checks:
        cmd = ("curl -s -o /dev/null -w '%{http_code}' --max-time 15 "
               "'https://app.kqi.ink" + path + "'")
        code, out, _ = run(cli, cmd, quiet=True)
        got = out.strip()
        results.append({"path": path, "want": want, "got": got,
                        "ok": got == str(want)})
    return results


def do_merge(cli):
    ts = time.strftime("%Y%m%d-%H%M%S")
    bdir = BACKUP_ROOT + "/" + ts
    run(cli, "mkdir -p " + bdir, quiet=True)
    manifest = {"ts": ts, "files": []}
    for p in BACKUPS:
        name = "_" + p.strip("/").replace("/", "_")
        code, out, _ = run(cli, "test -e " + p + " && cp -a " + p + " " + bdir
                           + "/" + name + " && echo OK", quiet=True)
        ok = "OK" in out
        manifest["files"].append({"src": p,
                                  "bak": (bdir + "/" + name) if ok else None,
                                  "backup_ok": ok})
    run(cli, "printf '%s' '" + json.dumps(manifest) + "' > " + bdir + "/manifest.json", quiet=True)
    print("[backup]", bdir, "files:", len([x for x in manifest["files"] if x["bak"]]))

    sftp = cli.open_sftp()
    for rel, remote, owner, mode in UPLOADS:
        local = LOCAL / rel
        if not local.is_file():
            raise RuntimeError("本地缺文件 " + str(local))
        mkdirs(sftp, posixpath.dirname(remote))
        sftp.put(str(local.resolve()), remote)
        sftp.chmod(remote, mode)
        run(cli, "chown " + owner + " " + remote, quiet=True)
        print("  uploaded", remote)
    sftp.close()

    patch_layout(cli, bdir)
    patch_env(cli)

    code, out, err = run(cli, "php -l " + YII + "/controllers/MizhenheAuthController.php && php -l "
                         + YII + "/controllers/admin/MizhenheController.php && php -l "
                         + YII + "/views/admin/mizhenhe/index.php && nginx -t 2>&1", quiet=True)
    print("[syntax]", (out + err).strip()[-800:])
    if code != 0:
        raise RuntimeError("php -l / nginx -t 未通过")

    # reload 之前先确认新增的 Yii2 路由确实能解析（避免把 /mzh/admin 打成 500）
    for path, want in (("/mizhenhe-auth/index", ("403", "200")),
                       ("/site/mizhenhe/index", ("302", "200"))):
        _, o, _ = run(cli, "curl -s -o /dev/null -w '%{http_code}' --max-time 10 "
                      "'https://app.kqi.ink" + path + "'", quiet=True)
        got = o.strip()
        print("[precheck]", path, "=", got)
        if got not in want:
            raise RuntimeError("Yii2 路由预检未过：" + path + " -> " + got + "，停止上线")

    run(cli, "systemctl daemon-reload; systemctl restart mizhenhe; nginx -s reload", quiet=True)
    # 等服务起来
    for _ in range(12):
        c, o, _ = run(cli, "curl -s -o /dev/null -w '%{http_code}' --max-time 5 "
                      "http://127.0.0.1:8790/health", quiet=True)
        if o.strip() == "200":
            break
        time.sleep(0.6)

    checks = http_checks(cli)
    for c in checks:
        print(("  OK  " if c["ok"] else "  FAIL"), c["path"], "want", c["want"], "got", c["got"])
    if not all(c["ok"] for c in checks):
        print("[rollback] 验证未全过，自动回滚")
        do_rollback(cli, bdir)
        raise RuntimeError("验证未通过，已回滚")

    result = {"ok": True, "backup_dir": bdir, "checks": checks,
              "login_url": "https://app.kqi.ink/site/auth/login",
              "merged_admin_url": "https://app.kqi.ink/site/mizhenhe/index",
              "embedded_panel": "https://app.kqi.ink/mzh/admin"}
    return result


def do_rollback(cli, bdir=None):
    if not bdir:
        code, out, _ = run(cli, "ls -1 " + BACKUP_ROOT + " 2>/dev/null | sort | tail -1", quiet=True)
        bdir = BACKUP_ROOT + "/" + out.strip()
    code, out, _ = run(cli, "cat " + bdir + "/manifest.json 2>/dev/null", quiet=True)
    if not out.strip():
        raise RuntimeError("找不到备份清单 " + bdir)
    manifest = json.loads(out.strip())
    restored = 0
    for item in manifest["files"]:
        bak = item.get("bak")
        if not bak:
            continue
        # 兼容早期 manifest 里缺前导下划线的命名
        _, exists, _ = run(cli, "test -e " + bak + " && echo Y || echo N", quiet=True)
        if "Y" not in exists:
            bak = posixpath.join(bdir, "_" + item["src"].strip("/").replace("/", "_"))
        code, out, err = run(cli, "cp -a " + bak + " " + item["src"], quiet=True)
        if code == 0:
            restored += 1
        else:
            print("[rollback][WARN] 恢复失败", item["src"], out.strip(), err.strip())
    code, out, err = run(cli, "nginx -t 2>&1 && nginx -s reload && systemctl restart mizhenhe", quiet=True)
    if code != 0:
        raise RuntimeError("回滚后 nginx 校验失败：" + out + err)
    print("[rollback] 已从", bdir, "恢复", restored, "/", len(manifest["files"]), "个文件")
    return {"ok": True, "restored_from": bdir, "restored_files": restored}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--backup-dir", default="")
    args = ap.parse_args()
    cli = connect()
    try:
        if args.rollback:
            res = do_rollback(cli, args.backup_dir or None)
        elif args.check:
            checks = http_checks(cli)
            for c in checks:
                print(("  OK  " if c["ok"] else "  FAIL"), c["path"], "want", c["want"], "got", c["got"])
            res = {"ok": all(c["ok"] for c in checks), "checks": checks}
        else:
            res = do_merge(cli)
        print("\nRESULT " + json.dumps(res, ensure_ascii=False, indent=2))
        out = ROOT / "evidence" / "yii2_merge_result.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if res.get("ok") else 3
    except Exception as e:
        print("[FAIL]", type(e).__name__, str(e))
        return 2
    finally:
        cli.close()


if __name__ == "__main__":
    sys.exit(main())
