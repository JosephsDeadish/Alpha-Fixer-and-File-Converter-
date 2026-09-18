"""
Video Tool Dialog.

Provides a lightweight video editor that lets the user:
  • Add one or more video clips (via imageio + imageio-ffmpeg + ffmpeg)
  • Drag clips in the list to reorder them (no Up/Down buttons)
  • Trim clips with start/end sliders
  • Adjust white point, black point, brightness, contrast, saturation, sharpness
    – all via smooth drag-sliders with live value readouts
  • Apply visual filters: greyscale, sepia, invert, sharpen, blur, vignette, etc.
  • Preview with play/pause/rewind and a position scrubber
  • Export to MP4 (via imageio+ffmpeg) or animated GIF (via Pillow)

**Dependency note**: Full video I/O requires imageio, imageio-ffmpeg, and a
working ffmpeg executable, preferably bundled and otherwise from the system PATH.
If video dependencies are unavailable the dialog can still assemble still images and GIFs
into an animated GIF.

UX highlights (Round-90):
  • All numeric controls use drag-sliders – no arrow-button spinboxes.
  • Clip list is drag-to-reorder; clip data stays in sync via item UserRole.
  • Trim sliders auto-update when a clip is selected.
  • Live preview refreshes immediately on any slider change.

Opening the dialog:
  • Right-clicking anywhere on the main window → "Open Video Editor"
"""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from threading import Lock
from typing import Optional

from PyQt6.QtCore import (
    Qt, QTimer, QSize, pyqtSignal,
)
from PyQt6.QtGui import (
    QImage, QPixmap, QKeySequence, QShortcut, QIcon,
    QDragEnterEvent, QDropEvent, QDragMoveEvent,
)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFileDialog, QSlider,
    QComboBox, QGroupBox, QGridLayout,
    QMessageBox, QProgressDialog, QSplitter, QWidget, QApplication,
    QFrame, QScrollArea,
)

_VIDEO_EXTS = {
    # Common containers
    ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".3gp", ".3g2", ".ts", ".m2ts",
    ".mts", ".vob", ".ogv", ".ogg", ".rm", ".rmvb", ".divx",
    ".asf", ".f4v", ".mxf", ".dv", ".yuv",
    # PlayStation / handheld console video formats (decoded via ffmpeg)
    ".pmf",    # PSP Movie Format (MPEG-2 based)
    ".pss",    # PlayStation 2 streaming video
    ".str",    # PlayStation 1/2 streaming video
    ".xa",     # PlayStation 1 audio/video
    # Disc images — experimental: ffmpeg can read video streams from these
    # when the image contains a demuxable video track (e.g. PSP UMD .iso
    # with MPEG inside).  Raw sector-level disc images may not load.
    ".iso",    # ISO 9660 disc image (PSP UMD / PS2 DVD)
    ".umd",    # PSP UMD disc image (same structure as ISO 9660)
    ".bin",    # CD-ROM disc image (may contain MPEG video sectors)
}
_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif",
    ".gif", ".dds", ".tga", ".ico", ".ppm", ".pgm", ".pbm", ".pnm",
    ".pcx", ".avif", ".qoi", ".svg", ".jp2", ".j2k", ".j2c",
    ".jfif", ".jpe", ".xnb", ".tim",
}

_PREVIEW_MAX_W = 420
_PREVIEW_MAX_H = 320
_CLIP_ROLE = Qt.ItemDataRole.UserRole  # stores _ClipEntry in list item


def _gif_frame_rect(gif, frame_img) -> tuple[int, int, int, int]:
    """Return the logical update rectangle for the current GIF frame."""
    rect = getattr(gif, "dispose_extent", None)
    if isinstance(rect, tuple) and len(rect) == 4:
        return rect
    tile = getattr(gif, "tile", None)
    if tile:
        candidate = tile[0][1]
        if isinstance(candidate, tuple) and len(candidate) == 4:
            return candidate
    return (0, 0, frame_img.width, frame_img.height)


@lru_cache(maxsize=1)
def _has_imageio() -> bool:
    """Return True when imageio is importable for video container I/O."""
    try:
        import imageio  # noqa: F401
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _has_imageio_ffmpeg() -> bool:
    """Return True when the imageio-ffmpeg plugin is importable."""
    try:
        import imageio_ffmpeg  # noqa: F401
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _configure_imageio_ffmpeg() -> Optional[str]:
    """Configure imageio to use the bundled/system ffmpeg executable once."""
    ffmpeg_exe = _get_ffmpeg_exe()
    if ffmpeg_exe:
        os.environ.setdefault("IMAGEIO_FFMPEG_EXE", ffmpeg_exe)
    return ffmpeg_exe


@lru_cache(maxsize=1)
def _has_ffmpeg() -> bool:
    """Return True if a bundled or PATH ffmpeg executable is available."""
    return _get_ffmpeg_exe() is not None


