"""
EPUB 流水线：直接下载官方 .epub 成品。

2026-08-31 Reqable 抓包实测（点击 → 阅读 → 下载期刊）：
  官方 App 获取 EPUB 仅两步：
    1. GET /resource/getHash?resourceType=..&resourceId=..&issueId=..&start=0&end=0
       返回 page=0 的条目，其 hash 即 EPUB 版本号（如 "44164560"、"8145240c"）
    2. GET http://epub.bookan.com.cn/epub2/{rid}/{rid}-{iid}/{iid}_{hash}.epub
       返回 200 / application/octet-stream，即完整 EPUB 文件（约 5~13MB）

  旧版「jpage 图片合成 EPUB」的做法废弃 —— 那是用页面图片拼的仿制品，
  而官方原版 EPUB（含正文 xhtml、字体图片、两级目录）可直接整本下载。

  注意：并非所有资源都有 EPUB 版本（hash 拉不到或 .epub 返回 403/404），
  此时上层应提示用户改用 PDF 输出。
"""

from __future__ import annotations

import os
from collections.abc import Callable

from .api import BookanAPI
from .config import sanitize_filename
from .models import IssueInfo


class EPUBPipeline:
    """EPUB 直下：getHash(start=0) 取版本号 → 整本 .epub 落盘。"""

    def __init__(self, api: BookanAPI):
        self.api = api

    def run(
        self,
        issue: IssueInfo,
        output_dir: str,
        on_log: Callable[[str], None] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
        cancel_event=None,
    ) -> str:
        """
        下载官方 EPUB 成品。

        入参:
            issue:        资源元数据
            output_dir:   输出目录
            on_progress:  (done_bytes, total_bytes, message)
        """
        log = on_log or (lambda m: None)
        progress = on_progress or (lambda d, t, x: None)
        os.makedirs(output_dir, exist_ok=True)

        rid, iid = issue.resource_id, issue.issue_id

        log("步骤 1/2: 获取 EPUB 版本 hash（getHash start=0）…")
        version_hash = self.api.get_epub_version_hash(rid, iid, issue.resource_type)
        log(f"  → 版本 hash: {version_hash}")

        url = self.api.build_epub_url(rid, iid, version_hash)
        log("步骤 2/2: 直接下载官方 EPUB…")

        filename = _epub_filename(issue)
        output_path = os.path.join(output_dir, filename)

        def _chunk(done: int, total: int) -> None:
            if total > 0:
                mb_done = done / 1024 / 1024
                mb_total = total / 1024 / 1024
                progress(done, total, f"下载 {mb_done:.1f}/{mb_total:.1f} MB")
            else:
                progress(done, 0, f"已下载 {done / 1024 / 1024:.1f} MB")

        self.api.download_to_file(
            url,
            output_path,
            on_progress=_chunk,
            cancel_event=cancel_event,
        )
        log(f"  → 官方 EPUB 已保存：{output_path}")
        progress(1, 1, "完成")
        return output_path


def _epub_filename(issue: IssueInfo) -> str:
    """书名-作者.epub；无作者时用出版社/刊名兜底。"""
    name = sanitize_filename(issue.resource_name) or "book"
    author = sanitize_filename(issue.author or issue.publisher) or "Unknown"
    base = f"{name}-{author}"
    if issue.issue_name:
        base = f"{base}-{sanitize_filename(issue.issue_name)}"
    return base + ".epub"
