"""
GIF Builder Dialog.

Lets the user build an animated GIF from scratch by selecting any mix of
image files (PNG, JPEG, WEBP, BMP, TIFF, GIF frame, etc.), ordering them,
setting per-frame or global delay, and previewing the animation live before
exporting.

Opening the dialog:
  • Selecting "GIF" in the Converter output combo and clicking Process
  • Right-clicking anywhere on the main window → "Open GIF Builder"
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (
    Qt, QTimer, QSize, QMimeData, pyqtSignal,
)
from PyQt6.QtGui import (
    QImage, QPixmap, QDragEnterEvent, QDropEvent, QDragMoveEvent,
    QIcon, QKeySequence, QShortcut,
)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFileDialog, QSpinBox,
    QCheckBox, QGroupBox, QGridLayout, QMessageBox,
    QProgressDialog, QSplitter, QWidget, QScrollArea,
    QAbstractSpinBox, QFrame, QSizePolicy,
)

# Supported input extensions (what PIL can open)
_SUPPORTED_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif",
    ".gif", ".ico", ".ppm", ".pcx", ".tga", ".avif",
}

_THUMB_W = 120
_THUMB_H = 100


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
    from PIL import Image
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
    """Drag-to-reorder list widget that also accepts dropped image files."""

    files_dropped = pyqtSignal(list)   # list[str]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setIconSize(QSize(_THUMB_W, _THUMB_H))
        self.setSpacing(4)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setWrapping(True)

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


class GifBuilderDialog(QDialog):
    """Full-featured animated GIF builder.

    Provides:
    • Add images from any supported format (PNG, JPEG, WEBP, GIF, etc.)
    • Drag-and-drop support for both file-drop and internal reordering
    • Global frame delay + optional per-frame overrides
    • Loop count (0 = infinite)
    • Live preview with play / pause
    • Export (Process) to a user-chosen GIF file

    :param initial_files: Optional list of file paths to pre-populate.
    :param parent:        Optional parent widget.
    """

    # Emitted after a successful export with the output path
    exported = pyqtSignal(str)

    def __init__(self, initial_files: Optional[list[str]] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GIF Builder")
        self.setMinimumSize(820, 600)
        self.setModal(False)
        self._frames: list[_FrameEntry] = []
        self._preview_idx: int = 0
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._advance_preview)
        self._build_ui()
        if initial_files:
            self._add_paths(initial_files)
        QShortcut(QKeySequence("Delete"), self).activated.connect(self._remove_selected)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Title
        title = QLabel("🎞  GIF Builder")
        title.setObjectName("subheader")
        root.addWidget(title)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ---- Left side: frame list + controls ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        # Toolbar: add / remove / move
        tb = QHBoxLayout()
        self._btn_add = QPushButton("➕ Add Images")
        self._btn_add.setToolTip(
            "Add one or more images to the GIF.\n"
            "Supports PNG, JPEG, WEBP, BMP, TIFF, GIF (frames), ICO, and more."
        )
        self._btn_add.clicked.connect(self._on_add_clicked)
        tb.addWidget(self._btn_add)

        self._btn_remove = QPushButton("🗑 Remove")
        self._btn_remove.setToolTip("Remove the selected frames from the list.")
        self._btn_remove.clicked.connect(self._remove_selected)
        tb.addWidget(self._btn_remove)

        self._btn_up = QPushButton("⬆ Up")
        self._btn_up.setToolTip("Move selected frame earlier in the sequence.")
        self._btn_up.clicked.connect(self._move_up)
        tb.addWidget(self._btn_up)

        self._btn_down = QPushButton("⬇ Down")
        self._btn_down.setToolTip("Move selected frame later in the sequence.")
        self._btn_down.clicked.connect(self._move_down)
        tb.addWidget(self._btn_down)

        self._btn_clear = QPushButton("✖ Clear All")
        self._btn_clear.setToolTip("Remove all frames.")
        self._btn_clear.clicked.connect(self._clear_all)
        tb.addWidget(self._btn_clear)
        tb.addStretch()
        self._frame_count_lbl = QLabel("0 frames")
        self._frame_count_lbl.setObjectName("subheader")
        tb.addWidget(self._frame_count_lbl)
        left_layout.addLayout(tb)

        # Frame grid
        self._frame_list = _FrameListWidget()
        self._frame_list.files_dropped.connect(self._add_paths)
        self._frame_list.currentRowChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self._frame_list, 1)

        # Per-frame delay override
        pf_box = QGroupBox("Per-Frame Delay Override")
        pf_layout = QHBoxLayout(pf_box)
        self._pf_check = QCheckBox("Override delay for selected frame:")
        self._pf_check.toggled.connect(self._on_pf_check)
        pf_layout.addWidget(self._pf_check)
        self._pf_spin = QSpinBox()
        self._pf_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._pf_spin.setRange(10, 60000)
        self._pf_spin.setValue(100)
        self._pf_spin.setSuffix(" ms")
        self._pf_spin.setEnabled(False)
        self._pf_spin.valueChanged.connect(self._on_pf_delay_changed)
        pf_layout.addWidget(self._pf_spin)
        pf_layout.addStretch()
        left_layout.addWidget(pf_box)

        splitter.addWidget(left)

        # ---- Right side: settings + preview ----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        # Global settings group
        grp_settings = QGroupBox("GIF Settings")
        sl = QGridLayout(grp_settings)
        sl.setHorizontalSpacing(10)
        sl.setVerticalSpacing(6)

        sl.addWidget(QLabel("Global Frame Delay:"), 0, 0)
        self._delay_spin = QSpinBox()
        self._delay_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._delay_spin.setRange(10, 60000)
        self._delay_spin.setValue(100)
        self._delay_spin.setSuffix(" ms")
        self._delay_spin.setToolTip(
            "Duration each frame is shown when no per-frame delay is set.\n"
            "100 ms = 10 fps, 50 ms ≈ 20 fps, 33 ms ≈ 30 fps."
        )
        sl.addWidget(self._delay_spin, 0, 1)

        sl.addWidget(QLabel("Loop count:"), 1, 0)
        self._loop_spin = QSpinBox()
        self._loop_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._loop_spin.setRange(0, 9999)
        self._loop_spin.setValue(0)
        self._loop_spin.setToolTip("0 = loop forever.  1 = play once.  N = repeat N times.")
        sl.addWidget(self._loop_spin, 1, 1)

        sl.addWidget(QLabel("Max width (0 = original):"), 2, 0)
        self._width_spin = QSpinBox()
        self._width_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._width_spin.setRange(0, 32768)
        self._width_spin.setValue(0)
        self._width_spin.setSuffix(" px")
        self._width_spin.setToolTip("Resize all frames to this width (aspect-ratio preserved). 0 = no resize.")
        sl.addWidget(self._width_spin, 2, 1)

        sl.addWidget(QLabel("Max height (0 = original):"), 3, 0)
        self._height_spin = QSpinBox()
        self._height_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._height_spin.setRange(0, 32768)
        self._height_spin.setValue(0)
        self._height_spin.setSuffix(" px")
        self._height_spin.setToolTip("Resize all frames to this height (aspect-ratio preserved). 0 = no resize.")
        sl.addWidget(self._height_spin, 3, 1)

        self._optimize_check = QCheckBox("Optimize (smaller file, slower export)")
        self._optimize_check.setChecked(True)
        sl.addWidget(self._optimize_check, 4, 0, 1, 2)

        right_layout.addWidget(grp_settings)

        # Preview group
        grp_preview = QGroupBox("Live Preview")
        pv_layout = QVBoxLayout(grp_preview)

        self._preview_lbl = QLabel()
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_lbl.setMinimumSize(260, 200)
        self._preview_lbl.setFrameShape(QFrame.Shape.StyledPanel)
        self._preview_lbl.setText("(no frames yet)")
        pv_layout.addWidget(self._preview_lbl, 1)

        pv_ctrl = QHBoxLayout()
        self._btn_play = QPushButton("▶ Play")
        self._btn_play.setCheckable(True)
        self._btn_play.setToolTip("Start / stop the animated preview.")
        self._btn_play.toggled.connect(self._on_play_toggled)
        pv_ctrl.addWidget(self._btn_play)
        self._preview_frame_lbl = QLabel("Frame 0 / 0")
        self._preview_frame_lbl.setObjectName("subheader")
        pv_ctrl.addWidget(self._preview_frame_lbl)
        pv_ctrl.addStretch()
        pv_layout.addLayout(pv_ctrl)

        right_layout.addWidget(grp_preview, 1)

        # Export row
        export_row = QHBoxLayout()
        self._btn_export = QPushButton("💾 Export GIF…")
        self._btn_export.setToolTip(
            "Build and save the animated GIF to a file you choose.\n"
            "Shortcut: Ctrl+S"
        )
        self._btn_export.clicked.connect(self._export)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._export)
        export_row.addStretch()
        export_row.addWidget(self._btn_export)
        right_layout.addLayout(export_row)

        splitter.addWidget(right)
        splitter.setSizes([500, 320])

    # ------------------------------------------------------------------
    # Frame management
    # ------------------------------------------------------------------

    def _on_add_clicked(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Images",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.tif *.gif "
            "*.ico *.ppm *.pcx *.tga *.avif);;All Files (*)",
        )
        if paths:
            self._add_paths(paths)

    def _add_paths(self, paths: list[str]) -> None:
        """Load image files and append their frames to the list."""
        progress = QProgressDialog("Loading images…", "Cancel", 0, len(paths), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(600)
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
                    label += f" [{frame_idx + 1}]"
                item.setText(label)
                item.setToolTip(f"{path}\nFrame {frame_idx + 1} / {len(pil_frames)}")
                self._frame_list.addItem(item)
        progress.setValue(len(paths))
        self._update_count()
        self._update_preview_frame()

    def _remove_selected(self) -> None:
        rows = sorted(
            {self._frame_list.row(item) for item in self._frame_list.selectedItems()},
            reverse=True,
        )
        for row in rows:
            self._frames[row].close()
            del self._frames[row]
            self._frame_list.takeItem(row)
        self._update_count()
        self._update_preview_frame()

    def _move_up(self) -> None:
        rows = sorted({self._frame_list.row(item) for item in self._frame_list.selectedItems()})
        if not rows or rows[0] == 0:
            return
        for row in rows:
            self._frames[row - 1], self._frames[row] = self._frames[row], self._frames[row - 1]
            item = self._frame_list.takeItem(row)
            self._frame_list.insertItem(row - 1, item)
        self._frame_list.clearSelection()
        for row in rows:
            self._frame_list.item(row - 1).setSelected(True)
        self._update_preview_frame()

    def _move_down(self) -> None:
        rows = sorted(
            {self._frame_list.row(item) for item in self._frame_list.selectedItems()},
            reverse=True,
        )
        if not rows or rows[0] >= len(self._frames) - 1:
            return
        for row in rows:
            self._frames[row], self._frames[row + 1] = self._frames[row + 1], self._frames[row]
            item = self._frame_list.takeItem(row)
            self._frame_list.insertItem(row + 1, item)
        self._frame_list.clearSelection()
        for row in rows:
            self._frame_list.item(row + 1).setSelected(True)
        self._update_preview_frame()

    def _clear_all(self) -> None:
        for entry in self._frames:
            entry.close()
        self._frames.clear()
        self._frame_list.clear()
        self._update_count()
        self._update_preview_frame()

    def _update_count(self) -> None:
        n = len(self._frames)
        self._frame_count_lbl.setText(f"{n} frame{'s' if n != 1 else ''}")

    # ------------------------------------------------------------------
    # Per-frame delay override
    # ------------------------------------------------------------------

    def _on_selection_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._frames):
            self._pf_check.blockSignals(True)
            self._pf_check.setChecked(False)
            self._pf_check.blockSignals(False)
            self._pf_spin.setEnabled(False)
            return
        delay = self._frames[row].delay_ms
        self._pf_check.blockSignals(True)
        self._pf_spin.blockSignals(True)
        self._pf_check.setChecked(delay is not None)
        self._pf_spin.setValue(delay if delay is not None else self._delay_spin.value())
        self._pf_spin.setEnabled(delay is not None)
        self._pf_check.blockSignals(False)
        self._pf_spin.blockSignals(False)

    def _on_pf_check(self, checked: bool) -> None:
        self._pf_spin.setEnabled(checked)
        row = self._frame_list.currentRow()
        if 0 <= row < len(self._frames):
            self._frames[row].delay_ms = self._pf_spin.value() if checked else None

    def _on_pf_delay_changed(self, value: int) -> None:
        row = self._frame_list.currentRow()
        if 0 <= row < len(self._frames) and self._pf_check.isChecked():
            self._frames[row].delay_ms = value

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _on_play_toggled(self, playing: bool) -> None:
        if playing:
            if len(self._frames) < 2:
                self._btn_play.setChecked(False)
                return
            self._preview_timer.start(self._delay_spin.value())
            self._btn_play.setText("⏸ Pause")
        else:
            self._preview_timer.stop()
            self._btn_play.setText("▶ Play")

    def _advance_preview(self) -> None:
        if not self._frames:
            self._preview_timer.stop()
            self._btn_play.setChecked(False)
            return
        self._preview_idx = (self._preview_idx + 1) % len(self._frames)
        self._update_preview_frame()
        # Honour per-frame delay for next tick
        entry = self._frames[self._preview_idx]
        interval = entry.delay_ms if entry.delay_ms is not None else self._delay_spin.value()
        self._preview_timer.setInterval(max(10, interval))

    def _update_preview_frame(self) -> None:
        total = len(self._frames)
        if total == 0:
            self._preview_lbl.setText("(no frames yet)")
            self._preview_frame_lbl.setText("Frame 0 / 0")
            return
        self._preview_idx = max(0, min(self._preview_idx, total - 1))
        entry = self._frames[self._preview_idx]
        pix = entry.thumbnail(
            self._preview_lbl.width() - 8,
            self._preview_lbl.height() - 8,
        )
        self._preview_lbl.setPixmap(pix)
        self._preview_frame_lbl.setText(f"Frame {self._preview_idx + 1} / {total}")

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
            self, "Save Animated GIF",
            "animation.gif",
            "GIF Files (*.gif);;All Files (*)",
        )
        if not out_path:
            return
        if not out_path.lower().endswith(".gif"):
            out_path += ".gif"

        from PIL import Image

        max_w = self._width_spin.value()
        max_h = self._height_spin.value()
        global_delay = self._delay_spin.value()
        loop = self._loop_spin.value()
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
                # Optional resize
                if max_w > 0 or max_h > 0:
                    target_w = max_w if max_w > 0 else 99999
                    target_h = max_h if max_h > 0 else 99999
                    frame.thumbnail((target_w, target_h), Image.LANCZOS)
                # Quantize to palette for smaller GIF
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
            QMessageBox.critical(self, "Build Error",
                                 f"Error preparing frames:\n{exc}")
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
