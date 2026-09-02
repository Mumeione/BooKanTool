# -*- coding: utf-8 -*-
"""
frozen 环境下的端到端验证入口（仅用于打包验证，不随产品分发）。

以 console 模式打包成 exe 后运行，验证：
  真实网络请求 → 图片下载 → PDF(两级书签) → EPUB
全部在 PyInstaller 解包环境内完成，可证明分发给他人后同样可用。
"""

import os
import sys
import io
import time
import tempfile
import zipfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.api import BookanAPI
from backend.url_parser import parse_input
from backend.catalog import to_tree
from backend.image_pipeline import ImagePipeline
from backend.pdf_pipeline import build_pdf
from backend.epub_pipeline import EPUBPipeline

OUT = os.path.join(tempfile.gettempdir(), "bookan_frozen_e2e")
os.makedirs(OUT, exist_ok=True)
PAGE_LIMIT = int(os.environ.get("PAGE_LIMIT", "20"))
COMPRESS = os.environ.get("COMPRESS", "1") == "1"

FAILED = []


def check(cond, msg):
    print(("  [OK]   " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILED.append(msg)


print("=" * 68)
print(f"FROZEN E2E   frozen={getattr(sys, 'frozen', False)}")
print(f"_MEIPASS = {getattr(sys, '_MEIPASS', '(none)')}")
print(f"输出目录  = {OUT}")
print("=" * 68)

api = BookanAPI()

try:
    rt, iid = parse_input("https://new.bookan.com.cn/?type=1&id=310826855")
    check(rt == 1 and iid == "310826855", f"输入解析 type={rt} id={iid}")

    print("\n[1] 拉取资源信息")
    issue = api.get_issue_info(iid, rt)
    print(f"     {issue.resource_name} / {issue.issue_name} / {issue.count}页")
    print(f"     主办={issue.publisher}  出版={issue.pub_date}  节点=jpage{issue.jpage_node}")
    check(bool(issue.publisher), "publisher 字段映射 (press)")
    check(bool(issue.pub_date), "pub_date 字段映射 (publish)")
    check(bool(issue.description), "description 字段映射 (text)")
    check(issue.jpage_node == "8", "jpage_node 字段映射 (jpg)")
    check(issue.count > 0, "页数有效")

    print("\n[2] 拉取目录")
    tree = to_tree(api.get_catalog(iid, rt), issue.count)
    nested = sum(len(c.children) for c in tree)
    print(f"     顶层 {len(tree)} 项，子条目 {nested} 项")
    for c in tree[:4]:
        print(f"       [{c.start_page:>3}] {c.title} ({len(c.children)} 子)")
    check(len(tree) > 0, "目录非空")
    check(all(c.start_page >= 1 for c in tree), "无 page<=0 伪条目")

    print(f"\n[3] 真实下载图片（前 {PAGE_LIMIT} 页，压缩={COMPRESS}）")
    issue.count = min(issue.count, PAGE_LIMIT)
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="frozen_") as tmp:
        res = ImagePipeline(api).run(
            issue,
            temp_dir=tmp,
            on_log=lambda m: print("     " + str(m)),
            cancel_event=None,
            compress=COMPRESS,
        )
        print(f"     下载 {res.total_pages} 页，用时 {time.time() - t0:.1f}s")
        check(res.total_pages == PAGE_LIMIT, f"全部 {PAGE_LIMIT} 页下载成功")
        with open(res.image_paths[0], "rb") as f:
            check(f.read(2) == b"\xff\xd8", "图片为有效 JPEG")

        print("\n[4] 生成 PDF（两级书签）")
        pdf = os.path.join(OUT, "frozen_test.pdf")
        build_pdf(issue, res.image_paths, pdf, chapters=tree, on_log=lambda m: None)
        from pypdf import PdfReader

        r = PdfReader(pdf)
        n_ol = len(r.outline)
        print(f"     {pdf}")
        print(f"     页数={len(r.pages)}  书签顶层={n_ol}  大小={os.path.getsize(pdf) // 1024}KB")
        check(len(r.pages) == PAGE_LIMIT, "PDF 页数正确")
        check(os.path.getsize(pdf) > 10000, "PDF 体积正常")

        print("\n[5] 生成 EPUB（图文版）")
        cover = api.download_cover(issue)
        print(f"     封面 {len(cover)} 字节")
        epub_path = EPUBPipeline(api).run(
            issue=issue,
            output_dir=OUT,
            image_paths=res.image_paths,
            chapters=tree,
            cover_bytes=cover,
            on_log=lambda m: print("     " + str(m)),
        )
        print(f"     {epub_path}")
        print(f"     大小={os.path.getsize(epub_path) // 1024}KB")
        with zipfile.ZipFile(epub_path) as z:
            names = z.namelist()
        check(any("container.xml" in n for n in names), "EPUB 含 container.xml")
        check(sum(1 for n in names if n.endswith(".jpg")) >= PAGE_LIMIT, "EPUB 含全部页面图片")
        check(sum(1 for n in names if n.endswith(".xhtml")) >= PAGE_LIMIT, "EPUB 含全部页面文档")

except Exception as e:
    import traceback

    traceback.print_exc()
    FAILED.append(f"异常中断: {type(e).__name__}: {e}")

print("\n" + "=" * 68)
if FAILED:
    print(f"失败 {len(FAILED)} 项：")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
print(">>> FROZEN 端到端全部通过，可以分发。")
print("=" * 68)
