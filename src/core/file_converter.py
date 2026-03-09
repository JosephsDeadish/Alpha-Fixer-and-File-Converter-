"""
File converter – converts between image formats.

Supported formats: PNG, JPEG, BMP, TIFF, WEBP, TGA, ICO, GIF, DDS.
"""
import os
import logging
from pathlib import Path
from typing import Optional

from PIL import Image

from .alpha_processor import load_image, _save_dds, _has_wand, SUPPORTED_READ

logger = logging.getLogger(__name__)

SUPPORTED_OUTPUT_FORMATS = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "BMP": ".bmp",
    "TIFF": ".tiff",
    "WEBP": ".webp",
    "TGA": ".tga",
    "ICO": ".ico",
    "GIF": ".gif",
    "DDS": ".dds",
}

# Display list for UI combos (name → extension)
OUTPUT_FORMAT_LIST = sorted(SUPPORTED_OUTPUT_FORMATS.items())


def convert_file(
    input_path: str,
    output_path: str,
    target_format: str,
    quality: int = 90,
    resize: Optional[tuple[int, int]] = None,
) -> str:
    """
    Convert a single image file.

    :param input_path:   Source file path.
    :param output_path:  Destination file path (with correct extension).
    :param target_format: One of the keys in SUPPORTED_OUTPUT_FORMATS, e.g. "PNG".
    :param quality:      JPEG/WEBP quality (1-100).
    :param resize:       Optional (width, height) tuple.
    :returns: output_path on success.
    :raises:  Exception on failure.
    """
    img = load_image(input_path)

    if resize:
        img = img.resize(resize, Image.LANCZOS)

    ext = Path(output_path).suffix.lower()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if ext == ".dds":
        _save_dds(img, output_path)
        return output_path

    if ext in (".jpg", ".jpeg"):
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(output_path, quality=quality)
        return output_path

    if ext == ".gif":
        img = img.convert("RGBA")
        img.save(output_path)
        return output_path

    if ext == ".ico":
        img.save(output_path, sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
        return output_path

    # Default: save as-is (PNG, BMP, TIFF, WEBP, TGA)
    if ext in (".bmp",):
        img = img.convert("RGB")
    img.save(output_path)
    return output_path


def build_output_path(
    input_path: str,
    target_ext: str,
    output_dir: Optional[str] = None,
    input_root: Optional[str] = None,
) -> str:
    """
    Derive an output file path for a converted file.

    If output_dir is given, the file is placed inside it (mirroring subdirectory
    structure when input_root is provided).  Otherwise the file is placed next
    to the original.
    """
    p = Path(input_path)
    new_name = p.stem + target_ext

    if output_dir:
        if input_root:
            try:
                rel = p.parent.relative_to(input_root)
                dest_dir = Path(output_dir) / rel
            except ValueError:
                dest_dir = Path(output_dir)
        else:
            dest_dir = Path(output_dir)
        return str(dest_dir / new_name)

    return str(p.parent / new_name)
