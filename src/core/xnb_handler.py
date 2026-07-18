"""
XNB (XNA Game Studio / MonoGame) file handler.

Supports reading Texture2D assets from XNB files produced by XNA 4.0
(format version 5) and MonoGame (format versions 5 and 7).

Supported surface formats on read:
  • Color             (RGBA8888, format ID 0)
  • Bgr565            (format ID 1)
  • Bgra5551          (format ID 2)
  • Bgra4444          (format ID 3)
  • Dxt1  / BC1       (format ID 4)
  • Dxt3  / BC2       (format ID 5)
  • Dxt5  / BC3       (format ID 6)
  • NormalizedByte2   (format ID 7)  → stored as RG
  • NormalizedByte4   (format ID 8)  → stored as RGBA
  • Rgba1010102       (format ID 9)
  • Rg32              (format ID 10)
  • Rgba64            (format ID 11) → down-sampled to 8-bit per channel
  • Alpha8            (format ID 12)
  • Single            (format ID 13) → float32 → greyscale
  • Vector2           (format ID 14) → RG float32
  • Vector4           (format ID 15) → RGBA float32
  • HalfSingle        (format ID 16) → greyscale
  • HalfVector2       (format ID 17) → RG
  • HalfVector4       (format ID 18) → RGBA
  • HdrBlendable      (format ID 19) → treated as HalfVector4

On write, Color (RGBA8888) is used regardless of the source mode.

References:
  https://github.com/nicowillis/xnb_parser (format overview)
  https://github.com/lidgren/lidgren-network-gen3 (MonoGame XNB spec)
"""
from __future__ import annotations

import io
import struct
import zlib
import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from .alpha_processor import _decompress_dds_blocks

logger = logging.getLogger(__name__)

# XNB magic bytes
_XNB_MAGIC = b"XNB"

# Platform identifiers
_PLATFORMS = {ord("w"): "Windows", ord("m"): "Windows Phone", ord("x"): "Xbox 360"}

# XNB flag bits
_FLAG_HIDEF      = 0x01
_FLAG_COMPRESSED = 0x80

# Surface format IDs (XNA 4.0 / MonoGame)
_FMT_COLOR          = 0
_FMT_BGR565         = 1
_FMT_BGRA5551       = 2
_FMT_BGRA4444       = 3
_FMT_DXT1           = 4
_FMT_DXT3           = 5
_FMT_DXT5           = 6
_FMT_NORMALIZEDBYTE2 = 7
_FMT_NORMALIZEDBYTE4 = 8
_FMT_RGBA1010102    = 9
_FMT_RG32           = 10
_FMT_RGBA64         = 11
_FMT_ALPHA8         = 12
_FMT_SINGLE         = 13
_FMT_VECTOR2        = 14
_FMT_VECTOR4        = 15
_FMT_HALFSINGLE     = 16
_FMT_HALFVECTOR2    = 17
_FMT_HALFVECTOR4    = 18
_FMT_HDRBLENDABLE   = 18   # same value as HalfVector4 in most implementations


def _read_7bit_int(buf: bytes, pos: int) -> tuple[int, int]:
    """Read a BinaryWriter 7-bit encoded integer.  Returns (value, new_pos)."""
    result = 0
    shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result, pos


class XnbError(Exception):
    """Raised when an XNB file cannot be parsed."""


