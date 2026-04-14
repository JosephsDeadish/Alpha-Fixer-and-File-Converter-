"""
File converter – converts between image formats.

Supported formats: PNG, JPEG, BMP, TIFF, WEBP, TGA, ICO, GIF, DDS,
                   PPM, PCX, AVIF, QOI, SVG, JPEG2000.

SVG input (raster rendering) requires one of:
  - cairosvg  (pip install cairosvg)   – needs libcairo system library
  - svglib    (pip install svglib)      – pure Python, may need reportlab
If neither is installed the app will raise an ImportError with install
instructions when an SVG file is opened.

SVG output — two modes depending on installed libraries:
  • vtracer available (pip install vtracer):
      Traces the raster into true vector paths (colour polygons/beziers).
      The result is a genuine scalable vector document suitable for logos,
      icons, pixel art, and game sprites.  Large or photographic images
      may produce complex SVGs.
  • vtracer not installed (fallback):
      Embeds the raster as a base64-encoded PNG inside an <svg> element.
      Pixel-perfect at any zoom level but not a true vector document.
"""
import base64
import io
import os
import logging
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image

from .alpha_processor import _save_dds, _load_dds

logger = logging.getLogger(__name__)

SUPPORTED_OUTPUT_FORMATS = {
    "AVIF": ".avif",
    "BMP": ".bmp",
    "DDS": ".dds",
    "GIF": ".gif",
    "ICO": ".ico",
    "JPEG": ".jpg",
    "JPEG2000": ".jp2",
    "PCX": ".pcx",
    "PNG": ".png",
    "PPM": ".ppm",
    "QOI": ".qoi",
    "SVG": ".svg",
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
    "JPEG2000": (
        "JPEG 2000 — advanced wavelet-based compression with alpha support.\n"
        "Superior quality to standard JPEG at the same file size.\n"
        "Supports full RGBA. Used in professional print, cinema (DCI), and medical imaging."
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
    "SVG": (
        "Scalable Vector Graphics — XML-based vector/lossless format.\n"
        "SVG input: renders the vector art to a full-colour RGBA raster.\n"
        "  Requires cairosvg (pip install cairosvg) or svglib.\n"
        "SVG output — two modes:\n"
        "  • vtracer installed: traces raster into true vector paths\n"
        "      (pip install vtracer). Best for logos, icons, pixel art.\n"
        "  • fallback: embeds raster as base64 PNG — pixel-perfect but\n"
        "      not true vector. No extra libraries required.\n"
        "Useful for icons, logos, UI assets, and scalable game graphics."
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
_QUALITY_FORMATS = {".jpg", ".jpeg", ".webp", ".avif", ".jp2"}


def _has_cairosvg() -> bool:
    """Return True when cairosvg is importable."""
    try:
        import cairosvg  # noqa: F401
        return True
    except ImportError:
        return False


def _has_svglib() -> bool:
    """Return True when svglib + reportlab are importable."""
    try:
        from svglib.svglib import svg2rlg  # noqa: F401
        from reportlab.graphics import renderPM  # noqa: F401
        return True
    except ImportError:
        return False


def _has_vtracer() -> bool:
    """Return True when vtracer is importable (used for raster→SVG vectorization)."""
    try:
        import vtracer  # noqa: F401
        return True
    except ImportError:
        return False


def _load_svg(path: str) -> Image.Image:
    """
    Render an SVG file to an RGBA PIL Image.

    Tries (in order):
    1. cairosvg       — pip install cairosvg
    2. svglib         — pip install svglib
    3. Raises ImportError with installation instructions.
    """
    if _has_cairosvg():
        import cairosvg
        png_bytes = cairosvg.svg2png(url=path)
        img = Image.open(io.BytesIO(png_bytes))
        try:
            img.load()
        except Exception:
            img.close()
            raise
        return img.convert("RGBA")

    if _has_svglib():
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPM
        drawing = svg2rlg(path)
        if drawing is None:
            raise ValueError(f"svglib could not parse SVG file: {path}")
        png_bytes = renderPM.drawToString(drawing, fmt="PNG")
        img = Image.open(io.BytesIO(png_bytes))
        try:
            img.load()
        except Exception:
            img.close()
            raise
        return img.convert("RGBA")

    raise ImportError(
        "SVG input requires cairosvg or svglib.\n"
        "Install one of them:\n"
        "    pip install cairosvg\n"
        "    pip install svglib\n"
        "(cairosvg also needs libcairo on your system — see https://cairosvg.org/)"
    )


def _save_svg(img: Image.Image, path: str) -> None:
    """
    Save *img* as an SVG file.

    Strategy:
    1. If vtracer is installed, write a temporary PNG, run vtracer to produce
       genuine vector paths, and write the resulting SVG.  Best for logos,
       icons, and pixel art.
    2. Otherwise, embed the raster as a base64-encoded PNG inside an <svg>
       wrapper.  Pixel-perfect but not a true vector document.
    """
    if _has_vtracer():
        import vtracer as _vtracer
        # vtracer needs RGB or RGBA PNG input
        save_img = img
        need_close = False
        if img.mode not in ("RGB", "RGBA"):
            save_img = img.convert("RGBA")
            need_close = True
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_png:
                tmp_png_path = tmp_png.name
            try:
                save_img.save(tmp_png_path, format="PNG")
                _vtracer.convert_image_to_svg_py(
                    tmp_png_path,
                    path,
                    colormode="color",
                )
            finally:
                try:
                    os.unlink(tmp_png_path)
                except OSError:
                    pass
        finally:
            if need_close:
                save_img.close()
        return

    # Fallback: embed raster as base64 PNG inside an SVG wrapper
    buf = io.BytesIO()
    save_img = img
    need_close = False
    if img.mode not in ("RGB", "RGBA"):
        save_img = img.convert("RGBA")
        need_close = True
    try:
        save_img.save(buf, format="PNG", optimize=False)
    finally:
        if need_close:
            save_img.close()
    w, h = img.size
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    svg_text = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">\n'
        f'  <image width="{w}" height="{h}" '
        f'xlink:href="data:image/png;base64,{b64}"/>\n'
        f'</svg>\n'
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg_text)


def _open_image(path: str) -> Image.Image:
    """Open an image preserving its native mode (DDS/SVG handled specially)."""
    ext = Path(path).suffix.lower()
    if ext == ".dds":
        return _load_dds(path)
    if ext == ".svg":
        return _load_svg(path)
    img = Image.open(path)
    try:
        img.load()  # force decode so the file handle can be closed
    except MemoryError:
        w, h = img.size
        img.close()
        raise MemoryError(
            f"Not enough memory to open {w}×{h} image "
            f"({w * h / 1_000_000:.1f} megapixels). Try a smaller file."
        )
    except Exception:
        img.close()
        raise
    return img


def _flatten_alpha(img: Image.Image, bg_rgb: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """
    Composite *img* onto a solid white (or *bg_rgb*) background, removing any
    alpha channel.  Returns an RGB (or L) image safe to save as JPEG/BMP/PPM.
    """
    if img.mode == "RGBA":
        base = Image.new("RGB", img.size, bg_rgb)
        try:
            base.paste(img, mask=img.split()[3])
        except Exception:
            base.close()
            raise
        return base
    if img.mode == "LA":
        base = Image.new("L", img.size, bg_rgb[0])
        try:
            base.paste(img, mask=img.split()[1])
        except Exception:
            base.close()
            raise
        return base
    if img.mode in ("PA", "P"):
        # Palette images may have embedded transparency; go via RGBA
        rgba = img.convert("RGBA")
        try:
            base = Image.new("RGB", img.size, bg_rgb)
            try:
                base.paste(rgba, mask=rgba.split()[3])
            except Exception:
                base.close()
                raise
            return base
        finally:
            rgba.close()
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
    :param quality:        JPEG/WEBP/AVIF/JPEG2000 quality (1-100). 100 = lossless for JPEG2000.
    :param resize:         Optional (width, height) tuple.
    :param keep_metadata:  When True, copy EXIF/ICC/DPI metadata to the output.
    :returns: output_path on success.
    :raises:  Exception on failure.
    """
    src_img = _open_image(input_path)
    try:
        img = src_img

        if resize:
            try:
                # int() intentionally truncates floats (e.g. 100.9 → 100).
                # Image dimensions are always whole pixels; callers should pass
                # integer values, but fractional values are silently floored here
                # to be lenient with minor rounding errors from UI spinboxes or
                # computed aspect-ratio widths/heights.
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

        # Capture final image dimensions before format-specific conversions may
        # change the image object, so any MemoryError message has accurate context.
        _save_w, _save_h = img.size

        try:
            # --- DDS (custom writer, needs RGBA) ---
            if ext == ".dds":
                rgba = _ensure_rgba(img)
                try:
                    _save_dds(rgba, output_path)
                finally:
                    if rgba is not img:
                        rgba.close()
                return output_path

            # --- SVG (raster embedded in SVG wrapper) ---
            if ext == ".svg":
                _save_svg(img, output_path)
                return output_path

            # --- JPEG (no alpha, RGB or L only) ---
            if ext in (".jpg", ".jpeg"):
                flat = _flatten_alpha(img)
                try:
                    flat.save(output_path, quality=quality, **_meta_kwargs(ext))
                finally:
                    if flat is not img:
                        flat.close()
                return output_path

            # --- BMP (no alpha; standard viewers expect RGB or L) ---
            if ext == ".bmp":
                flat = _flatten_alpha(img)
                try:
                    flat.save(output_path)
                finally:
                    if flat is not img:
                        flat.close()
                return output_path

            # --- PPM (RGB only, no alpha) ---
            if ext == ".ppm":
                flat = _flatten_alpha(img)
                rgb = None
                try:
                    if flat.mode not in ("RGB", "L"):
                        rgb = flat.convert("RGB")
                        rgb.save(output_path)
                    else:
                        flat.save(output_path)
                finally:
                    if rgb is not None:
                        rgb.close()
                    if flat is not img:
                        flat.close()
                return output_path

            # --- PCX (RGB or P, no alpha) ---
            if ext == ".pcx":
                flat = _flatten_alpha(img)
                try:
                    flat.save(output_path)
                finally:
                    if flat is not img:
                        flat.close()
                return output_path

            # --- GIF (palette mode; optionally 1-colour transparency) ---
            if ext == ".gif":
                if img.mode == "RGBA":
                    # Quantise to palette preserving transparency
                    gif_img = img.quantize(colors=255, method=Image.Quantize.FASTOCTREE, dither=0)
                    try:
                        gif_img.save(output_path)
                    finally:
                        gif_img.close()
                elif img.mode not in ("P", "L", "1"):
                    gif_img = img.convert("P")
                    try:
                        gif_img.save(output_path)
                    finally:
                        gif_img.close()
                else:
                    img.save(output_path)
                return output_path

            # --- ICO (needs RGBA for proper transparency) ---
            if ext == ".ico":
                rgba = _ensure_rgba(img)
                try:
                    rgba.save(output_path, sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
                finally:
                    if rgba is not img:
                        rgba.close()
                return output_path

            # --- WEBP (supports RGB and RGBA, quality applies) ---
            if ext == ".webp":
                img.save(output_path, quality=quality, **_meta_kwargs(ext))
                return output_path

            # --- AVIF (supports RGB and RGBA, quality applies) ---
            if ext == ".avif":
                img.save(output_path, quality=quality, **_meta_kwargs(ext))
                return output_path

            # --- JPEG 2000 (supports RGB and RGBA; quality maps to compression rate) ---
            if ext == ".jp2":
                if quality >= 100:
                    # Lossless (reversible wavelet transform)
                    img.save(output_path, irreversible=False)
                else:
                    # Lossy: map quality 1-99 to a compression rate.
                    # A rate of ~1.0 bpp is high quality; lower rates compress more.
                    # quality=99→rate≈1.0 bpp (near-lossless), quality=1→rate≈0.02 bpp.
                    rate = max(0.02, (quality / 100.0) ** 2 * 10)
                    img.save(output_path, irreversible=True,
                             quality_mode="rates", quality_layers=[rate])
                return output_path

            # --- QOI (supports RGB and RGBA) ---
            if ext == ".qoi":
                if img.mode not in ("RGB", "RGBA"):
                    qoi_img = img.convert("RGBA" if img.mode in ("LA", "PA") else "RGB")
                    try:
                        qoi_img.save(output_path)
                    finally:
                        qoi_img.close()
                else:
                    img.save(output_path)
                return output_path

            # --- Default: PNG, TIFF, TGA – all support RGBA; preserve mode ---
            img.save(output_path, **_meta_kwargs(ext))
            return output_path

        except MemoryError:
            raise MemoryError(
                f"Not enough memory to save {_save_w}×{_save_h} image "
                f"({_save_w * _save_h / 1_000_000:.1f} megapixels) as "
                f"{ext.lstrip('.')}. Try a smaller resize target or a lower quality setting."
            )
    finally:
        if img is not src_img:
            img.close()
        src_img.close()


def get_gif_frame_count(path: str) -> int:
    """
    Return the number of frames in a GIF file.

    Returns 1 for non-animated GIFs or any non-GIF file.
    Returns 1 on any error (safe fallback so callers need no try/except).
    """
    try:
        ext = Path(path).suffix.lower()
        if ext != ".gif":
            return 1
        with Image.open(path) as img:
            return getattr(img, "n_frames", 1)
    except Exception:
        return 1


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
