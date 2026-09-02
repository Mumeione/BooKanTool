# -*- coding: utf-8 -*-
"""端到端集成测试：真实调用 API → 下载 → 生成 PDF / EPUB。"""

import sys, os, io, time, tempfile, zipfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.api import BookanAPI
from backend.url_parser import parse_input
from backend.catalog import to_tree
from backend.image_pipeline import ImagePipeline
from backend.pdf_pipeline import build_pdf
from backend.epub_pipeline import EPUBPipeline

OUT = os.path.join(tempfile.gettempdir(), "bookan_e2e_out")
os.makedirs(OUT, exist_ok=True)

# 限制页数以控制测试时长；None 表示全量
PAGE_LIMIT = int(os.environ.get("PAGE_LIMIT", "12"))

api = BookanAPI()


def log(m):
    print("   " + str(m))


print("=" * 70)
print(f"E2E 测试  输出目录={OUT}  页数上限={PAGE_LIMIT}")
print("=" * 70)

rt, iid = parse_input("https://new.bookan.com.cn/?type=1&id=310826855")
print(f"\n[1] 解析输入 → type={rt}, id={iid}")

issue = api.get_issue_info(iid, rt)
print(f"\n[2] 资源信息（字段映射校验）")
print(f"    resource_name = {issue.resource_name!r}")
print(f"    issue_name    = {issue.issue_name!r}")
print(f"    count         = {issue.count}")
print(f"    publisher     = {issue.publisher!r}      ← 应来自 press")
print(f"    pub_date      = {issue.pub_date!r}  ← 应来自 publish")
print(f"    issn / cn     = {issue.issn!r} / {issue.cn!r}")
print(f"    jpage_node    = {issue.jpage_node!r}")
print(f"    description   = {issue.description[:40]!r}... ({len(issue.description)}ch)")
print(f"    tags          = {issue.tags}")
assert issue.publisher, "publisher 映射失败（应为 press）"
assert issue.pub_date, "pub_date 映射失败（应为 publish）"
assert issue.description, "description 映射失败（应为 text）"
assert issue.jpage_node == "8", "jpage_node 映射失败"
print("    ✓ 字段映射全部正确")

print(f"\n[3] 目录（两级结构校验）")
raw_ch = api.get_catalog(iid, rt)
print(f"    原始顶层条目 = {len(raw_ch)}")
tree = to_tree(raw_ch, issue.count)
print(f"    规整后顶层   = {len(tree)}")
for c in tree:
    print(f"      [{c.start_page:>3}] {c.title}")
    for k in c.children:
        print(f"            [{k.start_page:>3}] {k.title}")
assert len(tree) > 0, "目录解析为空"
assert all(c.start_page >= 1 for c in tree), "仍存在 page<=0 的伪条目"
print("    ✓ 目录结构正确，伪条目已剔除")

issue_total_pages = issue.count
print(f"\n[4] 下载图片（前 {PAGE_LIMIT} 页）")
issue.count = min(issue.count, PAGE_LIMIT)
t0 = time.time()
with tempfile.TemporaryDirectory(prefix="e2e_") as tmp:
    compress = os.environ.get("COMPRESS", "0") == "1"
    res = ImagePipeline(api).run(
        issue,
        temp_dir=tmp,
        on_log=log,
        on_progress=None,
        cancel_event=None,
        compress=compress,
    )
    dt = time.time() - t0
    print(f"    下载 {res.total_pages} 页，用时 {dt:.1f}s")
    assert res.total_pages > 0, "没有下载成功任何页"
    sizes = [os.path.getsize(p) for p in res.image_paths]
    print(f"    文件大小: min={min(sizes) // 1024}KB max={max(sizes) // 1024}KB")
    with open(res.image_paths[0], "rb") as f:
        magic = f.read(3)
    assert magic[:2] == b"\xff\xd8", f"不是 JPEG: {magic.hex()}"
    print("    ✓ 图片有效（JPEG 魔数正确）")

    print(f"\n[5] 生成 PDF（含两级书签）")
    pdf_path = os.path.join(OUT, "测试_南方经济.pdf")
    build_pdf(issue, res.image_paths, pdf_path, chapters=tree, on_log=log)
    print(f"    输出: {pdf_path}  ({os.path.getsize(pdf_path) // 1024} KB)")
    from pypdf import PdfReader

    r = PdfReader(pdf_path)
    outline = r.outline
    print(f"    PDF 页数 = {len(r.pages)}")
    print(f"    outline 结构：")

    def _walk(ol, depth=0):
        n_top = n_child = 0
        for item in ol:
            if isinstance(item, list):
                a, b = _walk(item, depth + 1)
                n_top += a
                n_child += b
            else:
                n_top += 1
                print(
                    "      "
                    + "  " * depth
                    + f"· {item.title}  → p{item.page.number if hasattr(item.page, 'number') else item.page}"
                )
        return n_top, n_child

    n_top, n_child = _walk(outline)
    print(f"    合计顶层={n_top}")
    assert os.path.getsize(pdf_path) > 1000
    print(f"    元数据 Title = {r.metadata.get('/Title')!r}")
    if PAGE_LIMIT >= issue_total_pages:
        # 全量时才断言书签完整
        assert n_top >= 5, f"书签数量异常: {n_top}"
        nested = sum(1 for i in outline if isinstance(i, list))
        print(f"    含子书签的顶层项 = {nested}")
        assert nested >= 3, f"两级书签未生效，嵌套项仅 {nested}"
        print("    ✓ 两级书签已写入 PDF")
    print("    ✓ PDF 生成成功")

    print(f"\n[6] 生成 EPUB（图文版降级）")
    cover = api.download_cover(issue)
    print(f"    封面字节 = {len(cover)}  {'✓' if cover else '(空，将回退用第1页)'}")
    ep = EPUBPipeline(api)
    epub_path = ep.run(
        issue=issue,
        output_dir=OUT,
        image_paths=res.image_paths,
        chapters=tree,
        cover_bytes=cover,
        on_log=log,
    )
    print(f"    输出: {epub_path}  ({os.path.getsize(epub_path) // 1024} KB)")

    # 校验 EPUB 是合法 zip 且含必要部件
    assert zipfile.is_zipfile(epub_path), "EPUB 不是合法 zip"
    with zipfile.ZipFile(epub_path) as z:
        names = z.namelist()
        assert "META-INF/container.xml" in " ".join(names) or any(
            "container.xml" in n for n in names
        ), "缺少 container.xml"
        n_img = sum(1 for n in names if n.lower().endswith(".jpg"))
        n_xhtml = sum(1 for n in names if n.lower().endswith(".xhtml"))
    print(f"    zip 部件: 图片={n_img}  xhtml={n_xhtml}")
    assert n_img > 0 and n_xhtml > 0
    print("    ✓ EPUB 结构合法")

print("\n" + "=" * 70)
print("全部通过 ✓")
print("=" * 70)