def load_xnb(path: str) -> Image.Image:
    """
    Load a Texture2D asset from an XNB file.

    :param path: Absolute path to the .xnb file.
    :returns:    PIL Image in RGBA mode.
    :raises:     XnbError on parse failure; ValueError if the asset is not
                 a Texture2D.
    """
    with open(path, "rb") as f:
        raw = f.read()

    if len(raw) < 10 or raw[:3] != _XNB_MAGIC:
        raise XnbError(f"Not a valid XNB file: {path}")

    platform  = raw[3]
    version   = raw[4]
    flags     = raw[5]
    _total_sz = int.from_bytes(raw[6:10], "little")

    if version not in (4, 5):
        raise XnbError(
            f"Unsupported XNB version {version} (supported: 4, 5) in {path}"
        )

    pos = 10
    payload: bytes

    if flags & _FLAG_COMPRESSED:
        # 4-byte decompressed size follows the header
        decomp_size = int.from_bytes(raw[pos:pos + 4], "little")
        pos += 4
        compressed_data = raw[pos:]
        try:
            # XNA uses raw DEFLATE (no zlib wrapper)
            payload = zlib.decompress(compressed_data, -15)
        except zlib.error:
            # Try standard zlib wrapper
            try:
                payload = zlib.decompress(compressed_data)
            except zlib.error as exc:
                raise XnbError(f"Failed to decompress XNB payload: {exc}") from exc
    else:
        payload = raw[pos:]

    buf = payload
    pos = 0

    # ── Type reader array ──────────────────────────────────────────────
    type_count, pos = _read_7bit_int(buf, pos)
    type_names: list[str] = []
    for _ in range(type_count):
        name_len, pos = _read_7bit_int(buf, pos)
        name = buf[pos:pos + name_len].decode("utf-8", errors="replace")
        pos += name_len
        _reader_version = int.from_bytes(buf[pos:pos + 4], "little")
        pos += 4
        type_names.append(name)

    # ── Shared resource count ──────────────────────────────────────────
    shared_count, pos = _read_7bit_int(buf, pos)

    # ── Primary asset ──────────────────────────────────────────────────
    # Type index (7-bit encoded; 0 means null)
    type_idx, pos = _read_7bit_int(buf, pos)
    if type_idx == 0:
        raise XnbError("XNB asset is null")

    # type_names[type_idx - 1] should contain "Texture2D"
    reader_name = type_names[type_idx - 1] if type_names else ""
    if "Texture2D" not in reader_name:
        raise ValueError(
            f"XNB asset is not a Texture2D (reader: {reader_name!r}). "
            "Only Texture2D assets are supported."
        )

    # ── Texture2D data ─────────────────────────────────────────────────
    surface_fmt = int.from_bytes(buf[pos:pos + 4], "little")
    pos += 4
    tex_width  = int.from_bytes(buf[pos:pos + 4], "little")
    pos += 4
    tex_height = int.from_bytes(buf[pos:pos + 4], "little")
    pos += 4
    mip_count  = int.from_bytes(buf[pos:pos + 4], "little")
    pos += 4

    # Read only the first (largest) mip level
    mip_size = int.from_bytes(buf[pos:pos + 4], "little")
    pos += 4
    mip_data = buf[pos:pos + mip_size]

    return _decode_texture2d(mip_data, tex_width, tex_height, surface_fmt)


