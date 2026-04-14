"""
Video Tool Dialog.

Provides a lightweight video editor that lets the user:
  • Add one or more video clips (via imageio + ffmpeg, or image sequences)
  • Reorder / trim clips (start/end frame per clip)
  • Adjust white point, black point, brightness, contrast, tone (Pillow)
  • Apply visual filters: greyscale, sepia, invert, sharpen, blur, vignette
  • Preview with play/pause/rewind and a position scrubber
  • Export to MP4 (via imageio+ffmpeg) or animated GIF (via Pillow)

**Dependency note**: Full video I/O requires ffmpeg on the system PATH plus the
imageio-ffmpeg package.  If ffmpeg is unavailable the dialog can still process
image-sequence "videos" (folders of PNGs) and export animated GIFs.

Opening the dialog:
  • Right-clicking anywhere on the main window → "Open Video Editor"
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QSize,
)
from PyQt6.QtGui import (
    QImage, QPixmap, QKeySequence, QShortcut, QIcon,
)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFileDialog, QSpinBox,
    QSlider, QComboBox, QCheckBox, QGroupBox, QGridLayout,
    QMessageBox, QProgressDialog, QSplitter, QWidget,
    QAbstractSpinBox, QDoubleSpinBox, QFrame, QSizePolicy,
    QScrollArea,
)

_VIDEO_EXTS = {
    ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".3gp",
}
_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif",
}

_PREVIEW_MAX_W = 400
_PREVIEW_MAX_H = 300


def _has_ffmpeg() -> bool:
    """Return True if imageio-ffmpeg (or system ffmpeg) is available."""
    try:
        import imageio
        import imageio.plugins.ffmpeg  # noqa: F401
        return True
    except Exception:
        pass
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


def _pil_to_pixmap(pil_img) -> QPixmap:
    from PIL import Image
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
    # Levels: remap [black_point, white_point] → [0, 255]
    if black_point > 0 or white_point < 255:
        img = ImageOps.autocontrast(img, cutoff=0)
        # Manual levels via point()
        def _levels(v: int) -> int:
            bp, wp = max(0, min(254, black_point)), max(black_point + 1, min(255, white_point))
            return max(0, min(255, int((v - bp) * 255 / max(1, wp - bp))))
        img = img.point(lambda v: _levels(v))
    # Brightness
    if abs(brightness - 1.0) > 0.01:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    # Contrast
    if abs(contrast - 1.0) > 0.01:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    # Saturation / colour
    if abs(saturation - 1.0) > 0.01:
        img = ImageEnhance.Color(img).enhance(saturation)
    # Sharpness
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
        from PIL import ImageOps
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
        from PIL import Image as PILImage
        mask = PILImage.new("L", img.size, 0)
        import struct
        w, h = img.size
        cx, cy = w / 2, h / 2
        mx = cx * 1.4
        pix = mask.load()
        for y in range(h):
            for x in range(w):
                d = math.hypot((x - cx) / mx, (y - cy) / mx)
                pix[x, y] = max(0, min(255, int((1 - min(1, d)) * 255)))
        dark = PILImage.new("RGB", img.size, (0, 0, 0))
        img = PILImage.composite(img, dark, mask)
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
        reader = imageio.get_reader(path)
        meta = reader.get_meta_data()
        fps = float(meta.get("fps", 25))
        # Cache all frames in memory (for short clips/demos)
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


class VideoToolDialog(QDialog):
    """Lightweight video editor dialog.

    Combines multiple clips, applies visual adjustments and filters, and
    exports the result.  Requires imageio+ffmpeg for video I/O; still works
    for single images and exports animated GIFs without ffmpeg.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Video Editor")
        self.setMinimumSize(900, 640)
        self.setModal(False)
        self._clips: list[_ClipEntry] = []
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._advance_preview)
        self._preview_clip_idx: int = 0
        self._preview_frame_in_clip: int = 0
        self._is_playing: bool = False
        self._ffmpeg_available = _has_ffmpeg()
        self._build_ui()
        QShortcut(QKeySequence("Delete"), self).activated.connect(self._remove_selected)
        QShortcut(QKeySequence("Space"), self).activated.connect(self._toggle_play)

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
                "⚠  ffmpeg / imageio-ffmpeg not found.  Video file import and MP4 export "
                "are unavailable.  You can still add image files and export an animated GIF."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet("color: orange;")
            root.addWidget(warn)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ---- Left: clip list + trim controls ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        tb = QHBoxLayout()
        self._btn_add_video = QPushButton("🎞 Add Video")
        self._btn_add_video.setEnabled(self._ffmpeg_available)
        self._btn_add_video.setToolTip(
            "Add a video file to the timeline.\n(Requires ffmpeg)"
        )
        self._btn_add_video.clicked.connect(self._add_video)
        tb.addWidget(self._btn_add_video)

        self._btn_add_img = QPushButton("🖼 Add Images")
        self._btn_add_img.setToolTip("Add still image(s) as single-frame clips.")
        self._btn_add_img.clicked.connect(self._add_images)
        tb.addWidget(self._btn_add_img)

        self._btn_remove = QPushButton("🗑 Remove")
        self._btn_remove.clicked.connect(self._remove_selected)
        tb.addWidget(self._btn_remove)

        self._btn_up = QPushButton("⬆ Up")
        self._btn_up.clicked.connect(self._move_up)
        tb.addWidget(self._btn_up)

        self._btn_down = QPushButton("⬇ Down")
        self._btn_down.clicked.connect(self._move_down)
        tb.addWidget(self._btn_down)

        tb.addStretch()
        left_layout.addLayout(tb)

        self._clip_list = QListWidget()
        self._clip_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._clip_list.currentRowChanged.connect(self._on_clip_selected)
        left_layout.addWidget(self._clip_list, 1)

        # Trim group
        grp_trim = QGroupBox("Trim Selected Clip")
        trim_gl = QGridLayout(grp_trim)
        trim_gl.addWidget(QLabel("Start frame:"), 0, 0)
        self._trim_start_spin = QSpinBox()
        self._trim_start_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._trim_start_spin.setRange(0, 999999)
        self._trim_start_spin.setValue(0)
        self._trim_start_spin.valueChanged.connect(self._on_trim_start_changed)
        trim_gl.addWidget(self._trim_start_spin, 0, 1)
        trim_gl.addWidget(QLabel("End frame:"), 1, 0)
        self._trim_end_spin = QSpinBox()
        self._trim_end_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._trim_end_spin.setRange(0, 999999)
        self._trim_end_spin.setValue(0)
        self._trim_end_spin.valueChanged.connect(self._on_trim_end_changed)
        trim_gl.addWidget(self._trim_end_spin, 1, 1)
        self._clip_info_lbl = QLabel("")
        trim_gl.addWidget(self._clip_info_lbl, 2, 0, 1, 2)
        left_layout.addWidget(grp_trim)

        splitter.addWidget(left)

        # ---- Centre: preview ----
        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(0, 0, 0, 0)
        centre_layout.setSpacing(6)

        grp_preview = QGroupBox("Preview")
        pv_layout = QVBoxLayout(grp_preview)

        self._preview_lbl = QLabel()
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_lbl.setMinimumSize(_PREVIEW_MAX_W, _PREVIEW_MAX_H)
        self._preview_lbl.setFrameShape(QFrame.Shape.StyledPanel)
        self._preview_lbl.setText("(no clips)")
        pv_layout.addWidget(self._preview_lbl, 1)

        # Scrubber
        self._scrubber = QSlider(Qt.Orientation.Horizontal)
        self._scrubber.setRange(0, 0)
        self._scrubber.setValue(0)
        self._scrubber.setToolTip("Drag to scrub through the timeline.")
        self._scrubber.sliderMoved.connect(self._on_scrub)
        pv_layout.addWidget(self._scrubber)

        # Transport controls
        ctrl = QHBoxLayout()
        self._btn_rewind = QPushButton("⏮ Rewind")
        self._btn_rewind.clicked.connect(self._rewind)
        ctrl.addWidget(self._btn_rewind)
        self._btn_play = QPushButton("▶ Play")
        self._btn_play.setCheckable(True)
        self._btn_play.toggled.connect(self._on_play_toggled)
        ctrl.addWidget(self._btn_play)
        self._pos_lbl = QLabel("0 / 0")
        self._pos_lbl.setObjectName("subheader")
        ctrl.addWidget(self._pos_lbl)
        ctrl.addStretch()
        pv_layout.addLayout(ctrl)

        centre_layout.addWidget(grp_preview, 1)

        splitter.addWidget(centre)

        # ---- Right: adjustments + export ----
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_inner = QWidget()
        right_layout = QVBoxLayout(right_inner)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_scroll.setWidget(right_inner)

        grp_adj = QGroupBox("Visual Adjustments")
        adj_gl = QGridLayout(grp_adj)
        adj_gl.setHorizontalSpacing(8)
        adj_gl.setVerticalSpacing(6)

        def _make_dbl_spin(lo, hi, val, step, suffix=""):
            s = QDoubleSpinBox()
            s.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
            s.setRange(lo, hi)
            s.setValue(val)
            s.setSingleStep(step)
            if suffix:
                s.setSuffix(suffix)
            s.valueChanged.connect(self._refresh_preview_adjustments)
            return s

        adj_gl.addWidget(QLabel("Brightness:"), 0, 0)
        self._brightness_spin = _make_dbl_spin(0.1, 4.0, 1.0, 0.05)
        self._brightness_spin.setToolTip("1.0 = original.  >1 = brighter.  <1 = darker.")
        adj_gl.addWidget(self._brightness_spin, 0, 1)

        adj_gl.addWidget(QLabel("Contrast:"), 1, 0)
        self._contrast_spin = _make_dbl_spin(0.1, 4.0, 1.0, 0.05)
        self._contrast_spin.setToolTip("1.0 = original.  >1 = more contrast.")
        adj_gl.addWidget(self._contrast_spin, 1, 1)

        adj_gl.addWidget(QLabel("Saturation:"), 2, 0)
        self._saturation_spin = _make_dbl_spin(0.0, 4.0, 1.0, 0.05)
        self._saturation_spin.setToolTip("1.0 = original.  0.0 = greyscale.  >1 = vivid.")
        adj_gl.addWidget(self._saturation_spin, 2, 1)

        adj_gl.addWidget(QLabel("Sharpness:"), 3, 0)
        self._sharpness_spin = _make_dbl_spin(0.0, 4.0, 1.0, 0.1)
        self._sharpness_spin.setToolTip("1.0 = original.  >1 = sharper.  <1 = softer.")
        adj_gl.addWidget(self._sharpness_spin, 3, 1)

        adj_gl.addWidget(QLabel("Black point:"), 4, 0)
        self._black_spin = QSpinBox()
        self._black_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._black_spin.setRange(0, 254)
        self._black_spin.setValue(0)
        self._black_spin.setToolTip("Input level remapped to black (0).  Lifts the shadows.")
        self._black_spin.valueChanged.connect(self._refresh_preview_adjustments)
        adj_gl.addWidget(self._black_spin, 4, 1)

        adj_gl.addWidget(QLabel("White point:"), 5, 0)
        self._white_spin = QSpinBox()
        self._white_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._white_spin.setRange(1, 255)
        self._white_spin.setValue(255)
        self._white_spin.setToolTip("Input level remapped to white (255).  Pulls down highlights.")
        self._white_spin.valueChanged.connect(self._refresh_preview_adjustments)
        adj_gl.addWidget(self._white_spin, 5, 1)

        btn_reset_adj = QPushButton("Reset All")
        btn_reset_adj.clicked.connect(self._reset_adjustments)
        adj_gl.addWidget(btn_reset_adj, 6, 0, 1, 2)

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
        ex_gl = QGridLayout(grp_export)

        ex_gl.addWidget(QLabel("Format:"), 0, 0)
        self._export_fmt_combo = QComboBox()
        self._export_fmt_combo.addItem("Animated GIF (.gif)", userData="gif")
        if self._ffmpeg_available:
            self._export_fmt_combo.addItem("MP4 Video (.mp4)", userData="mp4")
        ex_gl.addWidget(self._export_fmt_combo, 0, 1)

        ex_gl.addWidget(QLabel("FPS (for MP4/GIF):"), 1, 0)
        self._fps_spin = QDoubleSpinBox()
        self._fps_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._fps_spin.setRange(0.5, 120.0)
        self._fps_spin.setValue(25.0)
        self._fps_spin.setSingleStep(1.0)
        ex_gl.addWidget(self._fps_spin, 1, 1)

        self._btn_export = QPushButton("💾 Export…")
        self._btn_export.setToolTip("Render and export the edited video/GIF.")
        self._btn_export.clicked.connect(self._export)
        ex_gl.addWidget(self._btn_export, 2, 0, 1, 2)

        right_layout.addWidget(grp_export)
        right_layout.addStretch()

        splitter.addWidget(right_scroll)
        splitter.setSizes([230, 380, 250])

    # ------------------------------------------------------------------
    # Clip management
    # ------------------------------------------------------------------

    def _add_video(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Video Files", "",
            "Video Files (*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.m4v *.mpg);;All Files (*)",
        )
        for path in paths:
            clip = _load_video_clip(path)
            if clip is None:
                QMessageBox.warning(self, "Load Error",
                                    f"Could not open video:\n{Path(path).name}\n"
                                    "Ensure ffmpeg is installed.")
                continue
            self._clips.append(clip)
            self._clip_list.addItem(f"🎞 {Path(path).name}  [{clip.total_frames} frames @ {clip.fps:.1f} fps]")
        self._update_scrubber()
        self._update_preview()

    def _add_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Images", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.tif);;All Files (*)",
        )
        for path in paths:
            clip = _load_image_as_clip(path)
            if clip is None:
                continue
            self._clips.append(clip)
            self._clip_list.addItem(f"🖼 {Path(path).name}")
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

    def _move_up(self) -> None:
        row = self._clip_list.currentRow()
        if row <= 0:
            return
        self._clips[row - 1], self._clips[row] = self._clips[row], self._clips[row - 1]
        item = self._clip_list.takeItem(row)
        self._clip_list.insertItem(row - 1, item)
        self._clip_list.setCurrentRow(row - 1)

    def _move_down(self) -> None:
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._clips) - 1:
            return
        self._clips[row], self._clips[row + 1] = self._clips[row + 1], self._clips[row]
        item = self._clip_list.takeItem(row)
        self._clip_list.insertItem(row + 1, item)
        self._clip_list.setCurrentRow(row + 1)

    def _on_clip_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._clips):
            self._clip_info_lbl.setText("")
            return
        clip = self._clips[row]
        self._trim_start_spin.blockSignals(True)
        self._trim_end_spin.blockSignals(True)
        self._trim_start_spin.setMaximum(max(0, clip.total_frames - 1))
        self._trim_end_spin.setMaximum(max(0, clip.total_frames - 1))
        self._trim_start_spin.setValue(clip.trim_start)
        self._trim_end_spin.setValue(clip.trim_end)
        self._trim_start_spin.blockSignals(False)
        self._trim_end_spin.blockSignals(False)
        self._clip_info_lbl.setText(
            f"{clip.total_frames} total frames  |  "
            f"{clip.active_frames} active  |  {clip.fps:.1f} fps"
        )

    def _on_trim_start_changed(self, val: int) -> None:
        row = self._clip_list.currentRow()
        if 0 <= row < len(self._clips):
            clip = self._clips[row]
            clip.trim_start = min(val, clip.trim_end)
            self._clip_info_lbl.setText(
                f"{clip.total_frames} total frames  |  "
                f"{clip.active_frames} active  |  {clip.fps:.1f} fps"
            )
            self._update_scrubber()

    def _on_trim_end_changed(self, val: int) -> None:
        row = self._clip_list.currentRow()
        if 0 <= row < len(self._clips):
            clip = self._clips[row]
            clip.trim_end = max(val, clip.trim_start)
            self._clip_info_lbl.setText(
                f"{clip.total_frames} total frames  |  "
                f"{clip.active_frames} active  |  {clip.fps:.1f} fps"
            )
            self._update_scrubber()

    # ------------------------------------------------------------------
    # Preview / transport
    # ------------------------------------------------------------------

    def _total_preview_frames(self) -> int:
        return sum(c.active_frames for c in self._clips)

    def _update_scrubber(self) -> None:
        total = max(0, self._total_preview_frames() - 1)
        self._scrubber.setRange(0, total)
        self._pos_lbl.setText(f"0 / {self._total_preview_frames()}")

    def _global_frame_to_clip(self, global_idx: int):
        """Return (clip_idx, frame_in_clip) for a global frame index."""
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
        ci, fi = self._global_frame_to_clip(self._scrubber.value())
        self._preview_clip_idx = ci
        self._preview_frame_in_clip = fi
        try:
            pil = self._clips[ci].get_frame(fi)
            pil = _apply_adjustments(
                pil,
                brightness=self._brightness_spin.value(),
                contrast=self._contrast_spin.value(),
                black_point=self._black_spin.value(),
                white_point=self._white_spin.value(),
                saturation=self._saturation_spin.value(),
                sharpness=self._sharpness_spin.value(),
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

        g = self._scrubber.value()
        self._pos_lbl.setText(f"{g + 1} / {total}")

    def _refresh_preview_adjustments(self) -> None:
        self._update_preview()

    def _on_scrub(self, value: int) -> None:
        self._update_preview()

    def _toggle_play(self) -> None:
        self._btn_play.setChecked(not self._btn_play.isChecked())

    def _on_play_toggled(self, playing: bool) -> None:
        self._is_playing = playing
        if playing:
            if self._total_preview_frames() == 0:
                self._btn_play.setChecked(False)
                return
            fps = max(0.1, self._fps_spin.value())
            self._preview_timer.start(int(1000 / fps))
            self._btn_play.setText("⏸ Pause")
        else:
            self._preview_timer.stop()
            self._btn_play.setText("▶ Play")

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

        fps = max(0.1, self._fps_spin.value())
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
                    brightness=self._brightness_spin.value(),
                    contrast=self._contrast_spin.value(),
                    black_point=self._black_spin.value(),
                    white_point=self._white_spin.value(),
                    saturation=self._saturation_spin.value(),
                    sharpness=self._sharpness_spin.value(),
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
                # MP4 via imageio
                import imageio
                import numpy as np
                with imageio.get_writer(out_path, fps=fps, codec="libx264",
                                        quality=8) as writer:
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

        QMessageBox.information(self, "Export Complete",
                                f"Output saved to:\n{out_path}")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _reset_adjustments(self) -> None:
        for spin in (self._brightness_spin, self._contrast_spin,
                     self._saturation_spin, self._sharpness_spin):
            spin.blockSignals(True)
            spin.setValue(1.0)
            spin.blockSignals(False)
        self._black_spin.blockSignals(True)
        self._white_spin.blockSignals(True)
        self._black_spin.setValue(0)
        self._white_spin.setValue(255)
        self._black_spin.blockSignals(False)
        self._white_spin.blockSignals(False)
        self._filter_combo.blockSignals(True)
        self._filter_combo.setCurrentIndex(0)
        self._filter_combo.blockSignals(False)
        self._update_preview()

    def closeEvent(self, event) -> None:
        self._preview_timer.stop()
        super().closeEvent(event)
