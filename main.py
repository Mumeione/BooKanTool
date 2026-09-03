"""
BookanTool — PyWebView 应用入口。

同时支持两种运行方式：
  1. 源码运行:   python main.py
  2. 打包后运行: BookanTool.exe   (PyInstaller --onefile)

打包相关要点：
  • --onefile 会把所有资源解压到 sys._MEIPASS（临时目录），
    因此所有资源路径都必须走 resource_path() 而不是 __file__ 同级目录。
  • --windowed 模式下没有控制台，任何异常都会静默消失，
    所以这里把所有未捕获异常写入用户目录下的 error.log，便于排查。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import traceback

APP_NAME = "BookanTool"


# ─────────────────────────────────────────────────────────────
# 资源路径（PyInstaller 兼容）
# ─────────────────────────────────────────────────────────────
def is_frozen() -> bool:
    """是否被 PyInstaller / cx_Freeze 打包。"""
    return bool(getattr(sys, "frozen", False))


def resource_path(relative: str) -> str:
    """
    返回资源的绝对路径。
      • 打包后：基于 sys._MEIPASS（临时解压目录）
      • 源码态：基于本文件所在目录
    """
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative)


# ─────────────────────────────────────────────────────────────
# 用户数据目录（输出文件 + 日志，都放在这里，便于用户查找）
# ─────────────────────────────────────────────────────────────
def user_data_dir() -> str:
    """~/BookanTool —— 默认下载目录与日志目录。"""
    path = os.path.join(os.path.expanduser("~"), APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def default_output_dir() -> str:
    """默认把产物放在 ~/BookanTool/downloads。"""
    path = os.path.join(user_data_dir(), "downloads")
    os.makedirs(path, exist_ok=True)
    return path


def log_file_path() -> str:
    return os.path.join(user_data_dir(), "error.log")


def setup_exception_logging() -> None:
    """
    把未捕获异常写入 error.log。
    --windowed 模式没有控制台，没有这层的话程序会"双击没反应"。
    """

    def excepthook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            with open(log_file_path(), "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 60}\n{text}\n")
        except Exception:
            pass
        # 源码运行时仍打印到控制台，方便开发
        if not is_frozen():
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook


# ─────────────────────────────────────────────────────────────
# WebView2 运行时检测（Windows）
# ─────────────────────────────────────────────────────────────
def check_webview2() -> tuple[bool, str]:
    """
    检测 Windows 上是否安装了 Edge WebView2 Runtime。
    pywebview 在 Windows 默认走 edgechromium，缺运行时会直接启动失败。
    返回 (是否可用, 说明)。非 Windows 直接返回可用。
    """
    if sys.platform != "win32":
        return True, "非 Windows 平台，跳过 WebView2 检测"

    try:
        import winreg
    except ImportError:
        return True, "无法导入 winreg，跳过检测"

    # WebView2 Runtime 的注册表 CLSID
    keys = [
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        ),
        (
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        ),
    ]
    for root, sub in keys:
        try:
            handle = winreg.OpenKey(root, sub)
            winreg.CloseKey(handle)
            return True, "已检测到 WebView2 Runtime"
        except OSError:
            continue
    return False, (
        "未检测到 Edge WebView2 Runtime。\n\n"
        "本程序依赖它来渲染界面。请到微软官网下载安装：\n"
        "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
        "安装后重启本程序即可。"
    )


def show_fatal_dialog(title: str, message: str) -> None:
    """
    致命错误弹窗。
    Windows 优先用 ctypes 调原生 MessageBox —— 零额外依赖，
    避免为了一个报错框把整个 tkinter 打进 exe（约 +10MB）。
    """
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # MB_ICONERROR
            return
        except Exception:
            pass

    # 非 Windows 退回 tkinter
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 自检（仅在 --selftest 时启动）
# ─────────────────────────────────────────────────────────────
def run_selftest() -> None:
    """
    在 frozen 环境里跑一组真实的库调用冒烟测试。

    目的：验证 PyInstaller 打包后所有关键依赖都能真正工作，
    而不是只能 import —— 后者在 frozen 环境经常有数据文件
    找不到的隐性故障。

    输出：
      • 源码运行：打印到 stdout
      • 打包运行（console=False）：写到 ~/BookanTool/selftest.log，
        并弹 MessageBoxW 给最终用户看结果
    """
    import io

    results: list[tuple[str, bool, str]] = []

    def add(name: str, fn) -> None:
        try:
            fn()
            results.append((name, True, ""))
        except Exception as e:
            import traceback as _tb

            err = f"{type(e).__name__}: {e}".strip()
            results.append((name, False, err))
            # 把 traceback 写到 selftest.log 便于排查
            try:
                log_path = os.path.join(user_data_dir(), "selftest.log")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n[FAIL] {name}\n{_tb.format_exc()}\n")
            except Exception:
                pass

    print(f"[selftest] frozen={is_frozen()}  _MEIPASS={getattr(sys, '_MEIPASS', '(none)')}")
    print(f"[selftest] Python {sys.version.split()[0]}  平台={sys.platform}")

    # ── 后端各模块可导入 ──
    for name, mod in [
        ("backend.config", "backend.config"),
        ("backend.models", "backend.models"),
        ("backend.url_parser", "backend.url_parser"),
        ("backend.api", "backend.api"),
        ("backend.image_pipeline", "backend.image_pipeline"),
        ("backend.pdf_pipeline", "backend.pdf_pipeline"),
        ("backend.epub_pipeline", "backend.epub_pipeline"),
        ("backend.batch", "backend.batch"),
        ("backend.catalog", "backend.catalog"),
        ("backend.config.sanitize_filename", None),
    ]:
        if name == "backend.config.sanitize_filename":

            def _f():
                from backend.config import sanitize_filename

                assert sanitize_filename("a/b\\c:d") == "a_b_c_d"
        else:

            def _f(_m=mod):
                __import__(_m, fromlist=["*"])

        add(f"{name} 导入/解析", _f)

    # ── PIL：图像创建 + JPEG 编码 ──
    def t_pil():
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (800, 1200), (255, 200, 100))
        ImageDraw.Draw(img).text((50, 50), "selftest", fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        assert buf.tell() > 200, f"JPEG 输出过小 {buf.tell()}"

    add("PIL 创建 + JPEG 编码", t_pil)

    # ── img2pdf ──
    def t_img2pdf():
        import img2pdf
        from PIL import Image

        img = Image.new("RGB", (100, 100), (200, 200, 200))
        in_buf = io.BytesIO()
        img.save(in_buf, "PNG")
        in_buf.seek(0)
        out = img2pdf.convert(in_buf, pagesize=(100, 100))
        assert len(out) > 100, f"PDF 过小 {len(out)}"
        assert out[:4] == b"%PDF", "PDF magic 缺失"

    add("img2pdf 字节生成", t_img2pdf)

    # ── pypdf：元数据（Info + XMP）+ outline 写读 ──
    def t_pypdf():
        from pypdf import PdfReader, PdfWriter
        from pypdf.xmp import XmpInformation

        w = PdfWriter()
        w.add_blank_page(width=200, height=200)
        w.add_outline_item(title="chapter 1", page_number=0)
        w.add_metadata({"/Title": "selftest", "/Author": "test"})
        xmp = XmpInformation.create()
        xmp.dc_title = {"x-default": "selftest"}
        xmp.dc_creator = ["tester"]
        xmp.dc_publisher = ["pub"]
        xmp.dc_subject = ["tag1", "tag2"]
        xmp.dc_identifier = "9787557003784"
        w.xmp_metadata = xmp
        out_buf = io.BytesIO()
        w.write(out_buf)
        out_buf.seek(0)
        r = PdfReader(out_buf)
        assert len(r.pages) == 1
        assert len(r.outline) == 1
        assert r.metadata.title == "selftest"
        x = r.xmp_metadata
        assert x is not None, "XMP 元数据流缺失"
        assert "selftest" in str(x.dc_title), f"XMP title 异常: {x.dc_title}"
        assert x.dc_publisher == ["pub"], f"XMP publisher 异常: {x.dc_publisher}"
        assert x.dc_identifier == "9787557003784", f"XMP identifier 异常: {x.dc_identifier}"

    add("pypdf 元数据 + outline 写读", t_pypdf)

    # ── requests：Session 可创建（不真发网络） ──
    def t_requests():
        import requests

        with requests.Session() as s:
            s.headers.update({"User-Agent": "BookanTool-selftest"})

    add("requests Session 创建", t_requests)

    # ══════ 以下为 API 契约回归测试（不联网，验证字段名映射与目录规整） ══════

    # ── issueInfoList 字段映射 ──
    def t_issue_mapping():
        from backend.api import _parse_issue

        raw = {
            "resourceId": "11753",
            "issueId": "310826855",
            "resourceName": "南方经济",
            "issueName": "2026年7期",
            "resourceType": 1,
            "count": "182",
            "press": "广东经济学会、中山大学",  # ← 不是 publisher
            "publish": "2026-07-25",  # ← 不是 pubDate
            "issn": "1000-6249",
            "cn": "44-1068/F",
            "isbn": "",
            "text": "本刊立足广东……",  # ← 不是 description
            "jpg": "8",
            "webp": "8",  # ← CDN 节点号
            "tags": [{"id": "1176", "name": "北大核心"}],
        }
        iss = _parse_issue(raw, 1)
        assert iss.resource_name == "南方经济"
        assert iss.count == 182, f"count 应为 int 182，实际 {iss.count!r}"
        assert iss.publisher == "广东经济学会、中山大学", "publisher 应取自 press"
        assert iss.pub_date == "2026-07-25", "pub_date 应取自 publish"
        assert iss.description.startswith("本刊立足广东"), "description 应取自 text"
        assert iss.jpage_node == "8", "jpage_node 应取自 jpg"
        assert iss.tags == ["北大核心"], f"tags 解析异常: {iss.tags}"
        assert iss.author == "", "杂志无 owner/author 字段，应保持空串"

        # 图书 type=3：作者取 owner 字段（2026-09 实测）
        book = _parse_issue(
            {
                "resourceId": "2552780",
                "issueId": "310667698",
                "resourceName": "曾仕强品三国",
                "resourceType": 3,
                "count": "161",
                "owner": "曾仕强",
                "press": "广东旅游出版社",
                "publish": "2016-08-01",
                "isbn": "9787557003784.1",
            },
            3,
        )
        assert book.author == "曾仕强", "图书作者应取自 owner 字段"
        assert book.publisher == "广东旅游出版社", "publisher 应取自 press"
        assert book.isbn == "9787557003784.1", "isbn 应原样映射"

    add("API 字段映射 (press/publish/text/jpg)", t_issue_mapping)

    # ── URL 解析：官网 / 移动端分享（纯 ID 已不支持） ──
    def t_url_parse():
        from backend.url_parser import ParseError, parse_input

        assert parse_input("https://new.bookan.com.cn/?type=1&id=310823891") == (1, "310823891")
        # 移动端分享链接：?id=130 是站点 ID，书刊信息在 fragment #/dt/{type}/{issueId}
        assert parse_input("https://wk6.bookan.com.cn/?id=130#/dt/1/310823891") == (1, "310823891")
        assert parse_input("https://wk6.bookan.com.cn/?id=130#/dt/3/310577420") == (3, "310577420")
        try:
            parse_input("310823891")
        except ParseError:
            pass
        else:
            raise AssertionError("纯 ID 已不支持，应解析失败")

    add("URL 解析 (官网/分享链接)", t_url_parse)

    # ── catalogInfo 两级结构 + page<=0 伪条目过滤 ──
    def t_catalog_tree():
        from backend.api import _parse_catalog_node
        from backend.catalog import derive_page_offset, to_tree

        raw = [
            {"id": 0, "name": "封面", "page": -2, "sublevels": []},
            {"id": 0, "name": "目录", "page": 0, "sublevels": []},
            {
                "id": 0,
                "name": "栏目A",
                "page": 1,
                "sublevels": [
                    {"id": 1, "name": "文章A1", "page": 1, "sublevels": []},
                    {"id": 2, "name": "文章A2", "page": 5, "sublevels": []},
                ],
            },
            {
                "id": 0,
                "name": "栏目B",
                "page": 10,
                "sublevels": [
                    {"id": 3, "name": "文章B1", "page": 10, "sublevels": []},
                ],
            },
            {"id": 0, "name": "封底", "page": 179, "sublevels": []},
        ]
        nodes = [_parse_catalog_node(x) for x in raw]
        nodes = [n for n in nodes if n is not None]
        tree = to_tree(nodes, total_pages=182)
        titles = [c.title for c in tree]
        assert "封面" not in titles, f"page=-2 的伪条目应被剔除: {titles}"
        assert "目录" not in titles, f"page=0 的伪条目应被剔除: {titles}"
        assert titles[0] == "栏目A", f"首个应为栏目A，实际 {titles}"
        # 栏目与首篇文章同页码是常态，两者都应保留
        child_titles = [c.title for c in tree[0].children]
        assert child_titles == ["文章A1", "文章A2"], (
            f"栏目A 下应保留同页的 文章A1 与 文章A2，实际 {child_titles}"
        )
        assert all(c.start_page >= 1 for c in tree), "仍存在非法页码"

        # 页码偏移推导：封底锚点 182-179=3；首篇 1+3=物理页4（正文起点）
        offset = derive_page_offset(nodes, list(range(1, 183)))
        assert offset == 3, f"封底锚点应推出偏移 3，实际 {offset}"
        # 无伪条目且印刷页=物理页（部分图书）→ 偏移 0
        flat = [
            {"name": "第一章", "page": 1, "sublevels": []},
            {"name": "第二章", "page": 50, "sublevels": []},
        ]
        fnodes = [_parse_catalog_node(x) for x in flat]
        offset0 = derive_page_offset(fnodes, list(range(1, 51)))
        assert offset0 == 0, f"图书类应推出偏移 0，实际 {offset0}"

    add("目录两级规整 + 伪条目剔除 + 页码偏移推导", t_catalog_tree)

    # ── 图片 URL 拼装 ──
    def t_image_url():
        from backend.api import build_image_url

        u = build_image_url("11753", "310826855", "a871e638", "8", "big")
        assert u == (
            "http://img1-qn.bookan.com.cn/jpage8/11753/11753-310826855/a871e638_big.jpg"
        ), u
        s = build_image_url("11753", "310826855", "a871e638", "8", "small")
        assert s.endswith("a871e638_small.jpg"), s

    add("图片 URL 拼装 (jpage + hash + size)", t_image_url)

    # ── 图片压缩 ──
    def t_compress():
        import os
        import tempfile

        from PIL import Image

        from backend.image_pipeline import _compress_pages

        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "big.jpg")
            # 高噪点图，JPEG 压缩后仍应有可观体积，用于确认文件被真实重写
            img = Image.new("RGB", (2400, 3200))
            px = img.load()
            for y in range(0, 3200, 8):
                for x in range(0, 2400, 8):
                    for dy in range(8):
                        for dx in range(8):
                            if x + dx < 2400 and y + dy < 3200:
                                px[x + dx, y + dy] = ((x + y) % 256, (x * 3) % 256, (y * 5) % 256)
            img.save(p, "JPEG", quality=95)
            before = os.path.getsize(p)
            _compress_pages([p], on_log=lambda m: None)
            after = os.path.getsize(p)
            with Image.open(p) as im:
                assert im.size[0] <= 1600, f"应缩放到宽 1600，实际 {im.size}"
            assert after < before, f"压缩后应变小: {before} → {after}"

    add("图片压缩 (缩放 + JPEG 重编码)", t_compress)

    # ── pywebview 模块可导入（不创建窗口也不启动 GUI） ──
    def t_pywebview():
        # 仅验证可导入；不调用 start() 以免 headless 卡死
        from webview.platforms.winforms import setup_app as _  # noqa: F401

    add("pywebview + winforms 模块", t_pywebview)

    # ── 输出 ──
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    lines = [
        "===== BookanTool 自检 =====",
        f"frozen   = {is_frozen()}",
        f"Python   = {sys.version.split()[0]}",
        f"_MEIPASS = {getattr(sys, '_MEIPASS', '(none)')}",
        "",
        f"通过 {passed}/{total}:",
    ]
    for name, ok, err in results:
        marker = "OK  " if ok else "FAIL"
        lines.append(f"  [{marker}] {name}")
        if not ok:
            lines.append(f"         → {err}")
    lines.append("")
    if passed == total:
        lines.append(">>> 全部通过，可以放心分发。")
    else:
        lines.append(">>> 存在失败项，请打包前排查。")
    report = "\n".join(lines)

    log_path = os.path.join(user_data_dir(), "selftest.log")
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(report)
    except Exception:
        pass

    # 源码运行：打印；frozen 运行：弹 MessageBoxW
    if not is_frozen():
        print(report)
    else:
        # frozen 时 stdout=None，改用弹窗，并把 \n 改成换行段
        show_fatal_dialog(f"{APP_NAME} 自检 ({passed}/{total})", report.replace("\n", "\r\n"))


ERROR_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
  body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;
       background:#f7f8fb;color:#1d2333;padding:40px;line-height:1.7}
  h2{color:#ef4444;margin:0 0 12px}
  code{background:#e8ecf5;padding:2px 6px;border-radius:4px;font-size:13px}
  .box{background:#fff;border:1px solid #d8dde8;border-radius:10px;padding:24px;max-width:640px}
</style></head>
<body><div class="box">
  <h2>%s</h2>
  <div>%s</div>
</div></body></html>"""


