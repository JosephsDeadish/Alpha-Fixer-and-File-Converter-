"""Writable application storage without requiring a writable installation."""

import os
from pathlib import Path
import sys
import tempfile


def user_data_directory() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "AlphaFixerConverter"


def writable_app_directory(preferred: Path, subdirectory: str = "") -> Path:
    """Retain portable storage when writable; otherwise use per-user data."""
    if preferred.is_dir():
        try:
            with tempfile.TemporaryFile(dir=preferred):
                return preferred
        except OSError:
            pass
    fallback = user_data_directory()
    if subdirectory:
        fallback /= subdirectory
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback
