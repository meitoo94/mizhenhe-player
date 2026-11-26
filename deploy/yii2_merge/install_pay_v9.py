# -*- coding: utf-8 -*-
"""一键部署「未注册直付 + 米帧盒专属账号 / 回跳页 / 头像」(build 9 服务端部分)。

用法：
    python deploy/yii2_merge/install_pay_v9.py --check   # 只体检，不改服务器
    python deploy/yii2_merge/install_pay_v9.py           # 备份 → 校验 → 替换 → 重启 → 自检

原则：
- 不修改 novel / browser 在售档位，不改动跑量渠道 bbdd1 的任何配置；
- 书城 /api/auth/guest、pay-return.php 原文件保持原样，只新增米帧盒专属文件；
- 所有 PHP 先 php -l，Python 先 py_compile，任一失败则整批不替换；
- 每个被改文件先备份到 /opt/mizhenhe/backup/pay-v9-<时间戳>/。
"""
from __future__ import annotations

import argparse
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
PATCH = Path(__file__).resolve().parent / "mizhenhe_vip"
HOST = os.environ.get("MZH_SSH_HOST", "43.142.72.226")
USER = os.environ.get("MZH_SSH_USER", "root")
KEY = Path.home() / ".ssh" / "ishiwenge_ed25519"
YII2 = "/var/www/yii2-novel"
APP = "/opt/mizhenhe"
STAGE = "/tmp/mzh_pay_v9_stage"

# 本地补丁文件 -> 线上目标（相对 YII2 或 APP）
YII2_FILES = {
    "mizhenhe_vip/controllers/api/MizhenheUserController.php": "controllers/api/MizhenheUserController.php",
    "mizhenhe_vip/views/api/pay-return-mizhenhe.php": "views/api/pay-return-mizhenhe.php",
    "mizhenhe_vip/web/img/avatar-mizhenhe.png": "web/img/avatar-mizhenhe.png",
    "mizhenhe_vip/models/User.php": "models/User.php",
    "mizhenhe_vip/controllers/api/PayController.php": "controllers/api/PayController.php",
}
APP_FILES = {
    "server/app.py": "server/app.py",
    "server/yii2_bridge.py": "server/yii2_bridge.py",
    "verify/index.html": "verify/index.html",
}

# 后台用户列表 / 详情：加米帧盒专属头像分支与标签（只做精确字符串替换）
ADMIN_PATCHES = {
    "views/admin/user/index.php": [
        (
            """                    <?php if ($u->getRegMode() === \\app\\models\\ChannelDisplay::APP_MODE_BROWSER): ?>
                        <img class="u-avatar u-avatar-img" src="<?= Html::encode(Url::to('@web/img/avatar-default.png')) ?>" alt="">
                    <?php else: ?>
                        <span class="u-avatar"><?= Html::encode($u->getAvatarChar()) ?></span>
                    <?php endif; ?>""",
            """                    <?php if ($u->getRegMode() === \\app\\models\\User::REG_MODE_MIZHENHE): ?>
                        <img class="u-avatar u-avatar-img" src="<?= Html::encode(Url::to('@web/img/avatar-mizhenhe.png')) ?>" alt="米帧盒">
                    <?php elseif ($u->getRegMode() === \\app\\models\\ChannelDisplay::APP_MODE_BROWSER): ?>
                        <img class="u-avatar u-avatar-img" src="<?= Html::encode(Url::to('@web/img/avatar-default.png')) ?>" alt="">
                    <?php else: ?>
                        <span class="u-avatar"><?= Html::encode($u->getAvatarChar()) ?></span>
                    <?php endif; ?>""",
        ),
        (
            """                            <?= Html::encode($u->username) ?>
                            <?php if ((int)$u->is_admin === 1): ?><span class="badge-paid">管理员</span><?php endif; ?>""",
            """                            <?= Html::encode($u->username) ?>
                            <?php if ($u->getRegMode() === \\app\\models\\User::REG_MODE_MIZHENHE): ?><span class="badge-paid">米帧盒</span><?php endif; ?>
                            <?php if ((int)$u->is_admin === 1): ?><span class="badge-paid">管理员</span><?php endif; ?>""",
        ),
    ],
    "views/admin/user/view.php": [
        (
            """        <?php if ($user->getRegMode() === \\app\\models\\ChannelDisplay::APP_MODE_BROWSER): ?>
            <img class="u-profile-avatar u-profile-avatar-img" src="<?= Html::encode(Url::to('@web/img/avatar-default.png')) ?>" alt="">
        <?php else: ?>
            <div class="u-profile-avatar"><?= Html::encode($user->getAvatarChar()) ?></div>
        <?php endif; ?>""",
            """        <?php if ($user->getRegMode() === \\app\\models\\User::REG_MODE_MIZHENHE): ?>
            <img class="u-profile-avatar u-profile-avatar-img" src="<?= Html::encode(Url::to('@web/img/avatar-mizhenhe.png')) ?>" alt="米帧盒">
        <?php elseif ($user->getRegMode() === \\app\\models\\ChannelDisplay::APP_MODE_BROWSER): ?>
            <img class="u-profile-avatar u-profile-avatar-img" src="<?= Html::encode(Url::to('@web/img/avatar-default.png')) ?>" alt="">
        <?php else: ?>
            <div class="u-profile-avatar"><?= Html::encode($user->getAvatarChar()) ?></div>
        <?php endif; ?>""",
        ),
    ],
}


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


