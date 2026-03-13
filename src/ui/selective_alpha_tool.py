"""
Selective Alpha editor tab.

Lets the user paint up to 7 coloured mask zones on top of a single image
and assign a distinct alpha value to each zone.  On "Apply" the alpha
channel of every painted pixel is replaced with its zone's alpha value.

Drawing tools
─────────────
  Freehand  – paint a brush stroke (circle of configurable radius)
  Line      – click-and-drag to draw a straight line
  Rectangle – click-and-drag to paint a filled axis-aligned rectangle
  Ellipse   – click-and-drag to paint a filled ellipse
  Fill      – click to flood-fill from a point (edge-detection aware)

Auto-correct
────────────
When the "Auto-correct" checkbox is ticked, freehand and line strokes are
snapped toward any strong image edges within a search radius after the
mouse is released.  This lets an approximate stroke hug the actual object
boundary automatically.
"""

import os
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw

from PyQt6.QtCore import (
    Qt, QEvent, QPoint, QPointF, QRect, QRectF, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QCursor, QFont, QImage, QPainter, QPen, QPixmap,
    QBrush,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QSpinBox, QCheckBox, QGroupBox,
    QFileDialog, QMessageBox, QScrollArea, QSizePolicy,
    QButtonGroup, QAbstractButton, QSplitter, QFrame,
)

from ..core.selective_alpha_processor import (
    NUM_ZONES,
    ZONE_COLORS,
    ZONE_NAMES,
    detect_edges,
    edge_flood_fill,
    autocorrect_mask,
    apply_selective_alpha,
    composite_zones,
)

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _pil_to_qimage(img: Image.Image) -> QImage:
    """Convert a PIL image to a detached RGBA QImage."""
    rgba = img.convert("RGBA") if img.mode != "RGBA" else img
    try:
        data = rgba.tobytes("raw", "RGBA")
        qi = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
        return qi.copy()
    finally:
        if rgba is not img:
            rgba.close()


def _np_to_qimage(arr: np.ndarray) -> QImage:
    """Convert a uint8 (h, w, 4) numpy RGBA array to a detached QImage."""
    h, w = arr.shape[:2]
    qi = QImage(arr.tobytes(), w, h, QImage.Format.Format_RGBA8888)
    return qi.copy()


def _zone_qcolor(zone_idx: int, alpha: int = 200) -> QColor:
    r, g, b, _ = ZONE_COLORS[zone_idx]
    return QColor(r, g, b, alpha)


# ---------------------------------------------------------------------------
# Drawing canvas
# ---------------------------------------------------------------------------

