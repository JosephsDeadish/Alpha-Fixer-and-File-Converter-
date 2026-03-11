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
            return Image.open(io.BytesIO(blob)).convert("RGBA")
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
        img = Image.fromarray(arr[:, :, [2, 1, 0]], "RGB").convert("RGBA")
    return img


def _save_dds(img: Image.Image, path: str):
    """Save a PIL Image as DDS (BGRA uncompressed) via Wand, or fall back to raw."""
    if _has_wand():
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
        except Exception as exc:
            logger.warning("Wand failed to save DDS %s: %s", path, exc)
    _save_dds_raw(img, path)


def _save_dds_raw(img: Image.Image, path: str):
    """Write a minimal uncompressed BGRA DDS file."""
    img_rgba = img.convert("RGBA")
    w, h = img_rgba.size
    arr = np.array(img_rgba, dtype=np.uint8)
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
        img = img.convert("RGBA")
    return img


def save_image(img: Image.Image, path: str, original_ext: str):
    ext = original_ext.lower()
    if ext == ".dds":
        _save_dds(img, path)
        return
    if ext in (".jpg", ".jpeg", ".bmp"):
        img = img.convert("RGB")
    img.save(path)


def apply_alpha_preset(img: Image.Image, preset: AlphaPreset) -> Image.Image:
    """Apply an AlphaPreset to a PIL RGBA image and return the result.

    Processing pipeline (in order):
      1. Invert alpha (if preset.invert is True)
      2. Set fixed alpha value (if preset.alpha_value is not None), respecting threshold
      3. Binary threshold cut (if preset.binary_cut is True): pixels >= threshold → 255, else → 0
      4. Clamp to [clamp_min, clamp_max]
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    arr = np.array(img, dtype=np.int32)
    alpha = arr[:, :, 3].copy()

    # Step 1: Invert
    if preset.invert:
        alpha = 255 - alpha

    # Step 2: Set fixed value (only if alpha_value is specified)
    if preset.alpha_value is not None:
        if preset.threshold > 0:
            mask = alpha < preset.threshold
            alpha[mask] = preset.alpha_value
        else:
            alpha[:] = preset.alpha_value

    # Step 3: Binary threshold cut (hard 0/255 split)
    if preset.binary_cut and preset.threshold > 0:
        alpha = np.where(alpha >= preset.threshold, 255, 0)

    # Step 4: Clamp
    arr[:, :, 3] = np.clip(alpha, preset.clamp_min, preset.clamp_max).astype(np.uint8)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def apply_manual_alpha(
    img: Image.Image,
    value: Optional[int],
    threshold: int = 0,
    invert: bool = False,
    clamp_min: int = 0,
    clamp_max: int = 255,
    binary_cut: bool = False,
) -> Image.Image:
    """Apply alpha changes without a preset.

    Args:
        value: Target alpha value (0-255) to set on affected pixels.
               Pass None to skip the set step and only apply clamping.
        binary_cut: When True, apply a hard 0/255 split at the threshold
                    (pixels >= threshold → 255, else → 0).
    """
    pseudo = AlphaPreset(
        name="_manual",
        alpha_value=value,
        threshold=threshold,
        invert=invert,
        description="",
        clamp_min=clamp_min,
        clamp_max=clamp_max,
        binary_cut=binary_cut,
    )
    return apply_alpha_preset(img, pseudo)


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
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    arr = np.array(img, dtype=np.int32)
    arr[:, :, 0] = np.clip(arr[:, :, 0] + red_delta,   *red_clamp)
    arr[:, :, 1] = np.clip(arr[:, :, 1] + green_delta, *green_clamp)
    arr[:, :, 2] = np.clip(arr[:, :, 2] + blue_delta,  *blue_clamp)
    arr[:, :, 3] = np.clip(arr[:, :, 3] + alpha_delta, *alpha_clamp)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


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
