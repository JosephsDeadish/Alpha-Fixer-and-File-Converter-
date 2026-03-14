"""
Selective Alpha processor.

Provides utilities for the Selective Alpha editor:
  - Edge detection (Sobel on grayscale)
  - Edge-constrained flood fill (smart-fill tool)
  - Mask auto-correct (snap drawn mask boundary to nearby edges)
  - Applying per-zone alpha values to the final image
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NUM_ZONES = 7

# Semi-transparent overlay colors (R, G, B, overlay-alpha) for the 7 zones.
# overlay-alpha = 130 ≈ 51 % opacity so the source image stays visible.
ZONE_COLORS: list[tuple[int, int, int, int]] = [
    (255,  60,  60, 130),   # zone 0 – Red
    ( 60, 200,  60, 130),   # zone 1 – Green
    ( 60, 120, 255, 130),   # zone 2 – Blue
    (255, 210,  50, 130),   # zone 3 – Yellow
    (200,  60, 255, 130),   # zone 4 – Purple
    ( 50, 220, 220, 130),   # zone 5 – Cyan
    (255, 140,  50, 130),   # zone 6 – Orange
]

# Human-readable zone names shown in the UI.
ZONE_NAMES: list[str] = [
    "Zone 1 – Red",
    "Zone 2 – Green",
    "Zone 3 – Blue",
    "Zone 4 – Yellow",
    "Zone 5 – Purple",
    "Zone 6 – Cyan",
    "Zone 7 – Orange",
]

# ---------------------------------------------------------------------------
# Edge detection
# ---------------------------------------------------------------------------


def detect_edges(img: Image.Image) -> np.ndarray:
    """Return a float32 edge-strength map in [0, 1] for *img*.

    Uses a vectorised 3×3 Sobel operator applied to the BT.601 luminance
    channel.  The result array has shape ``(height, width)``.

    Parameters
    ----------
    img : PIL Image (any mode)

    Returns
    -------
    float32 ndarray, shape (h, w), values in [0.0, 1.0]
    """
    rgba = img.convert("RGBA") if img.mode != "RGBA" else img
    try:
        arr = np.asarray(rgba, dtype=np.float32)
    finally:
        if rgba is not img:
            rgba.close()

    # BT.601 luminance
    lum = (0.299 * arr[:, :, 0]
           + 0.587 * arr[:, :, 1]
           + 0.114 * arr[:, :, 2])

    # Pad with edge-reflection so the output has the same size as the input.
    padded = np.pad(lum, 1, mode="edge")

    # Sobel X (horizontal gradient)
    gx = (
        -padded[:-2, :-2] - 2.0 * padded[1:-1, :-2] - padded[2:, :-2]
        + padded[:-2,  2:] + 2.0 * padded[1:-1,  2:] + padded[2:,  2:]
    )
    # Sobel Y (vertical gradient)
    gy = (
        -padded[:-2, :-2] - 2.0 * padded[:-2, 1:-1] - padded[:-2, 2:]
        + padded[2:,  :-2] + 2.0 * padded[2:,  1:-1] + padded[2:,  2:]
    )

    mag = np.sqrt(gx * gx + gy * gy)
    max_val = float(mag.max())
    if max_val > 0.0:
        mag /= max_val
    return mag.astype(np.float32)


# ---------------------------------------------------------------------------
# Edge-constrained flood fill
# ---------------------------------------------------------------------------


def edge_flood_fill(
    seed: tuple[int, int],
    edge_map: np.ndarray,
    threshold: float = 0.15,
) -> np.ndarray:
    """Return a boolean mask from *seed* that stops at strong edges.

    Uses an iterative DFS (depth-first stack) so it never hits Python
    recursion limits on large images.

    Parameters
    ----------
    seed      : ``(x, y)`` pixel coordinate in image space (col, row).
    edge_map  : float32 (h, w) array from :func:`detect_edges`.
    threshold : pixels with edge strength >= threshold block expansion.
                Must be in [0.0, 1.0].

    Returns
    -------
    bool ndarray, shape (h, w)
    """
    if not (0.0 <= threshold <= 1.0):
        import warnings
        warnings.warn(
            f"edge_flood_fill: threshold={threshold!r} is outside [0.0, 1.0]; "
            "clamping to valid range.",
            UserWarning,
            stacklevel=2,
        )
        threshold = max(0.0, min(1.0, threshold))

    h, w = edge_map.shape
    x0, y0 = int(seed[0]), int(seed[1])
    result = np.zeros((h, w), dtype=bool)

    if not (0 <= x0 < w and 0 <= y0 < h):
        return result
    if edge_map[y0, x0] >= threshold:
        return result

    visited = np.zeros((h, w), dtype=bool)
    stack: list[tuple[int, int]] = [(x0, y0)]
    visited[y0, x0] = True

    while stack:
        x, y = stack.pop()
        result[y, x] = True
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not visited[ny, nx]:
                visited[ny, nx] = True
                if edge_map[ny, nx] < threshold:
                    stack.append((nx, ny))

    return result


# ---------------------------------------------------------------------------
# Mask dilation helper (pure numpy, no scipy)
# ---------------------------------------------------------------------------


def _dilate_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    """4-connected binary dilation of *mask* by *radius* pixels.

    Each iteration expands the mask by one pixel in all four cardinal
    directions.  Implemented with numpy pad/slice arithmetic to avoid
    per-pixel Python loops.
    """
    if radius <= 0 or not mask.any():
        return mask.copy()
    result = mask.copy()
    for _ in range(radius):
        padded = np.pad(result, 1, constant_values=False)
        result = (
            padded[1:-1, 1:-1]              # original pixels (keep existing set)
            | padded[:-2, 1:-1] | padded[2:, 1:-1]
            | padded[1:-1, :-2] | padded[1:-1, 2:]
        )
    return result


# ---------------------------------------------------------------------------
# Auto-correct: snap drawn mask to nearby strong edges
# ---------------------------------------------------------------------------


def autocorrect_mask(
    drawn_mask: np.ndarray,
    edge_map: np.ndarray,
    search_radius: int = 12,
    edge_threshold: float = 0.25,
) -> np.ndarray:
    """Snap the boundary of *drawn_mask* toward nearby strong edges.

    After a freehand stroke is drawn *approximately* around an object, this
    function incorporates any strong-edge pixels that fall within
    *search_radius* pixels of the current mask boundary.  This lets the
    selection "snap" to the object's actual silhouette without requiring
    perfect tracing.

    Parameters
    ----------
    drawn_mask     : bool (h, w) ndarray – the user's drawn selection.
    edge_map       : float32 (h, w) from :func:`detect_edges`.
    search_radius  : maximum search distance in pixels.
    edge_threshold : minimum edge strength to qualify as a snap target.

    Returns
    -------
    bool ndarray (h, w) – original mask expanded to include nearby edges.
    """
    if not drawn_mask.any():
        return drawn_mask.copy()

    strong = edge_map >= edge_threshold
    if not strong.any():
        return drawn_mask.copy()

    # Boundary = 1-pixel shell just outside the current mask.
    dilated_1 = _dilate_mask(drawn_mask, 1)
    boundary = dilated_1 & ~drawn_mask

    if not boundary.any():
        return drawn_mask.copy()

    # Expand the boundary outward by search_radius to create a search zone.
    search_zone = _dilate_mask(boundary, search_radius)
    search_zone &= ~drawn_mask          # exclude the mask interior

    # Any strong-edge pixel inside the search zone is added to the mask.
    new_edges = search_zone & strong
    return drawn_mask | new_edges


# ---------------------------------------------------------------------------
# Apply per-zone alpha values
# ---------------------------------------------------------------------------


def apply_selective_alpha(
    img: Image.Image,
    zone_masks: list[Optional[np.ndarray]],
    zone_alphas: list[int],
) -> Image.Image:
    """Return a new RGBA image with per-zone alpha values applied.

    Zone 0 has the *highest* priority: when masks overlap, zone 0 wins over
    zones 1-6 (it is written last).  Pixels not covered by any zone keep
    their original alpha value.

    Parameters
    ----------
    img         : source PIL Image (any mode).
    zone_masks  : list of :data:`NUM_ZONES` bool ndarray (h, w), or ``None``
                  for an empty / unused zone.
    zone_alphas : list of :data:`NUM_ZONES` int values in [0, 255].

    Returns
    -------
    A new RGBA PIL Image.

    Raises
    ------
    ValueError
        If ``zone_masks`` or ``zone_alphas`` do not each have exactly
        :data:`NUM_ZONES` elements.
    """
    if len(zone_masks) != NUM_ZONES:
        raise ValueError(
            f"zone_masks must have exactly {NUM_ZONES} elements, "
            f"got {len(zone_masks)}"
        )
    if len(zone_alphas) != NUM_ZONES:
        raise ValueError(
            f"zone_alphas must have exactly {NUM_ZONES} elements, "
            f"got {len(zone_alphas)}"
        )
    out = img.convert("RGBA") if img.mode != "RGBA" else img
    try:
        arr = np.array(out, dtype=np.uint8)
        # Apply from lowest priority to highest so zone 0 is written last
        # and therefore wins on overlap.
        for mask, alpha_val in zip(reversed(zone_masks), reversed(zone_alphas)):
            if mask is not None and mask.any():
                arr[mask, 3] = np.uint8(np.clip(alpha_val, 0, 255))
        return Image.fromarray(arr, "RGBA")
    finally:
        if out is not img:
            out.close()


# ---------------------------------------------------------------------------
# Composite helper (used by the canvas for live preview)
# ---------------------------------------------------------------------------


def composite_zones(
    src_rgba: np.ndarray,
    zone_masks: list[Optional[np.ndarray]],
) -> np.ndarray:
    """Blend zone-colour overlays onto *src_rgba* and return uint8 RGBA.

    Parameters
    ----------
    src_rgba   : uint8 (h, w, 4) ndarray – source RGBA image.
    zone_masks : list of :data:`NUM_ZONES` bool (h, w) ndarray or ``None``.

    Returns
    -------
    uint8 ndarray (h, w, 4) – blended composite.

    Raises
    ------
    ValueError
        If ``zone_masks`` does not have exactly :data:`NUM_ZONES` elements.
    """
    if len(zone_masks) != NUM_ZONES:
        raise ValueError(
            f"zone_masks must have exactly {NUM_ZONES} elements, "
            f"got {len(zone_masks)}"
        )
    out = src_rgba.astype(np.float32, copy=True)
    for mask, (r, g, b, oa) in zip(zone_masks, ZONE_COLORS):
        if mask is None or not mask.any():
            continue
        a = oa / 255.0
        out[mask, 0] = out[mask, 0] * (1.0 - a) + r * a
        out[mask, 1] = out[mask, 1] * (1.0 - a) + g * a
        out[mask, 2] = out[mask, 2] * (1.0 - a) + b * a
    return np.clip(out, 0, 255).astype(np.uint8)
