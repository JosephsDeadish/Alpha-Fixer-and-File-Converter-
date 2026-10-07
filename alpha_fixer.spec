# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Alpha Fixer & File Converter.

Build a standalone one-folder app with:
    pyinstaller alpha_fixer.spec

For a single-file build use:
    pyinstaller alpha_fixer_onefile.spec

Requires:  pip install pyinstaller
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata, collect_dynamic_libs

# Pull the authoritative version from src/version.py without importing Qt
_version_ns: dict = {}
exec(Path("src/version.py").read_text(), _version_ns)
_APP_VERSION = _version_ns["__version__"]

block_cipher = None

# ── collect hidden imports that PyInstaller may miss ──────────────────────────
hidden = [
    "PIL._imagingtk",
    "PIL.Image",
    "PIL.ImageFilter",
    "numpy",
    "imageio",
    "imageio_ffmpeg",
    "imageio.plugins",
    "imageio.plugins.pillow",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtMultimedia",
    "PyQt6.QtSvg",
    "PyQt6.QtSvgWidgets",
    "src.core.alpha_processor",
    "src.core.file_converter",
    "src.core.presets",
    "src.core.settings_manager",
    "src.core.worker",
    "src.ui.main_window",
    "src.ui.alpha_tool",
    "src.ui.converter_tool",
    "src.ui.history_tab",
    "src.ui.video_tool",
    "src.ui.preview_pane",
    "src.ui.selective_alpha_tool",
    "src.ui.settings_dialog",
    "src.ui.theme_engine",
    "src.ui.click_effects",
    "src.ui.tooltip_manager",
    "src.ui.drop_list",
    "src.ui.mouse_trail",
    "src.ui.sound_engine",
    "src.ui.splash_screen",
    "src.version",
]

_LINUX_RUNTIME_LIBS = [
    "libEGL.so.1",
    "libGL.so.1",
    "libGLESv2.so.2",
    "libpulse.so.0",
    "libxcb-keysyms.so.1",
    "libxcb-image.so.0",
    "libxcb-icccm.so.4",
    "libxcb-xkb.so.1",
    "libxcb-shape.so.0",
    "libxcb-cursor.so.0",
    "libxcb-render-util.so.0",
    "libxkbcommon-x11.so.0",
    "libxcb-util.so.1",
]


def _resolve_linux_shared_lib(lib_name: str) -> str | None:
    try:
        proc = subprocess.run(
            ["ldconfig", "-p"],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.stdout:
            for line in proc.stdout.splitlines():
                if lib_name not in line or "=>" not in line:
                    continue
                resolved = Path(line.rsplit("=>", 1)[-1].strip())
                if resolved.exists():
                    return str(resolved)
    except Exception:
        pass

    search_dirs = [
        Path("/lib"),
        Path("/lib64"),
        Path("/usr/lib"),
        Path("/usr/lib64"),
        Path("/usr/local/lib"),
        Path("/lib/x86_64-linux-gnu"),
        Path("/usr/lib/x86_64-linux-gnu"),
        Path("/lib/aarch64-linux-gnu"),
        Path("/usr/lib/aarch64-linux-gnu"),
    ]
    for raw_dir in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
        if raw_dir:
            search_dirs.insert(0, Path(raw_dir))
    for base in search_dirs:
        candidate = base / lib_name
        if candidate.exists():
            return str(candidate)
    return None


def _optional_linux_runtime_bundle():
    binaries = []
    if sys.platform != "linux":
        return binaries

    seen: set[str] = set()
    for lib_name in _LINUX_RUNTIME_LIBS:
        resolved = _resolve_linux_shared_lib(lib_name)
        if not resolved or resolved in seen:
            continue
        binaries.append((resolved, "."))
        seen.add(resolved)
    return binaries


def _optional_wand_bundle():
    datas = []
    binaries = []
    hiddenimports = []
    if importlib.util.find_spec("wand") is None:
        return datas, binaries, hiddenimports

    hiddenimports.extend(["wand", "wand.api", "wand.image", "wand.resource"])
    datas += collect_data_files("wand")
    try:
        datas += copy_metadata("wand")
    except Exception:
        pass
    try:
        binaries += collect_dynamic_libs("wand")
    except Exception:
        pass

    magick_home = os.environ.get("MAGICK_HOME") or os.environ.get("IMAGEMAGICK_HOME")
    if magick_home:
        for base in (Path(magick_home), Path(magick_home) / "bin", Path(magick_home) / "lib"):
            if not base.exists():
                continue
            for pattern in ("*.dll", "*.dylib", "*.so", "*.so.*"):
                for candidate in base.glob(pattern):
                    binaries.append((str(candidate), "."))
    return datas, binaries, hiddenimports


_wand_datas, _wand_binaries, _wand_hidden = _optional_wand_bundle()
_linux_runtime_binaries = _optional_linux_runtime_bundle()
_pyqt_binaries = collect_dynamic_libs("PyQt6")

a = Analysis(
    ["main.py"],
    pathex=[str(Path(".").resolve())],
    binaries=_wand_binaries + _linux_runtime_binaries + _pyqt_binaries,
    datas=[
        # Bundle all SVG theme files and the generated icon into the app.
        ("src/assets/svg", "src/assets/svg"),
        ("src/assets/icon.ico", "src/assets"),
    ]
    + collect_data_files("PyQt6")
    + copy_metadata("PyQt6")
    + collect_data_files("imageio")
    + copy_metadata("imageio")
    + collect_data_files("imageio_ffmpeg")
    + copy_metadata("imageio_ffmpeg")
    + _wand_datas,
    hiddenimports=hidden + _wand_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "test", "tests"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AlphaFixerConverter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # disabled: not reliably available on CI runners
    console=False,          # no console window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="src/assets/icon.ico",   # generated by scripts/make_icon.py
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,              # disabled: not reliably available on CI runners
    upx_exclude=[],
    name="AlphaFixerConverter",
)

# ── macOS .app bundle ─────────────────────────────────────────────────────────
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="AlphaFixerConverter.app",
        # icon="assets/icon.icns",
        bundle_identifier="com.pandatools.alphafixerconverter",
        info_plist={
            "CFBundleShortVersionString": _APP_VERSION,
            "NSHighResolutionCapable": True,
        },
    )
