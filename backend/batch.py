"""
批量任务调度：顺序执行一组流水线任务，每个任务独立输出文件 / 独立日志。
调用方把"业务回调"（输出进度事件）和"业务函数"（单条流水线）传进来，由本模块负责调度。
"""

from __future__ import annotations

import glob
import os
import shutil
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from .api import BookanAPI
from .catalog import derive_page_offset
from .catalog import to_tree as tree_chapters
from .config import RESOURCE_TYPE_BOOK, TEMP_DIR_PREFIX, sanitize_filename
from .epub_pipeline import EPUBPipeline
from .image_pipeline import ImagePipeline
from .models import IssueInfo
from .pdf_pipeline import build_pdf
from .url_parser import ParseError, parse_input


@dataclass
class JobSpec:
    """单条任务规格：用户输入解析后填充。"""

    raw_input: str  # 原始输入（书刊链接）
    resource_type: int  # 1 杂志 / 3 书籍
    issue_id: str
    output_format: str  # "pdf" / "epub"
    output_dir: str
    add_bookmarks: bool = True
    compress_images: bool = False
    compress_level: int = 1  # 1 轻度 / 2 中度 / 3 高度
    fallback_to_pdf: bool = False  # 自动格式：EPUB 失败时回退 PDF


@dataclass
class JobResult:
    """单条任务的输出。"""

    spec: JobSpec
    output_path: str = ""
    ok: bool = False
    error: str = ""
    log_lines: list[str] = field(default_factory=list)


def _cleanup_stale_temp() -> int:
    """
    清理历史遗留的临时缓存目录（bookan_tmp_*）。

    下载全年期刊（24+ 本）时单本图片可达上百 MB，以下情形会留下孤儿目录：
      • Windows 下文件被占用导致 TemporaryDirectory 自动清理静默失败
      • 进程崩溃 / 强杀，with 块根本没机会退出
    在每次批量任务开始时统一清扫，实现自愈。
    返回删除的目录数。
    """
    stale = glob.glob(os.path.join(tempfile.gettempdir(), f"{TEMP_DIR_PREFIX}*"))
    removed = 0
    for d in stale:
        shutil.rmtree(d, ignore_errors=True)
        if not os.path.exists(d):
            removed += 1
    return removed


def run_batch(
    inputs: list[str],
    options: dict,
    api: BookanAPI,
    on_log: Callable[[str], None],
    on_progress: Callable[
        [int, int, str, float | None], None
    ],  # (idx, total, message, job_fraction)
    cancel_event: threading.Event,
) -> list[JobResult]:
    """
    options:
        output_dir: 输出目录
        output_format: "pdf" | "epub" | "auto"  (auto = 优先 EPUB，无则 PDF)
        add_bookmarks: 是否添加 outline
        compress_images: 是否压缩图片（配合 compress_level 三档）

    on_progress 的 job_fraction 是当前任务内部进度 0..1（None 表示未知），
    上层据此计算全局比例：(idx-1+fraction)/total，避免"提前 100%"。
    """
    results: list[JobResult] = []
    total = len(inputs)

    # 批量开始前清扫上次遗留的临时缓存（多本连续下载易积攒，见函数 docstring）
    stale = _cleanup_stale_temp()
    if stale:
        on_log(f"已清理上次遗留的下载缓存 {stale} 个目录")

    for idx, raw in enumerate(inputs, start=1):
        on_progress(idx, total, f"开始处理第 {idx}/{total} 条", 0.0)

        # 1. 解析
        try:
            rt, iid = parse_input(raw)
        except ParseError as e:
            results.append(
                JobResult(
                    spec=JobSpec(
                        raw, 0, "", options.get("output_format", "pdf"), options["output_dir"]
                    ),
                    error=str(e),
                )
            )
            on_log(f"[{idx}] 解析失败: {e}")
            continue

        fmt = options.get("output_format", "pdf")
        prefer_pdf_fallback = False
        if fmt == "auto":
            # 自动 = 优先 EPUB，无 EPUB 版本再回退 PDF
            fmt = "epub"
            prefer_pdf_fallback = True

        spec = JobSpec(
            raw_input=raw,
            resource_type=rt,
            issue_id=iid,
            output_format=fmt,
            output_dir=options["output_dir"],
            add_bookmarks=bool(options.get("add_bookmarks", True)),
            compress_images=bool(options.get("compress_images", False)),
            compress_level=max(1, min(int(options.get("compress_level", 1) or 1), 3)),
            fallback_to_pdf=prefer_pdf_fallback,
        )
        result = JobResult(spec=spec)

        # 任务内部分阶段进度：(fraction 0..1, 阶段描述)
        def report(fraction: float, label: str, idx=idx, total=total):
            on_progress(idx, total, label, max(0.0, min(1.0, fraction)))

        # 2. 单条流水线
        # ignore_cleanup_errors=True：Windows 下个别图片句柄未释放时清理失败不抛异常，
        # 遗留目录由下一次任务开始的 _cleanup_stale_temp 兜底
        with tempfile.TemporaryDirectory(prefix=TEMP_DIR_PREFIX, ignore_cleanup_errors=True) as tmp:
            try:
                _run_one(spec, api, tmp, on_log, cancel_event, result, report)
            except Exception as e:
                result.ok = False
                result.error = str(e)
                on_log(f"[{idx}] 失败: {e}")

        if cancel_event.is_set():
            on_log("[任务被取消]")
            # 仍记录当前结果，循环结束
            results.append(result)
            break

        results.append(result)

    on_progress(total, total, "全部完成", 1.0)
    return results