def run(cli, cmd, timeout=240, quiet=False):
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


def mkdirs(sftp, path):
    parts, cur = path.strip("/").split("/"), ""
    for p in parts:
        cur += "/" + p
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)


def put_bytes(sftp, data: bytes, remote: str):
    mkdirs(sftp, posixpath.dirname(remote))
    with sftp.open(remote, "wb") as f:
        f.write(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只体检，不修改服务器")
    args = ap.parse_args()

    missing = [str(ROOT / f) for f in APP_FILES]
    missing += [str(PATCH.parent / f) for f in YII2_FILES]
    missing = [p for p in missing if not Path(p).is_file()]
    if missing:
        sys.exit("本地补丁文件缺失：\n  " + "\n  ".join(missing))

    cli = connect()
    print("== 连接 %s@%s 成功 ==" % (USER, HOST))
    sftp = cli.open_sftp()

    if args.check:
        run(cli, "php -v | head -1; grep -E 'MZH_UPSTREAM|PUBLIC_PREFIX' %s/mzh.env; "
                 "ls -l %s/controllers/api/MizhenheUserController.php 2>/dev/null || true" % (APP, YII2))
        sftp.close()
        cli.close()
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = "%s/backup/pay-v9-%s" % (APP, stamp)
    mkdirs(sftp, STAGE)
    mkdirs(sftp, backup)
    print("\n== 1. 备份 + 上传到暂存区 ==")
    run(cli, "mkdir -p %s" % backup, quiet=True)

    staged_php = []
    for rel, dst in YII2_FILES.items():
        data = (PATCH.parent / rel).read_bytes()
        remote = posixpath.join(STAGE, "yii2", dst)
        put_bytes(sftp, data, remote)
        live = posixpath.join(YII2, dst)
        # 备份已存在的原文件（新文件跳过）
        code, _, _ = run(cli, "test -f %s && cp -p %s %s/ || true"
                         % (live, live, backup), quiet=True)
        print("  staged %s (%d bytes)" % (dst, len(data)))
        if dst.endswith(".php"):
            staged_php.append(remote)

    for rel, dst in APP_FILES.items():
        data = (ROOT / rel).read_bytes()
        remote = posixpath.join(STAGE, "app", dst)
        put_bytes(sftp, data, remote)
        live = posixpath.join(APP, dst)
        run(cli, "test -f %s && cp -p %s %s/ || true" % (live, live, backup), quiet=True)
        print("  staged app/%s (%d bytes)" % (dst, len(data)))

    # ---- 后台两个视图：读线上原文 → 精确替换 → 暂存 ----
    for rel, pairs in ADMIN_PATCHES.items():
        live = posixpath.join(YII2, rel)
        try:
            text = sftp.open(live).read().decode("utf-8")
        except Exception as e:
            sftp.close()
            cli.close()
            sys.exit("读取 %s 失败：%s" % (live, e))
        for old, new in pairs:
            if old not in text:
                if new in text:
                    print("  [skip] %s 已是新版" % rel)
                    continue
                sftp.close()
                cli.close()
                sys.exit("补丁锚点未命中，整批中止：%s" % rel)
            text = text.replace(old, new, 1)
        remote = posixpath.join(STAGE, "yii2", rel)
        put_bytes(sftp, text.encode("utf-8"), remote)
        run(cli, "cp -p %s %s/" % (live, backup), quiet=True)
        staged_php.append(remote)
        print("  patched %s" % rel)

    # ---- 2. 校验：PHP 逐个 php -l，Python py_compile ----
    print("\n== 2. 语法校验（失败则整批不替换）==")
    bad = []
    for p in staged_php:
        code, out, err = run(cli, "php -l %s" % p, quiet=True)
        ok = code == 0 and "No syntax errors" in out
        print("  %-70s %s" % (p, "OK" if ok else "FAIL " + (out + err).strip()[:160]))
        if not ok:
            bad.append(p)
    for rel in APP_FILES:
        p = posixpath.join(STAGE, "app", rel)
        if rel.endswith(".py"):
            code, out, err = run(cli, "python3 -m py_compile %s" % p, quiet=True)
            print("  %-70s %s" % (p, "OK" if code == 0 else "FAIL " + (out + err).strip()[:160]))
            if code != 0:
                bad.append(p)
    if bad:
        sftp.close()
        cli.close()
        sys.exit("语法校验失败，线上未做任何替换：\n  " + "\n  ".join(bad))

    # ---- 3. 原子替换 ----
    print("\n== 3. 原子替换线上文件 ==")
    install_cmds = []
    for rel, dst in YII2_FILES.items():
        src = posixpath.join(STAGE, "yii2", dst)
        dst_abs = posixpath.join(YII2, dst)
        install_cmds.append("mkdir -p %s && cp %s %s.tmp && mv %s.tmp %s"
                            % (posixpath.dirname(dst_abs), src, dst_abs, dst_abs, dst_abs))
    for rel in ADMIN_PATCHES:
        src = posixpath.join(STAGE, "yii2", rel)
        dst_abs = posixpath.join(YII2, rel)
        install_cmds.append("cp %s %s.tmp && mv %s.tmp %s"
                            % (src, dst_abs, dst_abs, dst_abs))
    for rel, dst in APP_FILES.items():
        src = posixpath.join(STAGE, "app", dst)
        dst_abs = posixpath.join(APP, dst)
        install_cmds.append("mkdir -p %s && cp %s %s.tmp && mv %s.tmp %s"
                            % (posixpath.dirname(dst_abs), src, dst_abs, dst_abs, dst_abs))
    install_cmds += [
        "chown -R www-data:www-data %s/web/img/avatar-mizhenhe.png %s/controllers/api/MizhenheUserController.php %s/views/api/pay-return-mizhenhe.php 2>/dev/null || true"
        % (YII2, YII2, YII2),
        "chmod 644 %s/web/img/avatar-mizhenhe.png" % YII2,
    ]
    code, out, err = run(cli, "\n".join(install_cmds))
    if code != 0:
        sftp.close()
        cli.close()
        sys.exit("替换失败：%s %s" % (out[-500:], err[-500:]))
    print("  [OK] 全部替换完成，备份在 %s" % backup)

    # ---- 4. 重启服务 ----
    print("\n== 4. 重启 php-fpm + 米帧盒服务 ==")
    run(cli, "systemctl reload php8.3-fpm || systemctl restart php8.3-fpm; "
             "systemctl restart mizhenhe; sleep 1; "
             "systemctl is-active php8.3-fpm mizhenhe")
    sftp.close()

    # ---- 5. 自检 ----
    print("\n== 5. 线上自检 ==")
    checks = [
        "curl -s -m 10 -X POST https://app.kqi.ink/api/mizhenhe-user/register "
        "-H 'Content-Type: application/json' "
        "-d '{\"device_id\":\"MZH-V9-CHECK\",\"platform\":\"ios\",\"qd\":\"mzhtest\"}' | head -c 400",
        "curl -s -m 15 -X POST https://app.kqi.ink/mzh/v1/pay/selftest "
        "-H 'Content-Type: application/json' -d '{}' | head -c 400",
        "curl -s -m 10 -o /dev/null -w 'avatar HTTP %{http_code}\\n' "
        "https://app.kqi.ink/img/avatar-mizhenhe.png",
    ]
    for cmd in checks:
        print("  -- " + cmd[:90])
        run(cli, cmd)
    cli.close()
    print("\n[OK] 部署完成")


if __name__ == "__main__":
    main()
