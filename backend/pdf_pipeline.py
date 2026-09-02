"""
PDF 合成 + 目录书签（基于 pypdf + img2pdf）。
策略:
  1. 先用 img2pdf 把所有 jpg 合并为一个临时 PDF
  2. 用 pypdf 读取该临时 PDF：
       a. 给每个章节起始页加 outline —— 目录印刷页码 + 偏移 → 物理页号，
          再按 getHash 的页号映射精确定位 PDF 页下标
       b. 写元数据（作者、出版社、标题等）
  3. 保存为最终文件
"""

from __future__ import annotations

import bisect
import io
import os
from collections.abc import Callable

import img2pdf
from pypdf import PdfReader, PdfWriter

from .models import ChapterStart, IssueInfo


def build_pdf(
    issue: IssueInfo,
    image_paths: list[str],
    output_path: str,
    chapters: list[ChapterStart] | None = None,
    page_numbers: list[int] | None = None,
    page_offset: int = 0,
    on_log: Callable[[str], None] | None = None,
    on_phase: Callable[[float, str], None] | None = None,
) -> str:
    """
    把图片合成 PDF，并加上 outline（章节书签）+ 元数据。

    入参:
        issue: 资源元数据
        image_paths: 按页码顺序排列的图片本地路径
        output_path: 最终输出路径（自动处理 .pdf 后缀）
        chapters: 章节起始页（印刷页码，用于 outline）
        page_numbers: 与 image_paths 对齐的物理页号（来自 getHash）。
                      书签 = (印刷页码 + page_offset) 在此列表中精确定位，
                      即使个别页下载失败导致下标错位也不会指错页
        page_offset: 印刷页码 → 物理页号的偏移（由 catalog.derive_page_offset 推导）
        on_phase: 合成阶段回调 (fraction 0..1, label)，用于细化整体进度条

    返回: 实际写入的路径
    """
    log = on_log or (lambda m: None)
    phase = on_phase or (lambda f, s: None)

    if not image_paths:
        raise ValueError("没有可用图片")

    output_path = output_path if output_path.lower().endswith(".pdf") else output_path + ".pdf"
    if page_numbers is None:
        page_numbers = list(range(1, len(image_paths) + 1))

    # 1) img2pdf 拼图片
    phase(0.70, f"合成图片版 PDF（共 {len(image_paths)} 页）…")
    log(f"合成图片版 PDF，共 {len(image_paths)} 页…")
    img2pdf_kwargs = _img2pdf_kwargs()
    raw_pdf_bytes = img2pdf.convert(image_paths, **img2pdf_kwargs)
    log(f"  临时 PDF 字节数：{len(raw_pdf_bytes) / 1024:.1f} KB")

    # 2) pypdf 加 outline + metadata
    phase(0.82, "写入书签与元数据…")
    log("写入书签与元数据…")
    reader = PdfReader(io.BytesIO(raw_pdf_bytes))
    writer = PdfWriter(clone_from=reader)

    if chapters:
        n = _add_outline(writer, chapters, page_numbers, page_offset)
        log(f"  outline 写入 {n} 条（页码偏移 +{page_offset}）")

    _set_metadata(writer, issue)

    # 3) 写盘
    phase(0.90, "保存 PDF 文件…")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "wb") as f:
        writer.write(f)

    phase(0.97, "PDF 完成")
    log(f"PDF 已保存：{output_path}")
    return output_path


def _img2pdf_kwargs() -> dict:
    """
    img2pdf 通用参数：保持原图像尺寸，不强制 A4。
    注：img2pdf 0.6.x 起接口改为 `auto_orient`，`orient/Orientation` 已移除。
    """
    layout_fun = img2pdf.get_layout_fun(
        pagesize=None,
        fit=img2pdf.FitMode.into,
        auto_orient=False,
    )
    return {
        "layout_fun": layout_fun,
        "title": "",
        "author": "",
        "creator": "BookanTool",
        "producer": "BookanTool",
    }


def _add_outline(
    writer,
    chapters: list[ChapterStart],
    page_numbers: list[int],
    page_offset: int = 0,
) -> int:
    """
    按章节起始页逐个 add_outline_item，支持两级（栏目 → 文章）。

    定位方式：印刷页码 + page_offset = 物理页号，再在 page_numbers
    （getHash 页号，与 PDF 页序对齐）中精确定位下标。
    个别页下载失败时二分到最近的后续页，保证书签永不指错内容。

    返回成功写入的条目数。
    """
    sorted_pages = sorted(page_numbers)
    total = len(page_numbers)

    def _locate(physical: int) -> int:
        """物理页号 → PDF 页下标（0-based）；缺失时取最近的下一页。"""
        i = bisect.bisect_left(sorted_pages, physical)
        return i if i < total else -1

    count = 0
    for ch in chapters:
        idx = _locate(ch.start_page + page_offset)
        if idx < 0:
            continue
        try:
            parent = writer.add_outline_item(title=ch.title, page_number=idx)
            count += 1
        except Exception:
            continue  # 单个 outline 失败不影响整体

        for kid in ch.children:
            kid_idx = _locate(kid.start_page + page_offset)
            if kid_idx < 0 or kid_idx < idx:
                continue
            try:
                writer.add_outline_item(title=kid.title, page_number=kid_idx, parent=parent)
                count += 1
            except Exception:
                continue
    return count


def _set_metadata(writer, issue: IssueInfo) -> None:
    try:
        # 期刊展示出版社，图书展示作者；作者字段接口常为空，回退出版社。
        author = issue.author or issue.publisher or "Unknown"
        subject = "Bookan Magazine"
        if issue.resource_type == 1:  # 期刊
            bits = [f"期刊：{issue.resource_name}", f"出版：{issue.pub_date}"]
            if issue.publisher:
                bits.append(f"出版社：{issue.publisher}")
        else:  # 图书
            bits = [f"图书：{issue.resource_name}", f"出版：{issue.pub_date}"]
            if issue.author:
                bits.append(f"作者：{issue.author}")
        if issue.description:
            bits.append(issue.description[:200])
        subject = " · ".join(x for x in bits if x)
        writer.add_metadata(
            {
                "/Title": issue.display_title,
                "/Author": author,
                "/Producer": "BookanTool",
                "/Creator": "BookanTool (img2pdf + pypdf)",
                "/Subject": subject,
                "/Keywords": f"bookan, {issue.issue_name}",
            }
        )
    except Exception:
        pass
