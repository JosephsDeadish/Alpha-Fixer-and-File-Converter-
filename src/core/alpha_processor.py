"""
Alpha channel processor.

Supports: PNG, JPEG, BMP, TIFF, GIF, WEBP, TGA, ICO, DDS (via Wand/ImageMagick),
          PPM, PCX, AVIF, QOI.
"""
import os
import io
import logging
from pathlib import Path
from typing import Optional, Callable

import numpy as np
from PIL import Image

from .presets import AlphaPreset

logger = logging.getLogger(__name__)

# Formats that natively support an alpha channel
ALPHA_FORMATS = {".png", ".webp", ".tga", ".tiff", ".tif", ".dds", ".gif", ".ico"}

# Formats that need conversion to RGBA before processing
CONVERT_TO_RGBA = {".jpg", ".jpeg", ".bmp"}

SUPPORTED_READ = {
    ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif",
    ".gif", ".webp", ".tga", ".ico", ".dds",
    ".ppm", ".pcx", ".avif", ".qoi",
}

SUPPORTED_WRITE = SUPPORTED_READ


def _has_wand() -> bool:
    try:
        import wand.image  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# DDS helpers (via Wand / ImageMagick)
# ---------------------------------------------------------------------------

def _load_dds(path: str) -> Image.Image:
    """Load a DDS file, returning an RGBA PIL Image."""
    if _has_wand():
        try:
            from wand.image import Image as WandImage
            with WandImage(filename=path) as wimg:
                wimg.format = "png"
                blob = wimg.make_blob()
            _tmp = Image.open(io.BytesIO(blob))
            try:
                return _tmp.convert("RGBA")
            finally:
                _tmp.close()
        except MemoryError:
            raise
        except Exception as exc:
            logger.warning("Wand failed to load DDS %s: %s", path, exc)
    # Fallback: minimal DDS reader using raw BGRA or RGBA data
    return _load_dds_raw(path)


