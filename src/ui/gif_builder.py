"""
GIF Builder Dialog.

Lets the user build an animated GIF from scratch by selecting any mix of
image files (PNG, JPEG, WEBP, BMP, TIFF, GIF frame, etc.), ordering them,
setting per-frame or global delay, and previewing the animation live before
exporting.

Opening the dialog:
  • Selecting "GIF" in the Converter output combo and clicking Process
  • Right-clicking anywhere on the main window → "Open GIF Builder"

UX highlights (Round-90):
  • Drag frames inside the grid to reorder them – no Up/Down buttons needed.
    External file drops also accepted.
  • Frame order stays in sync via item UserRole data + rowsMoved signal.
  • Global delay + FPS controlled by a smooth drag-slider; per-frame delay
    also uses a slider (no arrow-button spinboxes).
  • Scrubber slider lets you jump to any frame without playing.
  • Live preview updates immediately when sliders are moved.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (
    Qt, QTimer, QSize, pyqtSignal,
)
from PyQt6.QtGui import (
    QImage, QPixmap, QDragEnterEvent, QDropEvent, QDragMoveEvent,
    QIcon, QKeySequence, QShortcut,
)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFileDialog, QSlider,
    QCheckBox, QGroupBox, QGridLayout, QMessageBox,
    QProgressDialog, QSplitter, QWidget, QApplication,
    QFrame, QPlainTextEdit, QSpinBox, QAbstractSpinBox,
)
from .video_tool import (
    _VIDEO_EXTS,
    _classify_video_import_failure,
    _extract_visual_still_frame,
    _get_ffprobe_exe,
    _has_ffmpeg,
    _has_imageio,
    _has_imageio_ffmpeg,
    _is_probably_video_source,
    _load_video_frames,
    _probe_media_details,
    _video_failure_guidance,
    _video_io_diagnostics,
    _video_load_failure_hint,
    _visual_still_fallback_note,
)

# Supported image input extensions (what PIL can open directly)
_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".jfif", ".jpe", ".webp", ".bmp", ".tiff", ".tif",
    ".gif", ".ico", ".ppm", ".pcx", ".tga", ".avif",
}
_SUPPORTED_EXTS = _IMAGE_EXTS | _VIDEO_EXTS

_THUMB_W = 120
_THUMB_H = 100

# UserRole key for storing _FrameEntry in QListWidgetItem
_ENTRY_ROLE = Qt.ItemDataRole.UserRole


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


def _load_pillow_rgba(path: str) -> list["PIL.Image.Image"]:
    """Return a list of composited RGBA frames from *path*.

    For animated GIFs every frame is composited onto an accumulating canvas
    (GIF delta encoding) before being returned.  All other formats return a
    single RGBA frame.
    """
    from PIL import Image
    img = Image.open(path)
    frames: list[Image.Image] = []
    try:
        n = getattr(img, "n_frames", 1)
        if n <= 1:
            frames.append(img.convert("RGBA"))
        else:
            canvas = Image.new("RGBA", img.size, (0, 0, 0, 0))
            for i in range(n):
                img.seek(i)
                curr = img.convert("RGBA")
                previous_canvas = canvas.copy()
                composite = canvas.copy()
                rect = _gif_frame_rect(img, curr)
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
                disposal = getattr(img, "disposal_method", img.info.get("disposal", 0))
                canvas.close()
                if disposal == 2:
                    canvas = Image.new("RGBA", img.size, (0, 0, 0, 0))
                    composite.close()
                    previous_canvas.close()
                elif disposal == 3:
                    canvas = previous_canvas
                    composite.close()
                else:
                    canvas = composite
                    previous_canvas.close()
            canvas.close()
    except EOFError:
        pass
    finally:
        img.close()
    return frames


def _pil_to_pixmap(pil_img) -> QPixmap:
    from PIL import Image  # noqa: F401 – needed for convert
    rgba = pil_img.convert("RGBA")
    try:
        data = rgba.tobytes("raw", "RGBA")
        qi = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qi)
    finally:
        rgba.close()


def _gif_builder_capability_summary() -> str:
    if _has_ffmpeg() and _has_imageio() and _has_imageio_ffmpeg():
        if _get_ffprobe_exe():
            odd_container_text = " detailed odd-container probing is enabled."
        else:
            odd_container_text = " odd-container probing detail stays limited until ffprobe is available."
        return (
            "Ready: images, animated GIFs, and video-source imports are available."
            + odd_container_text
            + " Video clips expand into GIF frames automatically; audio stays ignored for GIF import/export."
        )
    return (
        "Ready with limits: images and animated GIFs work, but video-source imports need imageio, imageio-ffmpeg, and ffmpeg.\n"
        + _video_io_diagnostics()
    )


def _gif_builder_capability_has_limits() -> bool:
    return not (_has_ffmpeg() and _has_imageio() and _has_imageio_ffmpeg() and _get_ffprobe_exe())


def _gif_builder_capability_details() -> str:
    deps_ok = _has_ffmpeg() and _has_imageio() and _has_imageio_ffmpeg()
    ffprobe_exe = _get_ffprobe_exe()
    lines = [_gif_builder_capability_summary(), "", _video_io_diagnostics()]
    if deps_ok:
        lines.extend([
            "",
            "Current behavior:",
            "• Image and animated-GIF imports are fully available.",
            "• Video imports expand decoded visual frames into GIF frames automatically; audio is ignored on import/export.",
            "• Odd-container probing/recovery is best-effort when ffmpeg can expose a playable stream or salvageable still frame.",
            "• Automatic preferred-stream selection is used when ffprobe is available, but a manual multi-stream picker is not available yet.",
        ])
        if ffprobe_exe:
            lines.append(f"• ffprobe detail/probing ready: {ffprobe_exe}")
        else:
            lines.append("• ffprobe detail/probing limited: grouped failure diagnostics stay less specific.")
    else:
        lines.extend([
            "",
            "Limited mode details:",
            "• Image and animated-GIF workflows remain available.",
            "• Video-source imports and odd-container probing need imageio, imageio-ffmpeg, and ffmpeg.",
            "• Manual multi-stream selection is not available yet.",
        ])
    return "\n".join(lines)


class _FrameEntry:
    """A single frame in the GIF builder's frame list."""

    def __init__(self, source_path: str, frame_index: int,
                 pil_image: "PIL.Image.Image", delay_ms: Optional[int] = None,
                 source_delay_ms: Optional[int] = None):
        self.source_path = source_path
        self.frame_index = frame_index  # 0-based index within source (>0 for animated GIF)
        self._pil = pil_image           # RGBA PIL image; ownership transferred here
        self.delay_ms: Optional[int] = delay_ms  # None = use global delay
        self.source_delay_ms: Optional[int] = source_delay_ms if source_delay_ms is not None else delay_ms
        self._thumb_cache: dict[tuple[int, int], QPixmap] = {}

    def thumbnail(self, w: int, h: int) -> QPixmap:
        from PIL import Image
        key = (w, h)
        cached = self._thumb_cache.get(key)
        if cached is not None:
            return cached
        tmp = self._pil.copy()
        tmp.thumbnail((w, h), Image.LANCZOS)
        pix = _pil_to_pixmap(tmp)
        self._thumb_cache = {key: pix}
        return pix

    def close(self) -> None:
        self._thumb_cache.clear()
        try:
            self._pil.close()
        except Exception:
            pass


