"""Writable application storage without requiring a writable installation."""

import os
import logging
from pathlib import Path
import sys
import tempfile

logger = logging.getLogger(__name__)


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
            logger.warning(
                "Falling back to per-user application data because %s is not writable",
                preferred,
                exc_info=True,
            )
    fallback = user_data_directory()
    if subdirectory:
        fallback /= subdirectory
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback
