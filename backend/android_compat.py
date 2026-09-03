"""
安卓端适配层：路径策略 + 桥接行为覆盖（pywebview Android 后端）。

pywebview 6.x 在 p4a 环境自动启用 Android 后端（guilib 检测
sys.getandroidapilevel），js_api / evaluate_js / events.loaded 通道
与桌面完全一致，因此 backend/bridge.py 与前端 app.js 均无需改动。
本模块只处理安卓特有的差异点：
  • Android 没有 /tmp —— TMPDIR 指到应用私有目录（batch 的
    bookan_tmp_* 缓存清扫与 TemporaryDirectory 依赖 tempfile）
  • 默认下载目录 —— 免权限直写公共下载目录 Download/bookantool，
    不可写时回退应用专属目录（纯文件系统探测，不碰 pyjnius）
  • 打开下载目录 —— 拉起系统文件管理器（pyjnius 仅主线程调用）
"""

from __future__ import annotations

import contextlib
import os
import queue
import tempfile

from .bridge import BridgeAPI
from .config import android_base_dir, android_external_files_dir


def setup_android_paths() -> str:
    """
    配置安卓下的临时目录，必须在任何流水线运行前调用。
    返回应用私有基础目录。
    """
    base = android_base_dir()
    tmp = os.path.join(base, "tmp")
    os.makedirs(tmp, exist_ok=True)
    os.environ["TMPDIR"] = tmp
    tempfile.tempdir = tmp
    return base


PUBLIC_DOWNLOAD_DIR = "/storage/emulated/0/Download/bookantool"
_output_dir_cache: str | None = None


def _probe_writable(path: str) -> bool:
    """探测目录可写：建目录 + 写删一个探针文件（纯 os 调用，线程安全）。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".bookan_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def default_output_dir() -> str:
    """
    安卓默认下载目录（免存储权限，不可自定义）：
      1. 公共下载目录 Download/bookantool —— 系统文件管理器直接可见；
         实测免权限可直写（失败则走 2）
      2. 应用外部专属目录 Android/data/<pkg>/files/downloads —— 永远可写，
         但 Android 11+ 文件管理器不可见（兜底）

    结果缓存：目录可写性在进程生命周期内不会翻转，只探测一次。
    """
    global _output_dir_cache
    if _output_dir_cache:
        return _output_dir_cache

    path = PUBLIC_DOWNLOAD_DIR
    if not _probe_writable(path):
        base = android_external_files_dir() or android_base_dir()
        path = os.path.join(base, "downloads")
        if not _probe_writable(path):  # 极端情况（存储挂载异常）最后兜底
            path = os.path.join(android_base_dir(), "downloads")
            os.makedirs(path, exist_ok=True)
    _output_dir_cache = path
    return path


# ────────────── 拉起系统文件管理器 ──────────────
# 约束：pyjnius 只能在 Python 主线程（SDLThread）安全调用；
# 后台线程（js_api 的 Java 线程 / 任务线程）调用会 native abort。
# 因此 open_in_explorer 只置 pending 标志，前端 reload 后由
# on_loaded（主线程）执行 do_pending_main_actions。
_open_dir_pending = False


def do_pending_main_actions() -> None:
    """主线程调用（on_loaded）：执行前端排队的 JNI 动作（打开文件管理器）。"""
    global _open_dir_pending
    if _open_dir_pending:
        _open_dir_pending = False
        _start_file_manager()


def _start_file_manager() -> None:
    """拉起系统文件管理器并定位到下载目录（pyjnius 仅主线程调用）。
    三级回退：DocumentsUI 深链 → 系统目录选择器 → 下载界面。"""
    from urllib.parse import quote

    doc = "content://com.android.externalstorage.documents/document/primary%3A" + quote(
        "Download/bookantool", safe=""
    )
    try:
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        intent = Intent(Intent.ACTION_VIEW)
        intent.setDataAndType(Uri.parse(doc), "vnd.android.document/directory")
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        activity.startActivity(intent)
        return
    except Exception:
        pass
    try:
        # 回退：系统目录选择器（无需返回结果，纯当文件管理器用）
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
        intent.putExtra(
            "android.provider.extra.INITIAL_URI",
            Uri.parse("content://com.android.externalstorage.documents/document/primary%3ADownload"),
        )
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        activity.startActivity(intent)
        return
    except Exception:
        pass
    try:
        # 最终回退：系统下载界面
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        Intent = autoclass("android.content.Intent")
        DownloadManager = autoclass("android.app.DownloadManager")
        activity.startActivity(Intent(DownloadManager.ACTION_VIEW_DOWNLOADS))
    except Exception:
        pass  # 无任何可拉起的组件时静默失败，前端 toast 文案已指路


class AndroidBridge(BridgeAPI):
    """仅覆盖 Android 后端不支持的桌面交互；js_api 通道全部复用。

    事件推送：BridgeAPI 默认用 evaluate_js，但其 Android 实现经 pyjnius
    （@run_on_ui_thread），从后台任务线程调用会触发 native 崩溃
    （tombstone 实锤：SIGSEGV / CheckJNI abort）。改为事件入队 +
    前端定时调 pull_events() 拉取（js_api 方向从 Java 线程进入
    Python，安全且已在解析/配置等调用中验证）。
    """

    def __init__(self) -> None:
        super().__init__(window=None)
        self._events: queue.Queue[dict] = queue.Queue(maxsize=1024)

    def _emit(self, event: str, payload: object) -> None:
        with contextlib.suppress(queue.Full):
            self._events.put_nowait({"event": event, "data": payload})

    def pull_events(self) -> list:
        """前端轮询入口：取走全部待派发事件（js_api 调用，线程安全）。"""
        out: list = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                break
        return out

    def start_task(self, payload: dict) -> dict:
        """安卓下载目录固定为 Download/bookantool，忽略前端任何自定义路径。"""
        options = ((payload or {}).get("options") or {})
        options["output_dir"] = default_output_dir()
        payload["options"] = options
        return super().start_task(payload)

    def open_in_explorer(self, path: str = "") -> dict:
        """拉起系统文件管理器定位下载目录（零 pyjnius 请求路径：置 pending 标志，
        前端 reload 后由主线程 startActivity —— pyjnius 仅主线程调用，安全）。"""
        global _open_dir_pending
        _open_dir_pending = True
        return {"ok": True, "error": "正在打开文件管理器…"}
