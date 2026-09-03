# -*- mode: python ; coding: utf-8 -*-
"""
BookanTool 打包配置 (PyInstaller 6.x)

执行：
    pyinstaller BookanTool.spec
或（推荐）：
    pyinstaller --noconfirm BookanTool.spec

产物：
    dist/BookanTool.exe（单文件，约 24MB）
"""
# ───────────── 后端子模块必须显式列出 ─────────────
# PyInstaller 默认只跟踪直接 import 分析出的模块；
# 我们的后端模块都通过 __init__.py 的间接引用加载，需要手动提示。
backend_modules = [
    "backend",
    "backend.api",
    "backend.batch",
    "backend.bridge",
    "backend.catalog",
    "backend.config",
    "backend.epub_pipeline",
    "backend.image_pipeline",
    "backend.models",
    "backend.pdf_pipeline",
    "backend.url_parser",
]

# ───────────── 数据文件 ─────────────
# 把整个 frontend/ 目录打包进 exe 的相同子目录，
# 这样前端代码不需要改路径，main.py 通过 resource_path() 找到。
datas = [
    ("frontend", "frontend"),
    ("assets/icon.ico", "assets"),  # 运行时窗口图标（resource_path 解析）
]

# ───────────── Analysis ─────────────
a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=backend_modules + [
        "pypdf",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",        # 自检用；PIL 走惰性加载，需显式声明
        "PIL.JpegImagePlugin",
        "PIL.PngImagePlugin",
        "requests",
        "img2pdf",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 减重 + 提速：这些包 pywebview 在 Windows 上根本不用
        "tkinter",          # 错误弹窗我们走 ctypes.MessageBoxW
        "matplotlib",
        "numpy",            # img2pdf 内置引擎不依赖 numpy
        "PyQt5", "PyQt6",
        "PySide2", "PySide6",
        "gtk", "gi",
        # 2026-09-01 体积排查：以下三项合计约 10MB 且均非必需
        "pikepdf",          # img2pdf 的可选引擎（hook 强制引入），排除后自动回退内置引擎
        "lxml",             # 仅被 pikepdf 可选引用（XMP 元数据），应用自身运行时并不加载
        "PIL._avif",        # Pillow 的 AVIF 格式插件（4.3MB），书刊页面只有 JPEG/PNG
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="BookanTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                    # 不压缩，启动更快且兼容性强
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                 # --windowed：无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
    version=None,
)
