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
        for fmt in ("PPM", "PCX", "AVIF", "QOI"):
            with self.subTest(fmt=fmt):
                self.assertIn(fmt, SUPPORTED_OUTPUT_FORMATS)


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


if __name__ == "__main__":
    unittest.main()
