# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller one-file spec for Alpha Fixer & File Converter.

Build with:
    pyinstaller alpha_fixer_onefile.spec
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

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

a = Analysis(
    ["main.py"],
    pathex=[str(Path(".").resolve())],
    binaries=[],
    datas=[
        ("src/assets/svg", "src/assets/svg"),
        ("src/assets/icon.ico", "src/assets"),
    ]
    + collect_data_files("imageio")
    + copy_metadata("imageio")
    + collect_data_files("imageio_ffmpeg")
    + copy_metadata("imageio_ffmpeg"),
    hiddenimports=hidden,
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