def _get_ffmpeg_exe() -> Optional[str]:
    """Return the path to the ffmpeg executable, or None."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe:
            return exe
    except Exception:
        pass
    try:
        import shutil
        return shutil.which("ffmpeg")
    except Exception:
        return None


def _open_video_reader(path: str):
    """Open an imageio ffmpeg reader, preferring the bundled ffmpeg binary."""
    import imageio

    _configure_imageio_ffmpeg()
    return imageio.get_reader(path, format="FFMPEG")


def _coerce_frame_count(value) -> int:
    """Return a positive integer frame count, or 0 when unavailable."""
    try:
        count = int(value)
    except Exception:
        return 0
    return count if count > 0 else 0


def _coerce_frame_size(value) -> Optional[tuple[int, int]]:
    """Return a positive (width, height) tuple when metadata provides one."""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            width = int(value[0])
            height = int(value[1])
        except Exception:
            return None
        if width > 0 and height > 0:
            return width, height
    return None


def _probe_video_clip(path: str) -> tuple[float, int, Optional[tuple[int, int]], object | None]:
    """Return (fps, frame_count, frame_size, first_frame) for a video."""
    reader = _open_video_reader(path)
    first_frame = None
    try:
        meta = reader.get_meta_data()
        try:
            fps = float(meta.get("fps") or 25.0)
        except Exception:
            fps = 25.0
        fps = fps if fps > 0 else 25.0
        frame_size = (
            _coerce_frame_size(meta.get("source_size"))
            or _coerce_frame_size(meta.get("size"))
        )
        frame_count = _coerce_frame_count(meta.get("nframes"))
        if frame_count <= 0:
            try:
                import imageio_ffmpeg

                counted, secs = imageio_ffmpeg.count_frames_and_secs(path)
                frame_count = _coerce_frame_count(counted)
                if frame_count <= 0 and secs > 0 and fps > 0:
                    frame_count = max(1, int(round(secs * fps)))
            except Exception:
                pass
        if frame_count <= 0:
            try:
                frame_count = _coerce_frame_count(reader.count_frames())
            except Exception:
                pass
        if frame_count <= 0:
            try:
                duration = float(meta.get("duration") or 0.0)
            except Exception:
                duration = 0.0
            if duration > 0 and fps > 0:
                frame_count = max(1, int(round(duration * fps)))
        if frame_count <= 0:
            try:
                first_frame = reader.get_data(0)
            except Exception:
                first_frame = None
            if first_frame is not None:
                try:
                    frame_size = (int(first_frame.shape[1]), int(first_frame.shape[0]))
                except Exception:
                    frame_size = None
                frame_count = 1
        elif frame_size is None:
            try:
                first_frame = reader.get_data(0)
            except Exception:
                first_frame = None
            if first_frame is not None:
                try:
                    frame_size = (int(first_frame.shape[1]), int(first_frame.shape[0]))
                except Exception:
                    frame_size = None
        return fps, frame_count, frame_size, first_frame
    finally:
        reader.close()


def _load_video_frames(path: str) -> tuple[list["PIL.Image.Image"], float]:
    """Decode a whole video into RGBA frames for reuse in GIF Builder."""
    from PIL import Image

    reader = _open_video_reader(path)
    frames: list[Image.Image] = []
    try:
        meta = reader.get_meta_data()
        try:
            fps = float(meta.get("fps") or 25.0)
        except Exception:
            fps = 25.0
        fps = fps if fps > 0 else 25.0
        frame_index = 0
        while True:
            try:
                frame = reader.get_data(frame_index)
            except IndexError:
                break
            except Exception:
                if frame_index == 0:
                    raise
                break
            pil = Image.fromarray(frame)
            try:
                frames.append(pil.convert("RGBA"))
            finally:
                pil.close()
            frame_index += 1
        if not frames:
            raise ValueError("No readable frames found in video.")
        return frames, fps
    finally:
        reader.close()


def _pil_to_pixmap(pil_img) -> QPixmap:
    from PIL import Image  # noqa: F401
    rgba = pil_img.convert("RGBA")
    try:
        data = rgba.tobytes("raw", "RGBA")
        qi = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qi)
    finally:
        rgba.close()


def _apply_adjustments(pil_img, brightness: float, contrast: float,
                       black_point: int, white_point: int,
                       saturation: float, sharpness: float) -> "PIL.Image.Image":
    """Apply brightness/contrast/levels/saturation/sharpness to a PIL RGBA image."""
    from PIL import Image, ImageEnhance, ImageOps
    img = pil_img.convert("RGB")
    if black_point > 0 or white_point < 255:
        def _levels(v: int) -> int:
            bp = max(0, min(254, black_point))
            wp = max(bp + 1, min(255, white_point))
            return max(0, min(255, int((v - bp) * 255 / max(1, wp - bp))))
        img = img.point(lambda v: _levels(v))
    if abs(brightness - 1.0) > 0.01:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if abs(contrast - 1.0) > 0.01:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if abs(saturation - 1.0) > 0.01:
        img = ImageEnhance.Color(img).enhance(saturation)
    if abs(sharpness - 1.0) > 0.01:
        img = ImageEnhance.Sharpness(img).enhance(sharpness)
    return img.convert("RGBA")


def _apply_filter(pil_img, filter_name: str) -> "PIL.Image.Image":
    """Apply a named visual filter to a PIL RGBA image."""
    from PIL import Image, ImageFilter
    img = pil_img.convert("RGB")
    if filter_name == "none":
        pass
    elif filter_name == "greyscale":
        img = img.convert("L").convert("RGB")
    elif filter_name == "sepia":
        grey = img.convert("L")
        sepia = Image.new("RGB", img.size)
        pixels = grey.load()
        sepia_pix = sepia.load()
        w, h = img.size
        for y in range(h):
            for x in range(w):
                v = pixels[x, y]
                sepia_pix[x, y] = (
                    min(255, int(v * 1.07)),
                    min(255, int(v * 0.74)),
                    min(255, int(v * 0.43)),
                )
    elif filter_name == "invert":
        from PIL import ImageOps
        img = ImageOps.invert(img)
    elif filter_name == "sharpen":
        img = img.filter(ImageFilter.SHARPEN)
    elif filter_name == "blur":
        img = img.filter(ImageFilter.GaussianBlur(radius=2))
    elif filter_name == "edge_enhance":
        img = img.filter(ImageFilter.EDGE_ENHANCE)
    elif filter_name == "emboss":
        img = img.filter(ImageFilter.EMBOSS)
    elif filter_name == "vignette":
        import math
        mask = Image.new("L", img.size, 0)
        w, h = img.size
        cx, cy = w / 2, h / 2
        mx = cx * 1.4
        pix = mask.load()
        for y in range(h):
            for x in range(w):
                d = math.hypot((x - cx) / mx, (y - cy) / mx)
                pix[x, y] = max(0, min(255, int((1 - min(1.0, d)) * 255)))
        dark = Image.new("RGB", img.size, (0, 0, 0))
        img = Image.composite(img, dark, mask)
    return img.convert("RGBA")


def _coerce_export_size(size: tuple[int, int], fmt: str) -> tuple[int, int]:
    """Clamp export size and round MP4 output up to even dimensions."""
    width = max(1, int(size[0]))
    height = max(1, int(size[1]))
    if fmt == "mp4":
        if width % 2:
            width += 1
        if height % 2:
            height += 1
    return width, height


def _fit_frame_to_canvas(pil_img, canvas_size: tuple[int, int], fmt: str) -> "PIL.Image.Image":
    """Resize a frame to fit inside a shared export canvas with letterboxing."""
    from PIL import Image, ImageOps

    canvas_size = _coerce_export_size(canvas_size, fmt)
    if pil_img.size == canvas_size and pil_img.mode == "RGBA":
        return pil_img.copy()

    rgba = pil_img if pil_img.mode == "RGBA" else pil_img.convert("RGBA")
    fitted = rgba
    canvas = None
    try:
        if rgba.size != canvas_size:
            fitted = ImageOps.contain(rgba, canvas_size, method=Image.Resampling.LANCZOS)
        background = (0, 0, 0, 255) if fmt == "mp4" else (0, 0, 0, 0)
        canvas = Image.new("RGBA", canvas_size, background)
        offset = (
            max(0, (canvas_size[0] - fitted.width) // 2),
            max(0, (canvas_size[1] - fitted.height) // 2),
        )
        canvas.paste(fitted, offset, fitted)
        return canvas
    finally:
        if fitted is not rgba:
            fitted.close()
        if rgba is not pil_img:
            rgba.close()


class _ImageFrameGetter:
    """Picklable frame getter for a single-image clip.

    Using a module-level class instead of a local closure avoids the
    ``AttributeError: Can't pickle local object '_load_image_as_clip.<locals>._get'``
    that occurs when Qt serialises QListWidgetItem UserRole data during a
    drag-to-reorder operation.
    """

    def __init__(self, img: "PIL.Image.Image") -> None:
        self._img = img

    def __call__(self, idx: int) -> "PIL.Image.Image":
        return self._img.copy()

    def _close_reader(self) -> None:
        try:
            self._img.close()
        except Exception:
            pass


class _SequenceFrameGetter:
    """Picklable frame getter backed by one or more in-memory PIL frames."""

    def __init__(self, frames: list["PIL.Image.Image"]) -> None:
        self._frames = list(frames)

    def __call__(self, idx: int) -> "PIL.Image.Image":
        clamped = max(0, min(len(self._frames) - 1, int(idx)))
        return self._frames[clamped].copy()

    def _close_reader(self) -> None:
        for frame in self._frames:
            try:
                frame.close()
            except Exception:
                pass
        self._frames.clear()


class _VideoFrameGetter:
    """Picklable frame getter for a multi-frame video clip."""

    def __init__(self, path: str, total_frames: int, first_frame=None) -> None:
        self._path = path
        self._total_frames = max(1, int(total_frames))
        self._prefetched_frame = first_frame
        self._reader = None
        self._last_idx = -1
        self._last_frame = None
        self._lock = Lock()

    def _open_reader(self) -> None:
        self._reader = _open_video_reader(self._path)

    def _close_reader(self) -> None:
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:
                pass
            self._reader = None
        self._last_idx = -1
        self._last_frame = None

    def __call__(self, idx: int) -> "PIL.Image.Image":
        from PIL import Image

        clamped = max(0, min(self._total_frames - 1, int(idx)))
        with self._lock:
            if clamped == 0 and self._prefetched_frame is not None:
                frame = self._prefetched_frame
                self._last_idx = 0
                self._last_frame = frame
                self._prefetched_frame = None
                return Image.fromarray(frame).convert("RGBA")
            reopened = self._reader is None or clamped < self._last_idx
            if reopened:
                self._close_reader()
                self._open_reader()
            try:
                if clamped == self._last_idx and self._last_frame is not None:
                    frame = self._last_frame
                elif not reopened and clamped == self._last_idx + 1:
                    frame = self._reader.get_next_data()
                else:
                    frame = self._reader.get_data(clamped)
                self._last_idx = clamped
                self._last_frame = frame
            except Exception:
                self._close_reader()
                raise
        return Image.fromarray(frame).convert("RGBA")

    def __getstate__(self) -> dict:
        return {"_path": self._path, "_total_frames": self._total_frames}

    def __setstate__(self, state: dict) -> None:
        self._path = state["_path"]
        self._total_frames = max(1, int(state["_total_frames"]))
        self._prefetched_frame = None
        self._reader = None
        self._last_idx = -1
        self._last_frame = None
        self._lock = Lock()


class _ClipEntry:
    """One video clip or image in the video tool's timeline."""

    def __init__(self, path: str, total_frames: int,
                 get_frame_fn, fps: float = 25.0,
                 frame_size: Optional[tuple[int, int]] = None):
        self.path = path
        self.total_frames = total_frames
        self.fps = fps
        self._get_frame = get_frame_fn   # callable(frame_idx) → PIL RGBA image
        self.frame_size = frame_size
        self.trim_start: int = 0
        self.trim_end: int = max(0, total_frames - 1)

    @property
    def active_frames(self) -> int:
        return max(0, self.trim_end - self.trim_start + 1)

    def get_frame(self, idx: int) -> "PIL.Image.Image":
        return self._get_frame(self.trim_start + idx)

    def close(self) -> None:
        close_fn = getattr(self._get_frame, "_close_reader", None)
        if callable(close_fn):
            close_fn()


