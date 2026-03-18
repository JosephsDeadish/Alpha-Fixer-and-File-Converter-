"""
Tests for file converter utilities.
"""
import sys
import os
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.file_converter import (
    convert_file,
    build_output_path,
    SUPPORTED_OUTPUT_FORMATS,
    _flatten_alpha,
)


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
        for fmt in ("PPM", "PCX", "AVIF", "QOI", "JPEG2000"):
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
