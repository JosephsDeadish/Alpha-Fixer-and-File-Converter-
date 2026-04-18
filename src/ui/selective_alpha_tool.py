"""
Selective Alpha editor tab.

Lets the user paint up to 40 coloured mask zones on top of a single image
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
import time
from typing import Optional

# File extensions that do not support a full per-pixel alpha channel.
# Opening one of these shows a warning so the user can decide whether to
# continue (the image is still loaded) or cancel; the result should be
# saved as PNG to preserve the alpha channel.
_NO_ALPHA_EXTS = frozenset({".jpg", ".jpeg", ".bmp", ".gif"})

import numpy as np
from PIL import Image, ImageDraw

from PyQt6.QtCore import (
    Qt, QEvent, QPointF, QRectF, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFont, QImage, QPainter, QPen,
    QBrush, QKeySequence, QShortcut, QPixmap, QIcon,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QSpinBox, QCheckBox, QGroupBox,
    QFileDialog, QMessageBox, QScrollArea, QSizePolicy,
    QButtonGroup, QFrame, QColorDialog, QMenu, QComboBox,
    QAbstractSpinBox, QInputDialog, QSlider, QLineEdit,
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
    detect_alpha_zones,
    shift_mask,
    rotate_mask,
    scale_mask,
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


def _zone_qcolor(
    zone_idx: int,
    alpha: int = 200,
    color_override: Optional[tuple[int, int, int, int]] = None,
) -> QColor:
    """Return a QColor for *zone_idx* with the given *alpha*.

    If *color_override* is provided it is used in place of the default
    ``ZONE_COLORS`` palette entry for that zone.
    """
    if color_override is not None:
        r, g, b = color_override[0], color_override[1], color_override[2]
    else:
        zone_idx = max(0, min(len(ZONE_COLORS) - 1, zone_idx))
        r, g, b, _ = ZONE_COLORS[zone_idx]
    return QColor(r, g, b, alpha)


# ---------------------------------------------------------------------------
# Drawing canvas
# ---------------------------------------------------------------------------

_MAX_HISTORY = 50   # maximum number of undo steps kept


class SelectiveAlphaCanvas(QWidget):
    """Interactive drawing canvas for zone-based selective alpha editing.

    Displays a PIL image and lets the user paint up to NUM_ZONES coloured
    alpha-zone masks using multiple drawing tools.  Each zone can be
    independently assigned an alpha value; pressing "Apply" replaces the
    alpha channel of every painted pixel with its zone's alpha value.

    Public attributes accessed by SelectiveAlphaTool
    ─────────────────────────────────────────────────
    _tool         – current drawing tool name (str)
    _active_zone  – currently selected zone index (int)
    _zone_alphas  – per-zone alpha values (list[int], len=NUM_ZONES)
    _zone_visible – per-zone visibility flags (list[bool], len=NUM_ZONES)
    """

    # ── PyQt signals ─────────────────────────────────────────────────────
    mask_changed        = pyqtSignal(int)   # zone index whose mask was modified
    undo_available      = pyqtSignal(bool)  # whether Ctrl+Z is available
    redo_available      = pyqtSignal(bool)  # whether Ctrl+Y is available
    undo_count_changed  = pyqtSignal(int)   # number of steps on undo stack
    redo_count_changed  = pyqtSignal(int)   # number of steps on redo stack
    zoom_changed        = pyqtSignal(float) # emitted whenever the canvas zoom changes
    copy_requested      = pyqtSignal(int)   # context-menu: copy zone mask
    paste_requested     = pyqtSignal(int)   # context-menu: paste zone mask
    copy_all_requested  = pyqtSignal()      # context-menu: copy all zones
    paste_all_requested = pyqtSignal()      # context-menu: paste all zones
    cursor_moved        = pyqtSignal(int, int)  # image (x, y) when mouse moves over canvas (item 50)

    _OVERLAY_ALPHA = 160  # zone colour overlay opacity (0–255) — class default

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(200, 200)

        # ── image / masks ─────────────────────────────────────────────
        self._src_img: Optional[Image.Image] = None
        self._img_w: int = 0
        self._img_h: int = 0
        self._masks: list[np.ndarray] = [
            np.zeros((1, 1), dtype=np.uint8) for _ in range(NUM_ZONES)
        ]
        self._src_qimage: Optional[QImage] = None   # source cached as QImage

        # ── zone display settings ─────────────────────────────────────
        self._zone_alphas:  list[int]  = [255] * NUM_ZONES
        self._zone_visible: list[bool] = [True] * NUM_ZONES
        # Per-zone colour override (r, g, b) – None → use ZONE_COLORS default
        self._zone_colors: list[Optional[tuple[int, int, int]]] = [None] * NUM_ZONES
        # Overlay opacity (0–255); instance variable so it can be changed at runtime
        self._overlay_alpha: int = self._OVERLAY_ALPHA

        # ── tool state ────────────────────────────────────────────────
        self._tool:         str  = "freehand"
        self._active_zone:  int  = 0
        self._brush_size:   int  = 10
        self._eraser_size:  int  = 10
        self._autocorrect:        bool = False
        self._show_zero_alpha:    bool = False
        self._show_alpha_labels:  bool = False
        self._paste_available:    bool = False

        # ── drawing state ─────────────────────────────────────────────
        self._drawing:        bool = False
        self._last_img_pt:    Optional[tuple[int, int]] = None
        self._drag_start_img: Optional[tuple[int, int]] = None
        self._poly_pts:       list[tuple[int, int]]     = []
        self._transform_start_mouse: Optional[tuple[float, float]] = None
        self._transform_orig_mask:   Optional[np.ndarray]          = None

        # ── zoom / pan ────────────────────────────────────────────────
        self._zoom:  float = 1.0
        self._pan_x: float = 0.0
        self._pan_y: float = 0.0
        self._panning:          bool = False
        self._pan_start_mouse:  Optional[tuple[float, float]] = None
        self._pan_start_offset: Optional[tuple[float, float]] = None

        # ── rendering cache ───────────────────────────────────────────
        self._composite_dirty:  bool = True
        self._composite_qimage: Optional[QImage] = None
        self._drag_preview:     Optional[QImage] = None  # line/rect/ellipse preview

        # ── cursor circle for brush size indicator ─────────────────────
        self._cursor_wx: float = -1.0
        self._cursor_wy: float = -1.0
        self._cursor_on_canvas: bool = False

        # Label geometry caches (computed in _rebuild_composite)
        self._zone_centroids:    list[Optional[tuple[int, int]]] = [None] * NUM_ZONES
        self._zone_label_points: list[list[tuple[int, int]]]     = [[] for _ in range(NUM_ZONES)]

        # ── undo / redo ───────────────────────────────────────────────
        # Each stack entry is a list[NUM_ZONES] of mask array copies.
        self._undo_stack: list[list[np.ndarray]] = []
        self._redo_stack: list[list[np.ndarray]] = []

    # ── public setters ────────────────────────────────────────────────────

    def set_brush_size(self, v: int) -> None:
        self._brush_size = max(1, int(v))

    def set_eraser_size(self, v: int) -> None:
        self._eraser_size = max(1, int(v))

    def set_autocorrect(self, v: bool) -> None:
        self._autocorrect = bool(v)

    def set_show_zero_alpha(self, v: bool) -> None:
        self._show_zero_alpha = bool(v)
        self._composite_dirty = True
        self.update()

    def set_show_alpha_labels(self, v: bool) -> None:
        self._show_alpha_labels = bool(v)
        self._composite_dirty = True   # need to recompute label-point geometry
        self.update()

    def set_paste_available(self, v: bool) -> None:
        self._paste_available = bool(v)

    def set_tool(self, key: str) -> None:
        if key != self._tool:
            if self._tool == "polygon" and self._poly_pts:
                self._poly_pts.clear()
                self.update()
            self._drawing        = False
            self._drag_start_img = None
            self._drag_preview   = None
        self._tool = key
        if key == "transform":
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif key == "fill":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def set_active_zone(self, idx: int) -> None:
        if 0 <= idx < NUM_ZONES:
            self._active_zone = idx

    def get_active_zone(self) -> int:
        return self._active_zone

    def set_zone_alpha_label(self, idx: int, alpha: int) -> None:
        if 0 <= idx < NUM_ZONES:
            self._zone_alphas[idx] = max(0, min(255, int(alpha)))
            self._composite_dirty = True
            self.update()

    def set_zone_color(self, idx: int, r: int, g: int, b: int) -> None:
        if 0 <= idx < NUM_ZONES:
            self._zone_colors[idx] = (int(r), int(g), int(b))
            self._composite_dirty = True
            self.update()

    def get_zone_color(self, idx: int) -> tuple[int, int, int, int]:
        """Return *(r, g, b, 255)* for zone *idx* (override or palette default)."""
        if 0 <= idx < NUM_ZONES and self._zone_colors[idx] is not None:
            r, g, b = self._zone_colors[idx]
        else:
            zi = max(0, min(len(ZONE_COLORS) - 1, idx))
            r, g, b, _ = ZONE_COLORS[zi]
        return (int(r), int(g), int(b), 255)

    def set_zone_visible(self, idx: int, visible: bool) -> None:
        if 0 <= idx < NUM_ZONES:
            self._zone_visible[idx] = bool(visible)
            self._composite_dirty = True
            self.update()

    def set_overlay_alpha(self, value: int) -> None:
        """Set the zone colour overlay opacity (0 = invisible, 255 = fully opaque)."""
        v = max(0, min(255, int(value)))
        if v != self._overlay_alpha:
            self._overlay_alpha = v
            self._composite_dirty = True
            self.update()

    # ── image management ──────────────────────────────────────────────────

    def has_image(self) -> bool:
        return self._src_img is not None

    def load_image(self, path: str) -> bool:
        """Load an image from *path*.  Returns True on success."""
        try:
            img = Image.open(path)
            img.load()
        except Exception:
            return False
        self.unload_image()
        self._src_img = img.convert("RGBA") if img.mode != "RGBA" else img
        self._img_w, self._img_h = self._src_img.size
        self._masks = [
            np.zeros((self._img_h, self._img_w), dtype=np.uint8)
            for _ in range(NUM_ZONES)
        ]
        self._src_qimage = _pil_to_qimage(self._src_img)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._emit_undo_redo_state()
        self._composite_dirty = True
        self._poly_pts.clear()
        self._zoom_fit()
        self.update()
        return True

    def unload_image(self) -> None:
        if self._src_img is not None:
            self._src_img.close()
            self._src_img = None
        self._src_qimage = None
        self._img_w = self._img_h = 0
        self._masks = [np.zeros((1, 1), dtype=np.uint8) for _ in range(NUM_ZONES)]
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._composite_dirty = True
        self._poly_pts.clear()
        self.update()

    def get_source_image(self) -> Optional[Image.Image]:
        return self._src_img

    # ── mask access ───────────────────────────────────────────────────────

    def get_mask_as_array(self, zone_idx: int) -> Optional[np.ndarray]:
        """Return a copy of zone *zone_idx*'s mask, or None if empty."""
        if not self.has_image() or not self._masks[zone_idx].any():
            return None
        return self._masks[zone_idx].copy()

    def set_mask_from_array(self, zone_idx: int, arr: np.ndarray) -> None:
        if not self.has_image():
            return
        self._push_history()
        h, w = self._img_h, self._img_w
        if arr.shape == (h, w):
            self._masks[zone_idx] = arr.astype(np.uint8)
        elif arr.ndim == 2 and arr.shape[0] > 0 and arr.shape[1] > 0:
            # Resize the mask to match the current image dimensions.
            # This handles cases where a mask was copied from an image of a
            # different size (e.g. import from a different-resolution source).
            try:
                from PIL import Image as _PILImage
                src = _PILImage.fromarray(arr.astype(np.uint8), mode="L")
                resized = src.resize((w, h), _PILImage.NEAREST)
                self._masks[zone_idx] = np.array(resized, dtype=np.uint8)
            except Exception:
                # Fallback: nearest-neighbour via numpy slicing
                import numpy as np_fb
                src_h, src_w = arr.shape
                row_idx = np_fb.clip(
                    (np_fb.arange(h) * src_h // h), 0, src_h - 1)
                col_idx = np_fb.clip(
                    (np_fb.arange(w) * src_w // w), 0, src_w - 1)
                self._masks[zone_idx] = arr.astype(np.uint8)[np_fb.ix_(row_idx, col_idx)]
        else:
            self._masks[zone_idx] = np.zeros((h, w), dtype=np.uint8)
        self._composite_dirty = True
        self.mask_changed.emit(zone_idx)
        self.update()

    def get_masks_as_bool(self) -> list[np.ndarray]:
        return [m.astype(bool) for m in self._masks]

    def get_all_masks(self) -> list[np.ndarray]:
        """Return a snapshot of all masks as a list of array copies."""
        return [m.copy() for m in self._masks]

    def set_all_masks(self, snapshot: list[np.ndarray]) -> None:
        """Restore all masks from a *snapshot* produced by get_all_masks()."""
        if not self.has_image():
            return
        self._push_history()
        for i, m in enumerate(snapshot):
            if i < NUM_ZONES:
                self._masks[i] = m.copy()
        self._composite_dirty = True
        for i in range(NUM_ZONES):
            self.mask_changed.emit(i)
        self.update()

    def populate_zones_from_detection(self, zones: list) -> None:
        """Fill zone masks from *zones* = [(alpha_val, bool_mask), ...]."""
        if not self.has_image():
            return
        self._push_history()
        h, w = self._img_h, self._img_w
        for i in range(NUM_ZONES):
            self._masks[i].fill(0)
        for i, (_, bool_mask) in enumerate(zones):
            if i >= NUM_ZONES:
                break
            if bool_mask.shape == (h, w):
                self._masks[i] = bool_mask.astype(np.uint8)
            self.mask_changed.emit(i)
        self._composite_dirty = True
        self.update()

    # ── undo / redo ───────────────────────────────────────────────────────

    def _push_history(self) -> None:
        """Snapshot current masks onto the undo stack."""
        self._undo_stack.append([m.copy() for m in self._masks])
        if len(self._undo_stack) > _MAX_HISTORY:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._emit_undo_redo_state()

    def _emit_undo_redo_state(self) -> None:
        self.undo_available.emit(bool(self._undo_stack))
        self.redo_available.emit(bool(self._redo_stack))
        self.undo_count_changed.emit(len(self._undo_stack))
        self.redo_count_changed.emit(len(self._redo_stack))

    def undo_mask(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append([m.copy() for m in self._masks])
        snapshot = self._undo_stack.pop()
        for i, m in enumerate(snapshot):
            self._masks[i] = m.copy()
        self._composite_dirty = True
        self._emit_undo_redo_state()
        for i in range(NUM_ZONES):
            self.mask_changed.emit(i)
        self.update()

    def redo_mask(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append([m.copy() for m in self._masks])
        snapshot = self._redo_stack.pop()
        for i, m in enumerate(snapshot):
            self._masks[i] = m.copy()
        self._composite_dirty = True
        self._emit_undo_redo_state()
        for i in range(NUM_ZONES):
            self.mask_changed.emit(i)
        self.update()

    # ── zone mask operations ──────────────────────────────────────────────

    def clear_mask(self, zone_idx: int) -> None:
        if not self.has_image():
            return
        self._push_history()
        self._masks[zone_idx].fill(0)
        self._composite_dirty = True
        self.mask_changed.emit(zone_idx)
        self.update()

    def clear_all_masks(self) -> None:
        if not self.has_image():
            return
        self._push_history()
        for i in range(NUM_ZONES):
            self._masks[i].fill(0)
        self._composite_dirty = True
        for i in range(NUM_ZONES):
            self.mask_changed.emit(i)
        self.update()

    # ── zoom / pan ────────────────────────────────────────────────────────

    def _zoom_fit(self) -> None:
        """Fit and centre the image within the current widget dimensions."""
        if not self.has_image() or self.width() <= 0 or self.height() <= 0:
            self._zoom  = 1.0
            self._pan_x = self._pan_y = 0.0
            return
        scale_x = self.width()  / self._img_w
        scale_y = self.height() / self._img_h
        self._zoom  = min(scale_x, scale_y) * 0.97
        self._pan_x = (self.width()  - self._img_w * self._zoom) / 2.0
        self._pan_y = (self.height() - self._img_h * self._zoom) / 2.0

    def zoom_by(self, factor: float) -> None:
        cx, cy   = self.width() / 2.0, self.height() / 2.0
        new_zoom = max(0.05, min(32.0, self._zoom * factor))
        self._pan_x = cx - (cx - self._pan_x) * (new_zoom / self._zoom)
        self._pan_y = cy - (cy - self._pan_y) * (new_zoom / self._zoom)
        self._zoom  = new_zoom
        self.zoom_changed.emit(self._zoom)
        self.update()

    def zoom_reset(self) -> None:
        self._zoom_fit()
        self.zoom_changed.emit(self._zoom)
        self.update()

    def center_on_zone(self, zone_idx: int) -> None:
        """Pan the canvas to center on zone *zone_idx*'s centroid (item 49).

        Does nothing if the zone has no painted pixels (centroid is None).
        """
        c = self._zone_centroids[zone_idx]
        if c is None:
            return
        ix, iy = c
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        self._pan_x = cx - ix * self._zoom
        self._pan_y = cy - iy * self._zoom
        self.update()

    # ── coordinate helpers ────────────────────────────────────────────────

    def _w2i(self, wx: float, wy: float) -> tuple[float, float]:
        """Widget coordinates → image coordinates (float)."""
        return (wx - self._pan_x) / self._zoom, (wy - self._pan_y) / self._zoom

    def _clamp_img(self, ix: float, iy: float) -> tuple[int, int]:
        """Clamp float image coordinates to valid integer pixel indices."""
        return (
            max(0, min(self._img_w - 1, int(round(ix)))),
            max(0, min(self._img_h - 1, int(round(iy)))),
        )

    # ── rendering ─────────────────────────────────────────────────────────

    def _rebuild_composite(self) -> QImage:
        """Compose source image + visible zone overlays into a QImage."""
        if not self.has_image():
            qi = QImage(max(1, self.width()), max(1, self.height()),
                        QImage.Format.Format_RGBA8888)
            qi.fill(Qt.GlobalColor.black)
            return qi

        src_arr = np.array(self._src_img, dtype=np.uint8).copy()

        blend = self._overlay_alpha / 255.0
        for i in range(NUM_ZONES):
            if not self._zone_visible[i]:
                continue
            if not self._show_zero_alpha and self._zone_alphas[i] == 0:
                continue
            mask = self._masks[i]
            if not mask.any():
                continue
            r, g, b, _ = self.get_zone_color(i)
            where = mask.astype(bool)
            src_arr[where, 0] = np.clip(
                src_arr[where, 0] * (1.0 - blend) + r * blend, 0, 255).astype(np.uint8)
            src_arr[where, 1] = np.clip(
                src_arr[where, 1] * (1.0 - blend) + g * blend, 0, 255).astype(np.uint8)
            src_arr[where, 2] = np.clip(
                src_arr[where, 2] * (1.0 - blend) + b * blend, 0, 255).astype(np.uint8)
            src_arr[where, 3] = 255

        # Recompute label-point geometry (used by _draw_alpha_labels)
        self._zone_centroids    = [None] * NUM_ZONES
        self._zone_label_points = [[] for _ in range(NUM_ZONES)]
        if self._show_alpha_labels:
            total_px = self._img_w * self._img_h
            # Use a consistent stride so all zones get similar label density
            label_stride = max(16, min(self._img_w, self._img_h) // 8)
            half = label_stride // 2
            for i in range(NUM_ZONES):
                mask = self._masks[i]
                if not mask.any():
                    continue
                ys, xs = np.where(mask)
                self._zone_centroids[i] = (int(xs.mean()), int(ys.mean()))
                # Sparse grid at uniform stride; cap at 40 to stay fast
                pts: list[tuple[int, int]] = []
                for sy in range(half, self._img_h, label_stride):
                    for sx in range(half, self._img_w, label_stride):
                        if mask[sy, sx]:
                            pts.append((sx, sy))
                self._zone_label_points[i] = pts[:40]

        return _np_to_qimage(src_arr)

    def _draw_alpha_labels(self, painter: QPainter) -> None:
        """Draw alpha-value text over each visible zone (in widget space).
        
        Labels are drawn with a black outline and white text (matching the
        style of the Alpha & RGBA Adjuster tool's highlight overlay).
        """
        if not self._show_alpha_labels:
            return
        z, px, py = self._zoom, self._pan_x, self._pan_y
        # Font scales with zoom; keep legible range
        px_size = max(8, min(16, int(10 * max(z, 0.5))))
        font = QFont("Arial", px_size, QFont.Weight.Bold)
        big_font = QFont("Arial", max(10, int(14 * max(z, 0.5))), QFont.Weight.Bold)
        outline_color = QColor(0, 0, 0, 200)
        text_color = QColor(255, 255, 255, 230)
        _offsets = ((-1, 0), (1, 0), (0, -1), (0, 1))

        def _draw_labeled(f, wx, wy, text):
            painter.setFont(f)
            painter.setPen(outline_color)
            for ox, oy in _offsets:
                painter.drawText(QPointF(wx + ox, wy + oy), text)
            painter.setPen(text_color)
            painter.drawText(QPointF(wx, wy), text)

        for i in range(NUM_ZONES):
            if not self._zone_visible[i]:
                continue
            text = str(self._zone_alphas[i])

            # Grid-point labels (small) at pre-computed sparse positions
            for ix, iy in self._zone_label_points[i]:
                wx, wy = ix * z + px, iy * z + py
                _draw_labeled(font, wx, wy, text)

            # Centroid label (larger, one per zone)
            c = self._zone_centroids[i]
            if c is not None:
                wx, wy = c[0] * z + px, c[1] * z + py
                _draw_labeled(big_font, wx, wy, text)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(40, 40, 40))

        if not self.has_image():
            painter.setPen(QColor(120, 120, 120))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Drop or open an image to begin\n\n"
                "Ctrl+O  ·  open image\n"
                "Ctrl+Z / Ctrl+Y  ·  undo / redo\n"
                "B Brush  ·  E Eraser  ·  L Line  ·  R Rect  ·  X Ellipse  ·  F Fill  ·  P Polygon  ·  T Transform\n"
                "[ / ]  ·  decrease / increase brush size\n"
                "🖱 Hold scroll-wheel button and drag to pan  ·  Ctrl+scroll to zoom\n"
                "H  ·  toggle highlights  ·  N / Shift+N  ·  cycle zones\n"
                "Ctrl+Enter / Ctrl+S  ·  save result",
            )
            return

        if self._composite_dirty or self._composite_qimage is None:
            self._composite_qimage = self._rebuild_composite()
            self._composite_dirty  = False

        dest = QRectF(
            self._pan_x, self._pan_y,
            self._img_w * self._zoom, self._img_h * self._zoom,
        )
        painter.drawImage(dest, self._composite_qimage)

        # Drag-tool preview (line / rect / ellipse while mouse held)
        if self._drag_preview is not None and self._drawing:
            painter.drawImage(dest, self._drag_preview)

        # In-progress polygon outline
        if self._tool == "polygon" and self._poly_pts:
            self._paint_polygon_preview(painter)

        # Alpha-value labels
        self._draw_alpha_labels(painter)

        # Brush/eraser size cursor ring
        self._draw_cursor_ring(painter)

    def _draw_cursor_ring(self, painter: QPainter) -> None:
        """Draw a circle at the cursor position showing the current brush radius."""
        if not self._cursor_on_canvas or not self.has_image():
            return
        if self._tool not in ("freehand", "eraser"):
            return
        is_erase = self._tool == "eraser"
        radius_px = (self._eraser_size if is_erase else self._brush_size) * self._zoom
        cx, cy = self._cursor_wx, self._cursor_wy
        # Outer ring: contrasting outline so it's visible on any background
        pen_out = QPen(QColor(0, 0, 0, 140), 2.0)
        pen_in  = QPen(QColor(255, 255, 255, 200), 1.0)
        pen_out.setStyle(Qt.PenStyle.SolidLine)
        pen_in.setStyle(Qt.PenStyle.SolidLine)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Draw dark outer ring then white inner ring for contrast
        painter.setPen(pen_out)
        painter.drawEllipse(QPointF(cx, cy), radius_px + 1, radius_px + 1)
        painter.setPen(pen_in)
        painter.drawEllipse(QPointF(cx, cy), radius_px, radius_px)

    def _paint_polygon_preview(self, painter: QPainter) -> None:
        z, px, py = self._zoom, self._pan_x, self._pan_y
        pen = QPen(QColor(255, 220, 0, 220), max(1.0, z))
        painter.setPen(pen)
        pts_w = [QPointF(ix * z + px, iy * z + py) for ix, iy in self._poly_pts]
        for j in range(len(pts_w) - 1):
            painter.drawLine(pts_w[j], pts_w[j + 1])
        for p in pts_w:
            painter.drawEllipse(p, 3.0, 3.0)

    def resizeEvent(self, event) -> None:  # noqa: N802
        # Re-fit on first proper resize (widget area was 0×0 at load_image time).
        if self.has_image() and self._zoom <= 1.0 and self._pan_x == 0.0:
            self._zoom_fit()
        super().resizeEvent(event)

    # ── input events ──────────────────────────────────────────────────────

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta    = event.angleDelta().y()
            factor   = 1.15 if delta > 0 else (1.0 / 1.15)
            mx, my   = event.position().x(), event.position().y()
            new_zoom = max(0.05, min(32.0, self._zoom * factor))
            self._pan_x = mx - (mx - self._pan_x) * (new_zoom / self._zoom)
            self._pan_y = my - (my - self._pan_y) * (new_zoom / self._zoom)
            self._zoom  = new_zoom
            self.zoom_changed.emit(self._zoom)
            self.update()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos  = event.position()
        wx, wy = pos.x(), pos.y()
        btn  = event.button()
        mods = event.modifiers()

        # Middle-mouse or Alt+Left → start pan
        if btn == Qt.MouseButton.MiddleButton or (
                btn == Qt.MouseButton.LeftButton
                and (mods & Qt.KeyboardModifier.AltModifier)):
            self._panning          = True
            self._pan_start_mouse  = (wx, wy)
            self._pan_start_offset = (self._pan_x, self._pan_y)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if btn != Qt.MouseButton.LeftButton or not self.has_image():
            return

        ix, iy       = self._w2i(wx, wy)
        img_x, img_y = self._clamp_img(ix, iy)
        mask = self._masks[self._active_zone]

        if self._tool == "polygon":
            self._poly_pts.append((img_x, img_y))
            self.update()
            return

        if self._tool in ("line", "rect", "ellipse"):
            self._drawing        = True
            self._drag_start_img = (img_x, img_y)
            self._drag_preview   = None
            return

        if self._tool == "transform":
            self._drawing = True
            self._push_history()
            self._transform_start_mouse = (wx, wy)
            self._transform_orig_mask   = mask.copy()
            return

        # freehand / eraser / fill
        self._push_history()
        self._drawing = True
        is_erase = self._tool == "eraser"

        if self._tool == "fill":
            self._do_fill(mask, img_x, img_y)
            if self._autocorrect:
                self._do_autocorrect(mask)
        else:
            self._paint_circle(
                mask, img_x, img_y,
                self._eraser_size if is_erase else self._brush_size,
                erase=is_erase,
            )
            self._last_img_pt = (img_x, img_y)

        self._composite_dirty = True
        self.mask_changed.emit(self._active_zone)
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos  = event.position()
        wx, wy = pos.x(), pos.y()

        # Always track cursor position for the brush-size indicator circle
        self._cursor_wx = wx
        self._cursor_wy = wy
        self._cursor_on_canvas = True

        # Emit image-space coordinates for the info panel (item 50)
        if self.has_image():
            ix, iy = self._w2i(wx, wy)
            ix = max(0, min(int(ix), self._img_w - 1))
            iy = max(0, min(int(iy), self._img_h - 1))
            self.cursor_moved.emit(ix, iy)

        if self._panning and self._pan_start_mouse is not None:
            dx = wx - self._pan_start_mouse[0]
            dy = wy - self._pan_start_mouse[1]
            self._pan_x = self._pan_start_offset[0] + dx
            self._pan_y = self._pan_start_offset[1] + dy
            self.update()
            return

        if not self._drawing or not self.has_image():
            self.update()  # redraw cursor ring even when not drawing
            return

        ix, iy       = self._w2i(wx, wy)
        img_x, img_y = self._clamp_img(ix, iy)
        mask = self._masks[self._active_zone]

        if self._tool in ("freehand", "eraser"):
            is_erase = self._tool == "eraser"
            sz = self._eraser_size if is_erase else self._brush_size
            if self._last_img_pt is not None:
                self._paint_line(
                    mask,
                    self._last_img_pt[0], self._last_img_pt[1],
                    img_x, img_y, sz, erase=is_erase,
                )
            self._last_img_pt     = (img_x, img_y)
            self._composite_dirty = True
            self.mask_changed.emit(self._active_zone)
            self.update()

        elif self._tool in ("line", "rect", "ellipse") and self._drag_start_img is not None:
            self._drag_preview = self._build_drag_preview(
                self._drag_start_img, (img_x, img_y))
            self.update()

        elif self._tool == "transform" and self._transform_start_mouse is not None:
            dx = int((wx - self._transform_start_mouse[0]) / self._zoom)
            dy = int((wy - self._transform_start_mouse[1]) / self._zoom)
            if self._transform_orig_mask is not None:
                self._masks[self._active_zone] = shift_mask(
                    self._transform_orig_mask, dx, dy)
                self._composite_dirty = True
                self.mask_changed.emit(self._active_zone)
                self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        pos  = event.position()
        wx, wy = pos.x(), pos.y()
        btn = event.button()

        if btn == Qt.MouseButton.MiddleButton or self._panning:
            self._panning          = False
            self._pan_start_mouse  = None
            self._pan_start_offset = None
            self.set_tool(self._tool)   # restore normal cursor
            return

        if btn != Qt.MouseButton.LeftButton or not self.has_image():
            return
        if not self._drawing:
            return

        ix, iy       = self._w2i(wx, wy)
        img_x, img_y = self._clamp_img(ix, iy)
        mask = self._masks[self._active_zone]

        if self._tool in ("line", "rect", "ellipse") and self._drag_start_img is not None:
            self._push_history()
            sx, sy = self._drag_start_img
            if self._tool == "line":
                self._paint_line(mask, sx, sy, img_x, img_y, self._brush_size)
                if self._autocorrect:
                    self._do_autocorrect(mask)
            elif self._tool == "rect":
                self._paint_rect(mask, sx, sy, img_x, img_y)
            elif self._tool == "ellipse":
                self._paint_ellipse(mask, sx, sy, img_x, img_y)
            self._drag_start_img  = None
            self._drag_preview    = None
            self._composite_dirty = True
            self.mask_changed.emit(self._active_zone)
            self.update()

        elif self._tool == "freehand" and self._autocorrect:
            self._do_autocorrect(mask)
            self._composite_dirty = True
            self.mask_changed.emit(self._active_zone)
            self.update()

        elif self._tool == "transform":
            self._transform_start_mouse = None
            self._transform_orig_mask   = None

        self._drawing     = False
        self._last_img_pt = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._tool == "polygon" and event.button() == Qt.MouseButton.LeftButton:
            self.close_polygon()

    def close_polygon(self) -> None:
        """Close and fill the in-progress polygon (≥3 points required)."""
        if len(self._poly_pts) < 3:
            self._poly_pts.clear()
            self.update()
            return
        self._push_history()
        mask = self._masks[self._active_zone]
        self._do_paint_polygon(mask, self._poly_pts)
        self._poly_pts.clear()
        self._composite_dirty = True
        self.mask_changed.emit(self._active_zone)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        """Hide the brush circle when the cursor leaves the canvas."""
        self._cursor_on_canvas = False
        self.update()

    # ── drawing primitives ────────────────────────────────────────────────

    def _paint_circle(self, mask: np.ndarray, cx: int, cy: int,
                      radius: int, erase: bool = False) -> None:
        h, w = mask.shape
        r    = max(0, radius)
        x0, x1 = max(0, cx - r), min(w, cx + r + 1)
        y0, y1 = max(0, cy - r), min(h, cy + r + 1)
        if x0 >= x1 or y0 >= y1:
            return
        yy, xx = np.ogrid[y0:y1, x0:x1]
        circle = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
        mask[y0:y1, x0:x1][circle] = 0 if erase else 1

    def _paint_line(self, mask: np.ndarray,
                    x0: int, y0: int, x1: int, y1: int,
                    radius: int, erase: bool = False) -> None:
        """Bresenham line of brush circles between two image-coord points."""
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(steps + 1):
            t = i / steps
            x = int(round(x0 + (x1 - x0) * t))
            y = int(round(y0 + (y1 - y0) * t))
            self._paint_circle(mask, x, y, radius, erase=erase)

    def _paint_rect(self, mask: np.ndarray,
                    x0: int, y0: int, x1: int, y1: int) -> None:
        h, w = mask.shape
        lx, rx = sorted((x0, x1))
        ty, by = sorted((y0, y1))
        lx = max(0, lx);  rx = min(w - 1, rx)
        ty = max(0, ty);  by = min(h - 1, by)
        mask[ty:by + 1, lx:rx + 1] = 1

    def _paint_ellipse(self, mask: np.ndarray,
                       x0: int, y0: int, x1: int, y1: int) -> None:
        h, w  = mask.shape
        lx, rx = sorted((x0, x1))
        ty, by = sorted((y0, y1))
        cx_f  = (lx + rx) / 2.0
        cy_f  = (ty + by) / 2.0
        rx_f  = max(1.0, (rx - lx) / 2.0)
        ry_f  = max(1.0, (by - ty) / 2.0)
        y_min = max(0, ty);   y_max = min(h, by + 1)
        x_min = max(0, lx);   x_max = min(w, rx + 1)
        if y_min >= y_max or x_min >= x_max:
            return
        yy, xx = np.ogrid[y_min:y_max, x_min:x_max]
        ell = ((xx - cx_f) / rx_f) ** 2 + ((yy - cy_f) / ry_f) ** 2 <= 1.0
        mask[y_min:y_max, x_min:x_max][ell] = 1

    def _do_paint_polygon(self, mask: np.ndarray,
                          pts: list[tuple[int, int]]) -> None:
        """Fill a polygon using PIL ImageDraw."""
        img  = Image.fromarray(mask * 255, mode="L")
        draw = ImageDraw.Draw(img)
        draw.polygon(pts, fill=255)
        mask[:] = (np.array(img) > 0).astype(np.uint8)

    def _do_fill(self, mask: np.ndarray, x: int, y: int) -> None:
        if self._src_img is None:
            return
        src_arr = np.array(self._src_img, dtype=np.uint8)
        edges   = detect_edges(src_arr)
        result  = edge_flood_fill(mask.astype(bool), edges, x, y)
        mask[:] = result.astype(np.uint8)

    def _do_autocorrect(self, mask: np.ndarray) -> None:
        if self._src_img is None:
            return
        try:
            src_arr   = np.array(self._src_img, dtype=np.uint8)
            edges     = detect_edges(src_arr)
            corrected = autocorrect_mask(mask.astype(bool), edges)
            mask[:]   = corrected.astype(np.uint8)
        except Exception:
            pass  # autocorrect is best-effort; silently skip on failure

    def _build_drag_preview(self, start: tuple[int, int],
                             end: tuple[int, int]) -> QImage:
        """Build a translucent preview QImage for drag-based tools."""
        preview = np.zeros((self._img_h, self._img_w), dtype=np.uint8)
        sx, sy  = start
        ex, ey  = end
        if self._tool == "line":
            self._paint_line(preview, sx, sy, ex, ey, self._brush_size)
        elif self._tool == "rect":
            self._paint_rect(preview, sx, sy, ex, ey)
        elif self._tool == "ellipse":
            self._paint_ellipse(preview, sx, sy, ex, ey)
        r, g, b, _ = self.get_zone_color(self._active_zone)
        rgba = np.zeros((self._img_h, self._img_w, 4), dtype=np.uint8)
        rgba[preview.astype(bool)] = (r, g, b, 100)
        return _np_to_qimage(rgba)

    # ── context menu ──────────────────────────────────────────────────────

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act_copy      = menu.addAction("Copy Zone Mask")
        act_paste     = menu.addAction("Paste Zone Mask")
        act_paste.setEnabled(self._paste_available)
        menu.addSeparator()
        act_copy_all  = menu.addAction("Copy All Zones")
        act_paste_all = menu.addAction("Paste All Zones")
        act_paste_all.setEnabled(self._paste_available)
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is act_copy:
            self.copy_requested.emit(self._active_zone)
        elif chosen is act_paste:
            self.paste_requested.emit(self._active_zone)
        elif chosen is act_copy_all:
            self.copy_all_requested.emit()
        elif chosen is act_paste_all:
            self.paste_all_requested.emit()


class _FloatingZoomOverlay(QFrame):
    """Semi-transparent floating overlay with Zoom In / Fit / Zoom Out buttons.

    Positioned at the top-right corner of its parent widget.  Reparent to the
    widget you want it to float over and call ``reposition()`` from the parent's
    ``resizeEvent`` to keep it pinned.
    """

    def __init__(self, zoom_in_cb, zoom_out_cb, zoom_fit_cb, parent=None):
        super().__init__(parent)
        self.setObjectName("zoomOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(
            "QFrame#zoomOverlay {"
            "  background: rgba(30, 30, 30, 160);"
            "  border-radius: 6px;"
            "  border: 1px solid rgba(255,255,255,40);"
            "}"
            "QPushButton {"
            "  background: rgba(60,60,60,200);"
            "  color: #eee;"
            "  border: none;"
            "  border-radius: 4px;"
            "  font-size: 13px;"
            "  min-width: 26px;"
            "  max-width: 26px;"
            "  min-height: 22px;"
            "  max-height: 22px;"
            "  padding: 0;"
            "}"
            "QPushButton:hover { background: rgba(100,100,100,220); }"
            "QPushButton:pressed { background: rgba(40,40,40,255); }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 3, 4, 3)
        row.setSpacing(3)
        btn_out = QPushButton("－")
        btn_out.setToolTip("Zoom out  (Ctrl+scroll)")
        btn_out.clicked.connect(zoom_out_cb)
        btn_fit = QPushButton("⊡")
        btn_fit.setToolTip("Reset zoom / fit to window")
        btn_fit.clicked.connect(zoom_fit_cb)
        btn_in = QPushButton("＋")
        btn_in.setToolTip("Zoom in  (Ctrl+scroll)")
        btn_in.clicked.connect(zoom_in_cb)
        row.addWidget(btn_out)
        row.addWidget(btn_fit)
        row.addWidget(btn_in)
        self._zoom_lbl = QLabel("100%")
        self._zoom_lbl.setStyleSheet("color: #ccc; font-size: 10px; min-width: 34px;")
        self._zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._zoom_lbl)
        self.adjustSize()
        self.raise_()

    def set_zoom(self, zoom: float) -> None:
        """Update the zoom percentage label."""
        self._zoom_lbl.setText(f"{int(round(zoom * 100))}%")

    def reposition(self, parent_size) -> None:
        """Pin the overlay to the top-right corner of *parent_size*."""
        margin = 6
        self.move(parent_size.width() - self.width() - margin, margin)


class _FloatingHistoryOverlay(QFrame):
    """Semi-transparent floating overlay with Undo / Redo canvas-drawing buttons.

    Positioned at the top-left corner of its parent widget.  Reparent to the
    canvas widget and call ``reposition()`` from the parent's ``resizeEvent``.
    """

    def __init__(self, undo_cb, redo_cb, parent=None):
        super().__init__(parent)
        self.setObjectName("historyOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(
            "QFrame#historyOverlay {"
            "  background: rgba(30, 30, 30, 160);"
            "  border-radius: 6px;"
            "  border: 1px solid rgba(255,255,255,40);"
            "}"
            "QPushButton {"
            "  background: rgba(60,60,60,200);"
            "  color: #eee;"
            "  border: none;"
            "  border-radius: 4px;"
            "  font-size: 12px;"
            "  min-height: 22px;"
            "  max-height: 22px;"
            "  padding: 0 4px;"
            "}"
            "QPushButton:hover { background: rgba(100,100,100,220); }"
            "QPushButton:pressed { background: rgba(40,40,40,255); }"
            "QPushButton:disabled { color: rgba(150,150,150,120); }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 3, 4, 3)
        row.setSpacing(3)
        self._btn_undo = QPushButton("↩")
        self._btn_undo.setToolTip("Undo the last brush/erase action  (Ctrl+Z)")
        self._btn_undo.setEnabled(False)
        self._btn_undo.clicked.connect(undo_cb)
        self._btn_redo = QPushButton("↪")
        self._btn_redo.setToolTip("Redo the last undone action  (Ctrl+Y)")
        self._btn_redo.setEnabled(False)
        self._btn_redo.clicked.connect(redo_cb)
        row.addWidget(self._btn_undo)
        row.addWidget(self._btn_redo)
        self.adjustSize()
        self.raise_()

    def set_undo_enabled(self, enabled: bool) -> None:
        self._btn_undo.setEnabled(enabled)

    def set_redo_enabled(self, enabled: bool) -> None:
        self._btn_redo.setEnabled(enabled)

    def set_undo_count(self, count: int) -> None:
        """Update undo button to reflect the number of available undo steps."""
        self._btn_undo.setEnabled(count > 0)
        self._btn_undo.setToolTip(
            f"Undo the last brush/erase action  (Ctrl+Z)\n"
            f"{count} step{'s' if count != 1 else ''} available"
            if count > 0 else "Nothing to undo  (Ctrl+Z)"
        )
        label = f"↩ {count}" if count > 0 else "↩"
        self._btn_undo.setText(label)
        self._btn_undo.setMaximumWidth(26 + (len(str(count)) * 8 if count > 0 else 0))
        self.adjustSize()

    def set_redo_count(self, count: int) -> None:
        """Update redo button to reflect the number of available redo steps."""
        self._btn_redo.setEnabled(count > 0)
        self._btn_redo.setToolTip(
            f"Redo the last undone action  (Ctrl+Y)\n"
            f"{count} step{'s' if count != 1 else ''} available"
            if count > 0 else "Nothing to redo  (Ctrl+Y)"
        )
        label = f"↪ {count}" if count > 0 else "↪"
        self._btn_redo.setText(label)
        self._btn_redo.setMaximumWidth(26 + (len(str(count)) * 8 if count > 0 else 0))
        self.adjustSize()

    def reposition(self, parent_size) -> None:
        """Pin the overlay to the top-left corner."""
        margin = 6
        self.move(margin, margin)

# ---------------------------------------------------------------------------
# Zone row widget
# ---------------------------------------------------------------------------


class _ZoneRow(QWidget):
    """A two-row widget showing zone colour swatch, name, alpha spinbox,
    visibility toggle, Clear button, and Copy/Paste mask buttons."""

    selected          = pyqtSignal(int)        # zone_idx  (or -(idx+1) for clear)
    color_changed     = pyqtSignal(int, object) # zone_idx, (r,g,b) tuple
    visibility_changed = pyqtSignal(int, bool) # zone_idx, visible
    copy_requested    = pyqtSignal(int)        # zone_idx
    paste_requested   = pyqtSignal(int)        # zone_idx

    def __init__(self, zone_idx: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._idx = zone_idx
        r0, g0, b0, _ = ZONE_COLORS[zone_idx]
        self._cur_rgb: tuple[int, int, int] = (r0, g0, b0)
        color_name = ZONE_NAMES[zone_idx]

        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 3, 2, 3)
        outer.setSpacing(3)

        # ── Row 1: visibility toggle + swatch + name + alpha spinbox ────
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)

        # Visibility toggle (eye icon)
        self._vis_btn = QPushButton("👁")
        self._vis_btn.setCheckable(True)
        self._vis_btn.setChecked(True)
        self._vis_btn.setFixedSize(28, 24)
        self._vis_btn.setToolTip(
            f"Toggle visibility of the {color_name} overlay in the canvas.\n"
            "Hidden zones keep their painted masks — they are just not shown."
        )
        self._vis_btn.clicked.connect(self._on_vis_toggled)
        top.addWidget(self._vis_btn)

        # Colour swatch — clickable to open colour picker
        self._swatch = QPushButton()
        self._swatch.setFixedSize(18, 18)
        self._swatch.setToolTip(
            f"Click to choose a custom overlay colour for {color_name}."
        )
        self._swatch.setFlat(True)
        self._update_swatch_style()
        self._swatch.clicked.connect(self._on_pick_color)
        top.addWidget(self._swatch)

        # Name label — expanding so it fills available space; wraps if needed
        name_lbl = QLabel(color_name)
        name_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        name_lbl.setWordWrap(True)
        top.addWidget(name_lbl)

        # Alpha label + spinbox
        top.addWidget(QLabel("α:"))
        self._alpha_spin = QSpinBox()
        self._alpha_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._alpha_spin.setRange(0, 255)
        self._alpha_spin.setValue(128)
        self._alpha_spin.setMinimumWidth(62)
        self._alpha_spin.setToolTip(
            "Alpha value applied to all pixels painted in this zone "
            "(0=transparent, 255=opaque)."
        )
        top.addWidget(self._alpha_spin)
        outer.addLayout(top)

        # ── Row 2: Clear button ───────────────────────────────────────────
        bot = QHBoxLayout()
        bot.setContentsMargins(0, 0, 0, 0)
        bot.setSpacing(4)
        self._clear_btn = QPushButton("🗑  Clear")
        self._clear_btn.setMinimumHeight(26)
        self._clear_btn.setToolTip(f"Erase all painted pixels in {color_name}")
        self._clear_btn.clicked.connect(self._on_clear)
        bot.addWidget(self._clear_btn)

        outer.addLayout(bot)

        # ── Row 3: Copy + Paste mask buttons ─────────────────────────────
        cp_row = QHBoxLayout()
        cp_row.setContentsMargins(0, 0, 0, 0)
        cp_row.setSpacing(4)

        self._copy_btn = QPushButton("📋  Copy Mask")
        self._copy_btn.setMinimumHeight(24)
        self._copy_btn.setToolTip(
            f"Copy the painted mask for {color_name} to the zone clipboard.\n"
            "Use 'Paste Mask' on any zone to apply this copy."
        )
        self._copy_btn.clicked.connect(lambda: self.copy_requested.emit(self._idx))
        cp_row.addWidget(self._copy_btn)

        self._paste_btn = QPushButton("📌  Paste Mask")
        self._paste_btn.setMinimumHeight(24)
        self._paste_btn.setEnabled(False)
        self._paste_btn.setToolTip(
            f"Paste the copied mask onto {color_name}, replacing its current paint."
        )
        self._paste_btn.clicked.connect(lambda: self.paste_requested.emit(self._idx))
        cp_row.addWidget(self._paste_btn)

        outer.addLayout(cp_row)

    # ---------------------------------------------------------------- helpers

    def _update_swatch_style(self) -> None:
        r, g, b = self._cur_rgb
        self._swatch.setStyleSheet(
            f"background:{QColor(r, g, b).name()};"
            "border:1px solid #666; border-radius:3px;"
            "padding:0;"
        )

    def _on_vis_toggled(self, checked: bool) -> None:
        self._vis_btn.setText("👁" if checked else "🚫")
        self.visibility_changed.emit(self._idx, checked)

    def _on_pick_color(self) -> None:
        """Open a colour-picker dialog and emit color_changed if the user confirms."""
        r, g, b = self._cur_rgb
        initial = QColor(r, g, b)
        chosen = QColorDialog.getColor(
            initial, self, f"Choose colour for {ZONE_NAMES[self._idx]}"
        )
        if chosen.isValid():
            self._cur_rgb = (chosen.red(), chosen.green(), chosen.blue())
            self._update_swatch_style()
            self.color_changed.emit(self._idx, self._cur_rgb)

    def _on_clear(self) -> None:
        # Bubbles up to SelectiveAlphaTool via canvas
        self.selected.emit(-(self._idx + 1))  # negative = clear signal

    # ---------------------------------------------------------------- public

    def alpha_value(self) -> int:
        return self._alpha_spin.value()

    def set_alpha(self, value: int) -> None:
        """Set the alpha spinbox value (0-255) without emitting extra signals."""
        self._alpha_spin.blockSignals(True)
        self._alpha_spin.setValue(max(0, min(255, value)))
        self._alpha_spin.blockSignals(False)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("active", str(selected).lower())
        self.style().unpolish(self)
        self.style().polish(self)

    def set_zone_color(self, rgb: tuple[int, int, int]) -> None:
        """Update the swatch colour without opening a dialog."""
        self._cur_rgb = (rgb[0], rgb[1], rgb[2])
        self._update_swatch_style()

    def set_vis_state(self, visible: bool) -> None:
        """Set visibility checked state and sync the eye/block icon."""
        self._vis_btn.setChecked(visible)
        self._vis_btn.setText("👁" if visible else "🚫")

    def set_paste_enabled(self, enabled: bool) -> None:
        """Enable or disable the Paste Mask button."""
        self._paste_btn.setEnabled(enabled)

    def register_tooltips(self, mgr) -> None:
        """Register zone-row widgets with the TooltipManager for cycling tips."""
        mgr.register(self._alpha_spin,  "sa_zone_alpha_spin")
        mgr.register(self._clear_btn,   "sa_zone_clear")
        mgr.register(self._vis_btn,     "sa_zone_visibility")
        mgr.register(self._copy_btn,    "sa_zone_copy_mask")
        mgr.register(self._paste_btn,   "sa_zone_paste_mask")


# ---------------------------------------------------------------------------
# Main tab widget
# ---------------------------------------------------------------------------


class SelectiveAlphaTool(QWidget):
    """Tab widget for the Selective Alpha editor."""

    _MASK_SLOT_COUNT: int = 150  # Maximum number of saved-mask slots
    _MASK_SLOT_INIT:  int = 3    # Number of slots created on first launch
    _AZ_SLOT_COUNT:   int = 150  # Maximum number of all-zones slots
    _AZ_SLOT_INIT:    int = 3    # Number of all-zones slots created on first launch

    def __init__(self, settings_manager=None, sound_engine=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings_manager
        self._sound = sound_engine
        # Throttle zone-paint sound: play at most once per 200 ms during strokes.
        self._last_zone_paint_sound_t: float = 0.0
        self._src_path: str = ""
        # Current applied result and history stack for the Undo Process feature.
        # _result_img holds the most recently applied image; _result_history
        # is a capped stack of prior results that can be restored via Undo Process.
        self._result_img: Image.Image | None = None
        # Stack of previously applied result images for Undo Process
        self._result_history: list[Image.Image] = []
        # Flag set during settings restoration to suppress spurious auto-saves.
        self._restoring: bool = False
        # Remember the last non-eraser tool so pressing E twice toggles back.
        self._prev_non_eraser_tool: str = "freehand"
        # Zone-mask clipboard: stores a single uint8 ndarray (the most recent
        # Copy Mask action).  Copying again silently replaces the previous entry.
        self._mask_clipboard: Optional[np.ndarray] = None
        # All-zones clipboard: multi-slot store that survives image changes.
        # Each slot holds a snapshot of every zone mask.
        _AZ_SLOT_INIT = self._AZ_SLOT_INIT
        self._az_slots: list["list | None"] = [None] * _AZ_SLOT_INIT
        self._az_slot_info: list[str] = ["(empty)"] * _AZ_SLOT_INIT
        self._az_slot_names: list[str] = [""] * _AZ_SLOT_INIT
        # Per-zone display names: start from the static defaults but update
        # whenever the user picks a new colour so the active-zone combo always
        # shows the correct colour label.
        self._zone_display_names: list[str] = list(ZONE_NAMES)
        # Per-zone custom names set by the user (None = auto-derived from colour).
        self._zone_custom_names: list[Optional[str]] = [None] * NUM_ZONES
        self._setup_ui()
        self._restore_settings()

    # ----------------------------------------------------------------- setup

    def _setup_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # ── Left panel (controls) ─────────────────────────────────────────
        left_panel = QWidget()
        left_panel.setFixedWidth(340)
        lv = QVBoxLayout(left_panel)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)

        # ── Workflow group: Open → Paint → Save ───────────────────────────
        # All primary actions are grouped here at the top so the workflow
        # is immediately obvious.
        wf_box = QGroupBox("Workflow  \u2460Open  \u2461Paint  \u2462Save")
        wf_lay = QVBoxLayout(wf_box)
        wf_lay.setSpacing(4)

        # Row 1: Open image
        self._btn_open = QPushButton("\U0001f4c2  Open Image\u2026")
        self._btn_open.setMinimumHeight(26)
        self._btn_open.setToolTip("Open an image to edit  (Ctrl+O)")
        self._btn_open.clicked.connect(self._on_open)
        wf_lay.addWidget(self._btn_open)

        # Row 1b: Overlay opacity slider (item 16)
        _ov_row = QHBoxLayout()
        _ov_row.setSpacing(4)
        _ov_lbl = QLabel("Overlay opacity:")
        _ov_lbl.setToolTip(
            "Controls how strongly the zone colours are blended over the image.\n"
            "0 = invisible zones (no tint), 255 = fully opaque colour overlay.\n"
            "The default (160) gives a clear tint while the image is still visible."
        )
        _ov_row.addWidget(_ov_lbl)
        self._overlay_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._overlay_opacity_slider.setRange(0, 255)
        self._overlay_opacity_slider.setValue(160)
        self._overlay_opacity_slider.setToolTip(_ov_lbl.toolTip())
        self._overlay_opacity_slider.setFixedHeight(20)
        _ov_row.addWidget(self._overlay_opacity_slider, 1)
        self._overlay_opacity_lbl = QLabel("160")
        self._overlay_opacity_lbl.setMinimumWidth(26)
        _ov_row.addWidget(self._overlay_opacity_lbl)
        wf_lay.addLayout(_ov_row)

        # Row 2: Save — alpha zones are applied automatically on save
        self._btn_save = QPushButton("\U0001f4be Save\u2026")
        self._btn_save.setMinimumHeight(26)
        self._btn_save.setToolTip(
            "Apply the painted zones and save the result to disk  (Ctrl+S)\n"
            "Alpha zones are applied automatically — no separate Apply step needed."
        )
        self._btn_save.clicked.connect(self._on_save)
        self._btn_save.setEnabled(False)
        self._btn_save.setStyleSheet("QPushButton:enabled { font-weight: bold; }")
        wf_lay.addWidget(self._btn_save)

        # Row 3: Clear All Zones
        self._btn_clear_all = QPushButton("\U0001f5d1  Clear All Zones")
        self._btn_clear_all.setMinimumHeight(26)
        self._btn_clear_all.setToolTip("Erase all painted zone masks and start over.")
        self._btn_clear_all.clicked.connect(self._on_clear_all)
        wf_lay.addWidget(self._btn_clear_all)

        lv.addWidget(wf_box)

        # Note: Undo / Redo drawing buttons are in a floating canvas overlay
        # (_FloatingHistoryOverlay, top-left of canvas) — not in the sidebar.

        # ── Drawing tools ─────────────────────────────────────────────────
        tools_box = QGroupBox("Drawing Tool")
        tg = QGridLayout(tools_box)
        tg.setSpacing(4)

        self._tool_btns: dict[str, QPushButton] = {}
        tool_defs = [
            ("freehand",   "✏  Freehand",  0, 0),
            ("line",       "╱  Line",      0, 1),
            ("rect",       "▭  Rect",      1, 0),
            ("ellipse",    "◯  Ellipse",   1, 1),
            ("fill",       "🪣  Fill",      2, 0),
            ("polygon",    "⬠  Polygon",   2, 1),
            ("eraser",     "⌫  Eraser",    3, 0),
            ("transform",  "✥  Move",      3, 1),
        ]
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)

        for key, label, row, col in tool_defs:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumHeight(24)
            btn.setMinimumWidth(100)
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
        self._btn_close_poly.setMinimumHeight(28)
        self._btn_close_poly.setVisible(False)
        self._btn_close_poly.clicked.connect(self._on_close_polygon)
        tg.addWidget(self._btn_close_poly, 4, 0, 1, 2)

        lv.addWidget(tools_box)

        # Tool sizes + Auto-correct grouped together
        size_box = QGroupBox("Tool Size")
        sg = QGridLayout(size_box)
        sg.setSpacing(4)
        sg.addWidget(QLabel("Highlighter (px):"), 0, 0)
        self._brush_spin = QSpinBox()
        self._brush_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._brush_spin.setRange(1, 200)
        self._brush_spin.setValue(10)
        self._brush_spin.setToolTip("Radius of the freehand / line / shape brush in image pixels.")
        self._brush_spin.valueChanged.connect(
            lambda v: self._canvas.set_brush_size(v)
        )
        sg.addWidget(self._brush_spin, 0, 1)
        sg.addWidget(QLabel("Eraser (px):"), 1, 0)
        self._eraser_spin = QSpinBox()
        self._eraser_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._eraser_spin.setRange(1, 200)
        self._eraser_spin.setValue(10)
        self._eraser_spin.setToolTip("Radius of the eraser brush in image pixels.")
        self._eraser_spin.valueChanged.connect(
            lambda v: self._canvas.set_eraser_size(v)
        )
        sg.addWidget(self._eraser_spin, 1, 1)

        # Auto-correct sits inside the Tool Size group so it is clearly
        # associated with the drawing tools and is never hidden by the
        # zone list scrolling out of view.
        self._autocorrect_chk = QCheckBox("Auto-correct (snap to edges)")
        self._autocorrect_chk.setToolTip(
            "When checked, freehand and line strokes are automatically snapped\n"
            "to nearby strong image edges after you release the mouse button.\n"
            "Helps rough strokes align to object boundaries without precise drawing."
        )
        self._autocorrect_chk.toggled.connect(
            lambda v: self._canvas.set_autocorrect(v)
        )
        sg.addWidget(self._autocorrect_chk, 2, 0, 1, 2)
        lv.addWidget(size_box)

        # Zoom controls
        # Zoom controls are in a floating overlay on the canvas (see _zoom_overlay below).

        # Zone rows
        zones_box = QGroupBox("Alpha Zones")
        zv = QVBoxLayout(zones_box)
        zv.setSpacing(2)

        # Master visibility toggle row
        vis_all_row = QHBoxLayout()
        vis_all_row.setContentsMargins(0, 0, 0, 2)
        self._btn_show_all = QPushButton("👁  Show All")
        self._btn_show_all.setMinimumHeight(24)
        self._btn_show_all.setToolTip(
            "Make all zone overlays visible in the canvas at once."
        )
        self._btn_show_all.clicked.connect(self._on_show_all_zones)
        self._btn_hide_all = QPushButton("🙈  Hide All")
        self._btn_hide_all.setMinimumHeight(24)
        self._btn_hide_all.setToolTip(
            "Hide all zone overlays in the canvas so the source image is shown clean.\n"
            "Painted masks are preserved — click Show All to reveal them again."
        )
        self._btn_hide_all.clicked.connect(self._on_hide_all_zones)
        vis_all_row.addWidget(self._btn_show_all)
        vis_all_row.addWidget(self._btn_hide_all)
        zv.addLayout(vis_all_row)

        # "Highlight transparent pixels" checkbox
        self._show_zero_alpha_chk = QCheckBox("Highlight transparent pixels")
        self._show_zero_alpha_chk.setChecked(False)
        self._show_zero_alpha_chk.setToolTip(
            "When checked, zone highlights are shown even over fully-transparent\n"
            "(alpha = 0) pixels so you can paint and see selections on\n"
            "transparent areas of the image."
        )
        zv.addWidget(self._show_zero_alpha_chk)

        # "Show α values" checkbox
        self._show_alpha_labels_chk = QCheckBox("Show α values on canvas")
        self._show_alpha_labels_chk.setChecked(True)
        self._show_alpha_labels_chk.setToolTip(
            "When checked, each zone's alpha value is drawn as text at the\n"
            "centre of its painted area on the canvas.  Off by default."
        )
        zv.addWidget(self._show_alpha_labels_chk)

        # "Show Highlights" checkbox — master visibility for all zone overlays (item 61)
        self._btn_show_highlights = QCheckBox("\U0001f441  Show Highlights")
        self._btn_show_highlights.setChecked(True)
        self._btn_show_highlights.setToolTip(
            "Toggle visibility of all alpha-zone highlight overlays on the canvas.\n"
            "Turn off to see the image without coloured highlights and alpha labels.\n"
            "Automatically turns on when multiple alpha zones are detected in an image.\n"
            "Keyboard shortcut: H"
        )
        self._btn_show_highlights.clicked.connect(self._on_show_highlights_toggled)
        zv.addWidget(self._btn_show_highlights)

        # Thin separator
        sep0 = QFrame()
        sep0.setFrameShape(QFrame.Shape.HLine)
        sep0.setFrameShadow(QFrame.Shadow.Sunken)
        sep0.setFixedHeight(1)
        zv.addWidget(sep0)

        # Active zone selector dropdown (replaces per-row Paint buttons)
        az_sel_row = QHBoxLayout()
        az_sel_row.setContentsMargins(0, 2, 0, 2)
        az_sel_row.setSpacing(4)
        az_sel_row.addWidget(QLabel("Active:"))
        self._active_zone_combo = QComboBox()
        self._active_zone_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        for i in range(NUM_ZONES):
            r, g, b, _ = ZONE_COLORS[i]
            self._active_zone_combo.addItem(
                self._make_zone_color_icon(r, g, b), ZONE_NAMES[i]
            )
        self._active_zone_combo.setToolTip(
            "Select the zone to paint into.  The chosen zone becomes the\n"
            "active paint target for all drawing tools."
        )
        self._active_zone_combo.currentIndexChanged.connect(
            lambda idx: self._on_zone_action(idx)
        )
        az_sel_row.addWidget(self._active_zone_combo)
        zv.addLayout(az_sel_row)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        sep1.setFixedHeight(1)
        zv.addWidget(sep1)

        self._zone_rows: list[_ZoneRow] = []
        self._ze_cur_idx: int = 0

        ze_frame = QFrame()
        ze_frame.setStyleSheet("QFrame { border: 1px solid #444; border-radius: 4px; padding: 2px; }")
        ze_vlay = QVBoxLayout(ze_frame)
        ze_vlay.setSpacing(4)
        ze_vlay.setContentsMargins(4, 4, 4, 4)

        # Row 1: vis toggle + color swatch + name label + alpha spinbox
        ze_row1 = QHBoxLayout()
        ze_row1.setSpacing(4)
        self._ze_vis_btn = QPushButton("👁")
        self._ze_vis_btn.setCheckable(True)
        self._ze_vis_btn.setChecked(True)
        self._ze_vis_btn.setFixedSize(28, 24)
        ze_row1.addWidget(self._ze_vis_btn)
        self._ze_swatch_btn = QPushButton()
        self._ze_swatch_btn.setFlat(True)
        self._ze_swatch_btn.setFixedSize(18, 18)
        self._ze_swatch_btn.setStyleSheet(
            "background:#ff4444;border:1px solid #666;border-radius:3px;padding:0;"
        )
        self._ze_swatch_btn.setToolTip("Click to choose a colour for this zone")
        ze_row1.addWidget(self._ze_swatch_btn)
        self._ze_name_edit = QLineEdit(ZONE_NAMES[0])
        self._ze_name_edit.setPlaceholderText("Zone name…")
        self._ze_name_edit.setToolTip(
            "Custom name for this zone.\n"
            "Leave blank to use the auto-derived colour name.\n"
            "Press Enter or Tab to confirm."
        )
        self._ze_name_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        ze_row1.addWidget(self._ze_name_edit)
        self._ze_jump_btn = QPushButton("⊕")
        self._ze_jump_btn.setFixedSize(22, 22)
        self._ze_jump_btn.setToolTip(
            "Center canvas on this zone's painted area.  [item 49]\n"
            "Does nothing if the zone has no painted pixels."
        )
        ze_row1.addWidget(self._ze_jump_btn)
        ze_row1.addWidget(QLabel("α:"))
        self._ze_alpha_spin = QSpinBox()
        self._ze_alpha_spin.setRange(0, 255)
        self._ze_alpha_spin.setValue(128)
        self._ze_alpha_spin.setMinimumWidth(62)
        ze_row1.addWidget(self._ze_alpha_spin)
        ze_vlay.addLayout(ze_row1)

        # Row 2: Clear button
        self._ze_clear_btn = QPushButton("🗑  Clear")
        ze_vlay.addWidget(self._ze_clear_btn)

        # Row 3: Copy + Paste buttons
        ze_row3 = QHBoxLayout()
        ze_row3.setSpacing(4)
        self._ze_copy_btn = QPushButton("📋  Copy Zone to Clipboard")
        self._ze_paste_btn = QPushButton("📌  Paste Zone from Clipboard")
        self._ze_paste_btn.setEnabled(False)
        ze_row3.addWidget(self._ze_copy_btn)
        ze_row3.addWidget(self._ze_paste_btn)
        ze_vlay.addLayout(ze_row3)

        zv.addWidget(ze_frame)
        lv.addWidget(zones_box)

        # ── Saved Masks Collection ─────────────────────────────────────────
        # Compact dropdown-based slot chooser.  Slots survive image changes so
        # the user can copy zones from one image and paste onto another.
        # The user can grow the list with "＋ Add Slot".
        _MASK_SLOT_INIT = self._MASK_SLOT_INIT
        self._mask_slots: list[Optional[np.ndarray]] = [None] * _MASK_SLOT_INIT
        self._mask_slot_info: list[str] = ["(empty)"] * _MASK_SLOT_INIT
        self._mask_slot_names: list[str] = [""] * _MASK_SLOT_INIT

        slots_box = QGroupBox("📋 Single-Zone Clipboard  (one zone mask per slot)")
        sv = QVBoxLayout(slots_box)
        sv.setSpacing(4)
        sv.setContentsMargins(4, 4, 4, 4)

        # Short description so users know what this section is for
        _sz_hint = QLabel(
            "Save/paste the painted mask for one zone at a time.\n"
            "Use slots to hold multiple saved masks."
        )
        _sz_hint.setStyleSheet("color: #888; font-size: 9px;")
        _sz_hint.setWordWrap(True)
        sv.addWidget(_sz_hint)

        # Slot selector row (combo on its own row so the name shows in full)
        sel_row = QHBoxLayout()
        sel_row.setSpacing(4)
        sel_row.addWidget(QLabel("Slot:"))
        self._slot_combo = QComboBox()
        self._slot_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._slot_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        for i in range(_MASK_SLOT_INIT):
            self._slot_combo.addItem(f"Slot {i + 1}  —  (empty)")
        self._slot_combo.currentIndexChanged.connect(self._on_slot_selected)
        sel_row.addWidget(self._slot_combo)
        sv.addLayout(sel_row)

        # Add / Delete buttons on a separate row so neither is cut off
        slot_btn_row = QHBoxLayout()
        slot_btn_row.setSpacing(4)
        self._btn_slot_add = QPushButton("＋ Add Slot")
        self._btn_slot_add.setMinimumHeight(26)
        self._btn_slot_add.setToolTip(
            "Add a new empty slot to the list.\n"
            "Useful when all existing slots are in use."
        )
        self._btn_slot_add.clicked.connect(self._on_slot_add)
        slot_btn_row.addWidget(self._btn_slot_add)

        self._btn_slot_del = QPushButton("✕ Delete Slot")
        self._btn_slot_del.setMinimumHeight(26)
        self._btn_slot_del.setToolTip(
            "Delete the currently selected slot from the list.\n"
            "Any mask stored in it will be lost."
        )
        self._btn_slot_del.clicked.connect(self._on_slot_del)
        slot_btn_row.addWidget(self._btn_slot_del)
        sv.addLayout(slot_btn_row)

        # Status label for the selected slot
        self._slot_info_lbl = QLabel("(empty)")
        self._slot_info_lbl.setStyleSheet("color: #888; font-size: 10px;")
        self._slot_info_lbl.setWordWrap(True)
        sv.addWidget(self._slot_info_lbl)

        # Row 1: Save | Paste
        act_row = QHBoxLayout()
        act_row.setSpacing(4)
        self._btn_slot_save = QPushButton("💾  Save")
        self._btn_slot_save.setMinimumHeight(26)
        self._btn_slot_save.setToolTip(
            "Save the active zone's mask into the selected slot.\n"
            "Overwrites any previously saved mask in this slot."
        )
        self._btn_slot_save.clicked.connect(self._on_save_to_slot_current)
        act_row.addWidget(self._btn_slot_save)

        self._btn_slot_paste = QPushButton("📌  Paste")
        self._btn_slot_paste.setMinimumHeight(26)
        self._btn_slot_paste.setEnabled(False)
        self._btn_slot_paste.setToolTip(
            "Paste the mask stored in the selected slot into the\n"
            "currently active zone, replacing its painted mask."
        )
        self._btn_slot_paste.clicked.connect(self._on_paste_from_slot_current)
        act_row.addWidget(self._btn_slot_paste)
        sv.addLayout(act_row)

        # Row 2: Rename | Clear
        rename_row = QHBoxLayout()
        rename_row.setSpacing(4)
        self._btn_slot_rename = QPushButton("✏  Rename")
        self._btn_slot_rename.setMinimumHeight(26)
        self._btn_slot_rename.setToolTip(
            "Give the selected slot a custom name so you can identify it easily."
        )
        self._btn_slot_rename.clicked.connect(self._on_slot_rename)
        rename_row.addWidget(self._btn_slot_rename)

        self._btn_slot_clear = QPushButton("✕  Clear")
        self._btn_slot_clear.setMinimumHeight(26)
        self._btn_slot_clear.setToolTip(
            "Erase the mask stored in the selected slot.\n"
            "The slot itself is kept; only its stored data is removed."
        )
        self._btn_slot_clear.clicked.connect(self._on_clear_slot_current)
        rename_row.addWidget(self._btn_slot_clear)
        sv.addLayout(rename_row)

        lv.addWidget(slots_box)

        # ── Copy / Paste all zones ──────────────────────────────────────────
        all_zones_box = QGroupBox("📋 All-Zones Clipboard  (all zones at once per slot)")
        azv = QVBoxLayout(all_zones_box)
        azv.setSpacing(4)
        azv.setContentsMargins(4, 4, 4, 4)

        # Short description so users know what this section is for
        _az_hint = QLabel(
            "Copy/paste all zones at once (full multi-zone snapshot).\n"
            "Slots hold complete zone sets across images."
        )
        _az_hint.setStyleSheet("color: #888; font-size: 9px;")
        _az_hint.setWordWrap(True)
        azv.addWidget(_az_hint)

        # Slot selector row (combo on its own row so the name shows in full)
        az_sel_row2 = QHBoxLayout()
        az_sel_row2.setSpacing(4)
        az_sel_row2.addWidget(QLabel("Slot:"))
        self._az_slot_combo = QComboBox()
        self._az_slot_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._az_slot_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        for i in range(len(self._az_slots)):
            self._az_slot_combo.addItem(f"Slot {i + 1}  —  (empty)")
        self._az_slot_combo.currentIndexChanged.connect(self._on_az_slot_selected)
        az_sel_row2.addWidget(self._az_slot_combo)
        azv.addLayout(az_sel_row2)

        # Add / Delete buttons on a separate row so neither is cut off
        az_slot_btn_row = QHBoxLayout()
        az_slot_btn_row.setSpacing(4)
        self._btn_az_slot_add = QPushButton("＋ Add Slot")
        self._btn_az_slot_add.setMinimumHeight(26)
        self._btn_az_slot_add.setToolTip(
            "Add a new empty all-zones slot to the list."
        )
        self._btn_az_slot_add.clicked.connect(self._on_az_slot_add)
        az_slot_btn_row.addWidget(self._btn_az_slot_add)

        self._btn_az_slot_del = QPushButton("✕ Delete Slot")
        self._btn_az_slot_del.setMinimumHeight(26)
        self._btn_az_slot_del.setToolTip(
            "Delete the currently selected all-zones slot from the list.\n"
            "Any snapshot stored in it will be lost."
        )
        self._btn_az_slot_del.clicked.connect(self._on_az_slot_del)
        az_slot_btn_row.addWidget(self._btn_az_slot_del)
        azv.addLayout(az_slot_btn_row)

        self._az_slot_info_lbl = QLabel("(empty)")
        self._az_slot_info_lbl.setStyleSheet("color: #888; font-size: 10px;")
        self._az_slot_info_lbl.setWordWrap(True)
        azv.addWidget(self._az_slot_info_lbl)

        # Row 1: Copy All | Paste All
        az_act_row = QHBoxLayout()
        az_act_row.setSpacing(4)
        self._btn_copy_all_zones = QPushButton("Copy All Zones")
        self._btn_copy_all_zones.setMinimumHeight(26)
        self._btn_copy_all_zones.setToolTip(
            "Copy ALL painted zone masks into the selected slot.\n"
            "Snapshots every zone at once so you can restore the\n"
            "full painting later (even on a different image)."
        )
        self._btn_copy_all_zones.clicked.connect(self._on_copy_all_zones)
        az_act_row.addWidget(self._btn_copy_all_zones)

        self._btn_paste_all_zones = QPushButton("Paste All Zones")
        self._btn_paste_all_zones.setMinimumHeight(26)
        self._btn_paste_all_zones.setEnabled(False)
        self._btn_paste_all_zones.setToolTip(
            "Paste all zone masks from the selected slot back onto\n"
            "the current image. Masks are scaled automatically if\n"
            "the image dimensions differ from when they were saved."
        )
        self._btn_paste_all_zones.clicked.connect(self._on_paste_all_zones)
        az_act_row.addWidget(self._btn_paste_all_zones)
        azv.addLayout(az_act_row)

        # Row 2: Rename | Clear
        az_rename_row = QHBoxLayout()
        az_rename_row.setSpacing(4)
        self._btn_az_slot_rename = QPushButton("✏  Rename")
        self._btn_az_slot_rename.setMinimumHeight(26)
        self._btn_az_slot_rename.setToolTip(
            "Give the selected all-zones slot a custom name."
        )
        self._btn_az_slot_rename.clicked.connect(self._on_az_slot_rename)
        az_rename_row.addWidget(self._btn_az_slot_rename)

        self._btn_az_slot_clear = QPushButton("✕  Clear")
        self._btn_az_slot_clear.setMinimumHeight(26)
        self._btn_az_slot_clear.setToolTip(
            "Erase the zone snapshot stored in the selected slot."
        )
        self._btn_az_slot_clear.clicked.connect(self._on_az_slot_clear)
        az_rename_row.addWidget(self._btn_az_slot_clear)
        azv.addLayout(az_rename_row)

        lv.addWidget(all_zones_box)

        # ── Import from Alpha & RGBA Adjuster Tool ─────────────────────────
        # When the user right-clicks the compare preview in the Alpha &
        # RGBA Adjuster tab and selects "Copy zones → Selective Alpha tool",
        # the zone data is deposited here so the user can import it into the
        # painting canvas with a single button click.
        import_box = QGroupBox("Import Zones from Alpha Tool")
        iv = QVBoxLayout(import_box)
        iv.setSpacing(4)
        iv.setContentsMargins(4, 4, 4, 4)

        import_note = QLabel(
            "1. Enable 'Highlight Alpha Values' in the Alpha & RGBA Adjuster.\n"
            "2. Right-click the compare preview → 'Copy zones → Selective Alpha'.\n"
            "3. Use the buttons below to import or save to slot/clipboard."
        )
        import_note.setWordWrap(True)
        import_note.setStyleSheet("color: #999; font-size: 11px;")
        iv.addWidget(import_note)

        self._import_shared_status = QLabel("No zones ready to import.")
        self._import_shared_status.setWordWrap(True)
        self._import_shared_status.setStyleSheet("color: #888; font-size: 11px;")
        iv.addWidget(self._import_shared_status)

        self._btn_import_shared = QPushButton("📥 Import Zones to Canvas")
        self._btn_import_shared.setEnabled(False)
        self._btn_import_shared.setToolTip(
            "Populate the painting canvas with zone masks that were copied from\n"
            "the Alpha & RGBA Adjuster's compare preview.\n\n"
            "Each detected alpha value becomes a separate coloured zone."
        )
        self._btn_import_shared.clicked.connect(self._on_import_shared_zones)
        iv.addWidget(self._btn_import_shared)

        # Item 22: save ALL shared zones into the current all-zones slot
        self._btn_import_to_az_slot = QPushButton("💾 Save All → All-Zones Clipboard Slot")
        self._btn_import_to_az_slot.setMinimumHeight(26)
        self._btn_import_to_az_slot.setEnabled(False)
        self._btn_import_to_az_slot.setToolTip(
            "Save ALL imported zones from the Alpha & RGBA Adjuster directly\n"
            "into the currently selected All-Zones Clipboard slot so they can\n"
            "be pasted onto any image later without re-importing."
        )
        self._btn_import_to_az_slot.clicked.connect(self._on_import_to_az_slot)
        iv.addWidget(self._btn_import_to_az_slot)

        # Item 23: copy one shared zone to the single-zone clipboard
        self._btn_import_zone_to_clipboard = QPushButton("📋 Copy Single Zone → Single-Zone Clipboard")
        self._btn_import_zone_to_clipboard.setMinimumHeight(26)
        self._btn_import_zone_to_clipboard.setEnabled(False)
        self._btn_import_zone_to_clipboard.setToolTip(
            "Copy one imported zone into the Single Zone Mask Clipboard.\n"
            "If more than one zone was imported, a picker will appear\n"
            "so you can choose which zone to copy.\n"
            "The copied mask can then be pasted via the '📌  Paste Mask' button\n"
            "in the Zone Editor section below."
        )
        self._btn_import_zone_to_clipboard.clicked.connect(self._on_import_zone_to_clipboard)
        iv.addWidget(self._btn_import_zone_to_clipboard)

        lv.addWidget(import_box)

        # Internal storage for zones received from the Alpha tool.
        self._shared_zones: list[tuple[int, "np.ndarray"]] = []

        lv.addStretch()

        # ── Wrap left panel in a scroll area so controls remain accessible
        # on smaller windows (same pattern as alpha_tool.py / converter_tool.py).
        left_scroll = QScrollArea()
        left_scroll.setWidget(left_panel)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        left_scroll.setFixedWidth(360)  # panel 340 + scroll bar ~20
        root.addWidget(left_scroll)

        # ── Canvas ───────────────────────────────────────────────────────
        self._canvas = SelectiveAlphaCanvas()
        self._canvas.setToolTip(
            "Paint alpha zones on the image.\n\n"
            "🖱  Hold middle-mouse (scroll wheel button) to pan\n"
            "Alt+drag also pans\n"
            "Ctrl+scroll to zoom in/out\n"
            "[ / ] keys: decrease / increase brush size\n"
            "H: toggle zone highlights  ·  N / Shift+N: cycle zones\n"
            "Right-click for copy/paste menu"
        )
        self._canvas.mask_changed.connect(self._on_mask_changed)
        # undo_available / redo_available are connected to the history overlay
        # below, after the overlay itself is instantiated.
        # Wire canvas context-menu copy/paste signals.
        self._canvas.copy_requested.connect(self._on_copy_mask)
        self._canvas.paste_requested.connect(self._on_paste_mask)
        # Wire canvas context-menu copy/paste all-zones signals.
        self._canvas.copy_all_requested.connect(self._on_copy_all_zones)
        self._canvas.paste_all_requested.connect(self._on_paste_all_zones)

        # Wire overlay opacity slider (item 16)
        def _on_overlay_opacity(val: int) -> None:
            self._overlay_opacity_lbl.setText(str(val))
            self._canvas.set_overlay_alpha(val)
        self._overlay_opacity_slider.valueChanged.connect(_on_overlay_opacity)

        # Wire single zone editor controls
        self._ze_vis_btn.clicked.connect(self._on_ze_vis_toggled)
        self._ze_swatch_btn.clicked.connect(self._on_ze_pick_color)
        self._ze_clear_btn.clicked.connect(self._on_ze_clear)
        self._ze_copy_btn.clicked.connect(self._on_ze_copy_mask)
        self._ze_paste_btn.clicked.connect(self._on_ze_paste_mask)
        self._ze_alpha_spin.valueChanged.connect(self._on_ze_alpha_changed)
        # Zone name editor (item 33) – save custom name when text changes
        self._ze_name_edit.textEdited.connect(self._on_ze_name_edited)
        # Jump to zone (item 49) — center canvas on zone centroid
        self._ze_jump_btn.clicked.connect(
            lambda: self._canvas.center_on_zone(self._ze_cur_idx)
        )

        # Wire show-zero-alpha checkbox.
        self._show_zero_alpha_chk.toggled.connect(self._canvas.set_show_zero_alpha)
        # Wire show-alpha-labels checkbox.
        self._show_alpha_labels_chk.toggled.connect(self._canvas.set_show_alpha_labels)

        # Wrap canvas + status label in a vertical layout.
        right_widget = QWidget()
        rv = QVBoxLayout(right_widget)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(2)
        rv.addWidget(self._canvas, 1)
        self._status_lbl = QLabel("Tool: Freehand  |  Zone 1 – Red  |  Brush: 10 px  |  🖱 Hold scroll-button to pan  |  Ctrl+scroll to zoom")
        self._status_lbl.setStyleSheet(
            "color: #999; font-size: 10px; padding: 2px 4px;"
        )
        self._status_lbl.setToolTip(
            "Shows the active tool, zone, brush size, and cursor position.\n\n"
            "Navigation tips:\n"
            "🖱 Hold middle-mouse button (scroll wheel) and drag to pan the canvas\n"
            "Alt + drag: alternative pan (no scroll wheel needed)\n"
            "Ctrl + scroll wheel: zoom in / out\n"
            "Keyboard shortcuts: B Brush · E Eraser · L Line · R Rect · X Ellipse · F Fill · P Polygon · T Transform\n"
            "[ / ] keys: decrease / increase brush size\n"
            "H: toggle zone highlights  ·  N / Shift+N: cycle zones"
        )
        # Coordinate label next to status bar (item 50)
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(4)
        status_row.addWidget(self._status_lbl, 1)
        self._coord_lbl = QLabel("—")
        self._coord_lbl.setStyleSheet(
            "color: #888; font-size: 10px; padding: 2px 6px;"
        )
        self._coord_lbl.setToolTip("Cursor position in image pixel coordinates (x, y).")
        self._coord_lbl.setMinimumWidth(80)
        self._coord_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        status_row.addWidget(self._coord_lbl)
        rv.addLayout(status_row)
        # Wire cursor position signal to coordinate label (item 50)
        self._canvas.cursor_moved.connect(
            lambda x, y: self._coord_lbl.setText(f"x:{x}  y:{y}")
        )
        root.addWidget(right_widget, 1)

        # ── Floating zoom overlay pinned to the top-right of the canvas ───
        self._zoom_overlay = _FloatingZoomOverlay(
            self._zoom_in, self._zoom_out, self._zoom_reset, self._canvas
        )
        self._zoom_overlay.adjustSize()
        # Keep the zoom percentage label in the overlay in sync with the canvas.
        self._canvas.zoom_changed.connect(self._zoom_overlay.set_zoom)

        # ── Floating undo/redo overlay pinned to the top-left of the canvas ─
        self._history_overlay = _FloatingHistoryOverlay(
            self._on_undo_mask, self._on_redo_mask, self._canvas
        )
        self._history_overlay.adjustSize()
        # Connect canvas undo/redo availability signals to the floating overlay.
        self._canvas.undo_available.connect(self._history_overlay.set_undo_enabled)
        self._canvas.redo_available.connect(self._history_overlay.set_redo_enabled)
        # Connect count signals so the overlay shows how many steps are available.
        self._canvas.undo_count_changed.connect(self._history_overlay.set_undo_count)
        self._canvas.redo_count_changed.connect(self._history_overlay.set_redo_count)

        # Install an event filter on the canvas so the overlays stay pinned
        # whenever the canvas is resized.
        self._canvas.installEventFilter(self)

        # Sync initial tool to canvas
        self._canvas.set_tool("freehand")
        self._canvas.set_active_zone(0)

        # Connect spinboxes to status updates (after _status_lbl is created).
        self._brush_spin.valueChanged.connect(lambda _: self._update_status())
        self._eraser_spin.valueChanged.connect(lambda _: self._update_status())
        # Connect zoom changes so the status bar always shows the current zoom level (item 47).
        self._canvas.zoom_changed.connect(lambda _z: self._update_status())

        # Auto-save settings when the user adjusts tool options.
        self._brush_spin.valueChanged.connect(lambda _: self._save_settings())
        self._eraser_spin.valueChanged.connect(lambda _: self._save_settings())
        self._autocorrect_chk.toggled.connect(lambda _: self._save_settings())
        self._show_zero_alpha_chk.toggled.connect(lambda _: self._save_settings())
        self._show_alpha_labels_chk.toggled.connect(lambda _: self._save_settings())
        # _ze_alpha_spin is intentionally absent here: _on_ze_alpha_changed (line 2307) already calls _save_settings().
        self._refresh_zone_editor(0)
        self._refresh_zone_combo_icons()
        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        """Bind common keyboard shortcuts for the Selective Alpha editor."""
        QShortcut(QKeySequence("Ctrl+Z"),       self).activated.connect(self._on_undo_mask)
        QShortcut(QKeySequence("Ctrl+Y"),       self).activated.connect(self._on_redo_mask)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self).activated.connect(self._on_redo_mask)
        QShortcut(QKeySequence("Ctrl+O"),       self).activated.connect(self._on_open)
        QShortcut(QKeySequence("Ctrl+S"),       self).activated.connect(self._on_save)
        QShortcut(QKeySequence("Ctrl+Return"),  self).activated.connect(self._on_apply)
        # Drawing tool shortcuts: single-key mnemonics for each tool
        _TOOL_KEYS = {
            "B": "freehand",
            "E": "eraser",
            "L": "line",
            "R": "rect",
            "X": "ellipse",
            "F": "fill",
            "P": "polygon",
            "T": "transform",
        }
        for key, tool in _TOOL_KEYS.items():
            QShortcut(QKeySequence(key), self).activated.connect(
                lambda _=None, t=tool: self._select_tool_by_key(t)
            )
        # Brush size adjust with [ and ]
        QShortcut(QKeySequence("["), self).activated.connect(
            lambda: self._adjust_brush_size(-2)
        )
        QShortcut(QKeySequence("]"), self).activated.connect(
            lambda: self._adjust_brush_size(2)
        )
        # H: toggle Show Highlights (item 54)
        QShortcut(QKeySequence("H"), self).activated.connect(
            lambda: self._btn_show_highlights.click()
        )
        # N / Shift+N: cycle to next / previous zone (item 52)
        QShortcut(QKeySequence("N"), self).activated.connect(
            lambda: self._cycle_zone(+1)
        )
        QShortcut(QKeySequence("Shift+N"), self).activated.connect(
            lambda: self._cycle_zone(-1)
        )

    def _cycle_zone(self, direction: int) -> None:
        """Cycle the active zone editor to the next (+1) or previous (-1) zone."""
        new_idx = (self._ze_cur_idx + direction) % NUM_ZONES
        self._active_zone_combo.setCurrentIndex(new_idx)
        self._refresh_zone_editor(new_idx)

    def _restore_settings(self) -> None:
        """Restore previously saved Selective Alpha Tool settings."""
        if self._settings is None:
            return
        self._restoring = True
        try:
            # Restore zone alpha values
            alphas = self._settings.get_sa_zone_alphas()
            for idx, alpha in enumerate(alphas):
                self._canvas.set_zone_alpha_label(idx, alpha)
            # Restore custom zone colours
            saved_colors = self._settings.get_sa_zone_colors()
            if saved_colors is not None:
                for idx, c in enumerate(saved_colors):
                    rgb = (c[0], c[1], c[2])
                    self._canvas.set_zone_color(idx, rgb[0], rgb[1], rgb[2])
            self._refresh_zone_editor(self._ze_cur_idx)
            self._refresh_zone_combo_icons()
            self._refresh_zone_display_names()
            self._brush_spin.setValue(int(self._settings.get("sa_brush_size", 10)))
            self._eraser_spin.setValue(int(self._settings.get("sa_eraser_size", 10)))
            # Restore autocorrect toggle
            self._autocorrect_chk.setChecked(bool(self._settings.get("sa_autocorrect", False)))
            # Restore show-zero-alpha toggle
            self._show_zero_alpha_chk.setChecked(
                bool(self._settings.get("sa_show_zero_alpha", False))
            )
            # Restore show-alpha-labels toggle
            self._show_alpha_labels_chk.setChecked(
                bool(self._settings.get("sa_show_alpha_labels", True))
            )
            # Restore last-used drawing tool
            last_tool = str(self._settings.get("sa_last_tool", "freehand"))
            if last_tool in self._tool_btns:
                self._tool_btns[last_tool].setChecked(True)
                self._on_tool_selected(last_tool)
        finally:
            self._restoring = False

    def _save_settings(self) -> None:
        """Persist the current Selective Alpha Tool settings."""
        if self._settings is None or self._restoring:
            return
        self._settings.set_sa_zone_alphas(
            [self._canvas._zone_alphas[i] for i in range(NUM_ZONES)]
        )
        # Persist custom zone colours: read from canvas which is the source of truth.
        self._settings.set_sa_zone_colors(
            [list(self._canvas.get_zone_color(i)) for i in range(NUM_ZONES)]
        )
        self._settings.set("sa_brush_size",  self._brush_spin.value())
        self._settings.set("sa_eraser_size", self._eraser_spin.value())
        self._settings.set("sa_autocorrect", self._autocorrect_chk.isChecked())
        self._settings.set("sa_show_zero_alpha", self._show_zero_alpha_chk.isChecked())
        self._settings.set("sa_show_alpha_labels", self._show_alpha_labels_chk.isChecked())
        self._settings.set("sa_last_tool",   self._canvas._tool)

    def closeEvent(self, event) -> None:  # noqa: N802
        """Save settings and release canvas PIL images on widget close."""
        self._save_settings()
        self._canvas.unload_image()
        if self._result_img is not None:
            self._result_img.close()
            self._result_img = None
        for img in self._result_history:
            img.close()
        self._result_history.clear()
        super().closeEvent(event)

    # ---------------------------------------------------------------- helpers

    def register_tooltips(self, mgr) -> None:
        """Register all Selective Alpha tab widgets with the TooltipManager."""
        mgr.register(self._btn_open,            "sa_open_btn")
        mgr.register(self._btn_show_highlights, "sa_show_highlights")
        mgr.register(self._btn_save,            "sa_save_btn")
        mgr.register(self._tool_btns["freehand"], "sa_tool_freehand")
        mgr.register(self._tool_btns["line"],     "sa_tool_line")
        mgr.register(self._tool_btns["rect"],     "sa_tool_rect")
        mgr.register(self._tool_btns["ellipse"],  "sa_tool_ellipse")
        mgr.register(self._tool_btns["fill"],     "sa_tool_fill")
        mgr.register(self._tool_btns["polygon"],  "sa_tool_polygon")
        mgr.register(self._tool_btns["eraser"],     "sa_tool_eraser")
        mgr.register(self._tool_btns["transform"],  "sa_tool_transform")
        mgr.register(self._btn_close_poly,   "sa_close_poly")
        mgr.register(self._brush_spin,       "sa_brush_spin")
        mgr.register(self._eraser_spin,      "sa_eraser_spin")
        mgr.register(self._autocorrect_chk,  "sa_autocorrect")
        # Zoom buttons are in the floating overlay; register the overlay frame itself
        mgr.register(self._zoom_overlay, "sa_zoom_overlay")
        mgr.register(self._btn_show_all,     "sa_show_all_zones")
        mgr.register(self._btn_hide_all,     "sa_hide_all_zones")
        mgr.register(self._ze_vis_btn,    "sa_zone_visibility")
        mgr.register(self._ze_alpha_spin, "sa_zone_alpha_spin")
        mgr.register(self._ze_clear_btn,  "sa_zone_clear")
        mgr.register(self._ze_copy_btn,   "sa_zone_copy_mask")
        mgr.register(self._ze_paste_btn,  "sa_zone_paste_mask")
        mgr.register(self._history_overlay,  "sa_history_overlay")
        mgr.register(self._btn_clear_all,    "sa_clear_all")
        mgr.register(self._btn_copy_all_zones,  "sa_copy_all_zones")
        mgr.register(self._btn_paste_all_zones, "sa_paste_all_zones")
        mgr.register(self._az_slot_combo,       "sa_az_slot_combo")
        mgr.register(self._btn_az_slot_add,     "sa_az_slot_add")
        mgr.register(self._btn_az_slot_del,     "sa_az_slot_del")
        mgr.register(self._btn_az_slot_clear,   "sa_az_slot_clear")
        mgr.register(self._btn_az_slot_rename,  "sa_az_slot_rename")
        mgr.register(self._slot_combo,          "sa_slot_combo")
        mgr.register(self._btn_slot_save,       "sa_slot_save")
        mgr.register(self._btn_slot_paste,      "sa_slot_paste")
        mgr.register(self._btn_slot_add,        "sa_slot_add")
        mgr.register(self._btn_slot_del,        "sa_slot_del")
        mgr.register(self._btn_slot_clear,      "sa_slot_clear")
        mgr.register(self._btn_slot_rename,     "sa_slot_rename")
        mgr.register(self._active_zone_combo,   "sa_active_zone_combo")
        mgr.register(self._canvas,              "sa_canvas")
        mgr.register(self._status_lbl,       "sa_status_lbl")
        mgr.register(self._btn_import_shared,            "sa_import_shared")
        mgr.register(self._btn_import_to_az_slot,        "sa_import_to_az_slot")
        mgr.register(self._btn_import_zone_to_clipboard, "sa_import_zone_clipboard")

    @staticmethod
    def _tool_tooltip(key: str) -> str:
        tips = {
            "freehand":  "Paint freehand.  Hold & drag to brush over the image.  [B]",
            "line":      "Draw a straight filled line.  Drag from start to end.  [L]",
            "rect":      "Fill a rectangle.  Drag from one corner to the opposite.  [R]",
            "ellipse":   "Fill an ellipse.  Drag bounding box corner-to-corner.  [X]",
            "fill":      "Click to flood-fill a region.  Stops at image edges.  [F]",
            "polygon":   "Click to add vertices; double-click to close & fill.\n"
                         "Press Esc to cancel.  [P]",
            "eraser":    "Erase painted highlights from all zones.\n"
                         "Hold & drag to remove previously painted areas.  [E]",
            "transform": "Drag to move the zone mask under the cursor.\n"
                         "Shift+drag to rotate.  Ctrl+drag to scale.\n"
                         "One undo entry is created per drag gesture.  [T]",
        }
        return tips.get(key, "")

    def _update_status(self) -> None:
        """Refresh the status label with current tool / zone / size / zoom info."""
        tool_names = {
            "freehand":  "Freehand",
            "line":      "Line",
            "rect":      "Rectangle",
            "ellipse":   "Ellipse",
            "fill":      "Fill",
            "polygon":   "Polygon",
            "eraser":    "Eraser",
            "transform": "Move",
        }
        tool_key  = self._canvas._tool
        tool_name = tool_names.get(tool_key, tool_key.title())
        zone_idx  = self._canvas._active_zone
        from ..core.selective_alpha_processor import ZONE_NAMES
        zone_name = ZONE_NAMES[zone_idx] if 0 <= zone_idx < len(ZONE_NAMES) else f"Zone {zone_idx + 1}"
        if tool_key == "eraser":
            size_txt = f"Eraser: {self._eraser_spin.value()} px"
        else:
            size_txt = f"Brush: {self._brush_spin.value()} px"
        zoom_pct = int(round(self._canvas._zoom * 100))
        self._status_lbl.setText(
            f"Tool: {tool_name}  |  {zone_name}  |  {size_txt}  |  Zoom: {zoom_pct}%"
            "  |  🖱 Hold scroll-button to pan  |  Ctrl+scroll to zoom"
        )

    def _set_btn_save_enabled(self, v: bool) -> None:
        self._btn_save.setEnabled(v)

    # ---------------------------------------------------------------- slots

    def _on_show_highlights_toggled(self, checked: bool) -> None:
        """Master toggle: show or hide all zone overlays from the top button."""
        if checked:
            self._on_show_all_zones()
        else:
            self._on_hide_all_zones()

    def _on_show_all_zones(self) -> None:
        """Make all zone overlays visible and sync the zone editor."""
        for idx in range(NUM_ZONES):
            self._canvas.set_zone_visible(idx, True)
        self._refresh_zone_editor(self._ze_cur_idx)

    def _on_hide_all_zones(self) -> None:
        """Hide all zone overlays and sync the zone editor."""
        for idx in range(NUM_ZONES):
            self._canvas.set_zone_visible(idx, False)
        self._refresh_zone_editor(self._ze_cur_idx)

    def _on_zone_color_changed(self, zone_idx: int, rgb: tuple) -> None:
        """Propagate a user-chosen zone colour to the canvas and save settings."""
        r, g, b = rgb
        self._canvas.set_zone_color(zone_idx, r, g, b)
        self._save_settings()

    def _on_copy_mask(self, zone_idx: int) -> None:
        """Copy the painted mask of *zone_idx* into the single-slot clipboard."""
        arr = self._canvas.get_mask_as_array(zone_idx)
        if arr is None:
            QMessageBox.information(
                self, "Nothing to copy",
                f"Zone {zone_idx + 1} has no painted mask to copy."
            )
            return
        self._mask_clipboard = arr
        # Enable Paste button and the canvas context menu.
        self._ze_paste_btn.setEnabled(True)
        self._canvas.set_paste_available(True)
        if self._sound is not None:
            self._sound.play_mask_copy()

    def _on_paste_mask(self, zone_idx: int) -> None:
        """Paste the clipboard mask into *zone_idx*, replacing its current mask."""
        if self._mask_clipboard is None:
            QMessageBox.information(
                self, "Nothing to paste",
                "Copy a zone mask first using the 📋 Copy Mask button."
            )
            return
        if not self._canvas.has_image():
            return
        # Attempt to paste; set_mask_from_array validates dimensions.
        self._canvas.set_mask_from_array(zone_idx, self._mask_clipboard.copy())
        if self._sound is not None:
            self._sound.play_mask_paste()

    def _on_save_to_slot(self, slot_idx: int) -> None:
        """Save the active zone's mask into slot *slot_idx* (internal helper)."""
        active_zone = self._canvas.get_active_zone() if self._canvas.has_image() else -1
        if not self._canvas.has_image() or active_zone < 0:
            QMessageBox.information(
                self, "No image loaded",
                "Please open an image and paint a zone mask before saving to a slot."
            )
            return
        arr = self._canvas.get_mask_as_array(active_zone)
        if arr is None:
            QMessageBox.information(
                self, "Nothing to save",
                f"Zone {active_zone + 1} has no painted mask to save."
            )
            return
        self._mask_slots[slot_idx] = arr.copy()
        zone_name = ZONE_NAMES[active_zone] if active_zone < len(ZONE_NAMES) else f"Zone {active_zone + 1}"
        self._mask_slot_info[slot_idx] = zone_name
        self._update_slot_combo_item(slot_idx)
        # Refresh info label if this slot is currently selected
        if self._slot_combo.currentIndex() == slot_idx:
            self._on_slot_selected(slot_idx)
        if self._sound is not None:
            self._sound.play_mask_copy()

    def _on_paste_from_slot(self, slot_idx: int) -> None:
        """Paste the mask stored in slot *slot_idx* into the currently active zone."""
        if slot_idx < 0 or slot_idx >= len(self._mask_slots):
            return
        if self._mask_slots[slot_idx] is None:
            QMessageBox.information(
                self, "Slot empty",
                f"Slot {slot_idx + 1} is empty. Save a mask there first."
            )
            return
        if not self._canvas.has_image():
            QMessageBox.information(
                self, "No image loaded",
                "Please open an image before pasting a saved mask."
            )
            return
        active_zone = self._canvas.get_active_zone()
        self._canvas.set_mask_from_array(active_zone, self._mask_slots[slot_idx].copy())
        if self._sound is not None:
            self._sound.play_mask_paste()

    # ---- dropdown slot helpers -----------------------------------------

    def _update_slot_combo_item(self, idx: int) -> None:
        """Refresh the combo text for a single slot index."""
        custom = self._mask_slot_names[idx] if idx < len(self._mask_slot_names) else ""
        prefix = custom if custom else f"Slot {idx + 1}"
        info = self._mask_slot_info[idx]
        if self._mask_slots[idx] is None:
            label = f"{prefix}  —  (empty)"
        else:
            label = f"{prefix}  —  {info}"
        self._slot_combo.setItemText(idx, label)

    def _on_slot_selected(self, idx: int) -> None:
        """Update info label and paste button state when the combo selection changes."""
        if idx < 0 or idx >= len(self._mask_slots):
            return
        if self._mask_slots[idx] is None:
            self._slot_info_lbl.setText("(empty)")
            self._slot_info_lbl.setStyleSheet("color: #888; font-size: 10px;")
            self._btn_slot_paste.setEnabled(False)
        else:
            self._slot_info_lbl.setText(f"Saved from: {self._mask_slot_info[idx]}")
            self._slot_info_lbl.setStyleSheet("color: #aef; font-size: 10px;")
            self._btn_slot_paste.setEnabled(True)

    def _on_save_to_slot_current(self) -> None:
        """Save active zone's mask into the currently selected slot."""
        self._on_save_to_slot(self._slot_combo.currentIndex())

    def _on_paste_from_slot_current(self) -> None:
        """Paste the currently selected slot's mask into the active zone."""
        self._on_paste_from_slot(self._slot_combo.currentIndex())

    def _on_clear_slot_current(self) -> None:
        """Erase the stored mask from the currently selected slot."""
        idx = self._slot_combo.currentIndex()
        if idx < 0 or idx >= len(self._mask_slots):
            return
        self._mask_slots[idx] = None
        self._mask_slot_info[idx] = "(empty)"
        self._update_slot_combo_item(idx)
        self._on_slot_selected(idx)

    def _on_slot_add(self) -> None:
        """Append a new empty slot to the list (max _MASK_SLOT_COUNT)."""
        if len(self._mask_slots) >= self._MASK_SLOT_COUNT:
            return
        idx = len(self._mask_slots)
        self._mask_slots.append(None)
        self._mask_slot_info.append("(empty)")
        self._mask_slot_names.append("")
        self._slot_combo.addItem(f"Slot {idx + 1}  —  (empty)")
        self._slot_combo.setCurrentIndex(idx)
        self._btn_slot_del.setEnabled(len(self._mask_slots) > 1)
        self._btn_slot_add.setEnabled(len(self._mask_slots) < self._MASK_SLOT_COUNT)

    def _on_slot_del(self) -> None:
        """Delete the currently selected slot from the list."""
        if len(self._mask_slots) <= 1:
            return
        idx = self._slot_combo.currentIndex()
        if idx < 0 or idx >= len(self._mask_slots):
            return
        self._mask_slots.pop(idx)
        self._mask_slot_info.pop(idx)
        self._mask_slot_names.pop(idx)
        self._slot_combo.removeItem(idx)
        # Renumber remaining combo items so labels stay accurate.
        for i in range(self._slot_combo.count()):
            custom = self._mask_slot_names[i] if self._mask_slot_names[i] else f"Slot {i + 1}"
            if self._mask_slots[i] is None:
                self._slot_combo.setItemText(i, f"{custom}  —  (empty)")
            else:
                self._slot_combo.setItemText(i, f"{custom}  —  {self._mask_slot_info[i]}")
        new_idx = min(idx, self._slot_combo.count() - 1)
        self._slot_combo.setCurrentIndex(new_idx)
        self._on_slot_selected(new_idx)
        self._btn_slot_del.setEnabled(len(self._mask_slots) > 1)
        self._btn_slot_add.setEnabled(len(self._mask_slots) < self._MASK_SLOT_COUNT)

    def _on_slot_rename(self) -> None:
        """Rename the currently selected saved-mask slot."""
        idx = self._slot_combo.currentIndex()
        if idx < 0 or idx >= len(self._mask_slots):
            return
        current_name = self._mask_slot_names[idx] if self._mask_slot_names[idx] else f"Slot {idx + 1}"
        new_name, ok = QInputDialog.getText(
            self, "Rename Slot", "Enter a new name for this slot:", text=current_name
        )
        if not ok:
            return
        new_name = new_name.strip()
        self._mask_slot_names[idx] = new_name
        self._update_slot_combo_item(idx)
        self._on_slot_selected(idx)

    def _on_copy_all_zones(self) -> None:
        """Copy all painted zone masks into the currently selected all-zones slot."""
        snapshot = self._canvas.get_all_masks()
        idx = self._az_slot_combo.currentIndex()
        self._az_slots[idx] = snapshot
        self._az_slot_info[idx] = "all zones"
        self._update_az_slot_combo_item(idx)
        self._on_az_slot_selected(idx)
        if self._sound is not None:
            self._sound.play_mask_copy()

    def _on_paste_all_zones(self) -> None:
        """Paste all zone masks from the currently selected all-zones slot."""
        idx = self._az_slot_combo.currentIndex()
        if idx < 0 or idx >= len(self._az_slots):
            return
        if self._az_slots[idx] is None:
            return
        if not self._canvas.has_image():
            QMessageBox.information(
                self, "No image loaded",
                "Please open an image before pasting zones."
            )
            return
        self._canvas.set_all_masks(self._az_slots[idx])
        if self._sound is not None:
            self._sound.play_mask_paste()

    # ---- all-zones slot helpers ----------------------------------------

    def _update_az_slot_combo_item(self, idx: int) -> None:
        """Refresh the combo text for a single all-zones slot index."""
        custom = self._az_slot_names[idx] if idx < len(self._az_slot_names) else ""
        prefix = custom if custom else f"Slot {idx + 1}"
        if self._az_slots[idx] is None:
            label = f"{prefix}  —  (empty)"
        else:
            label = f"{prefix}  —  {self._az_slot_info[idx]}"
        self._az_slot_combo.setItemText(idx, label)

    def _on_az_slot_selected(self, idx: int) -> None:
        """Update info label and paste button when the all-zones combo changes."""
        if idx < 0 or idx >= len(self._az_slots):
            return
        if self._az_slots[idx] is None:
            self._az_slot_info_lbl.setText("(empty)")
            self._az_slot_info_lbl.setStyleSheet("color: #888; font-size: 10px;")
            self._btn_paste_all_zones.setEnabled(False)
        else:
            self._az_slot_info_lbl.setText(f"Saved: {self._az_slot_info[idx]}")
            self._az_slot_info_lbl.setStyleSheet("color: #aef; font-size: 10px;")
            self._btn_paste_all_zones.setEnabled(True)

    def _on_az_slot_clear(self) -> None:
        """Erase the snapshot stored in the currently selected all-zones slot."""
        idx = self._az_slot_combo.currentIndex()
        if idx < 0 or idx >= len(self._az_slots):
            return
        self._az_slots[idx] = None
        self._az_slot_info[idx] = "(empty)"
        self._update_az_slot_combo_item(idx)
        self._on_az_slot_selected(idx)

    def _on_az_slot_add(self) -> None:
        """Append a new empty all-zones slot to the list (max _AZ_SLOT_COUNT)."""
        if len(self._az_slots) >= self._AZ_SLOT_COUNT:
            return
        idx = len(self._az_slots)
        self._az_slots.append(None)
        self._az_slot_info.append("(empty)")
        self._az_slot_names.append("")
        self._az_slot_combo.addItem(f"Slot {idx + 1}  —  (empty)")
        self._az_slot_combo.setCurrentIndex(idx)
        self._btn_az_slot_del.setEnabled(len(self._az_slots) > 1)
        self._btn_az_slot_add.setEnabled(len(self._az_slots) < self._AZ_SLOT_COUNT)

    def _on_az_slot_del(self) -> None:
        """Delete the currently selected all-zones slot from the list."""
        if len(self._az_slots) <= 1:
            return
        idx = self._az_slot_combo.currentIndex()
        if idx < 0 or idx >= len(self._az_slots):
            return
        self._az_slots.pop(idx)
        self._az_slot_info.pop(idx)
        self._az_slot_names.pop(idx)
        self._az_slot_combo.removeItem(idx)
        # Renumber remaining combo items so labels stay accurate.
        for i in range(self._az_slot_combo.count()):
            custom = self._az_slot_names[i] if self._az_slot_names[i] else f"Slot {i + 1}"
            if self._az_slots[i] is None:
                self._az_slot_combo.setItemText(i, f"{custom}  —  (empty)")
            else:
                self._az_slot_combo.setItemText(i, f"{custom}  —  {self._az_slot_info[i]}")
        new_idx = min(idx, self._az_slot_combo.count() - 1)
        self._az_slot_combo.setCurrentIndex(new_idx)
        self._on_az_slot_selected(new_idx)
        self._btn_az_slot_del.setEnabled(len(self._az_slots) > 1)
        self._btn_az_slot_add.setEnabled(len(self._az_slots) < self._AZ_SLOT_COUNT)

    def _on_az_slot_rename(self) -> None:
        """Rename the currently selected all-zones slot."""
        idx = self._az_slot_combo.currentIndex()
        if idx < 0 or idx >= len(self._az_slots):
            return
        current_name = self._az_slot_names[idx] if self._az_slot_names[idx] else f"Slot {idx + 1}"
        new_name, ok = QInputDialog.getText(
            self, "Rename Slot", "Enter a new name for this slot:", text=current_name
        )
        if not ok:
            return
        new_name = new_name.strip()
        self._az_slot_names[idx] = new_name
        self._update_az_slot_combo_item(idx)
        self._on_az_slot_selected(idx)

    # ------------------------------------------------------------------
    # Cross-tool zone sharing: receive zones from the Alpha & RGBA tool
    # ------------------------------------------------------------------

    def receive_shared_zones(self, zones: list) -> None:
        """Store alpha zone data shared from the Alpha & RGBA Adjuster tool.

        Called by MainWindow when the user right-clicks the compare preview
        in the Alpha & RGBA Adjuster and selects "Copy zones → Selective Alpha
        tool".  The zones are held in memory until the user explicitly clicks
        the Import button.

        When exactly one zone is received it is also automatically copied to
        the single-zone clipboard so the user can paste it directly without
        any extra clicks (item 23).

        Parameters
        ----------
        zones : list of ``(alpha_value, bool_mask)`` tuples as produced by
                :func:`~selective_alpha_processor.detect_alpha_zones`.
        """
        if not zones:
            return
        import numpy as np_imp
        self._shared_zones = list(zones)
        count = len(zones)
        _display_max = 7
        displayed = zones[:_display_max]
        suffix = "…" if count > _display_max else ""

        # Auto-copy to single-zone clipboard when exactly one zone is sent (item 23).
        if count == 1:
            alpha_val, bool_mask = zones[0]
            self._mask_clipboard = (bool_mask.astype(np_imp.uint8) * 255)
            self._ze_paste_btn.setEnabled(True)
            clipboard_note = (
                f"\n📋 α={alpha_val} pre-loaded in clipboard — 'Paste Mask' to apply."
            )
        else:
            clipboard_note = ""

        self._import_shared_status.setText(
            f"✅ {count} zone(s) ready"
            f" (α: {', '.join(str(v) for v, _ in displayed)}{suffix})"
            f"{clipboard_note}"
        )
        self._import_shared_status.setStyleSheet("color: #aef; font-size: 10px;")
        self._btn_import_shared.setEnabled(True)
        self._btn_import_to_az_slot.setEnabled(True)
        self._btn_import_zone_to_clipboard.setEnabled(True)

    def _on_import_shared_zones(self) -> None:
        """Import the zones previously received from the Alpha & RGBA Adjuster.

        Populates the canvas masks and syncs the zone UI rows (alpha spinboxes
        and visibility toggles) for each received zone.  The entire operation
        is a single undo step so the user can revert with Ctrl+Z.
        """
        if not self._shared_zones:
            QMessageBox.information(
                self, "No zones to import",
                "No zones have been copied from the Alpha & RGBA Adjuster yet.\n\n"
                "In the Alpha & RGBA Adjuster tab:\n"
                "1. Load an image with multiple alpha values.\n"
                "2. Enable 'Highlight Alpha Values'.\n"
                "3. Right-click the preview and choose 'Copy zones → Selective Alpha tool'."
            )
            return
        if not self._canvas.has_image():
            QMessageBox.information(
                self, "No image loaded",
                "Please open an image in the Selective Alpha tool before importing zones."
            )
            return

        # populate_zones_from_detection handles undo snapshotting.
        self._canvas.populate_zones_from_detection(self._shared_zones)

        # Sync UI for each imported zone.
        for i, (alpha_val, _) in enumerate(self._shared_zones):
            if i >= NUM_ZONES:
                break
            self._canvas.set_zone_alpha_label(i, alpha_val)
            self._canvas.set_zone_visible(i, True)
        self._refresh_zone_editor(self._ze_cur_idx)

        self._on_show_all_zones()
        self._btn_show_highlights.setChecked(True)

        if self._sound is not None:
            self._sound.play_mask_paste()

        # Update import status to reflect that zones have been applied.
        count = len(self._shared_zones)
        self._import_shared_status.setText(
            f"✅ {count} zone(s) imported successfully."
        )

    def _on_import_to_az_slot(self) -> None:
        """Save all shared zones to the currently selected all-zones slot (item 22).

        Converts the (alpha_value, bool_mask) pairs from the Alpha tool into the
        snapshot format expected by _az_slots and stores them in the active slot.
        """
        if not self._shared_zones:
            return
        import numpy as np_imp
        idx = self._az_slot_combo.currentIndex()
        if idx < 0 or idx >= len(self._az_slots):
            return
        # Build a NUM_ZONES-length snapshot list (same format as get_all_masks())
        num_z = len(self._shared_zones)
        snapshot: list = [None] * NUM_ZONES
        for i, (_alpha_val, bool_mask) in enumerate(self._shared_zones):
            if i >= NUM_ZONES:
                break
            mask_u8 = (bool_mask.astype(np_imp.uint8) * 255)
            snapshot[i] = (i, mask_u8)
        self._az_slots[idx] = snapshot
        self._az_slot_info[idx] = f"{num_z} zone(s) from Alpha tool"
        self._update_az_slot_combo_item(idx)
        self._on_az_slot_selected(idx)
        self._import_shared_status.setText(
            f"✅ {num_z} zone(s) saved to All-Zones Slot {idx + 1}."
        )
        if self._sound is not None:
            self._sound.play_mask_copy()

    def _on_import_zone_to_clipboard(self) -> None:
        """Copy one zone from shared zones to the single-zone clipboard (item 23).

        If more than one zone was imported, a small picker dialog is shown so
        the user can select which zone to copy.  The mask is placed in
        ``_mask_clipboard`` so it can be pasted via the 'Paste Mask' button.
        """
        if not self._shared_zones:
            return
        import numpy as np_imp
        if len(self._shared_zones) == 1:
            zone_idx = 0
        else:
            from PyQt6.QtWidgets import QInputDialog
            items = [
                f"Zone {i + 1}  (α = {av})"
                for i, (av, _) in enumerate(self._shared_zones)
            ]
            item, ok = QInputDialog.getItem(
                self, "Select Zone",
                "Choose a zone to copy to the clipboard:",
                items, 0, False,
            )
            if not ok:
                return
            zone_idx = items.index(item)
        _alpha_val, bool_mask = self._shared_zones[zone_idx]
        self._mask_clipboard = (bool_mask.astype(np_imp.uint8) * 255)
        self._ze_paste_btn.setEnabled(True)
        self._import_shared_status.setText(
            f"✅ Zone {zone_idx + 1} copied to clipboard — use 'Paste Mask' to apply."
        )
        if self._sound is not None:
            self._sound.play_mask_copy()

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tiff *.tif *.webp *.tga *.gif *.ico);;All Files (*)",
        )
        if not path:
            return
        # Warn the user if the selected format does not support alpha channels.
        _, ext = os.path.splitext(path)
        if ext.lower() in _NO_ALPHA_EXTS:
            ans = QMessageBox.warning(
                self,
                "Format Does Not Support Alpha",
                f"The file you selected ({ext.upper() or 'unknown format'}) does not support a full "
                f"per-pixel alpha (transparency) channel.\n\n"
                f"The image will be loaded as-is for zone painting.\n"
                f"To preserve the alpha output you must save the result as PNG.\n\n"
                f"The Save dialog will suggest a PNG filename automatically.",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if ans == QMessageBox.StandardButton.Cancel:
                return
        try:
            loaded = self._canvas.load_image(path)
        except MemoryError:
            QMessageBox.critical(
                self, "Load Error",
                "Not enough memory to load this image.\n"
                "Try a smaller file or close other applications."
            )
            return
        except Exception as exc:
            QMessageBox.critical(
                self, "Load Error",
                f"An unexpected error occurred while loading the image:\n{exc}\n\n"
                "Please try a different file."
            )
            return
        if not loaded:
            QMessageBox.warning(self, "Load Error", f"Could not load:\n{path}")
            return
        self._src_path = path
        self._btn_save.setEnabled(True)
        # Clear result history
        if self._result_img is not None:
            self._result_img.close()
            self._result_img = None
        for img in self._result_history:
            img.close()
        self._result_history.clear()
        # Reset the Show Highlights toggle so a freshly-loaded image starts
        # with highlights shown; _auto_populate_zones_from_image will keep it
        # on if multiple distinct alpha zones are detected.
        self._btn_show_highlights.setChecked(True)
        self._on_hide_all_zones()
        # Auto-populate zone masks if the image has multiple distinct alphas.
        try:
            self._auto_populate_zones_from_image()
        except Exception as _exc:
            import logging as _log
            _log.getLogger(__name__).warning(
                "Auto-populate zones skipped after load error: %s", _exc
            )
            # Non-fatal: image is loaded; auto-detection just skipped

    def _auto_populate_zones_from_image(self) -> None:
        """Auto-detect distinct alpha zones in the loaded image and populate them.

        When the source image contains 2–20 significant distinct alpha values
        (each covering ≥ 0.5 % of pixels) the method automatically:
          - Paints each zone mask to cover pixels sharing that alpha value.
          - Sets the corresponding alpha spinbox to the detected value.
          - Makes all populated zones visible.

        If the image has more than NUM_ZONES significant alpha values (continuous
        gradient), the NUM_ZONES most pixel-dominant zones are used.
        The entire operation is a single undo step.
        """
        src_img = self._canvas.get_source_image()
        if src_img is None:
            return

        # Build RGBA array for analysis.
        if src_img.mode == "RGBA":
            arr = np.array(src_img, dtype=np.uint8)
        else:
            tmp = src_img.convert("RGBA")
            try:
                arr = np.array(tmp, dtype=np.uint8)
            finally:
                tmp.close()

        zones = detect_alpha_zones(arr)
        del arr  # free memory; masks will be stored inside the canvas

        if not zones:
            return

        # Populate canvas masks in one undo step.
        self._canvas.populate_zones_from_detection(zones)

        # Sync alpha values and zone visibility.
        for i, (alpha_val, _) in enumerate(zones):
            if i >= NUM_ZONES:
                break
            self._canvas.set_zone_alpha_label(i, alpha_val)
            self._canvas.set_zone_visible(i, True)

        # Sample per-zone colors from the source image pixels
        src_img = self._canvas.get_source_image()
        if src_img is not None:
            try:
                rgba_arr = np.array(src_img.convert("RGBA"), dtype=np.uint8)
                for i, (_, bool_mask) in enumerate(zones):
                    if i >= NUM_ZONES:
                        break
                    if not bool_mask.any():
                        continue
                    avg_r = int(rgba_arr[bool_mask, 0].mean())
                    avg_g = int(rgba_arr[bool_mask, 1].mean())
                    avg_b = int(rgba_arr[bool_mask, 2].mean())
                    self._canvas.set_zone_color(i, avg_r, avg_g, avg_b)
            except Exception:
                pass  # Fall back to preset colors

        self._refresh_zone_editor(self._ze_cur_idx)
        self._refresh_zone_combo_icons()
        self._refresh_zone_display_names()

        # Reveal all populated zones and sync the master Show Highlights toggle.
        self._on_show_all_zones()
        self._btn_show_highlights.setChecked(True)

    def _on_save(self) -> None:
        if not self._canvas.has_image():
            return
        # Auto-apply the zones before saving so the user doesn't need a
        # separate Apply step (items 84/85).
        try:
            bool_masks = self._canvas.get_masks_as_bool()
            zone_alphas = list(self._canvas._zone_alphas)
        except Exception as exc:
            QMessageBox.critical(self, "Save Error",
                                 f"Could not read zone data:\n{exc}")
            return

        # Warn if no zones are painted.
        if all(m is None or not m.any() for m in bool_masks):
            QMessageBox.information(
                self, "No zones painted",
                "Paint at least one zone before saving."
            )
            return

        # Log the action for crash reporting
        try:
            from main import log_action
            zones_used = sum(1 for m in bool_masks if m is not None and m.any())
            log_action(f"Selective alpha: applied {zones_used} zone(s) to '{self._src_path}'")
        except Exception:
            pass

        try:
            src_img = self._canvas.get_source_image()
            result = None
            result = apply_selective_alpha(src_img, bool_masks, zone_alphas)
            # Push previous result onto the undo-process history stack (capped).
            if self._result_img is not None:
                self._result_history.append(self._result_img)
                if len(self._result_history) > _MAX_HISTORY:
                    self._result_history.pop(0).close()
            self._result_img = result
            result = None
        except MemoryError:
            if result is not None:
                result.close()
            QMessageBox.critical(
                self, "Apply Error",
                "Not enough memory to apply alpha zones to this image.\n"
                "Try reducing the image size or closing other applications."
            )
            return
        except Exception as exc:
            if result is not None:
                result.close()
            QMessageBox.critical(self, "Apply Error", str(exc))
            return

        # Always default to a .png path – PNG is the only widely-supported
        # format that preserves a full per-pixel alpha channel.
        base = os.path.splitext(self._src_path)[0] if self._src_path else ""
        default_path = (base + "_selective_alpha.png") if base else ""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Result",
            default_path,
            "PNG (*.png);;WebP (*.webp);;TIFF (*.tiff *.tif);;TGA (*.tga);;All Files (*)",
        )
        if not path:
            return
        # Ensure the chosen path ends in a supported alpha-capable extension;
        # if the user typed a non-alpha extension warn and append .png.
        _, save_ext = os.path.splitext(path)
        if save_ext.lower() in _NO_ALPHA_EXTS:
            QMessageBox.warning(
                self,
                "Format Cannot Store Alpha",
                f"The chosen format ({save_ext.upper()}) cannot store alpha channel data.\n"
                f"The file will be saved as PNG instead.",
            )
            path = os.path.splitext(path)[0] + ".png"
        try:
            self._result_img.save(path)
            # Record in history
            if self._settings is not None:
                from datetime import datetime as _dt
                import os as _os
                n_zones = sum(
                    1 for a in self._canvas._zone_alphas if a != 255
                )
                entry = {
                    "timestamp": _dt.now().isoformat(timespec="seconds"),
                    "mode": f"{n_zones} zone{'s' if n_zones != 1 else ''} painted",
                    "file_count": 1,
                    "success": 1,
                    "errors": 0,
                    "files": [_os.path.basename(path)],
                    # Store source path for thumbnail display (item 9)
                    "first_file": str(self._src_path) if self._src_path else str(path),
                    "source": self._src_path,
                    "output": path,
                    "zone_alphas": list(self._canvas._zone_alphas),
                }
                if self._settings.get("history_track_selective_alpha", True):
                    self._settings.add_selective_alpha_history(entry)
        except MemoryError:
            QMessageBox.critical(
                self, "Save Error",
                "Not enough memory to save the image.\n"
                "Try closing other applications and try again."
            )
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))

    def _on_apply(self) -> None:
        """Apply alpha zones — kept for Ctrl+Enter shortcut compatibility."""
        self._on_save()

    def _on_mask_changed(self, zone_idx: int) -> None:
        # Invalidate apply state when masks change
        if self._sound is not None:
            now = time.monotonic()
            if now - self._last_zone_paint_sound_t >= 0.2:
                self._last_zone_paint_sound_t = now
                self._sound.play_zone_paint()

    def _on_undo_mask(self) -> None:
        """Undo the last drawing / erase action on the canvas.

        Does nothing (lets Qt handle the event natively) if the current focus
        widget is a text-editing control such as QLineEdit or QSpinBox, so that
        Ctrl+Z undoes typed text rather than canvas strokes (item 29).
        """
        from PyQt6.QtWidgets import QLineEdit as _LE, QSpinBox as _SB, QAbstractSpinBox as _ASB
        fw = self.focusWidget()
        if isinstance(fw, (_LE, _SB, _ASB)):
            # Native undo (text widget) — do nothing here; Qt handles it.
            return
        self._canvas.undo_mask()

    def _on_redo_mask(self) -> None:
        """Redo the last undone drawing / erase action.

        Same focus-aware guard as _on_undo_mask (item 29).
        """
        from PyQt6.QtWidgets import QLineEdit as _LE, QSpinBox as _SB, QAbstractSpinBox as _ASB
        fw = self.focusWidget()
        if isinstance(fw, (_LE, _SB, _ASB)):
            return
        self._canvas.redo_mask()

    def _on_undo_process(self) -> None:
        """Undo the last Apply operation (kept for backward compatibility)."""
        if not self._result_history:
            return
        # Close the current result without pushing it forward (discard).
        if self._result_img is not None:
            self._result_img.close()
        self._result_img = self._result_history.pop()

    @staticmethod
    def _make_zone_color_icon(r: int, g: int, b: int) -> QIcon:
        """Return a 14×14 solid-color QIcon for use in zone combo items."""
        pm = QPixmap(14, 14)
        pm.fill(QColor(r, g, b))
        return QIcon(pm)

    # Palette used by _color_name_for to map arbitrary RGB to a human-readable
    # colour label (nearest Euclidean distance in RGB space).
    _COLOR_PALETTE: list[tuple[str, tuple[int, int, int]]] = [
        ("Red",        (220,  20,  20)),
        ("Green",      ( 30, 180,  30)),
        ("Blue",       ( 30,  30, 210)),
        ("Yellow",     (230, 220,   0)),
        ("Orange",     (230, 115,   0)),
        ("Purple",     (130,   0, 200)),
        ("Pink",       (255, 100, 160)),
        ("Cyan",       (  0, 210, 210)),
        ("Magenta",    (200,   0, 200)),
        ("Lime",       ( 50, 230,  50)),
        ("Teal",       (  0, 180, 150)),
        ("Indigo",     ( 60,   0, 160)),
        ("Violet",     (150,   0, 210)),
        ("Gold",       (210, 170,   0)),
        ("Brown",      (130,  70,  30)),
        ("Navy",       (  0,   0, 130)),
        ("Coral",      (255,  80,  70)),
        ("Salmon",     (255, 140, 110)),
        ("Olive",      (100, 100,   0)),
        ("Maroon",     (130,   0,   0)),
        ("Sky Blue",   ( 80, 160, 220)),
        ("Forest",     (  0, 100,  50)),
        ("Rose",       (230,  50, 100)),
        ("Lavender",   (170, 130, 220)),
        ("Peach",      (255, 190, 140)),
        ("Mint",       (150, 230, 180)),
        ("Crimson",    (180,   0,  40)),
        ("Amber",      (255, 180,   0)),
        ("Slate",      ( 80, 100, 120)),
        ("Silver",     (190, 190, 190)),
        ("Steel Blue", ( 70, 130, 180)),
        ("Dark Red",   (150,   0,   0)),
        ("Dark Green", (  0, 110,   0)),
        ("Dark Blue",  (  0,   0, 139)),
        ("Dark Gray",  ( 80,  80,  80)),
        ("Light Gray", (200, 200, 200)),
        ("Tan",        (210, 180, 140)),
        ("Turquoise",  ( 64, 224, 208)),
        ("White",      (255, 255, 255)),
        ("Black",      (  0,   0,   0)),
    ]

    @classmethod
    def _color_name_for(cls, r: int, g: int, b: int) -> str:
        """Return the nearest named colour for the given (r, g, b) value."""
        best, best_d = "Color", float("inf")
        for name, (cr, cg, cb) in cls._COLOR_PALETTE:
            d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
            if d < best_d:
                best_d, best = d, name
        return best

    def _refresh_zone_display_names(self) -> None:
        """Derive zone display names from current canvas colours and update the
        active-zone combo so names always reflect the real colour of each zone.
        Custom names (set via the Zone Editor name field) take precedence (item 33)."""
        for i in range(NUM_ZONES):
            r, g, b, _ = self._canvas.get_zone_color(i)
            color_name = self._color_name_for(r, g, b)
            auto_label = f"Zone {i + 1} – {color_name}"
            custom = self._zone_custom_names[i] if i < len(self._zone_custom_names) else None
            label = custom if custom else auto_label
            self._zone_display_names[i] = label
            self._active_zone_combo.setItemText(i, label)

    def _refresh_zone_combo_icons(self) -> None:
        """Sync all active-zone combo item icons to current canvas zone colors."""
        for i in range(NUM_ZONES):
            r, g, b, _ = self._canvas.get_zone_color(i)
            self._active_zone_combo.setItemIcon(i, self._make_zone_color_icon(r, g, b))

    def _on_zone_action(self, val: int) -> None:
        if val >= 0:
            self._canvas.set_active_zone(val)
            # Keep the dropdown in sync when activated by other means
            if self._active_zone_combo.currentIndex() != val:
                self._active_zone_combo.blockSignals(True)
                self._active_zone_combo.setCurrentIndex(val)
                self._active_zone_combo.blockSignals(False)
            self._refresh_zone_editor(val)
            self._update_status()
        else:
            zone_idx = -(val + 1)
            if 0 <= zone_idx < NUM_ZONES:
                self._canvas.clear_mask(zone_idx)

    def _refresh_zone_editor(self, idx: int) -> None:
        """Update the single zone editor panel to reflect zone *idx*."""
        self._ze_cur_idx = idx
        # Populate the editable name field with the custom name (if set)
        # and the placeholder with the auto-derived colour name (item 33).
        r_c, g_c, b_c, _ = self._canvas.get_zone_color(idx)
        color_name = self._color_name_for(r_c, g_c, b_c)
        auto_label = f"Zone {idx + 1} – {color_name}"
        custom = self._zone_custom_names[idx] if 0 <= idx < len(self._zone_custom_names) else None
        self._ze_name_edit.blockSignals(True)
        self._ze_name_edit.setText(custom or "")
        self._ze_name_edit.setPlaceholderText(auto_label)
        self._ze_name_edit.blockSignals(False)
        self._ze_swatch_btn.setStyleSheet(
            f"background:{QColor(r_c, g_c, b_c).name()};"
            "border:1px solid #666;border-radius:3px;padding:0;"
        )
        visible = self._canvas._zone_visible[idx]
        self._ze_vis_btn.setChecked(visible)
        self._ze_vis_btn.setText("👁" if visible else "🚫")
        self._ze_alpha_spin.blockSignals(True)
        self._ze_alpha_spin.setValue(self._canvas._zone_alphas[idx])
        self._ze_alpha_spin.blockSignals(False)

    def _on_ze_vis_toggled(self, checked: bool) -> None:
        self._ze_vis_btn.setText("👁" if checked else "🚫")
        self._canvas.set_zone_visible(self._ze_cur_idx, checked)

    def _on_ze_pick_color(self) -> None:
        r, g, b, _ = self._canvas.get_zone_color(self._ze_cur_idx)
        chosen = QColorDialog.getColor(
            QColor(r, g, b), self,
            f"Choose colour for {self._zone_display_names[self._ze_cur_idx]}"
        )
        if chosen.isValid():
            self._canvas.set_zone_color(
                self._ze_cur_idx, chosen.red(), chosen.green(), chosen.blue()
            )
            self._ze_swatch_btn.setStyleSheet(
                f"background:{chosen.name()};"
                "border:1px solid #666;border-radius:3px;padding:0;"
            )
            self._active_zone_combo.setItemIcon(
                self._ze_cur_idx,
                self._make_zone_color_icon(chosen.red(), chosen.green(), chosen.blue())
            )
            # Update the display name to reflect the new colour (if no custom name)
            color_name = self._color_name_for(chosen.red(), chosen.green(), chosen.blue())
            auto_label = f"Zone {self._ze_cur_idx + 1} – {color_name}"
            custom = self._zone_custom_names[self._ze_cur_idx]
            display = custom if custom else auto_label
            self._zone_display_names[self._ze_cur_idx] = display
            self._active_zone_combo.setItemText(self._ze_cur_idx, display)
            self._ze_name_edit.setPlaceholderText(auto_label)
            self._save_settings()

    def _on_ze_clear(self) -> None:
        self._canvas.clear_mask(self._ze_cur_idx)

    def _on_ze_copy_mask(self) -> None:
        self._on_copy_mask(self._ze_cur_idx)

    def _on_ze_paste_mask(self) -> None:
        self._on_paste_mask(self._ze_cur_idx)

    def _on_ze_alpha_changed(self, value: int) -> None:
        self._canvas.set_zone_alpha_label(self._ze_cur_idx, value)
        self._save_settings()

    def _on_ze_name_edited(self, text: str) -> None:
        """Store the custom zone name and refresh the combo/display (item 33)."""
        idx = self._ze_cur_idx
        if 0 <= idx < NUM_ZONES:
            custom = text.strip() or None
            self._zone_custom_names[idx] = custom
            # Recompute the display label for this zone
            r, g, b, _ = self._canvas.get_zone_color(idx)
            color_name = self._color_name_for(r, g, b)
            auto_label = f"Zone {idx + 1} – {color_name}"
            display = custom if custom else auto_label
            self._zone_display_names[idx] = display
            self._active_zone_combo.setItemText(idx, display)

    def _on_tool_selected(self, key: str) -> None:
        # Track the previous tool so E can toggle back to it
        current = getattr(self._canvas, "_tool", "freehand")
        if key != current and current != "eraser":
            self._prev_non_eraser_tool = current
        self._canvas.set_tool(key)
        self._btn_close_poly.setVisible(key == "polygon")
        self._update_status()
        self._save_settings()

    def _select_tool_by_key(self, tool: str) -> None:
        """Select a drawing tool by key name and update the UI button state.

        Special case: pressing E while the eraser is already active toggles
        back to the previously used non-eraser tool (usually freehand/brush).
        """
        if tool not in self._tool_btns:
            return
        current_tool = getattr(self._canvas, "_tool", "freehand")
        if tool == "eraser" and current_tool == "eraser":
            # Toggle back to the previous non-eraser tool
            tool = getattr(self, "_prev_non_eraser_tool", "freehand")
        self._tool_btns[tool].setChecked(True)
        self._on_tool_selected(tool)

    def _adjust_brush_size(self, delta: int) -> None:
        """Adjust the active brush size spinbox by *delta* pixels (clamped)."""
        current_tool = getattr(self._canvas, "_tool", "freehand")
        if current_tool == "eraser":
            new_val = max(1, min(200, self._eraser_spin.value() + delta))
            self._eraser_spin.setValue(new_val)
        else:
            new_val = max(1, min(200, self._brush_spin.value() + delta))
            self._brush_spin.setValue(new_val)

    def _on_close_polygon(self) -> None:
        """Programmatically close the in-progress polygon."""
        self._canvas.close_polygon()

    def _on_clear_all(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Clear All Zones?",
            "This will erase all painted zone masks.\n\n"
            "Are you sure you want to start over?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._canvas.clear_all_masks()

    def _zoom_in(self) -> None:
        self._canvas.zoom_by(1.25)

    def _zoom_out(self) -> None:
        self._canvas.zoom_by(1.0 / 1.25)

    def _zoom_reset(self) -> None:
        self._canvas.zoom_reset()

    def eventFilter(self, obj, event) -> bool:
        """Reposition the floating overlays when the canvas is resized."""
        if obj is self._canvas and event.type() == QEvent.Type.Resize:
            self._zoom_overlay.reposition(event.size())
            self._zoom_overlay.raise_()
            self._history_overlay.reposition(event.size())
            self._history_overlay.raise_()
        return False

