# -*- mode: python ; coding: utf-8 -*-
"""临时验证用 spec：console 模式打包 e2e_entry.py，跑完即删。"""
import os

backend_modules = [
    "backend", "backend.api", "backend.batch", "backend.bridge",
    "backend.catalog", "backend.config", "backend.epub_pipeline",
    "backend.image_pipeline", "backend.models", "backend.pdf_pipeline",
    "backend.url_parser",
]

a = Analysis(
    ["e2e_entry.py"],          # 相对 spec 自身所在目录（devtools/）解析
    pathex=[os.path.abspath(".")],   # 让 backend 包可被找到
    binaries=[],
    datas=[],      # 验证脚本不需要前端资源
    hiddenimports=backend_modules + [
        "pypdf", "PIL", "PIL.Image", "PIL.ImageDraw",
        "PIL.JpegImagePlugin", "PIL.PngImagePlugin",
        "requests", "img2pdf",
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PyQt5", "PyQt6",
              "PySide2", "PySide6", "gtk", "gi"],
    win_no_prefer_redirects=False, win_private_assemblies=False,
    cipher=None, noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="e2e_debug",
    debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # ← 需要看输出
    disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None,
    icon=None, version=None,
)
