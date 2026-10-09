"""Configure bundled native runtimes before application or Wand imports."""

import json
import os
from pathlib import Path
import sys


def configure_bundle(root: Path):
    layout = json.loads((root / "runtime-layout.json").read_text(encoding="utf-8"))
    os.environ["MAGICK_HOME"] = str(root / layout["imagemagick_home"])
    config_paths = layout["imagemagick_config"]
    if isinstance(config_paths, str):
        config_paths = [config_paths]
    os.environ["MAGICK_CONFIGURE_PATH"] = os.pathsep.join(str(root / p) for p in config_paths)
    os.environ["MAGICK_CODER_MODULE_PATH"] = str(root / layout["imagemagick_coders"])
    os.environ["IMAGEIO_FFMPEG_EXE"] = str(root / "imageio_ffmpeg/binaries" / layout["ffmpeg"])
    os.environ["ALPHA_FIXER_FFPROBE_EXE"] = str(root / "imageio_ffmpeg/binaries" / layout["ffprobe"])
    # Wand uses MAGICK_HOME/lib on Unix and MAGICK_HOME on Windows.
    # PyInstaller rewrites ctypes calls, but explicitly preloading also supports sonames.
    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        # Retain handles for the lifetime of the frozen process.
        globals()["_dll_directory"] = os.add_dll_directory(str(root))
    import ctypes
    ctypes.CDLL(str(root / layout["imagemagick_library"]), mode=getattr(ctypes, "RTLD_GLOBAL", 0))


if getattr(sys, "frozen", False):
    configure_bundle(Path(sys._MEIPASS))
