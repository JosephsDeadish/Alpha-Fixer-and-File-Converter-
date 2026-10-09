# -*- mode: python ; coding: utf-8 -*-
"""Strict one-file release; native runtimes are mandatory."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(".").resolve()))
from scripts.bundle_dependencies import add_analysis_notices, collect_release_dependencies

_version_ns = {}
exec(Path("src/version.py").read_text(), _version_ns)
datas, binaries, hidden = collect_release_dependencies()

a = Analysis(
    ["main.py"],
    pathex=[str(Path(".").resolve())],
    binaries=binaries,
    datas=[("src/assets/svg", "src/assets/svg"), ("src/assets/icon.ico", "src/assets")] + datas,
    hiddenimports=hidden + [
        "PIL._imagingtk", "PIL.ImageFilter", "numpy", "imageio.plugins.pillow",
        "PyQt6.QtMultimedia", "PyQt6.QtSvgWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["scripts/runtime_hook_dependencies.py"],
    excludes=["tkinter", "unittest", "test", "tests"],
    noarchive=False,
)
add_analysis_notices(a)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], exclude_binaries=False,
    name="AlphaFixerConverter", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None,
    entitlements_file=None, icon="src/assets/icon.ico",
)
if sys.platform == "darwin":
    app = BUNDLE(
        exe, name="AlphaFixerConverter.app", version=str(_version_ns["__version__"]),
        bundle_identifier="com.pandatools.alphafixerconverter",
    )
