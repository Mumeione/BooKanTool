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
import contextlib
import os
from collections.abc import Callable
from datetime import datetime

import img2pdf
from pypdf import PdfReader, PdfWriter
from pypdf.xmp import XmpInformation

from .config import place_output_file
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
    temp_dir: str | None = None,
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
        temp_dir: 中间文件的落盘目录（安卓传任务私有临时目录，免权限）；
                  缺省退回最终输出同目录（桌面端无分区存储限制）

    返回: 实际写入的路径
    """
    log = on_log or (lambda m: None)
    phase = on_phase or (lambda f, s: None)

    if not image_paths:
        raise ValueError("没有可用图片")

    output_path = output_path if output_path.lower().endswith(".pdf") else output_path + ".pdf"
    if page_numbers is None:
        page_numbers = list(range(1, len(image_paths) + 1))

    # 1) img2pdf 流式写盘到临时文件。
    #    不用 convert() 返回 bytes：整份 PDF 字节串常驻内存（百 MB 级），
    #    移动端再叠加 pypdf 解析/克隆极易 OOM —— 这正是"94% 失败"的根因。
    #    临时文件必须放私有目录：Android 11+ 分区存储下，公共目录只允许
    #    创建已知媒体类型扩展名的文件（".pdf" 合法，".raw.tmp" 直接 EACCES
    #    errno 13）—— 中间文件落 temp_dir，只有最终 .pdf 落公共下载目录。
    phase(0.92, f"合成图片版 PDF（共 {len(image_paths)} 页）…")
    log(f"合成图片版 PDF，共 {len(image_paths)} 页…")
    stem = os.path.splitext(os.path.basename(output_path))[0]
    raw_dir = (
        temp_dir
        if temp_dir and os.path.isdir(temp_dir)
        else os.path.dirname(os.path.abspath(output_path))
    )
    raw_tmp = os.path.join(raw_dir, f"{stem}.raw.tmp")
    img2pdf_kwargs = _img2pdf_kwargs()
    with open(raw_tmp, "wb") as f:
        img2pdf.convert(image_paths, outputstream=f, **img2pdf_kwargs)
    log(f"  临时 PDF：{os.path.getsize(raw_tmp) / 1024 / 1024:.1f} MB")

    # 2) pypdf 从磁盘惰性读取临时 PDF，加 outline + metadata。
    #    页内容流保持磁盘引用，写出时才读取，内存只持有解析后的对象图。
    phase(0.95, "写入书签与元数据…")
    log("写入书签与元数据…")
    reader = PdfReader(raw_tmp)
    writer = PdfWriter(clone_from=reader)

    if chapters:
        n = _add_outline(writer, chapters, page_numbers, page_offset)
        log(f"  outline 写入 {n} 条（页码偏移 +{page_offset}）")

    _set_metadata(writer, issue)

    # 3) 写盘并清理临时文件。
    #    先落私有目录再 place_output_file 落位：公共目录直写会被分区存储拒绝
    #    （重装/清数据后重下同名书刊，旧文件归属旧安装身份 → open('wb') EACCES）
    phase(0.97, "保存 PDF 文件…")
    done_tmp = os.path.join(raw_dir, f"{stem}.done.pdf")
    with open(done_tmp, "wb") as f:
        writer.write(f)
    with contextlib.suppress(OSError):
        os.remove(raw_tmp)
    output_path = place_output_file(done_tmp, output_path, on_log=log)
    with contextlib.suppress(OSError):
        os.remove(done_tmp)

    phase(0.99, "PDF 完成")
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
        # 图书作者取 owner（2026-09 实测字段）；作者缺失时回退出版社。
        author = issue.author or issue.publisher or "Unknown"
        # 出版社 / 出版日期 / ISBN（期刊为 ISSN）全部写入 Subject + Keywords，便于分类检索
        if issue.resource_type == 1:  # 期刊
            bits = [f"期刊：{issue.resource_name}"]
            code = issue.issn
        else:  # 图书
            bits = [f"图书：{issue.resource_name}"]
            code = issue.isbn
        if issue.author:
            bits.append(f"作者：{issue.author}")
        if issue.publisher:
            bits.append(f"出版社：{issue.publisher}")
        if issue.pub_date:
            bits.append(f"出版：{issue.pub_date}")
        if code:
            bits.append(f"{'ISSN' if issue.resource_type == 1 else 'ISBN'}：{code}")
        if issue.issue_name:
            bits.append(f"期次：{issue.issue_name}")
        if issue.description:
            bits.append(issue.description[:200])
        subject = " · ".join(bits)
        keywords = "bookan"
        for kw in (issue.issue_name, code):
            if kw:
                keywords += f", {kw}"
        writer.add_metadata(
            {
                "/Title": issue.display_title,
                "/Author": author,
                "/Producer": "BookanTool",
                "/Creator": "BookanTool (img2pdf + pypdf)",
                "/Subject": subject,
                "/Keywords": keywords,
            }
        )
        _set_xmp_metadata(writer, issue, author)
    except Exception:
        pass


def _clean_isbn(raw: str) -> str:
    """
    接口 ISBN 形如 "9787557003784.1"（书号.变体号）。
    去掉变体号后仅保留纯 10/13 位数字，否则 Calibre 的 check_isbn 会拒绝识别。
    """
    base = (raw or "").split(".")[0].replace("-", "").strip()
    return base if (len(base) in (10, 13) and base.isdigit()) else ""


def _set_xmp_metadata(writer, issue: IssueInfo, author: str) -> None:
    """
    写 XMP 元数据流（与 Info 字典并存）。

    PDF Info 字典没有出版社/出版日期/ISBN 的专用槽位，Calibre 把塞进
    Subject/Keywords 的内容一律当标签；而 Calibre 读取 PDF 时优先解析 XMP
    （calibre/ebooks/metadata/xmp.py metadata_from_xmp_packet）：
      dc:publisher → 出版社    dc:date → 出版日期
      dc:identifier → ISBN 标识符（check_isbn 校验）
      dc:description → 评论    dc:subject → 标签
    """
    xmp = XmpInformation.create()
    xmp.dc_title = {"x-default": issue.display_title}
    xmp.dc_creator = [author]

    if issue.publisher:
        xmp.dc_publisher = [issue.publisher]
    if issue.pub_date:
        with contextlib.suppress(ValueError):
            xmp.dc_date = [datetime.fromisoformat(issue.pub_date)]

    # 标签保持精简：类型 + 期次；ISBN 走 identifier，ISSN 无专用槽位只能放标签
    tags = ["图书" if issue.resource_type != 1 else "期刊", "bookan"]
    if issue.issue_name:
        tags.append(issue.issue_name)
    if issue.resource_type == 1 and issue.issn:
        tags.append(issue.issn)
    xmp.dc_subject = tags

    if issue.description:
        xmp.dc_description = {"x-default": issue.description[:800]}

    isbn = _clean_isbn(issue.isbn)
    if isbn:
        xmp.dc_identifier = isbn

    writer.xmp_metadata = xmp
