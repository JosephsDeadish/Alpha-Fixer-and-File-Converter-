"""
File converter – converts between image formats.

Supported formats: PNG, JPEG, BMP, TIFF, WEBP, TGA, ICO, GIF, DDS,
                   PPM, PCX, AVIF, QOI.
"""
import os
import logging
from pathlib import Path
from typing import Optional

from PIL import Image

from .alpha_processor import _save_dds, _load_dds, SUPPORTED_READ

logger = logging.getLogger(__name__)

SUPPORTED_OUTPUT_FORMATS = {
    "AVIF": ".avif",
    "BMP": ".bmp",
    "DDS": ".dds",
    "GIF": ".gif",
    "ICO": ".ico",
    "JPEG": ".jpg",
    "PCX": ".pcx",
    "PNG": ".png",
    "PPM": ".ppm",
    "QOI": ".qoi",
    "TGA": ".tga",
    "TIFF": ".tiff",
    "WEBP": ".webp",
}

# Display list for UI combos (name → extension), alphabetical
OUTPUT_FORMAT_LIST = sorted(SUPPORTED_OUTPUT_FORMATS.items())

# Human-readable descriptions for each output format, shown as combo tooltips
FORMAT_DESCRIPTIONS = {
    "AVIF": (
        "AV1 Image File Format — modern, high-quality lossy/lossless compression.\n"
        "Excellent for photos and game textures. Requires Pillow ≥ 9.4 + libavif.\n"
        "Supports alpha channel. Good browser support (Chrome, Firefox, Safari)."
    ),
    "BMP": (
        "Windows Bitmap — uncompressed raster format.\n"
        "Large file size but lossless and universally supported.\n"
        "No alpha channel support. Best for simple compatibility."
    ),
    "DDS": (
        "DirectDraw Surface — GPU-native texture format.\n"
        "Used by DirectX games and engines (Unreal, Unity, etc.).\n"
        "Supports DXT/BC compressed formats. Required for many game modding workflows."
    ),
    "GIF": (
        "Graphics Interchange Format — 256-colour indexed format with animation.\n"
        "Limited palette makes it unsuitable for photos or detailed textures.\n"
        "Supports 1-bit transparency only. Best for simple icons or animations."
    ),
    "ICO": (
        "Windows Icon format — multi-size icon bundle.\n"
        "Used for application icons, desktop shortcuts, and taskbar icons.\n"
        "Supports multiple resolutions in a single file (16×16 to 256×256)."
    ),
    "JPEG": (
        "Joint Photographic Experts Group — lossy compression for photos.\n"
        "No alpha channel support; transparent pixels are composited onto white.\n"
        "Quality 85–95 gives a good size/quality balance for photos."
    ),
    "PCX": (
        "PC Paintbrush format — old lossless format from the DOS era.\n"
        "Limited support in modern software. Use PNG or BMP instead where possible.\n"
        "Still encountered in some legacy game assets and old CAD workflows."
    ),
    "PNG": (
        "Portable Network Graphics — lossless compression with full alpha channel.\n"
        "The best choice for game textures, UI assets, and any image with transparency.\n"
        "Larger than JPEG for photos but preserves every pixel perfectly."
    ),
    "PPM": (
        "Portable Pixmap — simple, uncompressed text or binary RGB format.\n"
        "Very large files with no compression. Supported by most graphics tools.\n"
        "No alpha channel. Mostly used in scientific and batch-pipeline workflows."
    ),
    "QOI": (
        "Quite OK Image Format — fast lossless compression with alpha support.\n"
        "Encodes and decodes very quickly compared to PNG. Game-engine friendly.\n"
        "Smaller than BMP, similar quality to PNG. Growing tool support."
    ),
    "TGA": (
        "Truevision TGA — lossless format with optional alpha channel.\n"
        "Widely used in 3D modelling and older game engines (Source, Quake, etc.).\n"
        "Supports 32-bit RGBA. Simple format with broad tool support."
    ),
    "TIFF": (
        "Tagged Image File Format — flexible lossless/compressed format.\n"
        "Used in professional print, photography, and scientific imaging.\n"
        "Supports alpha, multiple layers, and various colour depths."
    ),
    "WEBP": (
        "Google WebP — modern lossy/lossless format for the web.\n"
        "Supports alpha channel. Smaller than PNG at similar quality.\n"
        "Best for web assets, UI images, and web-delivered game textures."
    ),
}

