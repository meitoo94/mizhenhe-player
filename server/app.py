# -*- coding: utf-8 -*-
"""
Channel Switch Server - 渠道分流 / 远程配置 / 付费墙下发 的最小可运行实现
零第三方依赖：python app.py [--port 8787]

路由:
  GET  /lp?ch=<channel>             广告落地页(读 landing/index.html)
  GET  /go?ch=<channel>             302 跳 App Store, 同时记录点击
  POST /v1/click                    落地页点击打点(调试用; 生产由百度 API 回传)
  POST /v1/attribution/baidu/callback  百度推广 API 归因回传(click_id / IP / 设备号 → 渠道号)
  POST /v1/attribution/resolve      首次启动: 服务端按 IP/设备号 匹配回传点击并绑定渠道
  GET  /admin/attribution           归因记录 / 设备绑定 / 参数(后台面板)
  POST /admin/attribution/config    修改归因参数与「计划ID → 渠道号」映射
  POST /admin/attribution/unbind    手动解绑设备
  POST /admin/attribution/clear     清空测试归因数据
  POST /v1/attribution              兼容旧入口(内部转百度归因, 不再以剪贴板为准)
  POST /v1/config                   按渠道下发配置; 不传 channel 时自动按绑定解析
  POST /v1/order                    创建 VIP 订单(返回自建订单页 + 收银台 URL)
  GET  /order/<order_no>            自建订单页: 交易进行中 / 订单编号 / 金额 / 重新支付
  POST /v1/login                    手机号免验证码登录
  POST /v1/account/delete           App 内账号注销(审核指南 5.1.1(v))
  GET  /admin/account/deletions     注销台账(后台面板)
  POST /v1/vip/status               查询会员状态
  GET  /admin                       渠道展示管理后台(可视化编辑, 即时生效)
  GET  /admin/state                 后台完整状态(皮肤 + 展示开关 + 审计日志)
  POST /admin/save                  保存整份配置(原子写盘 + 热更新)
  POST /admin/preview               预览某渠道真实下发的配置
  POST /admin/killswitch            全局一键关闭所有付费墙
  GET  /demo                        手机浏览器可交互演示(真机体验)
  GET  /demo/config?ch=<channel>    Demo 用的真实配置(已应用展示开关)
  GET  /download                    软件下载/安装入口(免安装版 + 原生包状态 + 二维码)
  GET  /download/state              发布状态 JSON(改 server/release_state.json 即生效)
  GET  /download/ipa/<name>.ipa     已上传的安装包(把 ipa 放进 download/ipa/ 自动出现)
  GET  /download/manifest.plist     Ad Hoc 扫码直装的 OTA 清单
  GET  /signing                     出包材料清单与实时进度
  GET  /signing/state               材料进度 JSON(由 deploy/signing/next.py 生成)
  GET  /admin/channels              渠道表(只读)
  GET  /admin/logs                  最近点击/配置决策日志(只读)
  GET  /admin/bridge                原后台(Yii2)接口接入状态
  POST /admin/bridge/probe          只读体检原后台接口(不改任何渠道)

原后台接口接入(可选, 默认关闭)
  MZH_UPSTREAM_ENABLED=1           启用 Yii2 /api/* 适配
  MZH_UPSTREAM_BASE=https://app.kqi.ink
  MZH_UPSTREAM_TEST_CHANNELS=mzhtest    写操作只放行新建测试渠道
  MZH_UPSTREAM_WRITES_TEST_ONLY=1       上线后改 0 才对所有渠道放行
"""
import argparse
import base64
import copy
import hashlib
import hmac
import json
import os
import base64
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

# ---- Yii2 原后台（app.kqi.ink/site）接口适配层 -----------------------------
# 默认关闭；MZH_UPSTREAM_ENABLED=1 启用。写操作默认只对新建测试渠道放行，
# 绝不往在跑量的 bbdd* / mt* / default 渠道写数据，也不改 Yii2 源码与配置。
try:
    from yii2_bridge import get_bridge, upstream_headers, BridgeError
    BRIDGE = get_bridge()
except Exception:
    BRIDGE = None

    class BridgeError(Exception):
        pass

    def upstream_headers(_handler):
        return {}

BRIDGE_PROBE = {"ts": 0, "channel": "", "result": []}

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(ROOT)
EVIDENCE = os.path.join(PROJ, "evidence")
LANDING = os.path.join(PROJ, "landing", "index.html")
RELEASE_STATE = os.path.join(ROOT, "release_state.json")
SIGNING_PROGRESS = os.path.join(ROOT, "signing_progress.json")
SIGNING_PAGE = os.path.join(PROJ, "signing", "index.html")
DOWNLOAD_DIR = os.path.join(PROJ, "download")
os.makedirs(EVIDENCE, exist_ok=True)

SECRET = os.environ.get("SWITCH_SECRET", "demo-secret-change-in-production").encode("utf-8")
APPSTORE_URL = "https://apps.apple.com/cn/app/id6823541080"

# 米帧盒 VIP 档位唯一的后台配置入口：Yii2「支付相关管理 → 米帧盒VIP套餐」。
# 原生 App 与 H5 演示都从这里读，渠道展示后台的「④ 套餐展示与价格」不再生效。
VIP_ADMIN_URL = os.environ.get(
    "MZH_VIP_ADMIN_URL", "https://app.kqi.ink/site/vip/index?scope=mizhenhe")

# ---------------------------------------------------------------- 渠道配置表
# mode 只有两种: normal(合规工具态) / paywall(倒计时+视频+VIP)
# 判定全部在服务端, 客户端二进制里两套 UI 都在, 但不带任何硬编码渠道判断
CHANNELS = {
    "organic": {
        "enabled": False, "mode": "normal", "desc": "自然量 / 商店直接搜索 / 审核流量",
    },
    "douyin_1125_a": {
        "enabled": True,
        "mode": "paywall",
        "desc": "投放渠道 A - 视频信息流",
        "guard": {
            "allow_countries": ["cn"],
            "block_builds": [],
            "block_ip_prefixes": [],
            "start_after_hours": 0.0,     # 安装后延迟开启, 可设 24~72 避开安装即审
            "active_window": None,        # [起始小时, 结束小时] UTC, None=全天
            "rollout": 1.0,               # 灰度比例 0.0~1.0
        },
        "paywall": {
            "splash": {
                "title": "专业视频播放",
                "subtitle": "精彩视频尽情观看",
                "button": "进入",
                "countdown_seconds": 6,
            },
            "trial": {
                "video_url": "https://cdn.example.com/v/trial_15s.mp4",
                "poster_url": "https://cdn.example.com/img/poster.jpg",
                "countdown_seconds": 15,
                "vip_button": "开通会员",
                "hint": "试看视频为演示资源，开通 VIP 解锁全部功能",
                "end_title": "试看结束",
                "end_body": "开通会员即可解锁完整内容",
                "end_primary": "开通会员",
                "end_secondary": "稍后再说",
            },
            "vip": {
                "title": "棱镜会员",
                "packages": [
                    {"id": "vip_3y", "name": "3年", "price": 198.00, "origin_price": 598.00,
                     "perk": "网络资源 20000 集 / 私密播放 / 一键字幕 100 次", "tag": "超值推荐"},
                    {"id": "vip_1y", "name": "年度", "price": 158.00, "origin_price": 398.00,
                     "perk": "网络资源 15000 集 / 私密播放 / 一键字幕 80 次", "tag": ""},
                    {"id": "vip_1m", "name": "月度", "price": 98.00, "origin_price": 128.00,
                     "perk": "网络资源 10000 集 / 私密播放 / 一键字幕 50 次", "tag": ""},
                ],
                "button": "支付宝支付",
                "agreements": ["《用户协议》", "《隐私政策》"],
            },
            "checkout": {
                "method": "alipay_h5",
                "order_endpoint": "http://127.0.0.1:8787/v1/order",
                "cashier_base": "https://mclient.alipay.com",
                "support_url": "https://www.example.com/support",
            },
        },
    },
    "gdt_1125_b": {
        "enabled": True,
        "mode": "paywall",
        "desc": "投放渠道 B - 灰度 30% / 安装 24 小时后开启",
        "guard": {
            "allow_countries": ["cn"],
            "block_builds": ["1.0.0"],
            "block_ip_prefixes": [],
            "start_after_hours": 24.0,
            "active_window": None,
            "rollout": 0.30,
        },
        "paywall": {
            "splash": {"title": "专业视频播放", "subtitle": "精彩视频尽情观看",
                       "button": "进入", "countdown_seconds": 5},
            "trial": {"video_url": "https://cdn.example.com/v/trial_b.mp4",
                      "poster_url": "https://cdn.example.com/img/poster_b.jpg",
                      "countdown_seconds": 20, "vip_button": "开通会员",
                      "hint": "试看视频为演示资源，开通 VIP 解锁全部功能",
                      "end_title": "试看结束", "end_body": "开通会员即可解锁完整内容",
                      "end_primary": "开通会员", "end_secondary": "稍后再说"},
            "vip": {"title": "棱镜会员",
                    "packages": [
                        {"id": "vip_3y", "name": "3年", "price": 198.00, "origin_price": 598.00,
                         "perk": "网络资源 20000 集", "tag": "超值推荐"},
                        {"id": "vip_1m", "name": "月度", "price": 98.00, "origin_price": 128.00,
                         "perk": "网络资源 10000 集", "tag": ""},
                    ],
                    "button": "支付宝支付",
                    "agreements": ["《用户协议》", "《隐私政策》"]},
            "checkout": {"method": "alipay_h5",
                         "order_endpoint": "http://127.0.0.1:8787/v1/order",
                         "cashier_base": "https://mclient.alipay.com",
                         "support_url": "https://www.example.com/support"},
        },
    },
}

# 合规态首页文案。切换皮肤时由外部 profile 覆盖。
NORMAL_HOME = {
    "tabs": ["动界", "万象", "创研台", "我的"],
    "headline": "本地影像动感玩法",
}
PROFILE_META = {"name": "default", "source": "built-in CHANNELS",
                "default_channel": "douyin_1125_a"}
PROFILE_PATH = None          # 当前皮肤文件路径(后台保存写回这里)

# 运营态单独存放, 不污染皮肤模板: 栏目显隐 / 模块开关 / 全局熔断
STATE_DIR = os.path.join(ROOT, "state")
STATE_FILE = os.path.join(STATE_DIR, "display.json")
ATTRIB_FILE = os.path.join(STATE_DIR, "attribution.json")
DELETION_FILE = os.path.join(STATE_DIR, "account_deletions.json")
BAIDU_API_SECRET = os.environ.get("BAIDU_API_SECRET", "").encode("utf-8")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "admin-local")
DISPLAY = {"kill_switch": False, "channels": {}}


def load_profile(path):
    """加载一套完整皮肤(渠道表 + 合规首页 + 商店链接), 用于矩阵化复制。
    结构不合法直接抛错, 不静默回落内置配置, 避免误判。"""
    global CHANNELS, NORMAL_HOME, PROFILE_META, APPSTORE_URL, PROFILE_PATH
    with open(path, encoding="utf-8") as f:
        p = json.load(f)
    ch = p.get("channels")
    if not isinstance(ch, dict) or "organic" not in ch:
        raise ValueError("profile.channels 必须是 dict 且包含 organic")
    for name, cfg in ch.items():
        if not isinstance(cfg, dict) or "mode" not in cfg:
            raise ValueError(f"channel {name} 缺少 mode")
        if cfg.get("mode") == "paywall":
            pw = cfg.get("paywall") or {}
            for k in ("splash", "trial", "vip", "checkout"):
                if k not in pw:
                    raise ValueError(f"channel {name}.paywall 缺少 {k}")
            if not pw["vip"].get("packages"):
                raise ValueError(f"channel {name}.paywall.vip.packages 为空")
    CHANNELS = ch
    PROFILE_PATH = os.path.abspath(path)
    NORMAL_HOME = p.get("normal", NORMAL_HOME)
    if p.get("appstore_url"):
        APPSTORE_URL = p["appstore_url"]
    meta = p.get("meta", {})
    PROFILE_META = {
        "name": meta.get("name", os.path.basename(path)),
        "source": os.path.abspath(path),
        "target": meta.get("target", ""),
        "default_channel": p.get("default_channel", next(
            (k for k, v in ch.items() if v.get("mode") == "paywall"), "organic")),
    }
    print(f"[profile] loaded {PROFILE_META['name']} | channels={list(ch)} | "
          f"normal_tabs={NORMAL_HOME.get('tabs')} | "
          f"appstore={APPSTORE_URL}", flush=True)


# ------------------------------------------------- 渠道展示(运营态) 读写与过滤
MODULES = ("splash", "trial", "vip", "checkout", "login")


def default_display():
    """从当前皮肤派生一份「全部展示」的默认运营态。"""
    tabs = list(NORMAL_HOME.get("tabs", []))
    sections = list(NORMAL_HOME.get("home", {}).get("sections", []))
    feats = [f.get("id", f.get("name", "")) for f in NORMAL_HOME.get("craft", {}).get("features", [])]
    out = {}
    for name, cfg in CHANNELS.items():
        c = {
            "normal_tabs": list(tabs),
            "home_sections": list(sections),
            "craft_features": list(feats),
            "cloud_subtitle": True,
            "membership_entry": True,
            "paywall_modules": {m: True for m in MODULES},
            "visible_packages": [p["id"] for p in cfg.get("paywall", {}).get("vip", {}).get("packages", [])],
        }
        out[name] = c
    return {"kill_switch": False, "channels": out}


def load_display_state():
    """皮肤切换 / 新增渠道后自动补齐缺失的展示字段, 不会因为旧 state 文件丢配置。"""
    global DISPLAY
    d = default_display()
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            d["kill_switch"] = bool(saved.get("kill_switch", False))
            for name, c in (saved.get("channels") or {}).items():
                if name in d["channels"] and isinstance(c, dict):
                    merged = dict(d["channels"][name])
                    merged.update(c)
                    if "paywall_modules" in merged:
                        mm = {m: True for m in MODULES}
                        mm.update(merged["paywall_modules"] or {})
                        merged["paywall_modules"] = mm
                    d["channels"][name] = merged
        except Exception as e:
            print("[display] state load failed, using defaults:", e, flush=True)
    DISPLAY = d


