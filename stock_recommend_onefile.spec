# -*- mode: python ; coding: utf-8 -*-
# 单文件 exe：只有一个 AStockRecommend.exe

block_cipher = None

a = Analysis(
    ["web_app.py"],
    pathex=[],
    binaries=[],
    datas=[("templates", "templates"), ("static", "static")],
    hiddenimports=[
        "pandas",
        "numpy",
        "requests",
        "certifi",
        "charset_normalizer",
        "idna",
        "urllib3",
        "flask",
        "jinja2",
        "werkzeug",
        "werkzeug.routing",
        "werkzeug.serving",
        "werkzeug.security",
        "click",
        "itsdangerous",
        "markupsafe",
        "auth_store",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["runtime_hook.py"],
    excludes=["matplotlib", "tkinter", "PyQt5", "PyQt6", "pip", "test"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="AStockRecommend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
