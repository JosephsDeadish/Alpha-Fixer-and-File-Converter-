"""
File converter – converts between image formats.

Supported output formats: PNG, JPEG, BMP, TIFF, WEBP, TGA, ICO, GIF, DDS,
                          PBM, PGM, PNM, PPM, PCX, AVIF, QOI, SVG,
                          JPEG2000, XNB, TIM.

SVG input uses bundled QtSvg, with source-run alternatives:
  - cairosvg  (pip install cairosvg)   – needs libcairo system library
  - svglib    (pip install svglib)      – pure Python, may need reportlab
QtSvg's supported SVG subset is rasterized to RGBA.

SVG output — two modes depending on installed libraries:
  • vtracer available (included in release bundles):
      Traces the raster into true vector paths (colour polygons/beziers).
      The result is a genuine scalable vector document suitable for logos,
      icons, pixel art, and game sprites.  Large or photographic images
      may produce complex SVGs.
  • vtracer not installed (fallback):
      Embeds the raster as a base64-encoded PNG inside an <svg> element.
      Pixel-perfect at original resolution but not a true vector document.
"""
import base64
import io
import os
import logging
import math
import re
import struct
import tempfile
import warnings
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from .alpha_processor import _save_dds, _load_dds, _has_wand

logger = logging.getLogger(__name__)

SUPPORTED_OUTPUT_FORMATS = {
    "AVIF": ".avif",
    "BMP": ".bmp",
    "PBM": ".pbm",
    "DDS": ".dds",
    "GIF": ".gif",
    "PGM": ".pgm",
    "ICO": ".ico",
    "JPEG": ".jpg",
    "JPEG2000": ".jp2",
    "PCX": ".pcx",
    "PNM": ".pnm",
    "PNG": ".png",
    "PPM": ".ppm",
    "QOI": ".qoi",
    "SVG": ".svg",
    "TGA": ".tga",
    "TIM": ".tim",
    "TIFF": ".tiff",
    "WEBP": ".webp",
    "XNB": ".xnb",
}

# Display list for UI combos (name → extension), alphabetical
OUTPUT_FORMAT_LIST = sorted(SUPPORTED_OUTPUT_FORMATS.items())

DDS_VARIANT_OPTIONS: list[tuple[str, str]] = [
    ("Auto (RGB/RGBA by alpha)", "auto"),
    ("RGB 24-bit (discard alpha)", "rgb"),
    ("RGBA 32-bit (preserve alpha)", "rgba"),
    ("BC1 / DXT1 compressed", "dxt1"),
    ("BC2 / DXT3 compressed", "dxt3"),
    ("BC3 / DXT5 compressed", "dxt5"),
]

_OPTIONAL_PIL_OUTPUT_FORMATS = ("AVIF", "JPEG2000", "QOI", "WEBP")


def dds_compression_available() -> bool:
    """Return True when compressed DDS save variants are available."""
    return _has_wand()


def optional_pillow_output_limits() -> list[tuple[str, str]]:
    """Return optional output formats that are unavailable in this build."""
    limits: list[tuple[str, str]] = []
    for target_format in _OPTIONAL_PIL_OUTPUT_FORMATS:
        reason = output_format_unavailable_reason(target_format)
        if reason:
            limits.append((target_format, reason))
    return limits


_CUSTOM_OUTPUT_FORMATS = {"DDS", "SVG", "TIM", "XNB"}
_ALPHA_UNSUPPORTED_OUTPUT_FORMATS = {"BMP", "JPEG", "PBM", "PGM", "PNM", "PPM", "PCX"}
_FORMAT_UNAVAILABLE_HINTS = {
    "AVIF": "AVIF export needs Pillow built with libavif support.",
    "JPEG2000": "JPEG2000 export needs Pillow built with OpenJPEG support.",
    "QOI": "QOI export needs a Pillow build with QOI support.",
    "WEBP": "WEBP export needs Pillow built with WebP support.",
}


def output_format_unavailable_reason(target_format: str) -> str:
    """Return a human-readable reason when *target_format* cannot be written."""
    if target_format in _CUSTOM_OUTPUT_FORMATS:
        return ""
    ext = SUPPORTED_OUTPUT_FORMATS.get(target_format)
    if not ext:
        return f"{target_format} export is not supported."
    try:
        Image.init()
        pil_format = Image.registered_extensions().get(ext.lower())
        if pil_format and pil_format in Image.SAVE:
            return ""
    except Exception:
        pass
    return _FORMAT_UNAVAILABLE_HINTS.get(
        target_format,
        f"{target_format} export is unavailable in this Pillow build.",
    )


