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
    QProgressDialog, QSplitter, QWidget,
    QFrame, QSpinBox, QAbstractSpinBox,
)

# Supported input extensions (what PIL can open)
_SUPPORTED_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif",
    ".gif", ".ico", ".ppm", ".pcx", ".tga", ".avif",
}

_THUMB_W = 120
_THUMB_H = 100

# UserRole key for storing _FrameEntry in QListWidgetItem
_ENTRY_ROLE = Qt.ItemDataRole.UserRole


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
                composite = canvas.copy()
                composite.paste(curr, (0, 0), curr)
                curr.close()
                frames.append(composite.copy())
                disposal = img.info.get("disposal", 0)
                canvas.close()
                canvas = Image.new("RGBA", img.size, (0, 0, 0, 0)) if disposal == 2 else composite
            canvas.close()
    except EOFError:
        pass
    finally:
        img.close()
    return frames


def _pil_to_pixmap(pil_img) -> QPixmap:
    from PIL import Image  # noqa: F401 – needed for convert
    rgba = pil_img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qi = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qi)


class _FrameEntry:
    """A single frame in the GIF builder's frame list."""

    def __init__(self, source_path: str, frame_index: int,
                 pil_image: "PIL.Image.Image", delay_ms: Optional[int] = None):
        self.source_path = source_path
        self.frame_index = frame_index  # 0-based index within source (>0 for animated GIF)
        self._pil = pil_image           # RGBA PIL image; ownership transferred here
        self.delay_ms: Optional[int] = delay_ms  # None = use global delay

    def thumbnail(self, w: int, h: int) -> QPixmap:
        from PIL import Image
        tmp = self._pil.copy()
        tmp.thumbnail((w, h), Image.LANCZOS)
        return _pil_to_pixmap(tmp)

    def close(self) -> None:
        try:
            self._pil.close()
        except Exception:
            pass


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
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)
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
                if p and Path(p).suffix.lower() in _SUPPORTED_EXTS:
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

    def __init__(self, initial_files: Optional[list[str]] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🎞 GIF Builder")
        self.setMinimumSize(860, 620)
        self.setModal(False)
        self._frames: list[_FrameEntry] = []
        self._preview_idx: int = 0
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._advance_preview)
        self._build_ui()
        if initial_files:
            self._add_paths(initial_files)
        QShortcut(QKeySequence("Delete"), self).activated.connect(self._remove_selected)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._export)

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
        self._btn_add = QPushButton("➕  Add Images")
        self._btn_add.setToolTip(
            "Add one or more images to the GIF.\n"
            "Supports PNG, JPEG, WEBP, BMP, TIFF, GIF (all frames), ICO, and more.\n"
            "You can also drag image files directly onto the grid below."
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
        hint = QLabel("💡 Drag frames to reorder  •  Drop image files to add")
        hint.setStyleSheet("color: gray; font-style: italic; font-size: 11px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(hint)

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
        self._btn_rewind.setMinimumWidth(80)
        self._btn_rewind.setMinimumHeight(26)
        self._btn_rewind.setToolTip("Rewind to first frame (jump to frame 1)")
        self._btn_rewind.clicked.connect(self._rewind)
        pv_ctrl.addWidget(self._btn_rewind)

        self._btn_play = QPushButton("▶  Play")
        self._btn_play.setCheckable(True)
        self._btn_play.setToolTip("Start / stop the animated preview.  Space bar also works.")
        self._btn_play.toggled.connect(self._on_play_toggled)
        QShortcut(QKeySequence("Space"), self).activated.connect(
            lambda: self._btn_play.setChecked(not self._btn_play.isChecked())
        )
        pv_ctrl.addWidget(self._btn_play)

        self._preview_frame_lbl = QLabel("0 / 0")
        self._preview_frame_lbl.setObjectName("subheader")
        pv_ctrl.addWidget(self._preview_frame_lbl)
        pv_ctrl.addStretch()
        pv_layout.addLayout(pv_ctrl)

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
            self, "Add Images", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.tif *.gif "
            "*.ico *.ppm *.pcx *.tga *.avif);;All Files (*)",
        )
        if paths:
            self._add_paths(paths)

    def _add_paths(self, paths: list[str]) -> None:
        """Load image files and append their frames to the list."""
        progress = QProgressDialog("Loading images…", "Cancel", 0, len(paths), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(500)
        for i, path in enumerate(paths):
            progress.setValue(i)
            if progress.wasCanceled():
                break
            if Path(path).suffix.lower() not in _SUPPORTED_EXTS:
                continue
            try:
                pil_frames = _load_pillow_rgba(path)
            except Exception as exc:
                QMessageBox.warning(self, "Load Error",
                                    f"Could not load {Path(path).name}:\n{exc}")
                continue
            for frame_idx, pil_frame in enumerate(pil_frames):
                entry = _FrameEntry(path, frame_idx, pil_frame)
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
        self._frame_count_lbl.setText(f"{n} frame{'s' if n != 1 else ''}")

    # ------------------------------------------------------------------
    # Per-frame delay override (slider-based)
    # ------------------------------------------------------------------

    def _on_selection_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._frames):
            self._pf_check.blockSignals(True)
            self._pf_check.setChecked(False)
            self._pf_check.blockSignals(False)
            self._pf_slider.setEnabled(False)
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

    def _on_pf_check(self, checked: bool) -> None:
        self._pf_slider.setEnabled(checked)
        row = self._frame_list.currentRow()
        if 0 <= row < len(self._frames):
            self._frames[row].delay_ms = self._pf_slider.value() if checked else None

    def _on_pf_slider_changed(self, value: int) -> None:
        self._pf_val_lbl.setText(f"{value} ms")
        row = self._frame_list.currentRow()
        if 0 <= row < len(self._frames) and self._pf_check.isChecked():
            self._frames[row].delay_ms = value

    # ------------------------------------------------------------------
    # Global delay slider
    # ------------------------------------------------------------------

    def _on_delay_changed(self, value: int) -> None:
        self._delay_val_lbl.setText(f"{value} ms")
        if self._preview_timer.isActive():
            self._preview_timer.setInterval(max(10, value))

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
            return
        self._preview_idx = max(0, min(self._preview_idx, total - 1))
        entry = self._frames[self._preview_idx]
        pix = entry.thumbnail(
            max(60, self._preview_lbl.width() - 8),
            max(60, self._preview_lbl.height() - 8),
        )
        self._preview_lbl.setPixmap(pix)
        self._preview_frame_lbl.setText(f"{self._preview_idx + 1} / {total}")

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

        pil_frames: list[Image.Image] = []
        durations: list[int] = []
        try:
            for idx, entry in enumerate(self._frames):
                progress.setValue(idx)
                if progress.wasCanceled():
                    for f in pil_frames:
                        f.close()
                    return
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
                pil_frames[0].save(out_path, format="GIF", optimize=optimize)
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
            QMessageBox.critical(self, "Save Error", f"Could not save GIF:\n{exc}")
            return
        finally:
            for f in pil_frames:
                try:
                    f.close()
                except Exception:
                    pass

        self.exported.emit(out_path)
        QMessageBox.information(
            self, "GIF Saved",
            f"Animated GIF saved to:\n{out_path}\n\n"
            f"{len(self._frames)} frame(s), loop={loop if loop > 0 else '∞'}",
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._preview_timer.stop()
        super().closeEvent(event)
