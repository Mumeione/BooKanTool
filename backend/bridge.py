"""
PyWebView 桥接层：把 Python 后端能力暴露给前端 JS。

JS 端使用方式：
  await window.pywebview.api.start_task({...})
  window.app.on('progress', cb)  // 监听进度
  window.app.on('log', cb)       // 监听日志
  window.app.on('done', cb)      // 监听结束

设计原则：
  1. 全部方法都是同步 / async-safe；后台线程跑任务，前台线程发事件
  2. 任务可取消：每个任务持有一个 threading.Event
  3. JS 端传 dict / list 这类基本 JSON 类型，自动转换
  4. 输出路径、列表等也用 JSON 友好形态
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import uuid
from typing import Any

import webview

from .api import BookanAPI, BookanAPIError
from .batch import run_batch
from .config import APP_VERSION, load_config, update_config
from .url_parser import ParseError, parse_input

# ────────────── 任务状态（线程间共享） ──────────────
_task_lock = threading.Lock()
_active_tasks: dict[str, dict] = {}  # task_id -> {cancel_event, status, progress, output, error}


# ────────────── JS 暴露类 ──────────────
class BridgeAPI:
    """
    PyWebView 的 js_api 参数会把这个类的所有公开方法暴露成
        window.pywebview.api.<method_name>(...)
    每个方法要返回 JSON 友好的对象。

    ⚠️ 窗口引用必须挂在 `_window`（下划线开头）：
    pywebview 生成 window.pywebview.api 时会递归 getattr 遍历 js_api
    的公开属性（webview/util.py get_functions），一旦走到 Window /
    .NET 控件对象，非 UI 线程的属性访问会死锁在 UI 线程上，
    表现为窗口显示后整体无响应。`_` 开头的属性会被其跳过。
    """

    def __init__(self, window: webview.Window | None = None):
        self._window = window
        self._api = BookanAPI()

    def set_window(self, window: webview.Window) -> None:
        """
        窗口创建后再回绑。
        原因：create_window(js_api=api) 要求 api 先存在，
        而 api 又需要 window 才能推事件 —— 这里打破循环依赖。
        """
        self._window = window

    # ────────────── 工具：向前端推事件 ──────────────
    def _emit(self, event: str, payload: Any) -> None:
        """通过 evaluate_js 把事件推给前端。失败时静默 —— 任务结束时会再发 'done'。"""
        if not self._window:
            return
        js = f"window.__bookan_dispatch({json.dumps(event)}, {json.dumps(payload, ensure_ascii=False, default=str)})"
        # --windowed 下 sys.stdout=None，print 会抛；
        # 推到前端只是 UI 实时反馈，丢一次不影响业务，下次或 'done' 会兜底。
        with contextlib.suppress(Exception):
            self._window.evaluate_js(js)

    # ────────────── 给前端调用的方法 ──────────────

    def health(self) -> dict:
        """前端启动时 ping 用，确认桥接层可用。"""
        return {"ok": True, "version": APP_VERSION, "backend": "python"}

    def get_config(self) -> dict:
        """返回持久化配置（输出目录 / 格式 / 勾选项），供前端恢复上次使用习惯。"""
        return {"ok": True, "config": load_config()}

    def save_config(self, patch: dict) -> dict:
        """前端主动保存使用习惯（如切换格式、选择目录后即时写入）。"""
        try:
            cfg = update_config(patch or {})
            return {"ok": True, "config": cfg}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def parse(self, text: str) -> dict:
        """前端解析用户输入，便于 UI 立即展示 'type/id' 预览。"""
        try:
            rt, iid = parse_input(text)
            return {"ok": True, "resource_type": rt, "issue_id": iid}
        except ParseError as e:
            return {"ok": False, "error": str(e)}

    def resolve_input(self, text: str) -> dict:
        """
        解析输入并拉取资源信息，供前端精简预览：
          • 期刊(type=1)：刊名 + 期数（issueName）
          • 图书(type=3)：书名 + 作者（接口常为空 → 回退出版社）
        """
        try:
            rt, iid = parse_input(text)
        except ParseError as e:
            return {"ok": False, "error": str(e)}
        try:
            issue = self._api.get_issue_info(iid, rt)
        except BookanAPIError as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "resource_type": rt,
            "issue_id": iid,
            "resource_name": issue.resource_name,
            "issue_name": issue.issue_name,
            "author": issue.author,
            "publisher": issue.publisher,
            "pub_date": issue.pub_date,
            "count": issue.count,
        }

    def collect_year_issues(self, issue_id: str, resource_type: int = 1) -> dict:
        """
        「下载全年」：以输入期为中心，按 issueID 前后推算同刊同年的全部期次，
        返回可直接加入输入列表的 URL 清单。仅期刊（type=1）支持。
        """
        try:
            rt = int(resource_type)
        except (TypeError, ValueError):
            rt = 1
        try:
            base = self._api.get_issue_info(str(issue_id), rt)
        except BookanAPIError as e:
            return {"ok": False, "error": str(e)}
        if base.resource_type != 1:
            return {"ok": False, "error": "该资源是图书，不支持按期下载"}
        try:
            issues = self._api.collect_year_issues(base)
        except BookanAPIError as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "count": len(issues),
            "resource_name": base.resource_name,
            "issue_name": base.issue_name,
            "issues": [
                {
                    "issue_id": it.issue_id,
                    "issue_name": it.issue_name,
                    "url": f"https://new.bookan.com.cn/?type=1&id={it.issue_id}",
                }
                for it in issues
            ],
        }

    def start_task(self, payload: dict) -> dict:
        """
        payload:
            inputs: List[str]   —— 多行文本，每条一个 URL/ID
            options: dict       —— 见 batch.run_batch
        返回: {"task_id": "..."}
        """
        task_id = uuid.uuid4().hex[:12]
        cancel_event = threading.Event()

        inputs = payload.get("inputs") or []
        options = payload.get("options") or {}

        if not inputs:
            return {"ok": False, "error": "没有有效的输入"}
        if not options.get("output_dir"):
            return {"ok": False, "error": "请选择输出目录"}

        # 单任务守卫：上一个任务尚未退出（如刚取消还在收尾）时拒绝新任务，
        # 避免两个任务线程同时向前端推 progress 事件造成界面错乱。
        with _task_lock:
            if any(t["status"] == "running" for t in _active_tasks.values()):
                return {"ok": False, "error": "已有任务进行中，请等待完成或取消"}

        os.makedirs(options["output_dir"], exist_ok=True)

        # 记录使用习惯（输出目录 / 格式 / 勾选项），窗口几何由 main.py 关闭时写
        with contextlib.suppress(Exception):
            update_config(options)

        with _task_lock:
            _active_tasks[task_id] = {
                "cancel_event": cancel_event,
                "status": "running",
                "progress": 0.0,
                "output": "",
                "error": "",
            }

        thread = threading.Thread(
            target=self._runner,
            args=(task_id, inputs, options, cancel_event),
            daemon=True,
        )
        thread.start()

        return {"ok": True, "task_id": task_id}

    def cancel_task(self, task_id: str) -> dict:
        with _task_lock:
            task = _active_tasks.get(task_id)
            if not task:
                return {"ok": False, "error": "task 不存在或已结束"}
            task["cancel_event"].set()
        self._emit("log", {"level": "warn", "msg": f"已请求取消 {task_id}"})
        return {"ok": True}

    def get_task_status(self, task_id: str) -> dict:
        with _task_lock:
            t = _active_tasks.get(task_id)
            if not t:
                return {"ok": False, "error": "task 不存在"}
            return {
                "ok": True,
                "status": t["status"],
                "progress": t["progress"],
                "output": t["output"],
                "error": t["error"],
            }

    def choose_directory(self, default_path: str = "") -> dict:
        """前端点击 '选择目录' 时调出系统目录选择对话框。"""
        try:
            result = self._window.create_file_dialog(
                dialog_type=webview.FOLDER_DIALOG,
                directory=default_path or os.path.expanduser("~"),
            )
            if not result:
                return {"ok": False, "error": "未选择"}
            # pywebview 5.x 返回 list[str] / str 视平台而定
            path = result if isinstance(result, str) else (result[0] if result else "")
            if not path:
                return {"ok": False, "error": "未选择"}
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_in_explorer(self, path: str) -> dict:
        """在前端点击 '打开输出目录' 时调用。"""
        try:
            if not path:
                return {"ok": False, "error": "路径为空"}
            if os.path.isfile(path):
                path = os.path.dirname(path)
            if not os.path.exists(path):
                return {"ok": False, "error": "路径不存在"}
            if hasattr(os, "startfile"):
                os.startfile(path)  # Windows
            else:
                _open_with_default(path)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ────────────── 后台执行器 ──────────────
    def _runner(
        self, task_id: str, inputs: list, options: dict, cancel_event: threading.Event
    ) -> None:
        """跑在后台线程里；每个事件通过 evaluate_js 推前端。"""
        try:
            self._emit("started", {"task_id": task_id, "total": len(inputs)})
            self._emit("log", {"level": "info", "msg": f"任务 {task_id} 开始，共 {len(inputs)} 条"})

            def on_log(msg: str):
                self._emit("log", {"level": "info", "msg": msg})

            def on_progress(idx: int, total: int, label: str, fraction=None):
                # fraction 为当前任务内部进度 0..1；全局 = (idx-1+fraction)/total。
                # 这样图片下载/目录/合成各阶段按权重推进，PDF 合成不再"卡 100%"。
                if fraction is None:
                    ratio = idx / max(total, 1)
                else:
                    ratio = (idx - 1 + max(0.0, min(1.0, fraction))) / max(total, 1)
                self._emit(
                    "progress",
                    {
                        "task_id": task_id,
                        "ratio": ratio,
                        "current": idx,
                        "total": total,
                        "label": label,
                    },
                )
                with _task_lock:
                    _active_tasks[task_id]["progress"] = ratio

            results = run_batch(
                inputs=inputs,
                options=options,
                api=self._api,
                on_log=on_log,
                on_progress=on_progress,
                cancel_event=cancel_event,
            )

            # 汇总
            output_files = [r.output_path for r in results if r.output_path]
            any_error = [r for r in results if not r.ok]
            summary = {
                "task_id": task_id,
                "total": len(results),
                "success": len(results) - len(any_error),
                "failed": len(any_error),
                "cancelled": cancel_event.is_set(),
                "errors": [{"input": r.spec.raw_input, "error": r.error} for r in any_error],
                "output_files": output_files,
            }
            with _task_lock:
                _active_tasks[task_id]["status"] = (
                    "succeeded"
                    if not any_error
                    else ("cancelled" if cancel_event.is_set() else "failed")
                )
                _active_tasks[task_id]["output"] = json.dumps(summary, ensure_ascii=False)
                _active_tasks[task_id]["progress"] = 1.0

            self._emit("done", summary)

        except Exception as e:
            with _task_lock:
                _active_tasks[task_id]["status"] = "failed"
                _active_tasks[task_id]["error"] = str(e)
            self._emit("done", {"task_id": task_id, "error": str(e)})


# ────────────── 跨平台文件打开 ──────────────
def _open_with_default(path: str) -> None:
    import subprocess

    with contextlib.suppress(Exception):
        subprocess.Popen(["xdg-open", path])
