# -*- mode: python ; coding: utf-8 -*-
# Parallel build recipe (derived from the upstream Sidro recipe, MIT). RapidOCR models are taken from the installed rapidocr package.

from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_all, copy_metadata


ROOT = Path.cwd()

datas = []
for distribution in ("rapidocr", "onnxruntime", "numpy"):
    datas.extend(copy_metadata(distribution))
for distribution in ("opencv-contrib-python", "pyclipper", "shapely"):
    datas.extend(copy_metadata(distribution, recursive=True))
for filename in ("items_cache.json", "item_ids_cache.json", "ducats_cache.json", "Parallel.png", "Parallel.ico", "NOTICE.md", "LICENSE"):
    path = ROOT / filename
    if path.exists():
        datas.append((str(path), "."))

icon_path = ROOT / "Parallel.ico"

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "rapidocr",
    "onnxruntime",
    "cv2",
]

excludes = [
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "easyocr",
    "paddle",
    "paddleocr",
    "paddlex",
    "winrt",
    "modelscope",
    "modelscope_hub",
    "bidi",
    "crc32c",
    "torch",
    "torchvision",
    "tensorboard",
    "tkinter",
    "tensorflow",
    "torchaudio",
    # thefuzz uses RapidFuzz. This unused GPL optional accelerator is not shipped.
    "Levenshtein",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
]


binaries = []
for package in ("rapidocr",):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_imports)

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
# Qt uses Windows ICU's unsuffixed API. A developer PATH containing Poppler
# can make bindepend collect its incompatible, version-suffixed ICU instead.
a.binaries = [
    entry for entry in a.binaries
    if not (entry[0].lower() == "icuuc.dll" and "poppler" in str(entry[1]).lower())
    # Sidro captures still frames; it never opens video files. Do not distribute
    # OpenCV's optional FFmpeg plugin and its unrelated codec/source obligations.
    and not Path(entry[0]).name.lower().startswith("opencv_videoio_ffmpeg")
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Parallel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=os.getenv("PARALLEL_BUILD_CONSOLE") == "1",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Parallel",
)