def save_display_state(d):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def apply_display(channel, normal, paywall):
    """按「渠道展示」开关裁剪实际下发的配置。关掉的内容直接从响应里移除。"""
    d = DISPLAY.get("channels", {}).get(channel, {})
    n = copy.deepcopy(normal)
    if "normal_tabs" in d and "tabs" in n:
        n["tabs"] = [t for t in n["tabs"] if t in d["normal_tabs"]]
    if "home_sections" in d and isinstance(n.get("home"), dict) and "sections" in n["home"]:
        n["home"]["sections"] = [s for s in n["home"]["sections"] if s in d["home_sections"]]
    if "craft_features" in d and isinstance(n.get("craft"), dict) and "features" in n["craft"]:
        n["craft"]["features"] = [f for f in n["craft"]["features"]
                                   if f.get("id", f.get("name", "")) in d["craft_features"]]
    if not d.get("cloud_subtitle", True) and isinstance(n.get("craft"), dict):
        n["craft"].pop("cloud", None)
    if not d.get("membership_entry", True) and isinstance(n.get("mine"), dict):
        n["mine"].pop("membership", None)

    pw = copy.deepcopy(paywall) if paywall else None
    if pw:
        mods = d.get("paywall_modules") or {}
        pw["modules"] = {m: bool(mods.get(m, True)) for m in MODULES}
        vis = d.get("visible_packages")
        if vis is not None and isinstance(pw.get("vip"), dict):
            pw["vip"]["packages"] = [p for p in pw["vip"]["packages"] if p["id"] in vis]
        for m in MODULES:
            if not pw["modules"][m] and m in pw:
                pw.pop(m, None)
    return n, pw


# ---------------------------------------------------------------- 内存状态 + 落盘
LOCK = threading.Lock()
CLICKS = []          # {click_id, channel, ts, ip, ua, model, tz, lang, screen, ...}
ORDERS = {}          # order_no -> {...}
VIP = {}             # idfv -> {level, expire_at, order_no}
LOGINS = []          # 手机号登录记录(不含明文)
DECISION_LOG = []    # 每次 /v1/config 的判定明细
APP_DIAG = []        # 原生 App 上报的运行时诊断(请求/验签/解码/最终形态)
DELETIONS = []       # 账号注销台账(只有哈希与统计, 不存手机号明文)


# ---- 百度推广 API 归因(主链路): 回传点击库 + 设备绑定表 ----------------------
# 不是剪贴板: 百度后台通过 API 告诉我们「哪些 IP / 设备号 是从广告点击进来的」,
# App 首启时服务端用请求出口 IP / 上报的设备号 去匹配这批点击; 命中就绑定
# 我们自定义的渠道号(例如 bbdd1), 之后 /v1/config 按该渠道的展示配置下发。
AD_CLICKS = []           # [{click_id, source, plan_id, channel, ip, device_hash, ts, ...}]
DEVICE_BINDINGS = {}     # device_key(hash) -> {channel, method, confidence, click_id, ip, ts}
ATTRIB_CONF = {
    "source": "baidu",
    "window_seconds": 86400,          # 点击 → 首启 的归因时间窗
    "min_confidence": 0.60,
    "allow_ip_only": True,            # 允许「后台知道这个 IP 来自百度」直接命中
    "ip_only_confidence": 0.65,
    "strict_mode": False,             # True 时纯 IP 不判渠道(需设备号或 2 项非 IP 特征)
    "channel_map": {"baidu_plan_a": "bbdd1"},
    "default_channel": "organic",
}
DEVICE_FIELDS = ("idfa", "caid", "device_id", "oaid", "idfv")

# 真机验证用的渠道白名单：只能绑新建的测试渠道，跑量渠道一个都不许碰。
TEST_CHANNELS = {c.strip() for c in os.environ.get(
    "MZH_UPSTREAM_TEST_CHANNELS", "mzhtest").split(",") if c.strip()}


# ---- 手机时钟校准（仅测试渠道，解决 App 静默回落 normal） ------------------
# App 的 verify() 除了 HMAC 还要求 |手机时间 - issued_at| < 3600 秒。
# 手机时钟与服务器不一致时，配置再完整、签名再正确，App 也会静默回普通首页，
# 而服务端日志只能看到「已下发 paywall」。验证台用同一台手机上报时钟，
# 按出口 IP 记下偏移量，下发时只给测试渠道修正 issued_at 并重新签名。
# organic / bbdd1 等生产渠道完全不读这张表，签名原文保持原样。
CLOCK_FILE = os.path.join(STATE_DIR, "clock_offsets.json")
CLOCK_OFFSETS = {}          # ip -> {offset, phone_now, ts}


