"""
Selective Alpha processor.

Provides utilities for the Selective Alpha editor:
  - Edge detection (Sobel on grayscale)
  - Edge-constrained flood fill (smart-fill tool)
  - Mask auto-correct (snap drawn mask boundary to nearby edges)
  - Applying per-zone alpha values to the final image
  - Auto-detection of distinct alpha zones in a source image
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NUM_ZONES = 20

# Semi-transparent overlay colors (R, G, B, overlay-alpha) for up to 20 zones.
# overlay-alpha = 130 ≈ 51 % opacity so the source image stays visible.
ZONE_COLORS: list[tuple[int, int, int, int]] = [
    (255,  60,  60, 130),   # zone 0  – Red
    ( 60, 200,  60, 130),   # zone 1  – Green
    ( 60, 120, 255, 130),   # zone 2  – Blue
    (255, 210,  50, 130),   # zone 3  – Yellow
    (200,  60, 255, 130),   # zone 4  – Purple
    ( 50, 220, 220, 130),   # zone 5  – Cyan
    (255, 140,  50, 130),   # zone 6  – Orange
    (255, 100, 180, 130),   # zone 7  – Pink
    (100, 255, 180, 130),   # zone 8  – Mint
    (180, 120,  60, 130),   # zone 9  – Brown
    (255, 255, 120, 130),   # zone 10 – Light Yellow
    ( 80, 255, 255, 130),   # zone 11 – Aqua
    (255,  80, 255, 130),   # zone 12 – Magenta
    (150, 255,  80, 130),   # zone 13 – Lime
    ( 80, 150, 255, 130),   # zone 14 – Sky Blue
    (255, 170, 100, 130),   # zone 15 – Peach
    (180,  80, 255, 130),   # zone 16 – Violet
    ( 80, 255, 150, 130),   # zone 17 – Seafoam
    (255, 120,  80, 130),   # zone 18 – Coral
    (120, 120, 255, 130),   # zone 19 – Periwinkle
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
    "Zone 8 – Pink",
    "Zone 9 – Mint",
    "Zone 10 – Brown",
    "Zone 11 – Light Yellow",
    "Zone 12 – Aqua",
    "Zone 13 – Magenta",
    "Zone 14 – Lime",
    "Zone 15 – Sky Blue",
    "Zone 16 – Peach",
    "Zone 17 – Violet",
    "Zone 18 – Seafoam",
    "Zone 19 – Coral",
    "Zone 20 – Periwinkle",
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
        # Apply from the highest zone index down to zone 0 so that zone 0 is
        # written last and therefore wins on overlap (zone 0 has highest priority).
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
    zone_colors: Optional[list[tuple[int, int, int, int]]] = None,
    show_zero_alpha: bool = False,
) -> np.ndarray:
    """Blend zone-colour overlays onto *src_rgba* and return uint8 RGBA.

    Parameters
    ----------
    src_rgba        : uint8 (h, w, 4) ndarray – source RGBA image.
    zone_masks      : list of :data:`NUM_ZONES` bool (h, w) ndarray or ``None``.
    zone_colors     : optional list of ``(R, G, B, overlay_alpha)`` tuples, one
                      per zone.  When *None* (default) the module-level
                      :data:`ZONE_COLORS` palette is used.  Pass a custom list to
                      support user-chosen zone colours.
    show_zero_alpha : when *True*, pixels whose source alpha is 0 but that fall
                      inside a painted zone have their output alpha lifted to
                      the zone's overlay-alpha so the highlight is visible even
                      on fully-transparent source areas.  Defaults to *False*.

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
    colors = zone_colors if zone_colors is not None else ZONE_COLORS
    out = src_rgba.astype(np.float32, copy=True)
    for mask, (r, g, b, oa) in zip(zone_masks, colors):
        if mask is None or not mask.any():
            continue
        a = oa / 255.0
        out[mask, 0] = out[mask, 0] * (1.0 - a) + r * a
        out[mask, 1] = out[mask, 1] * (1.0 - a) + g * a
        out[mask, 2] = out[mask, 2] * (1.0 - a) + b * a
        if show_zero_alpha:
            # Lift fully-transparent masked pixels so the overlay is visible.
            zero_mask = mask & (src_rgba[:, :, 3] == 0)
            if zero_mask.any():
                out[zero_mask, 3] = float(oa)
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Auto-detection of distinct alpha zones
# ---------------------------------------------------------------------------


