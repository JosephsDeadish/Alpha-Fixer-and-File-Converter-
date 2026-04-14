"""
GIF Frame Picker Dialog.

Shown when the user converts an animated GIF to another format.
Displays thumbnail previews of every frame so the user can choose
which frames to export.  "Select All" / "Deselect All" shortcuts
and a frame-count badge make it easy to work with long animations.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QGridLayout, QCheckBox, QFrame,
    QDialogButtonBox,
)

# Thumbnail dimensions (pixels).  Frames are scaled proportionally to fit.
_THUMB_W = 96
_THUMB_H = 96

# Number of thumbnails per row in the grid.
_COLS = 6


def _pil_to_qpixmap(pil_img) -> QPixmap:
    """Convert a PIL Image to a QPixmap, handling any mode."""
    from PIL import Image
    rgba = pil_img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qi = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qi)


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
        self._frame_count = 0
        self.setWindowTitle(f"Select GIF Frames — {Path(path).name}")
        self.setMinimumWidth(560)
        self.setMinimumHeight(400)
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
        root.addWidget(self._info_lbl)

        # Toolbar: Select All / Deselect All / invert
        tool_row = QHBoxLayout()
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
            btn.setFixedHeight(28)
            tool_row.addWidget(btn)
        tool_row.addStretch()
        self._sel_lbl = QLabel("0 / 0 selected")
        self._sel_lbl.setObjectName("subheader")
        tool_row.addWidget(self._sel_lbl)
        root.addLayout(tool_row)

        self._btn_all.clicked.connect(self._select_all)
        self._btn_none.clicked.connect(self._deselect_all)
        self._btn_invert.clicked.connect(self._invert_selection)

        # Scrollable grid of frame thumbnails
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidget(self._grid_widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.StyledPanel)
        root.addWidget(scroll, 1)

        # OK / Cancel
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.setToolTip(
            "OK exports the checked frames. Cancel abandons ship entirely. Choose your destiny."
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

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
                composite = canvas.copy()
                composite.paste(curr, (0, 0), curr)
                curr.close()
                frames.append(composite.copy())

                disposal = gif.info.get('disposal', 0)
                canvas.close()
                if disposal == 2:
                    # Restore-to-background: next frame starts on a blank canvas.
                    canvas = Image.new("RGBA", gif_size, (0, 0, 0, 0))
                    composite.close()
                else:
                    # disposal 0, 1, 3 – keep current composite as the base
                    # for the next frame (disposal=3 "restore-to-previous" is
                    # approximated as "keep" which is correct for the vast
                    # majority of animated GIFs in the wild).
                    canvas = composite
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
            frame.thumbnail((_THUMB_W, _THUMB_H), Image.LANCZOS)
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
            cb.setChecked(True)
            cb.toggled.connect(self._update_selection_label)
            cell_layout.addWidget(cb, 0, Qt.AlignmentFlag.AlignHCenter)

            self._checkboxes.append(cb)

            row, col = divmod(idx, _COLS)
            self._grid.addWidget(cell, row, col)

        self._update_selection_label()

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

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def selected_indices(self) -> list[int]:
        """Return 0-based indices of all checked frames (in order)."""
        return [i for i, cb in enumerate(self._checkboxes) if cb.isChecked()]