def _decode_texture2d(
    data: bytes, width: int, height: int, surface_fmt: int
) -> Image.Image:
    """Decode raw Texture2D pixel data into a PIL RGBA image."""

    if surface_fmt == _FMT_COLOR:
        # RGBA8888
        arr = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 4)
        # XNA stores BGRA not RGBA – swap R and B
        return Image.fromarray(arr[:, :, [2, 1, 0, 3]], "RGBA")

    if surface_fmt == _FMT_BGR565:
        arr = np.frombuffer(data, dtype="<u2").reshape(height, width)
        r = ((arr >> 11) & 0x1F).astype(np.uint8) * 8
        g = ((arr >> 5) & 0x3F).astype(np.uint8) * 4
        b = (arr & 0x1F).astype(np.uint8) * 8
        rgba = np.stack([r, g, b, np.full_like(r, 255)], axis=-1)
        return Image.fromarray(rgba, "RGBA")

    if surface_fmt == _FMT_BGRA5551:
        arr = np.frombuffer(data, dtype="<u2").reshape(height, width)
        b = ((arr >> 11) & 0x1F).astype(np.uint8) * 8
        g = ((arr >> 6) & 0x1F).astype(np.uint8) * 8
        r = ((arr >> 1) & 0x1F).astype(np.uint8) * 8
        a = (arr & 0x1).astype(np.uint8) * 255
        rgba = np.stack([r, g, b, a], axis=-1)
        return Image.fromarray(rgba, "RGBA")

    if surface_fmt == _FMT_BGRA4444:
        arr = np.frombuffer(data, dtype="<u2").reshape(height, width)
        b = ((arr >> 12) & 0xF).astype(np.uint8) * 17
        g = ((arr >> 8) & 0xF).astype(np.uint8) * 17
        r = ((arr >> 4) & 0xF).astype(np.uint8) * 17
        a = (arr & 0xF).astype(np.uint8) * 17
        rgba = np.stack([r, g, b, a], axis=-1)
        return Image.fromarray(rgba, "RGBA")

    if surface_fmt == _FMT_DXT1:
        return _decompress_dds_blocks(data, width, height, "DXT1")

    if surface_fmt == _FMT_DXT3:
        return _decompress_dds_blocks(data, width, height, "DXT3")

    if surface_fmt == _FMT_DXT5:
        return _decompress_dds_blocks(data, width, height, "DXT5")

    if surface_fmt == _FMT_ALPHA8:
        arr = np.frombuffer(data, dtype=np.uint8).reshape(height, width)
        rgba = np.stack([arr, arr, arr, arr], axis=-1)
        return Image.fromarray(rgba, "RGBA")

    if surface_fmt == _FMT_SINGLE:
        arr = np.frombuffer(data, dtype="<f4").reshape(height, width)
        grey = np.clip(arr * 255, 0, 255).astype(np.uint8)
        return Image.fromarray(grey, "L").convert("RGBA")

    if surface_fmt == _FMT_VECTOR2:
        arr = np.frombuffer(data, dtype="<f4").reshape(height, width, 2)
        r = np.clip(arr[:, :, 0] * 255, 0, 255).astype(np.uint8)
        g = np.clip(arr[:, :, 1] * 255, 0, 255).astype(np.uint8)
        blank = np.zeros_like(r)
        full = np.full_like(r, 255)
        return Image.fromarray(np.stack([r, g, blank, full], axis=-1), "RGBA")

    if surface_fmt in (_FMT_VECTOR4, _FMT_HDRBLENDABLE):
        arr = np.frombuffer(data, dtype="<f4").reshape(height, width, 4)
        rgba = np.clip(arr * 255, 0, 255).astype(np.uint8)
        return Image.fromarray(rgba, "RGBA")

    if surface_fmt == _FMT_RGBA64:
        arr = np.frombuffer(data, dtype="<u2").reshape(height, width, 4)
        rgba = (arr >> 8).astype(np.uint8)
        return Image.fromarray(rgba, "RGBA")

    if surface_fmt == _FMT_RGBA1010102:
        arr = np.frombuffer(data, dtype="<u4").reshape(height, width)
        r = ((arr >> 22) & 0xFF).astype(np.uint8)
        g = ((arr >> 12) & 0xFF).astype(np.uint8)
        b = ((arr >> 2) & 0xFF).astype(np.uint8)
        a = ((arr & 0x3) * 85).astype(np.uint8)
        return Image.fromarray(np.stack([r, g, b, a], axis=-1), "RGBA")

    if surface_fmt == _FMT_NORMALIZEDBYTE2:
        arr = np.frombuffer(data, dtype=np.int8).reshape(height, width, 2)
        r = np.clip(arr[:, :, 0].astype(np.int16) + 128, 0, 255).astype(np.uint8)
        g = np.clip(arr[:, :, 1].astype(np.int16) + 128, 0, 255).astype(np.uint8)
        blank = np.zeros_like(r)
        full = np.full_like(r, 255)
        return Image.fromarray(np.stack([r, g, blank, full], axis=-1), "RGBA")

    if surface_fmt == _FMT_NORMALIZEDBYTE4:
        arr = np.frombuffer(data, dtype=np.int8).reshape(height, width, 4)
        rgba = np.clip(arr.astype(np.int16) + 128, 0, 255).astype(np.uint8)
        return Image.fromarray(rgba, "RGBA")

    # RG32, HalfSingle, HalfVector2, HalfVector4 – limited fallbacks
    if surface_fmt == _FMT_RG32:
        arr = np.frombuffer(data, dtype="<u2").reshape(height, width, 2)
        r = (arr[:, :, 0] >> 8).astype(np.uint8)
        g = (arr[:, :, 1] >> 8).astype(np.uint8)
        blank = np.zeros_like(r)
        full = np.full_like(r, 255)
        return Image.fromarray(np.stack([r, g, blank, full], axis=-1), "RGBA")

    if surface_fmt == _FMT_HALFSINGLE:
        arr = np.frombuffer(data, dtype="<f2").reshape(height, width)
        grey = np.clip(arr * 255, 0, 255).astype(np.uint8)
        return Image.fromarray(grey, "L").convert("RGBA")

    if surface_fmt == _FMT_HALFVECTOR2:
        arr = np.frombuffer(data, dtype="<f2").reshape(height, width, 2)
        r = np.clip(arr[:, :, 0] * 255, 0, 255).astype(np.uint8)
        g = np.clip(arr[:, :, 1] * 255, 0, 255).astype(np.uint8)
        blank = np.zeros_like(r)
        full = np.full_like(r, 255)
        return Image.fromarray(np.stack([r, g, blank, full], axis=-1), "RGBA")

    if surface_fmt == _FMT_HALFVECTOR4:
        arr = np.frombuffer(data, dtype="<f2").reshape(height, width, 4)
        rgba = np.clip(arr * 255, 0, 255).astype(np.uint8)
        return Image.fromarray(rgba, "RGBA")

    raise ValueError(
        f"Unsupported XNB Texture2D surface format ID {surface_fmt}. "
        "Convert the asset with the XNA content pipeline or MonoGame Pipeline Tool first."
    )