def load_clock_offsets():
    try:
        with open(CLOCK_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        CLOCK_OFFSETS.update({str(k): v for k, v in d.items() if isinstance(v, dict)})
    except Exception:
        pass


def save_clock_offsets():
    tmp = CLOCK_FILE + ".tmp"
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(CLOCK_OFFSETS, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CLOCK_FILE)


def client_clock_offset(ip):
    rec = CLOCK_OFFSETS.get(norm_ip(ip))
    if not rec:
        return 0
    try:
        return int(rec.get("offset", 0))
    except (TypeError, ValueError):
        return 0


def test_channel_allowed(ch):
    return str(ch or "").strip() in TEST_CHANNELS


def load_attribution_state():
    global AD_CLICKS, DEVICE_BINDINGS
    try:
        with open(ATTRIB_FILE, encoding="utf-8") as f:
            d = json.load(f)
        AD_CLICKS = d.get("clicks", [])
        DEVICE_BINDINGS = d.get("bindings", {})
        # 历史测试绑定补写 /24 网段备用键：手机 5G 出口 IP 会在同一网段内漂移，
        # 只存精确 IP 时重启一次就可能掉回 organic。仅测试渠道迁移。
        migrated = 0
        for rec in list(DEVICE_BINDINGS.values()):
            if not isinstance(rec, dict):
                continue
            if rec.get("method") == "verify_console" and rec.get("channel") in TEST_CHANNELS:
                rip = norm_ip(rec.get("ip"))
                if "." in rip:
                    k = device_hash("ip24:" + rip.rsplit(".", 1)[0])
                    if k not in DEVICE_BINDINGS:
                        DEVICE_BINDINGS[k] = dict(rec, device_key=k,
                                                  device_kind="ip24_hash")
                        migrated += 1
        if migrated:
            save_attribution_state()
            print(f"[attribution] migrated {migrated} test bindings to /24", flush=True)
        conf = d.get("config")
        if isinstance(conf, dict):
            ATTRIB_CONF.update(conf)
        print(f"[attribution] loaded clicks={len(AD_CLICKS)} "
              f"bindings={len(DEVICE_BINDINGS)}", flush=True)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[attribution] load failed: {e}", flush=True)


def save_attribution_state():
    atomic_write_json(ATTRIB_FILE, {
        "clicks": AD_CLICKS[-1000:],
        "bindings": DEVICE_BINDINGS,
        "config": ATTRIB_CONF,
    })


def device_hash(value):
    """设备号只以加盐哈希落盘, 不存明文 IDFA / CAID / OAID。"""
    if not value:
        return ""
    return hashlib.sha256((SECRET.decode() + "|" + str(value)).encode("utf-8")).hexdigest()[:24]


def device_keys(body):
    """按优先级返回 (字段名, 设备号哈希)。"""
    return [(k, device_hash(k + ":" + str(v))) for k in DEVICE_FIELDS
            if (v := body.get(k))]


def norm_ip(ip):
    return str(ip or "").strip()


def ip_cidr24(a, b):
    return "." in a and "." in b and a.rsplit(".", 1)[0] == b.rsplit(".", 1)[0]


def verify_partner_signature(body):
    """生产设置 BAIDU_API_SECRET 后, 回传必须带正确的 _sig(HMAC-SHA256)。"""
    if not BAIDU_API_SECRET:
        return True, "demo_no_secret"
    sig = body.pop("_sig", "") if isinstance(body, dict) else ""
    canon = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    expect = hmac.new(BAIDU_API_SECRET, canon.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(str(sig), expect), "hmac_sha256"


def map_channel(item):
    """渠道号由我方定义: 优先回传里带的 channel, 否则按 百度计划ID 映射。"""
    ch = str(item.get("channel") or "").strip()
    if ch:
        return ch
    plan = str(item.get("plan_id") or "").strip()
    return ATTRIB_CONF.get("channel_map", {}).get(plan, "")


def match_ad_click(body, ip):
    """百度回传点击库 × 本次首启设备 → (channel, method, confidence, detail, click)。"""
    now = now_ts()
    win = int(ATTRIB_CONF.get("window_seconds", 86400))
    khash = {h for _, h in device_keys(body)}
    with LOCK:
        pool = [c for c in AD_CLICKS
                if 0 <= now - int(c.get("ts", 0)) <= win
                and str(c.get("channel", "")) in CHANNELS]
    best = None
    for c in pool:
        score, why = 0.0, []
        if c.get("device_hash") and c["device_hash"] in khash:
            score, why = 1.0, ["device_exact"]
            if norm_ip(c.get("ip")) == norm_ip(ip):
                why.append("ip_exact")
        else:
            c_ip, o_ip = norm_ip(c.get("ip")), norm_ip(ip)
            if c_ip == o_ip:
                score += 0.45; why.append("ip_exact")
            elif ip_cidr24(c_ip, o_ip):
                score += 0.20; why.append("ip_cidr24")
            else:
                continue
            non_ip = 0
            for k, w in (("model", 0.18), ("tz", 0.08), ("lang", 0.05),
                         ("screen", 0.07), ("ua", 0.10)):
                if c.get(k) and body.get(k) and str(c[k]) == str(body[k]):
                    score += w; why.append(k); non_ip += 1
            dt = now - int(c.get("ts", 0))
            if dt <= 600:
                score += 0.20; why.append("within_10min")
            elif dt <= 3600:
                score += 0.10; why.append("within_1h")
            else:
                score += 0.05; why.append("within_window")
            why.append(f"non_ip_hits_{non_ip}")
            strict = ATTRIB_CONF.get("strict_mode") or not ATTRIB_CONF.get("allow_ip_only", True)
            if strict and non_ip < 2:
                score = min(score, 0.30)   # 严格模式: 纯 IP 不足以判渠道
            elif non_ip == 0 and ATTRIB_CONF.get("allow_ip_only", True):
                score = max(score, float(ATTRIB_CONF.get("ip_only_confidence", 0.65)))
        if best is None or score > best[0]:
            best = (score, why, c)
    if not best:
        return "organic", "no_match", 0.0, ["no_click_in_window"], None
    score, why, c = best
    need = float(ATTRIB_CONF.get("min_confidence", 0.60))
    if score < need:
        return "organic", "low_confidence", round(score, 2), why + [f"need_{need}"], c
    method = "device_exact" if "device_exact" in why else (
        "ip_plus_fingerprint" if any(x in why for x in ("model", "tz", "lang", "screen", "ua"))
        else "ip_from_partner_api")
    detail = why + [f"score_{round(score,2)}", "click_id_" + str(c.get("click_id", ""))]
    return c["channel"], method, round(score, 2), detail, c


def bind_device(body, ip, channel, method, confidence, click):
    """命中后把渠道号写到设备绑定表; 卸载重装设备号变了也能按出口 IP 找回。"""
    binding = {
        "channel": channel, "method": method, "confidence": confidence,
        "click_id": (click or {}).get("click_id", ""),
        "ip": norm_ip(ip), "ts": now_ts(),
        "source": (click or {}).get("source", "baidu"),
    }
    keys = device_keys(body)
    with LOCK:
        if keys:
            for _, h in keys[:2]:
                DEVICE_BINDINGS[h] = dict(binding, device_key=h, device_kind="hash")
        else:
            h = device_hash("ip:" + norm_ip(ip))
            DEVICE_BINDINGS[h] = dict(binding, device_key=h, device_kind="ip_hash")
    save_attribution_state()
    return binding


def resolve_bound_channel(body, ip):
    """先查已绑定设备; 设备号变了(卸载重装)才回落到同出口 IP 的绑定。"""
    with LOCK:
        for _, h in device_keys(body):
            b = DEVICE_BINDINGS.get(h)
            if b:
                return dict(b)
        h = device_hash("ip:" + norm_ip(ip))
        b = DEVICE_BINDINGS.get(h)
        return dict(b) if b else None


def test_net_binding(ip):
    """测试渠道专用：精确 IP 未命中时，允许同 /24 网段兜底。

    手机用 5G 时运营商 NAT 出口 IP 会在同一网段内变化；只对验证台写入的
    TEST_CHANNELS 绑定生效，不参与真实广告归因，跑量渠道一律返回 None。
    """
    rip = norm_ip(ip)
    if "." not in rip:
        return None
    with LOCK:
        b = DEVICE_BINDINGS.get(device_hash("ip24:" + rip.rsplit(".", 1)[0]))
    if b and b.get("channel") in TEST_CHANNELS:
        return dict(b)
    return None


# ---- 账号注销(App Store Review Guidelines 5.1.1(v)) --------------------------
# 支持注册/登录的 App 必须允许用户在 App 内发起账号删除, 且要真正删除账号与关联
# 个人数据。台账只留哈希与统计, 作为「已执行删除」的合规凭证, 不存手机号明文。
def load_deletions():
    global DELETIONS
    try:
        with open(DELETION_FILE, encoding="utf-8") as f:
            DELETIONS = json.load(f).get("deletions", [])
        print(f"[account] loaded deletions={len(DELETIONS)}", flush=True)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[account] load failed: {e}", flush=True)


def write_deletions():
    atomic_write_json(DELETION_FILE, {"deletions": DELETIONS[-500:]})


def purge_account(ticket, idfv):
    """删除该账号的登录记录 / 会员权益 / 设备归因绑定, 交易流水去标识化。"""
    with LOCK:
        matched = [r for r in LOGINS
                   if (ticket and r.get("ticket") == ticket)
                   or (idfv and r.get("idfv") == idfv)]
        if not matched:
            return None
        phone_hash = hashlib.sha256(
            matched[0].get("phone_masked", "").encode("utf-8")).hexdigest()[:16]
        LOGINS[:] = [r for r in LOGINS if r not in matched]

        idfvs = {r.get("idfv", "") for r in matched}
        if idfv:
            idfvs.add(idfv)
        vip_removed = sum(1 for k in idfvs if VIP.pop(k, None))

        order_purged = 0
        for o in ORDERS.values():
            if o.get("idfv") or o.get("phone_masked") or not o.get("personal_data_purged"):
                o.pop("idfv", None)
                o.pop("phone_masked", None)
                o["personal_data_purged"] = True
                order_purged += 1

        binding_removed = 0
        own_clicks = set()
        for k in idfvs:
            for _, h in device_keys({"idfv": k}):
                b = DEVICE_BINDINGS.pop(h, None)
                if b:
                    binding_removed += 1
                    if b.get("click_id"):
                        own_clicks.add(b["click_id"])
        # 同一次广告点击会按多个设备标识各存一条绑定（idfv / device_id / caid…），
        # 注销时按 click_id 连带清掉，避免只删一个哈希留下设备画像。
        if own_clicks:
            for h in list(DEVICE_BINDINGS):
                if DEVICE_BINDINGS[h].get("click_id") in own_clicks:
                    DEVICE_BINDINGS.pop(h, None)
                    binding_removed += 1
        save_attribution_state()

        return {"logins": len(matched), "vip": vip_removed,
                "orders_deidentified": order_purged,
                "device_bindings": binding_removed,
                "phone_hash": phone_hash}


def append_log(name, obj):
    with open(os.path.join(EVIDENCE, name + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def now_ts():
    return int(time.time())


# 配置信封签名单独用一个密钥：它必须与客户端内置的验签密钥一致，
# 否则真机永远验签失败 → 退回内置兜底皮肤（浅色空壳），连渠道链路都不生效。
# 内部 click_token / 后台写操作仍用 SWITCH_SECRET，不受此值影响。
CONFIG_SIGN_SECRET = os.environ.get("MZH_CONFIG_SECRET",
                                    "demo-secret-change-in-production").encode("utf-8")


# ---------------------------------------------------------------- 用户反馈中心
# 对接 Yii2 /site 后台「用户反馈中心」真实接口（models/UserFeedback.php）：
#   GET  /api/feedback/types    投诉类型字典（公开）
#   POST /api/feedback/upload   截图（multipart, 字段名 file）
#   POST /api/feedback/submit   {types[], content, images[], contact, device_no}
# 反馈写入 user_feedback 独立台账；不改渠道配置、不动任何跑量渠道，
# 所以这条链路不经过 BRIDGE 的「写操作只放行 mzhtest」闸门。
FEEDBACK_BASE = os.environ.get("MZH_FEEDBACK_BASE", "https://app.kqi.ink").rstrip("/")
FEEDBACK_LEDGER = os.path.join(EVIDENCE, "feedback_ledger.json")
FEEDBACK_TOKENS = {}
# 与 UserFeedback::TYPES 完全一致；上游不可达时用它保证选项仍可渲染
FEEDBACK_TYPES = [
    "包含贩毒、迷信、暴力、恐怖、政治敏感信息",
    "包括诈骗诱导信息",
    "充值会员无法使用功能",
    "需要更多功能",
    "充值后未到账/无充值记录",
    "无法充值/充值提示失败",
    "点错了误充",
    "其他",
]


def feedback_request(path, payload=None, token="", raw=None, ctype="", timeout=12):
    """标准库调用上游 /api/*，返回 (http_ok, payload)。"""
    import urllib.error
    import urllib.request
    hdr = {"Accept": "application/json", "User-Agent": "MizhenheFeedback/1.0"}
    body = raw
    if raw is None and payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdr["Content-Type"] = "application/json"
    elif raw is not None and ctype:
        hdr["Content-Type"] = ctype
    if token:
        hdr["Authorization"] = "Bearer " + token
    req = urllib.request.Request(FEEDBACK_BASE + path, data=body, headers=hdr,
                                 method="POST" if body is not None else "GET")
    # 服务器本机直连优先（避免被部署机上的全局 HTTP 代理劫持），
    # 直连不通时再退回环境变量代理；HTTP 4xx/5xx 是业务响应，不重试。
    def _open(opener):
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.read().decode("utf-8", "replace")

    try:
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        text = _open(direct)
    except Exception as e1:
        try:
            text = _open(urllib.request.build_opener())
        except Exception as e2:
            return False, {"code": -1, "msg": "upstream_unreachable",
                           "detail": (str(e1) + " | " + str(e2))[:160]}
    try:
        return True, json.loads(text)
    except Exception:
        return False, {"code": -1, "msg": "upstream_non_json", "detail": text[:160]}


def feedback_code(p):
    """上游 code=0 表示成功，不能用 `or` 兜底（0 会被当成假值）。"""
    try:
        return int(p.get("code", -1))
    except Exception:
        return -1


def feedback_token(device_no):
    """游客 token（后台允许游客提交反馈），按设备缓存 6 小时。"""
    cached = FEEDBACK_TOKENS.get(device_no)
    if cached and now_ts() - cached[1] < 6 * 3600:
        return cached[0]
    ok, p = feedback_request("/api/auth/guest",
                             {"device_id": device_no, "platform": "ios"})
    if not ok or feedback_code(p) not in (0, 200):
        return ""
    tk = str((p.get("data") or {}).get("token") or "")
    if tk:
        FEEDBACK_TOKENS[device_no] = (tk, now_ts())
    return tk


def feedback_ledger():
    try:
        with open(FEEDBACK_LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def feedback_save(device_no, rec):
    with LOCK:
        d = feedback_ledger()
        d[device_no] = rec
        tmp = FEEDBACK_LEDGER + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, FEEDBACK_LEDGER)


def hmac_sign(raw: bytes) -> str:
    return hmac.new(CONFIG_SIGN_SECRET, raw, hashlib.sha256).hexdigest()


def make_click_token(click_id, channel, ts):
    """落地页写入剪贴板的渠道凭据, 客户端首启读取, 服务端验签防止伪造"""
    body = f"{click_id}|{channel}|{ts}"
    sig = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()[:16]
    return f"CHTOK1#{body}#{sig}"


def verify_click_token(token):
    try:
        head, body, sig = token.split("#")
        if head != "CHTOK1":
            return None
        click_id, channel, ts = body.split("|")
        expect = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expect):
            return None
        if now_ts() - int(ts) > 7 * 24 * 3600:
            return None
        return {"click_id": click_id, "channel": channel, "ts": int(ts)}
    except Exception:
        return None


def parse_ua_model(ua):
    m = re.search(r"(iPhone\d+,\d+|iPad\d+,\d+)", ua)
    if m:
        return m.group(1)
    if "iPhone" in ua:
        return "iPhone"
    if "Android" in ua:
        return "Android"
    return "unknown"


def client_ip(handler):
    xff = handler.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return handler.client_address[0]


def fingerprint_score(click, first_open, ip):
    """
    概率归因打分。返回 (score, reasons, eligible)。

    关键: 同一个出口 IP(运营商 NAT / 公司 WiFi)下会有大量无关用户,
    单靠 IP 命中就会把自然量误判成投放量 —— 后果是自然量也打开付费墙。
    因此加两道硬门槛:
      1) 除 IP 外的机型/时区/语言/屏幕至少命中 2 项
      2) 总分 >= 70
    """
    score, reasons, non_ip_hits = 0, [], 0
    c_ip, o_ip = click.get("ip", ""), ip
    if c_ip == o_ip:
        score += 35; reasons.append("ip_exact")
    elif c_ip.rsplit(".", 1)[0] == o_ip.rsplit(".", 1)[0] and "." in c_ip:
        score += 15; reasons.append("ip_cidr24")
    for key, weight in (("model", 20), ("tz", 8), ("lang", 5), ("screen", 7)):
        cv, ov = click.get(key), first_open.get(key)
        if cv and ov and cv == ov:
            score += weight; reasons.append(key); non_ip_hits += 1
    dt = abs(now_ts() - click.get("ts", 0))
    if dt <= 600:
        score += 20; reasons.append("within_10min")
    elif dt <= 3600:
        score += 10; reasons.append("within_1h")
    elif dt <= 86400:
        score += 3; reasons.append("within_1d")
    reasons.append(f"non_ip_hits_{non_ip_hits}")
    eligible = non_ip_hits >= 2 and score >= 70
    return score, reasons, eligible


# ---------------------------------------------------------------- 决策引擎
def resolve(channel, ctx):
    """
    返回 (mode, reasons, paywall_or_none)
    默认回落 normal: 渠道不存在 / 未启用 / 任何守卫未通过 / 灰度外 / 请求异常
    """
    reasons = []
    if DISPLAY.get("kill_switch"):
        return "normal", ["global_kill_switch"], None
    cfg = CHANNELS.get(channel)
    if not cfg:
        return "normal", ["channel_unknown"], None
    if not cfg.get("enabled"):
        return "normal", ["channel_disabled"], None
    if cfg.get("mode") != "paywall":
        return "normal", ["channel_mode_normal"], None

    g = cfg.get("guard", {})
    country = (ctx.get("country") or "").lower()
    if country not in [c.lower() for c in g.get("allow_countries", [])]:
        return "normal", ["country_not_allowed:" + country], None
    if ctx.get("build") in g.get("block_builds", []):
        return "normal", ["build_blocked:" + str(ctx.get("build"))], None
    ip = ctx.get("ip", "")
    for p in g.get("block_ip_prefixes", []):
        if ip.startswith(p):
            return "normal", ["ip_blocked:" + p], None

    age_h = (now_ts() - int(ctx.get("install_ts", now_ts()))) / 3600.0
    need = float(g.get("start_after_hours", 0))
    if age_h < need:
        return "normal", [f"install_age_{age_h:.2f}h_lt_{need}h"], None
    reasons.append(f"install_age_ok_{age_h:.2f}h")

    win = g.get("active_window")
    if win:
        h = datetime.now(timezone.utc).hour
        if not (win[0] <= h < win[1]):
            return "normal", [f"outside_window_utc{h}"], None
        reasons.append(f"window_ok_utc{h}")

    rollout = float(g.get("rollout", 1.0))
    bucket = int(hashlib.md5((ctx.get("idfv", "") + "|" + channel).encode()).hexdigest()[:8], 16) % 10000
    if bucket >= rollout * 10000:
        return "normal", [f"rollout_miss_bucket{bucket}_need{int(rollout*10000)}"], None
    reasons.append(f"rollout_hit_bucket{bucket}")

    return "paywall", reasons, cfg.get("paywall")


def ensure_ios_packages(cfg):
    """下发前保证 paywall.vip.packages 满足原生 App 的严格 Codable 契约。

    ios/ChannelModels.swift 的 VIPPackage 把 origin_price / perk / tag 定义为
    非可选字段；后台档位（Yii2「米帧盒VIP套餐」）不含这三个键时，Swift 的
    JSONDecoder 会让整个 ConfigPayload 解码失败，App 静默回落 normal 首页，
    表现就是「服务端已切 paywall，但 App 还是普通播放器」。
    """
    pw = cfg.get("paywall") if isinstance(cfg, dict) else None
    vip = pw.get("vip") if isinstance(pw, dict) else None
    pkgs = vip.get("packages") if isinstance(vip, dict) else None
    if not isinstance(pkgs, list):
        return cfg
    fixed = []
    for p in pkgs:
        if not isinstance(p, dict):
            continue
        q = dict(p)
        for k in ("price", "origin_price"):
            try:
                q[k] = float(q.get(k) or 0)
            except (TypeError, ValueError):
                q[k] = 0.0
        try:
            days = int(q.get("days") or 0)
        except (TypeError, ValueError):
            days = 0
        q.setdefault("perk", ("终身有效 · 解锁全部功能" if days >= 3650
                              else ("有效期 %d 天 · 解锁全部功能" % days if days > 0
                                    else "开通即解锁全部功能")))
        q["tag"] = str(q.get("tag") or q.get("badge") or "")
        for k in ("id", "name", "perk"):
            q[k] = str(q.get(k) or "")
        fixed.append(q)
    vip["packages"] = fixed
    return cfg


# 与 ios/ChannelModels.swift 的非可选字段一一对应；路径 → 必填键。
IOS_CONTRACT = {
    "": ["schema", "mode", "channel", "issued_at", "ttl"],
    "normal": ["brand", "tagline", "headline", "tabs", "theme", "home",
               "library", "craft", "mine"],
    "normal.theme": ["primary", "accent", "background", "surface", "text",
                     "muted", "corner", "tab_icons"],
    "normal.home": ["stats", "empty_library", "channels", "sections", "empty_tips"],
    "normal.library": ["search_placeholder", "view_modes", "bulk_actions",
                       "empty_title", "import_hint", "import_button"],
    "normal.craft": ["headline", "features", "cloud"],
    "normal.craft.cloud": ["name", "desc", "items"],
    "normal.mine": ["account", "account_hint", "membership", "links"],
    "normal.mine.membership": ["name", "placeholder", "maps_to"],
    "paywall": ["splash", "trial", "vip", "checkout"],
    "paywall.splash": ["title", "subtitle", "button", "countdown_seconds"],
    "paywall.trial": ["video_url", "poster_url", "countdown_seconds", "vip_button",
                      "hint", "end_title", "end_body", "end_primary", "end_secondary"],
    "paywall.vip": ["title", "packages", "button", "agreements"],
    "paywall.checkout": ["method", "order_endpoint", "cashier_base", "support_url"],
    "paywall.gate": ["once_per_install", "allow_skip", "skip_button",
                     "home_after_skip", "reopen_from"],
    "paywall.login": ["enabled", "method", "confirm_phone_input", "placeholder",
                      "confirm_placeholder", "submit", "agreements", "success_toast"],
}
IOS_PACKAGE_KEYS = ["id", "name", "price", "origin_price", "perk", "tag"]


def ios_contract_issues(cfg):
    """返回会让 App 解码失败的字段清单（空列表 = 兼容）。"""
    issues = []
    for path, keys in IOS_CONTRACT.items():
        obj = cfg
        if path:
            for seg in path.split("."):
                obj = obj.get(seg) if isinstance(obj, dict) else None
                if obj is None:
                    break
        if obj is None:
            # normal / paywall 是可选的，缺失不报错
            if path.split(".")[0] in ("normal", "paywall"):
                continue
            issues.append(path + " 对象缺失")
            continue
        if not isinstance(obj, dict):
            issues.append(path + " 不是对象")
            continue
        for k in keys:
            if k not in obj:
                issues.append((path + "." if path else "") + k + " 缺失")
        if path == "normal.craft":
            for i, f in enumerate(obj.get("features") or []):
                for k in ("id", "name", "desc", "icon", "maps_to"):
                    if isinstance(f, dict) and k not in f:
                        issues.append("normal.craft.features[%d].%s 缺失" % (i, k))
    pw = cfg.get("paywall") if isinstance(cfg, dict) else None
    vip = pw.get("vip") if isinstance(pw, dict) else None
    if isinstance(vip, dict):
        for i, p in enumerate(vip.get("packages") or []):
            if not isinstance(p, dict):
                issues.append("paywall.vip.packages[%d] 不是对象" % i)
                continue
            for k in IOS_PACKAGE_KEYS:
                if k not in p:
                    issues.append("paywall.vip.packages[%d].%s 缺失" % (i, k))
            for k in ("price", "origin_price"):
                if k in p and not isinstance(p[k], (int, float)):
                    issues.append("paywall.vip.packages[%d].%s 类型不符" % (i, k))
    return issues


def build_config(channel, mode, paywall, ctx):
    normal, pw = apply_display(channel, NORMAL_HOME, paywall)
    payload = {
        "schema": 1,
        "mode": mode,
        "channel": channel,
        "issued_at": now_ts(),
        "ttl": 600,
        "normal": normal,
    }
    if mode == "paywall" and pw:
        # 深拷贝: 避免把请求 Host 写回全局渠道表(会污染后续配置与签名)
        scheme = ctx.get("scheme", "http")
        host = ctx.get("host", "127.0.0.1:8787")
        # 生产反代挂在子路径(如 https://app.kqi.ink/mzh)时，
        # 下发的下单地址必须带回该前缀，否则会打到 PHP 站点根路径。
        base = f"{scheme}://{host}{ctx.get('prefix', '')}"
        if isinstance(pw.get("checkout"), dict):
            pw["checkout"]["order_endpoint"] = base + "/v1/order"
            pw["checkout"]["order_page_base"] = base + pw["checkout"].get("order_page_path", "/order/")
        payload["paywall"] = pw
    # 把配置对象序列化成规范化 JSON 字符串, 对这个字符串的原始字节签名。
    # 客户端直接验字符串字节、再反序列化, 避免 Swift/Python 两端浮点与键序
    # 序列化差异导致验签失败(198.0 vs 198)。
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"config": canonical, "sig": hmac_sign(canonical.encode("utf-8"))}


def context_from(body, handler):
    return {
        "idfv": body.get("idfv", "PREVIEW-DEVICE"),
        "build": body.get("build", ""),
        "country": body.get("country", "cn"),
        "ip": body.get("ip") or client_ip(handler),
        "install_ts": body.get("install_ts", now_ts() - 72 * 3600),
        "host": handler.headers.get("Host", "127.0.0.1:8787"),
        "scheme": handler.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip(),
        "prefix": handler.public_prefix(),
    }


def preview_payload(channel, ctx):
    mode, reasons, pw = resolve(channel, ctx)
    env = build_config(channel, mode, pw, ctx)
    cfg = json.loads(env["config"])
    # H5 演示与原生 App 统一读 Yii2「米帧盒VIP套餐」：
    # 本地皮肤里的「④ 套餐展示与价格」只留作历史数据，不再参与下发。
    if BRIDGE and BRIDGE.enabled and cfg.get("mode") == "paywall" \
            and isinstance(cfg.get("paywall"), dict):
        try:
            pkgs, _ = BRIDGE.vip_packages(channel, None)
            if pkgs:
                cfg["paywall"].setdefault("vip", {})["packages"] = pkgs
                cfg["paywall"]["vip"]["source"] = "yii2_mizhenhe"
        except BridgeError:
            pass
    ensure_ios_packages(cfg)
    return {"channel": channel, "mode": cfg["mode"], "decision": reasons,
            "sig": env["sig"], "config": cfg}


def read_page(relpath, prefix=""):
    """读静态页并注入部署前缀 window.MZH_BASE。
    本地直连 prefix="" -> 页面里 /v1 /admin 请求仍是根路径；
    Nginx 挂在 /mzh/ 下时 prefix="/mzh" -> 前端自动把请求拼到 /mzh 下。
    单机版 HTML 不经服务端、没有 window.MZH_BASE，等价于空前缀。
    """
    with open(os.path.join(PROJ, relpath), encoding="utf-8") as f:
        html = f.read()
    inject = "<script>window.MZH_BASE=%s;</script>" % json.dumps(prefix)
    html = html.replace("<head>", "<head>\n" + inject, 1)
    return html.replace("__MZH_BASE__", prefix)


def release_state(handler):
    """下载页的发布状态。改 server/release_state.json 即可切换 TestFlight /
    App Store 链接；把 .ipa 放进 download/ipa/ 会自动出现在下载页上。"""
    try:
        with open(RELEASE_STATE, encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        s = {}
    base = handler.base_url()
    s["h5_url"] = base + "/demo"
    s["download_url"] = base + "/download"
    s["backend_base"] = base
    try:
        ipa_dir = os.path.join(DOWNLOAD_DIR, "ipa")
        files = [x for x in os.listdir(ipa_dir) if x.lower().endswith(".ipa")]
        files.sort(key=lambda x: os.path.getmtime(os.path.join(ipa_dir, x)), reverse=True)
        if files:
            s["ipa_url"] = s.get("ipa_url") or base + "/download/ipa/" + quote(files[0])
    except OSError:
        pass
    if s.get("ipa_url") and not s.get("testflight_url") and not s.get("appstore_url"):
        s["native_status"] = "ipa_ready"
        s["native_status_text"] = "安装包已上传，可直接下载安装"
    return s


def signing_progress():
    """读 deploy/signing/next.py 生成的材料体检结果；文件缺失时给最小兜底。"""
    try:
        with open(SIGNING_PROGRESS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"updated_at": "", "mainline": {"done": 0, "total": 8,
                "name": "TestFlight · 官方迭代分发", "steps": [],
                "next": {"n": 1, "title": "开通 Apple Developer Program",
                          "action": "登录 developer.apple.com/account 查看 Membership",
                          "target": "deploy/signing/in/team_id.txt"}},
                "fastline": {"done": 0, "total": 7, "steps": []},
                "materials": {}}


def download_target(handler, ch=""):
    """广告落地页/短链的跳转目标：上架前指向自有下载页，上架后自动切 App Store。"""
    st = release_state(handler)
    target = st.get("appstore_url") or st.get("testflight_url") or st["download_url"]
    if ch:
        target += ("&" if "?" in target else "?") + "ch=" + quote(ch)
    return target


def current_profile():
    """返回完整皮肤对象: 文件里的 meta / evidence 等后台不编辑的字段原样保留,
    渠道与首页用内存最新值(可能已被后台热更新过)。"""
    p = {}
    if PROFILE_PATH and os.path.exists(PROFILE_PATH):
        try:
            with open(PROFILE_PATH, encoding="utf-8") as f:
                p = json.load(f)
        except Exception:
            pass
    p["normal"] = NORMAL_HOME
    p["channels"] = CHANNELS
    p["appstore_url"] = APPSTORE_URL
    p["default_channel"] = PROFILE_META.get("default_channel", "organic")
    return p


def audit(action, detail):
    rec = {"ts": now_ts(), "action": action, "detail": detail,
           "ip": "", "profile": PROFILE_META.get("name", "default")}
    append_log("admin_audit", rec)
    return rec


def detect_lan_ip():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            import socket
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


# ---------------------------------------------------------------- UDID 采集（Ad Hoc 设备登记）
# iOS 不允许网页 JS 读 UDID；标准做法是下发 PayloadType=Profile Service 的 mobileconfig，
# 系统安装时把设备信息 POST 回指定 HTTPS 回调。这里实现完整闭环：
#   GET  /udid                     引导页（手机 Safari）
#   GET  /udid/enroll.mobileconfig 下发采集描述文件
#   POST /udid/collect            接收并落盘 UDID，返回同 UUID 的空 Configuration 覆盖
#   GET  /udid/list              已登记设备 JSON（本地打包工程拉取）
UDID_FILE = os.path.join(EVIDENCE, "udid_devices.json")
UDID_PROFILE_UUID = "C7F3A2E1-5B48-4D9A-8E16-25F879952A1B"
UDID_PROFILE_ID = "com.mizhenhe.udid.enroll"
UDID_CHALLENGE = "mizhenhe-udid-2026"


def udid_devices():
    try:
        with open(UDID_FILE, encoding="utf-8") as f:
            rows = json.load(f)
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def udid_record(info):
    u = str(info.get("UDID") or "").strip()
    if not re.fullmatch(r"[0-9A-Fa-f\-]{16,64}", u):
        return None
    row = {"udid": u,
           "name": str(info.get("DEVICE_NAME") or ""),
           "product": str(info.get("PRODUCT") or ""),
           "version": str(info.get("VERSION") or ""),
           "ts": now_ts()}
    rows = [r for r in udid_devices() if str(r.get("udid", "")) != u]
    rows.append(row)
    tmp = UDID_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    os.replace(tmp, UDID_FILE)
    return row


def parse_udid_response(raw):
    """兼容三种回传：裸 XML plist / 表单 profile=<urlencoded> / PKCS#7 签名 DER。"""
    import plistlib
    import subprocess
    from urllib.parse import unquote_plus
    blobs = [raw]
    if raw.lstrip().startswith(b"profile="):
        blobs.append(unquote_plus(raw.decode("utf-8", "ignore")
                                  .split("profile=", 1)[1]).encode("utf-8", "ignore"))
    if b"<plist" not in raw[:1024]:
        try:
            p = subprocess.run(["openssl", "smime", "-verify", "-inform", "DER", "-noverify"],
                               input=raw, capture_output=True, timeout=8)
            if p.stdout and b"<plist" in p.stdout[:4096]:
                blobs.append(p.stdout)
        except Exception:
            pass
    for b in blobs:
        i = b.find(b"<plist")
        j = b.find(b"</plist>")
        if i < 0 or j < 0:
            continue
        try:
            d = plistlib.loads(b[i:j + 8])
            if isinstance(d, dict) and d.get("UDID"):
                return d
        except Exception:
            continue
    return {}


def udid_enroll_plist(base):
    import plistlib
    doc = {
        "PayloadType": "Profile Service",
        "PayloadVersion": 1,
        "PayloadIdentifier": UDID_PROFILE_ID,
        "PayloadUUID": UDID_PROFILE_UUID,
        "PayloadDisplayName": "米帧盒播放器 · 设备登记",
        "PayloadDescription": "仅读取设备标识用于登记 Ad Hoc 测试设备，不会改动任何设置。",
        "PayloadOrganization": "米帧盒播放器",
        "PayloadContent": {
            "URL": base + "/udid/collect",
            "DeviceAttributes": ["UDID", "DEVICE_NAME", "PRODUCT", "VERSION",
                                 "IMEI", "ICCID", "MEID", "SERIAL"],
            "Challenge": UDID_CHALLENGE,
        },
    }
    return plistlib.dumps(doc, fmt=plistlib.FMT_XML, sort_keys=False)


def udid_finish_plist():
    """同 UUID 的空配置，安装成功后覆盖采集描述文件，设置里不残留采集项。"""
    import plistlib
    doc = {
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": UDID_PROFILE_ID,
        "PayloadUUID": UDID_PROFILE_UUID,
        "PayloadDisplayName": "米帧盒播放器 · 已登记",
        "PayloadContent": [],
    }
    return plistlib.dumps(doc, fmt=plistlib.FMT_XML, sort_keys=False)


# ---------------------------------------------------------------- HTTP Handler
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, obj, headers=None):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Forwarded-For")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code, text, headers=None):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _audit_tail(self, n):
        p = os.path.join(EVIDENCE, "admin_audit.jsonl")
        if not os.path.exists(p):
            return []
        try:
            with open(p, encoding="utf-8") as f:
                lines = f.readlines()[-n:]
            return [json.loads(x) for x in lines if x.strip()]
        except Exception:
            return []

    def public_prefix(self):
        """Nginx 反代子路径：优先 X-Forwarded-Prefix，其次环境变量 PUBLIC_PREFIX。"""
        raw = self.headers.get("X-Forwarded-Prefix") or os.environ.get("PUBLIC_PREFIX", "")
        raw = str(raw).strip().strip("/")
        return "/" + raw if raw else ""

    def base_url(self):
        scheme = self.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip()
        return scheme + "://" + self.headers.get("Host", "127.0.0.1:8787") + self.public_prefix()

    def _body(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _raw(self, code, data, ctype, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _feedback_device(self, raw=""):
        """设备号统一哈希，避免把原始 IDFV 传到上游。"""
        s = str(raw or "").strip()[:128] or "unknown-device"
        return "mzh_" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:24]

    def _feedback_upload(self):
        """截图：multipart 原始 body 原样透传（不解析、不落盘）。"""
        n = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(n) if n > 0 else b""
        ctype = self.headers.get("Content-Type", "")
        dev = self._feedback_device(self.headers.get("X-Device-No", ""))
        token = feedback_token(dev)
        if not token:
            self._send(502, {"ok": False, "error": "auth_unavailable",
                             "msg": "反馈服务暂不可用，请稍后重试"})
            return
        ok, p = feedback_request("/api/feedback/upload", token=token,
                                 raw=raw, ctype=ctype)
        if ok and feedback_code(p) in (0, 200):
            self._send(200, {"ok": True,
                             "url": str((p.get("data") or {}).get("url") or "")},
                       {"Cache-Control": "no-store"})
        else:
            self._send(200, {"ok": False, "error": "upload_failed",
                             "msg": str(p.get("msg") or "截图上传失败，请重试")},
                       {"Cache-Control": "no-store"})

    def _udid_page(self):
        base = self.base_url()
        tpl = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>登记测试设备 · 米帧盒播放器</title><style>
*{box-sizing:border-box}body{margin:0;background:#0B1020;color:#F2F6FF;
font:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;padding:22px}
.wrap{max-width:480px;margin:0 auto}.brand{font-size:12px;letter-spacing:.2em;
color:#35E0C8;margin-bottom:20px}h1{font-size:23px;margin:0 0 8px}
.sub{color:#8A96B4;font-size:13px;line-height:1.8;margin-bottom:20px}
.card{background:#151C30;border-radius:18px;padding:20px;
border:1px solid rgba(255,255,255,.08);margin-bottom:16px}
.btn{display:block;width:100%;padding:16px;border:0;border-radius:28px;background:#35E0C8;
color:#06121A;font-size:16px;font-weight:700;text-decoration:none;text-align:center}
.row{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 0;
border-bottom:1px solid rgba(255,255,255,.07)}.row:last-child{border-bottom:0}
.name{font-size:13px;color:#F2F6FF}code{display:block;font-size:11px;color:#8A96B4;
word-break:break-all;margin-top:4px}button{flex:none;background:transparent;
border:1px solid #35E0C8;color:#35E0C8;border-radius:16px;padding:6px 12px;font-size:12px}
.empty{color:#8A96B4;font-size:13px;padding:8px 0}
.foot{color:#8A96B4;font-size:12px;line-height:1.9;margin-top:16px}b{color:#F2F6FF}
</style></head><body><div class="wrap">
<div class="brand">MIZHENHE · DEVICE</div>
<h1>登记测试设备</h1>
<div class="sub">请用 iPhone 的 <b>Safari</b> 打开本页，点下面按钮安装一次描述文件。
系统会把这台设备的 UDID 回传给我方服务器，用于 Ad Hoc 安装包签名。<br>
描述文件只读取设备标识，<b>不会改动任何设置</b>，登记完可在设置里删掉。</div>
<div class="card"><a class="btn" href="__APIROOT__/udid/enroll.mobileconfig">获取本机 UDID</a>
<div class="foot">点按后 iOS 会跳到「设置 → 已下载描述文件 → 安装」；
安装完成回到 Safari，UDID 会自动出现在下面，无需手动复制给我。</div></div>
<div class="card"><div style="font-size:14px;font-weight:700;margin-bottom:8px">已登记设备</div>
<div id="list"></div></div>
<div class="foot">多台设备各点一次即可。登记结果会自动同步到打包工程。</div>
</div><script>
var APIROOT=__PAYLOAD_BASE__;
var rows0=__PAYLOAD_ROWS__;
function render(rows){
  var box=document.getElementById('list');box.innerHTML='';
  if(!rows.length){box.innerHTML='<div class="empty">还没有登记过设备</div>';return;}
  rows.forEach(function(r){
    var row=document.createElement('div');row.className='row';
    var left=document.createElement('div');
    var nm=document.createElement('div');nm.className='name';
    nm.textContent=r.name||r.product||'iPhone';
    var code=document.createElement('code');code.textContent=r.udid;
    left.appendChild(nm);left.appendChild(code);
    var btn=document.createElement('button');btn.textContent='复制';
    btn.onclick=function(){navigator.clipboard.writeText(r.udid);btn.textContent='已复制';
      setTimeout(function(){btn.textContent='复制';},1500);};
    row.appendChild(left);row.appendChild(btn);box.appendChild(row);
  });
}
function refresh(){
  fetch(APIROOT+'/udid/list',{cache:'no-store'}).then(function(r){return r.json();})
  .then(function(d){render(d.devices||[]);}).catch(function(){});
}
render(rows0);refresh();setInterval(refresh,2500);
</script></body></html>"""
        html = (tpl.replace("__PAYLOAD_BASE__", json.dumps(base))
                   .replace("__PAYLOAD_ROWS__", json.dumps(udid_devices(), ensure_ascii=False))
                   .replace("__APIROOT__", base))
        self._html(200, html, {"Cache-Control": "no-store"})

    def _udid_done(self, udid):
        safe = (str(udid).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
        self._html(200, """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>已登记</title>
<style>body{background:#0B1020;color:#F2F6FF;font-family:-apple-system,sans-serif;padding:28px}
code{display:block;margin-top:14px;color:#35E0C8;font-size:13px;word-break:break-all}
a{color:#35E0C8}</style></head><body><h1>设备已登记</h1>
<p style="color:#8A96B4;font-size:14px">UDID 已写入服务器，打包工程会自动同步。</p>
<code>__UDID__</code><p><a href="__APIROOT__/udid">返回登记页</a></p>
</body></html>""".replace("__UDID__", safe).replace("__APIROOT__", self.base_url()),
                   {"Cache-Control": "no-store"})

    def _udid_collect(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(n) if n > 0 else b""
        info = parse_udid_response(raw)
        row = udid_record(info) if info else None
        append_log("udid", {"ok": bool(row), "bytes": len(raw), "ip": client_ip(self),
                            "udid": row["udid"] if row else ""})
        if not row:
            self._send(400, {"ok": False, "error": "udid_not_found"})
            return
        self._raw(200, udid_finish_plist(), "application/x-apple-aspen-config")

    def do_OPTIONS(self):
        self._send(200, {"ok": True})

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        # ---- 原生 App 运行时诊断（验证台只读查看） ----------------------------
        if u.path == "/v1/app/diag":
            with LOCK:
                rows = list(APP_DIAG[-30:])
            self._send(200, {"ok": True, "items": rows},
                       {"Cache-Control": "no-store"})
            return

        # ---- 手机时钟 / App 请求实况（验证台诊断，只读） ----------------------
        if u.path == "/v1/clock/status":
            ip = norm_ip(client_ip(self))
            rec = CLOCK_OFFSETS.get(ip)
            self._send(200, {"ok": True, "ip": ip, "server_now": now_ts(),
                             "offset": rec.get("offset") if rec else None,
                             "reported_at": rec.get("ts") if rec else None},
                       {"Cache-Control": "no-store"})
            return
        if u.path == "/v1/config/probe":
            with LOCK:
                rows = list(DECISION_LOG[-20:])
            self._send(200, {"ok": True, "items": rows,
                             "clock_offsets": len(CLOCK_OFFSETS)},
                       {"Cache-Control": "no-store"})
            return

        # ---- 用户反馈中心（→ Yii2 /api/feedback/*） ---------------------------
        if u.path == "/v1/feedback/types":
            ok, p = feedback_request("/api/feedback/types")
            upstream = ok and feedback_code(p) in (0, 200)
            types = []
            if upstream:
                types = (p.get("data") or {}).get("types") or []
            if not types:
                types = FEEDBACK_TYPES
            self._send(200, {"ok": True, "types": types,
                             "source": "yii2" if upstream else "local"},
                       {"Cache-Control": "no-store"})
            return
        if u.path == "/v1/feedback/status":
            dev = self._feedback_device(q.get("device_no", [""])[0])
            self._send(200, {"ok": True, "last": feedback_ledger().get(dev)},
                       {"Cache-Control": "no-store"})
            return

        # ---- 可视化渠道展示后台 -----------------------------------------------
        if u.path in ("/admin", "/admin/"):
            try:
                self._html(200, read_page("admin/index.html", self.public_prefix()),
                           {"Cache-Control": "no-store"})
            except FileNotFoundError:
                self._html(404, "<h1>admin/index.html missing</h1>")
            return
        if u.path == "/admin/state":
            self._send(200, {
                "ok": True,
                "profile_path": PROFILE_PATH,
                "profile_meta": PROFILE_META,
                "profile": current_profile(),
                "display": DISPLAY,
                "modules": list(MODULES),
                "tabs_all": list(NORMAL_HOME.get("tabs", [])),
                "sections_all": list(NORMAL_HOME.get("home", {}).get("sections", [])),
                "features_all": [{"id": f.get("id", f.get("name", "")),
                                  "name": f.get("name", ""),
                                  "maps_to": f.get("maps_to", "")}
                                 for f in NORMAL_HOME.get("craft", {}).get("features", [])],
                "channels_order": list(CHANNELS.keys()),
                "lan_ip": detect_lan_ip(),
                "port": int(self.headers.get("Host", "127.0.0.1:8787").split(":")[-1]) if ":" in
                        self.headers.get("Host", "") else 8787,
                "public_base": self.base_url(),
                # 原后台接口接入状态（管理面板「⑨ 原后台接口接入」用）
                "upstream": BRIDGE.describe() if BRIDGE else {"enabled": False},
                "upstream_probe": BRIDGE_PROBE,
                # 公网默认不下发写令牌；只有经过 /site 会话鉴权的 nginx 代理
                # （X-MZH-Admin: 1）进来的请求才下发，保证合并后的后台仍可保存配置。
                "admin_token": ("" if os.environ.get("MZH_PUBLIC") == "1"
                                and self.headers.get("X-MZH-Admin") != "1"
                                else ADMIN_TOKEN),
                "audit": self._audit_tail(40),
            }, {"Cache-Control": "no-store"})
            return
        if u.path == "/admin/audit":
            self._send(200, {"audit": self._audit_tail(200)})
            return
        # ---- 原后台(Yii2)接口接入：状态 / 只读体检 ---------------------------
        if u.path == "/admin/bridge":
            self._send(200, {"ok": True,
                             "bridge": BRIDGE.describe() if BRIDGE else {"enabled": False},
                             "probe": BRIDGE_PROBE},
                       {"Cache-Control": "no-store"})
            return
        if u.path == "/admin/attribution":
            with LOCK:
                clicks = list(AD_CLICKS[-60:])
                binds = list(DEVICE_BINDINGS.values())[-60:]
            self._send(200, {
                "ok": True, "config": ATTRIB_CONF,
                "clicks": clicks, "bindings": binds,
                "total_clicks": len(AD_CLICKS), "total_bindings": len(DEVICE_BINDINGS),
                "channels_order": list(CHANNELS.keys()),
                "channels_detail": {n: {"mode": c.get("mode"),
                                        "enabled": c.get("enabled"),
                                        "desc": c.get("desc", "")}
                                   for n, c in CHANNELS.items()},
                "lan_ip": detect_lan_ip(),
                "port": int(self.headers.get("Host", "127.0.0.1:8787").split(":")[-1])
                          if ":" in self.headers.get("Host", "") else 8787,
                "public_base": self.base_url(),
                "partner_auth": "hmac_sha256" if BAIDU_API_SECRET else "demo_open",
            }, {"Cache-Control": "no-store"})
            return

        # ---- 米帧盒VIP套餐（后台栏目当前配置，只读核对） ----------------------
        if u.path == "/v1/vip/plans":
            out = {"ok": True, "scope": BRIDGE.scope if BRIDGE else "local",
                   "admin_url": VIP_ADMIN_URL, "source": "local",
                   "items": [], "packages": []}
            if BRIDGE and BRIDGE.enabled:
                try:
                    d, meta = BRIDGE.call("vip_plans", {"scope": BRIDGE.scope},
                                          upstream_headers(self))
                    out["items"] = (d or {}).get("items") or []
                    out["meta"] = meta
                    pkgs, _ = BRIDGE.vip_packages("", upstream_headers(self), plans=d)
                    out["packages"] = pkgs
                    out["source"] = "yii2"
                except BridgeError as e:
                    out["error"] = str(e)
                    out["source"] = "error"
            self._send(200, out, {"Cache-Control": "no-store"})
            return

        # ---- 渠道配置验证台 ---------------------------------------------------
        if u.path in ("/verify", "/verify/"):
            try:
                html = read_page("verify/index.html", self.public_prefix())
                qrs = {}
                qr_dir = os.path.join(PROJ, "verify")
                if os.path.isdir(qr_dir):
                    for name in sorted(os.listdir(qr_dir)):
                        if name.startswith("qr-") and name.endswith(".png"):
                            with open(os.path.join(qr_dir, name), "rb") as f:
                                qrs[name[3:-4]] = ("data:image/png;base64,"
                                                   + base64.b64encode(f.read()).decode())
                inject = ("<script>window.MZH_TEST_CHANNELS=%s;"
                          "window.MZH_QR=%s;window.MZH_VIP_ADMIN=%s;</script>") % (
                    json.dumps(sorted(TEST_CHANNELS), ensure_ascii=False),
                    json.dumps(qrs, ensure_ascii=False),
                    json.dumps(VIP_ADMIN_URL, ensure_ascii=False))
                html = html.replace("<head>", "<head>\n" + inject, 1)
                self._html(200, html, {"Cache-Control": "no-store"})
            except FileNotFoundError:
                self._html(404, "<h1>verify/index.html missing</h1>")
            return
        if u.path == "/v1/attribution/test-status":
            ip = norm_ip(client_ip(self))
            key = device_hash("ip:" + ip)
            with LOCK:
                binding = dict(DEVICE_BINDINGS.get(key) or {})
            self._send(200, {"ok": True, "ip": ip, "binding": binding or None,
                             "test_channels": sorted(TEST_CHANNELS),
                             "total_bindings": len(DEVICE_BINDINGS)},
                       {"Cache-Control": "no-store"})
            return
        # ---- 手机可交互 Demo ---------------------------------------------------
        if u.path in ("/demo", "/demo/"):
            try:
                self._html(200, read_page("demo/index.html", self.public_prefix()),
                           {"Cache-Control": "no-store"})
            except FileNotFoundError:
                self._html(404, "<h1>demo/index.html missing</h1>")
            return
        # ---- 软件下载/安装入口 -----------------------------------------------
        if u.path in ("/download", "/download/"):
            try:
                html = read_page("download/index.html", self.public_prefix())
                inject = ("<script>window.MZH_RELEASE=%s;"
                          "window.MZH_PROGRESS=%s;</script>") % (
                    json.dumps(release_state(self), ensure_ascii=False),
                    json.dumps(signing_progress(), ensure_ascii=False))
                html = html.replace("<head>", "<head>\n" + inject, 1)
                self._html(200, html, {"Cache-Control": "no-store"})
            except FileNotFoundError:
                self._html(404, "<h1>download/index.html missing</h1>")
            return
        if u.path == "/download/state":
            self._send(200, release_state(self), {"Cache-Control": "no-store"})
            return
        # ---- 出包材料清单与进度 ------------------------------------------------
        if u.path in ("/signing", "/signing/"):
            try:
                html = read_page("signing/index.html", self.public_prefix())
                inject = "<script>window.MZH_PROGRESS=%s;</script>" % json.dumps(
                    signing_progress(), ensure_ascii=False)
                html = html.replace("<head>", "<head>\n" + inject, 1)
                self._html(200, html, {"Cache-Control": "no-store"})
            except FileNotFoundError:
                self._html(404, "<h1>signing/index.html missing</h1>")
            return
        if u.path == "/signing/state":
            self._send(200, signing_progress(), {"Cache-Control": "no-store"})
            return
        # ---- Ad Hoc OTA：iOS 安装用的 plist 与图标，必须 https 可直取 ----------
        if u.path == "/download/manifest.plist":
            path = os.path.join(DOWNLOAD_DIR, "ipa", "manifest.plist")
            if not os.path.isfile(path):
                self._html(404, "<h1>manifest not found</h1>")
                return
            data = open(path, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path == "/download/apple-touch-icon.png":
            path = os.path.join(DOWNLOAD_DIR, "apple-touch-icon.png")
            if not os.path.isfile(path):
                self._send(404, {"error": "icon_not_found"})
                return
            data = open(path, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path.startswith("/download/ipa/"):
            name = os.path.basename(u.path)
            path = os.path.join(DOWNLOAD_DIR, "ipa", name)
            if not name.lower().endswith(".ipa") or ".." in name or not os.path.isfile(path):
                self._send(404, {"error": "not_found", "path": u.path})
                return
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path == "/demo/config":
            ch = q.get("ch", [PROFILE_META.get("default_channel", "organic")])[0]
            self._send(200, preview_payload(ch, context_from({}, self)),
                       {"Cache-Control": "no-store"})
            return
        if u.path == "/admin/preview":
            ch = q.get("ch", ["organic"])[0]
            self._send(200, preview_payload(ch, context_from({}, self)),
                       {"Cache-Control": "no-store"})
            return

        if u.path == "/lp":
            try:
                html = open(LANDING, encoding="utf-8").read()
                ch = q.get("ch", [PROFILE_META.get("default_channel", "douyin_1125_a")])[0]
                html = html.replace("__SERVER__", self.base_url())
                html = html.replace("__CHANNEL__", ch)
                # 上架前跳自有下载页；上架后在 release_state.json 填 appstore_url 即自动切换
                html = html.replace("__APPSTORE__", download_target(self, ch))
                html = html.replace("OFFICIAL · APP STORE", "OFFICIAL · 官方下载")
                html = html.replace("前往 App Store 下载", "立即下载安装")
                html = html.replace(">App Store</b>", ">官方下载页</b>")
                html = html.replace("点击后将跳转到苹果应用商店", "点击后打开官方下载页，免安装即可体验")
                self._html(200, html)
            except FileNotFoundError:
                self._html(404, "<h1>landing/index.html missing</h1>")
            return
        if u.path == "/go":
            ch = q.get("ch", ["organic"])[0]
            self._record_click({"channel": ch, "ua": self.headers.get("User-Agent", ""),
                                "tz": "", "lang": "", "screen": "", "source": "go"})
            self.send_response(302)
            self.send_header("Location", download_target(self, ch))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if u.path.startswith("/order/"):
            self._order_page(u.path[len("/order/"):])
            return
        if u.path == "/admin/channels":
            self._send(200, {"secret": "<hidden>", "profile": PROFILE_META,
                             "normal": NORMAL_HOME, "channels": CHANNELS})
            return
        if u.path == "/admin/account/deletions":
            with LOCK:
                rows = list(DELETIONS[-60:])
            self._send(200, {"ok": True, "total": len(DELETIONS), "deletions": rows},
                       {"Cache-Control": "no-store"})
            return
        if u.path == "/admin/logs":
            self._send(200, {"clicks": CLICKS[-20:], "decisions": DECISION_LOG[-30:],
                             "orders": list(ORDERS.values())[-20:], "logins": LOGINS[-20:],
                             "baidu_clicks": AD_CLICKS[-20:],
                             "device_bindings": list(DEVICE_BINDINGS.values())[-20:],
                             "account_deletions": DELETIONS[-20:]})
            return
        # ---- UDID 采集（Ad Hoc 设备登记） -------------------------------------
        if u.path in ("/udid", "/udid/"):
            self._udid_page()
            return
        if u.path in ("/udid/enroll.mobileconfig", "/udid/enroll"):
            data = udid_enroll_plist(self.base_url())
            self._raw(200, data, "application/x-apple-aspen-config",
                      {"Content-Disposition": 'attachment; filename="mizhenhe-udid.mobileconfig"'})
            return
        if u.path == "/udid/list":
            rows = udid_devices()
            self._send(200, {"ok": True, "total": len(rows), "devices": rows},
                       {"Cache-Control": "no-store"})
            return
        if u.path == "/udid/done":
            self._udid_done(q.get("udid", [""])[0])
            return
        if u.path == "/health":
            self._send(200, {"ok": True, "ts": now_ts(), "channels": len(CHANNELS),
                             "baidu_clicks": len(AD_CLICKS),
                             "device_bindings": len(DEVICE_BINDINGS),
                             "logins": len(LOGINS),
                             "account_deletions": len(DELETIONS)})
            return
        self._send(404, {"error": "not_found", "path": u.path})

    def _order_page(self, order_no):
        """自建订单页。录屏 25.59s 的 trade.* 页面: 交易进行中 / 订单编号 /
        套餐名 / 金额 / 重新支付 / 售后联系。视觉换成皮肤主题色。"""
        o = ORDERS.get(order_no)
        if not o:
            self._html(404, "<h1>订单不存在</h1>")
            return
        cfg = CHANNELS.get(o["channel"], {}).get("paywall", {}).get("checkout", {})
        brand = NORMAL_HOME.get("brand", "米帧盒播放器")
        primary = NORMAL_HOME.get("theme", {}).get("primary", "#35E0C8")
        bg = NORMAL_HOME.get("theme", {}).get("background", "#0B1020")
        surface = NORMAL_HOME.get("theme", {}).get("surface", "#151C30")
        title = cfg.get("order_page_title", "交易进行中")
        repay = cfg.get("repay_button", "重新支付")
        support = cfg.get("support_url", "")
        support_prefix = cfg.get("support_prefix", "售后联系")
        cashier = o.get("cashier_url", "")
        html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{title} · {brand}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:{bg};color:#F2F6FF;
font:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;padding:24px}}
.wrap{{max-width:420px;margin:0 auto}}.brand{{font-size:13px;letter-spacing:.2em;
color:{primary};margin-bottom:26px}}.card{{background:{surface};border-radius:20px;
padding:24px;border:1px solid rgba(255,255,255,.08)}}
h1{{font-size:22px;margin:0 0 4px}}.sub{{color:#8A96B4;font-size:13px;margin-bottom:22px}}
.row{{display:flex;justify-content:space-between;align-items:center;padding:13px 0;
border-bottom:1px solid rgba(255,255,255,.07);font-size:14px}}
.row:last-of-type{{border-bottom:0}}.k{{color:#8A96B4}}.v{{font-weight:600}}
.amt{{font-size:30px;color:{primary};font-weight:700;margin:18px 0 4px}}
.btn{{display:block;width:100%;margin-top:22px;padding:15px;border:0;border-radius:28px;
background:{primary};color:#06121A;font-size:16px;font-weight:700;text-decoration:none;text-align:center}}
.foot{{text-align:center;color:#8A96B4;font-size:12px;margin-top:20px;line-height:1.8}}
a{{color:{primary};text-decoration:none}}
</style></head><body><div class="wrap">
<div class="brand">{brand.upper()} · ORDER</div>
<div class="card"><h1>{title}</h1><div class="sub">请完成付款以解锁会员权益</div>
<div class="row"><span class="k">订单编号</span><span class="v" style="font-size:12px">{o['order_no']}</span></div>
<div class="row"><span class="k">商品</span><span class="v">{o.get('package_name','会员')}</span></div>
<div class="row"><span class="k">状态</span><span class="v">{o['status']}</span></div>
<div class="amt">¥{o['amount']:.2f}</div>
<a class="btn" href="{cashier}">{repay}</a></div>
<div class="foot">{support_prefix} <a href="{support}">{support}</a><br>付款完成后返回 App 自动同步会员状态</div>
</div></body></html>"""
        self._html(200, html, {"Cache-Control": "no-store"})

    def _record_click(self, d):
        cid = "CLK" + uuid.uuid4().hex[:20].upper()
        ua = d.get("ua", "")
        rec = {
            "click_id": cid,
            "channel": d.get("channel", "organic"),
            "ts": now_ts(),
            "ip": client_ip(self),
            "ua": ua,
            "model": d.get("model") or parse_ua_model(ua),
            "tz": d.get("tz", ""),
            "lang": d.get("lang", ""),
            "screen": d.get("screen", ""),
            "source": d.get("source", "lp"),
        }
        with LOCK:
            CLICKS.append(rec)
        append_log("clicks", rec)
        return rec

    def do_POST(self):
        u = urlparse(self.path)
        # iOS 回传的是 plist / PKCS#7，不是 JSON，必须在 _body() 消费流之前拦截
        if u.path == "/udid/collect":
            self._udid_collect()
            return
        # 截图是 multipart，必须在 _body() 消费流之前原样读走
        if u.path == "/v1/feedback/upload":
            self._feedback_upload()
            return
        b = self._body()

        # ---- 0c. 原生 App 运行时诊断上报（build 5 起，验证台直接可读） --------
        if u.path == "/v1/app/diag":
            rec = {
                "ts": now_ts(),
                "ip": norm_ip(client_ip(self)),
                "diag": str(b.get("diag") or "")[:500],
                "mode": b.get("mode"),
                "channel": b.get("channel"),
                "payload_channel": b.get("payload_channel"),
                "build": b.get("build"),
                "idfv": str(b.get("idfv") or "")[:64],
                "install_ts": b.get("install_ts"),
                "client_now": b.get("client_now"),
            }
            with LOCK:
                APP_DIAG.append(rec)
            append_log("app_diag", rec)
            self._send(200, {"ok": True}, {"Cache-Control": "no-store"})
            return

        # ---- 0b. 手机时钟上报（验证台自动调用，只影响测试渠道的 issued_at） ----
        if u.path == "/v1/clock/report":
            try:
                phone = int(b.get("phone_now") or 0)
            except (TypeError, ValueError):
                phone = 0
            ip = norm_ip(client_ip(self))
            off = phone - now_ts() if phone > 0 else 0
            with LOCK:
                CLOCK_OFFSETS[ip] = {"offset": off, "phone_now": phone, "ts": now_ts()}
                save_clock_offsets()
            self._send(200, {"ok": phone > 0, "ip": ip, "offset": off,
                             "phone_now": phone, "server_now": now_ts()},
                       {"Cache-Control": "no-store"})
            return

        # ---- 0a. 原后台接口只读体检（不写任何渠道数据） -----------------------
        if u.path == "/admin/bridge/probe":
            ch = str(b.get("channel") or "")
            if not ch and BRIDGE and BRIDGE.test_channels:
                ch = sorted(BRIDGE.test_channels)[0]
            if not BRIDGE or not BRIDGE.enabled:
                self._send(200, {"ok": False, "error": "upstream_disabled",
                                 "bridge": BRIDGE.describe() if BRIDGE else {"enabled": False}})
                return
            BRIDGE_PROBE.update({"ts": now_ts(), "channel": ch,
                                 "result": BRIDGE.probe(ch, upstream_headers(self))})
            self._send(200, {"ok": True, "probe": BRIDGE_PROBE},
                       {"Cache-Control": "no-store"})
            return

        # ---- 0. 管理后台写操作 -----------------------------------------------
        if u.path in ("/admin/save", "/admin/killswitch"):
            token = b.get("token") or self.headers.get("X-Admin-Token", "")
            if not hmac.compare_digest(str(token), ADMIN_TOKEN):
                self._send(403, {"error": "bad_admin_token"})
                return
            if u.path == "/admin/killswitch":
                DISPLAY["kill_switch"] = bool(b.get("enabled", False))
                save_display_state(DISPLAY)
                audit("killswitch", {"enabled": DISPLAY["kill_switch"]})
                self._send(200, {"ok": True, "kill_switch": DISPLAY["kill_switch"]})
                return
            prof, disp = b.get("profile"), b.get("display")
            if not isinstance(prof, dict) or not isinstance(disp, dict):
                self._send(400, {"error": "profile_and_display_required"})
                return
            ch = prof.get("channels")
            if not isinstance(ch, dict) or "organic" not in ch:
                self._send(400, {"error": "profile.channels 必须含 organic"})
                return
            for name, cfg in ch.items():
                if not isinstance(cfg, dict) or "mode" not in cfg:
                    self._send(400, {"error": f"channel {name} 缺少 mode"})
                    return
                if cfg.get("mode") == "paywall":
                    pw = cfg.get("paywall") or {}
                    for k in ("splash", "trial", "vip", "checkout"):
                        if k not in pw:
                            self._send(400, {"error": f"channel {name}.paywall 缺少 {k}"})
                            return
                    if not pw["vip"].get("packages"):
                        self._send(400, {"error": f"channel {name}.paywall.vip.packages 为空"})
                        return
            path = PROFILE_PATH or os.path.join(ROOT, "profiles", "runtime.json")
            # 元数据/证据字段不在后台表单里, 从原文件补回, 避免保存时丢内容
            try:
                with open(path, encoding="utf-8") as f:
                    old = json.load(f)
                for k, v in old.items():
                    if k not in prof:
                        prof[k] = v
            except Exception:
                pass
            atomic_write_json(path, prof)
            save_display_state(disp)
            load_profile(path)          # 热更新 CHANNELS / NORMAL_HOME / 元数据
            DISPLAY.clear()
            DISPLAY.update(disp)
            audit("save", {"profile": os.path.basename(path),
                           "channels": {n: c.get("mode") for n, c in ch.items()},
                           "kill_switch": DISPLAY.get("kill_switch", False)})
            after = {n: preview_payload(n, context_from({}, self))["mode"] for n in CHANNELS}
            self._send(200, {"ok": True, "profile_path": path,
                             "state_path": STATE_FILE, "modes_after_save": after})
            return

        # ---- 0b. 百度推广归因: 后台参数 / 解绑 / 清空 --------------------------
        if u.path in ("/admin/attribution/config", "/admin/attribution/unbind",
                      "/admin/attribution/clear"):
            tk = b.get("token") or self.headers.get("X-Admin-Token", "")
            if not hmac.compare_digest(str(tk), ADMIN_TOKEN):
                self._send(403, {"error": "bad_admin_token"})
                return
            if u.path.endswith("/config"):
                for k in ("window_seconds", "min_confidence", "ip_only_confidence"):
                    if k in b:
                        ATTRIB_CONF[k] = b[k]
                for k in ("allow_ip_only", "strict_mode"):
                    if k in b:
                        ATTRIB_CONF[k] = bool(b[k])
                if isinstance(b.get("channel_map"), dict):
                    ATTRIB_CONF["channel_map"] = b["channel_map"]
                save_attribution_state()
                audit("attribution_config", {"config": ATTRIB_CONF})
                self._send(200, {"ok": True, "config": ATTRIB_CONF})
                return
            if u.path.endswith("/unbind"):
                key = str(b.get("device_key", ""))
                with LOCK:
                    DEVICE_BINDINGS.pop(key, None)
                save_attribution_state()
                audit("attribution_unbind", {"device_key": key})
                self._send(200, {"ok": True, "unbound": key})
                return
            with LOCK:
                AD_CLICKS.clear()
                DEVICE_BINDINGS.clear()
            save_attribution_state()
            audit("attribution_clear", {})
            self._send(200, {"ok": True, "cleared": True})
            return

        # ---- 1b. 百度推广 API 归因回传(主链路入口) ----------------------------
        if u.path == "/v1/attribution/baidu/callback":
            ok_sig, sig_mode = verify_partner_signature(b)
            if not ok_sig:
                self._send(403, {"error": "bad_partner_signature"})
                return
            items = b.get("clicks") if isinstance(b.get("clicks"), list) else [b]
            saved, dup, rejected = [], 0, []
            with LOCK:
                known = {c.get("click_id") for c in AD_CLICKS}
                for it in items:
                    if not isinstance(it, dict):
                        rejected.append({"reason": "not_object"})
                        continue
                    cid = str(it.get("click_id") or "").strip()
                    if not cid:
                        rejected.append({"reason": "missing_click_id"})
                        continue
                    if cid in known:
                        dup += 1
                        continue
                    ch = map_channel(it)
                    if not ch:
                        rejected.append({"click_id": cid, "reason": "no_channel_mapping"})
                        continue
                    dev_kind, dev_val = "", ""
                    for k in DEVICE_FIELDS:
                        if it.get(k):
                            dev_kind, dev_val = k, str(it[k])
                            break
                    rec = {
                        "click_id": cid, "source": "baidu",
                        "plan_id": str(it.get("plan_id") or ""),
                        "channel": ch,
                        "ip": norm_ip(it.get("ip") or client_ip(self)),
                        "device_hash": device_hash(dev_kind + ":" + dev_val) if dev_val else "",
                        "device_kind": dev_kind,
                        "ts": int(it.get("ts") or now_ts()),
                        "ua": str(it.get("ua") or ""),
                        "model": str(it.get("model") or ""),
                        "tz": str(it.get("tz") or ""),
                        "lang": str(it.get("lang") or ""),
                        "screen": str(it.get("screen") or ""),
                        "status": "pending",
                    }
                    AD_CLICKS.append(rec)
                    known.add(cid)
                    saved.append(rec)
            save_attribution_state()
            append_log("baidu_callbacks", {"saved": len(saved), "duplicates": dup,
                                           "rejected": rejected, "auth": sig_mode,
                                           "clicks": saved})
            self._send(200, {"ok": True, "saved": len(saved), "duplicates": dup,
                             "rejected": rejected, "total": len(AD_CLICKS),
                             "clicks": saved[-10:]})
            return

        # ---- 1c. 首次启动: 服务端按 IP / 设备号 解析渠道号 --------------------
        if u.path == "/v1/attribution/resolve":
            ip = norm_ip(b.get("ip") or client_ip(self))
            bound = resolve_bound_channel(b, ip)
            if bound:
                res = {"channel": bound["channel"],
                       "confidence": bound.get("confidence", 1.0),
                       "method": "binding:" + str(bound.get("method", "")),
                       "detail": ["device_binding_hit", bound.get("device_key", "")],
                       "resolved_at": now_ts(), "bound": True}
            else:
                ch, method, conf, detail, click = match_ad_click(b, ip)
                if ch != ATTRIB_CONF.get("default_channel", "organic"):
                    bind_device(b, ip, ch, method, conf, click)
                res = {"channel": ch, "confidence": conf, "method": method,
                       "detail": detail, "resolved_at": now_ts(),
                       "bound": ch != ATTRIB_CONF.get("default_channel", "organic")}
            # 只对新建测试渠道上报 Yii2 安装/归因，跑量渠道一个都不碰
            # 测试/新包可以显式带 channel；解析结果作为兼容回退。
            # 守护只允许白名单测试渠道上报，跑量渠道不会被写入 Yii2。
            report_ch = str(b.get("channel") or res.get("channel") or "")
            if BRIDGE and BRIDGE.enabled and BRIDGE.allow_write(report_ch):
                try:
                    BRIDGE.call("channel_report",
                                {"qd": report_ch,
                                 "device_id": b.get("device_id") or b.get("idfv") or "",
                                 "version": b.get("build") or b.get("version") or "",
                                 "platform": b.get("platform") or "ios"},
                                upstream_headers(self), channel=report_ch)
                    res["upstream_reported"] = True
                except BridgeError as e:
                    res["upstream_reported"] = False
                    res["upstream_error"] = str(e)
            append_log("attributions", {"ip": ip, "body": b, "result": res})
            self._send(200, res)
            return

        # ---- 1. 广告点击打点 -------------------------------------------------
        if u.path == "/v1/click":
            ua = b.get("ua") or self.headers.get("User-Agent", "")
            b["ua"] = ua
            rec = self._record_click(b)
            token = make_click_token(rec["click_id"], rec["channel"], rec["ts"])
            self._send(200, {"click_id": rec["click_id"], "channel": rec["channel"],
                             "clipboard_token": token, "expires_in": 7 * 86400})
            return

        # ---- 2. 首次启动归因(兼容旧入口, 内部一律走百度 API 回传匹配) ----------
        # 渠道号不再由剪贴板凭据决定: 先查设备绑定, 再用百度回传的 IP / 设备号
        # 点击库做匹配; 剪贴板 token 即使收到也只记日志, 不参与命中。
        if u.path == "/v1/attribution":
            ip = norm_ip(b.get("ip") or client_ip(self))
            bound = resolve_bound_channel(b, ip)
            if bound:
                method, channel, conf = "baidu_binding", bound["channel"], bound.get("confidence", 1.0)
                detail = ["device_binding_hit", bound.get("device_key", "")]
            else:
                channel, method, conf, detail, click = match_ad_click(b, ip)
                if channel != ATTRIB_CONF.get("default_channel", "organic"):
                    bind_device(b, ip, channel, method, conf, click)
            if b.get("clipboard_token"):
                detail = detail + ["clipboard_token_ignored"]
            res = {"channel": channel, "confidence": conf, "method": method,
                   "detail": detail, "resolved_at": now_ts()}
            append_log("attributions", {"ip": ip, "body": b, "result": res})
            self._send(200, res)
            return

        # ---- 3. 远程配置 -----------------------------------------------------
        if u.path == "/v1/config":
            ctx = {
                "idfv": b.get("idfv", ""),
                "build": b.get("build", ""),
                "country": b.get("country", "cn"),
                "ip": b.get("ip") or client_ip(self),
                "install_ts": b.get("install_ts", now_ts()),
                "host": self.headers.get("Host", "127.0.0.1:8787"),
                "scheme": self.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip(),
                "prefix": self.public_prefix(),
            }
            # 客户端可以不传 channel: 服务端按「百度回传 IP / 设备号 → 绑定」自动解析。
            attribution = None
            channel = str(b.get("channel") or "").strip()
            if not channel or channel == "auto" or b.get("auto_attribution"):
                bound = resolve_bound_channel(b, ctx["ip"]) or test_net_binding(ctx["ip"])
                if bound and bound.get("channel"):
                    channel = bound["channel"]
                    attribution = {"method": "binding:" + str(bound.get("method", "")),
                                   "channel": channel,
                                   "confidence": bound.get("confidence", 1.0),
                                   "click_id": bound.get("click_id", "")}
                else:
                    ch, method, conf, detail, click = match_ad_click(b, ctx["ip"])
                    if ch != ATTRIB_CONF.get("default_channel", "organic"):
                        bind_device(b, ctx["ip"], ch, method, conf, click)
                    channel = ch
                    attribution = {"method": method, "channel": ch,
                                   "confidence": conf, "detail": detail}
            if not channel:
                channel = ATTRIB_CONF.get("default_channel", "organic")
            mode, reasons, paywall = resolve(channel, ctx)
            # 原后台接口接入：VIP 档位直接取 Yii2「米帧盒VIP套餐」(scope=mizhenhe)，
            # 展示配置取 Yii2「渠道展示」，渠道启停取 Yii2「渠道管理」。
            # 小说 / 浏览器两套在售档位一个都不读、一个都不改。
            envelope_extra = None
            bridge_pkgs = []
            if BRIDGE and BRIDGE.enabled:
                try:
                    up = BRIDGE.upstream_config(channel, upstream_headers(self))
                    pkgs, _plans = BRIDGE.vip_packages(
                        channel, upstream_headers(self), plans=up.get("vip_plans"))
                    # 合规态(normal)的渠道 resolve() 会返回 paywall=None，只挂诊断信息
                    if pkgs and isinstance(paywall, dict):
                        paywall = copy.deepcopy(paywall)
                        paywall.setdefault("vip", {})["packages"] = pkgs
                        bridge_pkgs = pkgs
                        up["applied"] = "vip_packages_from_yii2"
                    else:
                        up["applied"] = "display_only"
                    envelope_extra = up
                except BridgeError as e:
                    envelope_extra = {"error": str(e), "fallback": "local"}
            envelope = build_config(channel, mode, paywall, ctx)
            # 本地「可见套餐」勾选只认皮肤里的档位 id；桥接后档位由 Yii2 后台
            # 「米帧盒VIP套餐」的上架/下架控制，因此在签名前把原后台档位写回，
            # 重新规范化序列化并重算 HMAC，客户端验签不受影响。
            cfg_obj = json.loads(envelope["config"])
            # 只给测试渠道修正 issued_at：手机时钟与服务器不一致时，
            # App 的 verify() 会因 |手机时间 - issued_at| >= 3600 判验签失败，
            # 从而静默回落 normal。修正后下面会重新规范化并重算 HMAC。
            if channel in TEST_CHANNELS:
                off = client_clock_offset(ctx["ip"])
                if off:
                    cfg_obj["issued_at"] = now_ts() + off
            if bridge_pkgs and isinstance(cfg_obj.get("paywall"), dict):
                cfg_obj["paywall"].setdefault("vip", {})["packages"] = bridge_pkgs
            # 原生 App 严格 Codable：下发前补齐档位字段并做契约自检，
            # 避免「服务端已切 paywall，App 解码失败却回落 normal 首页」。
            ensure_ios_packages(cfg_obj)
            issues = ios_contract_issues(cfg_obj)
            canonical = json.dumps(cfg_obj, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"))
            if canonical != envelope["config"]:
                envelope["config"] = canonical
                envelope["sig"] = hmac_sign(canonical.encode("utf-8"))
            envelope["ios_contract"] = {"ok": not issues, "issues": issues}
            if envelope_extra is not None:
                envelope["upstream"] = envelope_extra
            # decision / attribution 是诊断字段, 不参与签名(生产可去掉)
            envelope["decision"] = reasons
            envelope["attribution"] = attribution or {"method": "client_supplied",
                                                      "channel": channel}
            ua = self.headers.get("User-Agent", "")
            rec = {"ts": now_ts(), "channel": channel, "mode": mode,
                   "reasons": reasons, "idfv": ctx["idfv"], "ip": norm_ip(ctx["ip"]),
                   "attribution": envelope["attribution"],
                   "ua": ua, "build": ctx["build"],
                   "install_ts": ctx["install_ts"],
                   "issued_at": cfg_obj.get("issued_at"),
                   "size": len(envelope["config"].encode("utf-8")),
                   "is_app": "CFNetwork" in ua}
            with LOCK:
                DECISION_LOG.append(rec)
            append_log("decisions", rec)
            # 原生 App 只需要 config + sig：诊断字段（decision / attribution /
            # upstream / ios_contract）会让响应膨胀到 13KB+，在蜂窝网络首启时
            # 更容易触发 4 秒请求超时。给 App 返回精简信封，其余端保持完整。
            out = ({"config": envelope["config"], "sig": envelope["sig"]}
                   if "CFNetwork" in self.headers.get("User-Agent", "") else envelope)
            self._send(200, out, {"X-Config-Signature": envelope["sig"],
                                  "Cache-Control": "no-store"})
            return

        # ---- 4. VIP 下单 -----------------------------------------------------
        if u.path == "/v1/order":
            pkg = b.get("package_id", "vip_3y")
            ch = b.get("channel", "organic")
            # 原后台下单：订单进 Yii2「充值记录」，支付走 Yii2「支付配置」的支付宝通道
            if BRIDGE and BRIDGE.enabled and BRIDGE.allow_write(ch):
                try:
                    plan_id = str(pkg)[5:] if str(pkg).startswith("yii2_") else None
                    if plan_id is None:
                        pkgs, _ = BRIDGE.vip_packages(ch, upstream_headers(self))
                        if pkgs:
                            plan_id = pkgs[0]["plan_id"]
                    if plan_id is not None:
                        hdr = upstream_headers(self)
                        tk = str(b.get("ticket") or b.get("token") or "")
                        if tk and not tk.startswith("TKT") and not hdr.get("Authorization"):
                            hdr["Authorization"] = "Bearer " + tk
                        up_order, up_meta = BRIDGE.create_order(plan_id, ch, hdr)
                        if up_order.get("order_no") and up_order.get("cashier_url"):
                            up_order["created_at"] = now_ts()
                            up_order["order_page_url"] = self.base_url() + "/order/" + up_order["order_no"]
                            with LOCK:
                                ORDERS[up_order["order_no"]] = up_order
                            append_log("orders", dict(up_order, event="yii2_created"))
                            self._send(200, dict(up_order, upstream_meta=up_meta["meta"]))
                            return
                except BridgeError as e:
                    append_log("upstream_errors", {"path": u.path, "error": str(e)})
            price = None
            pkg_name = None
            c = CHANNELS.get(ch, {})
            for p in c.get("paywall", {}).get("vip", {}).get("packages", []):
                if p["id"] == pkg:
                    price = p["price"]
            # H5 演示渠道不在写白名单里时，仍按后台档位价格生成本地演示订单，
            # 保证任意渠道的「会员页 → 收银台」都能点通，且标价与后台一致。
            if price is None and str(pkg).startswith("yii2_") and BRIDGE and BRIDGE.enabled:
                try:
                    bp, _ = BRIDGE.vip_packages(ch, upstream_headers(self))
                    hit = next((x for x in bp
                                if "yii2_" + str(x.get("plan_id")) == str(pkg)), None)
                    if hit:
                        price = hit.get("price")
                        pkg_name = hit.get("name")
                except BridgeError:
                    pass
            if price is None:
                self._send(400, {"error": "package_not_available", "channel": ch, "package_id": pkg})
                return
            vis = DISPLAY.get("channels", {}).get(ch, {}).get("visible_packages")
            if vis is not None and pkg not in vis and not str(pkg).startswith("yii2_"):
                self._send(400, {"error": "package_hidden_by_display", "package_id": pkg})
                return
            order_no = ch[:3].upper() + datetime.now().strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:8].upper()
            pkg_name = pkg_name or next((p.get("name", pkg) for p in
                             c.get("paywall", {}).get("vip", {}).get("packages", [])
                             if p["id"] == pkg), pkg)
            ck = c.get("paywall", {}).get("checkout", {})
            cashier_base = ck.get("cashier_base", "https://mclient.alipay.com")
            order = {
                "order_no": order_no, "channel": ch, "package_id": pkg,
                "package_name": pkg_name, "amount": price, "status": "pending",
                "created_at": now_ts(),
                "cashier_url": cashier_base.rstrip("/") + "/h5/cashier?out_trade_no=" + order_no,
                "order_page_url": self.base_url() + ck.get("order_page_path", "/order/") + order_no,
            }
            with LOCK:
                ORDERS[order_no] = order
            append_log("orders", order)
            self._send(200, order)
            return

        # ---- 4b. 手机号免验证码登录(录屏 42.66–54.04s) ------------------------
        if u.path == "/v1/login":
            phone = str(b.get("phone", "")).strip()
            confirm = str(b.get("confirm_phone", "")).strip()
            agreed = bool(b.get("agreed", False))
            if not re.match(r"^1\d{10}$", phone):
                self._send(400, {"error": "invalid_phone"})
                return
            if confirm and confirm != phone:
                self._send(400, {"error": "phone_mismatch"})
                return
            if not agreed:
                self._send(400, {"error": "agreement_required"})
                return
            # 原后台账号体系：设备号一键建号，账号直接出现在 Yii2「用户管理」
            if BRIDGE and BRIDGE.enabled and BRIDGE.allow_write(b.get("channel", "")):
                dev = str(b.get("idfv") or "").strip()
                if len(dev) < 6 or not re.match(r"^[0-9A-Za-z_\-:.]+$", dev):
                    dev = device_hash("idfv:" + (dev or phone))
                try:
                    ticket, is_vip, _extra = BRIDGE.login(
                        dev, str(b.get("platform") or "ios"),
                        str(b.get("channel") or ""), upstream_headers(self))
                    if ticket:
                        rec = {"ts": now_ts(),
                               "phone_masked": phone[:3] + "****" + phone[-4:],
                               "idfv": b.get("idfv", ""), "channel": b.get("channel", ""),
                               "method": "yii2_guest", "ticket": ticket}
                        with LOCK:
                            LOGINS.append(rec)
                        append_log("logins", rec)
                        self._send(200, {"ok": True, "ticket": ticket,
                                        "phone_masked": rec["phone_masked"],
                                        "is_vip": is_vip, "toast": "登录成功",
                                        "upstream": True})
                        return
                except BridgeError as e:
                    append_log("upstream_errors", {"path": u.path, "error": str(e)})
            rec = {"ts": now_ts(), "phone_masked": phone[:3] + "****" + phone[-4:],
                   "idfv": b.get("idfv", ""), "channel": b.get("channel", "organic"),
                   "method": "phone_no_sms", "ticket": "TKT" + uuid.uuid4().hex[:16].upper()}
            with LOCK:
                LOGINS.append(rec)
            append_log("logins", rec)
            self._send(200, {"ok": True, "ticket": rec["ticket"],
                            "phone_masked": rec["phone_masked"],
                            "is_vip": bool(VIP.get(b.get("idfv", ""))),
                            "toast": "登录成功"})
            return

        # ---- 4c. App 内账号注销(App Store 审核指南 5.1.1(v)) ------------------
        # 必须能在 App 内发起, 且要真正删除账号与关联个人数据。
        # 身份校验: ticket 来自真实登录, 或 idfv 有登录记录; 不接受匿名删除请求。
        if u.path == "/v1/account/delete":
            ticket = str(b.get("ticket", "")).strip()
            idfv = str(b.get("idfv", "")).strip()
            if not ticket and not idfv:
                self._send(400, {"error": "identity_required"})
                return
            if str(b.get("confirm", "")).strip() not in ("DELETE", "注销"):
                self._send(400, {"error": "confirmation_required",
                                 "hint": "confirm 必须为 DELETE 或 注销"})
                return
            # 原后台账号注销：Yii2「用户管理」同步标记已注销并清除令牌
            upstream_deleted = False
            if BRIDGE and BRIDGE.enabled and BRIDGE.allow_write(b.get("channel", "")):
                hdr = upstream_headers(self)
                tk = str(ticket or b.get("token") or "")
                if tk and not tk.startswith("TKT") and not hdr.get("Authorization"):
                    hdr["Authorization"] = "Bearer " + tk
                if hdr.get("Authorization") or hdr.get("X-Token"):
                    try:
                        BRIDGE.delete_account(hdr, str(b.get("channel") or ""))
                        upstream_deleted = True
                    except BridgeError as e:
                        append_log("upstream_errors", {"path": u.path, "error": str(e)})
            with LOCK:
                known = any((ticket and r.get("ticket") == ticket)
                            or (idfv and r.get("idfv") == idfv)
                            for r in LOGINS)
            if not known:
                self._send(403, {"error": "login_required",
                                 "hint": "请先登录后再注销；已注销的账号不能重复提交"})
                return
            counts = purge_account(ticket, idfv)
            receipt = ("DEL" + datetime.now().strftime("%Y%m%d%H%M%S")
                       + uuid.uuid4().hex[:8].upper())
            rec = {"receipt": receipt, "ts": now_ts(),
                   "phone_hash": counts["phone_hash"],
                   "idfv_hash": device_hash("idfv:" + idfv) if idfv else "",
                   "channel": b.get("channel", ""),
                   "deleted": {k: v for k, v in counts.items() if k != "phone_hash"},
                   "retained": "匿名交易流水(去标识化) / 广告点击日志(不含账号标识)"}
            with LOCK:
                DELETIONS.append(rec)
            write_deletions()
            audit("account_deletion", {"receipt": receipt, "deleted": rec["deleted"]})
            append_log("account_deletions", rec)
            self._send(200, {"ok": True, "deleted": True, "receipt": receipt,
                             "deleted_at": rec["ts"], "deleted_items": rec["deleted"],
                             "retained": rec["retained"],
                             "upstream_deleted": upstream_deleted,
                             "toast": "账号已注销"})
            return

        # ---- 5. 支付回调 / 会员状态 -------------------------------------------
        if u.path == "/v1/pay/notify":
            no = b.get("order_no", "")
            with LOCK:
                o = ORDERS.get(no)
                if o:
                    o["status"] = "paid"
                    o["paid_at"] = now_ts()
                    VIP[b.get("idfv", "")] = {
                        "level": o["package_id"], "order_no": no,
                        "expire_at": now_ts() + 3 * 365 * 86400,
                    }
                    append_log("orders", dict(o, event="paid"))
            self._send(200, {"ok": bool(o), "order": o})
            return

        if u.path == "/v1/vip/status":
            # 原后台会员状态：读 Yii2 的订单/会员字段，付费成功才解锁
            if BRIDGE and BRIDGE.enabled:
                try:
                    hdr = upstream_headers(self)
                    tk = str(b.get("ticket") or b.get("token") or "")
                    if tk and not tk.startswith("TKT") and not hdr.get("Authorization"):
                        hdr["Authorization"] = "Bearer " + tk
                    st, meta = BRIDGE.vip_status(str(b.get("channel") or ""), hdr,
                                                 str(b.get("order_no") or ""))
                    self._send(200, dict(st, upstream_meta=meta))
                    return
                except BridgeError as e:
                    append_log("upstream_errors", {"path": u.path, "error": str(e)})
            v = VIP.get(b.get("idfv", ""))
            self._send(200, {"is_vip": bool(v), "vip": v})
            return

        if u.path == "/v1/feedback/submit":
            dev = self._feedback_device(str(b.get("device_no") or ""))
            types = b.get("types") or []
            if isinstance(types, str):
                types = [x.strip() for x in re.split(r"[,，]", types) if x.strip()]
            types = [str(x).strip() for x in types if str(x).strip()][:8]
            content = str(b.get("content") or "").strip()[:2000]
            contact = str(b.get("contact") or "").strip()[:128]
            images = [str(x) for x in (b.get("images") or [])
                      if str(x).startswith("/uploads/feedback/")][:9]
            if not types or not content:
                self._send(400, {"ok": False, "error": "invalid",
                                 "msg": "请选择问题类型并填写描述"})
                return
            token = feedback_token(dev)
            if not token:
                self._send(502, {"ok": False, "error": "auth_unavailable",
                                 "msg": "反馈服务暂不可用，请稍后重试"})
                return
            ok, p = feedback_request("/api/feedback/submit",
                                     {"types": types, "content": content,
                                      "contact": contact, "images": images,
                                      "device_no": dev}, token=token)
            if ok and feedback_code(p) in (0, 200):
                fid = (p.get("data") or {}).get("id")
                rec = {"id": fid, "at": now_ts(), "types": types,
                       "content": content[:200], "contact": contact,
                       "images": images, "status": "处理中"}
                feedback_save(dev, rec)
                append_log("feedback", dict(rec, device_no=dev))
                self._send(200, {"ok": True, "id": fid,
                                 "msg": str(p.get("msg") or "反馈已提交，感谢你的反馈"),
                                 "feedback": rec})
                return
            msg = str(p.get("msg") or "提交失败，请稍后重试")
            extra = p.get("data") if isinstance(p.get("data"), dict) else {}
            self._send(200, {"ok": False,
                             "error": "pending" if "处理中" in msg else "submit_failed",
                             "msg": msg,
                             "pending_id": extra.get("pending_id"),
                             "pending_at": extra.get("pending_at")})
            return

        # ---- 真机渠道验证：只对测试渠道开放的自助绑定 -------------------------
        # App 每次 /v1/config 都带 auto_attribution:true，服务端绑定优先于客户端
        # 缓存的渠道号，所以这里绑完不需要卸载重装、不需要清 Keychain。
        if u.path in ("/v1/attribution/test-bind", "/v1/attribution/test-unbind"):
            ch = str(b.get("channel") or "").strip()
            if not test_channel_allowed(ch):
                self._send(403, {"ok": False, "error": "channel_not_allowed",
                                 "msg": "只允许绑定测试渠道：" + ", ".join(sorted(TEST_CHANNELS))})
                return
            ip = norm_ip(client_ip(self))
            ip_key = device_hash("ip:" + ip)
            net_key = device_hash("ip24:" + ip.rsplit(".", 1)[0]) if "." in ip else ""
            keys = [ip_key] + ([net_key] if net_key else [])
            dev = str(b.get("device_id") or "").strip()
            if dev:
                keys.append(device_hash("device_id:" + dev))
            unbinding = u.path.endswith("/test-unbind")
            with LOCK:
                if unbinding:
                    for k in keys:
                        DEVICE_BINDINGS.pop(k, None)
                    # 一键撤销所有验证台测试绑定，避免换网络后旧 IP 残留。
                    for k, rec in list(DEVICE_BINDINGS.items()):
                        if isinstance(rec, dict) and rec.get("method") == "verify_console" \
                                and rec.get("channel") in TEST_CHANNELS:
                            DEVICE_BINDINGS.pop(k, None)
                else:
                    rec = {"channel": ch, "method": "verify_console",
                           "confidence": 1.0, "click_id": "", "ip": ip,
                           "ts": now_ts(), "source": "test_console"}
                    for k in keys:
                        DEVICE_BINDINGS[k] = dict(
                            rec, device_key=k,
                            device_kind=("ip_hash" if k == ip_key else
                                         ("ip24_hash" if k == net_key else "hash")))
            save_attribution_state()
            audit("attribution_test_unbind" if unbinding else "attribution_test_bind",
                  {"channel": ch, "ip": ip, "keys": len(keys)})
            append_log("attributions", {
                "action": "test_unbind" if unbinding else "test_bind",
                "channel": ch, "ip": ip})
            with LOCK:
                now = dict(DEVICE_BINDINGS.get(ip_key) or {})
            self._send(200, {
                "ok": True,
                "action": "unbind" if unbinding else "bind",
                "channel": ch, "ip": ip, "binding": now or None,
                "msg": ("已解绑，重启 App 回到合规态" if unbinding
                        else "已绑定 " + ch + "，完全退出并重新打开 App 即可看到该渠道形态")},
                {"Cache-Control": "no-store"})
            return

        self._send(404, {"error": "not_found", "path": u.path})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="0.0.0.0",
                    help="监听地址: 0.0.0.0 允许同一 WiFi 下的手机访问(真机测试)")
    ap.add_argument("--profile", default=None,
                    help="外部皮肤配置 JSON(渠道表 + 合规首页 + 商店链接)")
    a = ap.parse_args()
    if a.profile:
        load_profile(a.profile)
    load_display_state()
    load_attribution_state()
    load_clock_offsets()
    load_deletions()

    class LANServer(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = LANServer((a.host, a.port), Handler)
    lan_ip = detect_lan_ip()
    print(f"Channel Switch Server on {a.host}:{a.port}", flush=True)
    print(f"  电脑预览 : http://127.0.0.1:{a.port}/admin", flush=True)
    print(f"  手机访问 : http://{lan_ip}:{a.port}/admin   (需同一 WiFi)", flush=True)
    print(f"  真机演示 : http://{lan_ip}:{a.port}/demo", flush=True)
    print(f"  落地页   : http://{lan_ip}:{a.port}/lp?ch=" +
          PROFILE_META.get("default_channel", "organic"), flush=True)
    print(f"  订单页   : /order/<order_no>   登录: /v1/login", flush=True)
    print(f"  管理令牌 : {ADMIN_TOKEN}  (环境变量 ADMIN_TOKEN 可改)", flush=True)
    print(f"  运营态   : {STATE_FILE}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
