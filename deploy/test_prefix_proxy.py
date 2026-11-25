#!/usr/bin/env python3
"""本地模拟 Nginx 的 /mzh 反代，用来在没有 Mac / 没有服务器权限时真实验证整条链路。

启动：
  python server/app.py --port 8793 --host 127.0.0.1 --profile server/profiles/wanneng_rebrand.json
  python deploy/test_prefix_proxy.py --listen 8794 --upstream 8793 \
      --host app.kqi.ink --prefix /mzh --auth-user mzhadmin --auth-pass test123
  python deploy/verify_public.py --base http://127.0.0.1:8794/mzh \
      --user mzhadmin --pass test123 --out evidence/prefix_proxy_verify.json

行为与 deploy/nginx-mizhenhe.conf 一致：剥前缀 + 注入 X-Forwarded-* + /admin 需要 Basic Auth。
"""
import argparse
import base64
import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS = None


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _handle(self, method):
        path = self.path
        incoming = path[len(ARGS.prefix):] if path.startswith(ARGS.prefix) else path
        if not incoming.startswith("/"):
            incoming = "/" + incoming

        if incoming.startswith("/admin") and ARGS.auth_user:
            want = "Basic " + base64.b64encode(
                f"{ARGS.auth_user}:{ARGS.auth_pass}".encode()).decode()
            if self.headers.get("Authorization") != want:
                body = b"401 Authorization Required\n"
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="MZH Admin"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

        length = int(self.headers.get("Content-Length", "0") or 0)
        data = self.rfile.read(length) if length else None
        url = f"http://127.0.0.1:{ARGS.upstream}{incoming}"
        req = urllib.request.Request(url, data=data, method=method)
        for k in ("Content-Type", "Accept", "User-Agent"):
            if self.headers.get(k):
                req.add_header(k, self.headers.get(k))
        req.add_header("Host", ARGS.host)
        req.add_header("X-Real-IP", self.client_address[0])
        req.add_header("X-Forwarded-For", "203.0.113.77")
        req.add_header("X-Forwarded-Proto", "https")
        req.add_header("X-Forwarded-Prefix", ARGS.prefix)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                payload, status, hdrs = r.read(), r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            payload, status, hdrs = e.read(), e.code, dict(e.headers)
        except Exception as e:
            payload = json.dumps({"error": repr(e)}).encode()
            status, hdrs = 502, {"Content-Type": "application/json"}

        self.send_response(status)
        for k in ("Content-Type", "Cache-Control", "X-Config-Signature", "Location"):
            if hdrs.get(k):
                self.send_header(k, hdrs[k])
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_OPTIONS(self):
        self._handle("OPTIONS")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, default=8794)
    ap.add_argument("--upstream", type=int, default=8793)
    ap.add_argument("--host", default="app.kqi.ink")
    ap.add_argument("--prefix", default="/mzh")
    ap.add_argument("--auth-user", default="mzhadmin")
    ap.add_argument("--auth-pass", default="test123")
    ARGS = ap.parse_args()
    # Git Bash 会把以 / 开头的参数做 MSYS 路径转换（/mzh -> C:/.../mzh），
    # 这里兜底规范化，避免前缀没剥掉导致后端收到 /mzh/xxx 而 404。
    if not ARGS.prefix.startswith("/") or ":" in ARGS.prefix or "\\" in ARGS.prefix:
        print(f"[WARN] --prefix 被路径转换污染（{ARGS.prefix}），回退为 /mzh")
        ARGS.prefix = "/mzh"
    ARGS.prefix = "/" + ARGS.prefix.strip("/")
    print(f"prefix proxy : http://127.0.0.1:{ARGS.listen}{ARGS.prefix}"
          f" -> 127.0.0.1:{ARGS.upstream}  Host={ARGS.host}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", ARGS.listen), Proxy).serve_forever()