def main() -> None:
    setup_exception_logging()

    parser = argparse.ArgumentParser(description=f"{APP_NAME} - 博看书刊下载与导出工具")
    parser.add_argument("--debug", action="store_true", help="开启 DevTools 调试")
    parser.add_argument("--no-gui-check", action="store_true", help="跳过 WebView2 运行时检测")
    parser.add_argument(
        "--selftest", action="store_true", help="运行功能自检（不启动 GUI，用于排查打包问题）"
    )
    args = parser.parse_args()

    # 让 backend 包可被导入（打包后 PyInstaller 已把 _MEIPASS 加进 sys.path，这里是源码运行时的兜底）
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    # ── 自检模式（不动 GUI，最能验证打包后功能完备） ──
    if args.selftest:
        run_selftest()
        return

    # ── 前置检查：WebView2 运行时 ──
    if not args.no_gui_check:
        ok, msg = check_webview2()
        if not ok:
            show_fatal_dialog(f"{APP_NAME} - 环境缺失", msg)
            sys.exit(1)

    import webview

    from backend.bridge import BridgeAPI
    from backend.config import APP_VERSION, load_config, update_config

    # ── 定位前端资源 ──
    index_html = resource_path(os.path.join("frontend", "index.html"))

    if not os.path.exists(index_html):
        # 源码态缺失，或打包时 datas 没带上 frontend —— 直接报错页，避免"白屏无反应"
        msg = f"未找到前端文件：<code>{index_html}</code><br><br>请确认 frontend 目录完整。"
        window = webview.create_window(
            title=APP_NAME,
            html=ERROR_PAGE % ("前端资源缺失", msg),
            width=900,
            height=600,
        )
        webview.start()
        return

    # ── 恢复上次使用习惯（窗口大小/位置 + 输出选项）──
    cfg = load_config()
    win_cfg = cfg.get("window") or {}

    # ── 创建 API 桥接实例（先于窗口，用标准 js_api 参数传入）──
    api = BridgeAPI()

    # 小窗口工具定位：默认 420x680；历史记录里的大尺寸收敛到 [340x540, 600x900]，
    # 内容缩放由前端按窗口宽度自动钳制（见 frontend/app.js 的 applyZoom）。
    def _clamp_dim(value, lo: int, hi: int, default: int) -> int:
        try:
            v = int(value)
        except (TypeError, ValueError):
            v = default
        return max(lo, min(v, hi))

    window_kwargs = dict(
        title=f"{APP_NAME} · 博看书刊下载",
        url=index_html,
        js_api=api,  # 标准方式，由 pywebview 暴露为 window.pywebview.api
        width=_clamp_dim(win_cfg.get("width"), 340, 600, 420),
        height=_clamp_dim(win_cfg.get("height"), 540, 900, 680),
        min_size=(340, 540),
        resizable=True,
        text_select=True,
        confirm_close=False,
    )
    # 上次位置有效（在屏幕内）才恢复，避免窗口跑到不可见区域
    try:
        x, y = int(win_cfg.get("x", 0)), int(win_cfg.get("y", 0))
        if x > -200 and y > -200:
            window_kwargs["x"] = x
            window_kwargs["y"] = y
    except (TypeError, ValueError):
        pass

    window = webview.create_window(**window_kwargs)
    api.set_window(window)  # 回绑窗口，使后端能推事件到前端

    # 窗口/任务栏图标：源码态显式指定，否则 WinForms 会从 sys.executable
    # （即 python.exe）抽取默认图标，导致运行时图标显示错误。
    icon_path = resource_path(os.path.join("assets", "icon.ico"))

    # 页面加载完成后注入事件分发函数 + 配置与默认输出目录
    def on_loaded():
        try:
            default_dir = default_output_dir().replace("\\", "\\\\")  # JS 字符串转义
        except OSError:
            default_dir = ""  # 目录创建失败不阻断注入，前端可让用户手选
        try:
            import json as _json

            cfg_js = _json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/")
        except Exception:
            cfg_js = "{}"
        js = f"""
        window.__bookan_dispatch = function(event, data) {{
            if (window.app && typeof window.app.dispatch === 'function') {{
                window.app.dispatch(event, data);
            }}
        }};
        window.__bookan_default_dir = "{default_dir}";
        window.__bookan_version = "v{APP_VERSION}";
        window.__bookan_config = {cfg_js};
        window.__bookan_dispatch('ready', {{ts: Date.now()}});
        """
        window.evaluate_js(js)

    window.events.loaded += on_loaded

    # 关闭前保存窗口几何（使用习惯的一部分）。
    # 必须挂在 closing 而不是 closed：closed 触发时 WinForms 控件可能已销毁，
    # 读 window.x/y 会抛异常被静默吞掉，导致窗口位置/大小从未被记住。
    def on_closing():
        with contextlib.suppress(Exception):
            update_config(
                {
                    "window": {
                        "x": window.x,
                        "y": window.y,
                        "width": window.width,
                        "height": window.height,
                    }
                }
            )

    window.events.closing += on_closing

    webview.start(debug=args.debug, icon=icon_path if os.path.isfile(icon_path) else None)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 兜底：即使 excepthook 未生效也确保有记录，并提示用户
        text = traceback.format_exc()
        try:
            with open(log_file_path(), "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 60}\n{text}\n")
        except Exception:
            pass
        if is_frozen():
            show_fatal_dialog(
                f"{APP_NAME} - 启动失败",
                f"程序启动失败，详情已记录到：\n{log_file_path()}\n\n{text[:500]}",
            )
        raise
