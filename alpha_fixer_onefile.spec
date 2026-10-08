# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller one-file spec for Alpha Fixer & File Converter.

Build with:
    pyinstaller alpha_fixer_onefile.spec
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata, collect_dynamic_libs

_version_ns: dict = {}
exec(Path("src/version.py").read_text(), _version_ns)
_APP_VERSION = _version_ns["__version__"]

block_cipher = None

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


def _optional_ffprobe_bundle():
    binaries = []
    seen: set[str] = set()
    candidates = []
    try:
        import imageio_ffmpeg

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg_exe:
            ffmpeg_path = Path(ffmpeg_exe)
            candidates.extend([
                ffmpeg_path.with_name("ffprobe"),
                ffmpeg_path.with_name("ffprobe.exe"),
            ])
    except Exception:
        pass

    for name in ("ffprobe", "ffprobe.exe"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(Path(resolved))

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        resolved_text = str(resolved)
        if not resolved.exists() or resolved_text in seen:
            continue
        binaries.append((resolved_text, "imageio_ffmpeg/binaries"))
        seen.add(resolved_text)
    return binaries


_wand_datas, _wand_binaries, _wand_hidden = _optional_wand_bundle()
_linux_runtime_binaries = _optional_linux_runtime_bundle()
_ffprobe_binaries = _optional_ffprobe_bundle()
_pyqt_binaries = collect_dynamic_libs("PyQt6")

a = Analysis(
    ["main.py"],
    pathex=[str(Path(".").resolve())],
    binaries=_wand_binaries + _linux_runtime_binaries + _ffprobe_binaries + _pyqt_binaries,
    datas=[
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    exclude_binaries=False,
    name="AlphaFixerConverter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="src/assets/icon.ico",
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="AlphaFixerConverter.app",
        version=str(_APP_VERSION),
        bundle_identifier="com.pandatools.alphafixerconverter",
    )
