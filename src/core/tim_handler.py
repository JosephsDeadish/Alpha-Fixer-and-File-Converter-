"""
PlayStation 1 TIM (Texture Image) file reader.

TIM is the native texture format used by PlayStation 1 games.

File structure:
  [4-byte magic 0x00000010]
  [4-byte flags: bits 0-2 = pixel mode, bit 3 = has CLUT]
  [optional CLUT block]
  [image data block]

Pixel modes:
  0 = 4-bit indexed  (CLUT required)
  1 = 8-bit indexed  (CLUT required)
  2 = 16-bit direct  (ABGR1555)
  3 = 24-bit direct  (BGR888, no alpha)
  4 = Mixed (unsupported)
"""
from __future__ import annotations

import logging
import struct

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

_TIM_MAGIC = 0x00000010
_TIM_VERSION = 0x00000000

# Pixel mode constants
_PMODE_4BIT  = 0
_PMODE_8BIT  = 1
_PMODE_16BIT = 2
_PMODE_24BIT = 3


class TimError(Exception):
    """Raised when a TIM file cannot be parsed."""


def load_tim(path: str) -> Image.Image:
    """
    Load a PlayStation 1 TIM texture file.

    :param path: Path to the .tim file.
    :returns:    PIL Image in RGBA mode.
    :raises:     TimError on parse failure.
    """
    with open(path, "rb") as f:
        data = f.read()

    if len(data) < 8:
        raise TimError(f"File too small to be a TIM: {path}")

    magic   = int.from_bytes(data[0:4], "little")
    flags   = int.from_bytes(data[4:8], "little")

    if magic != _TIM_MAGIC:
        raise TimError(
            f"Not a valid TIM file (magic=0x{magic:08X}, expected 0x{_TIM_MAGIC:08X})"
        )

    pmode   = flags & 0x07
    has_clut = bool(flags & 0x08)

    pos = 8
    clut_colors: list[tuple[int, int, int, int]] = []

    # ── CLUT block ──────────────────────────────────────────────────────
    if has_clut:
        if pos + 12 > len(data):
            raise TimError("TIM file truncated in CLUT header")
        clut_block_size = int.from_bytes(data[pos:pos + 4], "little")
        clut_x = int.from_bytes(data[pos + 4:pos + 6], "little")
        clut_y = int.from_bytes(data[pos + 6:pos + 8], "little")
        clut_w = int.from_bytes(data[pos + 8:pos + 10], "little")
        clut_h = int.from_bytes(data[pos + 10:pos + 12], "little")
        pos += 12

        n_entries = clut_w * clut_h
        if pos + n_entries * 2 > len(data):
            raise TimError("TIM file truncated in CLUT data")

        for i in range(n_entries):
            word = int.from_bytes(data[pos + i * 2:pos + i * 2 + 2], "little")
            r = (word & 0x001F) * 8
            g = ((word >> 5) & 0x1F) * 8
            b = ((word >> 10) & 0x1F) * 8
            # STP flag: bit 15; if all RGB=0 and STP=0 → transparent
            if word == 0:
                a = 0
            elif (word >> 15) & 1:
                a = 128  # semi-transparent
            else:
                a = 255
            clut_colors.append((r, g, b, a))

        pos += n_entries * 2

    # ── Image data block ────────────────────────────────────────────────
    if pos + 12 > len(data):
        raise TimError("TIM file truncated in image data header")

    img_block_size = int.from_bytes(data[pos:pos + 4], "little")
    img_x = int.from_bytes(data[pos + 4:pos + 6], "little")
    img_y = int.from_bytes(data[pos + 6:pos + 8], "little")
    # Width is in 16-bit words; actual pixel count depends on pixel mode.
    img_w_words = int.from_bytes(data[pos + 8:pos + 10], "little")
    img_h = int.from_bytes(data[pos + 10:pos + 12], "little")
    pos += 12

    pixel_data_size = img_block_size - 12
    if pos + pixel_data_size > len(data):
        raise TimError("TIM file truncated in image pixel data")
    pixel_bytes = data[pos:pos + pixel_data_size]

    # ── Decode pixel data ───────────────────────────────────────────────
    if pmode == _PMODE_4BIT:
        img_w = img_w_words * 4  # 4 pixels per 16-bit word
        return _decode_4bit_clut(pixel_bytes, img_w, img_h, clut_colors)

    if pmode == _PMODE_8BIT:
        img_w = img_w_words * 2  # 2 pixels per 16-bit word
        return _decode_8bit_clut(pixel_bytes, img_w, img_h, clut_colors)

    if pmode == _PMODE_16BIT:
        img_w = img_w_words  # 1 pixel per 16-bit word
        return _decode_16bit(pixel_bytes, img_w, img_h)

    if pmode == _PMODE_24BIT:
        # 24-bit: 2 words per 3 bytes of RGB → img_w_words*2/3 pixels
        # Stored as packed BGR triplets
        img_w = (img_w_words * 2) // 3
        return _decode_24bit(pixel_bytes, img_w, img_h)

    raise TimError(f"Unsupported TIM pixel mode: {pmode}")


