import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from src.core import file_converter as converter


class TestBundledSvg(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not converter._has_qt_svg():
            raise unittest.SkipTest("Qt SVG stack unavailable")

    def _render(self, svg):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.svg"
            path.write_text(svg, encoding="utf-8")
            with patch.object(converter, "_has_cairosvg", return_value=False):
                with patch.object(converter, "_has_svglib", return_value=False):
                    return converter._load_svg(str(path))

    def test_rasterizes_vector_and_preserves_transparency_without_external_renderer(self):
        with self._render(
            '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8">'
            '<rect width="6" height="8" fill="#ff0000" fill-opacity="0.5"/></svg>'
        ) as image:
            self.assertEqual(image.size, (12, 8))
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((2, 2))[:3], (255, 0, 0))
            self.assertAlmostEqual(image.getpixel((2, 2))[3], 128, delta=1)
            self.assertEqual(image.getpixel((10, 2))[3], 0)

    def test_viewbox_only_svg_uses_intrinsic_size(self):
        with self._render(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 20">'
            '<rect width="30" height="20" fill="blue"/></svg>'
        ) as image:
            self.assertEqual(image.size, (30, 20))
            self.assertEqual(image.getpixel((15, 10)), (0, 0, 255, 255))

    def test_invalid_svg_fails_clearly(self):
        with self.assertRaisesRegex(ValueError, "Invalid or unsupported SVG"):
            self._render("<not-an-svg/>")

    def test_oversized_svg_rejected_before_pixel_allocation(self):
        with patch.object(Image, "MAX_IMAGE_PIXELS", 100):
            with self.assertRaises(Image.DecompressionBombError):
                self._render(
                    '<svg xmlns="http://www.w3.org/2000/svg" width="101" height="101"/>'
                )

    def test_embedded_raster_svg_roundtrips_without_tracing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "output.svg")
            with Image.new("RGBA", (8, 6), (30, 90, 150, 255)) as original:
                with patch.object(converter, "_has_vtracer", return_value=False):
                    converter._save_svg(original, path)
                with converter._load_svg(path) as loaded:
                    self.assertEqual(loaded.size, original.size)
                    self.assertEqual(loaded.getpixel((4, 3)), original.getpixel((4, 3)))

    def test_bundled_tracer_exports_real_paths(self):
        if not converter._has_vtracer():
            self.skipTest("Declared vtracer dependency unavailable")
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "traced.svg")
            with Image.new("RGBA", (12, 12), (200, 30, 80, 255)) as source:
                converter._save_svg(source, path)
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("<path", content)
            self.assertNotIn("data:image/png;base64,", content)


class TestOfflineVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "scripts" / "verify_packaged_app.py"
        spec = importlib.util.spec_from_file_location("offline_verifier", path)
        cls.verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.verifier)

    def test_external_runtime_configuration_removed(self):
        external = {
            "PATH": "/external/tools", "PYTHONPATH": "/external/python",
            "MAGICK_HOME": "/external/magick", "IMAGEIO_FFMPEG_EXE": "/external/ffmpeg",
            "ALPHA_FIXER_FFPROBE_EXE": "/external/ffprobe",
            "LD_LIBRARY_PATH": "/external/libs",
            "ALPHA_FIXER_RUNTIME_ALLOW_SAMPLE_DOWNLOADS": "1",
            "ALPHA_FIXER_ALLOW_SAMPLE_DOWNLOADS": "true",
        }
        with patch.dict(os.environ, external):
            env = self.verifier._verification_environment(True)
        for name in external.keys() - {
            "PATH", "ALPHA_FIXER_RUNTIME_ALLOW_SAMPLE_DOWNLOADS", "ALPHA_FIXER_ALLOW_SAMPLE_DOWNLOADS",
        }:
            self.assertNotIn(name, env)
        self.assertNotIn("/external", env["PATH"])
        self.assertEqual(env["ALPHA_FIXER_RUNTIME_ALLOW_SAMPLE_DOWNLOADS"], "0")
        self.assertEqual(env["ALPHA_FIXER_ALLOW_SAMPLE_DOWNLOADS"], "0")

    def test_normal_verification_retains_source_configuration(self):
        with patch.dict(os.environ, {"IMAGEIO_FFMPEG_EXE": "/test/ffmpeg"}):
            env = self.verifier._verification_environment(False)
        self.assertEqual(env["IMAGEIO_FFMPEG_EXE"], "/test/ffmpeg")

    def test_offline_mode_rejects_explicit_downloads_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "app"
            target.touch()
            with patch.object(self.verifier, "_run_and_echo") as run:
                with self.assertRaisesRegex(SystemExit, "cannot be combined"):
                    self.verifier.main([str(target), "--offline", "--allow-sample-downloads"])
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