def _load_dds_raw(path: str) -> Image.Image:
    """Very basic DDS reader for uncompressed RGBA/BGRA surfaces."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 128 or data[:4] != b"DDS ":
        raise ValueError("Not a valid DDS file")
    height = int.from_bytes(data[12:16], "little")
    width = int.from_bytes(data[16:20], "little")
    flags = int.from_bytes(data[80:84], "little")  # pixel format flags
    bits = int.from_bytes(data[88:92], "little")
    pixel_data = data[128:]
    expected = width * height * (bits // 8)
    if len(pixel_data) < expected or bits not in (32, 24):
        raise ValueError(f"Unsupported DDS pixel format (bits={bits})")
    arr = np.frombuffer(pixel_data[:expected], dtype=np.uint8).reshape(height, width, bits // 8)
    if bits == 32:
        # Most common: BGRA → RGBA
        img = Image.fromarray(arr[:, :, [2, 1, 0, 3]], "RGBA")
    else:
        _rgb = Image.fromarray(arr[:, :, [2, 1, 0]], "RGB")
        try:
            img = _rgb.convert("RGBA")
        finally:
            _rgb.close()
    return img


def _save_dds(img: Image.Image, path: str):
    """Save a PIL Image as DDS (BGRA uncompressed) via Wand, or fall back to raw."""
    if _has_wand():
        img_rgba = None
        buf = None
        try:
            from wand.image import Image as WandImage
            img_rgba = img.convert("RGBA")
            buf = io.BytesIO()
            img_rgba.save(buf, format="PNG")
            buf.seek(0)
            with WandImage(blob=buf.read(), format="png") as wimg:
                wimg.format = "dds"
                wimg.save(filename=path)
            return
        except MemoryError:
            raise
        except Exception as exc:
            logger.warning("Wand failed to save DDS %s: %s", path, exc)
        finally:
            if img_rgba is not None:
                img_rgba.close()
            if buf is not None:
                buf.close()
    _save_dds_raw(img, path)


def _save_dds_raw(img: Image.Image, path: str):
    """Write a minimal uncompressed BGRA DDS file."""
    img_rgba = img.convert("RGBA")
    try:
        w, h = img_rgba.size
        arr = np.array(img_rgba, dtype=np.uint8)
    finally:
        img_rgba.close()
    # Convert RGBA → BGRA
    bgra = arr[:, :, [2, 1, 0, 3]]
    pixel_data = bgra.tobytes()

    def dword(n):
        return n.to_bytes(4, "little")

    header = bytearray(128)
    header[0:4] = b"DDS "
    header[4:8] = dword(124)        # dwSize
    header[8:12] = dword(0x000A1007)  # DDSD flags: caps|height|width|pixelformat|linearsize
    header[12:16] = dword(h)
    header[16:20] = dword(w)
    header[20:24] = dword(w * 4)   # dwPitchOrLinearSize
    header[76:80] = dword(32)      # ddspf.dwSize
    header[80:84] = dword(0x41)    # ddspf.dwFlags: DDPF_ALPHAPIXELS | DDPF_RGB
    header[88:92] = dword(32)      # ddspf.dwRGBBitCount
    header[92:96] = dword(0x00FF0000)  # R mask
    header[96:100] = dword(0x0000FF00)  # G mask
    header[100:104] = dword(0x000000FF)  # B mask
    header[104:108] = dword(0xFF000000)  # A mask
    header[108:112] = dword(0x1000)  # dwCaps: DDSCAPS_TEXTURE

    with open(path, "wb") as f:
        f.write(bytes(header))
        f.write(pixel_data)


# ---------------------------------------------------------------------------
# Core alpha processing
# ---------------------------------------------------------------------------

def load_image(path: str) -> Image.Image:
    ext = Path(path).suffix.lower()
    if ext == ".dds":
        return _load_dds(path)
    img = Image.open(path)
    if img.mode != "RGBA":
        w, h = img.size
        try:
            img_rgba = img.convert("RGBA")
        except MemoryError:
            img.close()
            raise MemoryError(
                f"Not enough memory to load {w}×{h} image "
                f"({w * h / 1_000_000:.1f} megapixels) as RGBA. "
                "Try processing a smaller file."
            )
        except Exception:
            img.close()
            raise
        img.close()
        return img_rgba
    return img


def save_image(img: Image.Image, path: str, original_ext: str):
    ext = original_ext.lower()
    if ext == ".dds":
        _save_dds(img, path)
        return
    if ext in (".jpg", ".jpeg", ".bmp"):
        w, h = img.size
        try:
            img_rgb = img.convert("RGB")
        except MemoryError:
            raise MemoryError(
                f"Not enough memory to convert {w}×{h} image "
                f"({w * h / 1_000_000:.1f} megapixels) to RGB for {ext}. "
                "Try processing a smaller file."
            )
        try:
            img_rgb.save(path)
        except MemoryError:
            raise MemoryError(
                f"Not enough memory to write {w}×{h} image "
                f"({w * h / 1_000_000:.1f} megapixels) to {ext}. "
                "Try processing a smaller file."
            )
        finally:
            img_rgb.close()
        return
    w, h = img.size
    try:
        img.save(path)
    except MemoryError:
        raise MemoryError(
            f"Not enough memory to write {w}×{h} image "
            f"({w * h / 1_000_000:.1f} megapixels) to {ext}. "
            "Try processing a smaller file."
        )


def apply_alpha_preset(img: Image.Image, preset: AlphaPreset) -> Image.Image:
    """Apply an AlphaPreset to a PIL RGBA image and return the result.

    Processing pipeline (in order):
      1. Build processing mask: when preset.threshold > 0 and preset.binary_cut is
         False, only pixels with alpha strictly below the threshold are processed.
         Pixels at or above the threshold keep their original alpha (they are
         protected).  When threshold == 0 or binary_cut is True, all pixels are
         processed.
      2. Invert alpha (if preset.invert is True, applied to masked pixels only).
      3. Normalize: remap masked [img_min, img_max] → [clamp_min, clamp_max].
         When clamp_min == clamp_max every masked pixel gets that exact value.
      4. Binary threshold cut (if preset.binary_cut is True): pixels >= threshold → 255,
         else → 0 (applied to ALL pixels; threshold is the hard-cut split point here).
      5. Clamp to [clamp_min, clamp_max] (applied to processed pixels only when a
         threshold filter is active, so protected pixels keep their original values).
    """
    _converted = img.mode != "RGBA"
    if _converted:
        _orig = img
        img = img.convert("RGBA")
        _orig.close()
    try:
        try:
            arr = np.array(img, dtype=np.int32)
        except MemoryError:
            w, h = img.size
            raise MemoryError(
                f"Not enough memory to process {w}×{h} image "
                f"({w * h / 1_000_000:.1f} megapixels). Try a smaller image."
            )
        alpha = arr[:, :, 3].copy()

        # Step 1: Build processing mask.
        # When threshold > 0 with no binary_cut, protect pixels at/above threshold.
        # When threshold == 0 or binary_cut is True, process all pixels.
        if preset.threshold > 0 and not preset.binary_cut:
            proc_mask = alpha < preset.threshold
        else:
            proc_mask = np.ones(alpha.shape, dtype=bool)

        target_lo = min(preset.clamp_min, preset.clamp_max)
        target_hi = max(preset.clamp_min, preset.clamp_max)

        # Step 2: Invert (apply to masked pixels only)
        if preset.invert:
            alpha[proc_mask] = 255 - alpha[proc_mask]

        # Step 3: Normalize — remap masked [img_min, img_max] → [target_lo, target_hi].
        # When target_lo == target_hi every masked pixel becomes that value.
        # When the source is uniform (all processed pixels share one alpha value),
        # that value is clamped into [target_lo, target_hi] so the result is always
        # within the user's specified range.
        alpha_sel = alpha[proc_mask]
        if alpha_sel.size > 0:
            img_min = int(alpha_sel.min())
            img_max = int(alpha_sel.max())
            if img_max > img_min:
                alpha[proc_mask] = np.round(
                    target_lo
                    + (alpha_sel - img_min).astype(np.float32)
                    * (target_hi - target_lo)
                    / (img_max - img_min)
                ).astype(np.int32)
            else:
                # Uniform source: clamp the single value into [target_lo, target_hi].
                # This guarantees the output is always within the user's Min/Max range
                # regardless of where the uniform value falls on the 0-255 scale.
                alpha[proc_mask] = max(target_lo, min(target_hi, img_min))

        # Step 4: Binary threshold cut (hard 0/255 split, applied to ALL pixels;
        # threshold is the split point here, not a selection gate).
        if preset.binary_cut and preset.threshold > 0:
            alpha = np.where(alpha >= preset.threshold, 255, 0)

        # Step 5: Clamp (safety net).
        # When a threshold filter is active, only clamp the processed pixels so
        # that protected pixels keep their original values (which may exceed target_hi).
        if preset.threshold > 0 and not preset.binary_cut:
            alpha[proc_mask] = np.clip(alpha[proc_mask], target_lo, target_hi)
        else:
            alpha = np.clip(alpha, target_lo, target_hi)

        arr[:, :, 3] = alpha.astype(np.uint8)
        return Image.fromarray(arr.astype(np.uint8), "RGBA")
    finally:
        if _converted:
            img.close()


def apply_manual_alpha(
    img: Image.Image,
    threshold: int = 0,
    invert: bool = False,
    clamp_min: int = 0,
    clamp_max: int = 255,
    binary_cut: bool = False,
) -> Image.Image:
    """Apply alpha changes without a preset.

    Processing pipeline (in order):
      1. Build processing mask: when threshold > 0 and binary_cut is False, only
         pixels with alpha strictly below threshold are processed.  Pixels at or
         above threshold keep their original alpha (they are protected from change).
         When threshold == 0 (default) or binary_cut is True, all pixels are processed.
      2. Invert alpha (if invert is True, applied to masked pixels only).
      3. Normalize: remap [img_min, img_max] → [clamp_min, clamp_max].
         The existing alpha range of the processed pixels is stretched to exactly
         fill [clamp_min, clamp_max], so the output minimum is always clamp_min and
         the output maximum is always clamp_max when the source has varied alpha.
         When clamp_min == clamp_max every processed pixel gets that exact value.
         When the source is uniform (all processed pixels have the same alpha), that
         value is clamped into [clamp_min, clamp_max] — guaranteeing the output is
         always within the user's specified range regardless of the source value.
      4. Binary threshold cut (if binary_cut is True): pixels >= threshold → 255,
         else → 0 (applied to ALL pixels; threshold is the hard-cut split point here).
      5. Clamp to [clamp_min, clamp_max] (safety net).

    Args:
        threshold:  Protect pixels with alpha >= this value from being changed
                    (0 = process all pixels).  When binary_cut is True this becomes
                    the hard-cut split point instead.
        invert:     Invert alpha before normalizing (applied to processed pixels).
        clamp_min:  Target range minimum (0–255).  The darkest processed pixel maps
                    to this value; for uniform sources the value is clamped to this
                    floor if it falls below it.
        clamp_max:  Target range maximum (0–255).  The brightest processed pixel maps
                    to this value; for uniform sources the value is clamped to this
                    ceiling if it exceeds it.
        binary_cut: When True, apply a hard 0/255 split at the threshold after normalizing.
    """
    _converted = img.mode != "RGBA"
    if _converted:
        _orig = img
        img = img.convert("RGBA")
        _orig.close()
    try:
        try:
            arr = np.array(img, dtype=np.int32)
        except MemoryError:
            w, h = img.size
            raise MemoryError(
                f"Not enough memory to process {w}×{h} image "
                f"({w * h / 1_000_000:.1f} megapixels). Try a smaller image."
            )
        alpha = arr[:, :, 3].copy()

        # Step 1: Build processing mask.
        # When threshold > 0 with no binary_cut, protect pixels at/above threshold.
        # When threshold == 0 or binary_cut is True, process all pixels.
        if threshold > 0 and not binary_cut:
            proc_mask = alpha < threshold
        else:
            proc_mask = np.ones(alpha.shape, dtype=bool)

        # Step 2: Invert (apply to masked pixels only)
        if invert:
            alpha[proc_mask] = 255 - alpha[proc_mask]

        # Step 3: Normalize — remap [img_min, img_max] → [target_lo, target_hi].
        # The source range is stretched to exactly fill the target range so that
        # the output minimum is always target_lo and the output maximum is always
        # target_hi whenever the source has varied alpha (img_min < img_max).
        # When target_lo == target_hi every processed pixel gets that exact value.
        # When img_min == img_max (uniform source), the single value is clamped
        # into [target_lo, target_hi] so the output is always within the user's
        # specified range (above-Max → Max, below-Min → Min, in-range → unchanged).
        target_lo = min(clamp_min, clamp_max)
        target_hi = max(clamp_min, clamp_max)
        alpha_sel = alpha[proc_mask]
        if alpha_sel.size > 0:
            img_min = int(alpha_sel.min())
            img_max = int(alpha_sel.max())
            if target_lo == target_hi:
                alpha[proc_mask] = target_lo
            elif img_max > img_min:
                alpha[proc_mask] = np.round(
                    target_lo
                    + (alpha_sel - img_min).astype(np.float32)
                    * (target_hi - target_lo)
                    / (img_max - img_min)
                ).astype(np.int32)
            else:
                # Uniform source: clamp the single value into [target_lo, target_hi].
                # This guarantees the output is always within the user's Min/Max range
                # regardless of where the uniform value falls on the 0-255 scale.
                alpha[proc_mask] = max(target_lo, min(target_hi, img_min))

        # Step 4: Binary threshold cut (hard 0/255 split, applied to ALL pixels;
        # threshold is the split point here, not a selection gate).
        if binary_cut and threshold > 0:
            alpha = np.where(alpha >= threshold, 255, 0)

        # Step 5: Clamp (safety net).
        # When a threshold filter is active, only clamp the processed pixels so
        # that protected pixels keep their original values (which may exceed target_hi).
        if threshold > 0 and not binary_cut:
            alpha[proc_mask] = np.clip(alpha[proc_mask], target_lo, target_hi)
        else:
            alpha = np.clip(alpha, target_lo, target_hi)

        arr[:, :, 3] = alpha.astype(np.uint8)
        return Image.fromarray(arr.astype(np.uint8), "RGBA")
    finally:
        if _converted:
            img.close()


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

def apply_rgba_adjust(
    img: Image.Image,
    red_delta: int = 0,
    green_delta: int = 0,
    blue_delta: int = 0,
    alpha_delta: int = 0,
    red_clamp: tuple = (0, 255),
    green_clamp: tuple = (0, 255),
    blue_clamp: tuple = (0, 255),
    alpha_clamp: tuple = (0, 255),
) -> Image.Image:
    """Apply per-channel R/G/B/A deltas to a PIL image.

    Each delta shifts the channel value by the given signed integer offset.
    Clamp tuples define the allowed output range for each channel.
    Returns the modified image in RGBA mode.
    """
    _converted = img.mode != "RGBA"
    if _converted:
        _orig = img
        img = img.convert("RGBA")
        _orig.close()
    try:
        try:
            arr = np.array(img, dtype=np.int32)
        except MemoryError:
            w, h = img.size
            raise MemoryError(
                f"Not enough memory to adjust {w}×{h} image "
                f"({w * h / 1_000_000:.1f} megapixels). Try a smaller image."
            )
        arr[:, :, 0] = np.clip(arr[:, :, 0] + red_delta,   *red_clamp)
        arr[:, :, 1] = np.clip(arr[:, :, 1] + green_delta, *green_clamp)
        arr[:, :, 2] = np.clip(arr[:, :, 2] + blue_delta,  *blue_clamp)
        arr[:, :, 3] = np.clip(arr[:, :, 3] + alpha_delta, *alpha_clamp)
        return Image.fromarray(arr.astype(np.uint8), "RGBA")
    finally:
        if _converted:
            img.close()


def collect_files(
    paths: list[str],
    extensions: Optional[set] = None,
    recursive: bool = True,
) -> list[str]:
    """Expand a list of files/directories into individual file paths."""
    if extensions is None:
        extensions = SUPPORTED_READ
    result = []
    for p in paths:
        p = os.path.normpath(p)
        if os.path.isfile(p):
            if Path(p).suffix.lower() in extensions:
                result.append(p)
        elif os.path.isdir(p):
            if recursive:
                for root, _, files in os.walk(p):
                    for f in files:
                        if Path(f).suffix.lower() in extensions:
                            result.append(os.path.join(root, f))
            else:
                for f in os.listdir(p):
                    fp = os.path.join(p, f)
                    if os.path.isfile(fp) and Path(f).suffix.lower() in extensions:
                        result.append(fp)
    return result