def output_format_available(target_format: str) -> bool:
    """Return True when *target_format* can be written in this environment."""
    return not output_format_unavailable_reason(target_format)


def output_format_discards_alpha(target_format: str) -> bool:
    """Return True when *target_format* cannot preserve full alpha."""
    return target_format in _ALPHA_UNSUPPORTED_OUTPUT_FORMATS


def output_codec_selftest_checks() -> tuple[str, ...]:
    """Required encode/decode checks for the currently advertised outputs."""
    checks = []
    for fmt in SUPPORTED_OUTPUT_FORMATS:
        if fmt == "DDS":
            checks.append("png_to_dds_rgba")
        elif fmt == "SVG":
            checks.extend(("svg_rasterization", "svg_vectorization"))
        else:
            checks.append(f"png_to_{fmt.lower()}")
    return tuple(checks)


def output_codec_selfcheck(directory: str) -> dict[str, dict[str, object]]:
    """Exercise production codecs with small offline-generated RGB/RGBA images.

    DDS variants and SVG rendering/tracing have their own runtime checks.
    Registry availability alone is not evidence of a working encoder/decoder.
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    opaque = root / "codec_rgb.png"
    transparent = root / "codec_rgba.png"
    with Image.new("RGB", (32, 32), (40, 120, 200)) as image:
        image.save(opaque)
    with Image.new("RGBA", (32, 32), (40, 120, 200, 255)) as image:
        image.paste((40, 120, 200, 0), (0, 0, 8, 32))
        image.paste((40, 120, 200, 128), (8, 0, 16, 32))
        image.save(transparent)
    results = {}
    Image.init()
    for fmt, ext in SUPPORTED_OUTPUT_FORMATS.items():
        if fmt in {"DDS", "SVG"}:
            continue
        name = f"png_to_{fmt.lower()}"
        destination = root / f"codec_output{ext}"
        try:
            source = opaque if output_format_discards_alpha(fmt) else transparent
            actual_path = convert_file(str(source), str(destination), fmt)
            if Path(actual_path).resolve() != destination.resolve():
                raise ValueError(f"{fmt} export redirected to {actual_path}")
            if fmt in {"TIM", "XNB"}:
                signature = destination.read_bytes()[:4]
                expected = b"\x10\x00\x00\x00" if fmt == "TIM" else b"XNB"
                if not signature.startswith(expected):
                    raise ValueError(f"{fmt} output signature mismatch")
            else:
                with Image.open(destination) as encoded:
                    expected = Image.registered_extensions().get(ext)
                    if encoded.format != expected or not expected:
                        raise ValueError(f"{fmt} decoded as {encoded.format}, expected {expected}")
            with _open_image(str(destination)) as decoded:
                if decoded.size != (32, 32):
                    raise ValueError(f"{fmt} dimensions changed: {decoded.size}")
                if not output_format_discards_alpha(fmt):
                    with decoded.convert("RGBA") as rgba:
                        alpha = [rgba.getpixel((x, 16))[3] for x in (4, 12, 24)]
                    if alpha[0] != 0 or alpha[2] != 255:
                        raise ValueError(f"{fmt} lost transparent/opaque pixels: {alpha}")
                    if fmt not in {"GIF", "TIM"} and abs(alpha[1] - 128) > 2:
                        raise ValueError(f"{fmt} lost partial alpha: {alpha}")
            results[name] = {"ok": True, "detail": f"{fmt} encode/decode; size=32x32; alpha={source == transparent}"}
        except Exception as exc:
            results[name] = {"ok": False, "detail": f"{fmt}: {exc}"}
    return results

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
        "No alpha channel support. Transparent sources are auto-saved as PNG to preserve alpha."
    ),
    "DDS": (
        "DirectDraw Surface — GPU-native texture format.\n"
        "Used by DirectX games and engines (Unreal, Unity, etc.).\n"
        "Supports GPU texture workflows and includes an output variant selector in the converter.\n"
        "Choose automatic RGB/RGBA handling, force 24-bit RGB / 32-bit RGBA raw output,\n"
        "or use BC1/DXT1, BC2/DXT3, or BC3/DXT5 compressed output when ImageMagick/wand is available."
    ),
    "PBM": (
        "Portable Bitmap — simple 1-bit black-and-white image format.\n"
        "Best for masks, monochrome art, and legacy toolchains.\n"
        "No greyscale or alpha; transparent sources are auto-saved as PNG."
    ),
    "GIF": (
        "Graphics Interchange Format — 256-colour indexed format with animation.\n"
        "Limited palette makes it unsuitable for photos or detailed textures.\n"
        "Supports 1-bit transparency only. Best for simple icons or animations."
    ),
    "PGM": (
        "Portable Graymap — simple greyscale image format.\n"
        "Useful for masks, heightmaps, scientific tools, and older pipelines.\n"
        "Stores luminance only; transparent sources are auto-saved as PNG."
    ),
    "ICO": (
        "Windows Icon format — multi-size icon bundle.\n"
        "Used for application icons, desktop shortcuts, and taskbar icons.\n"
        "Supports multiple resolutions in a single file (16×16 to 256×256)."
    ),
    "JPEG": (
        "Joint Photographic Experts Group — lossy compression for photos.\n"
        "No alpha channel support; transparent sources are auto-saved as PNG.\n"
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
        "Still encountered in some legacy game assets and old CAD workflows.\n"
        "Transparent sources are auto-saved as PNG."
    ),
    "PNM": (
        "Portable AnyMap — Netpbm family container (PBM/PGM/PPM).\n"
        "Simple interchange format for command-line tools and legacy pipelines.\n"
        "This app saves color PNM output as a standard RGB pixmap.\n"
        "Transparent sources are auto-saved as PNG."
    ),
    "PNG": (
        "Portable Network Graphics — lossless compression with full alpha channel.\n"
        "The best choice for game textures, UI assets, and any image with transparency.\n"
        "Larger than JPEG for photos but preserves every pixel perfectly."
    ),
    "PPM": (
        "Portable Pixmap — simple, uncompressed text or binary RGB format.\n"
        "Very large files with no compression. Supported by most graphics tools.\n"
        "No alpha channel. Transparent sources are auto-saved as PNG."
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
        "  Uses QtSvg bundled with the application (SVG feature support follows QtSvg).\n"
        "SVG output — two modes:\n"
        "  • bundled vtracer: traces raster into true vector paths\n"
        "      Best for logos, icons, pixel art; tracing is approximate.\n"
        "  • fallback: embeds raster as base64 PNG — pixel-perfect but\n"
        "      not true vector. No extra libraries required.\n"
        "Useful for icons, logos, UI assets, and scalable game graphics."
    ),
    "TIM": (
        "PlayStation 1 TIM texture — classic console image/texture format.\n"
        "This app writes 16-bit direct-colour TIM files for export and modding.\n"
        "Transparency is mapped to TIM's limited transparent/semi-transparent states."
    ),
    "TIFF": (
        "Tagged Image File Format — flexible lossless/compressed format.\n"
        "Used in professional print, photography, and scientific imaging.\n"
        "Supports alpha and various colour depths.\n"
        "TIFF-to-TIFF preserves all decodable pages and supported per-page metadata.\n"
        "Other outputs and previews use page 1 only; private tags and original layout\n"
        "or compression are not guaranteed."
    ),
    "WEBP": (
        "Google WebP — modern lossy/lossless format for the web.\n"
        "Supports alpha channel. Smaller than PNG at similar quality.\n"
        "Best for web assets, UI images, and web-delivered game textures."
    ),
    "XNB": (
        "XNA / MonoGame binary content format — Texture2D asset.\n"
        "Used by XNA Game Studio and MonoGame for Windows/Xbox/Phone.\n"
        "Reads most XNB texture sub-formats; writes as Color (RGBA8888)."
    ),
}

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


def _has_qt_svg() -> bool:
    """QtSvg ships with the application and needs no external SVG renderer."""
    try:
        from PyQt6.QtSvg import QSvgRenderer
        from PyQt6.QtGui import QImage, QPainter
        return True
    except (ImportError, OSError):
        return False


def svg_input_available() -> bool:
    return _has_qt_svg() or _has_cairosvg() or _has_svglib()


_svg_app = None


def _load_svg_via_qt(path: str) -> Image.Image:
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer
    from PyQt6.QtWidgets import QApplication

    global _svg_app
    if QApplication.instance() is None:
        # Retain the application for command-line conversions and text rendering.
        _svg_app = QApplication(["AlphaFixer SVG", "-platform", "offscreen"])
    renderer = QSvgRenderer(path)
    if not renderer.isValid():
        raise ValueError(f"Invalid or unsupported SVG file: {path}")
    size = renderer.defaultSize()
    width, height = size.width(), size.height()
    if width <= 0 or height <= 0:
        raise ValueError("SVG must have positive dimensions or a valid viewBox.")
    if Image.MAX_IMAGE_PIXELS and width * height > Image.MAX_IMAGE_PIXELS:
        raise Image.DecompressionBombError("SVG dimensions exceed the safe pixel limit.")
    surface = QImage(width, height, QImage.Format.Format_RGBA8888)
    if surface.isNull():
        raise MemoryError("Cannot allocate SVG rendering surface.")
    surface.fill(0)
    painter = QPainter(surface)
    try:
        renderer.render(painter, QRectF(0, 0, width, height))
    finally:
        painter.end()
    pixels = surface.constBits()
    pixels.setsize(surface.sizeInBytes())
    return Image.frombytes("RGBA", (width, height), bytes(pixels), "raw", "RGBA",
                           surface.bytesPerLine())


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
    1. QtSvg         — bundled with PyQt6
    2. cairosvg / svglib — optional source-run alternatives
    3. Raises ImportError if no renderer is available.
    """
    if _has_qt_svg():
        return _load_svg_via_qt(path)
    if _has_cairosvg():
        import cairosvg
        png_bytes = cairosvg.svg2png(url=path)
        img = Image.open(io.BytesIO(png_bytes))
        try:
            img.load()
            return img.convert("RGBA")
        finally:
            img.close()

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
            return img.convert("RGBA")
        finally:
            img.close()

    raise ImportError(
        "SVG input requires bundled QtSvg, cairosvg or svglib.\n"
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


def _load_image_pixels(img: Image.Image) -> None:
    if img.format != "TIFF":
        img.load()
        return

    load_end = img.load_end

    def tracked_load_end():
        # Pillow 10 transposes the core but leaves Orientation in EXIF.
        # Observe the transpose, not just a tag or a change in dimensions:
        # mirrors and square rotations do not change the raster dimensions.
        orientation = img.tag_v2.get(274)
        if orientation is None and img._exif is not None:
            orientation = img._exif.get(274)
        core = img.im
        img._display_orientation_applied = False
        load_end()
        if orientation in range(2, 9) and img.im is not core:
            img._display_orientation_applied = True
            exif = img.getexif()
            if exif.get(274) == orientation:
                del exif[274]
                img.tag_v2.pop(274, None)
                if "exif" in img.info:
                    img.info["exif"] = exif.tobytes()

    img.load_end = tracked_load_end
    try:
        img.load()
    finally:
        del img.load_end


_XMP_ORIENTATION_PATTERNS = (
    r"""tiff:Orientation\s*=\s*["']\s*([0-9]+)\s*["']""",
    r"<tiff:Orientation>\s*([0-9]+)\s*</tiff:Orientation>",
)


def _xmp_orientation(value):
    packets = value if isinstance(value, tuple) else (value,)
    for packet in packets:
        if isinstance(packet, bytes):
            packet = packet.decode("utf-8", errors="replace")
        if not isinstance(packet, str):
            continue
        for pattern in _XMP_ORIENTATION_PATTERNS:
            match = re.search(pattern, packet)
            if match:
                # Attributes take precedence over elements, as in Pillow.
                # Reject the whole value, rather than accepting its first digit.
                number = match[1].lstrip("0")
                return int(number) if len(number) == 1 and number in "12345678" else None
    return None


def _without_xmp_orientation(value):
    if isinstance(value, tuple):
        return tuple(_without_xmp_orientation(item) for item in value)
    if isinstance(value, (str, bytes)):
        for pattern in _XMP_ORIENTATION_PATTERNS:
            value = re.sub(pattern.encode() if isinstance(value, bytes) else pattern,
                           b"" if isinstance(value, bytes) else "", value)
    return value


def _normalize_image_orientation(img: Image.Image) -> None:
    """Use display-oriented pixels, leaving no EXIF rotation to apply again."""
    from PIL import ImageOps

    # Decode errors must not be mistaken for optional metadata errors.
    img.load()
    try:
        # Cache the orientation before cleaning XMP-only inputs. Pillow 10
        # may clean EXIF instead of XMP; reduce() then reads the stale XMP anew.
        xmp = {key: img.info.pop(key)
               for key in ("xmp", "XML:com.adobe.xmp") if key in img.info}
        exif = img.getexif()
        if 274 not in exif and not getattr(img, "_display_orientation_applied", False):
            # Match Pillow's key precedence, but support every representation
            # that our cleaner removes. Explicit EXIF (even 1/invalid) wins.
            packets = [xmp.get(key) for key in ("XML:com.adobe.xmp", "xmp")]
            # Pillow 10 keeps TIFF XMP only in tag 700, not img.info.
            packets.append(exif.get(700))
            if hasattr(img, "tag_v2"):
                packets.append(img.tag_v2.get(700))
            for packet in packets:
                orientation = _xmp_orientation(packet)
                if orientation is not None:
                    exif[274] = orientation
                    break
        ImageOps.exif_transpose(img, in_place=True)
        img.info.update({key: _without_xmp_orientation(value) for key, value in xmp.items()})
        # TIFF XMP also lives in cached EXIF and tag_v2, which encoders retain.
        exif = img.getexif()
        if 700 in exif:
            exif[700] = _without_xmp_orientation(exif[700])
        if hasattr(img, "tag_v2"):
            if 700 in img.tag_v2:
                img.tag_v2[700] = _without_xmp_orientation(img.tag_v2[700])
            if 274 not in exif:
                img.tag_v2.pop(274, None)
        if "exif" in img.info:
            img.info["exif"] = exif.tobytes()
        if "Raw profile type exif" in img.info:
            img.info["Raw profile type exif"] = exif.tobytes().hex()
    except (OSError, SyntaxError, ValueError, TypeError, KeyError, struct.error):
        # A malformed EXIF block can fail parsing or serialization after the
        # transpose. Keep decoded pixels but never retain a stale orientation.
        for key in ("exif", "Raw profile type exif", "xmp", "XML:com.adobe.xmp"):
            img.info.pop(key, None)
        img._exif = Image.Exif()
        if hasattr(img, "tag_v2"):
            for tag in (274, 700):
                img.tag_v2.pop(tag, None)
        warnings.warn("Malformed EXIF metadata discarded; using decoded pixels.",
                      UserWarning, stacklevel=2)


def _open_image(path: str) -> Image.Image:
    """Open display-oriented pixels in their native mode."""
    ext = Path(path).suffix.lower()
    if ext == ".dds":
        return _load_dds(path)
    if ext == ".svg":
        return _load_svg(path)
    if ext == ".xnb":
        from .xnb_handler import load_xnb
        return load_xnb(path)
    if ext == ".tim":
        from .tim_handler import load_tim
        return load_tim(path)
    img = Image.open(path)
    try:
        if img.format == "TIFF":
            # Counting pages can leak later-page metadata into Pillow's info.
            with Image.open(path) as pages:
                img.info["tiff_page_count"] = _count_tiff_pages(pages)
        if ext in (".tiff", ".tif"):
            # TIFF loading closes the file before Pillow caches nested IFDs.
            try:
                exif = img.getexif()
                if 40965 in exif.get_ifd(34665):
                    exif.get_ifd(40965)
            except Exception:
                pass
        _load_image_pixels(img)  # force decode so the file handle can be closed
        _normalize_image_orientation(img)
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


def _open_image_for_preview(path: str, max_size: int) -> Image.Image:
    """Open *path* for preview/thumbnail use, preferring lower-memory decoding."""
    ext = Path(path).suffix.lower()
    if ext in {".dds", ".svg", ".xnb", ".tim"}:
        return _open_image(path)

    img = Image.open(path)
    try:
        if img.format == "TIFF":
            with Image.open(path) as pages:
                img.info["tiff_page_count"] = _count_tiff_pages(pages)
            _load_image_pixels(img)
        target = max(1, int(max_size))
        longest = max(img.size) if img.size else 0
        if longest > target * 2:
            try:
                img.draft(None, (target * 2, target * 2))
            except Exception:
                pass
            _normalize_image_orientation(img)
            current_longest = max(img.size) if img.size else longest
            reduce_factor = max(1, int(math.floor(current_longest / max(target * 2, 1))))
            if reduce_factor > 1:
                reduced = img.reduce(reduce_factor)
                img.close()
                img = reduced
        img.load()
        _normalize_image_orientation(img)
    except MemoryError:
        w, h = img.size
        img.close()
        raise MemoryError(
            f"Not enough memory to preview {w}×{h} image "
            f"({w * h / 1_000_000:.1f} megapixels). Try a smaller file."
        )
    except Exception:
        img.close()
        raise
    return img


def _count_tiff_pages(img: Image.Image) -> int | None:
    """Best-effort metadata scan; decoding page 1 remains the loader's job."""
    try:
        return img.n_frames
    except (OSError, SyntaxError, ValueError, EOFError):
        # A readable first page may be followed by an unsupported/malformed IFD.
        return None


def tiff_first_page_note(page_count: int | None) -> str:
    """Describe page loss when exporting TIFF to a single-image output."""
    if page_count is None:
        return (
            "TIFF: page 1 only — total page count unavailable; additional pages "
            "and their metadata are not exported."
        )
    if page_count <= 1:
        return ""
    return (
        f"TIFF: page 1 of {page_count} only — additional pages and their "
        "metadata are not exported."
    )


def get_tiff_page_count(path: str) -> int | None:
    if Path(path).suffix.lower() not in {".tif", ".tiff"}:
        return 1
    with Image.open(path) as img:
        return _count_tiff_pages(img) if img.format == "TIFF" else 1


def tiff_preview_note(page_count: int | None) -> str:
    if page_count is not None and page_count <= 1:
        return ""
    total = f" of {page_count}" if page_count is not None else ""
    unknown = " (total page count unavailable)" if page_count is None else ""
    return f"TIFF preview: page 1{total} only{unknown} — other pages are not previewed."


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


def _image_metadata(src_img: Image.Image, img: Image.Image, fmt_ext: str,
                    keep_metadata: bool) -> dict:
    if not keep_metadata:
        return {}
    kw: dict = {}
    try:
        if fmt_ext not in (".jpg", ".jpeg", ".jfif", ".jpe",
                           ".png", ".tiff", ".tif", ".webp", ".avif"):
            return kw
        for key in ("icc_profile", "dpi"):
            if key == "dpi" and fmt_ext in (".webp", ".avif"):
                continue
            if key in src_img.info:
                kw[key] = src_img.info[key]
        source_exif = src_img.getexif()
        if source_exif:
            exif = Image.Exif()
            exif.load(source_exif.tobytes())
            # Rebuild storage/layout tags for the output raster.
            for tag in (254, 255, 258, 259, 262, 266, 273, 277, 278,
                        279, 284, 288, 289, 292, 293, 297, 301, 317, 320,
                        322, 323, 324, 325, 330, 338, 339, 347, 34675):
                exif.pop(tag, None)
            for tag, value in ((256, img.width), (257, img.height),
                               (40962, img.width), (40963, img.height)):
                if tag in exif:
                    exif[tag] = value
            if 34665 in exif:
                # Keep offsets until nested interoperability IFDs are cached.
                details = exif.get_ifd(34665)
                for tag, value in ((40962, img.width), (40963, img.height)):
                    if tag in details:
                        details[tag] = value
                if fmt_ext in (".tiff", ".tif") and 40965 in details:
                    details[40965] = exif.get_ifd(40965)
            serialized = exif.tobytes()
            kw["exif"] = exif if fmt_ext in (".tiff", ".tif") else serialized
    except Exception:
        pass
    return kw


def _convert_tiff_pages(source: Image.Image, output_path: str,
                        resize: Optional[tuple[int, int]], keep_metadata: bool,
                        cancel_check: Optional[Callable[[], bool]]) -> str:
    from PIL.TiffImagePlugin import AppendingTiffWriter

    class MetadataTiffWriter(AppendingTiffWriter):
        def fixIFD(self):
            start = self.f.tell()
            super().fixIFD()
            end = self.f.tell()
            # Pillow relocates strip/payload offsets, but not nested EXIF/GPS
            # pointers. Relocate those IFDs and their own payloads as well.
            self.f.seek(start)
            count = self._read(8 if self._bigtiff else 2)
            entry_size = 20 if self._bigtiff else 12
            value_size = 8 if self._bigtiff else 4
            for index in range(count):
                self.f.seek(start + (8 if self._bigtiff else 2) + index * entry_size)
                tag, field_type, length = struct.unpack(
                    self.tagFormat, self.f.read(12 if self._bigtiff else 8)
                )
                if tag not in (34665, 34853, 40965) or length != 1:
                    continue
                offset = self._read(value_size)
                if offset:
                    offset += self.offsetOfNewPage
                    self._rewriteLast(offset, value_size)
                    self.f.seek(offset)
                    self.fixIFD()
            self.f.seek(end)

    if cancel_check and cancel_check():
        raise InterruptedError("TIFF conversion cancelled.")
    # Preflight IFDs: an unreadable later page must fail, not silently lose pages.
    first_info = source.info.copy()
    first_palette = source.palette.copy() if source.palette else None
    count = source.n_frames
    source.seek(0)
    source.palette = first_palette
    source.info.clear()
    source.info.update(first_info)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with MetadataTiffWriter(output_path, new=True) as writer:
        for index in range(count):
            if cancel_check and cancel_check():
                raise InterruptedError("TIFF conversion cancelled.")
            if index:
                # Pillow retains info keys absent on subsequent pages.
                source.info.clear()
                source.palette = None
                source.seek(index)
            try:
                exif = source.getexif()
                if 40965 in exif.get_ifd(34665):
                    exif.get_ifd(40965)
            except MemoryError:
                raise
            except Exception:
                pass
            _load_image_pixels(source)
            _normalize_image_orientation(source)
            page = source.resize(resize, Image.LANCZOS) if resize else source.copy()
            try:
                if page.mode == "I;16B":
                    # All appended pages need II headers. Decode the byte order
                    # explicitly: convert("I;16") can clip samples to 255.
                    normalized = Image.frombytes(
                        "I;16", page.size, page.tobytes(), "raw", "I;16B"
                    )
                    page.close()
                    page = normalized
                # Detached pages avoid implicit source layout/compression tags.
                page.info.clear()
                page.save(writer, format="TIFF",
                          **_image_metadata(source, page, ".tiff", keep_metadata))
                writer.newFrame()
            finally:
                page.close()
            if cancel_check and cancel_check():
                raise InterruptedError("TIFF conversion cancelled.")
    return output_path


def _validated_resize(resize: tuple[int, int]) -> tuple[int, int]:
    try:
        w, h = int(resize[0]), int(resize[1])
    except (TypeError, ValueError):
        raise ValueError(
            f"Invalid resize value: {resize!r}. Both width and height must be integers."
        )
    if w < 1 or h < 1:
        raise ValueError(
            f"Invalid resize dimensions: {w}×{h}. Both width and height must be at least 1 pixel."
        )
    if w > 65535 or h > 65535:
        raise ValueError(
            f"Resize dimensions too large: {w}×{h}. Maximum supported size is 65535×65535 pixels."
        )
    return w, h


def convert_file(
    input_path: str,
    output_path: str,
    target_format: str,
    quality: int = 90,
    resize: Optional[tuple[int, int]] = None,
    keep_metadata: bool = False,
    dds_variant: str = "auto",
    cancel_check: Optional[Callable[[], bool]] = None,
) -> str:
    """
    Convert a single image file.

    TIFF-to-TIFF preserves every decodable page and supported per-page metadata.
    Other outputs export page 1 only, with a UserWarning for multipage inputs.

    The image's native colour mode is preserved where the target format
    supports it.  For targets that cannot store an alpha channel (JPEG, BMP,
    PPM, PCX, GIF) the alpha is properly composited onto a white background
    rather than discarded.

    :param input_path:     Source file path.
    :param output_path:    Destination file path (with correct extension).
    :param target_format:  One of the keys in SUPPORTED_OUTPUT_FORMATS, e.g. "PNG".
    :param quality:        JPEG/WEBP/AVIF/JPEG2000 quality (1-100). 100 = lossless for JPEG2000.
    :param resize:         Optional (width, height) tuple.
    :param keep_metadata:  Copy supported EXIF/ICC/DPI, updating raster dimensions.
                           Pixels are display-oriented regardless of this option.
    :returns: output_path on success.
    :raises:  Exception on failure.
    """
    if (Path(output_path).suffix.lower() in (".tif", ".tiff")
            and Path(input_path).suffix.lower() not in (".dds", ".svg", ".xnb", ".tim")):
        with Image.open(input_path) as source:
            if source.format == "TIFF":
                if resize:
                    resize = _validated_resize(resize)
                # Direct callers may overwrite the input. Close its handle before
                # committing a complete sibling output, as the worker also does.
                from src.ui._ui_utils import staged_output_path
                os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                with staged_output_path(output_path, preserve_existing_mode=True) as staged:
                    try:
                        _convert_tiff_pages(source, staged, resize, keep_metadata, cancel_check)
                    finally:
                        source.close()
                return output_path
    src_img = _open_image(input_path)
    try:
        img = src_img
        page_note = tiff_first_page_note(src_img.info.get("tiff_page_count", 1))
        if page_note:
            warnings.warn(page_note, UserWarning, stacklevel=2)

        ext = Path(output_path).suffix.lower()
        if (img.mode in ("P", "RGB", "L") and "transparency" in img.info
                and (resize or ext in (".tiff", ".tif", ".tga", ".qoi", ".jp2"))):
            # These encoders cannot store PNG palette/color-key alpha. Materialize
            # it before resampling too, so interpolated pixels retain their alpha.
            img = img.convert("LA" if img.mode == "L" else "RGBA")

        if resize:
            w, h = _validated_resize(resize)
            try:
                resized = img.resize((w, h), Image.LANCZOS)
                if img is not src_img:
                    img.close()
                img = resized
            except MemoryError:
                raise MemoryError(
                    f"Not enough memory to resize image to {w}×{h}. "
                    "Try a smaller target size."
                )

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        if ext in (".tiff", ".tif"):
            # TIFF encoders also copy source tag_v2 fields implicitly.
            detached = img.copy()
            if img is not src_img:
                img.close()
            img = detached
        if not keep_metadata:
            for key in ("exif", "icc_profile", "dpi", "xmp", "Raw profile type exif"):
                img.info.pop(key, None)

        # Helper: inject metadata kwargs into save calls
        def _meta_kwargs(fmt_ext: str) -> dict:
            return _image_metadata(src_img, img, fmt_ext, keep_metadata)

        # Capture final image dimensions before format-specific conversions may
        # change the image object, so any MemoryError message has accurate context.
        _save_w, _save_h = img.size

        try:
            # --- DDS (custom writer, needs RGBA) ---
            if ext == ".dds":
                rgba = _ensure_rgba(img)
                try:
                    _save_dds(rgba, output_path, variant=dds_variant)
                finally:
                    if rgba is not img:
                        rgba.close()
                return output_path

            # --- XNB (XNA/MonoGame Texture2D) ---
            if ext == ".xnb":
                from .xnb_handler import save_xnb
                save_xnb(img, output_path)
                return output_path

            # --- TIM (PlayStation 1 texture; 16-bit direct colour) ---
            if ext == ".tim":
                from .tim_handler import save_tim
                save_tim(img, output_path)
                return output_path

            # --- SVG (raster embedded in SVG wrapper) ---
            if ext == ".svg":
                _save_svg(img, output_path)
                return output_path

            # --- JPEG (no alpha, RGB or L only) ---
            if ext in (".jpg", ".jpeg", ".jfif", ".jpe"):
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

            # --- Netpbm family (PBM/PGM/PNM/PPM; no alpha) ---
            if ext in (".pbm", ".pgm", ".pnm", ".ppm"):
                flat = _flatten_alpha(img)
                save_img = None
                grey = None
                try:
                    if ext == ".pbm":
                        grey = flat if flat.mode == "L" else flat.convert("L")
                        save_img = grey.point(lambda v: 255 if v >= 128 else 0, mode="1")
                    elif ext == ".pgm":
                        save_img = flat if flat.mode == "L" else flat.convert("L")
                    elif flat.mode not in ("RGB", "L"):
                        save_img = flat.convert("RGB")
                    else:
                        save_img = flat
                    save_img.save(output_path)
                finally:
                    if grey is not None and grey is not flat:
                        grey.close()
                    if save_img is not None and save_img is not flat:
                        save_img.close()
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
                    gif_img = img.quantize(colors=255, method=Image.Quantize.FASTOCTREE, dither=0)
                    try:
                        with img.getchannel("A") as alpha:
                            with alpha.point(lambda value: 255 if value == 0 else 0) as mask:
                                gif_img.paste(255, mask=mask)
                            if alpha.getextrema()[0] == 0:
                                gif_img.save(output_path, transparency=255)
                            else:
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
