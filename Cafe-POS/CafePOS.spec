# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

project_root = Path(SPECPATH)

# Ensure Conda/Anaconda base DLL directories (Qt6, sqlite3, openssl, etc.)
# are discoverable by PyInstaller even when invoked from a .venv.
extra_pathex = [str(project_root / "src")]
for candidate in (
    Path(sys.prefix) / "Library" / "bin",
    Path(sys.base_prefix) / "Library" / "bin",
    Path(sys.base_prefix) / "DLLs",
):
    if candidate.is_dir():
        c_str = str(candidate)
        if c_str not in extra_pathex:
            extra_pathex.append(c_str)
        current_path = os.environ.get("PATH", "")
        if c_str not in current_path.split(os.pathsep):
            os.environ["PATH"] = c_str + os.pathsep + current_path

a = Analysis(
    [str(project_root / "src" / "run_cafe_pos.py")],
    pathex=extra_pathex,
    binaries=[],
    datas=[
        (str(project_root / "src" / "cafe_pos" / "assets"), "cafe_pos/assets"),
        (str(project_root / "src" / "cafe_pos" / "translations"), "cafe_pos/translations"),
    ],
    hiddenimports=[
        "PySide6.QtPrintSupport",
        "PySide6.QtSvg",
        "PIL.Image",
        "PIL.ImageOps",
        "argon2",
        "argon2.low_level",
        "_argon2_cffi_bindings",
        "_cffi_backend",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "email.test",
        "numpy",
        "scipy",
        "pandas",
        "matplotlib",
        "torch",
        "tensorflow",
        "IPython",
        "jupyter",
        "notebook",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CafePOS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "src" / "cafe_pos" / "assets" / "app_icon.ico"),
)
