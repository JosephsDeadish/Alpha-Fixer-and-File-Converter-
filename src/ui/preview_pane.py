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
    QPixmap, QImage, QColor,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QSizePolicy, QFrame,
)


# ---------------------------------------------------------------------------
# Shared PIL → QImage helper
# ---------------------------------------------------------------------------

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
            img = Image.open(self._path)
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
            self.loaded.emit(qimg, meta_text)
        except Exception as exc:
            self.failed.emit(str(exc))
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

            img = Image.open(self._path)
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
                self.loaded.emit(qimg, meta)
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
            self.loaded.emit(qimg, meta)
        except Exception as exc:
            self.failed.emit(str(exc))
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
    """

    _HANDLE_R = 14    # handle circle radius (px)
    _DIVIDER_W = 2    # divider line width (px)
    _ARROW_W = 6      # arrow chevron reach from centre
    _ARROW_H = 4      # arrow head height
    # Below this widget width the compact overlay stats are shown as text
    # painted inside the widget itself instead of relying on the external side
    # panel QLabels in alpha_tool.py (those labels always occupy 84 px each).
    _COMPACT_OVERLAY_WIDTH_THRESHOLD = 500

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

        self.setMinimumSize(180, 120)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setMouseTracking(True)
        self.setToolTip(
            "Drag the ◀▶ handle to compare original and processed image"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_before(self, qimg: QImage) -> None:
        """Set the 'before' (original) side."""
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
        self._pix_before = None
        self._pix_after = None
        self._loading = False
        self._stats_before = ""
        self._stats_after = ""
        self.update()

    # ------------------------------------------------------------------
    # Qt events
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._checker = None  # invalidate; rebuilt lazily in paintEvent

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
        def _draw_pix(pix: QPixmap, clip_x: int, clip_w: int):
            if clip_w <= 0:
                return
            scaled = pix.scaled(
                w, h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            ox = (w - scaled.width()) // 2
            oy = (h - scaled.height()) // 2
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
            painter.setPen(QColor("#e94560"))
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
                painter.fillRect(ax, 4, aw2, lh, QColor(0, 0, 0, 150))
                painter.setPen(QColor("#e94560"))
                painter.drawText(ax + 4, 4 + fm.ascent() + 2, atext)

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
                painter.setPen(QColor("#e94560"))
                painter.setClipRect(QRect(sa_x, sa_y, sa_w, slh))
                painter.drawText(sa_x + 4, sa_y + sfm.ascent() + 2,
                                 self._stats_after)
                painter.setClipping(False)

        # ── Divider line ──────────────────────────────────────────────
        painter.setPen(QPen(QColor("#e94560"), self._DIVIDER_W))
        painter.drawLine(split_x, 0, split_x, h)

        # ── Handle circle ─────────────────────────────────────────────
        hr = self._HANDLE_R
        hy = h // 2
        painter.setPen(QPen(QColor("#e94560"), 2))
        painter.setBrush(QBrush(QColor("#1a1a2e")))
        painter.drawEllipse(split_x - hr, hy - hr, hr * 2, hr * 2)

        # Chevron arrows inside the handle
        aw_v, ah_v = self._ARROW_W, self._ARROW_H
        painter.setPen(QPen(QColor("#e94560"), 2))
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

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._dragging:
            self._update_split(event.pos().x())
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._near_divider(event.pos().x()):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _near_divider(self, x: int) -> bool:
        split_x = int(self.width() * self._split)
        return abs(x - split_x) <= self._HANDLE_R + 6

    def _update_split(self, x: int) -> None:
        self._split = max(0.02, min(0.98, x / max(self.width(), 1)))
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

            img = Image.open(self._path)
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
                self.ready.emit(src_qi, out_qi, src_meta, out_meta)
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
            self.ready.emit(src_qi, out_qi, src_meta, out_meta)
        except Exception as exc:
            self.failed.emit(str(exc))
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
