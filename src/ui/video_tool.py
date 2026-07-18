"""
Video Tool Dialog.

Provides a lightweight video editor that lets the user:
  • Add one or more video clips (via imageio + ffmpeg, or image sequences)
  • Drag clips in the list to reorder them (no Up/Down buttons)
  • Trim clips with start/end sliders
  • Adjust white point, black point, brightness, contrast, saturation, sharpness
    – all via smooth drag-sliders with live value readouts
  • Apply visual filters: greyscale, sepia, invert, sharpen, blur, vignette, etc.
  • Preview with play/pause/rewind and a position scrubber
  • Export to MP4 (via imageio+ffmpeg) or animated GIF (via Pillow)

**Dependency note**: Full video I/O requires ffmpeg on the system PATH plus the
imageio-ffmpeg package.  If ffmpeg is unavailable the dialog can still process
image-sequence "videos" (folders of PNGs) and export animated GIFs.

UX highlights (Round-90):
  • All numeric controls use drag-sliders – no arrow-button spinboxes.
  • Clip list is drag-to-reorder; clip data stays in sync via item UserRole.
  • Trim sliders auto-update when a clip is selected.
  • Live preview refreshes immediately on any slider change.

Opening the dialog:
  • Right-clicking anywhere on the main window → "Open Video Editor"
"""
from __future__ import annotations

from pathlib import Path
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
    QMessageBox, QProgressDialog, QSplitter, QWidget,
    QFrame, QScrollArea,
)

_VIDEO_EXTS = {
    # Common containers
    ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".3gp", ".3g2", ".ts", ".m2ts",
    ".mts", ".vob", ".ogv", ".ogg", ".rm", ".rmvb", ".divx",
    ".asf", ".f4v", ".mxf", ".dv", ".yuv",
    # PlayStation / handheld console video formats (opened via ffmpeg)
    ".pmf",    # PSP Movie Format (MPEG-2 based)
    ".pss",    # PlayStation 2 streaming video
    ".str",    # PlayStation 1/2 streaming video
    ".xa",     # PlayStation 1 audio/video
    ".iso",    # ISO disc image (PSP UMD / PS2 DVD)
    ".umd",    # PSP UMD disc image (same structure as ISO)
    ".bin",    # CD-ROM disc image (may contain video sectors)
}
_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif",
}

_PREVIEW_MAX_W = 420
_PREVIEW_MAX_H = 320

_CLIP_ROLE = Qt.ItemDataRole.UserRole  # stores _ClipEntry in list item


def _has_ffmpeg() -> bool:
    """Return True if imageio-ffmpeg (bundled binary) or system ffmpeg is available."""
    # 1. imageio-ffmpeg ships a static ffmpeg binary – preferred because it
    #    works even when no system ffmpeg is installed.
    try:
        import imageio_ffmpeg  # noqa: F401
        _exe = imageio_ffmpeg.get_ffmpeg_exe()
        if _exe:
            return True
    except Exception:
        pass
    # 2. Try imageio's legacy ffmpeg plugin path
    try:
        import imageio
        import imageio.plugins.ffmpeg  # noqa: F401
        return True
    except Exception:
        pass
    # 3. Fall back to a system-installed ffmpeg on the PATH
    try:
        import subprocess
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return result.returncode == 0
    except Exception:
        return False


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


def _pil_to_pixmap(pil_img) -> QPixmap:
    from PIL import Image  # noqa: F401
    rgba = pil_img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qi = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qi)


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


class _ClipEntry:
    """One video clip or image in the video tool's timeline."""

    def __init__(self, path: str, total_frames: int,
                 get_frame_fn, fps: float = 25.0):
        self.path = path
        self.total_frames = total_frames
        self.fps = fps
        self._get_frame = get_frame_fn   # callable(frame_idx) → PIL RGBA image
        self.trim_start: int = 0
        self.trim_end: int = max(0, total_frames - 1)

    @property
    def active_frames(self) -> int:
        return max(0, self.trim_end - self.trim_start + 1)

    def get_frame(self, idx: int) -> "PIL.Image.Image":
        return self._get_frame(self.trim_start + idx)


def _load_video_clip(path: str) -> Optional["_ClipEntry"]:
    """Try to load a video file using imageio-ffmpeg.  Returns None on failure."""
    try:
        import imageio
        # Prefer the imageio-ffmpeg plugin (bundled binary) over the legacy
        # ffmpeg plugin so the app works without a system-installed ffmpeg.
        kwargs: dict = {}
        try:
            import imageio_ffmpeg  # noqa: F401
            kwargs["plugin"] = "ffmpeg"
        except ImportError:
            pass
        reader = imageio.get_reader(path, **kwargs)
        meta = reader.get_meta_data()
        fps = float(meta.get("fps", 25))
        frames = []
        try:
            for frame in reader:
                from PIL import Image
                img = Image.fromarray(frame).convert("RGBA")
                frames.append(img)
        except StopIteration:
            pass
        reader.close()
        if not frames:
            return None
        def _get(idx: int):
            return frames[max(0, min(len(frames) - 1, idx))].copy()
        return _ClipEntry(path, len(frames), _get, fps)
    except Exception:
        return None


