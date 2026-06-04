# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the 连续性批量创作工具 desktop app.

Bundles:
  - the Flask backend + run_desktop.py launcher (pywebview frameless window)
  - the built Vue frontend (frontend/dist -> _internal/static, read via paths.STATIC_DIR)
  - ffmpeg.exe + ffprobe.exe (-> _internal, found by services.ffmpeg_util._find)

User data (data/, output/) is created next to the .exe at runtime (portable),
NOT inside the bundle — see backend/core/paths.py (_is_frozen branch).

Build:  pyinstaller desktop.spec --noconfirm
Output: dist/BatchStudio/BatchStudio.exe  (+ _internal/)
"""

import os
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.getcwd())
FFMPEG_BIN = os.environ.get(
    "FFMPEG_BIN",
    r"C:\Users\ALO\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin",
)
ICON = os.path.join(ROOT, "desktop_assets", "app.ico")
WEBVIEW2_SETUP = os.path.join(ROOT, "desktop_assets", "MicrosoftEdgeWebview2Setup.exe")

datas = [
    (os.path.join(ROOT, "frontend", "dist"), "static"),
    # Evergreen WebView2 bootstrapper -> _internal root (sys._MEIPASS);
    # run at first launch if the runtime is absent (see core/webview2.py).
    (WEBVIEW2_SETUP, "."),
]

# ffmpeg/ffprobe shipped as plain data files (copied verbatim, no dependency
# scan) — ffmpeg_util resolves them under BUNDLED_DIR (sys._MEIPASS).
binaries = [
    (os.path.join(FFMPEG_BIN, "ffmpeg.exe"), "."),
    (os.path.join(FFMPEG_BIN, "ffprobe.exe"), "."),
]

hiddenimports = (
    collect_submodules("backend")
    + collect_submodules("webview")
    + ["clr_loader", "clr_loader.ffi"]
)

a = Analysis(
    ["run_desktop.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PySide2", "PySide6", "PyQt5", "PyQt6", "matplotlib", "numpy.tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BatchStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=ICON,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BatchStudio",
)
