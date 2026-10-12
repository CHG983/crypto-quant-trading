#!/usr/bin/env python3
"""serve_proxy.py — 儀表板的 CORS 備援：本機靜態伺服 + 透明代理。

為什麼需要它
-----------
`data-api.binance.vision` 目前回應 `access-control-allow-origin: *`，
所以**正常情況下瀏覽器可以直接呼叫，不需要這個代理**。會用到它的情況：

  * 公司網路 / 代理 / 防火牆把跨域請求擋掉
  * 所在地區對 binance 網域有限制
  * 瀏覽器擴充套件或隱私設定攔截第三方請求

此時本腳本同時做兩件事：
  1. 在 http://127.0.0.1:<port>/ 提供 dashboard.html（同源，免 CORS）
  2. 在 http://127.0.0.1:<port>/binance/** 反向代理到幣安公開端點

儀表板偵測到直連失敗時，會自動改打 `/binance/...`（見 live 引擎中的
`state.proxyMode` 與 `getJson` 的 bases 選擇），因此不需要手動切換。

用法：
    python3 serve_proxy.py                       # 預設 8787
    python3 serve_proxy.py --port 9000
    python3 serve_proxy.py --open                # 啟動後自動開瀏覽器

然後在瀏覽器開啟 http://127.0.0.1:8787/dashboard.html
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPSTREAM = "https://data-api.binance.vision"
ALLOWED_PREFIXES = ("/api/v3/klines", "/api/v3/ticker", "/api/v3/time", "/api/v3/exchangeInfo")
TIMEOUT = 20


class Handler(BaseHTTPRequestHandler):
    server_version = "ctma-proxy/1.0"
    root: Path = Path(".")

    # ---------- 靜態檔 ----------
    def _serve_static(self) -> None:
        rel = self.path.split("?", 1)[0].lstrip("/") or "dashboard.html"
        target = (self.root / rel).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError:
            self.send_error(403, "forbidden")
            return
        if not target.is_file():
            self.send_error(404, "not found")
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".svg": "image/svg+xml",
        }.get(target.suffix.lower(), "application/octet-stream")
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------- 反向代理 ----------
    def _proxy(self) -> None:
        path = self.path[len("/binance"):] or "/"
        ok = any(path.startswith(p) for p in ALLOWED_PREFIXES)
        if not ok:
            self._json(403, {"error": "endpoint not allowed by proxy", "path": path})
            return
        url = UPSTREAM + path
        req = urllib.request.Request(url, headers={"User-Agent": "ctma-proxy/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310
                body = r.read()
                status = r.status
        except urllib.error.HTTPError as exc:
            self._json(exc.code, {"error": f"upstream HTTP {exc.code}", "url": url})
            return
        except Exception as exc:  # noqa: BLE001
            self._json(502, {"error": f"upstream failed: {exc}", "url": url})
            return
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 同源，不需要 CORS 標頭；仍附上以便他源測試
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/binance/"):
            self._proxy()
        elif self.path in ("/", "/dashboard.html", "/index.html"):
            self._serve_static()
        elif self.path.startswith("/__health"):
            self._json(200, {"ok": True, "upstream": UPSTREAM})
        else:
            self._serve_static()

    def log_message(self, fmt: str, *a) -> None:  # noqa: A003
        sys.stderr.write("[proxy] " + (fmt % a) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="儀表板本機伺服 + 幣安 API 反向代理")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--root", default=".")
    ap.add_argument("--open", action="store_true", help="啟動後自動開瀏覽器")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    Handler.root = root
    if not (root / "dashboard.html").exists():
        print(f"[proxy] 警告：{root/'dashboard.html'} 不存在，靜態檔會 404", file=sys.stderr)

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/dashboard.html"
    print(f"[proxy] serving {root}")
    print(f"[proxy] dashboard  {url}")
    print(f"[proxy] binance    http://127.0.0.1:{args.port}/binance/api/v3/...  -> {UPSTREAM}")
    print("[proxy] Ctrl+C 結束")
    if args.open:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[proxy] bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
