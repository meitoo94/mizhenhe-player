#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键替换全工程的产品品牌名，并热更新本机 8790 服务。

    python server/rename_brand.py --name "米帧盒播放器" --rename-files   # 只用中文名，不留英文
    python server/rename_brand.py --name "映格播放器" --en "YINGGE"     # 需要英文名时
    python server/rename_brand.py --revert            # 回退到上一次改名前的完整原文

覆盖：皮肤配置、H5 Demo、单机版、后台、渠道手册、资源清单、界面原型、归因文档、
      服务端默认品牌、两份自检脚本的断言；改完自动 POST /admin/save 热更新。
不改：evidence/、research/、本脚本自身、命名与上架风险对照.html（讨论文档需保留原名）。
文件名保持不变，避免打断已有链接；可用 --rename-files 一并改文件名与引用。
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY = os.path.join(ROOT, "server", "state", "brand_history.json")
BASE = "http://127.0.0.1:8790"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "admin-local")

def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def atomic_write(rel, text):
    path = os.path.join(ROOT, rel)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def current_brand():
    """以当前皮肤里的品牌为基准，脚本不写死任何产品名，可反复改名。"""
    p = json.loads(read("server/profiles/wanneng_rebrand.json"))
    n = p.get("normal", {})
    cn = n.get("brand", "")
    disp = p.get("meta", {}).get("display_name", "")
    en = disp[len(cn):].strip() if cn and disp.startswith(cn) else ""
    return cn, en, n.get("tagline", "")


OLD_CN, OLD_EN, OLD_TAGLINE = current_brand()

FILES = [
    "server/profiles/wanneng_rebrand.json",
    "demo/index.html",
    f"{OLD_CN}-单机版演示.html",
    "admin/index.html",
    "真机测试与渠道展示操作手册.html",
    "万能云播放器换皮复刻-服务资源清单与部署方案.html",
    f"design/{OLD_CN}-界面原型.html",
    "百度推广API归因对接方案.html",
    "server/app.py",
    "server/test_wanneng.py",
    "server/test_admin.py",
    "landing/index.html",
]
RENAME_PAIRS = [
    (f"{OLD_CN}-单机版演示.html", "{cn}-单机版演示.html"),
    (f"design/{OLD_CN}-界面原型.html", "design/{cn}-界面原型.html"),
]


def hot_reload():
    """把磁盘上的 profile 推给正在跑的服务（与后台「保存」走同一条路径）。"""
    try:
        prof = json.loads(read("server/profiles/wanneng_rebrand.json"))
        state = json.load(urllib.request.urlopen(BASE + "/admin/state", timeout=3))
        body = json.dumps({"token": ADMIN_TOKEN, "profile": prof,
                           "display": state["display"]}).encode("utf-8")
        req = urllib.request.Request(BASE + "/admin/save", data=body,
                                     headers={"Content-Type": "application/json"})
        resp = json.load(urllib.request.urlopen(req, timeout=5))
        brand = json.load(urllib.request.urlopen(
            BASE + "/demo/config?ch=wn_douyin_a", timeout=3))["config"]["normal"]["brand"]
        return f"热更新成功 {resp} · 下发 brand={brand}"
    except Exception as e:
        return f"服务未运行或未响应（{type(e).__name__}: {e}）；改动已落盘，启动后自动加载"


def snapshot(files):
    return {rel: read(rel) for rel in files}