def _frame_source_path(entry) -> str:
    return str(getattr(entry, "source_path", getattr(entry, "path", "")) or "")


def _frame_source_kind(path: str, source_frames: int = 1) -> str:
    ext = Path(path).suffix.lower()
    if ext in _VIDEO_EXTS:
        return "video"
    if ext == ".gif" and source_frames > 1:
        return "animated gif"
    return "image"


def _classify_import_failure(name: str, detail: str) -> str:
    ext = Path(name).suffix.lower()
    lower = detail.lower()
    if (
        ext in _VIDEO_EXTS
        or "ffprobe" in lower
        or "playable video stream" in lower
        or "cover-art" in lower
        or "non-corrupt video" in lower
    ):
        return _classify_video_import_failure(name, detail)
    if "truncated" in lower or "corrupt" in lower or "cannot identify image file" in lower:
        return "image decode"
    return "image import"


def _import_failure_guidance(category: str) -> str:
    if category == "image decode":
        return "The image or animated GIF could not be decoded cleanly; the source may be corrupt, truncated, or unsupported by the current Pillow build."
    if category == "image import":
        return "The source could not be imported as a supported image, animated GIF, or video-expanded frame set."
    return _video_failure_guidance(category)


def _summarize_count_buckets(counts: dict[str, int], limit: int = 3) -> str:
    if not counts:
        return ""
    parts = [f"{label} ×{count}" for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
    if len(parts) <= limit:
        return ", ".join(parts)
    remaining = len(parts) - limit
    return ", ".join(parts[:limit]) + f", +{remaining} more"


def _unique_summary_parts(parts: list[str], limit: int = 4) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
        if len(ordered) >= limit:
            break
    return ordered


def _gif_import_next_step_text(
    *,
    loaded_sources: int,
    recovered: list[tuple[str, str]],
    failures: list[tuple[str, str]],
    skipped: list[str],
) -> str:
    steps: list[str] = []
    grouped: dict[str, int] = {}
    for name, detail in failures:
        category = _classify_import_failure(name, detail)
        grouped[category] = grouped.get(category, 0) + 1
    video_runtime_ready = _has_ffmpeg() and _has_imageio() and _has_imageio_ffmpeg()
    if not video_runtime_ready:
        steps.append("images and animated GIFs still work here; video-source expansion needs ffmpeg/imageio support")
    if "audio-only container" in grouped:
        steps.append("audio-only files cannot be added because GIF Builder needs visual frames")
    if any(
        category in grouped
        for category in (
            "transport stream timing",
            "program stream layout",
            "quicktime metadata",
            "legacy index container",
            "matroska/webm program",
            "multi-stream container",
            "still-image video",
            "video codec",
            "video decode",
            "container codec mismatch",
            "partial / malformed video",
        )
    ):
        steps.append("retry the sample with Show details open so you can keep the grouped guidance with the asset")
    if recovered:
        steps.append("recovered sources already contributed frames, so you can keep arranging/exporting while reviewing failures")
    if skipped:
        steps.append("unsupported files were skipped; Show details lists exactly which ones")
    if failures:
        steps.append("use Show details or Copy details for grouped import guidance")
    elif loaded_sources > 0:
        steps.append("reorder frames, adjust timing, preview, or export when ready")
    ready_steps = _unique_summary_parts(steps)
    if not ready_steps:
        return "Next step: add media to build a frame timeline, then preview or export."
    return "Next step: " + "  •  ".join(ready_steps)


def _scaled_size_for_export(size: tuple[int, int], max_w: int, max_h: int) -> tuple[int, int]:
    width, height = size
    if width <= 0 or height <= 0:
        return 0, 0
    scale_w = (max_w / width) if max_w > 0 else 1.0
    scale_h = (max_h / height) if max_h > 0 else 1.0
    scale = min(scale_w, scale_h, 1.0)
    if scale >= 1.0:
        return width, height
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


class _FrameListWidget(QListWidget):
    """Icon-grid list widget with drag-to-reorder AND external file drop support.

    Each ``QListWidgetItem`` stores its ``_FrameEntry`` in ``_ENTRY_ROLE`` so
    the order can be re-synced after any internal drag.  The ``order_changed``
    signal fires after every internal reorder.
    """

    files_dropped = pyqtSignal(list)   # list[str]
    order_changed = pyqtSignal()       # emitted after internal drag-reorder

    def __init__(self, parent=None):
        super().__init__(parent)
        # InternalMove preserves QListWidgetItem objects (including their
        # Python UserRole data) during drag-reorder.  DragDrop mode would
        # serialise items through Qt's MIME layer, losing any Python object
        # stored in a custom role.  External file drops are still handled by
        # the overridden dragEnterEvent/dropEvent below.
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setIconSize(QSize(_THUMB_W, _THUMB_H))
        self.setSpacing(6)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setWrapping(True)
        self.setWordWrap(True)
        # Fire order_changed whenever rows move (internal drag-reorder)
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
            paths = []
            for url in event.mimeData().urls():
                p = url.toLocalFile()
                if p:
                    paths.append(p)
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


def _make_hslider(lo: int, hi: int, val: int,
                  tick_interval: int = 0) -> QSlider:
    """Return a horizontal QSlider pre-configured with the given range."""
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    s.setTracking(True)
    if tick_interval:
        s.setTickPosition(QSlider.TickPosition.TicksBelow)
        s.setTickInterval(tick_interval)
    return s


class GifBuilderDialog(QDialog):
    """Full-featured animated GIF builder.

    Provides:
    • Add images from any supported format (PNG, JPEG, WEBP, GIF, etc.)
    • Drag-and-drop for file import AND grid reordering (no Up/Down buttons)
    • Global frame delay set by a smooth slider – live preview updates
    • Per-frame delay override also via slider
    • Scrubber to jump to any frame
    • Loop count and optional resize on export
    • Export (Process) to a user-chosen GIF file

    :param initial_files: Optional list of file paths to pre-populate.
    :param parent:        Optional parent widget.
    """

    exported = pyqtSignal(str)  # emitted with output path on successful export
    status_notice = pyqtSignal(str, int)
    queue_status_changed = pyqtSignal(str)
    SHORTCUT_DEFS = (
        ("gif_remove_selected", "Delete", "Remove selected frame", "GIF Builder"),
        ("gif_export", "Ctrl+S", "Export GIF", "GIF Builder"),
        ("gif_toggle_play", "Space", "Play or pause preview", "GIF Builder"),
    )

    def __init__(self, initial_files: Optional[list[str]] = None, parent=None, tooltip_mgr=None):
        super().__init__(parent)
        self.setWindowTitle("🎞 GIF Builder")
        self.setMinimumSize(860, 620)
        self.setModal(False)
        self._tooltip_mgr = tooltip_mgr
        self._import_detail_expanded = False
        self._frames: list[_FrameEntry] = []
        self._preview_idx: int = 0
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._advance_preview)
        self._build_ui()
        self.queue_status_changed.connect(self._refresh_session_status)
        mgr = self._resolve_tooltip_mgr()
        if mgr is not None:
            self.register_tooltips(mgr)
        if initial_files:
            self._add_paths(initial_files)
        self._setup_shortcuts()
        self._refresh_session_status()

    def add_media_paths(self, paths: list[str]) -> None:
        """Append media files to the current builder session."""
        clean_paths = [str(path) for path in (paths or []) if str(path or "").strip()]
        if clean_paths:
            self._add_paths(clean_paths)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Title bar row
        title_row = QHBoxLayout()
        title = QLabel("🎞  GIF Builder")
        title.setObjectName("subheader")
        title_row.addWidget(title)
        title_row.addStretch()
        self._frame_count_lbl = QLabel("0 frames")
        self._frame_count_lbl.setObjectName("subheader")
        title_row.addWidget(self._frame_count_lbl)
        root.addLayout(title_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ── Left: frame grid ─────────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        # Toolbar
        tb = QHBoxLayout()
        self._btn_add = QPushButton("➕  Add Media")
        self._btn_add.setToolTip(
            "Add one or more images or video files to the GIF.\n"
            "Supports PNG, JPEG, WEBP, BMP, TIFF, GIF (all frames), MP4, WEBM, AVI, MOV, and more.\n"
            "You can also drag supported media files directly onto the grid below."
        )
        self._btn_add.setMinimumHeight(32)
        self._btn_add.clicked.connect(self._on_add_clicked)
        tb.addWidget(self._btn_add, 2)

        self._btn_remove = QPushButton("🗑  Remove")
        self._btn_remove.setToolTip("Remove selected frame(s).  Shortcut: Delete")
        self._btn_remove.clicked.connect(self._remove_selected)
        tb.addWidget(self._btn_remove, 1)

        self._btn_clear = QPushButton("✖  Clear All")
        self._btn_clear.setToolTip("Remove all frames.")
        self._btn_clear.clicked.connect(self._clear_all)
        tb.addWidget(self._btn_clear, 1)
        left_layout.addLayout(tb)

        # Hint label
        hint = QLabel("💡 Drag frames to reorder  •  Drop image or video files to add")
        hint.setStyleSheet("color: gray; font-style: italic; font-size: 11px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(hint)

        self._import_status_lbl = QLabel(
            "Ready: add images, GIFs, videos, or probe-detected odd containers. Import notes, grouped failures, and skipped-file details will appear here."
        )
        self._import_status_lbl.setWordWrap(True)
        self._import_status_lbl.setStyleSheet("color: gray; font-size: 11px;")
        self._import_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self._import_status_lbl)
        self._import_detail_box = QPlainTextEdit()
        self._import_detail_box.setReadOnly(True)
        self._import_detail_box.setPlaceholderText("Detailed import diagnostics will appear here.")
        self._import_detail_box.setMinimumHeight(70)
        self._import_detail_box.setMaximumHeight(110)
        self._import_detail_box.setVisible(False)
        left_layout.addWidget(self._import_detail_box)
        import_detail_actions = QHBoxLayout()
        import_detail_actions.addStretch()
        self._import_detail_toggle_btn = QPushButton("Show details")
        self._import_detail_toggle_btn.setToolTip("Show or hide the full grouped import diagnostics without opening a popup.")
        self._import_detail_toggle_btn.clicked.connect(self._toggle_import_details)
        self._import_detail_toggle_btn.setVisible(False)
        import_detail_actions.addWidget(self._import_detail_toggle_btn)
        self._import_copy_btn = QPushButton("Copy details")
        self._import_copy_btn.setToolTip("Copy the current import summary and diagnostics to the clipboard.")
        self._import_copy_btn.clicked.connect(self._copy_import_details)
        self._import_copy_btn.setVisible(False)
        import_detail_actions.addWidget(self._import_copy_btn)
        left_layout.addLayout(import_detail_actions)
        self._next_step_lbl = QLabel("Next step: add media to build a frame timeline, then preview or export.")
        self._next_step_lbl.setWordWrap(True)
        self._next_step_lbl.setStyleSheet("color: #888; font-size: 11px;")
        self._next_step_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self._next_step_lbl)

        self._capability_lbl = QLabel(_gif_builder_capability_summary())
        self._capability_lbl.setWordWrap(True)
        self._capability_lbl.setStyleSheet(
            "color: #b26a00; font-size: 11px;"
            if _gif_builder_capability_has_limits()
            else "color: #2e7d32; font-size: 11px;"
        )
        self._capability_lbl.setToolTip(_gif_builder_capability_details())
        self._capability_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self._capability_lbl)
        self._session_status_lbl = QLabel("")
        self._session_status_lbl.setWordWrap(True)
        self._session_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._session_status_lbl.setStyleSheet("color: #888; font-size: 11px;")
        left_layout.addWidget(self._session_status_lbl)

        # Frame grid
        self._frame_list = _FrameListWidget()
        self._frame_list.files_dropped.connect(self._add_paths)
        self._frame_list.order_changed.connect(self._sync_frames_from_list)
        self._frame_list.currentRowChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self._frame_list, 1)

        # Per-frame delay override  (slider-based)
        pf_box = QGroupBox("Per-Frame Delay Override")
        pf_layout = QVBoxLayout(pf_box)
        pf_top = QHBoxLayout()
        self._pf_check = QCheckBox("Override delay for selected frame")
        self._pf_check.toggled.connect(self._on_pf_check)
        pf_top.addWidget(self._pf_check)
        self._pf_val_lbl = QLabel("100 ms")
        self._pf_val_lbl.setFixedWidth(60)
        self._pf_val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        pf_top.addWidget(self._pf_val_lbl)
        pf_layout.addLayout(pf_top)
        self._pf_slider = _make_hslider(10, 3000, 100)
        self._pf_slider.setEnabled(False)
        self._pf_slider.setToolTip("Per-frame delay in milliseconds.  Drag left = faster.")
        self._pf_slider.valueChanged.connect(self._on_pf_slider_changed)
        pf_layout.addWidget(self._pf_slider)
        left_layout.addWidget(pf_box)

        splitter.addWidget(left)

        # ── Right: settings + preview ─────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        # Global settings
        grp_settings = QGroupBox("GIF Settings")
        gl = QGridLayout(grp_settings)
        gl.setHorizontalSpacing(8)
        gl.setVerticalSpacing(8)

        # Delay slider  (10–3000 ms)
        gl.addWidget(QLabel("Frame speed:"), 0, 0)
        delay_row = QHBoxLayout()
        self._delay_slider = _make_hslider(10, 3000, 100)
        self._delay_slider.setToolTip(
            "How long each frame is shown (milliseconds).\n"
            "Drag left for faster animation, right for slower.\n"
            "100 ms ≈ 10 fps  |  50 ms ≈ 20 fps  |  33 ms ≈ 30 fps"
        )
        self._delay_val_lbl = QLabel("100 ms")
        self._delay_val_lbl.setFixedWidth(60)
        self._delay_val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._delay_slider.valueChanged.connect(self._on_delay_changed)
        delay_row.addWidget(self._delay_slider, 1)
        delay_row.addWidget(self._delay_val_lbl)
        gl.addLayout(delay_row, 0, 1)

        # Loop count
        gl.addWidget(QLabel("Loop count:"), 1, 0)
        loop_row = QHBoxLayout()
        self._loop_slider = _make_hslider(0, 20, 0)
        self._loop_slider.setToolTip("0 = loop forever.  1 = play once.  N = repeat N times.")
        self._loop_val_lbl = QLabel("∞")
        self._loop_val_lbl.setFixedWidth(40)
        self._loop_val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._loop_slider.valueChanged.connect(
            lambda v: self._loop_val_lbl.setText("∞" if v == 0 else str(v))
        )
        loop_row.addWidget(self._loop_slider, 1)
        loop_row.addWidget(self._loop_val_lbl)
        gl.addLayout(loop_row, 1, 1)

        # Max width
        gl.addWidget(QLabel("Max width:"), 2, 0)
        w_row = QHBoxLayout()
        self._width_slider = _make_hslider(0, 3840, 0)
        self._width_slider.setToolTip("Resize frames to this width (preserves aspect ratio).  0 = no resize.")
        self._width_val_lbl = QLabel("original")
        self._width_val_lbl.setFixedWidth(65)
        self._width_val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._width_slider.valueChanged.connect(
            lambda v: self._width_val_lbl.setText("original" if v == 0 else f"{v} px")
        )
        self._width_slider.valueChanged.connect(lambda _v: self._update_frame_diagnostics())
        w_row.addWidget(self._width_slider, 1)
        w_row.addWidget(self._width_val_lbl)
        gl.addLayout(w_row, 2, 1)

        # Max height
        gl.addWidget(QLabel("Max height:"), 3, 0)
        h_row = QHBoxLayout()
        self._height_slider = _make_hslider(0, 2160, 0)
        self._height_slider.setToolTip("Resize frames to this height (preserves aspect ratio).  0 = no resize.")
        self._height_val_lbl = QLabel("original")
        self._height_val_lbl.setFixedWidth(65)
        self._height_val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._height_slider.valueChanged.connect(
            lambda v: self._height_val_lbl.setText("original" if v == 0 else f"{v} px")
        )
        self._height_slider.valueChanged.connect(lambda _v: self._update_frame_diagnostics())
        h_row.addWidget(self._height_slider, 1)
        h_row.addWidget(self._height_val_lbl)
        gl.addLayout(h_row, 3, 1)

        self._optimize_check = QCheckBox("Optimize palette (smaller file, slightly slower export)")
        self._optimize_check.setChecked(True)
        gl.addWidget(self._optimize_check, 4, 0, 1, 2)

        right_layout.addWidget(grp_settings)

        # Live preview
        grp_preview = QGroupBox("Live Preview")
        pv_layout = QVBoxLayout(grp_preview)
        pv_layout.setSpacing(6)

        self._preview_lbl = QLabel()
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_lbl.setMinimumSize(280, 220)
        self._preview_lbl.setFrameShape(QFrame.Shape.StyledPanel)
        self._preview_lbl.setText("(no frames yet)")
        pv_layout.addWidget(self._preview_lbl, 1)

        # Scrubber
        self._scrubber = _make_hslider(0, 0, 0)
        self._scrubber.setToolTip("Drag to jump to any frame.")
        self._scrubber.valueChanged.connect(self._on_scrub)
        pv_layout.addWidget(self._scrubber)

        # Transport controls
        pv_ctrl = QHBoxLayout()
        self._btn_rewind = QPushButton("⏮  Rewind")
        self._btn_rewind.setMinimumWidth(96)
        self._btn_rewind.setMinimumHeight(26)
        self._btn_rewind.setToolTip("Rewind to first frame (jump to frame 1)")
        self._btn_rewind.clicked.connect(self._rewind)
        pv_ctrl.addWidget(self._btn_rewind)

        self._btn_play = QPushButton("▶  Play")
        self._btn_play.setCheckable(True)
        self._btn_play.setToolTip("Start / stop the animated preview.  Space bar also works.")
        self._btn_play.toggled.connect(self._on_play_toggled)
        pv_ctrl.addWidget(self._btn_play)

        self._preview_frame_lbl = QLabel("0 / 0")
        self._preview_frame_lbl.setObjectName("subheader")
        pv_ctrl.addWidget(self._preview_frame_lbl)
        pv_ctrl.addStretch()
        pv_layout.addLayout(pv_ctrl)

        self._frame_diag_lbl = QLabel("Frame diagnostics: add or select media to inspect frame size, source, and timing.")
        self._frame_diag_lbl.setWordWrap(True)
        self._frame_diag_lbl.setStyleSheet("color: gray; font-size: 11px;")
        pv_layout.addWidget(self._frame_diag_lbl)

        right_layout.addWidget(grp_preview, 1)

        # Export row
        export_row = QHBoxLayout()
        self._btn_export = QPushButton("💾  Export GIF…")
        self._btn_export.setToolTip("Build and save the animated GIF.  Shortcut: Ctrl+S")
        self._btn_export.setMinimumHeight(34)
        self._btn_export.clicked.connect(self._export)
        export_row.addStretch()
        export_row.addWidget(self._btn_export)
        right_layout.addLayout(export_row)

        splitter.addWidget(right)
        splitter.setSizes([520, 340])

    # ------------------------------------------------------------------
    # Frame management
    # ------------------------------------------------------------------

    def _on_add_clicked(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Media", "",
            "Media (*.png *.jpg *.jpeg *.jfif *.jpe *.webp *.bmp *.tiff *.tif *.gif "
            "*.ico *.ppm *.pcx *.tga *.avif *.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm "
            "*.m4v *.mpg *.mpeg *.3gp *.3g2 *.ts *.m2ts *.mts *.vob *.ogv *.ogg "
            "*.rm *.rmvb *.divx *.asf *.f4v *.mxf *.dv *.pmf *.pss *.str *.xa *.iso *.umd *.bin);;All Files (*)",
        )
        if paths:
            self._add_paths(paths)

    def _add_paths(self, paths: list[str]) -> None:
        """Load image/video files and append their frames to the list."""
        progress = QProgressDialog("Loading media…", "Cancel", 0, len(paths), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(500)
        loaded_sources = 0
        added_frames = 0
        failures: list[tuple[str, str]] = []
        skipped: list[str] = []
        loaded_details: list[str] = []
        source_type_counts: dict[str, int] = {}
        frame_size_counts: dict[str, int] = {}
        alpha_source_count = 0
        largest_frame: tuple[int, int] = (0, 0)
        recovered_sources: list[tuple[str, str]] = []
        for i, path in enumerate(paths):
            progress.setValue(i)
            if progress.wasCanceled():
                break
            ext = Path(path).suffix.lower()
            probe = None
            treat_as_video = ext in _VIDEO_EXTS
            if not treat_as_video and ext not in _IMAGE_EXTS:
                probe = _probe_media_details(path)
                if _is_probably_video_source(path, probe):
                    treat_as_video = True
                elif probe:
                    failures.append((Path(path).name, _video_load_failure_hint(path)))
                    continue
                else:
                    skipped.append(Path(path).name)
                    continue
            try:
                if treat_as_video:
                    pil_frames, fps = _load_video_frames(path)
                    frame_delay_ms = max(10, int(round(1000.0 / max(1.0, fps))))
                    recovery_note = ""
                else:
                    pil_frames = _load_pillow_rgba(path)
                    frame_delay_ms = None
                    recovery_note = ""
            except Exception as exc:
                detail = str(exc)
                if treat_as_video:
                    recovery_note = _visual_still_fallback_note(probe)
                    still_frame = _extract_visual_still_frame(path, probe)
                    if still_frame is not None:
                        pil_frames = [still_frame]
                        frame_delay_ms = None
                        recovered_sources.append((Path(path).name, recovery_note))
                    else:
                        hint = _video_load_failure_hint(path)
                        detail = f"{detail}\n{hint}" if detail else hint
                        failures.append((Path(path).name, detail))
                        continue
                else:
                    failures.append((Path(path).name, detail))
                    continue
            loaded_sources += 1
            added_frames += len(pil_frames)
            source_kind = _frame_source_kind(path, len(pil_frames))
            source_type_counts[source_kind] = source_type_counts.get(source_kind, 0) + 1
            frame_width = frame_height = 0
            if pil_frames:
                try:
                    frame_width, frame_height = pil_frames[0].size
                except Exception:
                    frame_width = frame_height = 0
            if frame_width * frame_height > largest_frame[0] * largest_frame[1]:
                largest_frame = (frame_width, frame_height)
            if frame_width > 0 and frame_height > 0:
                size_key = f"{frame_width}×{frame_height}"
                frame_size_counts[size_key] = frame_size_counts.get(size_key, 0) + 1
            if any(("A" in frame.getbands()) or ("transparency" in getattr(frame, "info", {})) for frame in pil_frames):
                alpha_source_count += 1
            source_note = (
                f"{Path(path).name}: {len(pil_frames)} frame{'s' if len(pil_frames) != 1 else ''}"
                f"  •  {source_kind}"
            )
            if frame_width > 0 and frame_height > 0:
                source_note += f"  •  {frame_width}×{frame_height}"
            if frame_delay_ms is not None:
                source_note += f" @ ~{frame_delay_ms} ms"
            if recovery_note:
                source_note += f"  •  {recovery_note}"
            loaded_details.append(source_note)
            for frame_idx, pil_frame in enumerate(pil_frames):
                entry = _FrameEntry(
                    path,
                    frame_idx,
                    pil_frame,
                    delay_ms=frame_delay_ms,
                    source_delay_ms=frame_delay_ms,
                )
                self._frames.append(entry)
                item = QListWidgetItem()
                item.setIcon(QIcon(entry.thumbnail(_THUMB_W, _THUMB_H)))
                label = Path(path).stem
                if len(pil_frames) > 1:
                    label += f"\n[{frame_idx + 1}]"
                item.setText(label)
                item.setToolTip(f"{path}\nFrame {frame_idx + 1} / {len(pil_frames)}")
                item.setData(_ENTRY_ROLE, entry)
                self._frame_list.addItem(item)
        progress.setValue(len(paths))
        self._update_count()
        self._update_scrubber()
        self._update_preview_frame()
        self._update_import_status(
            attempted=len(paths),
            loaded_sources=loaded_sources,
            added_frames=added_frames,
            recovered=recovered_sources,
            failures=failures,
            skipped=skipped,
            loaded_details=loaded_details,
            source_type_counts=source_type_counts,
            frame_size_counts=frame_size_counts,
            alpha_source_count=alpha_source_count,
            largest_frame=largest_frame,
        )

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
        self._bind_shortcut("gif_remove_selected", "Delete", self._remove_selected)
        self._bind_shortcut("gif_export", "Ctrl+S", self._export)
        self._bind_shortcut(
            "gif_toggle_play",
            "Space",
            lambda: self._btn_play.setChecked(not self._btn_play.isChecked()),
        )

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

    def _record_export_history(
        self,
        out_path: str,
        delay_ms: int,
        loop: int,
        optimize: bool,
        resize: tuple[int, int],
    ) -> None:
        settings = self._resolve_settings()
        if settings is None:
            return
        files = [os.path.basename(_frame_source_path(entry)) for entry in self._frames if _frame_source_path(entry)]
        source_counts: dict[str, int] = {}
        seen_sources: set[str] = set()
        for entry in self._frames:
            source_path = _frame_source_path(entry)
            if not source_path or source_path in seen_sources:
                continue
            seen_sources.add(source_path)
            source_frames = sum(1 for candidate in self._frames if _frame_source_path(candidate) == source_path)
            kind = _frame_source_kind(source_path, source_frames)
            source_counts[kind] = source_counts.get(kind, 0) + 1
        source_summary = ", ".join(f"{kind} ×{count}" for kind, count in sorted(source_counts.items()))
        alpha_frames = sum(
            1
            for entry in self._frames
            if ("A" in entry._pil.getbands()) or ("transparency" in getattr(entry._pil, "info", {}))
        )
        largest_frame = (0, 0)
        for entry in self._frames:
            try:
                width, height = entry._pil.size
            except Exception:
                continue
            if width * height > largest_frame[0] * largest_frame[1]:
                largest_frame = (width, height)
        alpha_summary = f"{alpha_frames}/{len(self._frames)}" if self._frames else "0/0"
        resize_summary = (
            f"≤{resize[0] if resize[0] > 0 else 'auto'}×{resize[1] if resize[1] > 0 else 'auto'}"
            if resize[0] > 0 or resize[1] > 0 else
            "original"
        )
        delay_summary = f"{max(1, int(delay_ms))} ms"
        approx_fps = 1000.0 / max(1, int(delay_ms))
        notes = [
            f"delay={delay_summary}",
            f"loop={'∞' if loop == 0 else loop}",
            f"optimize={'on' if optimize else 'off'}",
        ]
        if resize[0] > 0 or resize[1] > 0:
            notes.append(f"resize{resize_summary}")
        if source_summary:
            notes.append(f"sources={source_summary}")
        if alpha_frames:
            notes.append(f"{alpha_frames}/{len(self._frames)} frame(s) carried alpha before quantizing")
        entry = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "output": out_path,
            "frame_count": len(self._frames),
            "success": len(self._frames),
            "errors": 0,
            "files": files,
            "first_file": _frame_source_path(self._frames[0]) if self._frames else "",
            "sources": source_summary,
            "largest_frame": f"{largest_frame[0]}×{largest_frame[1]}" if largest_frame[0] > 0 and largest_frame[1] > 0 else "",
            "alpha_summary": alpha_summary,
            "delay": delay_summary,
            "fps": f"{approx_fps:.2f}".rstrip("0").rstrip("."),
            "loop": "∞" if loop == 0 else str(loop),
            "optimize": "on" if optimize else "off",
            "resize": resize_summary,
            "notes": "; ".join(notes),
        }
        try:
            settings.add_gif_builder_history(entry)
        except Exception:
            pass

    def register_tooltips(self, mgr) -> None:
        """Register dialog widgets with the shared TooltipManager."""
        self._tooltip_mgr = mgr
        mgr.register(self._btn_add, "gif_media_add")
        mgr.register(self._btn_remove, "gif_frame_list")
        mgr.register(self._btn_clear, "gif_frame_list")
        mgr.register(self._frame_list, "gif_frame_list")
        mgr.register(self._pf_check, "gif_frame_delay")
        mgr.register(self._pf_slider, "gif_frame_delay")
        mgr.register(self._delay_slider, "gif_frame_delay")
        mgr.register(self._loop_slider, "gif_export_settings")
        mgr.register(self._width_slider, "gif_export_settings")
        mgr.register(self._height_slider, "gif_export_settings")
        mgr.register(self._preview_lbl, "gif_preview")
        mgr.register(self._scrubber, "gif_preview")
        mgr.register(self._btn_rewind, "gif_preview")
        mgr.register(self._btn_play, "gif_preview")
        mgr.register(self._btn_export, "gif_export")

    def _sync_frames_from_list(self) -> None:
        """Rebuild ``self._frames`` from current list-widget item order."""
        self._frames = []
        for i in range(self._frame_list.count()):
            entry = self._frame_list.item(i).data(_ENTRY_ROLE)
            if entry is not None:
                self._frames.append(entry)
        self._update_preview_frame()

    def _remove_selected(self) -> None:
        rows = sorted(
            {self._frame_list.row(item) for item in self._frame_list.selectedItems()},
            reverse=True,
        )
        for row in rows:
            if 0 <= row < len(self._frames):
                self._frames[row].close()
                del self._frames[row]
            self._frame_list.takeItem(row)
        self._update_count()
        self._update_scrubber()
        self._update_preview_frame()

    def _clear_all(self) -> None:
        for entry in self._frames:
            entry.close()
        self._frames.clear()
        self._frame_list.clear()
        self._update_count()
        self._update_scrubber()
        self._update_preview_frame()

    def _update_count(self) -> None:
        n = len(self._frames)
        source_count = len({path for path in (_frame_source_path(entry) for entry in self._frames) if path})
        extra = f"  •  {source_count} source{'s' if source_count != 1 else ''}" if source_count else ""
        self._frame_count_lbl.setText(f"{n} frame{'s' if n != 1 else ''}{extra}")
        self.queue_status_changed.emit(self.get_queue_status_text())

    def get_queue_status_text(self) -> str:
        n = len(self._frames)
        source_count = len({path for path in (_frame_source_path(entry) for entry in self._frames) if path})
        if n <= 0:
            return "🎞 GIF Builder ready"
        parts = [f"{n} frame{'s' if n != 1 else ''}"]
        if source_count:
            parts.append(f"{source_count} source{'s' if source_count != 1 else ''}")
        return "🎞 GIF Builder: " + "  •  ".join(parts)

    def _status_bar_import_summary(self) -> str:
        text = self._import_status_lbl.text().strip()
        if not text or text.lower().startswith("ready:"):
            return ""
        if text.startswith("Import summary: "):
            return "import " + text[len("Import summary: "):]
        return text

    def get_status_bar_text(self) -> str:
        summary = self.get_queue_status_text()
        extras = []
        import_summary = self._status_bar_import_summary()
        if self._frames:
            preview = self._preview_frame_lbl.text().strip()
            if preview and preview != "0 / 0":
                extras.append(f"preview {preview}")
            if self._preview_timer.isActive():
                extras.append("playing")
            if import_summary:
                extras.append(import_summary)
        elif _has_ffmpeg() and _has_imageio() and _has_imageio_ffmpeg():
            extras.append("video imports available")
        else:
            extras.append("image/GIF mode")
        if import_summary and import_summary not in extras:
            extras.append(import_summary)
        if extras:
            summary += "  •  " + "  •  ".join(extras)
        return summary

    def _refresh_session_status(self, *_args) -> None:
        status = self.get_status_bar_text().strip()
        text = f"What works here right now: {status}" if status else "What works here right now: ready"
        next_text = self._next_step_lbl.text().strip()
        if next_text:
            text += f"\n{next_text}"
        self._session_status_lbl.setText(text)
        self._session_status_lbl.setToolTip((status + "\n\n" + next_text).strip() or text)

    def _apply_import_detail_visibility(self) -> None:
        detail = self._import_detail_box.toPlainText().strip()
        has_detail = bool(detail)
        expanded = bool(has_detail and self._import_detail_expanded)
        self._import_detail_box.setVisible(expanded)
        self._import_detail_toggle_btn.setVisible(has_detail)
        self._import_copy_btn.setVisible(has_detail)
        self._import_copy_btn.setEnabled(has_detail)
        self._import_detail_toggle_btn.setText("Hide details" if expanded else "Show details")

    def _toggle_import_details(self) -> None:
        if not self._import_detail_box.toPlainText().strip():
            return
        self._import_detail_expanded = not self._import_detail_expanded
        self._apply_import_detail_visibility()

    def _copy_import_details(self) -> None:
        detail = self._import_detail_box.toPlainText().strip()
        if not detail:
            return
        summary = self._import_status_lbl.text().strip()
        payload = summary if not summary else f"{summary}\n\n{detail}"
        QApplication.clipboard().setText(payload)
        self.status_notice.emit("GIF Builder: copied import diagnostics", 4000)

    def _set_import_status(self, message: str, *, detail: str = "", tone: str = "neutral") -> None:
        colors = {
            "neutral": "gray",
            "success": "#2e7d32",
            "warning": "#b26a00",
            "error": "#b00020",
        }
        self._import_status_lbl.setText(message)
        self._import_status_lbl.setStyleSheet(f"color: {colors.get(tone, 'gray')}; font-size: 11px;")
        self._import_status_lbl.setToolTip(detail or message)
        detail = str(detail or "").strip()
        self._import_detail_box.setPlainText(detail)
        if not detail:
            self._import_detail_expanded = False
        elif tone in {"warning", "error"}:
            self._import_detail_expanded = True
        self._apply_import_detail_visibility()
        self.queue_status_changed.emit(self.get_queue_status_text())

    def _set_next_step_text(self, text: str, *, tone: str = "neutral") -> None:
        colors = {
            "neutral": "#888",
            "success": "#2e7d32",
            "warning": "#b26a00",
            "error": "#b00020",
        }
        rendered = str(text or "").strip() or "Next step: add media to build a frame timeline, then preview or export."
        self._next_step_lbl.setText(rendered)
        self._next_step_lbl.setToolTip(rendered)
        self._next_step_lbl.setStyleSheet(f"color: {colors.get(tone, '#888')}; font-size: 11px;")
        self._refresh_session_status()

    def _update_import_status(
        self,
        *,
        attempted: int,
        loaded_sources: int,
        added_frames: int,
        recovered: list[tuple[str, str]],
        failures: list[tuple[str, str]],
        skipped: list[str],
        loaded_details: list[str],
        source_type_counts: dict[str, int],
        frame_size_counts: dict[str, int],
        alpha_source_count: int,
        largest_frame: tuple[int, int],
    ) -> None:
        if attempted <= 0:
            self._set_import_status(
                "Ready: add images, GIFs, videos, or probe-detected odd containers. Import notes, grouped failures, and skipped-file details will appear here."
            )
            self._set_next_step_text(
                "Next step: add media to build a frame timeline, then adjust timing, preview, or export.",
                tone="neutral",
            )
            return
        parts = [
            f"Loaded {loaded_sources} source{'s' if loaded_sources != 1 else ''}",
            f"{added_frames} frame{'s' if added_frames != 1 else ''}",
        ]
        if recovered:
            parts.append(f"{len(recovered)} recovered")
        if failures:
            parts.append(f"{len(failures)} failed")
        if skipped:
            parts.append(f"{len(skipped)} skipped")
        if source_type_counts:
            parts.append(_summarize_count_buckets(source_type_counts, limit=2))
        if len(frame_size_counts) > 1:
            parts.append(f"{len(frame_size_counts)} frame sizes")
        if alpha_source_count > 0:
            parts.append(f"{alpha_source_count} alpha source{'s' if alpha_source_count != 1 else ''}")
        tone = "success" if loaded_sources and not failures and not skipped else "warning" if loaded_sources else "error"
        detail_lines = []
        if recovered:
            detail_lines.append(
                "Recovery fallbacks used:\n  " + "\n  ".join(f"{name}: {note}" for name, note in recovered)
            )
        if loaded_details:
            loaded_lines = loaded_details[:10]
            if len(loaded_details) > len(loaded_lines):
                loaded_lines.append(f"…and {len(loaded_details) - len(loaded_lines)} more source(s).")
            detail_lines.append("Loaded sources:\n  " + "\n  ".join(loaded_lines))
        if source_type_counts:
            detail_lines.append(
                "Source types: "
                + ", ".join(f"{kind} ×{count}" for kind, count in sorted(source_type_counts.items()))
            )
        if frame_size_counts:
            detail_lines.append("Frame sizes: " + _summarize_count_buckets(frame_size_counts, limit=4))
        if largest_frame[0] > 0 and largest_frame[1] > 0:
            detail_lines.append(f"Largest imported frame: {largest_frame[0]}×{largest_frame[1]}")
        if alpha_source_count > 0:
            detail_lines.append(
                f"Alpha-capable sources: {alpha_source_count} / {loaded_sources or max(1, alpha_source_count)}"
            )
        if failures:
            grouped: dict[str, int] = {}
            for name, detail in failures:
                category = _classify_import_failure(name, detail)
                grouped[category] = grouped.get(category, 0) + 1
            detail_lines.append(
                "Failure types: "
                + ", ".join(f"{category} ×{count}" for category, count in grouped.items())
            )
            detail_lines.append(
                "Failure guidance:\n  "
                + "\n  ".join(f"{category}: {_import_failure_guidance(category)}" for category in grouped)
            )
            detail_lines.append(
                "Import failures:\n  " + "\n  ".join(f"{name}: {detail}" for name, detail in failures)
            )
        if skipped:
            detail_lines.append("Skipped unsupported files:\n  " + "\n  ".join(skipped))
        summary = "Import summary: " + "  •  ".join(parts)
        self._set_import_status(summary, detail="\n\n".join(detail_lines), tone=tone)
        self._set_next_step_text(
            _gif_import_next_step_text(
                loaded_sources=loaded_sources,
                recovered=recovered,
                failures=failures,
                skipped=skipped,
            ),
            tone=tone,
        )
        self.status_notice.emit(f"GIF Builder: {summary}", 7000)

    def _update_frame_diagnostics(self) -> None:
        total = len(self._frames)
        if total <= 0:
            self._frame_diag_lbl.setText(
                "Frame diagnostics: add or select media to inspect frame size, source, and timing."
            )
            self._frame_diag_lbl.setToolTip(self._frame_diag_lbl.text())
            return
        row = max(0, min(self._preview_idx, total - 1))
        entry = self._frames[row]
        source_path = _frame_source_path(entry)
        source_name = os.path.basename(source_path) if source_path else "unknown source"
        width, height = entry._pil.size
        effective_delay = entry.delay_ms if entry.delay_ms is not None else self._delay_slider.value()
        alpha = "yes" if "A" in entry._pil.getbands() else "no"
        source_frames = sum(1 for candidate in self._frames if _frame_source_path(candidate) == source_path)
        source_kind = _frame_source_kind(source_path, source_frames)
        export_w, export_h = _scaled_size_for_export(
            (width, height),
            self._width_slider.value(),
            self._height_slider.value(),
        )
        if entry.delay_ms is None:
            timing_mode = "global timing"
        elif entry.source_delay_ms is not None and entry.delay_ms == entry.source_delay_ms:
            timing_mode = "source timing"
        elif entry.source_delay_ms is None:
            timing_mode = "per-frame override"
        else:
            timing_mode = "per-frame override"
        details = (
            f"Frame diagnostics: {source_name}  •  {source_kind} source  •  preview frame {row + 1}/{total}  •  "
            f"source frame {entry.frame_index + 1}"
        )
        if source_frames > 1:
            details += f"/{source_frames}"
        details += f"  •  {width}×{height}  •  {effective_delay} ms ({timing_mode})  •  alpha={alpha}"
        if export_w > 0 and export_h > 0:
            details += f"  •  export {export_w}×{export_h}"
            if (export_w, export_h) != (width, height):
                details += " (resized)"
        self._frame_diag_lbl.setText(details)
        tooltip = details if not source_path else f"{source_path}\n{details}"
        self._frame_diag_lbl.setToolTip(tooltip)

    # ------------------------------------------------------------------
    # Per-frame delay override (slider-based)
    # ------------------------------------------------------------------

    def _on_selection_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._frames):
            self._pf_check.blockSignals(True)
            self._pf_check.setChecked(False)
            self._pf_check.blockSignals(False)
            self._pf_slider.setEnabled(False)
            self._update_frame_diagnostics()
            return
        # Show the selected frame in the preview when playback is not running (item 39)
        if not self._preview_timer.isActive():
            self._preview_idx = row
            self._update_scrubber()
            self._update_preview_frame()
        delay = self._frames[row].delay_ms
        self._pf_check.blockSignals(True)
        self._pf_slider.blockSignals(True)
        self._pf_check.setChecked(delay is not None)
        val = delay if delay is not None else self._delay_slider.value()
        self._pf_slider.setValue(max(10, min(3000, val)))
        self._pf_val_lbl.setText(f"{self._pf_slider.value()} ms")
        self._pf_slider.setEnabled(delay is not None)
        self._pf_check.blockSignals(False)
        self._pf_slider.blockSignals(False)
        self._update_frame_diagnostics()

    def _on_pf_check(self, checked: bool) -> None:
        self._pf_slider.setEnabled(checked)
        row = self._frame_list.currentRow()
        if 0 <= row < len(self._frames):
            self._frames[row].delay_ms = self._pf_slider.value() if checked else None
        self._update_frame_diagnostics()

    def _on_pf_slider_changed(self, value: int) -> None:
        self._pf_val_lbl.setText(f"{value} ms")
        row = self._frame_list.currentRow()
        if 0 <= row < len(self._frames) and self._pf_check.isChecked():
            self._frames[row].delay_ms = value
        self._update_frame_diagnostics()

    # ------------------------------------------------------------------
    # Global delay slider
    # ------------------------------------------------------------------

    def _on_delay_changed(self, value: int) -> None:
        self._delay_val_lbl.setText(f"{value} ms")
        if self._preview_timer.isActive():
            self._preview_timer.setInterval(max(10, value))
        self._update_frame_diagnostics()

    # ------------------------------------------------------------------
    # Preview / transport
    # ------------------------------------------------------------------

    def _update_scrubber(self) -> None:
        total = len(self._frames)
        self._scrubber.blockSignals(True)
        self._scrubber.setRange(0, max(0, total - 1))
        self._scrubber.setValue(min(self._preview_idx, max(0, total - 1)))
        self._scrubber.blockSignals(False)

    def _on_scrub(self, value: int) -> None:
        self._preview_idx = value
        self._update_preview_frame()

    def _rewind(self) -> None:
        self._preview_idx = 0
        self._scrubber.setValue(0)
        self._update_preview_frame()

    def _on_play_toggled(self, playing: bool) -> None:
        if playing:
            if len(self._frames) < 2:
                self._btn_play.setChecked(False)
                return
            self._preview_timer.start(max(10, self._delay_slider.value()))
            self._btn_play.setText("⏸  Pause")
        else:
            self._preview_timer.stop()
            self._btn_play.setText("▶  Play")
        self.queue_status_changed.emit(self.get_queue_status_text())

    def _advance_preview(self) -> None:
        if not self._frames:
            self._preview_timer.stop()
            self._btn_play.setChecked(False)
            return
        self._preview_idx = (self._preview_idx + 1) % len(self._frames)
        self._scrubber.blockSignals(True)
        self._scrubber.setValue(self._preview_idx)
        self._scrubber.blockSignals(False)
        self._update_preview_frame()
        # Honour per-frame delay for the next tick
        entry = self._frames[self._preview_idx]
        interval = entry.delay_ms if entry.delay_ms is not None else self._delay_slider.value()
        self._preview_timer.setInterval(max(10, interval))

    def _update_preview_frame(self) -> None:
        total = len(self._frames)
        if total == 0:
            self._preview_lbl.setText("(no frames yet)")
            self._preview_frame_lbl.setText("0 / 0")
            self._update_frame_diagnostics()
            self.queue_status_changed.emit(self.get_queue_status_text())
            return
        self._preview_idx = max(0, min(self._preview_idx, total - 1))
        entry = self._frames[self._preview_idx]
        pix = entry.thumbnail(
            max(60, self._preview_lbl.width() - 8),
            max(60, self._preview_lbl.height() - 8),
        )
        self._preview_lbl.setPixmap(pix)
        self._preview_frame_lbl.setText(f"{self._preview_idx + 1} / {total}")
        self._update_frame_diagnostics()
        self.queue_status_changed.emit(self.get_queue_status_text())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_preview_frame()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export(self) -> None:
        if not self._frames:
            QMessageBox.information(self, "No Frames", "Add at least one image first.")
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Animated GIF", "animation.gif",
            "GIF Files (*.gif);;All Files (*)",
        )
        if not out_path:
            return
        if not out_path.lower().endswith(".gif"):
            out_path += ".gif"

        from PIL import Image

        max_w = self._width_slider.value()
        max_h = self._height_slider.value()
        global_delay = self._delay_slider.value()
        loop = self._loop_slider.value()
        optimize = self._optimize_check.isChecked()

        progress = QProgressDialog("Building GIF…", "Cancel", 0, len(self._frames), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)

        import time as _time
        import datetime
        _build_start = _time.monotonic()

        pil_frames: list[Image.Image] = []
        durations: list[int] = []
        try:
            for idx, entry in enumerate(self._frames):
                progress.setValue(idx)
                if progress.wasCanceled():
                    for f in pil_frames:
                        f.close()
                    progress.close()
                    return
                # Update progress label with time estimate (item 40)
                if idx > 0:
                    elapsed = _time.monotonic() - _build_start
                    rate = idx / elapsed
                    remaining = (len(self._frames) - idx) / rate if rate > 0 else 0
                    if remaining > 60:
                        eta_str = f"{int(remaining // 60)}m {int(remaining % 60)}s"
                    else:
                        eta_str = f"{int(remaining)}s"
                    progress.setLabelText(
                        f"Building GIF…  frame {idx + 1} / {len(self._frames)}"
                        f"  (≈ {eta_str} remaining)"
                    )
                frame = entry._pil.copy()
                if max_w > 0 or max_h > 0:
                    target_w = max_w if max_w > 0 else 99999
                    target_h = max_h if max_h > 0 else 99999
                    frame.thumbnail((target_w, target_h), Image.LANCZOS)
                frame_p = frame.quantize(colors=255, method=Image.Quantize.FASTOCTREE, dither=0)
                pil_frames.append(frame_p)
                durations.append(entry.delay_ms if entry.delay_ms is not None else global_delay)
                frame.close()
        except Exception as exc:
            for f in pil_frames:
                try:
                    f.close()
                except Exception:
                    pass
            progress.close()
            QMessageBox.critical(self, "Build Error", f"Error preparing frames:\n{exc}")
            return

        progress.setLabelText("Saving GIF…")
        progress.setValue(len(self._frames))
        try:
            if len(pil_frames) == 1:
                pil_frames[0].save(
                    out_path, format="GIF",
                    save_all=True,
                    append_images=[],
                    duration=durations[0] if durations else global_delay,
                    loop=loop,
                    optimize=optimize,
                )
            else:
                pil_frames[0].save(
                    out_path, format="GIF",
                    save_all=True,
                    append_images=pil_frames[1:],
                    duration=durations,
                    loop=loop,
                    optimize=optimize,
                )
        except Exception as exc:
            try:
                Path(out_path).unlink(missing_ok=True)
            except Exception:
                pass
            QMessageBox.critical(self, "Save Error", f"Could not save GIF:\n{exc}")
            return
        finally:
            for f in pil_frames:
                try:
                    f.close()
                except Exception:
                    pass

        self.exported.emit(out_path)
        self._record_export_history(
            out_path,
            delay_ms=global_delay,
            loop=loop,
            optimize=optimize,
            resize=(max_w, max_h),
        )
        self.status_notice.emit(
            f"GIF Builder export saved: {Path(out_path).name} ({len(self._frames)} frame{'s' if len(self._frames) != 1 else ''})",
            8000,
        )
        QMessageBox.information(
            self, "GIF Saved",
            f"Animated GIF saved to:\n{out_path}\n\n"
            f"{len(self._frames)} frame(s), loop={loop if loop > 0 else '∞'}",
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.queue_status_changed.emit(self.get_queue_status_text())

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.queue_status_changed.emit("")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._preview_timer.stop()
        for entry in self._frames:
            entry.close()
        self._frames.clear()
        super().closeEvent(event)
