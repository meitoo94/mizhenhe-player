# -*- coding: utf-8 -*-
"""
Yii2 原后台（app.kqi.ink/site）接口适配层 · 零第三方依赖

目的
----
米帧盒播放器继续用自己原有的 /v1/* 协议，后端把请求桥接到用户已有的
PHP Yii2 后台 /api/* 接口上，使「用户管理 / 充值记录 / 支付配置 /
数据统计 / 百度转化回传 / 账号注销」这些能力由原后台统一承载。

安全边界（重要）
--------------
1. 只读接口（配置、套餐、渠道状态、会员状态）对所有渠道生效；
2. 写接口（安装上报、建号、下单、注销）默认只对 MZH_UPSTREAM_TEST_CHANNELS
   白名单里的**新建测试渠道**生效，绝不往 bbdd* / mt* / default 等在跑量的
   渠道写数据，也不修改 Yii2 的任何渠道配置或源码；
3. 上游异常时抛 BridgeError，调用方自动回落到米帧盒本地实现，不影响可用性；
4. 开关默认关闭：MZH_UPSTREAM_ENABLED=1 才启用。

接口对照
--------
  /v1/attribution/resolve  ->  POST /api/channel/report        安装/渠道归因上报
  /v1/config               ->  GET  /api/app/config            渠道展示配置
                              GET  /api/channel/config?qd=    渠道启停
                              GET  /api/pay/vip-plans?scope=mizhenhe
                                                      米帧盒VIP套餐(后台栏目可配)
  /v1/login                ->  POST /api/auth/guest            设备号一键建号/登录
                              GET  /api/auth/me                会话校验
  /v1/order                ->  POST /api/pay/vip-create       支付宝 H5 下单
                              GET  /api/pay/status             订单/会员状态
  /v1/vip/status           ->  GET  /api/pay/vip-plans        会员状态
  /v1/account/delete       ->  POST /api/auth/delete-account  账号注销
  后台面板                 ->  GET  /api/pay/packs, /api/user/orders,
                              POST /api/user/track, /api/feedback/submit
"""

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://app.kqi.ink"
DEFAULT_TIMEOUT = 8

# 能力名 -> (HTTP 方法, 上游路径, 是否写操作)
ROUTES = {
    "channel_report":   ("POST", "/api/channel/report", True),
    "channel_config":   ("GET",  "/api/channel/config", False),
    "app_config":       ("GET",  "/api/app/config", False),
    "vip_plans":        ("GET",  "/api/pay/vip-plans", False),
    "packs":            ("GET",  "/api/pay/packs", False),
    "auth_guest":       ("POST", "/api/auth/guest", True),
    "auth_login":       ("POST", "/api/auth/login", True),
    "auth_me":          ("GET",  "/api/auth/me", False),
    "auth_delete":      ("POST", "/api/auth/delete-account", True),
    "vip_create":       ("POST", "/api/pay/vip-create", True),
    "pay_status":       ("GET",  "/api/pay/status", False),
    "user_orders":      ("GET",  "/api/user/orders", False),
    "user_track":       ("POST", "/api/user/track", True),
    "feedback_submit":  ("POST", "/api/feedback/submit", True),
}

# 中文说明，后台面板直接展示
LABELS = {
    "channel_report":  "安装/渠道归因上报（→ 数据统计、百度转化回传）",
    "channel_config":  "渠道启停与名称（→ 渠道管理）",
    "app_config":      "渠道展示配置（→ 渠道展示）",
    "vip_plans":       "VIP 套餐（→ 米帧盒VIP套餐，独立 scope=mizhenhe）",
    "packs":           "充值套餐（→ 充值套餐配置）",
    "auth_guest":      "设备号一键建号（→ 用户管理）",
    "auth_login":      "账号密码登录（→ 用户管理）",
    "auth_me":         "会话校验（→ 用户管理）",
    "auth_delete":     "账号注销（→ 用户管理、审核 5.1.1(v)）",
    "vip_create":      "支付宝 H5 下单（→ 充值记录、支付配置、米帧盒档位）",
    "pay_status":      "订单/会员状态（→ 充值记录）",
    "user_orders":     "我的订单（→ 充值记录）",
    "user_track":      "行为埋点（→ 数据统计）",
    "feedback_submit": "意见反馈（→ 用户反馈中心）",
}