class SelectiveAlphaCanvas(QWidget):
    """
    Interactive canvas that shows the source image with coloured mask
    overlays and handles all drawing operations.

    Signals
    -------
    mask_changed : int – emitted with the zone index whenever a mask is edited.
    """

    mask_changed = pyqtSignal(int)

    # ------------------------------------------------------------------ init

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(300, 200)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # ---- Source image -------------------------------------------------
        self._src_img:  Image.Image | None = None   # PIL RGBA
        self._src_arr:  np.ndarray  | None = None   # uint8 (h,w,4)

        # ---- Zone masks (PIL "L" images, 255=selected, 0=not) -------------
        # Using PIL so ImageDraw can paint directly into them.
        self._masks: list[Image.Image | None] = [None] * NUM_ZONES

        # ---- Cached edge map (computed once per image) -------------------
        self._edge_map: np.ndarray | None = None

        # ---- View transform -----------------------------------------------
        self._zoom: float   = 1.0
        self._pan_x: float  = 0.0
        self._pan_y: float  = 0.0

        # ---- Active zone --------------------------------------------------
        self._active_zone: int = 0

        # ---- Current tool -------------------------------------------------
        # "freehand" | "line" | "rect" | "ellipse" | "fill" | "polygon"
        self._tool: str = "freehand"

        # ---- Brush size (radius for freehand/line) -----------------------
        self._brush_size: int = 10

        # ---- Auto-correct -------------------------------------------------
        self._autocorrect: bool = False

        # ---- Drawing state ------------------------------------------------
        self._drawing:     bool              = False
        self._last_img_pt: tuple[int, int]   = (0, 0)
        self._start_img_pt: tuple[int, int]  = (0, 0)
        self._poly_pts: list[tuple[int, int]] = []   # polygon mode
        self._preview_end:  QPointF | None   = None  # rubber-band end (widget coords)

        # ---- Pan via middle-mouse / Alt+drag -----------------------------
        self._panning:      bool    = False
        self._pan_start_w:  QPointF = QPointF()
        self._pan_start_off: tuple[float, float] = (0.0, 0.0)

        # ---- Composite cache (invalidated when a mask changes) -----------
        self._composite_dirty: bool             = True
        self._composite_qimg:  QImage | None    = None

    # ---------------------------------------------------------------- public

    def load_image(self, path: str) -> bool:
        """Load *path* into the canvas.  Returns True on success."""
        try:
            img = Image.open(path)
            img.load()
            rgba = img.convert("RGBA")
            if rgba is not img:
                img.close()
            if self._src_img is not None:
                self._src_img.close()
            self._src_img  = rgba
            self._src_arr  = np.array(rgba, dtype=np.uint8)
            self._edge_map = None   # recomputed lazily
            self._masks    = [None] * NUM_ZONES
            self._zoom     = 1.0
            self._pan_x    = 0.0
            self._pan_y    = 0.0
            self._poly_pts = []
            self._composite_dirty = True
            self.update()
            return True
        except Exception:
            return False

    def clear_mask(self, zone_idx: int) -> None:
        """Erase the mask for zone *zone_idx*."""
        old = self._masks[zone_idx]
        if old is not None:
            old.close()
        self._masks[zone_idx]  = None
        self._composite_dirty  = True
        self.update()
        self.mask_changed.emit(zone_idx)

    def clear_all_masks(self) -> None:
        """Erase all zone masks."""
        for i in range(NUM_ZONES):
            m = self._masks[i]
            if m is not None:
                m.close()
            self._masks[i] = None
        self._composite_dirty = True
        self.update()

    def set_active_zone(self, idx: int) -> None:
        self._active_zone = max(0, min(NUM_ZONES - 1, idx))

    def set_tool(self, tool: str) -> None:
        """Set the active tool name."""
        self._tool   = tool
        self._poly_pts = []
        self._preview_end = None
        self.update()

    def set_brush_size(self, size: int) -> None:
        self._brush_size = max(1, size)

    def set_autocorrect(self, enabled: bool) -> None:
        self._autocorrect = enabled

    def get_masks_as_bool(self) -> list[Optional[np.ndarray]]:
        """Return a list of bool (h, w) arrays or None for each zone."""
        result: list[Optional[np.ndarray]] = []
        for m in self._masks:
            if m is None:
                result.append(None)
            else:
                result.append(np.array(m, dtype=np.uint8) > 127)
        return result

    def has_image(self) -> bool:
        """Return True if a source image is currently loaded."""
        return self._src_img is not None

    def get_source_image(self) -> Image.Image | None:
        """Return the currently loaded PIL source image, or None."""
        return self._src_img

    def close_polygon(self) -> None:
        """Close and fill the in-progress polygon, optionally auto-correcting."""
        pts = self._poly_pts
        if len(pts) >= 3:
            self._paint_polygon_on_mask(pts)
            if self._autocorrect:
                self._apply_autocorrect(self._active_zone)
            self._poly_pts    = []
            self._preview_end = None
            self._composite_dirty = True
            self.mask_changed.emit(self._active_zone)
            self.update()

    def zoom_by(self, factor: float) -> None:
        """Multiply the current zoom level by *factor* (clamped to [0.1, 20])."""
        self._zoom = max(0.1, min(20.0, self._zoom * factor))
        self._composite_dirty = True
        self.update()

    def zoom_reset(self) -> None:
        """Reset zoom and pan to default (fit-to-canvas)."""
        self._zoom  = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._composite_dirty = True
        self.update()

    # ----------------------------------------------------------- edge map

    def _get_edge_map(self) -> np.ndarray | None:
        """Return (computing if necessary) the cached edge map."""
        if self._src_img is None:
            return None
        if self._edge_map is None:
            self._edge_map = detect_edges(self._src_img)
        return self._edge_map

    # --------------------------------------------------- coordinate helpers

    def _transform(self) -> tuple[float, float, float]:
        """Return (scale, offset_x, offset_y) for the current view."""
        if self._src_img is None:
            return 1.0, 0.0, 0.0
        iw, ih = self._src_img.size
        cw, ch  = self.width(), self.height()
        if iw == 0 or ih == 0 or cw == 0 or ch == 0:
            return 1.0, 0.0, 0.0
        base = min(cw / iw, ch / ih)
        scale = base * self._zoom
        ox = (cw - iw * scale) / 2.0 + self._pan_x
        oy = (ch - ih * scale) / 2.0 + self._pan_y
        return scale, ox, oy

    def _w2i(self, wx: float, wy: float) -> tuple[int, int]:
        """Widget → image pixel (clipped to image bounds)."""
        s, ox, oy = self._transform()
        ix = int((wx - ox) / s)
        iy = int((wy - oy) / s)
        if self._src_img is not None:
            iw, ih = self._src_img.size
            ix = max(0, min(iw - 1, ix))
            iy = max(0, min(ih - 1, iy))
        return ix, iy

    def _i2w(self, ix: float, iy: float) -> QPointF:
        """Image pixel → widget position."""
        s, ox, oy = self._transform()
        return QPointF(ix * s + ox, iy * s + oy)

    # --------------------------------------------------- mask utilities

    def _ensure_mask(self, zone_idx: int) -> Image.Image:
        """Return the mask PIL image for *zone_idx*, creating it if absent."""
        if self._masks[zone_idx] is None:
            if self._src_img is None:
                raise RuntimeError("No image loaded")
            w, h = self._src_img.size
            self._masks[zone_idx] = Image.new("L", (w, h), 0)
        return self._masks[zone_idx]   # type: ignore[return-value]

    def _paint_brush(self, ix: int, iy: int) -> None:
        """Paint a filled circle of radius _brush_size at (ix, iy)."""
        mask = self._ensure_mask(self._active_zone)
        draw = ImageDraw.Draw(mask)
        r = self._brush_size
        draw.ellipse([(ix - r, iy - r), (ix + r, iy + r)], fill=255)
        del draw
        self._composite_dirty = True

    def _paint_line_on_mask(
        self, x0: int, y0: int, x1: int, y1: int, width: int | None = None
    ) -> None:
        """Paint a line on the active zone mask."""
        mask = self._ensure_mask(self._active_zone)
        draw = ImageDraw.Draw(mask)
        w = width if width is not None else max(2, self._brush_size)
        draw.line([(x0, y0), (x1, y1)], fill=255, width=w)
        del draw
        self._composite_dirty = True

    def _paint_rect_on_mask(
        self, x0: int, y0: int, x1: int, y1: int
    ) -> None:
        mask = self._ensure_mask(self._active_zone)
        draw = ImageDraw.Draw(mask)
        draw.rectangle(
            [(min(x0, x1), min(y0, y1)), (max(x0, x1), max(y0, y1))],
            fill=255,
        )
        del draw
        self._composite_dirty = True

    def _paint_ellipse_on_mask(
        self, x0: int, y0: int, x1: int, y1: int
    ) -> None:
        mask = self._ensure_mask(self._active_zone)
        draw = ImageDraw.Draw(mask)
        draw.ellipse(
            [(min(x0, x1), min(y0, y1)), (max(x0, x1), max(y0, y1))],
            fill=255,
        )
        del draw
        self._composite_dirty = True

    def _paint_polygon_on_mask(
        self, pts: list[tuple[int, int]]
    ) -> None:
        if len(pts) < 3:
            return
        mask = self._ensure_mask(self._active_zone)
        draw = ImageDraw.Draw(mask)
        draw.polygon(pts, fill=255)
        del draw
        self._composite_dirty = True

    def _apply_autocorrect(self, zone_idx: int) -> None:
        """Snap the zone mask boundary to nearby image edges."""
        mask = self._masks[zone_idx]
        if mask is None:
            return
        edge_map = self._get_edge_map()
        if edge_map is None:
            return
        bool_mask = np.array(mask, dtype=np.uint8) > 127
        snapped   = autocorrect_mask(bool_mask, edge_map)
        if snapped is bool_mask:
            return
        # Write result back to the PIL mask
        new_pil = Image.fromarray((snapped * 255).astype(np.uint8), "L")
        old = self._masks[zone_idx]
        if old is not None:
            old.close()
        self._masks[zone_idx]  = new_pil
        self._composite_dirty  = True

    # --------------------------------------------------- rendering

    def _rebuild_composite(self) -> None:
        """Recompute the cached composite QImage."""
        if self._src_arr is None:
            self._composite_qimg = None
            return
        bool_masks = self.get_masks_as_bool()
        comp = composite_zones(self._src_arr, bool_masks)
        self._composite_qimg = _np_to_qimage(comp)
        self._composite_dirty = False

    def paintEvent(self, event) -> None:   # noqa: N802
        if self._src_img is None:
            # Draw a placeholder when no image is loaded.
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(40, 40, 40))
            p.setPen(QColor(120, 120, 120))
            p.setFont(QFont("Arial", 14))
            p.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Open an image to start editing",
            )
            p.end()
            return

        if self._composite_dirty:
            self._rebuild_composite()

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Draw checker background
        cw, ch = self.width(), self.height()
        _draw_checker(p, cw, ch)

        # Draw the composite image scaled to the current view.
        if self._composite_qimg is not None:
            s, ox, oy = self._transform()
            iw, ih = self._src_img.size
            dst = QRectF(ox, oy, iw * s, ih * s)
            p.drawImage(dst, self._composite_qimg)

        # Draw rubber-band preview for line/rect/ellipse tools.
        self._draw_preview(p)

        # Draw in-progress polygon.
        if self._tool == "polygon" and len(self._poly_pts) > 0:
            self._draw_polygon_preview(p)

        p.end()

    def _draw_preview(self, painter: QPainter) -> None:
        """Draw rubber-band shape preview during drag."""
        if not self._drawing or self._preview_end is None:
            return
        if self._tool not in ("line", "rect", "ellipse"):
            return

        zc = _zone_qcolor(self._active_zone, 200)
        pen = QPen(zc, 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor(zc.red(), zc.green(), zc.blue(), 60)))

        s, ox, oy = self._transform()
        sx_i, sy_i = self._start_img_pt
        sx_w = QPointF(sx_i * s + ox, sy_i * s + oy)
        ex_w = self._preview_end

        if self._tool == "line":
            painter.drawLine(sx_w, ex_w)
        elif self._tool == "rect":
            r = QRectF(sx_w, ex_w).normalized()
            painter.drawRect(r)
        elif self._tool == "ellipse":
            r = QRectF(sx_w, ex_w).normalized()
            painter.drawEllipse(r)

    def _draw_polygon_preview(self, painter: QPainter) -> None:
        """Draw in-progress polygon vertices and connecting lines."""
        zc = _zone_qcolor(self._active_zone, 220)
        pen = QPen(zc, 2)
        painter.setPen(pen)

        s, ox, oy = self._transform()
        pts_w = [self._i2w(x, y) for x, y in self._poly_pts]
        for i in range(len(pts_w) - 1):
            painter.drawLine(pts_w[i], pts_w[i + 1])
        if self._preview_end is not None:
            painter.drawLine(pts_w[-1], self._preview_end)
        # Draw small squares at each vertex
        for pw in pts_w:
            painter.fillRect(
                QRectF(pw.x() - 4, pw.y() - 4, 8, 8),
                QBrush(zc),
            )

    # --------------------------------------------------- mouse events

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._src_img is None:
            return

        pt = event.position()

        # Middle-mouse or Alt+Left = pan
        if (event.button() == Qt.MouseButton.MiddleButton
                or (event.button() == Qt.MouseButton.LeftButton
                    and event.modifiers() & Qt.KeyboardModifier.AltModifier)):
            self._panning = True
            self._pan_start_w  = pt
            self._pan_start_off = (self._pan_x, self._pan_y)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        ix, iy = self._w2i(pt.x(), pt.y())
        self._start_img_pt = (ix, iy)
        self._last_img_pt  = (ix, iy)
        self._preview_end  = pt

        if self._tool == "freehand":
            self._drawing = True
            self._paint_brush(ix, iy)
            self.mask_changed.emit(self._active_zone)
            self.update()

        elif self._tool in ("line", "rect", "ellipse"):
            self._drawing = True

        elif self._tool == "fill":
            edge_map = self._get_edge_map()
            if edge_map is not None:
                filled = edge_flood_fill((ix, iy), edge_map)
            else:
                # Fallback: fill the whole image
                h, w = self._src_img.size[1], self._src_img.size[0]
                filled = np.ones((h, w), dtype=bool)
            mask = self._ensure_mask(self._active_zone)
            arr  = np.array(mask, dtype=np.uint8)
            arr[filled] = 255
            new_pil = Image.fromarray(arr, "L")
            old = self._masks[self._active_zone]
            if old is not None:
                old.close()
            self._masks[self._active_zone] = new_pil
            self._composite_dirty = True
            self.mask_changed.emit(self._active_zone)
            self.update()

        elif self._tool == "polygon":
            # Single-click adds a point; double-click closes the polygon.
            if event.type() == QEvent.Type.MouseButtonDblClick:
                if len(self._poly_pts) >= 3:
                    self._poly_pts.append((ix, iy))
                    self._paint_polygon_on_mask(self._poly_pts)
                    if self._autocorrect:
                        self._apply_autocorrect(self._active_zone)
                    self._poly_pts = []
                    self._preview_end = None
                    self.mask_changed.emit(self._active_zone)
                    self.update()
                return
            self._poly_pts.append((ix, iy))
            self._preview_end = pt
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._src_img is None:
            return
        pt = event.position()

        if self._panning:
            dx = pt.x() - self._pan_start_w.x()
            dy = pt.y() - self._pan_start_w.y()
            self._pan_x = self._pan_start_off[0] + dx
            self._pan_y = self._pan_start_off[1] + dy
            self._composite_dirty = True
            self.update()
            return

        if not self._drawing:
            if self._tool == "polygon" and len(self._poly_pts) > 0:
                self._preview_end = pt
                self.update()
            return

        ix, iy = self._w2i(pt.x(), pt.y())
        self._preview_end = pt

        if self._tool == "freehand":
            lx, ly = self._last_img_pt
            # Paint a circle at the new position AND a line between the last
            # and current position to fill in gaps at high speeds.
            mask = self._ensure_mask(self._active_zone)
            draw = ImageDraw.Draw(mask)
            r = self._brush_size
            draw.line([(lx, ly), (ix, iy)], fill=255, width=r * 2)
            draw.ellipse([(ix - r, iy - r), (ix + r, iy + r)], fill=255)
            del draw
            self._composite_dirty = True
            self._last_img_pt = (ix, iy)
            self.mask_changed.emit(self._active_zone)
            self.update()
        else:
            # Just update rubber-band preview
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._panning and event.button() in (
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.LeftButton,
        ):
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._drawing:
            return
        if self._src_img is None:
            return

        pt = event.position()
        ix, iy = self._w2i(pt.x(), pt.y())
        sx, sy = self._start_img_pt

        if self._tool in ("freehand",):
            if self._autocorrect:
                self._apply_autocorrect(self._active_zone)
                self._composite_dirty = True

        elif self._tool == "line":
            self._paint_line_on_mask(sx, sy, ix, iy)
            if self._autocorrect:
                self._apply_autocorrect(self._active_zone)
            self.mask_changed.emit(self._active_zone)

        elif self._tool == "rect":
            self._paint_rect_on_mask(sx, sy, ix, iy)
            self.mask_changed.emit(self._active_zone)

        elif self._tool == "ellipse":
            self._paint_ellipse_on_mask(sx, sy, ix, iy)
            self.mask_changed.emit(self._active_zone)

        self._drawing      = False
        self._preview_end  = None
        self._composite_dirty = True
        self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Zoom with the mouse wheel, centred on the cursor position."""
        if self._src_img is None:
            return
        delta   = event.angleDelta().y()
        factor  = 1.1 if delta > 0 else 1.0 / 1.1
        old_zoom = self._zoom
        self._zoom = max(0.1, min(20.0, self._zoom * factor))

        # Adjust pan so the image point under the cursor stays fixed.
        pt = event.position()
        s_old, ox_old, oy_old = self._transform()
        # After zoom, without pan adjustment:
        s_new = s_old / old_zoom * self._zoom
        ox_new = (self.width()  - self._src_img.width  * s_new) / 2.0 + self._pan_x
        oy_new = (self.height() - self._src_img.height * s_new) / 2.0 + self._pan_y
        # Image point under cursor before: (pt - old_offset) / s_old
        # Same point after: (pt - new_offset) / s_new
        # => new_pan = old_pan + pt*(1 - s_new/s_old)
        ratio = s_new / s_old
        self._pan_x += pt.x() * (1.0 - ratio)
        self._pan_y += pt.y() * (1.0 - ratio)

        self._composite_dirty = True
        self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Escape cancels the current polygon in progress."""
        if event.key() == Qt.Key.Key_Escape and self._tool == "polygon":
            self._poly_pts    = []
            self._preview_end = None
            self.update()
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Checker background helper
# ---------------------------------------------------------------------------