def _load_image_as_clip(path: str) -> Optional["_ClipEntry"]:
    """Wrap a single image file as a 1-frame clip."""
    try:
        from PIL import Image
        img = Image.open(path).convert("RGBA")
        img_copy = img.copy()
        img.close()
        def _get(idx: int):
            return img_copy.copy()
        return _ClipEntry(path, 1, _get, 25.0)
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

    files_dropped = pyqtSignal(list)  # list[str]
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
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class VideoToolDialog(QDialog):
    """Lightweight video editor dialog.

    Combines multiple clips, applies visual adjustments and filters, and
    exports the result.  Requires imageio+ffmpeg for video I/O; still works
    for single images and exports animated GIFs without ffmpeg.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🎬 Video Editor")
        self.setMinimumSize(960, 660)
        self.setModal(False)
        self._clips: list[_ClipEntry] = []
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._advance_preview)
        self._is_playing: bool = False
        self._ffmpeg_available = _has_ffmpeg()
        self._build_ui()
        QShortcut(QKeySequence("Delete"), self).activated.connect(self._remove_selected)
        QShortcut(QKeySequence("Space"), self).activated.connect(self._toggle_play)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._export)

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

        if not self._ffmpeg_available:
            warn = QLabel(
                "⚠  ffmpeg / imageio-ffmpeg not found — video import and MP4 export unavailable.  "
                "You can still add images and export an animated GIF."
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
        self._btn_add_video.setEnabled(self._ffmpeg_available)
        self._btn_add_video.setToolTip("Add a video file to the timeline. (Requires ffmpeg)")
        self._btn_add_video.clicked.connect(self._add_video)
        tb.addWidget(self._btn_add_video)

        self._btn_add_img = QPushButton("🖼  Add Images")
        self._btn_add_img.setToolTip("Add still image(s) as single-frame clips.")
        self._btn_add_img.clicked.connect(self._add_images)
        tb.addWidget(self._btn_add_img)

        self._btn_remove = QPushButton("🗑  Remove")
        self._btn_remove.setToolTip("Remove selected clip.  Shortcut: Delete")
        self._btn_remove.clicked.connect(self._remove_selected)
        tb.addWidget(self._btn_remove)
        left_layout.addLayout(tb)

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
        if self._ffmpeg_available:
            self._export_fmt_combo.addItem("MP4 Video (.mp4)", userData="mp4")
        fmt_row.addWidget(self._export_fmt_combo, 1)
        ex_vl.addLayout(fmt_row)

        self._btn_export = QPushButton("💾  Export…")
        self._btn_export.setToolTip("Render and export.  Shortcut: Ctrl+S")
        self._btn_export.setMinimumHeight(34)
        self._btn_export.clicked.connect(self._export)
        ex_vl.addWidget(self._btn_export)

        right_layout.addWidget(grp_export)
        right_layout.addStretch()

        splitter.addWidget(right_scroll)
        splitter.setSizes([240, 420, 260])

    # ------------------------------------------------------------------
    # Clip management
    # ------------------------------------------------------------------

    def _on_files_dropped(self, paths: list[str]) -> None:
        vid, img = [], []
        for p in paths:
            ext = Path(p).suffix.lower()
            if ext in _VIDEO_EXTS:
                vid.append(p)
            elif ext in _IMAGE_EXTS:
                img.append(p)
        if vid:
            self._load_video_paths(vid)
        if img:
            self._load_image_paths(img)

    def _add_video(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Video Files", "",
            "Video Files (*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.m4v "
            "*.mpg *.mpeg *.3gp *.3g2 *.ts *.m2ts *.mts *.vob *.ogv *.ogg "
            "*.rm *.rmvb *.divx *.asf *.f4v *.mxf *.dv "
            "*.pmf *.pss *.str *.xa *.iso *.umd *.bin);;All Files (*)",
        )
        self._load_video_paths(paths)

    def _load_video_paths(self, paths: list[str]) -> None:
        for path in paths:
            clip = _load_video_clip(path)
            if clip is None:
                QMessageBox.warning(
                    self, "Load Error",
                    f"Could not open video:\n{Path(path).name}\n"
                    "Ensure ffmpeg is installed."
                )
                continue
            self._clips.append(clip)
            item = QListWidgetItem(f"🎞  {Path(path).name}  "
                                   f"[{clip.total_frames} fr @ {clip.fps:.1f} fps]")
            item.setData(_CLIP_ROLE, clip)
            self._clip_list.addItem(item)
        self._update_scrubber()
        self._update_preview()

    def _add_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Images", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.tif);;All Files (*)",
        )
        self._load_image_paths(paths)

    def _load_image_paths(self, paths: list[str]) -> None:
        for path in paths:
            clip = _load_image_as_clip(path)
            if clip is None:
                continue
            self._clips.append(clip)
            item = QListWidgetItem(f"🖼  {Path(path).name}")
            item.setData(_CLIP_ROLE, clip)
            self._clip_list.addItem(item)
        self._update_scrubber()
        self._update_preview()

    def _remove_selected(self) -> None:
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._clips):
            return
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
            f"{clip.total_frames} total  •  {clip.active_frames} active  •  {clip.fps:.1f} fps"
        )

    def _on_trim_start_changed(self, val: int) -> None:
        self._trim_start_lbl.setText(str(val))
        row = self._clip_list.currentRow()
        if 0 <= row < len(self._clips):
            clip = self._clips[row]
            clip.trim_start = min(val, clip.trim_end)
            self._clip_info_lbl.setText(
                f"{clip.total_frames} total  •  {clip.active_frames} active  •  {clip.fps:.1f} fps"
            )
            self._update_scrubber()

    def _on_trim_end_changed(self, val: int) -> None:
        self._trim_end_lbl.setText(str(val))
        row = self._clip_list.currentRow()
        if 0 <= row < len(self._clips):
            clip = self._clips[row]
            clip.trim_end = max(val, clip.trim_start)
            self._clip_info_lbl.setText(
                f"{clip.total_frames} total  •  {clip.active_frames} active  •  {clip.fps:.1f} fps"
            )
            self._update_scrubber()

    # ------------------------------------------------------------------
    # Preview / transport
    # ------------------------------------------------------------------

    def _total_preview_frames(self) -> int:
        return sum(c.active_frames for c in self._clips)

    def _update_scrubber(self) -> None:
        total = max(0, self._total_preview_frames() - 1)
        self._scrubber.blockSignals(True)
        self._scrubber.setRange(0, total)
        self._scrubber.blockSignals(False)
        self._pos_lbl.setText(f"0 / {self._total_preview_frames()}")

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
        try:
            pil = self._clips[ci].get_frame(fi)
            pil = _apply_adjustments(
                pil,
                brightness=self._brightness_slider.value() / 100.0,
                contrast=self._contrast_slider.value() / 100.0,
                black_point=self._black_slider.value(),
                white_point=self._white_slider.value(),
                saturation=self._saturation_slider.value() / 100.0,
                sharpness=self._sharpness_slider.value() / 100.0,
            )
            filter_key = self._filter_combo.currentData() or "none"
            pil = _apply_filter(pil, filter_key)
            pix = _pil_to_pixmap(pil).scaled(
                _PREVIEW_MAX_W, _PREVIEW_MAX_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._preview_lbl.setPixmap(pix)
            pil.close()
        except Exception as exc:
            self._preview_lbl.setText(f"(preview error: {exc})")

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
        total = self._total_preview_frames()
        if total == 0:
            QMessageBox.information(self, "No Clips", "Add at least one clip or image first.")
            return

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

        fps = max(0.1, float(self._fps_slider.value()))
        filter_key = self._filter_combo.currentData() or "none"

        progress = QProgressDialog("Rendering frames…", "Cancel", 0, total, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)

        from PIL import Image

        rendered: list[Image.Image] = []
        try:
            for i in range(total):
                progress.setValue(i)
                if progress.wasCanceled():
                    break
                ci, fi = self._global_frame_to_clip(i)
                pil = self._clips[ci].get_frame(fi)
                pil = _apply_adjustments(
                    pil,
                    brightness=self._brightness_slider.value() / 100.0,
                    contrast=self._contrast_slider.value() / 100.0,
                    black_point=self._black_slider.value(),
                    white_point=self._white_slider.value(),
                    saturation=self._saturation_slider.value() / 100.0,
                    sharpness=self._sharpness_slider.value() / 100.0,
                )
                pil = _apply_filter(pil, filter_key)
                rendered.append(pil)
        except Exception as exc:
            for f in rendered:
                try:
                    f.close()
                except Exception:
                    pass
            progress.close()
            QMessageBox.critical(self, "Render Error", f"Error rendering frames:\n{exc}")
            return

        if progress.wasCanceled() or not rendered:
            for f in rendered:
                try:
                    f.close()
                except Exception:
                    pass
            return

        progress.setLabelText("Saving output…")
        progress.setValue(total)

        try:
            if fmt == "gif":
                delay_ms = max(10, int(1000 / fps))
                palettes = [f.quantize(colors=255,
                                       method=Image.Quantize.FASTOCTREE,
                                       dither=0) for f in rendered]
                palettes[0].save(
                    out_path, format="GIF",
                    save_all=True,
                    append_images=palettes[1:],
                    duration=delay_ms,
                    loop=0,
                    optimize=True,
                )
                for p in palettes:
                    try:
                        p.close()
                    except Exception:
                        pass
            else:
                import imageio
                import numpy as np
                _MP4_QUALITY = 8  # 1–10 scale; 10 = best quality / largest file
                with imageio.get_writer(out_path, fps=fps, codec="libx264",
                                        quality=_MP4_QUALITY) as writer:
                    for f in rendered:
                        writer.append_data(np.array(f.convert("RGB")))
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", f"Could not save output:\n{exc}")
            return
        finally:
            for f in rendered:
                try:
                    f.close()
                except Exception:
                    pass

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

    def closeEvent(self, event) -> None:
        self._preview_timer.stop()
        super().closeEvent(event)