def _run_one(
    spec: JobSpec,
    api: BookanAPI,
    tmp: str,
    on_log: Callable[[str], None],
    cancel_event: threading.Event,
    result: JobResult,
    report: Callable[[float, str], None],
) -> None:
    """单条任务完整流水线。"""
    report(0.02, "获取资源信息…")
    on_log(f"[{spec.issue_id}] 步骤 1: 拉取资源信息…")
    issue = api.get_issue_info(spec.issue_id, spec.resource_type)
    on_log(
        f"  → {issue.display_title}，共 {issue.count} 页"
        f" / 主办 {issue.publisher or '未知'}"
        f" / 出版 {issue.pub_date or '未知'}"
    )

    if not issue.count:
        on_log("  警告：接口未返回页数，仍尝试拉取")

    if spec.output_format == "epub":
        try:
            _run_epub(issue, spec, api, tmp, on_log, cancel_event, result, report)
        except Exception as e:
            # 自动模式：EPUB 任一环节失败（版本 hash 缺失 / 424/403/404 / 网络异常）
            # 都回退 PDF；用户主动取消则原样终止，不转 PDF 继续下载。
            if not spec.fallback_to_pdf or cancel_event.is_set():
                raise
            on_log(f"  EPUB 获取失败（{e}），自动回退 PDF…")
            spec.output_format = "pdf"
            _run_pdf(issue, spec, api, tmp, on_log, cancel_event, result, report)
    elif spec.output_format == "pdf":
        _run_pdf(issue, spec, api, tmp, on_log, cancel_event, result, report)
    else:
        raise ValueError(f"未知的输出格式: {spec.output_format}")


def _fetch_chapters(
    issue: IssueInfo, spec: JobSpec, api: BookanAPI, physical_pages: list, on_log
) -> tuple[list, int]:
    """
    拉取并规整目录（两级）。失败时返回空列表，不阻断主流程。
    返回 (chapters, page_offset)：offset 由目录结构推导（封底锚点），
    把 catalogInfo 的印刷页码校准到 getHash 物理页。
    catalogInfo 的 categoryId 必须传 issue_id，传 resource_id 会拿到跨期历史文章。
    """
    try:
        raw_chapters = api.get_catalog(spec.issue_id, spec.resource_type)
        offset = derive_page_offset(raw_chapters, physical_pages)
        chapters = tree_chapters(raw_chapters, max(physical_pages) if physical_pages else 0)
        if chapters:
            nested = sum(len(c.children) for c in chapters)
            on_log(f"  → 目录 {len(chapters)} 项（含 {nested} 个子条目），页码偏移 +{offset}")
        else:
            on_log("  → 该资源没有可用目录，跳过书签")
        return chapters, offset
    except Exception as e:
        on_log(f"  目录获取失败（跳过书签）：{e}")
        return [], 0


