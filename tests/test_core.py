"""
Tests for core alpha processing and preset management.
"""
import sys
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
from PIL import Image

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.presets import AlphaPreset, PresetManager, BUILTIN_PRESETS
from src.core.alpha_processor import (
    apply_alpha_preset,
    apply_manual_alpha,
    collect_files,
    load_image,
    save_image,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_rgba_image(w=4, h=4, alpha=128) -> Image.Image:
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, :3] = 200  # grey RGB
    arr[:, :, 3] = alpha
    return Image.fromarray(arr, "RGBA")


# ---------------------------------------------------------------------------
# Preset tests
# ---------------------------------------------------------------------------

class TestPresets(unittest.TestCase):

    def setUp(self):
        self._mock_settings = MagicMock()
        self._mock_settings.get_custom_presets.return_value = []
        self._saved = []
        self._mock_settings.save_custom_presets.side_effect = lambda d: None
        self._mgr = PresetManager(self._mock_settings)

    def test_builtin_presets_present(self):
        names = [p.name for p in self._mgr.all_presets()]
        self.assertIn("PS2", names)
        self.assertIn("N64", names)
        self.assertIn("No Alpha", names)
        self.assertIn("Max Alpha", names)
        self.assertIn("Transparent", names)

    def test_ps2_preset_values(self):
        p = self._mgr.get_preset("PS2")
        self.assertIsNotNone(p)
        self.assertEqual(p.alpha_value, 128)
        self.assertEqual(p.fill_mode, "set")

    def test_n64_preset_values(self):
        p = self._mgr.get_preset("N64")
        self.assertIsNotNone(p)
        self.assertEqual(p.alpha_value, 255)

    def test_get_nonexistent_preset(self):
        self.assertIsNone(self._mgr.get_preset("DoesNotExist"))

    def test_cannot_overwrite_builtin(self):
        custom = AlphaPreset("PS2", 0, "set", 0, 0, False, "test")
        result = self._mgr.save_custom_preset(custom)
        self.assertFalse(result)

    def test_save_and_retrieve_custom_preset(self):
        custom = AlphaPreset("My Custom", 200, "set", 200, 0, False, "desc", builtin=False)
        result = self._mgr.save_custom_preset(custom)
        self.assertTrue(result)
        retrieved = self._mgr.get_preset("My Custom")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.alpha_value, 200)

    def test_delete_custom_preset(self):
        custom = AlphaPreset("ToDelete", 100, "set", 100, 0, False, "desc", builtin=False)
        self._mgr.save_custom_preset(custom)
        result = self._mgr.delete_custom_preset("ToDelete")
        self.assertTrue(result)
        self.assertIsNone(self._mgr.get_preset("ToDelete"))

    def test_cannot_delete_builtin(self):
        result = self._mgr.delete_custom_preset("PS2")
        self.assertFalse(result)
        self.assertIsNotNone(self._mgr.get_preset("PS2"))


# ---------------------------------------------------------------------------
# Alpha processor tests
# ---------------------------------------------------------------------------

class TestAlphaProcessor(unittest.TestCase):

    def test_set_alpha_to_255(self):
        img = make_rgba_image(alpha=128)
        preset = AlphaPreset("test", 255, "set", 255, 0, False, "")
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 255))

    def test_set_alpha_to_0(self):
        img = make_rgba_image(alpha=200)
        preset = AlphaPreset("test", 0, "set", 0, 0, False, "")
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 0))

    def test_ps2_preset(self):
        img = make_rgba_image(alpha=200)
        preset = AlphaPreset("PS2", 128, "set", 128, 0, False, "")
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 128))

    def test_invert_alpha(self):
        img = make_rgba_image(alpha=100)
        preset = AlphaPreset("inv", None, "set", 0, 0, True, "")
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 155))  # 255 - 100 = 155

    def test_add_alpha(self):
        img = make_rgba_image(alpha=100)
        preset = AlphaPreset("add", 50, "add", 50, 0, False, "")
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 150))

    def test_subtract_alpha(self):
        img = make_rgba_image(alpha=100)
        preset = AlphaPreset("sub", 30, "subtract", 30, 0, False, "")
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 70))

    def test_clamp_alpha_no_negative(self):
        img = make_rgba_image(alpha=10)
        preset = AlphaPreset("sub", 50, "subtract", 50, 0, False, "")
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        # 10 - 50 = -40 -> clamped to 0
        self.assertTrue(np.all(arr[:, :, 3] == 0))

    def test_clamp_alpha_no_overflow(self):
        img = make_rgba_image(alpha=250)
        preset = AlphaPreset("add", 20, "add", 20, 0, False, "")
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        # 250 + 20 = 270 -> clamped to 255
        self.assertTrue(np.all(arr[:, :, 3] == 255))

    def test_manual_alpha(self):
        img = make_rgba_image(alpha=100)
        result = apply_manual_alpha(img, mode="set", value=200)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 200))

    def test_multiply_mode(self):
        img = make_rgba_image(alpha=200)
        preset = AlphaPreset("mul", None, "multiply", 50, 0, False, "")
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        expected = int(200 * 0.50)
        self.assertTrue(np.all(arr[:, :, 3] == expected))

    def test_output_is_rgba(self):
        img = make_rgba_image(alpha=100)
        preset = AlphaPreset("test", 200, "set", 200, 0, False, "")
        result = apply_alpha_preset(img, preset)
        self.assertEqual(result.mode, "RGBA")

    def test_rgb_input_converted(self):
        img = Image.new("RGB", (4, 4), (200, 200, 200))
        preset = AlphaPreset("test", 128, "set", 128, 0, False, "")
        result = apply_alpha_preset(img, preset)
        self.assertEqual(result.mode, "RGBA")
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 128))