# Formats whose save() accepts a quality parameter
_QUALITY_FORMATS = {".jpg", ".jpeg", ".webp", ".avif"}


def _open_image(path: str) -> Image.Image:
    """Open an image preserving its native mode (DDS handled specially)."""
    ext = Path(path).suffix.lower()
    if ext == ".dds":
        return _load_dds(path)
    img = Image.open(path)
    try:
        img.load()  # force decode so the file handle can be closed
    except MemoryError:
        w, h = img.size
        raise MemoryError(
            f"Not enough memory to open {w}×{h} image "
            f"({w * h / 1_000_000:.1f} megapixels). Try a smaller file."
        )
    return img


def _flatten_alpha(img: Image.Image, bg_rgb: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """
    Composite *img* onto a solid white (or *bg_rgb*) background, removing any
    alpha channel.  Returns an RGB (or L) image safe to save as JPEG/BMP/PPM.
    """
    if img.mode == "RGBA":
        base = Image.new("RGB", img.size, bg_rgb)
        base.paste(img, mask=img.split()[3])
        return base
    if img.mode == "LA":
        base = Image.new("L", img.size, bg_rgb[0])
        base.paste(img, mask=img.split()[1])
        return base
    if img.mode in ("PA", "P"):
        # Palette images may have embedded transparency; go via RGBA
        rgba = img.convert("RGBA")
        base = Image.new("RGB", img.size, bg_rgb)
        base.paste(rgba, mask=rgba.split()[3])
        return base
    if img.mode not in ("RGB", "L", "1"):
        return img.convert("RGB")
    return img


def _ensure_rgba(img: Image.Image) -> Image.Image:
    """Return the image in RGBA mode (used for DDS/ICO targets)."""
    if img.mode == "RGBA":
        return img
    return img.convert("RGBA")


def convert_file(
    input_path: str,
    output_path: str,
    target_format: str,
    quality: int = 90,
    resize: Optional[tuple[int, int]] = None,
    keep_metadata: bool = False,
) -> str:
    """
    Convert a single image file.

    The image's native colour mode is preserved where the target format
    supports it.  For targets that cannot store an alpha channel (JPEG, BMP,
    PPM, PCX, GIF) the alpha is properly composited onto a white background
    rather than discarded.

    :param input_path:     Source file path.
    :param output_path:    Destination file path (with correct extension).
    :param target_format:  One of the keys in SUPPORTED_OUTPUT_FORMATS, e.g. "PNG".
    :param quality:        JPEG/WEBP/AVIF quality (1-100).
    :param resize:         Optional (width, height) tuple.
    :param keep_metadata:  When True, copy EXIF/ICC/DPI metadata to the output.
    :returns: output_path on success.
    :raises:  Exception on failure.
    """
    src_img = _open_image(input_path)
    img = src_img

    if resize:
        try:
            w, h = int(resize[0]), int(resize[1])
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid resize value: {resize!r}. "
                "Both width and height must be integers."
            )
        if w < 1 or h < 1:
            raise ValueError(
                f"Invalid resize dimensions: {w}×{h}. "
                "Both width and height must be at least 1 pixel."
            )
        if w > 65535 or h > 65535:
            raise ValueError(
                f"Resize dimensions too large: {w}×{h}. "
                "Maximum supported size is 65535×65535 pixels."
            )
        try:
            img = img.resize((w, h), Image.LANCZOS)
        except MemoryError:
            raise MemoryError(
                f"Not enough memory to resize image to {w}×{h}. "
                "Try a smaller target size."
            )

    ext = Path(output_path).suffix.lower()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Helper: inject metadata kwargs into save calls
    def _meta_kwargs(fmt_ext: str) -> dict:
        if not keep_metadata:
            return {}
        kw: dict = {}
        try:
            if fmt_ext in (".jpg", ".jpeg"):
                for k in ("exif", "icc_profile", "dpi"):
                    if k in src_img.info:
                        kw[k] = src_img.info[k]
            elif fmt_ext in (".webp",):
                for k in ("exif", "icc_profile"):
                    if k in src_img.info:
                        kw[k] = src_img.info[k]
            elif fmt_ext in (".png",):
                for k in ("exif", "icc_profile", "dpi"):
                    if k in src_img.info:
                        kw[k] = src_img.info[k]
            elif fmt_ext in (".tiff", ".tif"):
                for k in ("exif", "icc_profile", "dpi"):
                    if k in src_img.info:
                        kw[k] = src_img.info[k]
            elif fmt_ext in (".avif",):
                if "exif" in src_img.info:
                    kw["exif"] = src_img.info["exif"]
        except Exception:
            pass
        return kw

    # --- DDS (custom writer, needs RGBA) ---
    if ext == ".dds":
        _save_dds(_ensure_rgba(img), output_path)
        return output_path

    # --- JPEG (no alpha, RGB or L only) ---
    if ext in (".jpg", ".jpeg"):
        img = _flatten_alpha(img)
        img.save(output_path, quality=quality, **_meta_kwargs(ext))
        return output_path

    # --- BMP (no alpha; standard viewers expect RGB or L) ---
    if ext == ".bmp":
        img = _flatten_alpha(img)
        img.save(output_path)
        return output_path

    # --- PPM (RGB only, no alpha) ---
    if ext == ".ppm":
        img = _flatten_alpha(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(output_path)
        return output_path

    # --- PCX (RGB or P, no alpha) ---
    if ext == ".pcx":
        img = _flatten_alpha(img)
        img.save(output_path)
        return output_path

    # --- GIF (palette mode; optionally 1-colour transparency) ---
    if ext == ".gif":
        if img.mode == "RGBA":
            # Quantise to palette preserving transparency
            img = img.quantize(colors=255, method=Image.Quantize.FASTOCTREE, dither=0)
        elif img.mode not in ("P", "L", "1"):
            img = img.convert("P")
        img.save(output_path)
        return output_path

    # --- ICO (needs RGBA for proper transparency) ---
    if ext == ".ico":
        img = _ensure_rgba(img)
        img.save(output_path, sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
        return output_path

    # --- WEBP (supports RGB and RGBA, quality applies) ---
    if ext == ".webp":
        img.save(output_path, quality=quality, **_meta_kwargs(ext))
        return output_path

    # --- AVIF (supports RGB and RGBA, quality applies) ---
    if ext == ".avif":
        img.save(output_path, quality=quality, **_meta_kwargs(ext))
        return output_path

    # --- QOI (supports RGB and RGBA) ---
    if ext == ".qoi":
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "PA") else "RGB")
        img.save(output_path)
        return output_path

    # --- Default: PNG, TIFF, TGA – all support RGBA; preserve mode ---
    img.save(output_path, **_meta_kwargs(ext))
    return output_path


def build_output_path(
    input_path: str,
    target_ext: str,
    output_dir: Optional[str] = None,
    input_root: Optional[str] = None,
    suffix: str = "",
) -> str:
    """
    Derive an output file path for a converted file.

    If output_dir is given, the file is placed inside it (mirroring subdirectory
    structure when input_root is provided).  Otherwise the file is placed next
    to the original.  ``suffix`` is appended to the stem before the extension
    (e.g. suffix="_converted" → "photo_converted.png").
    """
    p = Path(input_path)
    new_name = p.stem + (suffix or "") + target_ext

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
