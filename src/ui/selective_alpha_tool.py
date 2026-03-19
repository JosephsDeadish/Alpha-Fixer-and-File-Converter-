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
    Qt, QEvent, QPointF, QRectF, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFont, QImage, QPainter, QPen,
    QBrush, QKeySequence, QShortcut,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QSpinBox, QCheckBox, QGroupBox,
    QFileDialog, QMessageBox, QScrollArea, QSizePolicy,
    QButtonGroup, QFrame, QColorDialog,
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
        r, g, b, _ = ZONE_COLORS[zone_idx]
    return QColor(r, g, b, alpha)


# ---------------------------------------------------------------------------
# Drawing canvas
# ---------------------------------------------------------------------------

_MAX_HISTORY = 50   # maximum number of undo steps kept


class SelectiveAlphaCanvas(QWidget):
    """
    Interactive canvas that shows the source image with coloured mask
    overlays and handles all drawing operations.

    Signals
    -------
    mask_changed     : int  – emitted with the zone index whenever a mask is edited.
    undo_available   : bool – True when there is at least one undo step available.
    redo_available   : bool – True when there is at least one redo step available.
    """

    mask_changed   = pyqtSignal(int)
    undo_available = pyqtSignal(bool)
    redo_available = pyqtSignal(bool)

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
        # "freehand" | "line" | "rect" | "ellipse" | "fill" | "polygon" | "eraser"
        self._tool: str = "freehand"

        # ---- Brush / eraser sizes (radius in image pixels) ---------------
        self._brush_size: int  = 10
        self._eraser_size: int = 10

        # ---- Drawing history (undo/redo) ----------------------------------
        # Each entry is a snapshot: list[Optional[np.ndarray]] (one per zone).
        self._history:    list[list] = []
        self._redo_stack: list[list] = []

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

        # ---- Zone-level visibility flags (True = overlay shown in canvas) -----
        self._zone_visible: list[bool] = [True] * NUM_ZONES

        # ---- Per-zone overlay colors (R,G,B,overlay_alpha) ------------------
        # Starts as a mutable copy of the module-level palette so individual
        # zones can be recoloured without touching the shared constant.
        self._zone_colors: list[tuple[int, int, int, int]] = list(ZONE_COLORS)

        # ---- Show-zero-alpha flag -----------------------------------------
        # When True, fully-transparent source pixels inside a painted zone
        # have their output alpha lifted so the overlay remains visible.
        self._show_zero_alpha: bool = False

        # ---- Cursor position for brush-size preview circle ---------------
        self._cursor_pos: QPointF | None = None

    # ---------------------------------------------------------------- public

    def load_image(self, path: str) -> bool:
        """Load *path* into the canvas.  Returns True on success.

        Raises
        ------
        MemoryError
            Re-raised without modification so callers can distinguish an
            out-of-memory condition from other load failures.
        """
        img = None
        rgba = None
        try:
            img = Image.open(path)
            img.load()
            rgba = img.convert("RGBA")
            if rgba is not img:
                img.close()
                img = None
            # Build the numpy array BEFORE modifying any instance state so
            # that a MemoryError here leaves the canvas fully unchanged.
            # rgba is tracked separately so the except handlers can close it
            # when the array allocation fails (ownership not yet transferred).
            new_arr = np.array(rgba, dtype=np.uint8)
            # Both operations succeeded: atomically replace the stored image.
            if self._src_img is not None:
                self._src_img.close()
            self._src_img  = rgba
            rgba = None   # ownership transferred to self._src_img
            self._src_arr  = new_arr
            self._edge_map = None   # recomputed lazily
            # Close any existing mask images before discarding them.
            for m in self._masks:
                if m is not None:
                    m.close()
            self._masks    = [None] * NUM_ZONES
            self._zoom     = 1.0
            self._pan_x    = 0.0
            self._pan_y    = 0.0
            self._poly_pts = []
            self._history.clear()
            self._redo_stack.clear()
            self._composite_dirty = True
            self.update()
            self.undo_available.emit(False)
            self.redo_available.emit(False)
            return True
        except MemoryError:
            if img is not None:
                img.close()
            if rgba is not None:
                rgba.close()
            raise
        except Exception:
            if img is not None:
                img.close()
            if rgba is not None:
                rgba.close()
            return False

    def unload_image(self) -> None:
        """Release all PIL images held by the canvas (source image and masks).

        Called by :meth:`SelectiveAlphaTool.closeEvent` so that file handles
        and pixel buffers are freed deterministically rather than waiting for
        the garbage collector.
        """
        if self._src_img is not None:
            self._src_img.close()
            self._src_img = None
        self._src_arr = None
        self._edge_map = None
        for i, m in enumerate(self._masks):
            if m is not None:
                m.close()
                self._masks[i] = None

    def clear_mask(self, zone_idx: int) -> None:
        """Erase the mask for zone *zone_idx*."""
        self._push_history()
        old = self._masks[zone_idx]
        if old is not None:
            old.close()
        self._masks[zone_idx]  = None
        self._composite_dirty  = True
        self.update()
        self.mask_changed.emit(zone_idx)

    def clear_all_masks(self) -> None:
        """Erase all zone masks."""
        self._push_history()
        for i in range(NUM_ZONES):
            m = self._masks[i]
            if m is not None:
                m.close()
            self._masks[i] = None
        self._composite_dirty = True
        self.update()
        for i in range(NUM_ZONES):
            self.mask_changed.emit(i)

    def set_zone_visible(self, idx: int, visible: bool) -> None:
        """Show or hide the overlay for zone *idx* in the canvas.

        Hidden zones still accumulate paint strokes; they are just not blended
        into the on-screen composite.  This lets the user focus on one region
        at a time without losing painted work.
        """
        if 0 <= idx < NUM_ZONES and self._zone_visible[idx] != visible:
            self._zone_visible[idx] = visible
            self._composite_dirty = True
            self.update()

    def set_zone_color(self, idx: int, r: int, g: int, b: int) -> None:
        """Replace the overlay colour for zone *idx* with (r, g, b).

        The overlay opacity (alpha) is preserved from the current entry.
        """
        if 0 <= idx < NUM_ZONES:
            _, _, _, oa = self._zone_colors[idx]
            self._zone_colors[idx] = (
                max(0, min(255, r)),
                max(0, min(255, g)),
                max(0, min(255, b)),
                oa,
            )
            self._composite_dirty = True
            self.update()

    def set_show_zero_alpha(self, enabled: bool) -> None:
        """Control whether fully-transparent source pixels inside a zone are highlighted.

        When *enabled* is ``True`` the overlay for painted zones is made
        visible even over pixels whose source alpha is 0, so the user can see
        and adjust zones on fully-transparent areas.
        """
        if self._show_zero_alpha != enabled:
            self._show_zero_alpha = enabled
            self._composite_dirty = True
            self.update()

    def get_zone_color(self, idx: int) -> tuple[int, int, int, int]:
        """Return the (R, G, B, overlay_alpha) tuple for zone *idx*."""
        return self._zone_colors[idx]

    def get_mask_as_array(self, idx: int) -> Optional[np.ndarray]:
        """Return the painted mask for zone *idx* as a uint8 (h, w) numpy array.

        Returns *None* if the zone has not been painted yet.  The array is a
        fresh copy so the caller may modify it freely.
        """
        if not (0 <= idx < NUM_ZONES):
            return None
        m = self._masks[idx]
        if m is None:
            return None
        return np.array(m, dtype=np.uint8)

    def set_mask_from_array(self, idx: int, arr: np.ndarray) -> None:
        """Replace the mask for zone *idx* with the uint8 array *arr*.

        A snapshot is pushed onto the undo stack before the replacement so the
        operation can be undone.  *arr* must have the same (h, w) shape as the
        source image; if no image is loaded the call is silently ignored.
        """
        if self._src_img is None or not (0 <= idx < NUM_ZONES):
            return
        iw, ih = self._src_img.size
        if arr.shape[:2] != (ih, iw):
            return
        self._push_history()
        new_mask = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="L")
        if self._masks[idx] is not None:
            self._masks[idx].close()
        self._masks[idx] = new_mask
        self._composite_dirty = True
        self.update()
        self.mask_changed.emit(idx)
        self.undo_available.emit(bool(self._history))
        self.redo_available.emit(False)

    def populate_zones_from_detection(
        self, zones: list[tuple[int, np.ndarray]]
    ) -> None:
        """Bulk-load auto-detected alpha masks into the canvas.

        All zone masks are set in a single undo step so the user can revert
        the entire auto-population with one Ctrl+Z.  Only zones present in
        *zones* are modified; any remaining zones keep their current (empty)
        state.

        Parameters
        ----------
        zones : list of ``(alpha_value, bool_mask)`` tuples as returned by
                :func:`~selective_alpha_processor.detect_alpha_zones`.
        """
        if self._src_img is None or not zones:
            return
        iw, ih = self._src_img.size
        self._push_history()
        for i, (_, bool_mask) in enumerate(zones):
            if i >= NUM_ZONES:
                break
            if bool_mask.shape != (ih, iw):
                continue
            mask_arr = (bool_mask.astype(np.uint8)) * 255
            new_mask = Image.fromarray(mask_arr, mode="L")
            if self._masks[i] is not None:
                self._masks[i].close()
            self._masks[i] = new_mask
        self._composite_dirty = True
        self.update()
        for i in range(min(len(zones), NUM_ZONES)):
            try:
                self.mask_changed.emit(i)
            except RuntimeError:
                pass
        self.undo_available.emit(bool(self._history))
        self.redo_available.emit(False)

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

    def set_eraser_size(self, size: int) -> None:
        self._eraser_size = max(1, size)

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
            self._push_history()
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
        self.update()

    def zoom_reset(self) -> None:
        """Reset zoom and pan to default (fit-to-canvas)."""
        self._zoom  = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.update()

    # ----------------------------------------------------------- undo / redo

    def undo_mask(self) -> bool:
        """Undo the last drawing action.  Returns True if something was undone."""
        if not self._history:
            return False
        self._redo_stack.append(self._snapshot())
        self._restore_snapshot(self._history.pop())
        self.undo_available.emit(bool(self._history))
        self.redo_available.emit(True)
        for i in range(NUM_ZONES):
            self.mask_changed.emit(i)
        return True

    def redo_mask(self) -> bool:
        """Redo a previously undone drawing action.  Returns True if done."""
        if not self._redo_stack:
            return False
        self._history.append(self._snapshot())
        self._restore_snapshot(self._redo_stack.pop())
        self.undo_available.emit(True)
        self.redo_available.emit(bool(self._redo_stack))
        for i in range(NUM_ZONES):
            self.mask_changed.emit(i)
        return True

    def has_undo(self) -> bool:
        return bool(self._history)

    def has_redo(self) -> bool:
        return bool(self._redo_stack)

    # ----------------------------------------------------------- history helpers

    def _snapshot(self) -> list:
        """Capture a copy of all zone masks as numpy arrays."""
        result = []
        for m in self._masks:
            if m is None:
                result.append(None)
            else:
                result.append(np.array(m, dtype=np.uint8).copy())
        return result

    def _restore_snapshot(self, state: list) -> None:
        """Restore all zone masks from a snapshot."""
        for i, arr in enumerate(state):
            old = self._masks[i]
            if old is not None:
                old.close()
            if arr is None:
                self._masks[i] = None
            else:
                self._masks[i] = Image.fromarray(arr, "L")
        self._composite_dirty = True
        self.update()

    def _push_history(self) -> None:
        """Push current mask state onto the undo stack and clear redo."""
        self._history.append(self._snapshot())
        if len(self._history) > _MAX_HISTORY:
            self._history.pop(0)
        self._redo_stack.clear()
        self.undo_available.emit(True)
        self.redo_available.emit(False)

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

    def _erase_brush(self, ix: int, iy: int) -> None:
        """Erase a circle from ALL zone masks at the given image position."""
        r = self._eraser_size
        for zone_idx, mask in enumerate(self._masks):
            if mask is None:
                continue
            draw = ImageDraw.Draw(mask)
            draw.ellipse([(ix - r, iy - r), (ix + r, iy + r)], fill=0)
            del draw
        self._composite_dirty = True

    def _erase_brush_move(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Erase a line+cap from ALL zone masks (fills gaps during fast moves)."""
        r = self._eraser_size
        for zone_idx, mask in enumerate(self._masks):
            if mask is None:
                continue
            draw = ImageDraw.Draw(mask)
            draw.line([(x0, y0), (x1, y1)], fill=0, width=r * 2)
            draw.ellipse([(x1 - r, y1 - r), (x1 + r, y1 + r)], fill=0)
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
        if np.array_equal(snapped, bool_mask):
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
        """Recompute the cached composite QImage.

        Zones whose visibility flag is False are rendered as if their mask is
        empty (the overlay is hidden) without discarding any painted data.
        Custom per-zone colours stored in *_zone_colors* are forwarded to
        :func:`composite_zones` so user colour choices are reflected live.
        """
        if self._src_arr is None:
            self._composite_qimg = None
            return
        raw_masks = self.get_masks_as_bool()
        # Apply visibility: hide zones that have been toggled off.
        visible_masks = [
            m if vis else None
            for m, vis in zip(raw_masks, self._zone_visible)
        ]
        comp = composite_zones(self._src_arr, visible_masks,
                               zone_colors=self._zone_colors,
                               show_zero_alpha=self._show_zero_alpha)
        self._composite_qimg = _np_to_qimage(comp)
        self._composite_dirty = False

    def paintEvent(self, event) -> None:   # noqa: N802
        if self._src_img is None:
            # Draw a placeholder when no image is loaded.
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(40, 40, 40))
            p.setPen(QColor(120, 120, 120))
            p.setFont(QFont("Arial", 13))
            p.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "📂  Open an image to start painting\n\n"
                "  Ctrl+O  Open    Ctrl+Z  Undo    Ctrl+Y  Redo\n"
                "  Ctrl+S  Save    Ctrl+Enter  Apply",
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
        if self._composite_qimg is not None and self._src_img is not None:
            s, ox, oy = self._transform()
            iw, ih = self._src_img.size
            dst = QRectF(ox, oy, iw * s, ih * s)
            p.drawImage(dst, self._composite_qimg)

        # Draw rubber-band preview for line/rect/ellipse tools.
        self._draw_preview(p)

        # Draw in-progress polygon.
        if self._tool == "polygon" and len(self._poly_pts) > 0:
            self._draw_polygon_preview(p)

        # Draw brush / eraser size preview circle at the cursor position.
        self._draw_cursor_circle(p)

        p.end()

    def _draw_preview(self, painter: QPainter) -> None:
        """Draw rubber-band shape preview during drag."""
        if not self._drawing or self._preview_end is None:
            return
        if self._tool not in ("line", "rect", "ellipse"):
            return

        zc = _zone_qcolor(self._active_zone, 200,
                          color_override=self._zone_colors[self._active_zone])
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
        zc = _zone_qcolor(self._active_zone, 220,
                          color_override=self._zone_colors[self._active_zone])
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

    def _draw_cursor_circle(self, painter: QPainter) -> None:
        """Draw a circle showing the current brush or eraser radius."""
        if self._cursor_pos is None or self._src_img is None:
            return
        if self._tool not in ("freehand", "eraser", "line"):
            return
        s, _ox, _oy = self._transform()
        if self._tool == "eraser":
            r = self._eraser_size * s
            # White dashed circle for eraser
            pen = QPen(QColor(255, 255, 255, 200), 1.5, Qt.PenStyle.DashLine)
        else:
            r = self._brush_size * s
            zc = _zone_qcolor(self._active_zone, 220,
                              color_override=self._zone_colors[self._active_zone])
            pen = QPen(zc, 1.5)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = self._cursor_pos.x(), self._cursor_pos.y()
        painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

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
            self._push_history()
            self._drawing = True
            self._paint_brush(ix, iy)
            self.mask_changed.emit(self._active_zone)
            self.update()

        elif self._tool == "eraser":
            self._push_history()
            self._drawing = True
            self._erase_brush(ix, iy)
            for i in range(NUM_ZONES):
                self.mask_changed.emit(i)
            self.update()

        elif self._tool in ("line", "rect", "ellipse"):
            self._drawing = True

        elif self._tool == "fill":
            self._push_history()
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
                    self._push_history()
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
        self._cursor_pos = pt

        if self._panning:
            dx = pt.x() - self._pan_start_w.x()
            dy = pt.y() - self._pan_start_w.y()
            self._pan_x = self._pan_start_off[0] + dx
            self._pan_y = self._pan_start_off[1] + dy
            self.update()
            return

        if not self._drawing:
            if self._tool == "polygon" and len(self._poly_pts) > 0:
                self._preview_end = pt
            # Always repaint so the cursor circle follows the mouse.
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
        elif self._tool == "eraser":
            lx, ly = self._last_img_pt
            self._erase_brush_move(lx, ly, ix, iy)
            self._last_img_pt = (ix, iy)
            for i in range(NUM_ZONES):
                self.mask_changed.emit(i)
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

        elif self._tool == "eraser":
            pass  # history already pushed at press; nothing extra needed

        elif self._tool == "line":
            self._push_history()
            self._paint_line_on_mask(sx, sy, ix, iy)
            if self._autocorrect:
                self._apply_autocorrect(self._active_zone)
            self.mask_changed.emit(self._active_zone)

        elif self._tool == "rect":
            self._push_history()
            self._paint_rect_on_mask(sx, sy, ix, iy)
            if self._autocorrect:
                self._apply_autocorrect(self._active_zone)
            self.mask_changed.emit(self._active_zone)

        elif self._tool == "ellipse":
            self._push_history()
            self._paint_ellipse_on_mask(sx, sy, ix, iy)
            if self._autocorrect:
                self._apply_autocorrect(self._active_zone)
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
        # Derivation: the image point under cursor (ix, iy) must satisfy
        #   ix = (pt.x - ox) / s   before and after the zoom.
        # Solving for pan_x_new gives:
        #   pan_x_new = (1 - ratio) * (pt.x - cw/2) + pan_x_old * ratio
        # where ratio = s_new / s_old = new_zoom / old_zoom.
        pt = event.position()
        ratio = self._zoom / old_zoom
        cw_f  = float(self.width())
        ch_f  = float(self.height())
        self._pan_x = (1.0 - ratio) * (pt.x() - cw_f / 2.0) + self._pan_x * ratio
        self._pan_y = (1.0 - ratio) * (pt.y() - ch_f / 2.0) + self._pan_y * ratio

        self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Escape cancels the current polygon in progress."""
        if event.key() == Qt.Key.Key_Escape and self._tool == "polygon":
            self._poly_pts    = []
            self._preview_end = None
            self.update()
        super().keyPressEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        """Clear cursor circle when the mouse leaves the canvas."""
        self._cursor_pos = None
        self.update()
        super().leaveEvent(event)


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
    """A three-row widget showing zone colour swatch, name, alpha spinbox,
    visibility toggle, Paint/Clear buttons, and Copy/Paste mask buttons."""

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
        self._vis_btn.setFixedSize(24, 24)
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

        # Name label
        name_lbl = QLabel(color_name)
        name_lbl.setMinimumWidth(52)
        top.addWidget(name_lbl)

        top.addStretch()

        # Alpha label + spinbox
        top.addWidget(QLabel("α:"))
        self._alpha_spin = QSpinBox()
        self._alpha_spin.setRange(0, 255)
        self._alpha_spin.setValue(128)
        self._alpha_spin.setMinimumWidth(62)
        self._alpha_spin.setToolTip(
            "Alpha value applied to all pixels painted in this zone "
            "(0=transparent, 255=opaque)."
        )
        top.addWidget(self._alpha_spin)
        outer.addLayout(top)

        # ── Row 2: Paint + Clear buttons ─────────────────────────────────
        bot = QHBoxLayout()
        bot.setContentsMargins(0, 0, 0, 0)
        bot.setSpacing(4)

        self._sel_btn = QPushButton("🖌  Paint")
        self._sel_btn.setCheckable(True)
        self._sel_btn.setMinimumHeight(26)
        self._sel_btn.setToolTip(f"Activate {color_name} for painting")
        self._sel_btn.clicked.connect(lambda: self.selected.emit(self._idx))
        bot.addWidget(self._sel_btn)

        self._clear_btn = QPushButton("✕  Clear")
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
        self._sel_btn.setChecked(selected)
        self.setProperty("active", str(selected).lower())
        self.style().unpolish(self)
        self.style().polish(self)

    def set_zone_color(self, rgb: tuple[int, int, int]) -> None:
        """Update the swatch colour without opening a dialog."""
        self._cur_rgb = (rgb[0], rgb[1], rgb[2])
        self._update_swatch_style()

    def set_paste_enabled(self, enabled: bool) -> None:
        """Enable or disable the Paste Mask button."""
        self._paste_btn.setEnabled(enabled)

    def register_tooltips(self, mgr) -> None:
        """Register zone-row widgets with the TooltipManager for cycling tips."""
        mgr.register(self._alpha_spin,  "sa_zone_alpha_spin")
        mgr.register(self._sel_btn,     "sa_zone_select")
        mgr.register(self._clear_btn,   "sa_zone_clear")
        mgr.register(self._vis_btn,     "sa_zone_visibility")
        mgr.register(self._copy_btn,    "sa_zone_copy_mask")
        mgr.register(self._paste_btn,   "sa_zone_paste_mask")


# ---------------------------------------------------------------------------
# Main tab widget
# ---------------------------------------------------------------------------


class SelectiveAlphaTool(QWidget):
    """Tab widget for the Selective Alpha editor."""

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
        # Zone-mask clipboard: stores a single uint8 ndarray (the most recent
        # Copy Mask action).  Copying again silently replaces the previous entry.
        self._mask_clipboard: Optional[np.ndarray] = None
        self._setup_ui()
        self._restore_settings()

    # ----------------------------------------------------------------- setup

    def _setup_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # ── Left panel (controls) ─────────────────────────────────────────
        left_panel = QWidget()
        left_panel.setFixedWidth(330)
        lv = QVBoxLayout(left_panel)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)

        # ── Workflow group: Open → Paint → Apply → Save ───────────────────
        # All primary actions are grouped here at the top so the workflow
        # is immediately obvious.
        wf_box = QGroupBox("Workflow  ①Open  ②Paint  ③Apply  ④Save")
        wf_lay = QVBoxLayout(wf_box)
        wf_lay.setSpacing(4)

        # Row 1: Open image
        self._btn_open = QPushButton("📂  Open Image…")
        self._btn_open.setMinimumHeight(30)
        self._btn_open.setToolTip("Open an image to edit  (Ctrl+O)")
        self._btn_open.clicked.connect(self._on_open)
        wf_lay.addWidget(self._btn_open)

        # Row 2: Apply (primary action – most prominent)
        self._btn_apply = QPushButton("✅  Apply Alpha Zones")
        self._btn_apply.setEnabled(False)
        self._btn_apply.setMinimumHeight(34)
        self._btn_apply.setToolTip(
            "Apply the painted zones to the image and make the result ready to save.  (Ctrl+Enter)"
        )
        self._btn_apply.clicked.connect(self._on_apply)
        self._btn_apply.setStyleSheet(
            "QPushButton:enabled { font-weight: bold; }"
        )
        wf_lay.addWidget(self._btn_apply)

        # Row 3: Undo Process + Save side-by-side
        row3 = QHBoxLayout()
        row3.setSpacing(4)
        self._btn_undo_process = QPushButton("↩  Undo Process")
        self._btn_undo_process.setEnabled(False)
        self._btn_undo_process.setMinimumHeight(28)
        self._btn_undo_process.setToolTip(
            "Undo the last Apply operation and restore the previous result."
        )
        self._btn_undo_process.clicked.connect(self._on_undo_process)
        row3.addWidget(self._btn_undo_process)

        self._btn_save = QPushButton("💾  Save Result…")
        self._btn_save.setMinimumHeight(28)
        self._btn_save.setToolTip("Save the processed result to disk  (Ctrl+S)")
        self._btn_save.clicked.connect(self._on_save)
        self._btn_save.setEnabled(False)
        row3.addWidget(self._btn_save)
        wf_lay.addLayout(row3)

        # Row 4: Clear All Zones
        self._btn_clear_all = QPushButton("🗑  Clear All Zones")
        self._btn_clear_all.setMinimumHeight(28)
        self._btn_clear_all.setToolTip("Erase all painted zone masks and start over.")
        self._btn_clear_all.clicked.connect(self._on_clear_all)
        wf_lay.addWidget(self._btn_clear_all)

        lv.addWidget(wf_box)

        # ── Drawing History: Undo / Redo (above Drawing Tool) ─────────────
        hist_row = QHBoxLayout()
        hist_row.setSpacing(4)
        self._btn_undo = QPushButton("↩  Undo Drawing")
        self._btn_undo.setEnabled(False)
        self._btn_undo.setMinimumHeight(28)
        self._btn_undo.setToolTip("Undo the last highlight / erase action.  (Ctrl+Z)")
        self._btn_undo.clicked.connect(self._on_undo_mask)
        hist_row.addWidget(self._btn_undo)
        self._btn_redo = QPushButton("↪  Redo Drawing")
        self._btn_redo.setEnabled(False)
        self._btn_redo.setMinimumHeight(28)
        self._btn_redo.setToolTip("Redo the last undone action.  (Ctrl+Y)")
        self._btn_redo.clicked.connect(self._on_redo_mask)
        hist_row.addWidget(self._btn_redo)
        lv.addLayout(hist_row)

        # ── Drawing tools ─────────────────────────────────────────────────
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
            ("eraser",   "⌫  Eraser",    3, 0),
        ]
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)

        for key, label, row, col in tool_defs:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumHeight(28)
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

        # Tool sizes
        size_box = QGroupBox("Tool Size")
        sg = QGridLayout(size_box)
        sg.setSpacing(4)
        sg.addWidget(QLabel("Highlighter (px):"), 0, 0)
        self._brush_spin = QSpinBox()
        self._brush_spin.setRange(1, 200)
        self._brush_spin.setValue(10)
        self._brush_spin.setToolTip("Radius of the freehand / line / shape brush in image pixels.")
        self._brush_spin.valueChanged.connect(
            lambda v: self._canvas.set_brush_size(v)
        )
        sg.addWidget(self._brush_spin, 0, 1)
        sg.addWidget(QLabel("Eraser (px):"), 1, 0)
        self._eraser_spin = QSpinBox()
        self._eraser_spin.setRange(1, 200)
        self._eraser_spin.setValue(10)
        self._eraser_spin.setToolTip("Radius of the eraser brush in image pixels.")
        self._eraser_spin.valueChanged.connect(
            lambda v: self._canvas.set_eraser_size(v)
        )
        sg.addWidget(self._eraser_spin, 1, 1)
        lv.addWidget(size_box)

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
        self._btn_zoom_in = QPushButton("＋  In")
        self._btn_zoom_in.setMinimumHeight(26)
        self._btn_zoom_in.clicked.connect(self._zoom_in)
        self._btn_zoom_out = QPushButton("－  Out")
        self._btn_zoom_out.setMinimumHeight(26)
        self._btn_zoom_out.clicked.connect(self._zoom_out)
        self._btn_zoom_fit = QPushButton("⊡  Fit")
        self._btn_zoom_fit.setMinimumHeight(26)
        self._btn_zoom_fit.clicked.connect(self._zoom_reset)
        zh.addWidget(self._btn_zoom_out)
        zh.addWidget(self._btn_zoom_fit)
        zh.addWidget(self._btn_zoom_in)
        lv.addWidget(zoom_box)

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

        # Thin separator
        sep0 = QFrame()
        sep0.setFrameShape(QFrame.Shape.HLine)
        sep0.setFrameShadow(QFrame.Shadow.Sunken)
        sep0.setFixedHeight(1)
        zv.addWidget(sep0)

        self._zone_rows: list[_ZoneRow] = []
        for i in range(NUM_ZONES):
            row = _ZoneRow(i)
            row.selected.connect(self._on_zone_action)
            row.color_changed.connect(self._on_zone_color_changed)
            row.copy_requested.connect(self._on_copy_mask)
            row.paste_requested.connect(self._on_paste_mask)
            # visibility_changed and canvas connections wired after canvas is
            # created (further below in _setup_ui).
            zv.addWidget(row)
            self._zone_rows.append(row)
            # Thin separator between rows (not after the last one)
            if i < NUM_ZONES - 1:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setFrameShadow(QFrame.Shadow.Sunken)
                sep.setFixedHeight(1)
                sep.setStyleSheet("color: #3a3a5a; background: #3a3a5a;")
                zv.addWidget(sep)
        self._zone_rows[0].set_selected(True)
        lv.addWidget(zones_box)

        lv.addStretch()

        # ── Wrap left panel in a scroll area so controls remain accessible
        # on smaller windows (same pattern as alpha_tool.py / converter_tool.py).
        left_scroll = QScrollArea()
        left_scroll.setWidget(left_panel)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        left_scroll.setFixedWidth(350)  # panel 330 + scroll bar ~20
        root.addWidget(left_scroll)

        # ── Canvas ───────────────────────────────────────────────────────
        self._canvas = SelectiveAlphaCanvas()
        self._canvas.mask_changed.connect(self._on_mask_changed)
        self._canvas.undo_available.connect(self._btn_undo.setEnabled)
        self._canvas.redo_available.connect(self._btn_redo.setEnabled)

        # Wire zone visibility signals now that _canvas exists.
        for row in self._zone_rows:
            row.visibility_changed.connect(self._canvas.set_zone_visible)

        # Wire show-zero-alpha checkbox.
        self._show_zero_alpha_chk.toggled.connect(self._canvas.set_show_zero_alpha)

        # Wrap canvas + status label in a vertical layout.
        right_widget = QWidget()
        rv = QVBoxLayout(right_widget)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(2)
        rv.addWidget(self._canvas, 1)
        self._status_lbl = QLabel("Tool: Freehand  |  Zone 1 – Red  |  Brush: 10 px")
        self._status_lbl.setStyleSheet(
            "color: #999; font-size: 10px; padding: 2px 4px;"
        )
        rv.addWidget(self._status_lbl)
        root.addWidget(right_widget, 1)

        # Sync initial tool to canvas
        self._canvas.set_tool("freehand")
        self._canvas.set_active_zone(0)

        # Connect spinboxes to status updates (after _status_lbl is created).
        self._brush_spin.valueChanged.connect(lambda _: self._update_status())
        self._eraser_spin.valueChanged.connect(lambda _: self._update_status())

        # Auto-save settings when the user adjusts tool options.
        self._brush_spin.valueChanged.connect(lambda _: self._save_settings())
        self._eraser_spin.valueChanged.connect(lambda _: self._save_settings())
        self._autocorrect_chk.toggled.connect(lambda _: self._save_settings())
        self._show_zero_alpha_chk.toggled.connect(lambda _: self._save_settings())
        for row in self._zone_rows:
            row._alpha_spin.valueChanged.connect(lambda _: self._save_settings())

        # Keyboard shortcuts
        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        """Bind common keyboard shortcuts for the Selective Alpha editor."""
        QShortcut(QKeySequence("Ctrl+Z"),       self).activated.connect(self._on_undo_mask)
        QShortcut(QKeySequence("Ctrl+Y"),       self).activated.connect(self._on_redo_mask)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self).activated.connect(self._on_redo_mask)
        QShortcut(QKeySequence("Ctrl+O"),       self).activated.connect(self._on_open)
        QShortcut(QKeySequence("Ctrl+S"),       self).activated.connect(self._on_save)
        QShortcut(QKeySequence("Ctrl+Return"),  self).activated.connect(self._on_apply)

    def _restore_settings(self) -> None:
        """Restore previously saved Selective Alpha Tool settings."""
        if self._settings is None:
            return
        self._restoring = True
        try:
            # Restore zone alpha values
            alphas = self._settings.get_sa_zone_alphas()
            for row, alpha in zip(self._zone_rows, alphas):
                row.set_alpha(alpha)
            # Restore custom zone colours
            saved_colors = self._settings.get_sa_zone_colors()
            if saved_colors is not None:
                for idx, (row, c) in enumerate(zip(self._zone_rows, saved_colors)):
                    rgb = (c[0], c[1], c[2])
                    row.set_zone_color(rgb)
                    self._canvas.set_zone_color(idx, rgb[0], rgb[1], rgb[2])
            # Restore brush / eraser sizes
            self._brush_spin.setValue(int(self._settings.get("sa_brush_size", 10)))
            self._eraser_spin.setValue(int(self._settings.get("sa_eraser_size", 10)))
            # Restore autocorrect toggle
            self._autocorrect_chk.setChecked(bool(self._settings.get("sa_autocorrect", False)))
            # Restore show-zero-alpha toggle
            self._show_zero_alpha_chk.setChecked(
                bool(self._settings.get("sa_show_zero_alpha", False))
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
            [row.alpha_value() for row in self._zone_rows]
        )
        # Persist custom zone colours: read from canvas which is the source of truth.
        self._settings.set_sa_zone_colors(
            [list(self._canvas.get_zone_color(i)) for i in range(NUM_ZONES)]
        )
        self._settings.set("sa_brush_size",  self._brush_spin.value())
        self._settings.set("sa_eraser_size", self._eraser_spin.value())
        self._settings.set("sa_autocorrect", self._autocorrect_chk.isChecked())
        self._settings.set("sa_show_zero_alpha", self._show_zero_alpha_chk.isChecked())
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
        mgr.register(self._btn_open,         "sa_open_btn")
        mgr.register(self._btn_save,         "sa_save_btn")
        mgr.register(self._tool_btns["freehand"], "sa_tool_freehand")
        mgr.register(self._tool_btns["line"],     "sa_tool_line")
        mgr.register(self._tool_btns["rect"],     "sa_tool_rect")
        mgr.register(self._tool_btns["ellipse"],  "sa_tool_ellipse")
        mgr.register(self._tool_btns["fill"],     "sa_tool_fill")
        mgr.register(self._tool_btns["polygon"],  "sa_tool_polygon")
        mgr.register(self._tool_btns["eraser"],   "sa_tool_eraser")
        mgr.register(self._btn_close_poly,   "sa_close_poly")
        mgr.register(self._brush_spin,       "sa_brush_spin")
        mgr.register(self._eraser_spin,      "sa_eraser_spin")
        mgr.register(self._autocorrect_chk,  "sa_autocorrect")
        mgr.register(self._btn_zoom_in,      "sa_zoom_in")
        mgr.register(self._btn_zoom_out,     "sa_zoom_out")
        mgr.register(self._btn_zoom_fit,     "sa_zoom_fit")
        mgr.register(self._btn_show_all,     "sa_show_all_zones")
        mgr.register(self._btn_hide_all,     "sa_hide_all_zones")
        for row in self._zone_rows:
            row.register_tooltips(mgr)
        mgr.register(self._btn_undo,         "sa_undo")
        mgr.register(self._btn_redo,         "sa_redo")
        mgr.register(self._btn_apply,        "sa_apply")
        mgr.register(self._btn_undo_process, "sa_undo_process")
        mgr.register(self._btn_clear_all,    "sa_clear_all")
        mgr.register(self._canvas,           "sa_canvas")
        mgr.register(self._status_lbl,       "sa_status_lbl")

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
            "eraser":   "Erase painted highlights from all zones.\n"
                        "Hold & drag to remove previously painted areas.",
        }
        return tips.get(key, "")

    def _update_status(self) -> None:
        """Refresh the status label with current tool / zone / size info."""
        tool_names = {
            "freehand": "Freehand",
            "line":     "Line",
            "rect":     "Rectangle",
            "ellipse":  "Ellipse",
            "fill":     "Fill",
            "polygon":  "Polygon",
            "eraser":   "Eraser",
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
        self._status_lbl.setText(
            f"Tool: {tool_name}  |  {zone_name}  |  {size_txt}"
        )

    def _set_btn_save_enabled(self, v: bool) -> None:
        self._btn_save.setEnabled(v)

    # ---------------------------------------------------------------- slots

    def _on_show_all_zones(self) -> None:
        """Make all zone overlays visible and sync the eye-icon toggles."""
        for idx, row in enumerate(self._zone_rows):
            row._vis_btn.setChecked(True)
            self._canvas.set_zone_visible(idx, True)

    def _on_hide_all_zones(self) -> None:
        """Hide all zone overlays and sync the eye-icon toggles."""
        for idx, row in enumerate(self._zone_rows):
            row._vis_btn.setChecked(False)
            self._canvas.set_zone_visible(idx, False)

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
        # Enable Paste button on all zones.
        for row in self._zone_rows:
            row.set_paste_enabled(True)
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
        if not loaded:
            QMessageBox.warning(self, "Load Error", f"Could not load:\n{path}")
            return
        self._src_path = path
        self._btn_apply.setEnabled(True)
        self._btn_save.setEnabled(False)
        # Clear result history
        if self._result_img is not None:
            self._result_img.close()
            self._result_img = None
        for img in self._result_history:
            img.close()
        self._result_history.clear()
        self._btn_undo_process.setEnabled(False)
        # Auto-populate zone masks if the image has multiple distinct alphas.
        self._auto_populate_zones_from_image()

    def _auto_populate_zones_from_image(self) -> None:
        """Auto-detect distinct alpha zones in the loaded image and populate them.

        When the source image contains 2–7 significant distinct alpha values
        (each covering ≥ 0.5 % of pixels) the method automatically:
          - Paints each zone mask to cover pixels sharing that alpha value.
          - Sets the corresponding alpha spinbox to the detected value.
          - Makes all populated zones visible.

        The entire operation is a single undo step.  If the image has only one
        alpha value, or more than NUM_ZONES distinct significant values (smooth
        gradient), nothing happens.
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

        # Sync alpha spinboxes and zone visibility.
        for i, (alpha_val, _) in enumerate(zones):
            if i >= len(self._zone_rows):
                break
            row = self._zone_rows[i]
            row.set_alpha(alpha_val)
            # Ensure the visibility toggle is on for this zone.
            row._vis_btn.setChecked(True)
            self._canvas.set_zone_visible(i, True)

        # Reveal all populated zones.
        self._on_show_all_zones()

    def _on_save(self) -> None:
        if self._result_img is None:
            QMessageBox.information(
                self, "Nothing to save",
                "Press 'Apply Alpha Zones' first to generate the result."
            )
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
                entry = {
                    "timestamp": _dt.now().isoformat(timespec="seconds"),
                    "source": self._src_path,
                    "output": path,
                    "zone_alphas": [row.alpha_value() for row in self._zone_rows],
                }
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
            # Initialise to None so the except handlers can safely close it
            # if apply_selective_alpha raises after allocating an intermediate image.
            result = None
            result = apply_selective_alpha(
                src_img, bool_masks, zone_alphas
            )
            # Push previous result onto the undo-process history stack (capped).
            if self._result_img is not None:
                self._result_history.append(self._result_img)
                # Cap the process-undo stack to the same depth as drawing history.
                if len(self._result_history) > _MAX_HISTORY:
                    self._result_history.pop(0).close()
                self._btn_undo_process.setEnabled(True)
            self._result_img = result
            # Null out the local so the except handlers below do not
            # accidentally close the image that is now owned by _result_img.
            result = None
            self._btn_save.setEnabled(True)
            QMessageBox.information(
                self, "Done",
                "Alpha zones applied successfully.\n"
                "Click 'Save Result…' to export the image."
            )
        except MemoryError:
            if result is not None:
                result.close()
            QMessageBox.critical(
                self, "Apply Error",
                "Not enough memory to apply alpha zones to this image.\n"
                "Try reducing the image size or closing other applications."
            )
        except Exception as exc:
            if result is not None:
                result.close()
            QMessageBox.critical(self, "Apply Error", str(exc))

    def _on_mask_changed(self, zone_idx: int) -> None:
        # Invalidate apply state when masks change
        if self._sound is not None:
            now = time.monotonic()
            if now - self._last_zone_paint_sound_t >= 0.2:
                self._last_zone_paint_sound_t = now
                self._sound.play_zone_paint()

    def _on_undo_mask(self) -> None:
        """Undo the last drawing / erase action on the canvas."""
        self._canvas.undo_mask()

    def _on_redo_mask(self) -> None:
        """Redo the last undone drawing / erase action."""
        self._canvas.redo_mask()

    def _on_undo_process(self) -> None:
        """Undo the last Apply operation."""
        if not self._result_history:
            self._btn_undo_process.setEnabled(False)
            return
        # Close the current result without pushing it forward (discard).
        if self._result_img is not None:
            self._result_img.close()
        self._result_img = self._result_history.pop()
        self._btn_save.setEnabled(True)
        self._btn_undo_process.setEnabled(bool(self._result_history))

    def _on_zone_action(self, val: int) -> None:
        """val >= 0 → select zone; val < 0 → clear zone -(val+1)."""
        if val >= 0:
            self._canvas.set_active_zone(val)
            for i, row in enumerate(self._zone_rows):
                row.set_selected(i == val)
            self._update_status()
        else:
            zone_idx = -(val + 1)
            if 0 <= zone_idx < NUM_ZONES:
                self._canvas.clear_mask(zone_idx)

    def _on_tool_selected(self, key: str) -> None:
        self._canvas.set_tool(key)
        self._btn_close_poly.setVisible(key == "polygon")
        self._update_status()
        self._save_settings()

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
