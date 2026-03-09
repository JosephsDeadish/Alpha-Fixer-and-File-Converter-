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

from src.core.file_converter import convert_file, build_output_path, SUPPORTED_OUTPUT_FORMATS


def _make_png(path: str, w=8, h=8, alpha=200):
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, 0] = 200
    arr[:, :, 1] = 100
    arr[:, :, 2] = 50
    arr[:, :, 3] = alpha
    img = Image.fromarray(arr, "RGBA")
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


if __name__ == "__main__":
    unittest.main()
