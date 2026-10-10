"""
GIF Frame Picker Dialog.

Shown when the user converts an animated GIF to another format.
Displays thumbnail previews of every frame so the user can choose
which frames to export.  "Select All" / "Deselect All" shortcuts
and a frame-count badge make it easy to work with long animations.
"""
from __future__ import annotations

from pathlib import Path

from ._ui_utils import fit_dialog_to_screen

from PyQt6.QtCore import Qt, QSize, QTimer, QEvent
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QGridLayout, QCheckBox, QFrame,
    QDialogButtonBox, QBoxLayout,
)

# Thumbnail dimensions (pixels).  Frames are scaled proportionally to fit.
_THUMB_W = 96
_THUMB_H = 96

# Maximum number of thumbnails per row.
_COLS = 6


def _pil_to_qpixmap(pil_img) -> QPixmap:
    """Convert a PIL Image to a QPixmap, handling any mode."""
    from PIL import Image
    rgba = pil_img.convert("RGBA")
    try:
        data = rgba.tobytes("raw", "RGBA")
        qi = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qi)
    finally:
        rgba.close()


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


class GifFramePickerDialog(QDialog):
    """
    Modal dialog that shows thumbnail previews of every frame in an animated
    GIF and lets the user tick the frames they want to export.

    :param path:        Absolute path to the GIF file.
    :param parent:      Optional parent widget.
    """

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self._checkboxes: list[QCheckBox] = []
        self._cells: list[QWidget] = []
        self._columns = 0
        self._frame_count = 0
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._reflow)
        self.setWindowTitle(f"Select GIF Frames — {Path(path).name}")
        self.setMinimumSize(320, 240)
        self.resize(700, 480)
        self.setModal(True)
        self._build_ui()
        self._load_frames()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Info label
        self._info_lbl = QLabel("Loading frames…")
        self._info_lbl.setObjectName("subheader")
        self._info_lbl.setWordWrap(True)
        self._info_lbl.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self._info_lbl)

        # Toolbar: Select All / Deselect All / invert
        tool_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._tool_row = tool_row
        self._btn_all = QPushButton("Select All")
        self._btn_all.setToolTip(
            "Check all frames for export. Every single one. The whole family is coming."
        )
        self._btn_none = QPushButton("Deselect All")
        self._btn_none.setToolTip(
            "Uncheck everything. Fresh slate. No frames selected. Bold strategy, let's see if it pays off."
        )
        self._btn_invert = QPushButton("Invert")
        self._btn_invert.setToolTip(
            "Flip every frame's selection. The ones you wanted are now unwanted. Chaos theory in action."
        )
        for btn in (self._btn_all, self._btn_none, self._btn_invert):
            btn.setMinimumHeight(28)
            btn.setAutoDefault(False)
            tool_row.addWidget(btn)
        self._sel_lbl = QLabel("0 / 0 selected")
        self._sel_lbl.setObjectName("subheader")
        root.addLayout(tool_row)
        root.addWidget(self._sel_lbl)

        self._btn_all.clicked.connect(self._select_all)
        self._btn_none.clicked.connect(self._deselect_all)
        self._btn_invert.clicked.connect(self._invert_selection)

        # Scrollable grid of frame thumbnails
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setSpacing(8)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._grid_widget.installEventFilter(self)

        scroll = QScrollArea()
        self._scroll = scroll
        scroll.setAccessibleName("GIF frames")
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.viewport().installEventFilter(self)
        scroll.setWidget(self._grid_widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.StyledPanel)
        root.addWidget(scroll, 1)

        # OK / Cancel
        self._btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._btn_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._btn_box.setToolTip(
            "OK exports the checked frames. Cancel abandons ship entirely. Choose your destiny."
        )
        self._btn_box.accepted.connect(self.accept)
        self._btn_box.rejected.connect(self.reject)
        root.addWidget(self._btn_box)

    # ------------------------------------------------------------------
    # Frame loading
    # ------------------------------------------------------------------

    def _load_frames(self):
        """Extract frame thumbnails from the GIF and populate the grid."""
        from PIL import Image

        gif = None
        frames = []
        try:
            gif = Image.open(self._path)
            n_frames = getattr(gif, 'n_frames', 1)
            gif_size = gif.size

            # Build properly composited RGBA frames.
            #
            # Simply copying while iterating (f.copy()) leaves each copy
            # lazily bound to the same underlying file handle.  After the
            # file is closed the deferred .convert("RGBA") call decodes
            # from whatever seek position the handle is at — almost always
            # the final frame, making every thumbnail look identical.
            #
            # The correct approach is to composite each frame onto an
            # accumulating RGBA canvas and capture the result immediately,
            # honouring GIF disposal methods so delta-encoded frames
            # (where only changed pixels are stored) display correctly.
            canvas = Image.new("RGBA", gif_size, (0, 0, 0, 0))
            for frame_no in range(n_frames):
                gif.seek(frame_no)
                curr = gif.convert("RGBA")   # force-decode at this position
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

                disposal = getattr(gif, "disposal_method", gif.info.get('disposal', 0))
                canvas.close()
                if disposal == 2:
                    # Restore-to-background: next frame starts on a blank canvas.
                    canvas = Image.new("RGBA", gif_size, (0, 0, 0, 0))
                    composite.close()
                    previous_canvas.close()
                elif disposal == 3:
                    canvas = previous_canvas
                    composite.close()
                else:
                    # disposal 0, 1 – keep current composite as the base.
                    canvas = composite
                    previous_canvas.close()
            canvas.close()
            self._frame_count = len(frames)
        except Exception as exc:
            self._info_lbl.setText(f"Error reading frames: {exc}")
            for f in frames:
                try:
                    f.close()
                except Exception:
                    pass
            return
        finally:
            if gif is not None:
                gif.close()

        self._info_lbl.setText(
            f"{Path(self._path).name}  —  {self._frame_count} frame"
            f"{'s' if self._frame_count != 1 else ''}"
        )

        for idx, frame in enumerate(frames):
            # frame is already RGBA; thumbnail in-place then build pixmap.
            frame.thumbnail((_THUMB_W, _THUMB_H), Image.Resampling.LANCZOS)
            pixmap = _pil_to_qpixmap(frame)
            frame.close()

            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(2, 2, 2, 2)
            cell_layout.setSpacing(2)
            cell_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

            img_lbl = QLabel()
            img_lbl.setFixedSize(QSize(_THUMB_W, _THUMB_H))
            img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            img_lbl.setPixmap(pixmap)
            cell_layout.addWidget(img_lbl)

            cb = QCheckBox(f"Frame {idx + 1}")
            cb.setAccessibleDescription(
                f"Select frame {idx + 1} for export. Press Space to toggle selection."
            )
            cb.setChecked(True)
            cb.installEventFilter(self)
            cb.toggled.connect(self._update_selection_label)
            cell_layout.addWidget(cb, 0, Qt.AlignmentFlag.AlignHCenter)

            self._checkboxes.append(cb)
            self._cells.append(cell)

            row, col = divmod(idx, _COLS)
            self._grid.addWidget(cell, row, col)

        self._columns = _COLS
        previous = self._btn_invert
        for cb in self._checkboxes:
            QWidget.setTabOrder(previous, cb)
            previous = cb
        QWidget.setTabOrder(
            previous, self._btn_box.button(QDialogButtonBox.StandardButton.Ok)
        )
        self._layout_timer.start(0)
        self._update_selection_label()

    def eventFilter(self, watched, event):
        if (watched is self._scroll.viewport()
                and event.type() == QEvent.Type.Resize):
            self._layout_timer.start(0)
        elif (watched is self._grid_widget
              and event.type() == QEvent.Type.LayoutRequest):
            self._layout_timer.start(0)
        elif isinstance(watched, QCheckBox) and event.type() == QEvent.Type.FocusIn:
            self._reveal_frame(watched)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_timer.start(0)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._layout_timer.start(0)

    def _reveal_frame(self, checkbox):
        self._scroll.ensureWidgetVisible(checkbox.parentWidget(), 4, 4)
        # A short viewport may fit the checkbox but not the whole thumbnail.
        self._scroll.ensureWidgetVisible(checkbox, 4, 4)

    def _reflow(self):
        buttons = (self._btn_all, self._btn_none, self._btn_invert)
        toolbar_width = sum(btn.sizeHint().width() for btn in buttons)
        toolbar_width += self._tool_row.spacing() * (len(buttons) - 1)
        margins = self.layout().contentsMargins()
        available = self.width() - margins.left() - margins.right()
        self._tool_row.setDirection(
            QBoxLayout.Direction.TopToBottom if toolbar_width > available
            else QBoxLayout.Direction.LeftToRight
        )
        if not self._cells:
            return
        margins = self._grid.contentsMargins()
        available = self._scroll.viewport().width() - margins.left() - margins.right()
        cell_width = max(cell.minimumSizeHint().width() for cell in self._cells)
        spacing = self._grid.horizontalSpacing()
        columns = max(1, min(_COLS, (available + spacing) // (cell_width + spacing)))
        if columns != self._columns:
            for cell in self._cells:
                self._grid.removeWidget(cell)
            for idx, cell in enumerate(self._cells):
                self._grid.addWidget(cell, *divmod(idx, columns))
            self._columns = columns
            self._grid.activate()
        focused = self.focusWidget()
        if focused in self._checkboxes:
            self._reveal_frame(focused)

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _select_all(self):
        for cb in self._checkboxes:
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)
        self._update_selection_label()

    def _deselect_all(self):
        for cb in self._checkboxes:
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self._update_selection_label()

    def _invert_selection(self):
        for cb in self._checkboxes:
            cb.blockSignals(True)
            cb.setChecked(not cb.isChecked())
            cb.blockSignals(False)
        self._update_selection_label()

    def _update_selection_label(self):
        selected = sum(1 for cb in self._checkboxes if cb.isChecked())
        total = len(self._checkboxes)
        self._sel_lbl.setText(f"{selected} / {total} selected")
        self._btn_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(selected > 0)

    def accept(self):
        if self.selected_indices():
            super().accept()

    def showEvent(self, event):
        super().showEvent(event)
        fit_dialog_to_screen(self)
        QTimer.singleShot(0, lambda: fit_dialog_to_screen(self))

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def selected_indices(self) -> list[int]:
        """Return 0-based indices of all checked frames (in order)."""
        return [i for i, cb in enumerate(self._checkboxes) if cb.isChecked()]