def _format_clip_label(clip: "_ClipEntry", path: str, icon: str) -> str:
    size = clip.frame_size
    size_text = f"{size[0]}×{size[1]}  •  " if size else ""
    if clip.total_frames > 1:
        return f"{icon}  {Path(path).name}  [{size_text}{clip.total_frames} fr @ {clip.fps:.1f} fps]"
    return f"{icon}  {Path(path).name}  [{size_text}still]"


def _load_video_clip(path: str) -> Optional["_ClipEntry"]:
    """Try to load a video file using imageio-ffmpeg.  Returns None on failure."""
    try:
        fps, frame_count, frame_size, first_frame = _probe_video_clip(path)
        if frame_count <= 0:
            return None
        return _ClipEntry(
            path,
            frame_count,
            _VideoFrameGetter(path, frame_count, first_frame),
            fps,
            frame_size=frame_size,
        )
    except Exception:
        return None


def _load_image_as_clip(path: str) -> Optional["_ClipEntry"]:
    """Wrap a still image or animated GIF file as a clip."""
    try:
        from PIL import Image
        from ..core.alpha_processor import load_image

        ext = Path(path).suffix.lower()
        if ext == ".gif":
            gif = Image.open(path)
            frames: list[Image.Image] = []
            durations: list[int] = []
            try:
                n = getattr(gif, "n_frames", 1)
                if n <= 1:
                    frames.append(gif.convert("RGBA"))
                else:
                    canvas = Image.new("RGBA", gif.size, (0, 0, 0, 0))
                    try:
                        for i in range(n):
                            gif.seek(i)
                            curr = gif.convert("RGBA")
                            previous_canvas = canvas.copy()
                            composite = canvas.copy()
                            rect = _gif_frame_rect(gif, curr)
                            left, top, right, bottom = rect
                            rect_size = (max(0, right - left), max(0, bottom - top))
                            if curr.size == rect_size:
                                paste_img = curr
                            elif curr.width >= right and curr.height >= bottom:
                                paste_img = curr.crop(rect)
                            else:
                                paste_img = curr
                            try:
                                composite.paste(paste_img, (left, top), paste_img)
                            finally:
                                if paste_img is not curr:
                                    paste_img.close()
                                curr.close()
                            frames.append(composite.copy())
                            durations.append(max(1, int(gif.info.get("duration", 100) or 100)))
                            disposal = getattr(gif, "disposal_method", gif.info.get("disposal", 0))
                            canvas.close()
                            if disposal == 2:
                                canvas = Image.new("RGBA", gif.size, (0, 0, 0, 0))
                                composite.close()
                                previous_canvas.close()
                            elif disposal == 3:
                                canvas = previous_canvas
                                composite.close()
                            else:
                                canvas = composite
                                previous_canvas.close()
                    finally:
                        canvas.close()
            finally:
                gif.close()
            fps = 25.0
            if durations:
                avg_duration = sum(durations) / len(durations)
                if avg_duration > 0:
                    fps = max(0.1, min(60.0, 1000.0 / avg_duration))
            total_frames = max(1, len(frames))
            getter = _SequenceFrameGetter(frames)
            return _ClipEntry(path, total_frames, getter, fps, frame_size=frames[0].size if frames else None)

        img = load_image(path)
        return _ClipEntry(path, 1, _ImageFrameGetter(img), 25.0, frame_size=img.size)
    except Exception:
        return None


def _make_hslider(lo: int, hi: int, val: int) -> QSlider:
    """Return a horizontal QSlider."""
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    s.setTracking(True)
    return s