# ---------------------------------------------------------------------------
# collect_files tests
# ---------------------------------------------------------------------------

class TestCollectFiles(unittest.TestCase):

    def test_collect_single_file(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            result = collect_files([path])
            self.assertIn(path, result)
        finally:
            os.unlink(path)

    def test_collect_directory_recursive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = os.path.join(tmpdir, "sub")
            os.makedirs(sub)
            p1 = os.path.join(tmpdir, "a.png")
            p2 = os.path.join(sub, "b.png")
            for p in (p1, p2):
                with open(p, "w"):
                    pass
            result = collect_files([tmpdir], recursive=True)
            self.assertIn(p1, result)
            self.assertIn(p2, result)

    def test_collect_directory_non_recursive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = os.path.join(tmpdir, "sub")
            os.makedirs(sub)
            p1 = os.path.join(tmpdir, "a.png")
            p2 = os.path.join(sub, "b.png")
            for p in (p1, p2):
                with open(p, "w"):
                    pass
            result = collect_files([tmpdir], recursive=False)
            self.assertIn(p1, result)
            self.assertNotIn(p2, result)

    def test_unsupported_extension_excluded(self):
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            path = f.name
        try:
            result = collect_files([path])
            self.assertNotIn(path, result)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Save / load round-trip
# ---------------------------------------------------------------------------

class TestSaveLoad(unittest.TestCase):

    def test_png_round_trip(self):
        img = make_rgba_image(alpha=150)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            save_image(img, path, ".png")
            loaded = load_image(path)
            arr = np.array(loaded)
            self.assertTrue(np.all(arr[:, :, 3] == 150))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# AlphaWorker._resolve_output  (Bug: ignored output_dir when overwrite=True)
# ---------------------------------------------------------------------------

class TestAlphaWorkerResolveOutput(unittest.TestCase):
    """_resolve_output must honour output_dir regardless of overwrite flag."""

    def _make_worker(self, output_dir=None, suffix="", overwrite=False):
        from src.core.worker import AlphaWorker
        return AlphaWorker(
            files=[],
            output_dir=output_dir,
            overwrite=overwrite,
            suffix=suffix,
        )

    def test_no_output_dir_writes_beside_source(self):
        w = self._make_worker()
        result = w._resolve_output("/some/dir/image.png")
        self.assertEqual(result, "/some/dir/image.png")

    def test_output_dir_used_with_suffix(self):
        w = self._make_worker(output_dir="/out", suffix="_fixed", overwrite=False)
        result = w._resolve_output("/src/image.png")
        self.assertEqual(result, "/out/image_fixed.png")

    def test_output_dir_honoured_even_when_overwrite_true(self):
        """The previous bug: output_dir was ignored when overwrite=True (suffix='')."""
        w = self._make_worker(output_dir="/out", suffix="", overwrite=True)
        result = w._resolve_output("/src/image.png")
        # Must go to /out, NOT back to /src
        self.assertEqual(result, "/out/image.png")

    def test_no_output_dir_overwrite_writes_beside_source(self):
        """Without output_dir, overwrite mode correctly writes beside the source."""
        w = self._make_worker(output_dir=None, suffix="", overwrite=True)
        result = w._resolve_output("/src/image.png")
        self.assertEqual(result, "/src/image.png")