class BridgeError(Exception):
    """上游不可用 / 返回业务错误。调用方应回落到本地实现。"""

    def __init__(self, message, code=0, payload=None):
        super().__init__(message)
        self.code = code
        self.payload = payload


class Bridge:
    def __init__(self):
        self.base = (os.environ.get("MZH_UPSTREAM_BASE", DEFAULT_BASE)
                     or DEFAULT_BASE).rstrip("/")
        self.enabled = os.environ.get("MZH_UPSTREAM_ENABLED", "0") == "1"
        # 米帧盒专用第三套档位；小说 / 浏览器两套在售档位完全不读、不改。
        self.scope = os.environ.get("MZH_UPSTREAM_VIP_SCOPE", "mizhenhe") or "mizhenhe"
        self.timeout = float(os.environ.get("MZH_UPSTREAM_TIMEOUT", DEFAULT_TIMEOUT))
        self.writes_test_only = os.environ.get("MZH_UPSTREAM_WRITES_TEST_ONLY", "1") == "1"
        raw = os.environ.get("MZH_UPSTREAM_TEST_CHANNELS", "mzhtest")
        self.test_channels = {x.strip() for x in str(raw).split(",") if x.strip()}
        # 需要登录态的能力（后台面板展示用，实际 token 由客户端透传）
        self.authed = {"auth_me", "auth_delete", "vip_create",
                       "pay_status", "user_orders", "user_track"}
        self._ssl = ssl.create_default_context()

    # ---------------------------------------------------------------- 策略
    def allow_write(self, channel=""):
        """写操作是否放行：默认只允许新建的测试渠道。"""
        if not self.writes_test_only:
            return True
        return str(channel or "").strip() in self.test_channels

    def describe(self):
        return {
            "enabled": self.enabled,
            "base": self.base,
            "vip_scope": self.scope,
            "writes_test_only": self.writes_test_only,
            "test_channels": sorted(self.test_channels),
            "timeout": self.timeout,
            "routes": [{"key": k, "label": LABELS.get(k, k),
                        "method": r[0], "path": r[1], "write": r[2],
                        "need_token": k in self.authed}
                       for k, r in ROUTES.items()],
        }

    # ---------------------------------------------------------------- 请求
    def call(self, name, params=None, headers=None, method=None, channel="",
             allow_write=None):
        """调用上游，返回 (data, meta)。失败抛 BridgeError。"""
        if name not in ROUTES:
            raise BridgeError("unknown_upstream_route:" + str(name))
        verb, path, is_write = ROUTES[name]
        verb = method or verb
        if is_write and (self.allow_write(channel) if allow_write is None else allow_write) is False:
            raise BridgeError("write_blocked_by_channel_guard", 403,
                              {"channel": channel, "allowed": sorted(self.test_channels)})

        params = dict(params or {})
        hdr = {
            "Accept": "application/json",
            "User-Agent": "MizhenheBridge/1.0",
        }
        # 渠道识别：Yii2 的 ChannelDisplay / PayController 读这两个头
        if channel:
            hdr["X-Channel"] = str(channel)
        for k, v in (headers or {}).items():
            if v and k.lower() in ("authorization", "x-token", "x-device", "x-channel"):
                hdr[k] = str(v)

        url = self.base + path
        body = None
        if verb == "GET":
            if params:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        else:
            body = json.dumps(params, ensure_ascii=False).encode("utf-8")
            hdr["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=hdr, method=verb)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl) as resp:
                raw = resp.read().decode("utf-8", "replace")
                status = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            status = e.code
        except Exception as e:
            raise BridgeError("upstream_unreachable:" + type(e).__name__ + ":" + str(e)[:120])

        ms = int((time.time() - t0) * 1000)
        try:
            payload = json.loads(raw)
        except Exception:
            raise BridgeError("upstream_non_json:" + raw[:120], status)

        code = int(payload.get("code", 0) or 0)
        data = payload.get("data")
        meta = {"http": status, "code": code, "msg": payload.get("msg", ""),
                "ms": ms, "path": path, "upstream": True}
        # HTTP 401/403/404 属于业务错误，也要让调用方回落
        if code not in (0, 200) or status >= 400:
            raise BridgeError(str(payload.get("msg") or "upstream_error"), code or status, payload)
        return data, meta

    # ---------------------------------------------------------------- 组合能力
    def upstream_config(self, channel, headers=None):
        """聚合 /api/app/config + /api/channel/config + /api/pay/vip-plans。"""
        out = {"channel": channel, "source": "yii2"}
        if channel:
            try:
                d, m = self.call("channel_config", {"qd": channel}, headers, channel=channel)
                out["channel_state"] = d or {}
                out["channel_state_meta"] = m
            except BridgeError as e:
                out["channel_state_error"] = str(e)
        try:
            d, m = self.call("app_config", {}, headers, channel=channel)
            out["display"] = d or {}
            out["display_meta"] = m
        except BridgeError as e:
            out["display_error"] = str(e)
        try:
            d, m = self.call("vip_plans", {"scope": self.scope}, headers, channel=channel)
            out["vip_plans"] = d or {}
            out["vip_plans_meta"] = m
        except BridgeError as e:
            out["vip_plans_error"] = str(e)
        if "display_error" in out and "channel_state_error" in out and "vip_plans_error" in out:
            raise BridgeError("all_upstream_config_calls_failed")
        return out

    def vip_packages(self, channel, headers=None, plans=None):
        """把 Yii2 后台的 VIP 档位转成米帧盒的 packages 结构。

        plans 传入 upstream_config() 已拿到的结果时不再重复请求上游。
        """
        if plans is None:
            d, _ = self.call("vip_plans", {"scope": self.scope}, headers, channel=channel)
        else:
            d = plans
        items = (d or {}).get("items") or []
        packages = []
        for p in items:
            pid = p.get("id") or p.get("plan_id") or p.get("code")
            if pid is None:
                continue
            price = p.get("price", p.get("amount", 0))
            try:
                price = float(price)
            except (TypeError, ValueError):
                price = 0.0
            origin = p.get("origin_price", p.get("origin", 0))
            try:
                origin = float(origin or 0)
            except (TypeError, ValueError):
                origin = 0.0
            try:
                days = int(p.get("days") or 0)
            except (TypeError, ValueError):
                days = 0
            badge = str(p.get("badge") or p.get("bonus_text") or "")
            recommend = bool(p.get("is_recommend"))
            # 原生 App 的 VIPPackage 是严格 Codable：origin_price / perk / tag
            # 三个字段缺任意一个，整个 ConfigPayload 都会解码失败并回落到
            # 内置 normal 首页（表现为「绑了渠道却换不了形态」）。
            # 后台栏目只存名称 / 天数 / 价格 / 角标，这里补齐 App 需要的展示文案。
            perk = ("终身有效 · 解锁全部功能" if days >= 3650
                    else ("有效期 %d 天 · 解锁全部功能" % days if days > 0
                          else "开通即解锁全部功能"))
            tag = badge or ("超值推荐" if recommend else "")
            packages.append({
                "id": "yii2_" + str(pid),
                "plan_id": pid,
                "name": str(p.get("title") or p.get("name") or ("VIP-" + str(pid))),
                "price": price,
                "price_text": str(p.get("price_text") or ("¥" + format(price, ".2f"))),
                "days": days,
                "badge": badge,
                "recommend": recommend,
                "source": "yii2",
                "origin_price": origin,
                "perk": perk,
                "tag": tag,
            })
        return packages, d or {}

    def login(self, device_id, platform="ios", channel="", headers=None):
        """设备号一键建号（Yii2 用户管理可见），返回 (ticket, is_vip, raw)。"""
        d, meta = self.call("auth_guest",
                            {"device_id": device_id, "platform": platform, "qd": channel},
                            headers, channel=channel)
        d = d or {}
        ticket = (d.get("token") or d.get("access_token")
                  or (d.get("device_account") or {}).get("token") or "")
        is_vip = bool(d.get("is_vip") or (d.get("user") or {}).get("is_vip"))
        return ticket, is_vip, {"payload": d, "meta": meta}

    def create_order(self, plan_id, channel="", headers=None, pay_type="alipay"):
        """调 Yii2 /api/pay/vip-create，返回米帧盒订单结构。"""
        d, meta = self.call("vip_create",
                            {"plan_id": plan_id, "scope": self.scope, "pay_type": pay_type},
                            headers, channel=channel)
        d = d or {}
        return {
            "order_no": str(d.get("order_no") or ""),
            "channel": channel,
            "package_id": "yii2_" + str(plan_id),
            "package_name": str(d.get("title") or ("VIP-" + str(plan_id))),
            "amount": float(d.get("amount") or 0),
            "status": "pending",
            "pay_type": d.get("pay_type") or pay_type,
            "pay_mode": d.get("pay_mode") or "wap",
            "cashier_url": str(d.get("pay_url") or ""),
            "expires_at": int(d.get("expires_at") or 0),
            "upstream": True,
        }, {"payload": d, "meta": meta}

    def vip_status(self, channel="", headers=None, order_no=""):
        """会员状态：优先用订单状态，其次用套餐接口的会员字段。"""
        if order_no:
            d, meta = self.call("pay_status", {"order_no": order_no}, headers, channel=channel)
            d = d or {}
            return {"is_vip": bool(d.get("is_vip") or d.get("is_browser_vip")),
                    "paid": bool(d.get("paid")), "order_status": d.get("status"),
                    "vip_expire_at": d.get("browser_vip_expire_at") or d.get("vip_expire_at"),
                    "source": "yii2"}, meta
        d, meta = self.call("vip_plans", {"scope": self.scope}, headers, channel=channel)
        d = d or {}
        return {"is_vip": bool(d.get("is_vip") or d.get("is_browser_vip")),
                "vip_expire_at": d.get("vip_expire_at") or d.get("browser_vip_expire_at"),
                "vip_days_left": d.get("vip_days_left") or 0,
                "source": "yii2"}, meta

    def delete_account(self, headers=None, channel=""):
        d, meta = self.call("auth_delete", {"confirm": "注销"}, headers, channel=channel)
        return d, meta

    def probe(self, channel="", headers=None):
        """只读体检：逐个打一遍不改数据的接口，后台面板用。"""
        result = []
        for key, label in LABELS.items():
            verb, path, is_write = ROUTES[key]
            if is_write or key in self.authed:
                result.append({"key": key, "label": label, "path": path,
                               "status": "skipped", "reason": "写操作/需登录，体检跳过"})
                continue
            params = {}
            if key == "channel_config":
                params = {"qd": channel or "mzhtest"}
            elif key == "vip_plans":
                params = {"scope": self.scope}
            try:
                _, meta = self.call(key, params, headers, channel=channel)
                result.append({"key": key, "label": label, "path": path,
                               "status": "ok", "http": meta["http"], "ms": meta["ms"],
                               "msg": meta["msg"]})
            except BridgeError as e:
                result.append({"key": key, "label": label, "path": path,
                               "status": "fail", "http": e.code, "error": str(e)[:160]})
        return result


_BRIDGE = None


def get_bridge():
    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = Bridge()
    return _BRIDGE


def upstream_headers(handler):
    """从米帧盒请求里抽出 Yii2 需要的头，原样透传。"""
    out = {}
    for src, dst in (("Authorization", "Authorization"), ("X-Token", "X-Token"),
                     ("X-Device", "X-Device"), ("X-Channel", "X-Channel")):
        v = handler.headers.get(src)
        if v:
            out[dst] = v
    return out
