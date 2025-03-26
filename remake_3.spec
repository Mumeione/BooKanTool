# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['remake_3.py'],
    pathex=['d:\\Documents\\pythoncharm\\project'],
    binaries=[],
    datas=[
        ('app_icon.ico', '.'),  # 包含图标文件
        ('app_icon.png', '.'),  # 包含图标文件
        ('preferences.cfg', '.')  # 包含配置文件
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='图书PDF生成工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='remake_3',
)