def do_rename(cn, en, tagline, rename_files):
    if os.path.exists(HISTORY):
        print("已存在改名记录，请先执行 --revert 再改；避免叠加替换。")
        return 2
    targets = [f for f in FILES if os.path.exists(os.path.join(ROOT, f))]
    if rename_files:
        for old, _ in RENAME_PAIRS:
            if os.path.exists(os.path.join(ROOT, old)) and old not in targets:
                targets.append(old)
    original = snapshot(targets)
    # 只替换用户可见的品牌字样。meta.name 是内部皮肤标识，不属于商店名称。
    pairs = [(OLD_TAGLINE, tagline), (OLD_CN, cn)]
    if OLD_EN:
        if en:
            pairs.append((OLD_EN, en))
        else:
            # 不要英文名时，连中英混排的分隔符一起清掉，避免留下
            # "米帧盒播放器 " / "米帧盒播放器 / " 这种尾空格和悬空斜杠。
            for sep in (" / ", " · ", "/", " "):
                pairs.append((sep + OLD_EN, ""))
            pairs.append((OLD_EN, ""))
    changed = {}
    for rel, text in original.items():
        new = text
        for a, b in pairs:
            if a and a != b:
                new = new.replace(a, b)
        if new != text:
            changed[rel] = (text.count(OLD_CN) + text.count(OLD_EN)
                            + text.count(OLD_TAGLINE))
            atomic_write(rel, new)

    renamed = []
    if rename_files:
        for old, tpl in RENAME_PAIRS:
            new_rel = tpl.format(cn=cn)
            op, np = os.path.join(ROOT, old), os.path.join(ROOT, new_rel)
            if not os.path.exists(op):
                continue
            os.replace(op, np)
            renamed.append((old, new_rel))
            for rel in targets:
                p = os.path.join(ROOT, rel)
                if os.path.exists(p):
                    t = read(rel)
                    # 文件名里的品牌字样已在上面的文本替换阶段改过了，这里只补
                    # 完整路径的差异；不能再按旧 basename 替换一次，否则会出现
                    # 「米米帧盒播放器-界面原型.html」这种叠前缀。
                    t2 = t.replace(old, new_rel)
                    if t2 != t:
                        atomic_write(rel, t2)

    with open(HISTORY, "w", encoding="utf-8") as f:
        json.dump({"ts": datetime.now().isoformat(timespec="seconds"),
                   "old": {"cn": OLD_CN, "en": OLD_EN, "tagline": OLD_TAGLINE},
                   "new": {"cn": cn, "en": en, "tagline": tagline},
                   "files": original, "renamed": renamed}, f, ensure_ascii=False)

    print(f"品牌已改为「{cn + (' ' + en if en else '')}」，共改写 {len(changed)} 个文件、"
          f"{sum(changed.values())} 处品牌字样：")
    for rel, n in changed.items():
        print(f"  {rel}  ({n})")
    if renamed:
        for a, b in renamed:
            print(f"  文件名 {a} -> {b}")
    print(hot_reload())
    print("回退：python server/rename_brand.py --revert")
    return 0


def do_revert():
    if not os.path.exists(HISTORY):
        print("没有可回退的改名记录。")
        return 2
    with open(HISTORY, encoding="utf-8") as f:
        h = json.load(f)
    # 必须先把改过名的文件挪回原路径，再写回 snapshot 原文；
    # 反过来做会让「改名后的内容」覆盖掉刚恢复的原文（单机版曾因此残留临时名）。
    for old, new in reversed(h.get("renamed", [])):
        op, np = os.path.join(ROOT, old), os.path.join(ROOT, new)
        if os.path.exists(np):
            os.replace(np, op)
    for rel, text in h["files"].items():
        atomic_write(rel, text)
    os.remove(HISTORY)
    _old = h["old"]
    _label = _old["cn"] + (" " + _old["en"] if _old.get("en") else "")
    print(f"已回退为「{_label}」，恢复 {len(h['files'])} 个文件原文。")
    print(hot_reload())
    return 0


def main():
    ap = argparse.ArgumentParser(description="替换全工程产品品牌名")
    ap.add_argument("--name", "-n", help="新中文名，例如 格影播放器")
    ap.add_argument("--en", "-e", default="", help="新英文/拉丁名，留空则只用中文")
    ap.add_argument("--tagline", "-t", default="本地影像 · 归档 · 调色 · 拼帧", help="新 slogan")
    ap.add_argument("--rename-files", action="store_true", help="同时改文件名与引用")
    ap.add_argument("--revert", action="store_true", help="回退上一次改名")
    a = ap.parse_args()
    if a.revert:
        return do_revert()
    if not a.name:
        ap.print_help()
        return 2
    return do_rename(a.name.strip(), a.en.strip(), a.tagline.strip(), a.rename_files)


if __name__ == "__main__":
    sys.exit(main())