def save_xnb(img: Image.Image, path: str) -> None:
    """
    Save a PIL Image as an uncompressed XNB Texture2D file (XNA 4.0, Color format).

    The output is always uncompressed, Color (RGBA8888) surface format,
    a single mip level, targeting Windows ('w').

    :param img:  PIL Image (any mode; converted to RGBA internally).
    :param path: Destination .xnb file path.
    """
    rgba = img.convert("RGBA")
    try:
        width, height = rgba.size
        arr = np.array(rgba, dtype=np.uint8)
        # XNA Color = BGRA order on disk
        bgra = arr[:, :, [2, 1, 0, 3]]
        pixel_data = bgra.tobytes()
    finally:
        if rgba is not img:
            rgba.close()

    buf = io.BytesIO()

    def _write7bit(n: int) -> None:
        while n >= 0x80:
            buf.write(bytes([n | 0x80]))
            n >>= 7
        buf.write(bytes([n]))

    # ── Type reader array: one entry for Texture2DReader ──────────────
    reader_name = (
        "Microsoft.Xna.Framework.Content.Texture2DReader, "
        "Microsoft.Xna.Framework.Graphics, Version=4.0.0.0, "
        "Culture=neutral, PublicKeyToken=842cf8be1de50553"
    )
    name_bytes = reader_name.encode("utf-8")

    _write7bit(1)                              # type reader count = 1
    _write7bit(len(name_bytes))
    buf.write(name_bytes)
    buf.write(struct.pack("<i", 0))            # reader version = 0

    _write7bit(0)                              # shared resource count = 0
    _write7bit(1)                              # primary asset type index = 1

    # ── Texture2D data ─────────────────────────────────────────────────
    buf.write(struct.pack("<i", _FMT_COLOR))   # surface format = Color
    buf.write(struct.pack("<i", width))
    buf.write(struct.pack("<i", height))
    buf.write(struct.pack("<i", 1))            # mip count = 1
    buf.write(struct.pack("<i", len(pixel_data)))
    buf.write(pixel_data)

    payload = buf.getvalue()

    # ── XNB file header ───────────────────────────────────────────────
    total_size = 10 + len(payload)  # 10-byte header + payload
    header = (
        b"XNB"
        + b"w"                            # platform: Windows
        + bytes([5])                      # version: 5 (XNA 4.0)
        + bytes([0])                      # flags: not compressed, not HiDef
        + struct.pack("<I", total_size)   # total file size
    )

    with open(path, "wb") as f:
        f.write(header)
        f.write(payload)
