"""
图片版流水线：探测 jpage → 并发下载 → 可选压缩 → 返回本地路径列表。
核心特性:
  1. jpage 自动探测（解决部分资源走 CDN 节点不同的问题）
  2. 进度回调：每张图片下载完后回调 on_progress(done, total, page)
  3. 取消支持：通过 threading.Event 取消未下载完的任务
  4. 可选压缩：三档（轻度/中度/高度），JPEG 重编码 + 限制最大宽度
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from PIL import Image

from .api import BookanAPI, BookanAPIError, build_image_url
from .config import (
    COMPRESSION_LEVELS,
    IMAGE_DOWNLOAD_THREADS,
    IMAGE_MAX_RETRIES,
    IMAGE_SIZE_FULL,
    JPAGE_DEFAULT,
    JPAGE_PROBE_RANGE,
)
from .models import IssueInfo, PageHash

ProgressCB = Callable[[int, int, int], None]  # (done, total, current_page)


@dataclass
class ImagePipelineResult:
    """图片流水线的输出。"""

    image_paths: list[str]  # 按 page 升序排列的本地 jpg 路径
    page_numbers: list[int]  # 与 image_paths 对齐的物理页号（来自 getHash）
    temp_dir: str  # 使用的临时目录（由调用方决定是否清理）
    total_pages: int  # 实际下载成功的页数


class ImagePipeline:
    """图片下载流水线。"""

    def __init__(self, api: BookanAPI):
        self.api = api

    # ────────────── 主入口 ──────────────
    def run(
        self,
        issue: IssueInfo,
        temp_dir: str,
        on_progress: ProgressCB | None = None,
        on_log: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        compress: bool = False,
        compress_level: int = 1,
    ) -> ImagePipelineResult:
        """
        流程：
          1. 拉取 hash 列表
          2. 确定 jpage 节点（优先用接口给出的 jpg/webp 字段，失败才探测）
          3. 并发下载所有页
          4. （可选）按档位压缩
          5. 返回结果
        """
        log = on_log or (lambda m: None)
        cancel_event = cancel_event or threading.Event()

        os.makedirs(temp_dir, exist_ok=True)

        # 1) 拉 hash
        log(f"拉取图片 hash 列表，共 {issue.count} 页…")
        hashes = self.api.get_hashes(
            resource_id=issue.resource_id,
            issue_id=issue.issue_id,
            page_count=issue.count,
            resource_type=issue.resource_type,
        )
        # 排序：按 page 升序
        hashes = sorted(hashes, key=lambda h: h.page)
        total = len(hashes)

        if total == 0:
            raise BookanAPIError("empty", "hash 列表为空")
        if issue.count and total < issue.count:
            log(f"  注意：接口声明 {issue.count} 页，实际返回 {total} 条 hash，以实际为准")

        # 2) jpage 节点：接口已直接给出，仅在缺失或校验失败时才探测
        jpage_no = self._resolve_jpage(issue, hashes[0].hash, log)

        # 3) 并发下载
        log(f"开始并发下载（线程={IMAGE_DOWNLOAD_THREADS}）…")
        page_path_pairs, done_count, failed = self._download_all(
            hashes=hashes,
            jpage=jpage_no,
            rid=issue.resource_id,
            iid=issue.issue_id,
            temp_dir=temp_dir,
            on_progress=on_progress,
            cancel_event=cancel_event,
        )

        if cancel_event.is_set():
            log("任务已取消")
            paths = [p for _, p in page_path_pairs]
            return ImagePipelineResult(
                image_paths=paths,
                page_numbers=[n for n, _ in page_path_pairs],
                temp_dir=temp_dir,
                total_pages=done_count,
            )

        log(f"下载完成: 成功 {done_count}/{total}，失败 {failed}")

        if done_count == 0:
            raise BookanAPIError("download", "所有页面下载均失败")

        paths = [p for _, p in page_path_pairs]

        # 4) 可选：按档位压缩（默认关闭，保持原画质）
        if compress:
            before = sum(os.path.getsize(p) for p in paths)
            paths = _compress_pages(paths, level=compress_level, on_log=log)
            after = sum(os.path.getsize(p) for p in paths)
            if before:
                log(
                    f"压缩完成: {before / 1024 / 1024:.1f}MB → {after / 1024 / 1024:.1f}MB "
                    f"（{after / before * 100:.0f}%）"
                )

        page_numbers = [n for n, _ in page_path_pairs]

        return ImagePipelineResult(
            image_paths=paths,
            page_numbers=page_numbers,
            temp_dir=temp_dir,
            total_pages=len(paths),
        )

    # ────────────── jpage 节点确定 ──────────────
    def _resolve_jpage(self, issue: IssueInfo, sample_hash: str, log) -> int:
        """
        优先使用 issueInfoList 返回的 jpg / webp 字段（实测值如 '8'）。
        只有当该字段缺失、或用它拼出的第 1 页 URL 校验不通过时，才回退到轮询探测。
        """
        declared = str(issue.jpage_node or "").strip()
        if declared:
            if self._check_node(issue, sample_hash, declared):
                log(f"使用接口指定的 jpage 节点：jpage{declared}")
                return _as_int(declared, JPAGE_DEFAULT)
            log(f"接口指定 jpage{declared} 校验失败，改为轮询探测…")
        else:
            log("接口未返回 jpage 节点，开始轮询探测…")

        for n in JPAGE_PROBE_RANGE:
            if self._check_node(issue, sample_hash, str(n)):
                log(f"探测到可用节点：jpage{n}")
                return n

        log(f"未探测到可用节点，回退默认 jpage{JPAGE_DEFAULT}")
        return JPAGE_DEFAULT

    def _check_node(self, issue: IssueInfo, sample_hash: str, node: str) -> bool:
        """HEAD 校验某个 jpage 节点是否可用。"""
        url = build_image_url(
            issue.resource_id,
            issue.issue_id,
            sample_hash,
            node,
            size=IMAGE_SIZE_FULL,
        )
        try:
            r = self.api.session.head(url, allow_redirects=True, timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    # ────────────── 并发下载 ──────────────
    def _download_all(
        self,
        hashes: list[PageHash],
        jpage: int,
        rid: str,
        iid: str,
        temp_dir: str,
        on_progress: ProgressCB | None,
        cancel_event: threading.Event,
    ) -> tuple[list[tuple[int, str]], int, int]:
        """返回 [(物理页号, 本地路径)] 按 page 升序；以及成功数/失败数。"""
        total = len(hashes)
        if on_progress:
            on_progress(0, total, 0)

        results: dict[int, str] = {}
        progress_lock = threading.Lock()
        done = [0]
        failed = [0]

        def task(h: PageHash) -> tuple[int, str | None]:
            if cancel_event.is_set():
                return h.page, None
            url = build_image_url(rid, iid, h.hash, jpage, size=IMAGE_SIZE_FULL)
            out = os.path.join(temp_dir, f"page_{h.page:04d}.jpg")
            for attempt in range(1, IMAGE_MAX_RETRIES + 1):
                if cancel_event.is_set():
                    return h.page, None
                try:
                    chunks = []
                    for chunk in self.api.download_stream(url):
                        chunks.append(chunk)
                    if not chunks:
                        raise BookanAPIError("empty", "空响应")
                    with open(out, "wb") as f:
                        for c in chunks:
                            f.write(c)
                    return h.page, out
                except Exception:
                    if attempt >= IMAGE_MAX_RETRIES:
                        return h.page, None
                    time.sleep(0.5 * attempt)
            return h.page, None

        with ThreadPoolExecutor(max_workers=IMAGE_DOWNLOAD_THREADS) as pool:
            futures = [pool.submit(task, h) for h in hashes]
            for fut in as_completed(futures):
                page, path = fut.result()
                with progress_lock:
                    done[0] += 1
                    if path is None:
                        failed[0] += 1
                    else:
                        results[page] = path
                    d, t, p = done[0], total, page
                if on_progress:
                    on_progress(d, t, p)

        sorted_pairs = [(k, results[k]) for k in sorted(results.keys())]
        return sorted_pairs, done[0] - failed[0], failed[0]


def _compress_pages(paths: list[str], on_log: Callable[[str], None], level: int = 1) -> list[str]:
    """
    按档位重编码页面为 JPEG，并在超过该档最大宽度时等比缩小。
    单页失败不影响整体（保留原文件）。
    """
    cfg = COMPRESSION_LEVELS.get(level, COMPRESSION_LEVELS[1])
    quality, max_width = cfg["quality"], cfg["max_width"]
    for path in paths:
        try:
            with Image.open(path) as img:
                img.load()
                w, h = img.size
                if max_width and w > max_width:
                    new_h = round(h * max_width / w)
                    img = img.resize((max_width, new_h), Image.LANCZOS)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(path, "JPEG", quality=quality, optimize=True)
        except Exception as e:
            on_log(f"  压缩失败，保留原图: {os.path.basename(path)} ({e})")
    return paths


def _as_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
