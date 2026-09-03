"""
浏览器联调模式：本地 HTTP 服务器 + SSE 事件流，模拟安卓桥接协议。

用途：开发期不打包 APK，直接在桌面浏览器里调试前端与桥接交互
  python main.py --web        # 然后浏览器打开 http://127.0.0.1:8788

正式安卓打包不走本模块 —— 那条路径用 pywebview 的 Android 后端
（见 backend/android_compat.py 与 main.py 的 is_android 分支）。

桥接协议（frontend/android.js 垫片负责对接，前端 app.js 零改动）：
  • POST /bridge/<method>   body={"args":[...]} → BridgeAPI 同名方法的返回值 JSON
  • GET  /events            SSE 事件流，data={"event":..,"data":..}
                            （对应桌面端 evaluate_js 推送的事件）
  • GET  / 与静态文件        服务 frontend/，index.html 由服务端注入桥接脚本
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .android_compat import AndroidBridge, default_output_dir, setup_android_paths
from .bridge import BridgeAPI
from .config import APP_VERSION, load_config

# 项目根目录（backend/ 的上一级）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND_DIR = os.path.join(_ROOT, "frontend")

# 本地联调端口；BOOKAN_PORT 可覆盖
_DEFAULT_PORT = 8788

# 允许直接下发的静态文件白名单（防止路径穿越）
_STATIC_FILES = {"app.js", "android.js", "style.css", "logo.png", "favicon.ico"}

_STATIC_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
}


# ────────────── SSE 事件中心 ──────────────
class _SseHub:
    """管理 SSE 订阅者队列；publish 对应桌面端的一次 evaluate_js。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: list[queue.Queue] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._clients.remove(q)

    def publish(self, event: str, payload: Any) -> None:
        item = {"event": event, "data": payload}
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            with contextlib.suppress(queue.Full):
                q.put_nowait(item)


class _WebDebugBridge(AndroidBridge):
    """HTTP 桥：事件改走 SSE（浏览器里没有 pywebview 的 evaluate_js）。"""

    def __init__(self, hub: _SseHub):
        super().__init__()
        self._hub = hub

    def _emit(self, event: str, payload: Any) -> None:
        with contextlib.suppress(Exception):
            self._hub.publish(event, payload)


# ────────────── index.html 注入（对应桌面 on_loaded 的 evaluate_js） ──────────────
def _build_injected_head(default_dir: str, cfg: dict) -> str:
    dir_js = json.dumps(default_dir, ensure_ascii=False)
    cfg_js = json.dumps(cfg, ensure_ascii=False, default=str).replace("</", "<\\/")
    return (
        "<script>window.__bookan_android__ = true;</script>"
        '<script src="android.js"></script>'
        "<script>"
        "window.__bookan_dispatch = function (event, data) {"
        "  if (window.app && typeof window.app.dispatch === 'function') {"
        "    window.app.dispatch(event, data);"
        "  }"
        "};"
        f"window.__bookan_default_dir = {dir_js};"
        f'window.__bookan_version = "v{APP_VERSION}";'
        f"window.__bookan_config = {cfg_js};"
        "</script>"
    )


# ────────────── HTTP Handler ──────────────
def _make_handler(bridge: BridgeAPI, hub: _SseHub, injected_head: str):
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "BookanTool"

        # 静默默认访问日志
        def log_message(self, fmt: str, *args) -> None:  # noqa: N802
            pass

        # ── 路由 ──
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/events":
                self._handle_events()
            elif path in ("/", "/index.html"):
                self._send_file("index.html", inject=True)
            else:
                name = path.lstrip("/")
                if name in _STATIC_FILES:
                    self._send_file(name)
                else:
                    self._send_404()

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if not path.startswith("/bridge/"):
                self._send_404()
                return
            method = path[len("/bridge/") :]
            if not method or method.startswith("_") or not hasattr(bridge, method):
                self._send_404()
                return

            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                data = json.loads(raw.decode("utf-8")) if raw else {}
                args = data.get("args", []) if isinstance(data, dict) else data
                if not isinstance(args, list):
                    args = []
                result = getattr(bridge, method)(*args)
            except Exception as e:  # 异常以 JSON 形式回传，前端按 r.ok 处理
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            self._send_json(result)

        # ── 响应工具 ──
        def _send_json(self, obj: Any, status: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with contextlib.suppress(OSError):
                self.wfile.write(body)

        def _send_404(self) -> None:
            self._send_json({"ok": False, "error": "not found"}, status=404)

        def _send_file(self, name: str, inject: bool = False) -> None:
            full = os.path.join(_FRONTEND_DIR, name)
            if not os.path.isfile(full):
                self._send_404()
                return
            with open(full, "rb") as f:
                body = f.read()
            if inject:
                body = body.replace(b"</head>", injected_head.encode("utf-8") + b"</head>", 1)
            ext = os.path.splitext(name)[1].lower()
            self.send_response(200)
            self.send_header("Content-Type", _STATIC_TYPES.get(ext, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")  # 升级后避免旧缓存
            self.end_headers()
            with contextlib.suppress(OSError):
                self.wfile.write(body)

        # ── SSE：服务端推送事件流（对应桌面 evaluate_js 通道） ──
        def _handle_events(self) -> None:
            q = hub.subscribe()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                while True:
                    try:
                        item = q.get(timeout=15)
                        payload = json.dumps(item, ensure_ascii=False, default=str)
                        self.wfile.write(f"data: {payload}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": hb\n\n")  # 心跳注释，保活连接
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # 页面刷新 / 客户端断开属正常
            finally:
                hub.unsubscribe(q)

    return _Handler


# ────────────── 联调入口 ──────────────
def run_web_debug() -> None:
    """浏览器联调：配置路径 → 起 HTTP 服务并永久阻塞。Ctrl+C 退出。"""
    setup_android_paths()  # 复用安卓路径策略，联调环境与真实环境一致
    default_dir = default_output_dir()
    cfg = load_config()
    hub = _SseHub()
    bridge = _WebDebugBridge(hub)
    handler = _make_handler(bridge, hub, _build_injected_head(default_dir, cfg))

    port = int(os.environ.get("BOOKAN_PORT") or _DEFAULT_PORT)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    print(f"[web-debug] BookanTool v{APP_VERSION} 桥接服务就绪: http://127.0.0.1:{port}")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
