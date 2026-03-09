"""
ImagePreviewPane – a compact widget that shows a thumbnail and basic metadata
for the currently-selected file in either the Alpha Fixer or Converter tab.

The preview is generated in a background thread so it never blocks the UI.
"""
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap, QImage, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QSizePolicy, QFrame,
)


# ---------------------------------------------------------------------------
# Background loader
# ---------------------------------------------------------------------------

class _ThumbLoader(QThread):
    """Load + scale an image to a thumbnail in a worker thread."""
    loaded = pyqtSignal(QImage, str)   # (thumbnail_qimage, metadata_text)
    failed = pyqtSignal(str)           # error message

    def __init__(self, path: str, max_size: int = 260):
        super().__init__()
        self._path = path
        self._max_size = max_size

    def run(self):
        try:
            from PIL import Image
            img = Image.open(self._path)
            mode = img.mode
            width, height = img.size
            file_size = os.path.getsize(self._path)

            # Thumbnail (keep aspect ratio, RGBA for full fidelity)
            img.thumbnail((self._max_size, self._max_size), Image.LANCZOS)
            img_rgba = img.convert("RGBA")

            data = img_rgba.tobytes("raw", "RGBA")
            qimg = QImage(data, img_rgba.width, img_rgba.height, QImage.Format.Format_RGBA8888)
            qimg = qimg.copy()  # detach from the bytes buffer

            size_str = _fmt_size(file_size)
            meta = (
                f"{Path(self._path).name}\n"
                f"{width} × {height}  ·  {mode}\n"
                f"{size_str}"
            )
            self.loaded.emit(qimg, meta)
        except Exception as exc:
            self.failed.emit(str(exc))


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 ** 2:.1f} MB"


# ---------------------------------------------------------------------------
# Preview pane widget
# ---------------------------------------------------------------------------

class ImagePreviewPane(QWidget):
    """
    Drop-in side-panel.  Call ``show_file(path)`` to load a preview.
    Call ``clear()`` to reset to the placeholder.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loader: _ThumbLoader | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Title
        title = QLabel("Preview")
        title.setObjectName("section")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Image frame
        frame = QFrame()
        frame.setObjectName("card")
        frame.setFrameShape(QFrame.Shape.Box)
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(4, 4, 4, 4)

        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setMinimumSize(200, 200)
        self._img_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._img_label.setScaledContents(False)
        fl.addWidget(self._img_label)
        layout.addWidget(frame, 1)

        # Metadata label
        self._meta_label = QLabel("Select a file to preview")
        self._meta_label.setObjectName("subheader")
        self._meta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._meta_label.setWordWrap(True)
        layout.addWidget(self._meta_label)

        self._set_placeholder()

    def _set_placeholder(self):
        """Show a grey checkerboard placeholder."""
        size = 200
        placeholder = QImage(size, size, QImage.Format.Format_ARGB32)
        sq = 20
        for row in range(0, size, sq):
            for col in range(0, size, sq):
                color = QColor("#3a3a4a") if (row // sq + col // sq) % 2 == 0 else QColor("#2a2a3a")
                for y in range(row, min(row + sq, size)):
                    for x in range(col, min(col + sq, size)):
                        placeholder.setPixelColor(x, y, color)
        self._img_label.setPixmap(QPixmap.fromImage(placeholder))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_file(self, path: str):
        """Start loading a thumbnail for *path* in a background thread."""
        if not path or not os.path.isfile(path):
            self.clear()
            return

        # Cancel previous loader if still running
        if self._loader and self._loader.isRunning():
            self._loader.quit()
            self._loader.wait(500)

        self._meta_label.setText("Loading…")
        self._loader = _ThumbLoader(path)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)
        self._loader.start()

    def clear(self):
        """Reset to placeholder state."""
        self._set_placeholder()
        self._meta_label.setText("Select a file to preview")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_loaded(self, qimg: QImage, meta: str):
        pix = QPixmap.fromImage(qimg)
        available = self._img_label.size()
        scaled = pix.scaled(
            available,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._img_label.setPixmap(scaled)
        self._meta_label.setText(meta)

    def _on_failed(self, err: str):
        self._set_placeholder()
        self._meta_label.setText(f"Preview unavailable\n{err[:80]}")
