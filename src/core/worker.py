"""
Background worker threads for the Alpha Fixer and Converter tools.

Uses QThread + signals for safe UI communication without blocking the main thread.

For large batches (>= _LARGE_BATCH_THRESHOLD files) the workers suppress
per-file success log messages and emit progress updates at most every
_PROGRESS_INTERVAL files to prevent flooding the UI event queue.
"""
import os
import time
import traceback
import logging
from pathlib import Path
from typing import Optional, Callable

from PyQt6.QtCore import QThread, pyqtSignal

# Above this number of files, suppress individual success log lines and
# throttle progress signals to keep the UI responsive.
_LARGE_BATCH_THRESHOLD = 1_000
# Minimum seconds between consecutive progress signal emissions in large-batch mode.
_PROGRESS_MIN_INTERVAL = 0.1   # 100 ms

from .alpha_processor import (
    load_image,
    save_image,
    apply_alpha_preset,
    apply_manual_alpha,
    apply_rgba_adjust,
    collect_files,
    SUPPORTED_READ,
)
from .file_converter import convert_file, build_output_path
from .presets import AlphaPreset

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Alpha Worker
# ---------------------------------------------------------------------------

class AlphaWorker(QThread):
    """Processes alpha on one or many files in a background thread."""

    progress = pyqtSignal(int, int, str)          # current, total, current_file
    file_done = pyqtSignal(str, bool, str)         # path, success, message
    finished = pyqtSignal(int, int)                # success_count, error_count
    error = pyqtSignal(str)

    def __init__(
        self,
        files: list[str],
        preset: Optional[AlphaPreset] = None,
        manual_params: Optional[dict] = None,
        output_dir: Optional[str] = None,
        input_root: Optional[str] = None,
        overwrite: bool = False,
        suffix: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._files = list(files)
        self._preset = preset
        self._manual = manual_params        # dict with keys: mode, value, threshold, invert
        self._output_dir = output_dir
        self._input_root = input_root
        self._overwrite = overwrite
        self._suffix = suffix
        self._abort = False

    def stop(self):
        self._abort = True

    def run(self):
        total = len(self._files)
        success = 0
        errors = 0
        large_batch = total >= _LARGE_BATCH_THRESHOLD
        last_progress_time = 0.0
        for idx, src in enumerate(self._files):
            if self._abort:
                break
            # Throttle progress signal in large-batch mode
            now = time.monotonic()
            if not large_batch or (now - last_progress_time) >= _PROGRESS_MIN_INTERVAL:
                self.progress.emit(idx, total, src)
                last_progress_time = now
            try:
                img = load_image(src)
                if self._preset is not None:
                    img = apply_alpha_preset(img, self._preset)
                elif self._manual is not None:
                    img = apply_manual_alpha(
                        img,
                        value=self._manual.get("value"),  # None = clamp only, no fixed value
                        threshold=self._manual.get("threshold", 0),
                        invert=self._manual.get("invert", False),
                        clamp_min=self._manual.get("clamp_min", 0),
                        clamp_max=self._manual.get("clamp_max", 255),
                        binary_cut=self._manual.get("binary_cut", False),
                        mode=self._manual.get("mode", "set"),
                    )
                # Optional per-channel RGBA adjust (works with both preset and manual modes)
                rgb = (self._manual or {}).get("rgb")
                if rgb and (rgb.get("r") or rgb.get("g") or rgb.get("b") or rgb.get("a")):
                    img = apply_rgba_adjust(
                        img,
                        red_delta=rgb.get("r", 0),
                        green_delta=rgb.get("g", 0),
                        blue_delta=rgb.get("b", 0),
                        alpha_delta=rgb.get("a", 0),
                    )
                dest = self._resolve_output(src)
                os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                ext = Path(src).suffix.lower()
                save_image(img, dest, ext)
                success += 1
                # In large-batch mode suppress per-file success messages to keep
                # the UI log from accumulating 50 000 lines.
                if not large_batch:
                    self.file_done.emit(src, True, dest)
            except Exception:
                errors += 1
                msg = traceback.format_exc()
                logger.error("Alpha worker error on %s:\n%s", src, msg)
                self.file_done.emit(src, False, msg)  # always emit errors
        self.finished.emit(success, errors)

    def _resolve_output(self, src: str) -> str:
        p = Path(src)
        name = p.stem + (self._suffix or "") + p.suffix
        # Always honour output_dir when the user has specified one, regardless
        # of whether overwrite mode is active (overwrite = no filename suffix,
        # not "write back to the source directory").
        if self._output_dir:
            # Mirror subdirectory structure relative to input_root so that
            # files from different subdirectories never overwrite each other.
            # E.g. root=C:/textures, src=C:/textures/chars/hero.png,
            #      output_dir=D:/out  →  D:/out/chars/hero.png
            if self._input_root:
                try:
                    rel = p.parent.relative_to(self._input_root)
                    dest_dir = Path(self._output_dir) / rel
                except ValueError:
                    # src is not under input_root (e.g. file added from a
                    # different drive); fall back to flat placement.
                    logger.debug(
                        "_resolve_output: %s is not relative to root %s; "
                        "placing flat in output_dir",
                        src, self._input_root,
                    )
                    dest_dir = Path(self._output_dir)
            else:
                dest_dir = Path(self._output_dir)
            return str(dest_dir / name)
        return str(p.parent / name)


# ---------------------------------------------------------------------------
# Converter Worker
# ---------------------------------------------------------------------------

class ConverterWorker(QThread):
    """Converts files between formats in a background thread."""

    progress = pyqtSignal(int, int, str)
    file_done = pyqtSignal(str, bool, str)
    finished = pyqtSignal(int, int)
    error = pyqtSignal(str)

    def __init__(
        self,
        files: list[str],
        target_format: str,
        target_ext: str,
        output_dir: Optional[str] = None,
        input_root: Optional[str] = None,
        quality: int = 90,
        resize: Optional[tuple[int, int]] = None,
        keep_metadata: bool = False,
        suffix: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._files = list(files)
        self._target_format = target_format
        self._target_ext = target_ext
        self._output_dir = output_dir
        self._input_root = input_root
        self._quality = quality
        self._resize = resize
        self._keep_metadata = keep_metadata
        self._suffix = suffix
        self._abort = False

    def stop(self):
        self._abort = True

    def run(self):
        total = len(self._files)
        success = 0
        errors = 0
        large_batch = total >= _LARGE_BATCH_THRESHOLD
        last_progress_time = 0.0
        for idx, src in enumerate(self._files):
            if self._abort:
                break
            now = time.monotonic()
            if not large_batch or (now - last_progress_time) >= _PROGRESS_MIN_INTERVAL:
                self.progress.emit(idx, total, src)
                last_progress_time = now
            try:
                dest = build_output_path(
                    src,
                    self._target_ext,
                    output_dir=self._output_dir,
                    input_root=self._input_root,
                    suffix=self._suffix,
                )
                convert_file(
                    src,
                    dest,
                    self._target_format,
                    quality=self._quality,
                    resize=self._resize,
                    keep_metadata=self._keep_metadata,
                )
                success += 1
                if not large_batch:
                    self.file_done.emit(src, True, dest)
            except Exception:
                errors += 1
                msg = traceback.format_exc()
                logger.error("Converter worker error on %s:\n%s", src, msg)
                self.file_done.emit(src, False, msg)
        self.finished.emit(success, errors)
