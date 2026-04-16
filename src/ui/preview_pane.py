"""
preview_pane.py – image preview components.

• ImagePreviewPane   – compact thumbnail + metadata panel (used by Converter tab).
• BeforeAfterWidget  – side-by-side comparison with a draggable divider
                       (used by Alpha & RGBA Adjuster tab).

All image loading is done in background QThreads so the UI is never blocked.
"""
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QRect, QSize, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QFont, QFontMetrics,
    QPixmap, QImage, QColor, QMovie,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QSizePolicy, QFrame,
    QPushButton, QHBoxLayout, QDialog,
)


# ---------------------------------------------------------------------------
# Floating zoom overlay reused by preview widgets
# ---------------------------------------------------------------------------

class _ZoomOverlayBar(QFrame):
    """Compact semi-transparent zoom control bar (－ / ⊡ / ＋).

    Create as a *child* of the widget it should float over, then call
    ``reposition(parent_size)`` in the parent's ``resizeEvent`` to keep it
    pinned to the top-right corner.
    """

    def __init__(self, zoom_in_cb, zoom_out_cb, zoom_fit_cb, parent=None):
        super().__init__(parent)
        self.setObjectName("zoomOverlayBar")
        self.setStyleSheet(
            "QFrame#zoomOverlayBar {"
            "  background: rgba(20, 20, 20, 155);"
            "  border-radius: 6px;"
            "  border: 1px solid rgba(255,255,255,35);"
            "}"
            "QPushButton {"
            "  background: rgba(55,55,55,190);"
            "  color: #eee;"
            "  border: none;"
            "  border-radius: 4px;"
            "  font-size: 13px;"
            "  min-width: 24px; max-width: 24px;"
            "  min-height: 20px; max-height: 20px;"
            "  padding: 0;"
            "}"
            "QPushButton:hover  { background: rgba(95,95,95,210); }"
            "QPushButton:pressed{ background: rgba(35,35,35,240); }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 2, 3, 2)
        row.setSpacing(3)
        for label, tip, cb in [
            ("－", "Zoom out  (Ctrl + scroll-down)", zoom_out_cb),
            ("⊡", "Reset zoom / fit to window",      zoom_fit_cb),
            ("＋", "Zoom in  (Ctrl + scroll-up)",     zoom_in_cb),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(cb)
            row.addWidget(btn)
        self.adjustSize()
        self.raise_()

    def reposition(self, parent_size) -> None:
        """Pin to top-right corner of *parent_size*."""
        margin = 6
        self.move(parent_size.width() - self.width() - margin, margin)


def _pil_to_qimage(img) -> QImage:
    """Convert any PIL Image to a detached RGBA QImage."""
    # Only create a new RGBA image when the mode is not already RGBA so we
    # can close the temporary conversion image and release its backing store
    # early instead of relying on the garbage collector.
    img_rgba = img.convert("RGBA") if img.mode != "RGBA" else img
    try:
        data = img_rgba.tobytes("raw", "RGBA")
        qimg = QImage(data, img_rgba.width, img_rgba.height,
                      QImage.Format.Format_RGBA8888)
        return qimg.copy()  # detach from the bytes buffer
    finally:
        if img_rgba is not img:
            img_rgba.close()


def _make_checker(w: int, h: int, sq: int = 12) -> QPixmap:
    """Render a w×h checkerboard pixmap.

    Parameters
    ----------
    w, h : int   Pixel dimensions of the output pixmap.
    sq   : int   Side length of each checker square in pixels (default 12).
    """
    w, h = max(w, 1), max(h, 1)
    pix = QPixmap(w, h)
    p = QPainter(pix)
    c1, c2 = QColor("#3a3a4a"), QColor("#2a2a3a")
    for row in range(0, h, sq):
        for col in range(0, w, sq):
            color = c1 if (row // sq + col // sq) % 2 == 0 else c2
            p.fillRect(col, row, min(sq, w - col), min(sq, h - row), color)
    p.end()
    return pix


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 ** 2:.1f} MB"


# ---------------------------------------------------------------------------
# Background loader – thumbnail only (used by ImagePreviewPane)
# ---------------------------------------------------------------------------

#: Formats that use the ``quality`` parameter when saving.
_QUALITY_FORMATS = {"JPEG", "WEBP"}


class _ThumbLoader(QThread):
    """Load + scale an image to a thumbnail in a worker thread."""
    loaded = pyqtSignal(QImage, str)   # (thumbnail_qimage, metadata_text)
    failed = pyqtSignal(str)           # error message

    def __init__(self, path: str, max_size: int = 512):
        super().__init__()
        self._path = path
        self._max_size = max_size
        self._abort = False

    def stop(self) -> None:
        """Request that the thread abandon work as soon as it can check."""
        self._abort = True

    def run(self):
        img = None
        try:
            from PIL import Image
            from src.core.file_converter import _open_image
            img = _open_image(self._path)
            mode = img.mode
            width, height = img.size
            file_size = os.path.getsize(self._path)

            # Bail out if a newer file has been selected before we decoded.
            if self._abort:
                return

            # Check for alpha channel
            has_alpha = mode in ("RGBA", "LA", "PA") or (
                mode == "P" and img.info.get("transparency") is not None
            )
            alpha_note = "  ·  α" if has_alpha else ""

            # Check for embedded metadata
            meta_keys = []
            if "exif" in img.info:
                meta_keys.append("EXIF")
            if "icc_profile" in img.info:
                meta_keys.append("ICC")
            if "dpi" in img.info:
                dpi = img.info["dpi"]
                meta_keys.append(f"DPI {dpi[0]:.0f}×{dpi[1]:.0f}")
            meta_note = ("  ·  " + "/".join(meta_keys)) if meta_keys else ""

            img.thumbnail((self._max_size, self._max_size), Image.LANCZOS)
            qimg = _pil_to_qimage(img)

            meta_text = (
                f"{Path(self._path).name}\n"
                f"{width} × {height}  ·  {mode}{alpha_note}\n"
                f"{_fmt_size(file_size)}{meta_note}"
            )
            try:
                self.loaded.emit(qimg, meta_text)
            except RuntimeError:
                pass  # receiver destroyed; nothing to do
        except Exception as exc:
            try:
                self.failed.emit(str(exc))
            except RuntimeError:
                pass  # receiver destroyed; nothing to do
        finally:
            if img is not None:
                img.close()


class _ConvertedThumbLoader(QThread):
    """Load, convert in-memory to *target_fmt*, then scale to thumbnail.

    Used by :meth:`ImagePreviewPane.show_converted` to give a live preview
    of what the output file will look like after conversion (especially
    useful for lossy formats like JPEG/WEBP where quality matters).
    """
    loaded = pyqtSignal(QImage, str)
    failed = pyqtSignal(str)

    def __init__(self, path: str, target_fmt: str, quality: int,
                 max_size: int = 512):
        super().__init__()
        self._path = path
        self._target_fmt = target_fmt.upper()
        self._quality = quality
        self._max_size = max_size
        self._abort = False

    def stop(self) -> None:
        """Request that the thread abandon work as soon as it can check."""
        self._abort = True

    def run(self):
        img = None
        try:
            import io
            from PIL import Image
            from src.core.file_converter import _open_image

            img = _open_image(self._path)
            orig_mode = img.mode
            orig_w, orig_h = img.size

            # Bail out early if a newer format/file was selected before the
            # image header was even decoded.
            if self._abort:
                img.close()
                img = None
                return

            # Convert to target format in-memory so the preview reflects
            # actual encoding artefacts (e.g. JPEG chroma subsampling).
            buf = io.BytesIO()
            save_img = img
            fmt = self._target_fmt
            if fmt == "JPEG":
                # JPEG does not support alpha; flatten to RGB.
                if save_img.mode != "RGB":
                    save_img = save_img.convert("RGB")
            elif fmt == "BMP":
                if save_img.mode == "RGBA":
                    save_img = save_img.convert("RGB")
            elif fmt == "GIF":
                save_img = save_img.convert("P")
            elif fmt == "ICO":
                save_img = save_img.convert("RGBA")

            save_kwargs: dict = {}
            if fmt in _QUALITY_FORMATS:
                save_kwargs["quality"] = self._quality

            preview_img = None
            try:
                save_img.save(buf, format=fmt, **save_kwargs)
                converted_size = buf.tell()
                buf.seek(0)
                preview_img = Image.open(buf)
                preview_img.load()  # fully decode into memory; buf can be closed now
                buf.close()
            except Exception:
                # Fallback: show the source image if in-memory conversion fails
                # (e.g. unsupported format like DDS which requires wand).
                buf.close()
                if preview_img is not None:
                    preview_img.close()
                    preview_img = None
                if save_img is not img:
                    save_img.close()
                img.thumbnail((self._max_size, self._max_size), Image.LANCZOS)
                qimg = _pil_to_qimage(img)
                img.close()
                img = None
                meta = (
                    f"{Path(self._path).name}\n"
                    f"{orig_w} × {orig_h}  ·  {orig_mode}\n"
                    f"Preview as {fmt}  (source shown)"
                )
                try:
                    self.loaded.emit(qimg, meta)
                except RuntimeError:
                    pass  # receiver destroyed; nothing to do
                return

            if save_img is not img:
                save_img.close()
            img.close()
            img = None
            preview_img.thumbnail((self._max_size, self._max_size), Image.LANCZOS)
            qimg = _pil_to_qimage(preview_img)
            preview_img.close()

            quality_note = f"  ·  Q {self._quality}" if fmt in _QUALITY_FORMATS else ""
            meta = (
                f"{Path(self._path).name}\n"
                f"{orig_w} × {orig_h}  ·  {orig_mode}\n"
                f"Preview as {fmt}{quality_note}  ·  ~{_fmt_size(converted_size)}"
            )
            try:
                self.loaded.emit(qimg, meta)
            except RuntimeError:
                pass  # receiver destroyed; nothing to do
        except Exception as exc:
            try:
                self.failed.emit(str(exc))
            except RuntimeError:
                pass  # receiver destroyed; nothing to do
        finally:
            if img is not None:
                img.close()


# ---------------------------------------------------------------------------
# Before / After comparison widget
# ---------------------------------------------------------------------------

class BeforeAfterWidget(QWidget):
    """
    Drag the central handle left/right to reveal more of the 'before'
    (original) image or the 'after' (processed) image.

    Public API
    ----------
    set_before(QImage)  – update the left (original) side
    set_after(QImage)   – update the right (processed) side
    set_loading()       – show a "Processing…" indicator on the right side
    clear()             – reset to empty placeholder

    Signals
    -------
    popout_requested()  – emitted when the ⤢ pop-out button is clicked.
                          The parent tool should open a floating dialog.
    """

    #: Emitted when the user clicks the ⤢ pop-out button.
    popout_requested = pyqtSignal()

    _HANDLE_R = 14    # handle circle radius (px)
    _DIVIDER_W = 2    # divider line width (px)
    _ARROW_W = 6      # arrow chevron reach from centre
    _ARROW_H = 4      # arrow head height
    # Below this widget width the compact overlay stats are shown as text
    # painted inside the widget itself instead of relying on the external side
    # panel QLabels in alpha_tool.py (those labels always occupy 84 px each).
    _COMPACT_OVERLAY_WIDTH_THRESHOLD = 500
    # Default divider / handle accent color (matches the default "Panda Dark" theme).
    _DEFAULT_DIVIDER_COLOR = "#e94560"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix_before: QPixmap | None = None
        self._pix_after: QPixmap | None = None
        self._split: float = 0.5     # divider position 0–1
        self._dragging: bool = False
        self._loading: bool = False
        self._checker: QPixmap | None = None  # lazily built / invalidated
        # Alpha-channel stats shown as bottom-corner overlays inside the widget
        # so they don't need external QLabel widgets (which have opaque backgrounds).
        self._stats_before: str = ""
        self._stats_after: str = ""
        # Raw (unmodified) images stored so callers can toggle overlays without
        # re-running the background worker.
        self._raw_before: QImage | None = None
        self._raw_after: QImage | None = None
        # QMovie used to animate the "before" side when the source is a GIF.
        self._movie: QMovie | None = None
        # Zoom & pan state
        self._zoom: float = 1.0          # 1.0 = fit-to-widget
        self._pan_x: float = 0.0         # pixel offset (applied when _zoom > 1)
        self._pan_y: float = 0.0
        self._panning: bool = False
        self._pan_start_pos: "QPoint | None" = None
        # Theme-tinted divider colour (updated by set_divider_color).
        self._divider_color: str = self._DEFAULT_DIVIDER_COLOR
        # Floating pop-out dialog (kept alive while open so images persist).
        self._popout_dialog: "QDialog | None" = None

        self.setMinimumSize(180, 120)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setMouseTracking(True)
        self.setToolTip(
            "Drag the ◀▶ handle to compare original and processed image.\n"
            "Ctrl+Scroll to zoom; middle-drag to pan when zoomed.\n"
            "Click ⤢ (top-left) to pop out a resizable floating window."
        )

        # Floating zoom overlay (top-right corner)
        self._zoom_bar = _ZoomOverlayBar(
            self.zoom_in, self.zoom_out, self.zoom_reset, self
        )
        self._zoom_bar.reposition(self.size())

        # Floating pop-out button (top-left corner)
        self._popout_btn = self._make_popout_button()
        self._reposition_popout_btn()

    # ------------------------------------------------------------------
    # Pop-out overlay button
    # ------------------------------------------------------------------

    def _make_popout_button(self) -> "QPushButton":
        btn = QPushButton("⇗ Pop Out", self)
        btn.setObjectName("popoutBtn")
        btn.setToolTip(
            "Pop out the preview into a separate floating window.\n"
            "The preview panel here will hide to make room for other controls.\n"
            "Close the floating window (or click 'Dock Preview Back') to restore it."
        )
        btn.setFixedSize(72, 22)
        btn.setStyleSheet(
            "QPushButton#popoutBtn {"
            "  background: rgba(20,20,20,155);"
            "  color: #eee;"
            "  border: 1px solid rgba(255,255,255,35);"
            "  border-radius: 5px;"
            "  font-size: 13px;"
            "  padding: 0;"
            "}"
            "QPushButton#popoutBtn:hover  { background: rgba(80,80,80,200); }"
            "QPushButton#popoutBtn:pressed{ background: rgba(30,30,30,240); }"
        )
        btn.clicked.connect(self._on_popout_clicked)
        btn.raise_()
        return btn

    def _reposition_popout_btn(self) -> None:
        margin = 6
        self._popout_btn.move(margin, margin)

    def _on_popout_clicked(self) -> None:
        """Open (or bring to front) a floating comparison window."""
        if self._popout_dialog is not None and not self._popout_dialog.isHidden():
            self._popout_dialog.raise_()
            self._popout_dialog.activateWindow()
            return

        dlg = QDialog(self.window())
        dlg.setWindowTitle("Preview — Pop-out Comparison")
        dlg.resize(900, 600)
        dlg.setMinimumSize(400, 300)
        dlg_layout = QVBoxLayout(dlg)
        dlg_layout.setContentsMargins(6, 6, 6, 6)
        dlg_layout.setSpacing(4)

        # Stand-alone BeforeAfterWidget with the same images
        compare = BeforeAfterWidget(dlg)
        if self._pix_before is not None:
            compare._pix_before = self._pix_before
        if self._pix_after is not None:
            compare._pix_after = self._pix_after
        if self._raw_before is not None:
            compare._raw_before = self._raw_before
        if self._raw_after is not None:
            compare._raw_after = self._raw_after
        compare._divider_color = self._divider_color
        compare._stats_before = self._stats_before
        compare._stats_after = self._stats_after
        dlg_layout.addWidget(compare, 1)

        self._popout_dialog = dlg
        # Emit signal so the parent tool can attach extra widgets (e.g. checkboxes)
        self.popout_requested.emit()
        dlg.show()

    # ------------------------------------------------------------------
    # Theme tinting
    # ------------------------------------------------------------------

    def set_divider_color(self, color: str) -> None:
        """Update the divider / handle accent color to match the active theme."""
        self._divider_color = color or self._DEFAULT_DIVIDER_COLOR
        self.update()

    # ------------------------------------------------------------------
    # Zoom API
    # ------------------------------------------------------------------

    def zoom_in(self) -> None:
        """Zoom in by 25%."""
        self._zoom = min(8.0, self._zoom * 1.25)
        self._clamp_pan()
        self.update()

    def zoom_out(self) -> None:
        """Zoom out by 25%."""
        self._zoom = max(0.2, self._zoom / 1.25)
        self._clamp_pan()
        self.update()

    def zoom_reset(self) -> None:
        """Reset zoom to fit-to-widget and clear pan offset."""
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.update()

    def _clamp_pan(self) -> None:
        """Clamp pan offsets so the image cannot be dragged completely off-screen."""
        w, h = self.width(), self.height()
        # Allow panning by at most half the scaled image dimension
        max_px = w * (self._zoom - 1) / 2 + w * 0.5
        max_py = h * (self._zoom - 1) / 2 + h * 0.5
        self._pan_x = max(-max_px, min(max_px, self._pan_x))
        self._pan_y = max(-max_py, min(max_py, self._pan_y))


    def set_before(self, qimg: QImage) -> None:
        """Set the 'before' (original) side."""
        self._stop_movie()
        self._pix_before = QPixmap.fromImage(qimg)
        self._loading = False
        self.update()

    def set_after(self, qimg: QImage) -> None:
        """Set the 'after' (processed) side."""
        self._pix_after = QPixmap.fromImage(qimg)
        self._loading = False
        self.update()

    def set_loading(self) -> None:
        """Show a processing indicator on the 'after' side."""
        self._pix_after = None
        self._loading = True
        self.update()

    def set_stats(self, before: dict, after: dict) -> None:
        """Store alpha-channel stats to be painted at the bottom corners.

        *before* and *after* are dicts with keys 'min', 'max', 'mean'.
        Passing empty dicts clears the overlaid text.
        """
        def _fmt(s: dict) -> str:
            if not s:
                return ""
            return f"min {s['min']}  max {s['max']}  mean {s['mean']:.1f}"

        self._stats_before = _fmt(before)
        self._stats_after = _fmt(after)
        self.update()

    def clear(self) -> None:
        """Reset to empty / placeholder state."""
        self._stop_movie()
        self._pix_before = None
        self._pix_after = None
        self._loading = False
        self._stats_before = ""
        self._stats_after = ""
        self._raw_before = None
        self._raw_after = None
        self.update()

    def store_raw_images(self, before: QImage, after: QImage) -> None:
        """Store the unmodified before/after images so overlay toggles can
        re-apply or remove visualisation without re-running the worker."""
        self._raw_before = before
        self._raw_after = after

    def before_image(self) -> QImage | None:
        """Return the stored raw 'before' image, or None if not set."""
        return self._raw_before

    def after_image(self) -> QImage | None:
        """Return the stored raw 'after' image, or None if not set."""
        return self._raw_after

    def has_images(self) -> bool:
        """Return True when raw images have been stored (i.e. a preview was loaded)."""
        return self._raw_before is not None and self._raw_after is not None

    # ------------------------------------------------------------------
    # Qt events
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._checker = None  # invalidate; rebuilt lazily in paintEvent
        self._clamp_pan()
        self._zoom_bar.reposition(event.size())
        self._zoom_bar.raise_()
        self._reposition_popout_btn()
        self._popout_btn.raise_()

    def wheelEvent(self, event):  # noqa: N802
        """Ctrl+scroll zooms in/out; plain scroll is passed to the parent."""
        from PyQt6.QtCore import Qt as _Qt
        if event.modifiers() & _Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
        else:
            event.ignore()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        split_x = int(w * self._split)

        # ── Checkerboard background ──────────────────────────────────
        if self._checker is None:
            self._checker = _make_checker(w, h)
        painter.drawPixmap(0, 0, self._checker)

        # ── Helper: draw pixmap scaled to widget, clipped to x-band ─
        zoom = self._zoom

        def _draw_pix(pix: QPixmap, clip_x: int, clip_w: int):
            if clip_w <= 0:
                return
            if zoom <= 1.0:
                scaled = pix.scaled(
                    w, h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                ox = (w - scaled.width()) // 2
                oy = (h - scaled.height()) // 2
            else:
                scaled = pix.scaled(
                    int(w * zoom), int(h * zoom),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                ox = (w - scaled.width()) // 2 + int(self._pan_x)
                oy = (h - scaled.height()) // 2 + int(self._pan_y)
            painter.setClipRect(QRect(clip_x, 0, clip_w, h))
            painter.drawPixmap(ox, oy, scaled)
            painter.setClipping(False)

        # ── Before (left of divider) ─────────────────────────────────
        if self._pix_before:
            _draw_pix(self._pix_before, 0, split_x)

        # ── After (right of divider) or loading indicator ────────────
        if self._pix_after:
            _draw_pix(self._pix_after, split_x, w - split_x)
        elif self._loading:
            painter.setClipRect(QRect(split_x, 0, w - split_x, h))
            painter.fillRect(split_x, 0, w - split_x, h, QColor(0, 0, 0, 110))
            painter.setClipping(False)
            painter.setPen(QColor(self._divider_color))
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            painter.drawText(
                QRect(split_x, 0, w - split_x, h),
                Qt.AlignmentFlag.AlignCenter,
                "Processing…",
            )

        # ── Placeholder when no images at all ────────────────────────
        if not self._pix_before and not self._loading:
            painter.setPen(QColor("#a0a0b0"))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(
                QRect(0, 0, w, h),
                Qt.AlignmentFlag.AlignCenter,
                "Select a file to compare",
            )

        # ── BEFORE / AFTER labels ─────────────────────────────────────
        if self._pix_before or self._pix_after or self._loading:
            lbl_font = QFont("Segoe UI", 8, QFont.Weight.Bold)
            painter.setFont(lbl_font)
            fm = QFontMetrics(lbl_font)
            lh = fm.height() + 4

            if split_x > 55:
                btext = "BEFORE"
                bw = fm.horizontalAdvance(btext) + 8
                painter.fillRect(4, 4, bw, lh, QColor(0, 0, 0, 150))
                painter.setPen(QColor("#dddddd"))
                painter.drawText(8, 4 + fm.ascent() + 2, btext)

            if w - split_x > 55:
                atext = "AFTER"
                aw2 = fm.horizontalAdvance(atext) + 8
                ax = w - aw2 - 4
                # Offset AFTER downward to clear the zoom overlay bar in the top-right corner.
                ay = 32
                painter.fillRect(ax, ay, aw2, lh, QColor(0, 0, 0, 150))
                painter.setPen(QColor(self._divider_color))
                painter.drawText(ax + 4, ay + fm.ascent() + 2, atext)

        # ── Alpha stats overlay (compact, below divider handle) ──────────
        # The primary stats display is now in the side panels in alpha_tool.py;
        # this compact overlay serves as a fallback when the widget is used
        # standalone (e.g. in other contexts) or the window is too narrow.
        if (self._stats_before or self._stats_after) and w < self._COMPACT_OVERLAY_WIDTH_THRESHOLD:
            stats_font = QFont("Segoe UI", 8)
            painter.setFont(stats_font)
            sfm = QFontMetrics(stats_font)
            slh = sfm.height() + 4
            margin = 4

            if self._stats_before and split_x > 10:
                sb_w = min(sfm.horizontalAdvance(self._stats_before) + 8,
                           split_x - margin)
                sb_y = h - slh - margin
                painter.fillRect(margin, sb_y, sb_w, slh, QColor(0, 0, 0, 150))
                painter.setPen(QColor("#dddddd"))
                painter.setClipRect(QRect(margin, sb_y, sb_w, slh))
                painter.drawText(margin + 4, sb_y + sfm.ascent() + 2,
                                 self._stats_before)
                painter.setClipping(False)

            if self._stats_after and w - split_x > 10:
                sa_w = min(sfm.horizontalAdvance(self._stats_after) + 8,
                           w - split_x - margin)
                sa_x = w - sa_w - margin
                sa_y = h - slh - margin
                painter.fillRect(sa_x, sa_y, sa_w, slh, QColor(0, 0, 0, 150))
                painter.setPen(QColor(self._divider_color))
                painter.setClipRect(QRect(sa_x, sa_y, sa_w, slh))
                painter.drawText(sa_x + 4, sa_y + sfm.ascent() + 2,
                                 self._stats_after)
                painter.setClipping(False)

        # ── Divider line ──────────────────────────────────────────────
        painter.setPen(QPen(QColor(self._divider_color), self._DIVIDER_W))
        painter.drawLine(split_x, 0, split_x, h)

        # ── Handle circle ─────────────────────────────────────────────
        hr = self._HANDLE_R
        hy = h // 2
        painter.setPen(QPen(QColor(self._divider_color), 2))
        painter.setBrush(QBrush(QColor("#1a1a2e")))
        painter.drawEllipse(split_x - hr, hy - hr, hr * 2, hr * 2)

        # Chevron arrows inside the handle
        aw_v, ah_v = self._ARROW_W, self._ARROW_H
        painter.setPen(QPen(QColor(self._divider_color), 2))
        painter.setBrush(QBrush())
        # Left-pointing arrow
        painter.drawLine(split_x - 2, hy, split_x - aw_v, hy)
        painter.drawLine(split_x - aw_v, hy, split_x - aw_v + ah_v, hy - ah_v)
        painter.drawLine(split_x - aw_v, hy, split_x - aw_v + ah_v, hy + ah_v)
        # Right-pointing arrow
        painter.drawLine(split_x + 2, hy, split_x + aw_v, hy)
        painter.drawLine(split_x + aw_v, hy, split_x + aw_v - ah_v, hy - ah_v)
        painter.drawLine(split_x + aw_v, hy, split_x + aw_v - ah_v, hy + ah_v)

        painter.end()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self._near_divider(event.pos().x()):
                self._dragging = True
                self._update_split(event.pos().x())
        if event.button() == Qt.MouseButton.MiddleButton and self._zoom > 1.0:
            self._panning = True
            self._pan_start_pos = event.pos()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._panning and self._pan_start_pos is not None:
            delta = event.pos() - self._pan_start_pos
            self._pan_x += delta.x()
            self._pan_y += delta.y()
            self._pan_start_pos = event.pos()
            self._clamp_pan()
            self.update()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif self._dragging:
            self._update_split(event.pos().x())
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._near_divider(event.pos().x()):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._zoom > 1.0:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self._pan_start_pos = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _near_divider(self, x: int) -> bool:
        split_x = int(self.width() * self._split)
        return abs(x - split_x) <= self._HANDLE_R + 6

    def _update_split(self, x: int) -> None:
        self._split = max(0.02, min(0.98, x / max(self.width(), 1)))
        self.update()

    # ------------------------------------------------------------------
    # Animated GIF support
    # ------------------------------------------------------------------

    def animate_before(self, path: str) -> None:
        """Animate an animated GIF on the 'before' side using QMovie.

        Each decoded frame is captured as a QPixmap and painted into the
        split-view's left panel so the animation plays inside the normal
        before/after comparison widget.  Any previously running movie is
        stopped first.  The 'after' side (converted output) is not affected.
        """
        self._stop_movie()
        movie = QMovie(path)
        if not movie.isValid():
            movie.deleteLater()
            return
        self._movie = movie
        self._movie.frameChanged.connect(self._on_movie_frame)
        self._movie.start()
        self._loading = False
        self.update()

    def set_animation_speed(self, percent: int) -> None:
        """Set the playback speed of the GIF animation as a percentage of normal speed.

        100 = normal speed, 200 = twice as fast, 50 = half speed.
        Has no effect when no animation is currently playing.
        """
        if self._movie is not None:
            self._movie.setSpeed(percent)

    def _stop_movie(self) -> None:
        """Stop and clean up any running QMovie."""
        if self._movie is not None:
            try:
                self._movie.stop()
                self._movie.frameChanged.disconnect(self._on_movie_frame)
            except RuntimeError:
                pass  # already disconnected / destroyed
            self._movie.deleteLater()
            self._movie = None

    def _on_movie_frame(self, _frame_no: int) -> None:
        """Slot called by QMovie on each new frame; updates the before pixmap."""
        if self._movie is not None:
            pix = self._movie.currentPixmap()
            if not pix.isNull():
                self._pix_before = pix
                self.update()


# ---------------------------------------------------------------------------
# Before/After preview loader for the Converter tab
# ---------------------------------------------------------------------------

class _ConverterPreviewLoader(QThread):
    """Load the source image AND an in-memory converted version.

    Emits both images as QImages together with compact metadata strings
    so the Converter tab can display a side-by-side before/after view
    matching the Alpha & RGBA Adjuster tab's preview style.
    """
    ready = pyqtSignal(QImage, QImage, str, str)   # (src_qi, out_qi, src_meta, out_meta)
    failed = pyqtSignal(str)

    def __init__(self, path: str, target_fmt: str, quality: int, max_size: int = 512):
        super().__init__()
        self._path = path
        self._target_fmt = target_fmt.upper()
        self._quality = quality
        self._max_size = max_size
        self._abort = False

    def stop(self) -> None:
        """Request that the thread abandon work as soon as it can check."""
        self._abort = True

    def run(self):
        img = None
        try:
            import io
            from PIL import Image
            from src.core.file_converter import _open_image

            img = _open_image(self._path)
            orig_mode = img.mode
            orig_w, orig_h = img.size
            src_file_size = os.path.getsize(self._path)

            # Early abort: if the request is already stale (user moved to a
            # different file before we even decoded the header), skip all work.
            if self._abort:
                img.close()
                img = None
                return

            # --- Source side ---
            src_thumb = img.copy()
            try:
                src_thumb.thumbnail((self._max_size, self._max_size), Image.LANCZOS)
                src_qi = _pil_to_qimage(src_thumb)
            finally:
                src_thumb.close()
            src_meta = (
                f"{Path(self._path).name}\n"
                f"{orig_w} × {orig_h}  ·  {orig_mode}\n"
                f"{_fmt_size(src_file_size)}"
            )

            # If a newer selection or format change arrived, bail out before
            # running the expensive in-memory conversion step.
            if self._abort:
                img.close()
                img = None
                return

            # --- Output side: convert in-memory to see encoding artefacts ---
            fmt = self._target_fmt
            save_img = img
            if fmt == "JPEG":
                if save_img.mode != "RGB":
                    save_img = save_img.convert("RGB")
            elif fmt == "BMP":
                if save_img.mode == "RGBA":
                    save_img = save_img.convert("RGB")
            elif fmt == "GIF":
                save_img = save_img.convert("P")
            elif fmt == "ICO":
                save_img = save_img.convert("RGBA")

            save_kwargs: dict = {}
            if fmt in _QUALITY_FORMATS:
                save_kwargs["quality"] = self._quality

            # Allocate the buffer outside the try so it is always in scope for
            # the except block and can be closed on both success and error paths.
            buf = io.BytesIO()
            out_img = None
            try:
                save_img.save(buf, format=fmt, **save_kwargs)
                converted_size = buf.tell()
                buf.seek(0)
                out_img = Image.open(buf)
                out_img.load()  # fully decode into memory; buf can be closed now
                buf.close()
            except Exception:
                # Fallback: show source image again if conversion fails
                buf.close()
                if out_img is not None:
                    out_img.close()
                    out_img = None
                if save_img is not img:
                    save_img.close()
                out_qi = src_qi
                quality_note = (
                    f"  ·  Q {self._quality}" if fmt in _QUALITY_FORMATS else ""
                )
                out_meta = (
                    f"{orig_w} × {orig_h}  ·  {orig_mode}\n"
                    f"Preview as {fmt}{quality_note}\n"
                    f"(source shown – conversion unsupported)"
                )
                img.close()
                img = None
                try:
                    self.ready.emit(src_qi, out_qi, src_meta, out_meta)
                except RuntimeError:
                    pass  # receiver destroyed; nothing to do
                return

            if save_img is not img:
                save_img.close()
            img.close()
            img = None
            out_mode = out_img.mode
            out_thumb = None
            try:
                out_thumb = out_img.copy()
                out_thumb.thumbnail((self._max_size, self._max_size), Image.LANCZOS)
                out_qi = _pil_to_qimage(out_thumb)
            finally:
                if out_thumb is not None:
                    out_thumb.close()
                out_img.close()

            quality_note = f"  ·  Q {self._quality}" if fmt in _QUALITY_FORMATS else ""
            out_meta = (
                f"{orig_w} × {orig_h}  ·  {out_mode}\n"
                f"Preview as {fmt}{quality_note}\n"
                f"~{_fmt_size(converted_size)}"
            )
            try:
                self.ready.emit(src_qi, out_qi, src_meta, out_meta)
            except RuntimeError:
                pass  # receiver destroyed; nothing to do
        except Exception as exc:
            try:
                self.failed.emit(str(exc))
            except RuntimeError:
                pass  # receiver destroyed; nothing to do
        finally:
            if img is not None:
                img.close()


# ---------------------------------------------------------------------------
# Simple thumbnail preview pane (kept for backward compatibility)
# ---------------------------------------------------------------------------

class ImagePreviewPane(QWidget):
    """
    Drop-in side-panel.  Call ``show_file(path)`` to load a preview.
    Call ``clear()`` to reset to the placeholder.

    The loaded pixmap is kept in ``_current_pix`` and re-scaled every time the
    widget is resized so the image always fills the available space without
    being clipped.
    """

    # Minimum dimensions (px) used when the label has not been laid out yet.
    _PLACEHOLDER_MIN_WIDTH: int = 200
    _PLACEHOLDER_MIN_HEIGHT: int = 140
    # Ignore resize events where the label is thinner/shorter than this (px).
    _MIN_DISPLAY_SIZE: int = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loader: _ThumbLoader | None = None
        # Full-resolution pixmap received from the loader thread.  Stored so
        # it can be re-scaled to whatever size the pane has at any given time.
        self._current_pix: QPixmap | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        title = QLabel("Preview")
        title.setObjectName("section")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl = title
        layout.addWidget(title)

        frame = QFrame()
        frame.setObjectName("card")
        frame.setFrameShape(QFrame.Shape.Box)
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(4, 4, 4, 4)

        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setMinimumSize(160, 140)
        self._img_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding,
        )
        self._img_label.setScaledContents(False)
        fl.addWidget(self._img_label)
        layout.addWidget(frame, 1)

        self._meta_label = QLabel("Select a file to preview")
        self._meta_label.setObjectName("subheader")
        self._meta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._meta_label.setWordWrap(True)
        layout.addWidget(self._meta_label)

        self._set_placeholder()

    def _set_placeholder(self):
        w = max(self._img_label.width(), self._PLACEHOLDER_MIN_WIDTH)
        h = max(self._img_label.height(), self._PLACEHOLDER_MIN_HEIGHT)
        checker = _make_checker(w, h, sq=20)
        self._img_label.setPixmap(checker)

    def _update_display_pix(self):
        """Scale ``_current_pix`` to fill the current label size."""
        if self._current_pix is None:
            return
        available = self._img_label.size()
        if available.width() < self._MIN_DISPLAY_SIZE or available.height() < self._MIN_DISPLAY_SIZE:
            return
        scaled = self._current_pix.scaled(
            available,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._img_label.setPixmap(scaled)

    # ------------------------------------------------------------------
    # Qt event override
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._current_pix is not None:
            self._update_display_pix()
        else:
            self._set_placeholder()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_theme(self, icon: str) -> None:
        """Update the Preview title label to include *icon* (the theme emoji)."""
        self._title_lbl.setText(f"{icon}  Preview")

    def show_file(self, path: str):
        if not path or not os.path.isfile(path):
            self.clear()
            return
        self._start_loader(_ThumbLoader(path))

    def show_converted(self, path: str, target_fmt: str, quality: int):
        """Show a live preview of *path* as it would appear after conversion.

        Converts the image in-memory to *target_fmt* (with *quality* for
        JPEG/WEBP) so the user sees any encoding artefacts before committing
        to the conversion.  Falls back to the source thumbnail if the
        in-memory conversion fails (e.g. for DDS).
        """
        if not path or not os.path.isfile(path):
            self.clear()
            return
        self._start_loader(_ConvertedThumbLoader(path, target_fmt, quality))

    def _start_loader(self, loader):
        """Disconnect and stop any stale loader, then start *loader*."""
        if self._loader is not None:
            self._loader.stop()
            try:
                self._loader.loaded.disconnect()
                self._loader.failed.disconnect()
            except RuntimeError:
                pass  # already disconnected
        self._meta_label.setText("Loading…")
        self._loader = loader
        self._loader.loaded.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)
        self._loader.start()

    def clear(self):
        self._current_pix = None
        self._set_placeholder()
        self._meta_label.setText("Select a file to preview")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_loaded(self, qimg: QImage, meta: str):
        # Store the full pixmap so _update_display_pix can re-scale it any
        # time the pane changes size (e.g., window resize or splitter drag).
        self._current_pix = QPixmap.fromImage(qimg)
        # Guard against a zero/tiny label size when the widget hasn't been
        # laid out yet (the thread may finish before the first layout pass).
        available = self._img_label.size()
        if available.width() < 20 or available.height() < 20:
            available = QSize(
                max(self._current_pix.width(), self._PLACEHOLDER_MIN_WIDTH),
                max(self._current_pix.height(), self._PLACEHOLDER_MIN_HEIGHT),
            )
            scaled = self._current_pix.scaled(
                available,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_label.setPixmap(scaled)
        else:
            self._update_display_pix()
        self._meta_label.setText(meta)

    def _on_failed(self, err: str):
        self._current_pix = None
        self._set_placeholder()
        self._meta_label.setText(f"Preview unavailable\n{err[:80]}")