def detect_alpha_zones(
    arr: np.ndarray,
    min_pixel_fraction: float = 0.005,
) -> list[tuple[int, np.ndarray]]:
    """Detect distinct alpha-value zones in an RGBA uint8 array.

    Looks at the alpha channel and identifies pixel groups that share the same
    alpha value.  Only *significant* alpha values — those covering at least
    *min_pixel_fraction* of all pixels — are returned.

    The function returns an empty list when:
      - The array is not RGBA (fewer than 4 channels).
      - Fewer than 2 distinct significant alpha values exist (i.e. the image is
        uniformly transparent or uniformly opaque).

    When the image has more distinct significant alpha values than :data:`NUM_ZONES`
    (e.g. a smooth gradient), the :data:`NUM_ZONES` most pixel-dominant zones are
    still returned so the tool can auto-populate even for complex images.

    Parameters
    ----------
    arr               : uint8 ndarray (h, w, 4) – RGBA source array.
    min_pixel_fraction: minimum fraction of total pixels a unique alpha value
                        must occupy to be considered a zone.  Default 0.5%.

    Returns
    -------
    list of ``(alpha_value, bool_mask)`` tuples, sorted by pixel count
    (largest zone first), at most :data:`NUM_ZONES` entries.
    """
    if arr.ndim != 3 or arr.shape[2] < 4:
        return []

    alpha = arr[:, :, 3]
    total = alpha.size
    min_pixels = max(1, int(total * min_pixel_fraction))

    unique_vals, counts = np.unique(alpha, return_counts=True)

    # Keep only alpha values that represent at least min_pixel_fraction of pixels.
    significant = [
        (int(v), int(c))
        for v, c in zip(unique_vals, counts)
        if c >= min_pixels
    ]

    # Need at least 2 distinct values to auto-populate zones.
    if len(significant) < 2:
        return []

    # Sort by descending pixel count (most common zone first).
    # When there are more distinct values than NUM_ZONES, take only the
    # most significant ones rather than silently giving up – this lets
    # the tool auto-populate even for images with many distinct alpha
    # levels, picking the zones that cover the most pixels.
    significant.sort(key=lambda x: -x[1])
    significant = significant[:NUM_ZONES]

    result: list[tuple[int, np.ndarray]] = []
    for alpha_val, _ in significant:
        bool_mask = alpha == alpha_val
        result.append((alpha_val, bool_mask))

    return result


# ---------------------------------------------------------------------------
# Mask geometric transforms (shift / rotate / scale)
# ---------------------------------------------------------------------------


def shift_mask(mask: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Translate a uint8 or bool mask by (dx, dy) pixels.

    Pixels that shift outside the image boundary are discarded; the vacated
    area is filled with zeros.  Positive *dx* shifts right, positive *dy*
    shifts down.

    Parameters
    ----------
    mask : uint8 or bool ndarray, shape (h, w)
    dx   : horizontal shift in pixels (right-positive)
    dy   : vertical shift in pixels (down-positive)

    Returns
    -------
    Same dtype ndarray, shape (h, w)
    """
    h, w = mask.shape[:2]
    result = np.zeros_like(mask)
    src_x0 = max(0, -dx)
    src_x1 = min(w, w - dx)
    dst_x0 = max(0,  dx)
    dst_x1 = min(w, w + dx)
    src_y0 = max(0, -dy)
    src_y1 = min(h, h - dy)
    dst_y0 = max(0,  dy)
    dst_y1 = min(h, h + dy)
    if src_x0 < src_x1 and src_y0 < src_y1 and dst_x0 < dst_x1 and dst_y0 < dst_y1:
        result[dst_y0:dst_y1, dst_x0:dst_x1] = mask[src_y0:src_y1, src_x0:src_x1]
    return result


def rotate_mask(mask: np.ndarray, angle_degrees: float) -> np.ndarray:
    """Rotate a uint8 or bool mask around the image centre.

    Uses PIL nearest-neighbour resampling so the mask stays binary.  Positive
    *angle_degrees* rotates **counter-clockwise** (PIL convention).  The image
    size is unchanged; pixels that rotate outside the bounds are discarded.

    Parameters
    ----------
    mask           : uint8 or bool ndarray, shape (h, w)
    angle_degrees  : rotation angle in degrees (positive = counter-clockwise)

    Returns
    -------
    Same dtype ndarray, shape (h, w)
    """
    original_dtype = mask.dtype
    pil_src = Image.fromarray(mask.astype(np.uint8), mode="L")
    try:
        rotated = pil_src.rotate(
            angle_degrees,
            resample=Image.Resampling.NEAREST,
            expand=False,
            fillcolor=0,
        )
        result = np.array(rotated, dtype=np.uint8)
        rotated.close()
    finally:
        pil_src.close()
    if original_dtype == bool:
        return result > 0
    return result


def scale_mask(mask: np.ndarray, factor: float) -> np.ndarray:
    """Scale a uint8 or bool mask about the image centre by *factor*.

    Uses a PIL affine transform with nearest-neighbour resampling so the mask
    stays binary.  *factor* > 1 enlarges the masked region; 0 < *factor* < 1
    shrinks it.  The image size is unchanged.

    Parameters
    ----------
    mask   : uint8 or bool ndarray, shape (h, w)
    factor : scale factor (must be > 0)

    Returns
    -------
    Same dtype ndarray, shape (h, w)

    Raises
    ------
    ValueError
        If *factor* is not positive.
    """
    if factor <= 0.0:
        raise ValueError(f"scale_mask: factor must be positive, got {factor!r}")
    original_dtype = mask.dtype
    h, w = mask.shape[:2]
    inv_f = 1.0 / factor
    cx = (w - 1) / 2.0
    cy = (h - 1) / 2.0
    tx = cx * (1.0 - inv_f)
    ty = cy * (1.0 - inv_f)
    pil_src = Image.fromarray(mask.astype(np.uint8), mode="L")
    try:
        transformed = pil_src.transform(
            (w, h),
            Image.Transform.AFFINE,
            (inv_f, 0.0, tx, 0.0, inv_f, ty),
            resample=Image.Resampling.NEAREST,
            fillcolor=0,
        )
        result = np.array(transformed, dtype=np.uint8)
        transformed.close()
    finally:
        pil_src.close()
    if original_dtype == bool:
        return result > 0
    return result