def _draw_checker(painter: QPainter, w: int, h: int, sq: int = 10) -> None:
    """Fill the canvas with a checkerboard to indicate transparency."""
    c1 = QColor(60, 60, 60)
    c2 = QColor(40, 40, 40)
    for row in range(0, h, sq):
        for col in range(0, w, sq):
            c = c1 if (row // sq + col // sq) % 2 == 0 else c2
            painter.fillRect(col, row, sq, sq, c)


# ---------------------------------------------------------------------------
# Zone row widget
# ---------------------------------------------------------------------------


class _ZoneRow(QWidget):
    """A compact row showing zone colour swatch, name, alpha spinbox and
    Clear/Select buttons for one zone."""

    selected = pyqtSignal(int)   # zone_idx

    def __init__(self, zone_idx: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._idx = zone_idx

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(4)

        # Colour swatch
        r, g, b, _ = ZONE_COLORS[zone_idx]
        swatch = QLabel()
        swatch.setFixedSize(16, 16)
        swatch.setStyleSheet(
            f"background:{QColor(r,g,b).name()};"
            "border:1px solid #666; border-radius:3px;"
        )
        lay.addWidget(swatch)

        # Name
        name_lbl = QLabel(f"Zone {zone_idx + 1}")
        name_lbl.setMinimumWidth(50)
        lay.addWidget(name_lbl)

        # Alpha spinbox
        lay.addWidget(QLabel("α:"))
        self._alpha_spin = QSpinBox()
        self._alpha_spin.setRange(0, 255)
        self._alpha_spin.setValue(128)
        self._alpha_spin.setFixedWidth(58)
        self._alpha_spin.setToolTip(
            "Alpha value applied to all pixels painted in this zone (0=transparent, 255=opaque)."
        )
        lay.addWidget(self._alpha_spin)

        # Select button
        self._sel_btn = QPushButton("Select")
        self._sel_btn.setCheckable(True)
        self._sel_btn.setFixedWidth(58)
        self._sel_btn.clicked.connect(lambda: self.selected.emit(self._idx))
        lay.addWidget(self._sel_btn)

        # Clear button
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedWidth(50)
        clear_btn.setToolTip("Erase this zone's mask")
        clear_btn.clicked.connect(self._on_clear)
        lay.addWidget(clear_btn)

        lay.addStretch()

    def _on_clear(self) -> None:
        # Bubbles up to SelectiveAlphaTool via canvas
        self.selected.emit(-(self._idx + 1))  # negative = clear signal

    def alpha_value(self) -> int:
        return self._alpha_spin.value()

    def set_selected(self, selected: bool) -> None:
        self._sel_btn.setChecked(selected)
        self.setProperty("active", str(selected).lower())
        self.style().unpolish(self)
        self.style().polish(self)


# ---------------------------------------------------------------------------
# Main tab widget
# ---------------------------------------------------------------------------


class SelectiveAlphaTool(QWidget):
    """Tab widget for the Selective Alpha editor."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._src_path: str = ""
        self._setup_ui()

    # ----------------------------------------------------------------- setup

    def _setup_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # ── Left panel (controls) ─────────────────────────────────────────
        left_panel = QWidget()
        left_panel.setFixedWidth(230)
        lv = QVBoxLayout(left_panel)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)

        # Open / Save
        io_box = QGroupBox("Image")
        io_lay = QVBoxLayout(io_box)
        btn_open = QPushButton("📂  Open Image…")
        btn_open.clicked.connect(self._on_open)
        io_lay.addWidget(btn_open)
        self._btn_save = QPushButton("💾  Save Result…")
        self._btn_save.clicked.connect(self._on_save)
        self._btn_save.setEnabled(False)
        io_lay.addWidget(self._btn_save)
        lv.addWidget(io_box)

        # Drawing tools
        tools_box = QGroupBox("Drawing Tool")
        tg = QGridLayout(tools_box)
        tg.setSpacing(4)

        self._tool_btns: dict[str, QPushButton] = {}
        tool_defs = [
            ("freehand", "✏  Freehand",  0, 0),
            ("line",     "╱  Line",      0, 1),
            ("rect",     "▭  Rectangle", 1, 0),
            ("ellipse",  "◯  Ellipse",   1, 1),
            ("fill",     "🪣  Fill",      2, 0),
            ("polygon",  "⬠  Polygon",   2, 1),
        ]
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)

        for key, label, row, col in tool_defs:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip(self._tool_tooltip(key))
            self._tool_btns[key] = btn
            tg.addWidget(btn, row, col)
            self._tool_group.addButton(btn)
            btn.clicked.connect(lambda checked, k=key: self._on_tool_selected(k))

        # Default tool
        self._tool_btns["freehand"].setChecked(True)

        # Close Polygon button
        self._btn_close_poly = QPushButton("⬠ Close Polygon")
        self._btn_close_poly.setToolTip("Close and fill the in-progress polygon")
        self._btn_close_poly.setVisible(False)
        self._btn_close_poly.clicked.connect(self._on_close_polygon)
        tg.addWidget(self._btn_close_poly, 3, 0, 1, 2)

        lv.addWidget(tools_box)

        # Brush size
        brush_box = QGroupBox("Brush Size")
        bh = QHBoxLayout(brush_box)
        bh.addWidget(QLabel("Radius (px):"))
        self._brush_spin = QSpinBox()
        self._brush_spin.setRange(1, 200)
        self._brush_spin.setValue(10)
        self._brush_spin.valueChanged.connect(
            lambda v: self._canvas.set_brush_size(v)
        )
        bh.addWidget(self._brush_spin)
        lv.addWidget(brush_box)

        # Auto-correct
        self._autocorrect_chk = QCheckBox("Auto-correct (snap to edges)")
        self._autocorrect_chk.setToolTip(
            "When checked, freehand and line strokes are automatically snapped\n"
            "to nearby strong image edges after you release the mouse button.\n"
            "Helps rough strokes align to object boundaries without precise drawing."
        )
        self._autocorrect_chk.toggled.connect(
            lambda v: self._canvas.set_autocorrect(v)
        )
        lv.addWidget(self._autocorrect_chk)

        # Zoom controls
        zoom_box = QGroupBox("Zoom")
        zh = QHBoxLayout(zoom_box)
        btn_zi = QPushButton("＋")
        btn_zi.setFixedWidth(36)
        btn_zi.clicked.connect(self._zoom_in)
        btn_zo = QPushButton("－")
        btn_zo.setFixedWidth(36)
        btn_zo.clicked.connect(self._zoom_out)
        btn_zr = QPushButton("Fit")
        btn_zr.setFixedWidth(40)
        btn_zr.clicked.connect(self._zoom_reset)
        zh.addWidget(btn_zo)
        zh.addWidget(btn_zr)
        zh.addWidget(btn_zi)
        lv.addWidget(zoom_box)

        # Zone rows
        zones_box = QGroupBox("Alpha Zones  (click Select to paint)")
        zv = QVBoxLayout(zones_box)
        zv.setSpacing(2)
        self._zone_rows: list[_ZoneRow] = []
        for i in range(NUM_ZONES):
            row = _ZoneRow(i)
            row.selected.connect(self._on_zone_action)
            zv.addWidget(row)
            self._zone_rows.append(row)
        self._zone_rows[0].set_selected(True)
        lv.addWidget(zones_box)

        # Apply button
        self._btn_apply = QPushButton("✅  Apply Alpha Zones")
        self._btn_apply.setEnabled(False)
        self._btn_apply.setToolTip(
            "Apply the painted zones to the image and make the result ready to save."
        )
        self._btn_apply.clicked.connect(self._on_apply)
        lv.addWidget(self._btn_apply)

        # Clear all
        btn_clear_all = QPushButton("🗑  Clear All Zones")
        btn_clear_all.clicked.connect(self._on_clear_all)
        lv.addWidget(btn_clear_all)

        lv.addStretch()
        root.addWidget(left_panel)

        # ── Canvas ───────────────────────────────────────────────────────
        self._canvas = SelectiveAlphaCanvas()
        self._canvas.mask_changed.connect(self._on_mask_changed)
        root.addWidget(self._canvas, 1)

        # Sync initial tool to canvas
        self._canvas.set_tool("freehand")
        self._canvas.set_active_zone(0)

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _tool_tooltip(key: str) -> str:
        tips = {
            "freehand": "Paint freehand.  Hold & drag to brush over the image.",
            "line":     "Draw a straight filled line.  Drag from start to end.",
            "rect":     "Fill a rectangle.  Drag from one corner to the opposite.",
            "ellipse":  "Fill an ellipse.  Drag bounding box corner-to-corner.",
            "fill":     "Click to flood-fill a region.  Stops at image edges.",
            "polygon":  "Click to add vertices; double-click to close & fill.\n"
                        "Press Esc to cancel.",
        }
        return tips.get(key, "")

    def _set_btn_save_enabled(self, v: bool) -> None:
        self._btn_save.setEnabled(v)

    # ---------------------------------------------------------------- slots

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tiff *.tif *.webp *.tga *.gif *.ico);;All Files (*)",
        )
        if not path:
            return
        if not self._canvas.load_image(path):
            QMessageBox.warning(self, "Load Error", f"Could not load:\n{path}")
            return
        self._src_path = path
        self._btn_apply.setEnabled(True)
        self._btn_save.setEnabled(False)   # need to apply first
        self._result_img: Image.Image | None = None

    def _on_save(self) -> None:
        if not hasattr(self, "_result_img") or self._result_img is None:
            QMessageBox.information(
                self, "Nothing to save",
                "Press 'Apply Alpha Zones' first to generate the result."
            )
            return
        base, ext = os.path.splitext(self._src_path)
        default_path = (base + "_selective_alpha" + (ext or ".png")) if self._src_path else ""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Result",
            default_path,
            "PNG (*.png);;All Files (*)",
        )
        if not path:
            return
        try:
            self._result_img.save(path)
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))

    def _on_apply(self) -> None:
        if not self._canvas.has_image():
            return
        bool_masks = self._canvas.get_masks_as_bool()
        zone_alphas = [row.alpha_value() for row in self._zone_rows]

        # Warn if no zones are painted.
        if all(m is None or not m.any() for m in bool_masks):
            QMessageBox.information(
                self, "No zones painted",
                "Paint at least one zone before applying."
            )
            return

        try:
            src_img = self._canvas.get_source_image()
            result = apply_selective_alpha(
                src_img, bool_masks, zone_alphas
            )
            if hasattr(self, "_result_img") and self._result_img is not None:
                self._result_img.close()
            self._result_img = result
            self._btn_save.setEnabled(True)
            QMessageBox.information(
                self, "Done",
                "Alpha zones applied successfully.\n"
                "Click 'Save Result…' to export the image."
            )
        except Exception as exc:
            QMessageBox.critical(self, "Apply Error", str(exc))

    def _on_mask_changed(self, zone_idx: int) -> None:
        # Invalidate apply state when masks change
        pass  # canvas handles dirty flag internally

    def _on_zone_action(self, val: int) -> None:
        """val >= 0 → select zone; val < 0 → clear zone -(val+1)."""
        if val >= 0:
            self._canvas.set_active_zone(val)
            for i, row in enumerate(self._zone_rows):
                row.set_selected(i == val)
        else:
            zone_idx = -(val + 1)
            self._canvas.clear_mask(zone_idx)

    def _on_tool_selected(self, key: str) -> None:
        self._canvas.set_tool(key)
        self._btn_close_poly.setVisible(key == "polygon")

    def _on_close_polygon(self) -> None:
        """Programmatically close the in-progress polygon."""
        self._canvas.close_polygon()

    def _on_clear_all(self) -> None:
        self._canvas.clear_all_masks()

    def _zoom_in(self) -> None:
        self._canvas.zoom_by(1.25)

    def _zoom_out(self) -> None:
        self._canvas.zoom_by(1.0 / 1.25)

    def _zoom_reset(self) -> None:
        self._canvas.zoom_reset()