def _decode_4bit_clut(
    data: bytes, width: int, height: int,
    clut: list[tuple[int, int, int, int]],
) -> Image.Image:
    """Decode 4-bit indexed TIM pixel data using a CLUT."""
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    byte_idx = 0
    for y in range(height):
        for x in range(0, width, 2):
            if byte_idx >= len(data):
                break
            b = data[byte_idx]
            byte_idx += 1
            lo = b & 0x0F
            hi = (b >> 4) & 0x0F
            if x < width:
                c = clut[lo] if lo < len(clut) else (0, 0, 0, 255)
                rgba[y, x] = c
            if x + 1 < width:
                c = clut[hi] if hi < len(clut) else (0, 0, 0, 255)
                rgba[y, x + 1] = c
    return Image.fromarray(rgba, "RGBA")


def _decode_8bit_clut(
    data: bytes, width: int, height: int,
    clut: list[tuple[int, int, int, int]],
) -> Image.Image:
    """Decode 8-bit indexed TIM pixel data using a CLUT."""
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    byte_idx = 0
    for y in range(height):
        for x in range(width):
            if byte_idx >= len(data):
                break
            idx = data[byte_idx]
            byte_idx += 1
            c = clut[idx] if idx < len(clut) else (0, 0, 0, 255)
            rgba[y, x] = c
    return Image.fromarray(rgba, "RGBA")


def _decode_16bit(data: bytes, width: int, height: int) -> Image.Image:
    """Decode 16-bit ABGR1555 TIM pixel data."""
    arr = np.frombuffer(data, dtype="<u2").reshape(height, width)
    r = ((arr & 0x001F) * 8).astype(np.uint8)
    g = (((arr >> 5) & 0x1F) * 8).astype(np.uint8)
    b = (((arr >> 10) & 0x1F) * 8).astype(np.uint8)
    # Transparent where all-zero (black pixels with STP=0)
    a = np.where(arr == 0, np.uint8(0), np.uint8(255))
    # Semi-transparent where STP flag set
    stp = ((arr >> 15) & 1).astype(bool)
    a = np.where(stp & (arr != 0), np.uint8(128), a).astype(np.uint8)
    return Image.fromarray(np.stack([r, g, b, a], axis=-1), "RGBA")


def _decode_24bit(data: bytes, width: int, height: int) -> Image.Image:
    """Decode 24-bit BGR TIM pixel data."""
    n_pixels = width * height
    needed = n_pixels * 3
    if len(data) < needed:
        # Pad with zeros if the data is shorter than expected
        data = data + bytes(needed - len(data))
    arr = np.frombuffer(data[:needed], dtype=np.uint8).reshape(height, width, 3)
    # BGR → RGB + full alpha
    rgba = np.stack([arr[:, :, 2], arr[:, :, 1], arr[:, :, 0],
                     np.full((height, width), 255, dtype=np.uint8)], axis=-1)
    return Image.fromarray(rgba, "RGBA")