def _run_pdf(
    issue: IssueInfo,
    spec: JobSpec,
    api: BookanAPI,
    tmp: str,
    on_log: Callable[[str], None],
    cancel_event: threading.Event,
    result: JobResult,
    report: Callable[[float, str], None],
) -> None:
    img_pipeline = ImagePipeline(api)

    def prog(done, total_, page):
        # 图片下载是耗时主体：占整体 2% ~ 90%，收尾阶段压缩在最后 10%
        frac = 0.02 + 0.88 * (done / total_) if total_ else 0.02
        report(frac, f"下载图片 {done}/{total_}（第 {page} 页）")

    img_result = img_pipeline.run(
        issue,
        temp_dir=tmp,
        on_progress=prog,
        on_log=on_log,
        cancel_event=cancel_event,
        compress=spec.compress_images,
        compress_level=spec.compress_level,
    )

    if cancel_event.is_set():
        return

    on_log(f"[{spec.issue_id}] 步骤 3: 合成 PDF…")
    chapters = []
    page_offset = 0
    if spec.add_bookmarks:
        report(0.91, "获取目录结构…")
        chapters, page_offset = _fetch_chapters(issue, spec, api, img_result.page_numbers, on_log)

    # 封面说明：物理第 1 页即官方封面（与 EPUB 内 cover.jpg 同一文件），
    # 无需也不应再插入缩略图封面页。

    # 输出文件名：期刊 = 刊名_期号；图书 = 书名_作者（实测 owner，缺失回退出版社）
    if issue.resource_type == RESOURCE_TYPE_BOOK:
        sub = issue.author or issue.publisher or issue.issue_id
    else:
        issue.issue_name = issue.issue_name or issue.issue_id
        sub = issue.issue_name
    fname = f"{sanitize_filename(issue.resource_name)}_{sanitize_filename(sub)}.pdf"
    output_path = os.path.join(spec.output_dir, fname)

    def phase(fraction: float, label: str):
        # 合成阶段占整体 91% ~ 99%（见 build_pdf 的 phase 调用点）
        report(fraction, label)

    out = build_pdf(
        issue=issue,
        image_paths=img_result.image_paths,
        output_path=output_path,
        chapters=chapters,
        page_numbers=img_result.page_numbers,
        page_offset=page_offset,
        on_log=on_log,
        on_phase=phase,
    )
    result.output_path = out
    result.ok = True
    report(1.0, "PDF 完成")
    on_log(f"  → 生成成功: {out}")


def _run_epub(
    issue: IssueInfo,
    spec: JobSpec,
    api: BookanAPI,
    tmp: str,
    on_log: Callable[[str], None],
    cancel_event: threading.Event,
    result: JobResult,
    report: Callable[[float, str], None],
) -> None:
    """
    EPUB 输出：直接下载官方 .epub 成品（2026-08-31 抓包实测）。
    getHash(start=0) 拿版本号 → epub.bookan.com.cn 整本 .epub 直下，
    不再走「jpage 图片合成」的老流程。
    """
    on_log(f"[{spec.issue_id}] 步骤 2: 直接下载官方 EPUB…")

    def prog(done: int, total: int, label: str):
        # EPUB 直下同为耗时主体：占整体 5% ~ 90%
        frac = 0.05 + 0.85 * (done / total) if total else 0.05
        report(frac, f"下载 EPUB {label}")

    pipeline = EPUBPipeline(api)
    out = pipeline.run(
        issue=issue,
        output_dir=spec.output_dir,
        on_log=on_log,
        on_progress=prog,
        cancel_event=cancel_event,
    )
    result.output_path = out
    result.ok = True
    report(1.0, "EPUB 完成")
    on_log(f"  → 生成成功: {out}")