class _ClipListWidget(QListWidget):
    """Drag-to-reorder clip list that also accepts dropped video/image files.

    Each item stores its ``_ClipEntry`` in ``_CLIP_ROLE``.  ``order_changed``
    fires after any internal drag so the caller can re-sync ``_clips``.
    """

    files_dropped = pyqtSignal(list, int)  # list[str], insert_row
    order_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # InternalMove keeps _ClipEntry objects intact across drags (same
        # reasoning as _FrameListWidget in gif_builder.py).
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.model().rowsMoved.connect(lambda *_: self.order_changed.emit())

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()
                     if url.toLocalFile()]
            if paths:
                pos = event.position().toPoint()
                row = self.count()
                index = self.indexAt(pos)
                if index.isValid():
                    row = index.row()
                    rect = self.visualRect(index)
                    if pos.y() > rect.center().y():
                        row += 1
                self.files_dropped.emit(paths, row)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class VideoToolDialog(QDialog):
    """Lightweight video editor dialog.

    Combines multiple clips, applies visual adjustments and filters, and
    exports the result. Video clips and MP4 export require imageio,
    imageio-ffmpeg, and a working ffmpeg executable. Still-image clips can
    still be assembled into animated GIF exports without those video
    dependencies.
    """
    SHORTCUT_DEFS = (
        ("video_remove_selected", "Delete", "Remove selected clip", "Video Editor"),
        ("video_toggle_play", "Space", "Play or pause preview", "Video Editor"),
        ("video_export", "Ctrl+S", "Export video or GIF", "Video Editor"),
    )

    def __init__(self, parent=None, tooltip_mgr=None):
        super().__init__(parent)
        self.setWindowTitle("🎬 Video Editor")
        self.setMinimumSize(960, 660)
        self.setModal(False)
        self._tooltip_mgr = tooltip_mgr
        _configure_imageio_ffmpeg()
        self._clips: list[_ClipEntry] = []
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._advance_preview)
        self._is_playing: bool = False
        self._ffmpeg_available = _has_ffmpeg()
        self._imageio_available = _has_imageio()
        self._imageio_ffmpeg_available = _has_imageio_ffmpeg()
        self._video_io_available = (
            self._ffmpeg_available
            and self._imageio_available
            and self._imageio_ffmpeg_available
        )
        self._mp4_export_available = self._video_io_available
        self._build_ui()
        mgr = self._resolve_tooltip_mgr()
        if mgr is not None:
            self.register_tooltips(mgr)
        self._setup_shortcuts()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        title = QLabel("🎬  Video Editor")
        title.setObjectName("subheader")
        root.addWidget(title)

        if not self._video_io_available:
            warn = QLabel(
                "⚠  Video import and MP4 export need imageio, imageio-ffmpeg, and a working ffmpeg executable.  "
                "You can still add images/GIFs and export an animated GIF."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet("color: orange;")
            root.addWidget(warn)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ── Left: clip list ──────────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        tb = QHBoxLayout()
        self._btn_add_video = QPushButton("🎞  Add Video")
        self._btn_add_video.setEnabled(self._video_io_available)
        self._btn_add_video.setToolTip("Add a video file to the timeline. (Requires ffmpeg, imageio, and imageio-ffmpeg)")
        self._btn_add_video.clicked.connect(self._add_video)
        tb.addWidget(self._btn_add_video)

        self._btn_add_img = QPushButton("🖼  Add Images / GIFs")
        self._btn_add_img.setToolTip("Add still image(s), animated GIFs, or other supported image files.")
        self._btn_add_img.clicked.connect(self._add_images)
        tb.addWidget(self._btn_add_img)

        self._btn_remove = QPushButton("🗑  Remove")
        self._btn_remove.setToolTip("Remove selected clip.  Shortcut: Delete")
        self._btn_remove.clicked.connect(self._remove_selected)
        tb.addWidget(self._btn_remove)
        left_layout.addLayout(tb)

        insert_row = QHBoxLayout()
        insert_row.addWidget(QLabel("Insert new clips:"))
        self._insert_mode_combo = QComboBox()
        self._insert_mode_combo.addItem("After selected clip", userData="after")
        self._insert_mode_combo.addItem("Before selected clip", userData="before")
        self._insert_mode_combo.addItem("At end of timeline", userData="end")
        self._insert_mode_combo.setToolTip(
            "Controls where newly added media is inserted when using the Add buttons.\n"
            "Dropping files onto the timeline still inserts exactly at the drop position."
        )
        insert_row.addWidget(self._insert_mode_combo, 1)
        left_layout.addLayout(insert_row)

        hint = QLabel("💡 Drag clips to reorder  •  Drop files to add")
        hint.setStyleSheet("color: gray; font-style: italic; font-size: 11px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(hint)

        self._clip_list = _ClipListWidget()
        self._clip_list.files_dropped.connect(self._on_files_dropped)
        self._clip_list.order_changed.connect(self._sync_clips_from_list)
        self._clip_list.currentRowChanged.connect(self._on_clip_selected)
        left_layout.addWidget(self._clip_list, 1)

        # Trim sliders
        grp_trim = QGroupBox("Trim Selected Clip")
        trim_vl = QVBoxLayout(grp_trim)
        trim_vl.setSpacing(6)

        # Start trim
        start_row = QHBoxLayout()
        start_row.addWidget(QLabel("In:"))
        self._trim_start_slider = _make_hslider(0, 0, 0)
        self._trim_start_slider.setToolTip("Drag to set the clip's start (in) point.")
        self._trim_start_slider.valueChanged.connect(self._on_trim_start_changed)
        start_row.addWidget(self._trim_start_slider, 1)
        self._trim_start_lbl = QLabel("0")
        self._trim_start_lbl.setFixedWidth(48)
        self._trim_start_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        start_row.addWidget(self._trim_start_lbl)
        trim_vl.addLayout(start_row)

        # End trim
        end_row = QHBoxLayout()
        end_row.addWidget(QLabel("Out:"))
        self._trim_end_slider = _make_hslider(0, 0, 0)
        self._trim_end_slider.setToolTip("Drag to set the clip's end (out) point.")
        self._trim_end_slider.valueChanged.connect(self._on_trim_end_changed)
        end_row.addWidget(self._trim_end_slider, 1)
        self._trim_end_lbl = QLabel("0")
        self._trim_end_lbl.setFixedWidth(48)
        self._trim_end_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        end_row.addWidget(self._trim_end_lbl)
        trim_vl.addLayout(end_row)

        self._clip_info_lbl = QLabel("")
        self._clip_info_lbl.setStyleSheet("color: gray; font-size: 11px;")
        trim_vl.addWidget(self._clip_info_lbl)
        left_layout.addWidget(grp_trim)

        splitter.addWidget(left)

        # ── Centre: preview ──────────────────────────────────────────────
        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(0, 0, 0, 0)
        centre_layout.setSpacing(6)

        grp_preview = QGroupBox("Preview")
        pv_layout = QVBoxLayout(grp_preview)
        pv_layout.setSpacing(6)

        self._preview_lbl = QLabel()
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_lbl.setMinimumSize(_PREVIEW_MAX_W, _PREVIEW_MAX_H)
        self._preview_lbl.setFrameShape(QFrame.Shape.StyledPanel)
        self._preview_lbl.setText("(no clips)")
        pv_layout.addWidget(self._preview_lbl, 1)

        # Scrubber
        self._scrubber = _make_hslider(0, 0, 0)
        self._scrubber.setToolTip("Drag to scrub through the timeline.")
        self._scrubber.valueChanged.connect(self._on_scrub)
        pv_layout.addWidget(self._scrubber)

        # Transport
        ctrl = QHBoxLayout()
        self._btn_rewind = QPushButton("⏮")
        self._btn_rewind.setFixedWidth(36)
        self._btn_rewind.setToolTip("Rewind to beginning")
        self._btn_rewind.clicked.connect(self._rewind)
        ctrl.addWidget(self._btn_rewind)

        self._btn_play = QPushButton("▶  Play")
        self._btn_play.setCheckable(True)
        self._btn_play.setToolTip("Play / Pause.  Space bar also works.")
        self._btn_play.toggled.connect(self._on_play_toggled)
        ctrl.addWidget(self._btn_play)

        self._pos_lbl = QLabel("0 / 0")
        self._pos_lbl.setObjectName("subheader")
        ctrl.addWidget(self._pos_lbl)
        ctrl.addStretch()
        pv_layout.addLayout(ctrl)

        # FPS row
        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("Preview FPS:"))
        self._fps_slider = _make_hslider(1, 60, 25)
        self._fps_slider.setToolTip("Playback speed for preview and export.")
        self._fps_val_lbl = QLabel("25 fps")
        self._fps_val_lbl.setFixedWidth(50)
        self._fps_val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._fps_slider.valueChanged.connect(
            lambda v: self._fps_val_lbl.setText(f"{v} fps")
        )
        fps_row.addWidget(self._fps_slider, 1)
        fps_row.addWidget(self._fps_val_lbl)
        pv_layout.addLayout(fps_row)

        centre_layout.addWidget(grp_preview, 1)
        splitter.addWidget(centre)

        # ── Right: adjustments + export ──────────────────────────────────
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_inner = QWidget()
        right_layout = QVBoxLayout(right_inner)
        right_layout.setContentsMargins(4, 0, 4, 0)
        right_layout.setSpacing(6)
        right_scroll.setWidget(right_inner)

        grp_adj = QGroupBox("Visual Adjustments")
        adj_vl = QVBoxLayout(grp_adj)
        adj_vl.setSpacing(8)

        def _adj_row(label: str, lo: int, hi: int, val: int,
                     fmt_fn=None, tooltip: str = "") -> QSlider:
            """Add a labelled slider row; return the slider."""
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(90)
            row.addWidget(lbl)
            slider = _make_hslider(lo, hi, val)
            if tooltip:
                slider.setToolTip(tooltip)
            row.addWidget(slider, 1)
            val_lbl = QLabel()
            val_lbl.setFixedWidth(52)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if fmt_fn is None:
                fmt_fn = str
            val_lbl.setText(fmt_fn(val))
            slider.valueChanged.connect(lambda v, fn=fmt_fn, lb=val_lbl: lb.setText(fn(v)))
            slider.valueChanged.connect(self._refresh_preview_adjustments)
            row.addWidget(val_lbl)
            adj_vl.addLayout(row)
            return slider

        # Float sliders use ×100 integer range, divide by 100.0 when reading
        def _f(v: int) -> str:  # format 100-scale int as 2-dp float
            return f"{v / 100:.2f}"

        self._brightness_slider = _adj_row(
            "Brightness:", 10, 400, 100, _f,
            "1.00 = original  •  >1 = brighter  •  <1 = darker"
        )
        self._contrast_slider = _adj_row(
            "Contrast:", 10, 400, 100, _f,
            "1.00 = original  •  >1 = more contrast"
        )
        self._saturation_slider = _adj_row(
            "Saturation:", 0, 400, 100, _f,
            "1.00 = original  •  0.00 = greyscale  •  >1 = vivid"
        )
        self._sharpness_slider = _adj_row(
            "Sharpness:", 0, 400, 100, _f,
            "1.00 = original  •  >1 = sharper  •  <1 = softer"
        )
        self._black_slider = _adj_row(
            "Black point:", 0, 254, 0,
            tooltip="Input level mapped to black — lifts shadows"
        )
        self._white_slider = _adj_row(
            "White point:", 1, 255, 255,
            tooltip="Input level mapped to white — pulls down highlights"
        )

        btn_reset = QPushButton("↺  Reset All Adjustments")
        btn_reset.clicked.connect(self._reset_adjustments)
        adj_vl.addWidget(btn_reset)

        right_layout.addWidget(grp_adj)

        grp_filter = QGroupBox("Visual Filter")
        fl_layout = QHBoxLayout(grp_filter)
        fl_layout.addWidget(QLabel("Filter:"))
        self._filter_combo = QComboBox()
        _FILTERS = [
            ("None", "none"), ("Greyscale", "greyscale"), ("Sepia", "sepia"),
            ("Invert", "invert"), ("Sharpen", "sharpen"), ("Blur", "blur"),
            ("Edge Enhance", "edge_enhance"), ("Emboss", "emboss"),
            ("Vignette", "vignette"),
        ]
        for label, key in _FILTERS:
            self._filter_combo.addItem(label, userData=key)
        self._filter_combo.currentIndexChanged.connect(self._refresh_preview_adjustments)
        fl_layout.addWidget(self._filter_combo, 1)
        right_layout.addWidget(grp_filter)

        grp_export = QGroupBox("Export")
        ex_vl = QVBoxLayout(grp_export)
        ex_vl.setSpacing(8)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format:"))
        self._export_fmt_combo = QComboBox()
        self._export_fmt_combo.addItem("Animated GIF (.gif)", userData="gif")
        if self._mp4_export_available:
            self._export_fmt_combo.addItem("MP4 Video (.mp4)", userData="mp4")
        self._export_fmt_combo.currentIndexChanged.connect(self._update_export_summary)
        fmt_row.addWidget(self._export_fmt_combo, 1)
        ex_vl.addLayout(fmt_row)

        self._export_size_lbl = QLabel("Canvas: auto once clips are added")
        self._export_size_lbl.setWordWrap(True)
        self._export_size_lbl.setStyleSheet("color: gray; font-size: 11px;")
        ex_vl.addWidget(self._export_size_lbl)

        self._export_pad_lbl = QLabel(
            "Mixed-size clips are resized to fit and centred automatically."
        )
        self._export_pad_lbl.setWordWrap(True)
        self._export_pad_lbl.setStyleSheet("color: gray; font-size: 11px;")
        ex_vl.addWidget(self._export_pad_lbl)

        self._btn_export = QPushButton("💾  Export…")
        self._btn_export.setToolTip("Render and export.  Shortcut: Ctrl+S")
        self._btn_export.setMinimumHeight(34)
        self._btn_export.clicked.connect(self._export)
        ex_vl.addWidget(self._btn_export)

        right_layout.addWidget(grp_export)
        right_layout.addStretch()

        splitter.addWidget(right_scroll)
        splitter.setSizes([240, 420, 260])

    def _resolve_tooltip_mgr(self):
        if self._tooltip_mgr is not None:
            return self._tooltip_mgr
        parent = self.parentWidget()
        while parent is not None:
            mgr = getattr(parent, "_tooltip_mgr", None)
            if mgr is not None:
                self._tooltip_mgr = mgr
                return mgr
            parent = parent.parentWidget()
        return None

    @classmethod
    def shortcut_definitions(cls) -> tuple[tuple[str, str, str, str], ...]:
        return cls.SHORTCUT_DEFS

    def _resolve_settings(self):
        parent = self.parentWidget()
        while parent is not None:
            settings = getattr(parent, "_settings", None)
            if settings is not None:
                return settings
            parent = parent.parentWidget()
        return None

    def _setup_shortcuts(self) -> None:
        self._shortcut_objects: dict[str, QShortcut] = {}
        self._bind_shortcut("video_remove_selected", "Delete", self._remove_selected)
        self._bind_shortcut("video_toggle_play", "Space", self._toggle_play)
        self._bind_shortcut("video_export", "Ctrl+S", self._export)

    def update_shortcut_binding(self, shortcut_id: str, key_sequence: str) -> None:
        shortcut = getattr(self, "_shortcut_objects", {}).get(shortcut_id)
        if shortcut is not None:
            shortcut.setKey(QKeySequence(key_sequence))

    def _bind_shortcut(self, shortcut_id: str, default: str, slot) -> None:
        settings = self._resolve_settings()
        key_sequence = default
        if settings is not None:
            key_sequence = settings.get_shortcut_binding(shortcut_id, default)
        shortcut = QShortcut(QKeySequence(key_sequence), self)
        shortcut.activated.connect(slot)
        self._shortcut_objects[shortcut_id] = shortcut

    def register_tooltips(self, mgr) -> None:
        """Register dialog widgets with the shared TooltipManager."""
        self._tooltip_mgr = mgr
        mgr.register(self._btn_add_video, "video_media_add")
        mgr.register(self._btn_add_img, "video_media_add")
        mgr.register(self._btn_remove, "video_timeline")
        mgr.register(self._clip_list, "video_timeline")
        mgr.register(self._trim_start_slider, "video_trim")
        mgr.register(self._trim_end_slider, "video_trim")
        mgr.register(self._clip_info_lbl, "video_trim")
        mgr.register(self._preview_lbl, "video_preview")
        mgr.register(self._scrubber, "video_preview")
        mgr.register(self._btn_rewind, "video_preview")
        mgr.register(self._btn_play, "video_preview")
        mgr.register(self._fps_slider, "video_preview")
        mgr.register(self._filter_combo, "video_filter")
        mgr.register(self._export_fmt_combo, "video_export")
        mgr.register(self._btn_export, "video_export")

    # ------------------------------------------------------------------
    # Clip management
    # ------------------------------------------------------------------

    def _next_insert_row(self) -> int:
        row = self._clip_list.currentRow()
        mode = self._insert_mode_combo.currentData() or "after"
        if mode == "end" or row < 0:
            return len(self._clips)
        if mode == "before":
            return max(0, row)
        return row + 1

    def _insert_clip(self, clip: "_ClipEntry", label: str, row: Optional[int] = None) -> int:
        insert_row = len(self._clips) if row is None else max(0, min(len(self._clips), row))
        self._clips.insert(insert_row, clip)
        item = QListWidgetItem(label)
        item.setData(_CLIP_ROLE, clip)
        self._clip_list.insertItem(insert_row, item)
        self._clip_list.setCurrentRow(insert_row)
        return insert_row + 1

    def _on_files_dropped(self, paths: list[str], insert_row: int) -> None:
        skipped = []
        next_row = max(0, min(len(self._clips), insert_row))
        for path in paths:
            ext = Path(path).suffix.lower()
            if ext in _VIDEO_EXTS:
                clip = _load_video_clip(path)
                if clip is None:
                    QMessageBox.warning(
                        self, "Load Error",
                        f"Could not open video:\n{Path(path).name}\n"
                        "Ensure imageio-ffmpeg or a system ffmpeg binary is available,\n"
                        "and check that the file is a supported, non-corrupt video."
                    )
                    continue
                label = _format_clip_label(clip, path, "🎞")
                next_row = self._insert_clip(clip, label, next_row)
            elif ext in _IMAGE_EXTS:
                clip = _load_image_as_clip(path)
                if clip is None:
                    skipped.append(Path(path).name)
                    continue
                label = _format_clip_label(clip, path, "🖼")
                next_row = self._insert_clip(clip, label, next_row)
            else:
                skipped.append(Path(path).name)
        self._update_scrubber()
        self._update_preview()
        if skipped:
            QMessageBox.information(
                self,
                "Unsupported Files Skipped",
                "These files are not supported by the Video Editor:\n"
                + "\n".join(skipped),
            )

    def _add_video(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Video Files", "",
            "Video Files (*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.m4v "
            "*.mpg *.mpeg *.3gp *.3g2 *.ts *.m2ts *.mts *.vob *.ogv *.ogg "
            "*.rm *.rmvb *.divx *.asf *.f4v *.mxf *.dv "
            "*.pmf *.pss *.str *.xa *.iso *.umd *.bin);;All Files (*)",
        )
        self._load_video_paths(paths, insert_row=self._next_insert_row())

    def _load_video_paths(self, paths: list[str], insert_row: Optional[int] = None) -> None:
        next_row = len(self._clips) if insert_row is None else max(0, min(len(self._clips), insert_row))
        for path in paths:
            clip = _load_video_clip(path)
            if clip is None:
                QMessageBox.warning(
                    self, "Load Error",
                    f"Could not open video:\n{Path(path).name}\n"
                    "Ensure imageio-ffmpeg or a system ffmpeg binary is available,\n"
                    "and check that the file is a supported, non-corrupt video."
                )
                continue
            label = _format_clip_label(clip, path, "🎞")
            next_row = self._insert_clip(clip, label, next_row)
        self._update_scrubber()
        self._update_preview()

    def _add_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Images / GIFs", "",
            "Images (*.png *.jpg *.jpeg *.jfif *.jpe *.webp *.bmp *.tiff *.tif *.gif *.dds "
            "*.tga *.ico *.ppm *.pgm *.pbm *.pnm *.pcx *.avif *.qoi *.svg *.jp2 *.j2k *.j2c "
            "*.xnb *.tim);;All Files (*)",
        )
        self._load_image_paths(paths, insert_row=self._next_insert_row())

    def _load_image_paths(self, paths: list[str], insert_row: Optional[int] = None) -> None:
        next_row = len(self._clips) if insert_row is None else max(0, min(len(self._clips), insert_row))
        for path in paths:
            clip = _load_image_as_clip(path)
            if clip is None:
                continue
            label = _format_clip_label(clip, path, "🖼")
            next_row = self._insert_clip(clip, label, next_row)
        self._update_scrubber()
        self._update_preview()

    def _remove_selected(self) -> None:
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._clips):
            return
        self._clips[row].close()
        del self._clips[row]
        self._clip_list.takeItem(row)
        self._update_scrubber()
        self._update_preview()

    def _sync_clips_from_list(self) -> None:
        """Rebuild ``self._clips`` from current list-item order."""
        self._clips = []
        for i in range(self._clip_list.count()):
            clip = self._clip_list.item(i).data(_CLIP_ROLE)
            if clip is not None:
                self._clips.append(clip)
        self._update_scrubber()
        self._update_preview()

    def _on_clip_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._clips):
            self._clip_info_lbl.setText("")
            self._trim_start_slider.blockSignals(True)
            self._trim_end_slider.blockSignals(True)
            self._trim_start_slider.setRange(0, 0)
            self._trim_end_slider.setRange(0, 0)
            self._trim_start_slider.blockSignals(False)
            self._trim_end_slider.blockSignals(False)
            return
        clip = self._clips[row]
        self._trim_start_slider.blockSignals(True)
        self._trim_end_slider.blockSignals(True)
        mx = max(0, clip.total_frames - 1)
        self._trim_start_slider.setRange(0, mx)
        self._trim_end_slider.setRange(0, mx)
        self._trim_start_slider.setValue(clip.trim_start)
        self._trim_end_slider.setValue(clip.trim_end)
        self._trim_start_lbl.setText(str(clip.trim_start))
        self._trim_end_lbl.setText(str(clip.trim_end))
        self._trim_start_slider.blockSignals(False)
        self._trim_end_slider.blockSignals(False)
        self._clip_info_lbl.setText(
            f"{clip.total_frames} total  •  {clip.active_frames} active  •  "
            f"{clip.fps:.1f} fps"
            + (f"  •  {clip.frame_size[0]}×{clip.frame_size[1]}" if clip.frame_size else "")
        )

    def _on_trim_start_changed(self, val: int) -> None:
        row = self._clip_list.currentRow()
        if 0 <= row < len(self._clips):
            clip = self._clips[row]
            clip.trim_start = min(val, clip.trim_end)
            self._trim_start_slider.blockSignals(True)
            self._trim_start_slider.setValue(clip.trim_start)
            self._trim_start_slider.blockSignals(False)
            self._trim_start_lbl.setText(str(clip.trim_start))
            self._clip_info_lbl.setText(
                f"{clip.total_frames} total  •  {clip.active_frames} active  •  "
                f"{clip.fps:.1f} fps"
                + (f"  •  {clip.frame_size[0]}×{clip.frame_size[1]}" if clip.frame_size else "")
            )
            self._update_scrubber()
        else:
            self._trim_start_lbl.setText(str(val))

    def _on_trim_end_changed(self, val: int) -> None:
        row = self._clip_list.currentRow()
        if 0 <= row < len(self._clips):
            clip = self._clips[row]
            clip.trim_end = max(val, clip.trim_start)
            self._trim_end_slider.blockSignals(True)
            self._trim_end_slider.setValue(clip.trim_end)
            self._trim_end_slider.blockSignals(False)
            self._trim_end_lbl.setText(str(clip.trim_end))
            self._clip_info_lbl.setText(
                f"{clip.total_frames} total  •  {clip.active_frames} active  •  "
                f"{clip.fps:.1f} fps"
                + (f"  •  {clip.frame_size[0]}×{clip.frame_size[1]}" if clip.frame_size else "")
            )
            self._update_scrubber()
        else:
            self._trim_end_lbl.setText(str(val))

    # ------------------------------------------------------------------
    # Preview / transport
    # ------------------------------------------------------------------

    def _total_preview_frames(self) -> int:
        return sum(c.active_frames for c in self._clips)

    def _update_scrubber(self) -> None:
        total_frames = self._total_preview_frames()
        total = max(0, total_frames - 1)
        current = min(self._scrubber.value(), total)
        self._scrubber.blockSignals(True)
        self._scrubber.setRange(0, total)
        self._scrubber.setValue(current)
        self._scrubber.blockSignals(False)
        self._pos_lbl.setText(f"{(current + 1) if total_frames else 0} / {total_frames}")
        self._update_export_summary()

    def _global_frame_to_clip(self, global_idx: int):
        idx = global_idx
        for ci, clip in enumerate(self._clips):
            n = clip.active_frames
            if idx < n:
                return ci, idx
            idx -= n
        return max(0, len(self._clips) - 1), 0

    def _update_preview(self) -> None:
        total = self._total_preview_frames()
        if total == 0 or not self._clips:
            self._preview_lbl.setText("(no clips)")
            self._pos_lbl.setText("0 / 0")
            return
        g = max(0, min(self._scrubber.value(), total - 1))
        ci, fi = self._global_frame_to_clip(g)
        source = None
        adjusted = None
        filtered = None
        try:
            source = self._clips[ci].get_frame(fi)
            adjusted = _apply_adjustments(
                source,
                brightness=self._brightness_slider.value() / 100.0,
                contrast=self._contrast_slider.value() / 100.0,
                black_point=self._black_slider.value(),
                white_point=self._white_slider.value(),
                saturation=self._saturation_slider.value() / 100.0,
                sharpness=self._sharpness_slider.value() / 100.0,
            )
            filter_key = self._filter_combo.currentData() or "none"
            filtered = _apply_filter(adjusted, filter_key)
            pix = _pil_to_pixmap(filtered).scaled(
                _PREVIEW_MAX_W, _PREVIEW_MAX_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._preview_lbl.setPixmap(pix)
        except Exception as exc:
            self._preview_lbl.setText(f"(preview error: {exc})")
        finally:
            if filtered is not None and filtered is not adjusted:
                filtered.close()
            if adjusted is not None and adjusted is not source:
                adjusted.close()
            if source is not None:
                source.close()

        self._pos_lbl.setText(f"{g + 1} / {total}")

    def _refresh_preview_adjustments(self) -> None:
        self._update_preview()

    def _on_scrub(self, _value: int) -> None:
        self._update_preview()

    def _toggle_play(self) -> None:
        self._btn_play.setChecked(not self._btn_play.isChecked())

    def _on_play_toggled(self, playing: bool) -> None:
        self._is_playing = playing
        if playing:
            if self._total_preview_frames() == 0:
                self._btn_play.setChecked(False)
                return
            fps = max(0.1, float(self._fps_slider.value()))
            self._preview_timer.start(int(1000 / fps))
            self._btn_play.setText("⏸  Pause")
        else:
            self._preview_timer.stop()
            self._btn_play.setText("▶  Play")

    def _advance_preview(self) -> None:
        total = self._total_preview_frames()
        if total == 0:
            return
        nxt = (self._scrubber.value() + 1) % total
        self._scrubber.setValue(nxt)
        self._update_preview()

    def _rewind(self) -> None:
        self._scrubber.setValue(0)
        self._update_preview()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export(self) -> None:
        clip_snapshot = [
            (clip.get_frame, clip.active_frames, clip.frame_size)
            for clip in self._clips
            if clip.active_frames > 0
        ]
        total = sum(active_frames for _, active_frames, _ in clip_snapshot)
        if total == 0:
            QMessageBox.information(self, "No Clips", "Add at least one clip or image first.")
            return

        def _global_frame_to_snapshot(global_idx: int) -> tuple[int, int]:
            idx = global_idx
            for clip_idx, (_, active_frames, _frame_size) in enumerate(clip_snapshot):
                if idx < active_frames:
                    return clip_idx, idx
                idx -= active_frames
            return max(0, len(clip_snapshot) - 1), 0

        fmt = self._export_fmt_combo.currentData() or "gif"
        if fmt == "gif":
            out_path, _ = QFileDialog.getSaveFileName(
                self, "Export as GIF", "output.gif",
                "GIF Files (*.gif);;All Files (*)",
            )
        else:
            out_path, _ = QFileDialog.getSaveFileName(
                self, "Export as MP4", "output.mp4",
                "MP4 Files (*.mp4);;All Files (*)",
            )
        if not out_path:
            return
        target_suffix = ".gif" if fmt == "gif" else ".mp4"
        current_suffix = Path(out_path).suffix.lower()
        if current_suffix != target_suffix:
            known_media_suffixes = _VIDEO_EXTS | _IMAGE_EXTS | {".gif", ".mp4"}
            if current_suffix in known_media_suffixes:
                out_path = str(Path(out_path).with_suffix(target_suffix))
            else:
                out_path = f"{out_path}{target_suffix}"

        fps = max(0.1, float(self._fps_slider.value()))
        filter_key = self._filter_combo.currentData() or "none"
        canvas_size = self._timeline_canvas_size(fmt)
        if canvas_size is None:
            QMessageBox.warning(self, "Export Error", "Could not determine an output size.")
            return
        if fmt == "mp4" and not self._mp4_export_available:
            QMessageBox.warning(
                self,
                "MP4 Export Unavailable",
                "MP4 export requires imageio, imageio-ffmpeg, and a working ffmpeg executable. Animated GIF export is still available.",
            )
            return

        progress = QProgressDialog("Rendering frames…", "Cancel", 0, total, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        progress.setLabelText("Rendering and saving output…")
        progress.show()
        QApplication.processEvents()
        brightness = self._brightness_slider.value() / 100.0
        contrast = self._contrast_slider.value() / 100.0
        black_point = self._black_slider.value()
        white_point = self._white_slider.value()
        saturation = self._saturation_slider.value() / 100.0
        sharpness = self._sharpness_slider.value() / 100.0
        writer = None
        gif_frames = []
        canceled = False
        wrote_frames = False
        np = None
        try:
            if fmt == "gif":
                pass
            else:
                import imageio
                import numpy as np
                writer = imageio.get_writer(
                    out_path,
                    fps=fps,
                    codec="libx264",
                )
            for i in range(total):
                progress.setValue(i)
                QApplication.processEvents()
                if progress.wasCanceled():
                    canceled = True
                    break
                ci, fi = _global_frame_to_snapshot(i)
                get_frame_fn, _active_frames, _frame_size = clip_snapshot[ci]
                source_pil = get_frame_fn(fi)
                adjusted = source_pil
                filtered = source_pil
                framed = None
                rgb = None
                try:
                    adjusted = _apply_adjustments(
                        source_pil,
                        brightness=brightness,
                        contrast=contrast,
                        black_point=black_point,
                        white_point=white_point,
                        saturation=saturation,
                        sharpness=sharpness,
                    )
                    filtered = _apply_filter(adjusted, filter_key)
                    framed = _fit_frame_to_canvas(filtered, canvas_size, fmt)
                    if fmt == "gif":
                        gif_frames.append(framed.copy())
                    else:
                        rgb = framed if framed.mode == "RGB" else framed.convert("RGB")
                        try:
                            writer.append_data(np.array(rgb))
                        finally:
                            if rgb is not None and rgb is not framed:
                                rgb.close()
                    wrote_frames = True
                finally:
                    if framed is not None and framed is not filtered:
                        try:
                            framed.close()
                        except Exception:
                            pass
                    if filtered is not adjusted:
                        try:
                            filtered.close()
                        except Exception:
                            pass
                    if adjusted is not source_pil:
                        try:
                            adjusted.close()
                        except Exception:
                            pass
                    try:
                        source_pil.close()
                    except Exception:
                        pass
            if canceled and writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
                writer = None
            if not canceled and writer is not None:
                writer.close()
                writer = None
            if fmt == "gif" and not canceled and gif_frames:
                first, *rest = gif_frames
                first.save(
                    out_path,
                    format="GIF",
                    save_all=True,
                    append_images=rest,
                    duration=max(1, int(round(1000.0 / fps))),
                    loop=0,
                    disposal=2,
                )
            progress.setValue(total)
        except Exception as exc:
            try:
                Path(out_path).unlink(missing_ok=True)
            except Exception:
                pass
            progress.close()
            QMessageBox.critical(self, "Export Error", f"Could not save output:\n{exc}")
            return
        finally:
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
            for frame in gif_frames:
                try:
                    frame.close()
                except Exception:
                    pass

        if progress.wasCanceled() or not wrote_frames:
            try:
                Path(out_path).unlink(missing_ok=True)
            except Exception:
                pass
            progress.close()
            return

        progress.close()
        QMessageBox.information(self, "Export Complete", f"Saved to:\n{out_path}")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _reset_adjustments(self) -> None:
        for slider, val in [
            (self._brightness_slider, 100),
            (self._contrast_slider, 100),
            (self._saturation_slider, 100),
            (self._sharpness_slider, 100),
            (self._black_slider, 0),
            (self._white_slider, 255),
        ]:
            slider.blockSignals(True)
            slider.setValue(val)
            slider.blockSignals(False)
        self._filter_combo.blockSignals(True)
        self._filter_combo.setCurrentIndex(0)
        self._filter_combo.blockSignals(False)
        self._update_preview()

    def _clip_canvas_size(self, clip: "_ClipEntry") -> Optional[tuple[int, int]]:
        if clip.active_frames <= 0:
            return None
        if clip.frame_size and clip.frame_size[0] > 0 and clip.frame_size[1] > 0:
            return clip.frame_size
        probe = None
        try:
            probe = clip.get_frame(0)
            clip.frame_size = probe.size
            return clip.frame_size
        except Exception:
            return None
        finally:
            if probe is not None:
                try:
                    probe.close()
                except Exception:
                    pass

    def _timeline_canvas_size(self, fmt: Optional[str] = None) -> Optional[tuple[int, int]]:
        active = [clip for clip in self._clips if clip.active_frames > 0]
        if not active:
            return None
        sizes = [self._clip_canvas_size(clip) for clip in active]
        sizes = [size for size in sizes if size is not None]
        if not sizes:
            return None
        width = max(size[0] for size in sizes)
        height = max(size[1] for size in sizes)
        export_fmt = fmt or (self._export_fmt_combo.currentData() or "gif")
        return _coerce_export_size((width, height), export_fmt)

    def _update_export_summary(self) -> None:
        canvas_size = self._timeline_canvas_size()
        if canvas_size is None:
            self._export_size_lbl.setText("Canvas: auto once clips are added")
            self._export_pad_lbl.setText("Mixed-size clips are resized to fit and centred automatically.")
            return
        natural_sizes = [self._clip_canvas_size(clip) for clip in self._clips if clip.active_frames > 0]
        natural_sizes = [size for size in natural_sizes if size is not None]
        natural_width = max(size[0] for size in natural_sizes)
        natural_height = max(size[1] for size in natural_sizes)
        if canvas_size == (natural_width, natural_height):
            detail = "Canvas: auto from the largest clip"
        else:
            detail = "Canvas: auto from the largest clip (rounded for MP4 compatibility)"
        self._export_size_lbl.setText(f"{detail}: {canvas_size[0]} × {canvas_size[1]}")
        self._export_pad_lbl.setText(
            "Mixed-size clips are scaled to fit this canvas and letterboxed automatically."
        )

    def closeEvent(self, event) -> None:
        self._preview_timer.stop()
        for clip in self._clips:
            clip.close()
        super().closeEvent(event)
