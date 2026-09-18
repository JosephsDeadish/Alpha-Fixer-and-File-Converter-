"""
Tests for file converter utilities.
"""
import sys
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.file_converter import (
    convert_file,
    build_output_path,
    SUPPORTED_OUTPUT_FORMATS,
    _flatten_alpha,
)
from src.core.alpha_processor import SUPPORTED_WRITE, save_image


def _make_png(path: str, w=8, h=8, alpha=200):
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, 0] = 200
    arr[:, :, 1] = 100
    arr[:, :, 2] = 50
    arr[:, :, 3] = alpha
    img = Image.fromarray(arr, "RGBA")
    img.save(path)


def _make_rgb_png(path: str, w=8, h=8):
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :, 0] = 200
    arr[:, :, 1] = 100
    arr[:, :, 2] = 50
    img = Image.fromarray(arr, "RGB")
    img.save(path)


def _make_palette_png(path: str, w=8, h=8):
    img = Image.new("P", (w, h))
    img.putpalette([i % 256 for i in range(256 * 3)])
    img.save(path)


def _make_raw_dds(
    path: str,
    width: int,
    height: int,
    bits: int,
    pixel_data: bytes,
    *,
    pf_flags: int = 0x41,
    r_mask: int = 0x00FF0000,
    g_mask: int = 0x0000FF00,
    b_mask: int = 0x000000FF,
    a_mask: int = 0xFF000000,
):
    def dword(n: int) -> bytes:
        return int(n).to_bytes(4, "little")

    header = bytearray(128)
    header[0:4] = b"DDS "
    header[4:8] = dword(124)
    header[8:12] = dword(0x000A1007)
    header[12:16] = dword(height)
    header[16:20] = dword(width)
    header[20:24] = dword(width * max(1, bits // 8))
    header[76:80] = dword(32)
    header[80:84] = dword(pf_flags)
    header[88:92] = dword(bits)
    header[92:96] = dword(r_mask)
    header[96:100] = dword(g_mask)
    header[100:104] = dword(b_mask)
    header[104:108] = dword(a_mask)
    header[108:112] = dword(0x1000)
    with open(path, "wb") as f:
        f.write(bytes(header))
        f.write(pixel_data)


class TestBuildOutputPath(unittest.TestCase):

    def test_same_dir(self):
        result = build_output_path("/some/dir/file.png", ".dds")
        self.assertEqual(result, "/some/dir/file.dds")

    def test_output_dir(self):
        result = build_output_path("/some/dir/file.png", ".jpg", output_dir="/out")
        self.assertEqual(result, "/out/file.jpg")

    def test_output_dir_with_root(self):
        result = build_output_path(
            "/src/sub/file.png", ".jpg",
            output_dir="/out",
            input_root="/src",
        )
        self.assertEqual(result, "/out/sub/file.jpg")


class TestConvertFile(unittest.TestCase):

    def test_png_to_jpeg(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.jpg")
            _make_png(src)
            convert_file(src, dst, "JPEG")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertEqual(img.format, "JPEG")

    def test_png_to_bmp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.bmp")
            _make_png(src)
            convert_file(src, dst, "BMP")
            self.assertTrue(os.path.isfile(dst))

    def test_png_to_tim(self):
        from src.core.tim_handler import load_tim

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.tim")
            _make_png(src, w=4, h=4, alpha=200)
            convert_file(src, dst, "TIM")
            self.assertTrue(os.path.isfile(dst))
            img = load_tim(dst)
            try:
                self.assertEqual(img.mode, "RGBA")
                self.assertEqual(img.size, (4, 4))
                self.assertEqual(img.getpixel((0, 0))[3], 128)
            finally:
                img.close()

    def test_png_to_tiff(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.tiff")
            _make_png(src)
            convert_file(src, dst, "TIFF")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertEqual(img.format, "TIFF")

    def test_png_to_webp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.webp")
            _make_png(src)
            convert_file(src, dst, "WEBP")
            self.assertTrue(os.path.isfile(dst))

    def test_resize(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.png")
            _make_png(src, w=64, h=64)
            convert_file(src, dst, "PNG", resize=(32, 32))
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertEqual(img.size, (32, 32))

    def test_output_dir_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            new_sub = os.path.join(tmpdir, "sub", "output.png")
            _make_png(src)
            convert_file(src, new_sub, "PNG")
            self.assertTrue(os.path.isfile(new_sub))

    def test_supported_output_formats_includes_dds(self):
        self.assertIn("DDS", SUPPORTED_OUTPUT_FORMATS)

    def test_dds_to_png_uses_native_loader_when_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.dds")
            dst = os.path.join(tmpdir, "output.png")
            Image.new("RGBA", (8, 8), (10, 20, 30, 40)).save(src, format="DDS")
            convert_file(src, dst, "PNG")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst).convert("RGBA")
            self.assertEqual(img.size, (8, 8))

    def test_save_dds_tries_pillow_before_raw_fallback(self):
        from src.core import alpha_processor as ap
        img = Image.new("RGBA", (4, 4), (1, 2, 3, 4))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "output.dds")
                with mock.patch.object(ap.Image.Image, "save", wraps=Image.Image.save) as save_mock:
                    ap._save_dds(img, dst)
                self.assertTrue(save_mock.called)
                self.assertTrue(os.path.isfile(dst))
        finally:
            img.close()

    def test_save_dds_falls_back_when_pillow_save_fails(self):
        from src.core import alpha_processor as ap
        img = Image.new("RGBA", (4, 4), (9, 8, 7, 6))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "output.dds")
                with mock.patch.object(ap, "_has_wand", return_value=False):
                    with mock.patch.object(ap.Image.Image, "save", side_effect=OSError("save failed")):
                        ap._save_dds(img, dst)
                self.assertTrue(os.path.isfile(dst))
                with Image.open(dst) as result:
                    self.assertEqual(result.size, (4, 4))
        finally:
            img.close()

    def test_load_dds_supports_16bit_argb1555_masks(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.dds")
            pixels = (
                (0xFC00).to_bytes(2, "little")
                + (0x83E0).to_bytes(2, "little")
                + (0x801F).to_bytes(2, "little")
                + (0xFFFF).to_bytes(2, "little")
            )
            _make_raw_dds(
                src,
                2,
                2,
                16,
                pixels,
                pf_flags=0x41,
                r_mask=0x7C00,
                g_mask=0x03E0,
                b_mask=0x001F,
                a_mask=0x8000,
            )
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.mode, "RGBA")
                self.assertEqual(img.size, (2, 2))
                self.assertGreaterEqual(img.getpixel((0, 0))[0], 240)
                self.assertEqual(img.getpixel((0, 0))[3], 255)
                self.assertGreaterEqual(img.getpixel((1, 0))[1], 240)
                self.assertGreaterEqual(img.getpixel((0, 1))[2], 240)
            finally:
                img.close()

    def test_load_dds_supports_luminance_alpha_masks(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.dds")
            pixels = (0x4080).to_bytes(2, "little")
            _make_raw_dds(
                src,
                1,
                1,
                16,
                pixels,
                pf_flags=0x20001,
                r_mask=0x00FF,
                g_mask=0x0000,
                b_mask=0x0000,
                a_mask=0xFF00,
            )
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.getpixel((0, 0)), (128, 128, 128, 64))
            finally:
                img.close()

    def test_load_dds_supports_custom_32bit_channel_masks(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.dds")
            pixels = bytes((10, 20, 30, 40))
            _make_raw_dds(
                src,
                1,
                1,
                32,
                pixels,
                pf_flags=0x41,
                r_mask=0x000000FF,
                g_mask=0x0000FF00,
                b_mask=0x00FF0000,
                a_mask=0xFF000000,
            )
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.getpixel((0, 0)), (10, 20, 30, 40))
            finally:
                img.close()

    def test_supported_output_formats_includes_png(self):
        self.assertIn("PNG", SUPPORTED_OUTPUT_FORMATS)

    # ------------------------------------------------------------------
    # New format tests
    # ------------------------------------------------------------------

    def test_rgba_png_to_jpeg_has_no_black_areas(self):
        """RGBA → JPEG should composite onto white, not produce a black image."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.jpg")
            # semi-transparent red image
            _make_png(src, alpha=128)
            convert_file(src, dst, "JPEG")
            img = Image.open(dst).convert("RGB")
            arr = np.array(img)
            # If alpha were dropped the result would be very dark; compositing
            # onto white makes it noticeably bright.
            self.assertGreater(arr[:, :, 2].mean(), 100)  # blue channel bright from white bg

    def test_rgba_png_to_bmp_has_no_alpha(self):
        """BMP should not contain alpha (must be composited onto white)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.bmp")
            _make_png(src, alpha=128)
            convert_file(src, dst, "BMP")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertNotIn("A", img.mode)

    def test_rgba_png_to_gif(self):
        """GIF from RGBA source should save without error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.gif")
            _make_png(src)
            convert_file(src, dst, "GIF")
            self.assertTrue(os.path.isfile(dst))

    def test_png_to_ppm(self):
        """PPM output should be RGB with no alpha."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.ppm")
            _make_png(src)
            convert_file(src, dst, "PPM")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertIn(img.mode, ("RGB", "L"))

    def test_jfif_input_converts_to_png(self):
        """JFIF input should be treated like JPEG."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.jfif")
            dst = os.path.join(tmpdir, "output.png")
            Image.new("RGB", (8, 8), (12, 34, 56)).save(src, format="JPEG")
            convert_file(src, dst, "PNG")
            self.assertTrue(os.path.isfile(dst))
            with Image.open(dst) as img:
                self.assertEqual(img.size, (8, 8))
                self.assertIn(img.mode, ("RGBA", "RGB"))

    def test_alpha_processor_loads_jpe_alias(self):
        from src.core.alpha_processor import load_image, SUPPORTED_READ, CONVERT_TO_RGBA

        self.assertIn(".jpe", SUPPORTED_READ)
        self.assertIn(".jfif", SUPPORTED_READ)
        self.assertIn(".jpe", CONVERT_TO_RGBA)
        self.assertIn(".jfif", CONVERT_TO_RGBA)
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.jpe")
            Image.new("RGB", (4, 4), (10, 20, 30)).save(src, format="JPEG")
            img = load_image(src)
            try:
                self.assertEqual(img.mode, "RGBA")
                self.assertEqual(img.size, (4, 4))
            finally:
                img.close()

    def test_alpha_processor_saves_jfif_and_jpe_aliases(self):
        from src.core.alpha_processor import save_image

        with tempfile.TemporaryDirectory() as tmpdir:
            img = Image.new("RGBA", (4, 4), (10, 20, 30, 120))
            try:
                for ext in (".jfif", ".jpe"):
                    dst = os.path.join(tmpdir, f"output{ext}")
                    save_image(img, dst, ext)
                    self.assertTrue(os.path.isfile(dst))
                    with Image.open(dst) as saved:
                        self.assertEqual(saved.format, "JPEG")
                        self.assertEqual(saved.size, (4, 4))
            finally:
                img.close()

    def test_alpha_processor_supported_write_includes_tim(self):
        self.assertIn(".tim", SUPPORTED_WRITE)
        self.assertIn(".svg", SUPPORTED_WRITE)
        self.assertIn(".xnb", SUPPORTED_WRITE)

    def test_converter_supported_output_includes_tim(self):
        self.assertEqual(SUPPORTED_OUTPUT_FORMATS["TIM"], ".tim")

    def test_alpha_processor_save_svg_uses_svg_writer(self):
        from src.core import alpha_processor as ap

        img = Image.new("RGBA", (4, 4), (10, 20, 30, 40))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "output.svg")
                with mock.patch("src.core.file_converter._save_svg") as save_svg:
                    save_image(img, dst, ".svg")
                save_svg.assert_called_once()
        finally:
            img.close()

    def test_alpha_processor_save_xnb_uses_xnb_writer(self):
        img = Image.new("RGBA", (4, 4), (10, 20, 30, 40))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "output.xnb")
                with mock.patch("src.core.xnb_handler.save_xnb") as save_xnb:
                    save_image(img, dst, ".xnb")
                save_xnb.assert_called_once()
        finally:
            img.close()

    def test_alpha_processor_save_tim_uses_tim_writer(self):
        img = Image.new("RGBA", (4, 4), (10, 20, 30, 40))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "output.tim")
                with mock.patch("src.core.tim_handler.save_tim") as save_tim:
                    save_image(img, dst, ".tim")
                save_tim.assert_called_once()
        finally:
            img.close()

    def test_tim_roundtrip_preserves_basic_transparency_states(self):
        from src.core.tim_handler import load_tim, save_tim

        img = Image.new("RGBA", (2, 2))
        img.putdata([
            (0, 0, 0, 0),
            (255, 0, 0, 120),
            (0, 0, 0, 255),
            (0, 255, 0, 255),
        ])
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "roundtrip.tim")
                save_tim(img, dst)
                out = load_tim(dst)
                try:
                    self.assertEqual(out.getpixel((0, 0))[3], 0)
                    self.assertEqual(out.getpixel((1, 0))[3], 128)
                    self.assertEqual(out.getpixel((0, 1)), (0, 0, 0, 255))
                    self.assertEqual(out.getpixel((1, 1))[1], 248)
                    self.assertEqual(out.getpixel((1, 1))[3], 255)
                finally:
                    out.close()
        finally:
            img.close()

    def test_xnb_rgba64_reorders_channels_to_rgba(self):
        from src.core.xnb_handler import _decode_texture2d, _FMT_RGBA64

        data = bytes.fromhex("0011002200330044")
        with _decode_texture2d(data, 1, 1, _FMT_RGBA64) as img:
            self.assertEqual(img.getpixel((0, 0)), (0x33, 0x22, 0x11, 0x44))

    def test_xnb_rgba1010102_uses_full_10bit_channels(self):
        from src.core.xnb_handler import _decode_texture2d, _FMT_RGBA1010102

        packed = (1023 << 22) | (512 << 12) | (256 << 2) | 0x3
        data = packed.to_bytes(4, "little")
        with _decode_texture2d(data, 1, 1, _FMT_RGBA1010102) as img:
            r, g, b, a = img.getpixel((0, 0))
            self.assertEqual(r, 255)
            self.assertEqual(g, 128)
            self.assertEqual(b, 64)
            self.assertEqual(a, 255)

    def test_png_to_pgm(self):
        """PGM output should be grayscale with no alpha."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.pgm")
            _make_png(src)
            convert_file(src, dst, "PGM")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertEqual(img.mode, "L")

    def test_png_to_pbm(self):
        """PBM output should be 1-bit with no alpha."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.pbm")
            _make_png(src)
            convert_file(src, dst, "PBM")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertEqual(img.mode, "1")

    def test_png_to_pnm(self):
        """PNM output should save successfully through the Netpbm path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.pnm")
            _make_png(src)
            convert_file(src, dst, "PNM")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertIn(img.mode, ("RGB", "L"))

    def test_png_to_pcx(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.pcx")
            _make_png(src)
            convert_file(src, dst, "PCX")
            self.assertTrue(os.path.isfile(dst))

    def test_png_to_avif(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.avif")
            _make_png(src)
            convert_file(src, dst, "AVIF")
            self.assertTrue(os.path.isfile(dst))

    def test_png_to_qoi(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.qoi")
            _make_png(src)
            convert_file(src, dst, "QOI")
            self.assertTrue(os.path.isfile(dst))

    def test_png_to_jpeg2000(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.jp2")
            _make_png(src)
            convert_file(src, dst, "JPEG2000")
            self.assertTrue(os.path.isfile(dst))
            self.assertGreater(os.path.getsize(dst), 0)

    def test_png_to_jpeg2000_lossless(self):
        """quality=100 should produce a lossless JPEG2000 file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.jp2")
            _make_png(src)
            convert_file(src, dst, "JPEG2000", quality=100)
            self.assertTrue(os.path.isfile(dst))
            result = Image.open(dst)
            self.assertIn(result.mode, ("RGBA", "RGB"))

    def test_rgb_png_preserved_as_png(self):
        """RGB source → PNG should not be needlessly upcast to RGBA."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.png")
            _make_rgb_png(src)
            convert_file(src, dst, "PNG")
            img = Image.open(dst)
            self.assertEqual(img.mode, "RGB")

    def test_supported_output_formats_includes_new(self):
        for fmt in ("PBM", "PGM", "PNM", "PPM", "PCX", "AVIF", "QOI", "JPEG2000"):
            with self.subTest(fmt=fmt):
                self.assertIn(fmt, SUPPORTED_OUTPUT_FORMATS)

    def test_supported_output_formats_includes_svg(self):
        self.assertIn("SVG", SUPPORTED_OUTPUT_FORMATS)

    def test_png_to_svg_creates_valid_svg(self):
        """PNG → SVG (fallback mode) should produce a file with embedded base64 PNG."""
        import unittest.mock as mock
        from src.core import file_converter as fc
        with mock.patch.object(fc, "_has_vtracer", return_value=False):
            with tempfile.TemporaryDirectory() as tmpdir:
                src = os.path.join(tmpdir, "input.png")
                dst = os.path.join(tmpdir, "output.svg")
                _make_png(src)
                convert_file(src, dst, "SVG")
                self.assertTrue(os.path.isfile(dst))
                with open(dst, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("<svg", content)
                self.assertIn("data:image/png;base64,", content)
                self.assertIn("</svg>", content)

    def test_png_to_svg_vtracer_creates_valid_svg(self):
        """PNG → SVG (vtracer mode) should produce a true vector SVG with <path> elements."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.svg")
            _make_png(src)
            convert_file(src, dst, "SVG")
            self.assertTrue(os.path.isfile(dst))
            with open(dst, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("<svg", content)

    def test_rgba_png_to_svg_preserves_size(self):
        """SVG output should encode the correct width/height attributes."""
        import unittest.mock as mock
        from src.core import file_converter as fc
        with mock.patch.object(fc, "_has_vtracer", return_value=False):
            with tempfile.TemporaryDirectory() as tmpdir:
                src = os.path.join(tmpdir, "input.png")
                dst = os.path.join(tmpdir, "output.svg")
                _make_png(src, w=16, h=8)
                convert_file(src, dst, "SVG")
                with open(dst, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn('width="16"', content)
                self.assertIn('height="8"', content)

    def test_svg_output_roundtrip(self):
        """The base64 PNG embedded in the fallback SVG should decode to a valid image."""
        import base64
        import re
        import unittest.mock as mock
        from src.core import file_converter as fc
        from io import BytesIO
        with mock.patch.object(fc, "_has_vtracer", return_value=False):
            with tempfile.TemporaryDirectory() as tmpdir:
                src = os.path.join(tmpdir, "input.png")
                dst = os.path.join(tmpdir, "output.svg")
                _make_png(src, w=8, h=8)
                convert_file(src, dst, "SVG")
                with open(dst, encoding="utf-8") as f:
                    content = f.read()
                match = re.search(r'data:image/png;base64,([A-Za-z0-9+/=]+)', content)
                self.assertIsNotNone(match)
                png_data = base64.b64decode(match.group(1))
                rt_img = Image.open(BytesIO(png_data))
                self.assertEqual(rt_img.size, (8, 8))


class TestFlattenAlpha(unittest.TestCase):

    def test_rgba_flattened_to_rgb(self):
        img = Image.new("RGBA", (4, 4), (100, 100, 100, 128))
        result = _flatten_alpha(img)
        self.assertEqual(result.mode, "RGB")

    def test_rgb_unchanged(self):
        img = Image.new("RGB", (4, 4), (200, 100, 50))
        result = _flatten_alpha(img)
        self.assertEqual(result.mode, "RGB")

    def test_la_flattened_to_l(self):
        img = Image.new("LA", (4, 4), (100, 128))
        result = _flatten_alpha(img)
        self.assertEqual(result.mode, "L")

    def test_palette_flattened_to_rgb(self):
        img = Image.new("P", (4, 4))
        img.putpalette([i % 256 for i in range(256 * 3)])
        result = _flatten_alpha(img)
        self.assertEqual(result.mode, "RGB")


class TestSupportedRead(unittest.TestCase):
    """Verify SUPPORTED_READ in alpha_processor includes all expected extensions."""

    def setUp(self):
        from src.core.alpha_processor import SUPPORTED_READ
        self.exts = SUPPORTED_READ

    def test_jp2_in_supported_read(self):
        self.assertIn(".jp2", self.exts)

    def test_svg_in_supported_read(self):
        self.assertIn(".svg", self.exts)

    def test_png_in_supported_read(self):
        self.assertIn(".png", self.exts)

    def test_avif_in_supported_read(self):
        self.assertIn(".avif", self.exts)

    def test_netpbm_family_in_supported_read(self):
        for ext in (".pbm", ".pgm", ".pnm"):
            with self.subTest(ext=ext):
                self.assertIn(ext, self.exts)

    def test_jpeg2000_aliases_in_supported_read(self):
        for ext in (".j2k", ".j2c"):
            with self.subTest(ext=ext):
                self.assertIn(ext, self.exts)


class TestSvgVtracerFallback(unittest.TestCase):
    """_save_svg uses base64 fallback when vtracer is not available."""

    def test_fallback_produces_base64_svg(self):
        """Without vtracer the SVG must contain an embedded base64 PNG."""
        import unittest.mock as mock
        from src.core import file_converter as fc
        with mock.patch.object(fc, "_has_vtracer", return_value=False):
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "out.svg")
                img = Image.new("RGBA", (8, 8), (200, 100, 50, 128))
                fc._save_svg(img, dst)
                with open(dst, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("<svg", content)
                self.assertIn("data:image/png;base64,", content)

    def test_vtracer_path_called(self):
        """When vtracer is available, vtracer.convert_image_to_svg_py is invoked."""
        import unittest.mock as mock
        from src.core import file_converter as fc

        mock_vtracer = mock.MagicMock()
        mock_vtracer.convert_image_to_svg_py = mock.MagicMock()

        with mock.patch.object(fc, "_has_vtracer", return_value=True):
            with mock.patch.dict("sys.modules", {"vtracer": mock_vtracer}):
                with tempfile.TemporaryDirectory() as tmpdir:
                    dst = os.path.join(tmpdir, "out.svg")
                    img = Image.new("RGB", (4, 4), (200, 100, 50))
                    fc._save_svg(img, dst)
                    mock_vtracer.convert_image_to_svg_py.assert_called_once()
                    args = mock_vtracer.convert_image_to_svg_py.call_args
                    self.assertEqual(args[1]["colormode"], "color")


if __name__ == "__main__":
    unittest.main()
